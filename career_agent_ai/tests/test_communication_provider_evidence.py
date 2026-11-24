from __future__ import annotations

from datetime import datetime, timezone

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


def test_unknown_operation_and_intent_without_attempts_are_inconclusive(tmp_path):
    database = SQLiteDatabase(str(tmp_path / "messages.db"))
    provider = SQLiteSandboxCommunicationProvider(database)

    assert provider.verify_delivery("never-prepared").state == DeliveryEvidenceState.UNKNOWN

    message = _message()
    provider.draft(message)
    fingerprint = provider._fingerprint("send", message)
    database.connection.execute(
        "INSERT INTO sandbox_communication_intents VALUES (?, ?)",
        ("prepared-without-attempt", fingerprint),
    )
    database.connection.commit()

    assert (
        provider.verify_delivery("prepared-without-attempt").state
        == DeliveryEvidenceState.UNKNOWN
    )
    database.close()


def test_gapped_communication_attempt_history_is_inconclusive(tmp_path):
    database = SQLiteDatabase(str(tmp_path / "messages.db"))
    provider = SQLiteSandboxCommunicationProvider(
        database, lambda _key, _attempt: SandboxCommunicationOutcome.NO_EFFECT
    )
    provider.draft(_message())
    with pytest.raises(PreDeliveryCommunicationError, match="no delivery"):
        provider.send("send:gapped", _message())

    database.connection.execute(
        "UPDATE sandbox_communication_attempts SET attempt_no = 2 "
        "WHERE operation_id = ?",
        ("send:gapped",),
    )
    database.connection.commit()
    assert provider.verify_delivery("send:gapped").state == DeliveryEvidenceState.UNKNOWN
    database.close()


def test_unrecognized_communication_attempt_outcome_is_inconclusive(tmp_path):
    database = SQLiteDatabase(str(tmp_path / "messages.db"))
    provider = SQLiteSandboxCommunicationProvider(
        database, lambda _key, _attempt: SandboxCommunicationOutcome.NO_EFFECT
    )
    provider.draft(_message())
    with pytest.raises(PreDeliveryCommunicationError, match="no delivery"):
        provider.send("send:unknown-outcome", _message())

    database.connection.execute(
        "UPDATE sandbox_communication_attempts SET outcome = ? "
        "WHERE operation_id = ?",
        ("provider_claimed_absent", "send:unknown-outcome"),
    )
    database.connection.commit()
    assert (
        provider.verify_delivery("send:unknown-outcome").state
        == DeliveryEvidenceState.UNKNOWN
    )
    database.close()


@pytest.mark.parametrize(
    "outcome",
    [
        SandboxCommunicationOutcome.SUCCESS,
        SandboxCommunicationOutcome.TIMEOUT_AFTER_EFFECT,
    ],
)
def test_missing_delivery_after_effect_attempt_is_inconclusive(tmp_path, outcome):
    database = SQLiteDatabase(str(tmp_path / "messages.db"))
    provider = SQLiteSandboxCommunicationProvider(
        database, lambda _key, _attempt: outcome
    )
    provider.draft(_message())
    if outcome == SandboxCommunicationOutcome.TIMEOUT_AFTER_EFFECT:
        with pytest.raises(TimeoutError, match="after recording delivery"):
            provider.send("send:missing-effect", _message())
    else:
        provider.send("send:missing-effect", _message())

    database.connection.execute(
        "DELETE FROM sandbox_communication_deliveries WHERE operation_id = ?",
        ("send:missing-effect",),
    )
    database.connection.commit()
    assert (
        provider.verify_delivery("send:missing-effect").state
        == DeliveryEvidenceState.UNKNOWN
    )
    database.close()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("fingerprint", "f" * 64),
        ("action", "unsupported"),
    ],
)
def test_delivery_metadata_must_match_durable_intent(tmp_path, column, value):
    database = SQLiteDatabase(str(tmp_path / "messages.db"))
    provider = SQLiteSandboxCommunicationProvider(database)
    message = _message()
    provider.draft(message)
    provider.send("send:corrupt-metadata", message)

    database.connection.execute(
        f"UPDATE sandbox_communication_deliveries SET {column} = ? "
        "WHERE operation_id = ?",
        (value, "send:corrupt-metadata"),
    )
    database.connection.commit()
    assert (
        provider.verify_delivery("send:corrupt-metadata").state
        == DeliveryEvidenceState.UNKNOWN
    )
    database.close()


def test_delivery_without_its_outbound_message_is_inconclusive(tmp_path):
    database = SQLiteDatabase(str(tmp_path / "messages.db"))
    provider = SQLiteSandboxCommunicationProvider(database)
    message = _message()
    provider.draft(message)
    provider.send("send:missing-message", message)

    database.connection.execute(
        "DELETE FROM sandbox_communication_messages WHERE message_id = ?",
        (message.message_id,),
    )
    database.connection.commit()
    assert (
        provider.verify_delivery("send:missing-message").state
        == DeliveryEvidenceState.UNKNOWN
    )
    database.close()
