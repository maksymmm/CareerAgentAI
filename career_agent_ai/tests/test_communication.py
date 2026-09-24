from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest

from career_agent_ai.application.communication import (
    CommunicationMessage,
    CommunicationService,
    FakeCommunicationAdapter,
    MessageDirection,
)
from career_agent_ai.application.external_actions import (
    ExternalActionOperation,
    ExternalActionService,
    ExternalActionStatus,
)
from career_agent_ai.application.storage.sqlite_communication_repository import (
    SQLiteCommunicationRepository,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import (
    SQLiteExternalActionOperationRepository,
)


def message(
    message_id: str = "message-1",
    *,
    thread_id: str = "thread-1",
    direction: MessageDirection = MessageDirection.DRAFT,
    in_reply_to: str | None = None,
) -> CommunicationMessage:
    return CommunicationMessage(
        message_id=message_id,
        thread_id=thread_id,
        sender="candidate@example.test",
        recipient="recruiter@example.test",
        subject="Opportunity",
        body="Hello,\nI am interested.",
        direction=direction,
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        in_reply_to=in_reply_to,
    )


def stack(database: SQLiteDatabase, provider: FakeCommunicationAdapter | None = None):
    provider = provider or FakeCommunicationAdapter()
    messages = SQLiteCommunicationRepository(database)
    operations = SQLiteExternalActionOperationRepository(database)
    external = ExternalActionService(
        operations, CommunicationService.action_adapter(provider, messages)
    )
    return CommunicationService(messages, provider, external), messages, operations, provider


def test_draft_creation_is_provider_neutral_and_persisted():
    service, repository, _, provider = stack(SQLiteDatabase())

    created = service.create_draft(message())

    assert created.direction == MessageDirection.DRAFT
    assert repository.get("message-1") == created
    assert provider.calls == [("draft", "message-1")]


def test_dry_run_send_requires_approval_and_suppresses_duplicates():
    service, repository, _, provider = stack(SQLiteDatabase())
    service.create_draft(message())

    with pytest.raises(PermissionError, match="human approval"):
        service.send("send:message-1", "message-1", human_approved=False)
    assert [call for call in provider.calls if call[0] == "send"] == []

    sent = service.send("send:message-1", "message-1", human_approved=True)
    duplicate = service.send("send:message-1", "message-1", human_approved=True)

    assert sent == duplicate
    assert sent is not None and sent.direction == MessageDirection.OUTBOUND
    assert repository.get("message-1") == sent
    assert [call for call in provider.calls if call[0] == "send"] == [
        ("send", "send:message-1")
    ]


def test_outbound_message_cannot_be_resent_with_a_new_operation_id():
    service, _, operations, provider = stack(SQLiteDatabase())
    service.create_draft(message())
    assert service.send("send:1", "message-1", human_approved=True) is not None

    with pytest.raises(ValueError, match="Only a draft"):
        service.send("send:2", "message-1", human_approved=True)

    assert operations.get("send:2") is None
    assert provider.calls.count(("send", "send:1")) == 1
    assert ("send", "send:2") not in provider.calls


def test_dry_run_reply_persists_thread_and_parent_identifiers(tmp_path):
    path = str(tmp_path / "communication.sqlite")
    database = SQLiteDatabase(path)
    service, repository, _, provider = stack(database)
    parent = message(direction=MessageDirection.INBOUND)
    repository.save(parent)
    reply = message("message-2", in_reply_to="message-1")

    sent = service.reply(
        "reply:message-2", "message-1", reply, human_approved=True
    )
    database.close()

    restarted = SQLiteCommunicationRepository(SQLiteDatabase(path))
    assert sent is not None and sent.thread_id == "thread-1"
    assert sent.in_reply_to == "message-1"
    assert restarted.get("message-2") == sent
    assert restarted.list_thread("thread-1") == (parent, sent)
    assert ("reply", "reply:message-2") in provider.calls


def test_provider_failure_is_safely_persisted_and_not_retried():
    provider = FakeCommunicationAdapter()
    service, _, operations, _ = stack(SQLiteDatabase(), provider)
    service.create_draft(message())
    provider.failure = RuntimeError("temporary provider outage")

    assert service.send("send:1", "message-1", human_approved=True) is None
    assert service.send("send:1", "message-1", human_approved=True) is None

    failed = operations.get("send:1")
    assert failed is not None and failed.status == ExternalActionStatus.FAILED
    assert failed.error == "temporary provider outage"
    assert provider.calls.count(("send", "send:1")) == 1

    provider.failure = None
    retried = service.send("send:2", "message-1", human_approved=True)
    assert retried is not None
    assert operations.get("send:2").status == ExternalActionStatus.SUCCEEDED
    assert provider.calls.count(("send", "send:2")) == 1


def test_restart_replays_persisted_success_without_provider_side_effect(tmp_path):
    path = str(tmp_path / "communication.sqlite")
    first_database = SQLiteDatabase(path)
    service, _, _, _ = stack(first_database)
    service.create_draft(message())
    assert service.send("send:1", "message-1", human_approved=True) is not None
    first_database.close()

    restarted_provider = FakeCommunicationAdapter()
    restarted_service, _, _, _ = stack(SQLiteDatabase(path), restarted_provider)
    recovered = restarted_service.send("send:1", "message-1", human_approved=True)

    assert recovered is not None and recovered.message_id == "message-1"
    assert restarted_provider.calls == []


def test_successful_reply_retry_is_idempotent_after_restart(tmp_path):
    path = str(tmp_path / "reply-retry.sqlite")
    first_database = SQLiteDatabase(path)
    first, messages, _, first_provider = stack(first_database)
    messages.save(message(direction=MessageDirection.INBOUND))
    reply = message("message-2", in_reply_to="message-1")
    delivered = first.reply(
        "reply:1", "message-1", reply, human_approved=True
    )
    assert delivered is not None
    assert first_provider.calls.count(("reply", "reply:1")) == 1
    first_database.close()

    restarted_provider = FakeCommunicationAdapter()
    restarted, _, _, _ = stack(SQLiteDatabase(path), restarted_provider)
    repeated = restarted.reply(
        "reply:1", "message-1", reply, human_approved=True
    )

    assert repeated == delivered
    assert restarted_provider.calls == []


def test_ambiguous_in_flight_send_requires_reconciliation_without_provider_call():
    database = SQLiteDatabase()
    service, repository, operations, provider = stack(database)
    repository.save(message())
    operations.create(
        ExternalActionOperation("send:1", "communication.send", {"message_id": "message-1"})
    )
    operations.transition(
        "send:1", ExternalActionStatus.PREPARED, ExternalActionStatus.IN_PROGRESS
    )

    assert service.send("send:1", "message-1", human_approved=True) is None
    assert operations.get("send:1").status == ExternalActionStatus.RECONCILIATION_REQUIRED
    assert provider.calls == []


def test_stale_prepared_operation_cannot_send_after_competing_operation_wins():
    database = SQLiteDatabase()
    service, repository, operations, provider = stack(database)
    repository.save(message())
    with pytest.raises(PermissionError):
        service.send("stale:1", "message-1", human_approved=False)
    with pytest.raises(PermissionError):
        service.send("winner:1", "message-1", human_approved=False)

    assert service.send("winner:1", "message-1", human_approved=True) is not None
    assert service.send("stale:1", "message-1", human_approved=True) is None

    assert operations.get("stale:1").status == ExternalActionStatus.FAILED
    assert provider.calls.count(("send", "winner:1")) == 1
    assert provider.calls.count(("send", "stale:1")) == 0


def test_atomic_claim_prevents_concurrent_competing_sends(tmp_path):
    path = str(tmp_path / "concurrent.sqlite")
    entered_provider = Event()
    release_provider = Event()

    class BlockingProvider(FakeCommunicationAdapter):
        def send(self, operation_id, prepared):
            entered_provider.set()
            assert release_provider.wait(timeout=5)
            return super().send(operation_id, prepared)

    provider = BlockingProvider()
    setup_database = SQLiteDatabase(path)
    setup, setup_messages, _, _ = stack(setup_database, provider)
    setup_messages.save(message())
    with pytest.raises(PermissionError):
        setup.send("send:first", "message-1", human_approved=False)
    with pytest.raises(PermissionError):
        setup.send("send:second", "message-1", human_approved=False)
    setup_database.close()

    def execute(operation_id):
        worker_database = SQLiteDatabase(path)
        worker, _, _, _ = stack(worker_database, provider)
        try:
            return worker.send(operation_id, "message-1", human_approved=True)
        finally:
            worker_database.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        winner = executor.submit(execute, "send:first")
        assert entered_provider.wait(timeout=5)
        loser = executor.submit(execute, "send:second")
        assert loser.result(timeout=5) is None
        release_provider.set()
        assert winner.result(timeout=5) is not None

    verification_database = SQLiteDatabase(path)
    _, _, verification_operations, _ = stack(verification_database, provider)
    assert (
        verification_operations.get("send:second").status
        == ExternalActionStatus.FAILED
    )
    verification_database.close()
    assert [call for call in provider.calls if call[0] == "send"] == [
        ("send", "send:first")
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"message_id": "../bad"},
        {"thread_id": "bad thread"},
        {"sender": "bad\x00sender"},
        {"subject": "\x01unsafe"},
        {"body": " "},
        {"body": "x" * 100_001},
    ],
)
def test_malformed_untrusted_message_input_is_rejected(changes):
    values = {
        "message_id": "message-1",
        "thread_id": "thread-1",
        "sender": "candidate@example.test",
        "recipient": "recruiter@example.test",
        "subject": "Subject",
        "body": "Body",
        "direction": MessageDirection.DRAFT,
    }
    values.update(changes)
    with pytest.raises((TypeError, ValueError)):
        CommunicationMessage(**values)


