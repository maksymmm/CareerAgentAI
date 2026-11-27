from __future__ import annotations

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from threading import Event, Lock

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



def test_concurrent_timeout_after_effect_delivery_has_one_effect_and_restart_replay(tmp_path):
    """A same-key duplicate waits for the committed sandbox delivery, then replays it."""
    path = str(tmp_path / "concurrent-message-delivery.sqlite")
    setup = SQLiteDatabase(path)
    setup_provider = SQLiteSandboxCommunicationProvider(setup)
    message = _message()
    assert setup_provider.draft(message) == message
    setup.close()

    start = Event()
    ready = (Event(), Event())
    begin_entered = (Event(), Event())
    plan_entered = Event()
    release_plan = Event()
    plan_lock = Lock()
    plan_calls: list[tuple[str, int]] = []
    operation_id = "send:concurrent-timeout-after-effect"

    def plan(key: str, attempt_no: int) -> SandboxCommunicationOutcome:
        with plan_lock:
            plan_calls.append((key, attempt_no))
        plan_entered.set()
        if not release_plan.wait(timeout=5):
            raise TimeoutError("Test did not release the communication provider plan.")
        return SandboxCommunicationOutcome.TIMEOUT_AFTER_EFFECT

    class ObservedProvider(SQLiteSandboxCommunicationProvider):
        def __init__(self, database, outcome_plan, index):
            self._begin_observed = begin_entered[index]
            super().__init__(database, outcome_plan)

        def _begin(self):
            self._begin_observed.set()
            super()._begin()

    def deliver(index: int):
        database = SQLiteDatabase(path)
        provider = ObservedProvider(database, plan, index)
        ready[index].set()
        if not start.wait(timeout=5):
            database.close()
            raise TimeoutError("Concurrent communication start was not released.")
        try:
            return provider.send(operation_id, message)
        finally:
            database.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(deliver, 0)
        second = executor.submit(deliver, 1)
        assert ready[0].wait(timeout=5)
        assert ready[1].wait(timeout=5)
        start.set()
        assert plan_entered.wait(timeout=5)
        assert begin_entered[0].wait(timeout=5)
        assert begin_entered[1].wait(timeout=5)
        try:
            # One request holds the SQLite writer transaction inside the outcome plan;
            # the duplicate has reached BEGIN IMMEDIATE and must wait for that commit.
            for future in (first, second):
                with pytest.raises(FutureTimeoutError):
                    future.result(timeout=0.05)
        finally:
            release_plan.set()

        outcomes = []
        for future in (first, second):
            try:
                outcomes.append(("returned", future.result(timeout=5)))
            except TimeoutError:
                outcomes.append(("timeout_after_effect", None))

    returned = [value for kind, value in outcomes if kind == "returned"]
    timed_out = [kind for kind, _ in outcomes if kind == "timeout_after_effect"]
    assert len(returned) == 1
    assert len(timed_out) == 1
    delivered = returned[0]
    assert delivered.message_id == message.message_id
    assert delivered.direction == MessageDirection.OUTBOUND
    assert plan_calls == [(operation_id, 1)]

    verification = SQLiteDatabase(path)
    verifier = SQLiteSandboxCommunicationProvider(
        verification,
        lambda *_: pytest.fail("Evidence verification must not call the outcome plan."),
    )
    attempts = verification.connection.execute(
        "SELECT attempt_no, outcome FROM sandbox_communication_attempts "
        "WHERE operation_id = ? ORDER BY attempt_no",
        (operation_id,),
    ).fetchall()
    delivery = verification.connection.execute(
        "SELECT action, replays, message_id FROM sandbox_communication_deliveries "
        "WHERE operation_id = ?",
        (operation_id,),
    ).fetchone()
    stored_message = verification.connection.execute(
        "SELECT direction FROM sandbox_communication_messages WHERE message_id = ?",
        (message.message_id,),
    ).fetchone()
    assert attempts == [(1, SandboxCommunicationOutcome.TIMEOUT_AFTER_EFFECT.value)]
    assert delivery == ("send", 1, message.message_id)
    assert stored_message == (MessageDirection.OUTBOUND.value,)
    assert verifier.delivery_count(operation_id) == 1
    evidence = verifier.verify_delivery(operation_id)
    assert evidence.state == DeliveryEvidenceState.PRESENT
    assert evidence.message == delivered
    verification.close()

    restarted = SQLiteDatabase(path)
    replay_provider = SQLiteSandboxCommunicationProvider(
        restarted,
        lambda *_: pytest.fail("A durable restart replay must not call the outcome plan."),
    )
    assert replay_provider.send(operation_id, message) == delivered
    assert restarted.connection.execute(
        "SELECT attempt_no, outcome FROM sandbox_communication_attempts "
        "WHERE operation_id = ? ORDER BY attempt_no",
        (operation_id,),
    ).fetchall() == attempts
    assert restarted.connection.execute(
        "SELECT action, replays, message_id FROM sandbox_communication_deliveries "
        "WHERE operation_id = ?",
        (operation_id,),
    ).fetchone() == ("send", 2, message.message_id)
    assert replay_provider.delivery_count(operation_id) == 1
    restarted.close()
