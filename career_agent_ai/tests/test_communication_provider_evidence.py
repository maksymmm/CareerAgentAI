from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import sqlite3
from threading import Barrier, Lock

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



def test_busy_communication_writer_lock_records_no_attempt_and_allows_same_intent_retry(
    tmp_path,
):
    path = str(tmp_path / "messages-busy-writer.db")
    database = SQLiteDatabase(path)
    provider = SQLiteSandboxCommunicationProvider(database)
    message = _message()
    provider.draft(message)
    database.connection.execute("PRAGMA busy_timeout = 0")
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(PreDeliveryCommunicationError, match="busy; nothing was attempted"):
            provider.send("send:busy-writer", message)
    finally:
        holder.rollback()
        holder.close()

    assert database.connection.execute(
        "SELECT COUNT(*) FROM sandbox_communication_intents WHERE operation_id = ?",
        ("send:busy-writer",),
    ).fetchone()[0] == 0
    assert database.connection.execute(
        "SELECT COUNT(*) FROM sandbox_communication_attempts WHERE operation_id = ?",
        ("send:busy-writer",),
    ).fetchone()[0] == 0
    assert provider.delivery_count("send:busy-writer") == 0
    assert provider.verify_delivery(
        "send:busy-writer"
    ).state == DeliveryEvidenceState.ABSENT_CONFIRMED

    delivered = provider.send("send:busy-writer", message)
    assert delivered.direction == MessageDirection.OUTBOUND
    assert provider.delivery_count("send:busy-writer") == 1
    attempts = database.connection.execute(
        "SELECT attempt_no, outcome FROM sandbox_communication_attempts "
        "WHERE operation_id = ?",
        ("send:busy-writer",),
    ).fetchall()
    assert attempts == [(1, SandboxCommunicationOutcome.SUCCESS.value)]
    database.close()


def test_communication_evidence_lookup_fails_closed_under_competing_writer_lock(
    tmp_path,
):
    path = str(tmp_path / "messages-evidence-busy.db")
    database = SQLiteDatabase(path)
    provider = SQLiteSandboxCommunicationProvider(database)
    message = _message()
    provider.draft(message)
    delivered = provider.send("send:evidence-busy", message)
    database.connection.execute("PRAGMA busy_timeout = 0")
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(PreDeliveryCommunicationError, match="busy; nothing was attempted"):
            provider.verify_delivery("send:evidence-busy")
    finally:
        holder.rollback()
        holder.close()

    evidence = provider.verify_delivery("send:evidence-busy")
    assert evidence.state == DeliveryEvidenceState.PRESENT
    assert evidence.message == delivered
    assert provider.delivery_count("send:evidence-busy") == 1
    database.close()


def test_concurrent_same_key_communication_calls_commit_one_delivery(tmp_path):
    path = str(tmp_path / "messages-concurrent-same-key.db")
    initialized = SQLiteDatabase(path)
    provider = SQLiteSandboxCommunicationProvider(initialized)
    original = _message()
    provider.draft(original)
    initialized.close()

    barrier = Barrier(2)
    setup_lock = Lock()
    plan_lock = Lock()
    plan_calls = []

    def plan(operation_id, attempt):
        with plan_lock:
            plan_calls.append((operation_id, attempt))
        return SandboxCommunicationOutcome.SUCCESS

    def deliver():
        database = SQLiteDatabase(path)
        try:
            with setup_lock:
                provider = SQLiteSandboxCommunicationProvider(database, plan)
            barrier.wait(timeout=10)
            return provider.send("send:concurrent", original)
        finally:
            database.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(deliver) for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]

    assert results[0] == results[1]
    assert plan_calls == [("send:concurrent", 1)]

    verifier = SQLiteDatabase(path)
    provider = SQLiteSandboxCommunicationProvider(verifier)
    attempts = verifier.connection.execute(
        "SELECT attempt_no, outcome FROM sandbox_communication_attempts "
        "WHERE operation_id = ? ORDER BY attempt_no",
        ("send:concurrent",),
    ).fetchall()
    deliveries = verifier.connection.execute(
        "SELECT message_id, action, replays FROM sandbox_communication_deliveries "
        "WHERE operation_id = ?",
        ("send:concurrent",),
    ).fetchall()
    assert attempts == [(1, SandboxCommunicationOutcome.SUCCESS.value)]
    assert len(deliveries) == 1
    message_id, action, replays = deliveries[0]
    assert action == "send"
    assert replays == 1
    assert provider.delivery_count("send:concurrent") == 1
    assert provider.read(message_id) == results[0]
    verifier.close()


def test_concurrent_conflicting_message_intents_cannot_rebind_one_operation_id(tmp_path):
    path = str(tmp_path / "messages-concurrent-conflict.db")
    initialized = SQLiteDatabase(path)
    provider = SQLiteSandboxCommunicationProvider(initialized)
    original = _message()
    provider.draft(original)
    initialized.close()

    barrier = Barrier(2)
    setup_lock = Lock()
    plan_lock = Lock()
    plan_calls = []

    def plan(operation_id, attempt):
        with plan_lock:
            plan_calls.append((operation_id, attempt))
        return SandboxCommunicationOutcome.SUCCESS

    def deliver(message):
        database = SQLiteDatabase(path)
        try:
            with setup_lock:
                provider = SQLiteSandboxCommunicationProvider(database, plan)
            barrier.wait(timeout=10)
            try:
                return ("success", provider.send("send:conflict", message))
            except PreDeliveryCommunicationError:
                return ("conflict", None)
        finally:
            database.close()

    messages = (original, replace(original, body="Different approved content"))
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(deliver, message) for message in messages]
        outcomes = [future.result(timeout=30) for future in futures]

    assert sorted(kind for kind, _value in outcomes) == ["conflict", "success"]
    assert plan_calls == [("send:conflict", 1)]

    verifier = SQLiteDatabase(path)
    provider = SQLiteSandboxCommunicationProvider(verifier)
    assert provider.delivery_count("send:conflict") == 1
    attempts = verifier.connection.execute(
        "SELECT attempt_no, outcome FROM sandbox_communication_attempts "
        "WHERE operation_id = ?",
        ("send:conflict",),
    ).fetchall()
    evidence = provider.verify_delivery("send:conflict")
    successful_message = next(value for kind, value in outcomes if kind == "success")
    assert attempts == [(1, SandboxCommunicationOutcome.SUCCESS.value)]
    assert evidence.state == DeliveryEvidenceState.PRESENT
    assert evidence.message == successful_message
    verifier.close()