@pytest.mark.parametrize(
    "field",
    ["subject", "body"],
)
def test_lone_unicode_surrogates_are_rejected(field):
    values = {
        "message_id": "message-1",
        "thread_id": "thread-1",
        "sender": "candidate@example.test",
        "recipient": "recruiter@example.test",
        "subject": "Subject",
        "body": "Body",
        "direction": MessageDirection.DRAFT,
    }
    values[field] = "malformed \ud800 text"

    with pytest.raises(ValueError, match="Unicode surrogate"):
        CommunicationMessage(**values)


@pytest.mark.parametrize(
    "changes,error_type",
    [
        ({"message_id": 42}, TypeError),
        ({"direction": "draft"}, TypeError),
        ({"created_at": "2026-01-02T00:00:00Z"}, TypeError),
        ({"created_at": datetime(2026, 1, 2)}, ValueError),
    ],
)
def test_message_types_and_timestamps_are_strictly_validated(changes, error_type):
    values = {
        "message_id": "message-1",
        "thread_id": "thread-1",
        "sender": "candidate@example.test",
        "recipient": "recruiter@example.test",
        "subject": "Subject",
        "body": "Body",
        "direction": MessageDirection.DRAFT,
    }
    values.update(changes)
    with pytest.raises(error_type):
        CommunicationMessage(**values)


