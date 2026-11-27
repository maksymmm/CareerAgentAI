from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from threading import Event

import pytest

from career_agent_ai.application.communication import (
    CommunicationMessage,
    CommunicationReconciliationEvidenceError,
    CommunicationService,
    DeliveryEvidenceState,
    MessageDirection,
    PreDeliveryCommunicationError,
)
from career_agent_ai.application.external_actions import (
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
from career_agent_ai.application.storage.sqlite_sandbox_communication_provider import (
    SandboxCommunicationOutcome,
    SQLiteSandboxCommunicationProvider,
)


def _message() -> CommunicationMessage:
    return CommunicationMessage(
        message_id="message-evidence",
        thread_id="thread-evidence",
        sender="candidate@example.test",
        recipient="recruiter@example.test",
        subject="Sandbox opportunity",
        body="This stays inside the sandbox.",
        direction=MessageDirection.DRAFT,
        created_at=datetime(2026, 11, 19, tzinfo=timezone.utc),
    )


def _stack(database: SQLiteDatabase, plan):
    provider = SQLiteSandboxCommunicationProvider(database, plan)
    messages = SQLiteCommunicationRepository(database)
    operations = SQLiteExternalActionOperationRepository(database)
    external = ExternalActionService(
        operations, CommunicationService.action_adapter(provider, messages)
    )
    service = CommunicationService(
        messages, provider, external, require_provider_evidence=True
    )
    return service, provider, operations


def test_timeout_after_effect_requires_present_provider_evidence(tmp_path):
    path = str(tmp_path / "messages.db")
    database = SQLiteDatabase(path)
    service, provider, operations = _stack(
        database,
        lambda _key, _attempt: SandboxCommunicationOutcome.TIMEOUT_AFTER_EFFECT,
    )
    service.create_draft(_message())

    assert service.send(
        "send:evidence", "message-evidence", human_approved=True
    ) is None
    assert operations.get("send:evidence").status == ExternalActionStatus.RECONCILIATION_REQUIRED
    assert provider.delivery_count("send:evidence") == 1
    database.close()

    restarted = SQLiteDatabase(path)
    service, provider, operations = _stack(restarted, None)
    evidence = provider.verify_delivery("send:evidence")
    assert evidence.state == DeliveryEvidenceState.PRESENT

    with pytest.raises(
        CommunicationReconciliationEvidenceError,
        match="no delivery",
    ):
        service.resolve_reconciliation("send:evidence", delivered=False)
    assert operations.get("send:evidence").status == ExternalActionStatus.RECONCILIATION_REQUIRED

    resolved = service.resolve_reconciliation("send:evidence", delivered=True)
    assert resolved.status == ExternalActionStatus.SUCCEEDED
    assert provider.delivery_count("send:evidence") == 1


def test_interrupted_before_effect_rejects_false_positive_then_retries(tmp_path):
    path = str(tmp_path / "messages.db")
    database = SQLiteDatabase(path)
    service, provider, operations = _stack(
        database,
        lambda _key, attempt: (
            SandboxCommunicationOutcome.INTERRUPTED_BEFORE_EFFECT
            if attempt == 1
            else SandboxCommunicationOutcome.SUCCESS
        ),
    )
    service.create_draft(_message())
    assert service.send(
        "send:evidence", "message-evidence", human_approved=True
    ) is None
    assert provider.verify_delivery("send:evidence").state == DeliveryEvidenceState.ABSENT_CONFIRMED

    with pytest.raises(CommunicationReconciliationEvidenceError, match="does not match"):
        service.resolve_reconciliation("send:evidence", delivered=True)
    reopened = service.resolve_reconciliation("send:evidence", delivered=False)
    assert reopened.status == ExternalActionStatus.PREPARED

    delivered = service.send(
        "send:evidence", "message-evidence", human_approved=True
    )
    assert delivered is not None and delivered.direction == MessageDirection.OUTBOUND
    assert provider.delivery_count("send:evidence") == 1


def test_durable_provider_replays_one_effect_and_rejects_changed_intent(tmp_path):
    path = str(tmp_path / "messages.db")
    database = SQLiteDatabase(path)
    provider = SQLiteSandboxCommunicationProvider(database)
    original = _message()
    assert provider.draft(original) == original
    delivered = provider.send("send:evidence", original)
    assert provider.read(original.message_id) == delivered
    database.close()

    restarted = SQLiteDatabase(path)
    provider = SQLiteSandboxCommunicationProvider(restarted)
    assert provider.send("send:evidence", original) == delivered
    assert provider.delivery_count("send:evidence") == 1
    changed = CommunicationMessage(
        **{**original.__dict__, "body": "Changed body"}
    )
    with pytest.raises(PreDeliveryCommunicationError, match="different message intent"):
        provider.send("send:evidence", changed)
    assert provider.delivery_count("send:evidence") == 1


def test_provider_confirms_no_effect_and_rejects_nested_evidence_lookup(tmp_path):
    database = SQLiteDatabase(str(tmp_path / "messages.db"))
    provider = SQLiteSandboxCommunicationProvider(
        database,
        lambda _key, _attempt: SandboxCommunicationOutcome.NO_EFFECT,
    )
    provider.draft(_message())
    with pytest.raises(PreDeliveryCommunicationError, match="no delivery"):
        provider.send("send:evidence", _message())
    assert provider.verify_delivery("send:evidence").state == DeliveryEvidenceState.ABSENT_CONFIRMED

    database.connection.execute("BEGIN")
    with pytest.raises(PreDeliveryCommunicationError, match="inside a transaction"):
        provider.verify_delivery("send:evidence")
    database.connection.rollback()


def test_service_requires_a_verifiable_provider():
    from career_agent_ai.application.communication import FakeCommunicationAdapter

    database = SQLiteDatabase()
    provider = FakeCommunicationAdapter()
    messages = SQLiteCommunicationRepository(database)
    external = ExternalActionService(
        SQLiteExternalActionOperationRepository(database),
        CommunicationService.action_adapter(provider, messages),
    )
    with pytest.raises(TypeError, match="does not support"):
        CommunicationService(
            messages, provider, external, require_provider_evidence=True
        )



def test_live_duplicate_reply_after_effect_is_reconciled_without_redelivery(tmp_path):
    """A live same-key reply duplicate observes IN_PROGRESS and never calls the provider."""
    path = str(tmp_path / "reply-live-duplicate.sqlite")
    parent = CommunicationMessage(
        message_id="message-recruiter-parent",
        thread_id="thread-interview-reply",
        sender="recruiter@example.test",
        recipient="candidate@example.test",
        subject="Interview availability",
        body="Could you confirm the proposed interview slot?",
        direction=MessageDirection.INBOUND,
        created_at=datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc),
    )
    reply = CommunicationMessage(
        message_id="message-candidate-reply",
        thread_id=parent.thread_id,
        sender=parent.recipient,
        recipient=parent.sender,
        subject="Re: Interview availability",
        body="I confirm the proposed interview slot.",
        direction=MessageDirection.DRAFT,
        created_at=datetime(2026, 11, 27, 9, 5, tzinfo=timezone.utc),
        in_reply_to=parent.message_id,
    )
    operation_id = "reply:live-duplicate-after-effect"

    def build_service(database, provider):
        messages = SQLiteCommunicationRepository(database)
        operations = SQLiteExternalActionOperationRepository(database)
        external = ExternalActionService(
            operations,
            CommunicationService.action_adapter(provider, messages),
        )
        service = CommunicationService(
            messages, provider, external, require_provider_evidence=True
        )
        return service, messages, operations

    setup = SQLiteDatabase(path)
    setup_provider = SQLiteSandboxCommunicationProvider(setup)
    setup_provider._save_message(parent)
    setup_service, setup_messages, _ = build_service(setup, setup_provider)
    setup_messages.save(parent)
    setup_service.create_draft(reply)
    setup.close()

    effect_committed = Event()
    release_first = Event()
    first_plan_calls: list[tuple[str, int]] = []
    duplicate_plan_calls: list[tuple[str, int]] = []

    def first_plan(key: str, attempt_no: int) -> SandboxCommunicationOutcome:
        first_plan_calls.append((key, attempt_no))
        return SandboxCommunicationOutcome.TIMEOUT_AFTER_EFFECT

    def unexpected_duplicate_plan(key: str, attempt_no: int):
        duplicate_plan_calls.append((key, attempt_no))
        raise AssertionError("A live duplicate must not invoke the provider outcome plan.")

    class PausingProvider(SQLiteSandboxCommunicationProvider):
        def _deliver(self, action, key, message):
            try:
                return super()._deliver(action, key, message)
            except TimeoutError:
                # The sandbox delivery and attempt are already committed. Hold the
                # lost response before ExternalActionService records reconciliation.
                effect_committed.set()
                if not release_first.wait(timeout=10):
                    raise AssertionError("The first reply response was not released.")
                raise

    def first_reply():
        database = SQLiteDatabase(path)
        provider = PausingProvider(database, first_plan)
        service, _, _ = build_service(database, provider)
        try:
            return service.reply(
                operation_id, parent.message_id, reply, human_approved=True
            )
        finally:
            database.close()

    def duplicate_reply():
        database = SQLiteDatabase(path)
        provider = SQLiteSandboxCommunicationProvider(database, unexpected_duplicate_plan)
        service, _, operations = build_service(database, provider)
        try:
            result = service.reply(
                operation_id, parent.message_id, reply, human_approved=True
            )
            operation = operations.get(operation_id)
            return result, operation, provider.delivery_count(operation_id)
        finally:
            database.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(first_reply)
        try:
            assert effect_committed.wait(timeout=5)
            duplicate = executor.submit(duplicate_reply)
            duplicate_result, observed_operation, observed_deliveries = duplicate.result(timeout=5)
            assert duplicate_result is None
            assert observed_operation is not None
            assert observed_operation.status == ExternalActionStatus.IN_PROGRESS
            assert observed_deliveries == 1
            assert not first.done()
        finally:
            release_first.set()
        assert first.result(timeout=5) is None

    assert first_plan_calls == [(operation_id, 1)]
    assert duplicate_plan_calls == []

    restarted = SQLiteDatabase(path)
    provider = SQLiteSandboxCommunicationProvider(
        restarted,
        lambda *_: pytest.fail("Evidence reconciliation must not invoke the provider plan."),
    )
    service, messages, operations = build_service(restarted, provider)
    expected_reply = replace(reply, direction=MessageDirection.OUTBOUND)

    operation = operations.get(operation_id)
    assert operation is not None
    assert operation.status == ExternalActionStatus.RECONCILIATION_REQUIRED
    assert provider.delivery_count(operation_id) == 1
    evidence = provider.verify_delivery(operation_id)
    assert evidence.state == DeliveryEvidenceState.PRESENT
    assert evidence.message == expected_reply

    attempts = restarted.connection.execute(
        "SELECT attempt_no, outcome FROM sandbox_communication_attempts "
        "WHERE operation_id = ? ORDER BY attempt_no",
        (operation_id,),
    ).fetchall()
    delivery = restarted.connection.execute(
        "SELECT action, replays, message_id FROM sandbox_communication_deliveries "
        "WHERE operation_id = ?",
        (operation_id,),
    ).fetchone()
    assert attempts == [(1, SandboxCommunicationOutcome.TIMEOUT_AFTER_EFFECT.value)]
    assert delivery == ("reply", 0, reply.message_id)

    resolved = service.resolve_reconciliation(operation_id, delivered=True)
    assert resolved.status == ExternalActionStatus.SUCCEEDED
    assert messages.get(parent.message_id) == parent
    assert messages.get(reply.message_id) == expected_reply
    assert service.reply(
        operation_id, parent.message_id, reply, human_approved=True
    ) == expected_reply
    assert provider.delivery_count(operation_id) == 1
    assert restarted.connection.execute(
        "SELECT attempt_no, outcome FROM sandbox_communication_attempts "
        "WHERE operation_id = ? ORDER BY attempt_no",
        (operation_id,),
    ).fetchall() == attempts
    assert restarted.connection.execute(
        "SELECT action, replays, message_id FROM sandbox_communication_deliveries "
        "WHERE operation_id = ?",
        (operation_id,),
    ).fetchone() == delivery
    provider_parent = restarted.connection.execute(
        "SELECT direction, in_reply_to FROM sandbox_communication_messages "
        "WHERE message_id = ?",
        (parent.message_id,),
    ).fetchone()
    provider_reply = restarted.connection.execute(
        "SELECT direction, in_reply_to FROM sandbox_communication_messages "
        "WHERE message_id = ?",
        (reply.message_id,),
    ).fetchone()
    assert provider_parent == (MessageDirection.INBOUND.value, None)
    assert provider_reply == (MessageDirection.OUTBOUND.value, parent.message_id)
    restarted.close()
