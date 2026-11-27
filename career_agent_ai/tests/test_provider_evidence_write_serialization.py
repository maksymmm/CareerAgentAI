from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event

import pytest

from career_agent_ai.application.career.application_submission import EvidenceState
from career_agent_ai.application.communication import (
    CommunicationMessage,
    DeliveryEvidenceState,
    MessageDirection,
)
from career_agent_ai.application.scheduling import (
    CalendarEvidenceState,
    ScheduleEvent,
    ScheduleEventType,
    ScheduleStatus,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_sandbox_calendar_provider import (
    SandboxCalendarOutcome,
    SQLiteSandboxCalendarProvider,
)
from career_agent_ai.application.storage.sqlite_sandbox_communication_provider import (
    SandboxCommunicationOutcome,
    SQLiteSandboxCommunicationProvider,
)
from career_agent_ai.application.storage.sqlite_sandbox_submission_provider import (
    SandboxOutcome,
    SQLiteSandboxSubmissionProvider,
)

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
OPERATION_ID = "evidence-lock:operation"
MESSAGE_ID = "evidence-lock-message"
EVENT_ID = "evidence-lock-interview"
APPLICATION_ID = "evidence-lock-application"


def _message() -> CommunicationMessage:
    return CommunicationMessage(
        message_id=MESSAGE_ID,
        thread_id="evidence-lock-thread",
        sender="candidate@example.test",
        recipient="recruiter@example.test",
        subject="Sandbox opportunity",
        body="Synthetic message retained inside the local provider.",
        direction=MessageDirection.DRAFT,
        created_at=NOW,
    )


def _event() -> ScheduleEvent:
    start = NOW + timedelta(days=3)
    return ScheduleEvent(
        event_id=EVENT_ID,
        candidate_id="evidence-lock-candidate",
        employer_name="Sandbox Logistics",
        event_type=ScheduleEventType.INTERVIEW,
        location="Karlsruhe",
        start_at=start,
        end_at=start + timedelta(hours=1),
        timezone_name="Europe/Berlin",
        created_at=NOW,
        updated_at=NOW,
    )


def _provider(kind, database, plan):
    if kind == "application":
        return SQLiteSandboxSubmissionProvider(database, plan)
    if kind == "communication":
        return SQLiteSandboxCommunicationProvider(database, plan)
    if kind == "calendar":
        return SQLiteSandboxCalendarProvider(database, plan)
    raise AssertionError(f"Unknown provider kind: {kind}")


def _write(kind, provider):
    if kind == "application":
        return provider.submit(
            OPERATION_ID,
            "evidence-lock-job",
            APPLICATION_ID,
            "synthetic application artifact",
            "a" * 64,
            "b" * 64,
        )
    if kind == "communication":
        return provider.send(OPERATION_ID, _message())
    if kind == "calendar":
        return provider.accept(OPERATION_ID, _event())
    raise AssertionError(f"Unknown provider kind: {kind}")


def _verify(kind, provider):
    if kind == "application":
        return provider.verify_submission(OPERATION_ID)
    if kind == "communication":
        return provider.verify_delivery(OPERATION_ID)
    if kind == "calendar":
        return provider.verify_calendar_action(OPERATION_ID)
    raise AssertionError(f"Unknown provider kind: {kind}")


@pytest.mark.parametrize("kind", ["application", "communication", "calendar"])
def test_provider_evidence_waits_for_inflight_effect_commit(tmp_path, kind):
    """A verifier on another connection must not report absence during a live provider write."""
    path = str(tmp_path / f"{kind}-evidence-lock.sqlite")
    setup = SQLiteDatabase(path)
    setup_provider = _provider(kind, setup, None)
    if kind == "communication":
        setup_provider.draft(_message())
    setup.close()

    plan_entered = Event()
    release_plan = Event()
    writer_ready = Event()
    verifier_ready = Event()
    start_verification = Event()
    verification_started = Event()
    plan_calls = []

    def plan(operation_id, attempt_no):
        plan_calls.append((operation_id, attempt_no))
        plan_entered.set()
        if not release_plan.wait(timeout=10):
            raise AssertionError("The test did not release the sandbox provider transaction.")
        if kind == "application":
            return SandboxOutcome.TIMEOUT_AFTER_EFFECT
        if kind == "communication":
            return SandboxCommunicationOutcome.TIMEOUT_AFTER_EFFECT
        return SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT

    def write():
        database = SQLiteDatabase(path)
        provider = _provider(kind, database, plan)
        writer_ready.set()
        try:
            _write(kind, provider)
        except TimeoutError:
            return "timeout_after_effect"
        finally:
            database.close()
        raise AssertionError("TIMEOUT_AFTER_EFFECT must commit the effect and lose the response.")

    def verify():
        database = SQLiteDatabase(path)
        provider = _provider(
            kind,
            database,
            lambda *_: pytest.fail("Evidence verification must never invoke the provider plan."),
        )
        verifier_ready.set()
        try:
            if not start_verification.wait(timeout=10):
                raise AssertionError("The test did not start evidence verification.")
            verification_started.set()
            return _verify(kind, provider)
        finally:
            database.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        verify_future = executor.submit(verify)
        assert verifier_ready.wait(timeout=5)
        write_future = executor.submit(write)
        assert writer_ready.wait(timeout=5)
        assert plan_entered.wait(timeout=5)
        start_verification.set()
        try:
            assert verification_started.wait(timeout=5)
            with pytest.raises(FutureTimeoutError):
                verify_future.result(timeout=0.05)
        finally:
            release_plan.set()

        assert write_future.result(timeout=5) == "timeout_after_effect"
        evidence = verify_future.result(timeout=5)

    assert plan_calls == [(OPERATION_ID, 1)]
    if kind == "application":
        assert evidence.state is EvidenceState.PRESENT
        assert evidence.application_id == APPLICATION_ID
        attempts_table = "sandbox_submission_attempts"
        key_column = "idempotency_key"
        effect_table = "sandbox_submission_ledger"
    elif kind == "communication":
        assert evidence.state is DeliveryEvidenceState.PRESENT
        assert evidence.message.message_id == MESSAGE_ID
        assert evidence.message.direction is MessageDirection.OUTBOUND
        attempts_table = "sandbox_communication_attempts"
        key_column = "operation_id"
        effect_table = "sandbox_communication_deliveries"
    else:
        assert evidence.state is CalendarEvidenceState.PRESENT
        assert evidence.event.event_id == EVENT_ID
        assert evidence.event.status is ScheduleStatus.ACCEPTED
        attempts_table = "sandbox_calendar_attempts"
        key_column = "operation_id"
        effect_table = "sandbox_calendar_effects"

    verification = SQLiteDatabase(path)
    try:
        attempts = verification.connection.execute(
            f"SELECT attempt_no, outcome FROM {attempts_table} "
            f"WHERE {key_column} = ? ORDER BY attempt_no",
            (OPERATION_ID,),
        ).fetchall()
        effect = verification.connection.execute(
            f"SELECT replays FROM {effect_table} WHERE {key_column} = ?",
            (OPERATION_ID,),
        ).fetchone()
        assert attempts == [(1, "timeout_after_effect")]
        assert effect == (0,)
        assert verification.connection.execute(
            f"SELECT COUNT(*) FROM {effect_table} WHERE {key_column} = ?",
            (OPERATION_ID,),
        ).fetchone() == (1,)
    finally:
        verification.close()


def test_ci_runs_independent_coverage_gate_after_full_pytest():
    """The CLI coverage gate must run after pytest, including a failed test run."""
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")
    assert "name: Run test suite" in workflow
    assert "id: pytest" in workflow
    assert "name: Enforce independent coverage threshold" in workflow
    assert "python -m pytest" in workflow
    assert "python -m coverage report --precision=2 --fail-under=90" in workflow
    assert "always() && !cancelled()" in workflow
    assert "steps.pytest.outcome != 'skipped'" in workflow
    assert workflow.index("Run test suite") < workflow.index(
        "Enforce independent coverage threshold"
    )