def test_message_timestamp_is_deterministically_normalized_to_utc():
    value = message()
    offset_value = replace(
        value,
        created_at=datetime(2026, 1, 2, 2, tzinfo=timezone(timedelta(hours=2))),
    )

    assert offset_value.created_at == datetime(2026, 1, 2, tzinfo=timezone.utc)
    assert offset_value.created_at.tzinfo is timezone.utc


def test_read_validates_and_persists_inbound_provider_content():
    provider = FakeCommunicationAdapter()
    inbound = message(direction=MessageDirection.INBOUND)
    provider.draft(inbound)
    service, repository, _, _ = stack(SQLiteDatabase(), provider)

    assert service.read("message-1") == inbound
    assert repository.get("message-1") == inbound


def test_reply_rejects_mismatched_parent_before_action_is_prepared():
    service, repository, operations, provider = stack(SQLiteDatabase())
    repository.save(message(direction=MessageDirection.INBOUND))

    with pytest.raises(ValueError, match="identifiers"):
        service.reply(
            "reply:1",
            "message-1",
            message("message-2", thread_id="other", in_reply_to="message-1"),
            human_approved=True,
        )

    assert operations.get("reply:1") is None
    assert provider.calls == []


def test_reply_requires_human_approval_without_provider_side_effect():
    service, repository, operations, provider = stack(SQLiteDatabase())
    repository.save(message(direction=MessageDirection.INBOUND))
    reply = message("message-2", in_reply_to="message-1")

    with pytest.raises(PermissionError, match="human approval"):
        service.reply(
            "reply:1", "message-1", reply, human_approved=False
        )

    assert operations.get("reply:1").status == ExternalActionStatus.PREPARED
    assert provider.calls == []


def test_malformed_persisted_message_is_rejected():
    database = SQLiteDatabase()
    repository = SQLiteCommunicationRepository(database)
    repository.save(message())
    database.connection.execute(
        "UPDATE communication_messages SET body = ? WHERE message_id = ?",
        ("\x00", "message-1"),
    )
    database.connection.commit()

    with pytest.raises(ValueError, match="malformed"):
        repository.get("message-1")


def test_thread_ordering_uses_instants_instead_of_timestamp_text():
    database = SQLiteDatabase()
    database.connection.execute(
        """
        CREATE TABLE communication_messages (
            message_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
            sender TEXT NOT NULL, recipient TEXT NOT NULL, subject TEXT NOT NULL,
            body TEXT NOT NULL, direction TEXT NOT NULL, created_at TEXT NOT NULL,
            in_reply_to TEXT, delivery_operation_id TEXT
        )
        """
    )
    values = (
        "thread-1", "candidate@example.test", "recruiter@example.test",
        "Opportunity", "Body", "inbound", None, None,
    )
    database.connection.execute(
        """INSERT INTO communication_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("message-first", *values[:6], "2026-01-01T01:00:00+02:00", *values[6:]),
    )
    database.connection.execute(
        """INSERT INTO communication_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("message-second", *values[:6], "2025-12-31T23:30:00+00:00", *values[6:]),
    )
    database.connection.commit()
    repository = SQLiteCommunicationRepository(database)

    assert tuple(item.message_id for item in repository.list_thread("thread-1")) == (
        "message-first",
        "message-second",
    )


def test_thread_ordering_preserves_exact_microsecond_precision():
    repository = SQLiteCommunicationRepository(SQLiteDatabase())
    base = datetime(2026, 1, 2, tzinfo=timezone.utc)
    earlier = replace(message("z-earlier"), created_at=base)
    later = replace(message("a-later"), created_at=base + timedelta(microseconds=1))
    repository.save(later)
    repository.save(earlier)

    assert tuple(item.message_id for item in repository.list_thread("thread-1")) == (
        "z-earlier",
        "a-later",
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("message_id", "different-message"),
        ("thread_id", "different-thread"),
        ("recipient", "attacker@example.test"),
        ("body", "Provider changed the body"),
        ("direction", MessageDirection.DRAFT),
    ],
)
def test_provider_delivery_must_match_prepared_send_intent(field, value):
    class MutatingProvider(FakeCommunicationAdapter):
        def send(self, operation_id, prepared):
            delivered = super().send(operation_id, prepared)
            return replace(delivered, **{field: value})

    provider = MutatingProvider()
    service, repository, operations, _ = stack(SQLiteDatabase(), provider)
    service.create_draft(message())

    assert service.send("send:1", "message-1", human_approved=True) is None
    failed = operations.get("send:1")
    assert failed is not None
    assert failed.status == ExternalActionStatus.RECONCILIATION_REQUIRED
    assert "could not be persisted" in failed.error
    assert repository.get("message-1").direction == MessageDirection.DRAFT


def test_provider_delivery_must_match_prepared_reply_intent():
    class MutatingReplyProvider(FakeCommunicationAdapter):
        def reply(self, operation_id, parent, prepared):
            delivered = super().reply(operation_id, parent, prepared)
            return replace(delivered, in_reply_to="different-parent")

    provider = MutatingReplyProvider()
    service, repository, operations, _ = stack(SQLiteDatabase(), provider)
    repository.save(message(direction=MessageDirection.INBOUND))

    assert service.reply(
        "reply:1",
        "message-1",
        message("message-2", in_reply_to="message-1"),
        human_approved=True,
    ) is None
    assert (
        operations.get("reply:1").status
        == ExternalActionStatus.RECONCILIATION_REQUIRED
    )
    assert repository.get("message-2").direction == MessageDirection.DRAFT


def test_provider_success_followed_by_repository_failure_requires_reconciliation():
    class FailingDeliveryRepository(SQLiteCommunicationRepository):
        def save(self, value):
            if value.direction == MessageDirection.OUTBOUND:
                raise RuntimeError("database unavailable after provider success")
            return super().save(value)

    database = SQLiteDatabase()
    provider = FakeCommunicationAdapter()
    messages = FailingDeliveryRepository(database)
    operations = SQLiteExternalActionOperationRepository(database)
    external = ExternalActionService(
        operations, CommunicationService.action_adapter(provider, messages)
    )
    service = CommunicationService(messages, provider, external)
    service.create_draft(message())

    assert service.send("send:1", "message-1", human_approved=True) is None

    operation = operations.get("send:1")
    assert operation is not None
    assert operation.status == ExternalActionStatus.RECONCILIATION_REQUIRED
    assert provider.calls.count(("send", "send:1")) == 1
    assert messages.get("message-1").direction == MessageDirection.DRAFT

    assert service.send("send:2", "message-1", human_approved=True) is None
    assert operations.get("send:2").status == ExternalActionStatus.FAILED
    assert provider.calls.count(("send", "send:2")) == 0


def test_concurrent_identical_message_saves_return_the_winning_row(tmp_path):
    path = str(tmp_path / "identical-save.sqlite")
    setup_database = SQLiteDatabase(path)
    SQLiteCommunicationRepository(setup_database)
    setup_database.close()
    barrier = Barrier(2)

    class SynchronizedRepository(SQLiteCommunicationRepository):
        def __init__(self, database):
            super().__init__(database)
            self._first_get = True

        def get(self, message_id):
            result = super().get(message_id)
            if self._first_get:
                self._first_get = False
                barrier.wait(timeout=5)
            return result

    def save_identical():
        database = SQLiteDatabase(path)
        repository = SynchronizedRepository(database)
        try:
            return repository.save(message())
        finally:
            database.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: save_identical(), range(2)))

    assert results == (message(), message())


@pytest.mark.parametrize(
    "conflicting",
    [
        message(direction=MessageDirection.OUTBOUND),
        replace(message(), body="Conflicting content"),
    ],
)
def test_concurrent_conflicting_message_saves_reject_the_loser(tmp_path, conflicting):
    path = str(tmp_path / "conflicting-save.sqlite")
    setup_database = SQLiteDatabase(path)
    SQLiteCommunicationRepository(setup_database)
    setup_database.close()
    barrier = Barrier(2)

    class SynchronizedRepository(SQLiteCommunicationRepository):
        def __init__(self, database):
            super().__init__(database)
            self._first_get = True

        def get(self, message_id):
            result = super().get(message_id)
            if self._first_get:
                self._first_get = False
                barrier.wait(timeout=5)
            return result

    def save_value(value):
        database = SQLiteDatabase(path)
        repository = SynchronizedRepository(database)
        try:
            return repository.save(value)
        except ValueError as exc:
            return exc
        finally:
            database.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(
                save_value,
                (message(), conflicting),
            )
        )

    assert sum(isinstance(result, CommunicationMessage) for result in results) == 1
    errors = [result for result in results if isinstance(result, ValueError)]
    assert len(errors) == 1
    assert "different message content" in str(errors[0]) or "direction" in str(errors[0])
