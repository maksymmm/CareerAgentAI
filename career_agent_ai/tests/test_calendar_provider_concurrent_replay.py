from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock

import pytest

from career_agent_ai.application.scheduling.calendar_adapter import CalendarEvidenceState
from career_agent_ai.application.scheduling.models import (
    ScheduleEvent,
    ScheduleEventType,
    ScheduleStatus,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_sandbox_calendar_provider import (
    SandboxCalendarOutcome,
    SQLiteSandboxCalendarProvider,
)


NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
OPERATION_ID = "accept:concurrent-timeout-replay"


def _event() -> ScheduleEvent:
    return ScheduleEvent(
        event_id="calendar-concurrent-replay",
        candidate_id="candidate-concurrent-replay",
        employer_name="Sandbox Logistics",
        event_type=ScheduleEventType.INTERVIEW,
        location="Karlsruhe",
        start_at=datetime(2026, 12, 2, 10, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 12, 2, 11, 0, tzinfo=timezone.utc),
        timezone_name="Europe/Berlin",
        created_at=NOW,
        updated_at=NOW,
    )


def test_concurrent_calendar_timeout_after_effect_has_one_effect_and_restart_replay(tmp_path):
    """A concurrent duplicate observes the committed effect after the first response is lost."""
    path = str(tmp_path / "calendar-concurrent-timeout.sqlite")
    setup = SQLiteDatabase(str(path))
    SQLiteSandboxCalendarProvider(setup)
    setup.close()

    start = Event()
    ready = (Event(), Event())
    calling = (Event(), Event())
    plan_entered = Event()
    release_plan = Event()
    plan_lock = Lock()
    plan_calls: list[tuple[str, int]] = []

    def plan(operation_id: str, attempt_no: int) -> SandboxCalendarOutcome:
        with plan_lock:
            plan_calls.append((operation_id, attempt_no))
        plan_entered.set()
        if not release_plan.wait(timeout=5):
            raise TimeoutError("Test did not release the calendar provider plan.")
        return SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT

    event = _event()

    def accept(index: int):
        database = SQLiteDatabase(str(path))
        provider = SQLiteSandboxCalendarProvider(database, plan)
        ready[index].set()
        if not start.wait(timeout=5):
            database.close()
            raise TimeoutError("Concurrent calendar start was not released.")
        calling[index].set()
        try:
            return provider.accept(OPERATION_ID, event)
        finally:
            database.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(accept, 0)
        second = executor.submit(accept, 1)
        assert ready[0].wait(timeout=5)
        assert ready[1].wait(timeout=5)
        start.set()
        assert calling[0].wait(timeout=5)
        assert calling[1].wait(timeout=5)
        assert plan_entered.wait(timeout=5)

        # The first request holds SQLite's writer transaction while the duplicate
        # waits. Neither request may complete before the ambiguous effect commits.
        with pytest.raises(FutureTimeoutError):
            first.result(timeout=0.05)
        with pytest.raises(FutureTimeoutError):
            second.result(timeout=0.05)
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
    accepted = returned[0]
    assert accepted.status == ScheduleStatus.ACCEPTED
    assert accepted.event_id == event.event_id
    assert accepted.provider_event_id == f"fake-{event.event_id}"
    assert plan_calls == [(OPERATION_ID, 1)]

    verification = SQLiteDatabase(str(path))
    attempts = verification.connection.execute(
        "SELECT attempt_no, outcome FROM sandbox_calendar_attempts "
        "WHERE operation_id = ? ORDER BY attempt_no",
        (OPERATION_ID,),
    ).fetchall()
    effect = verification.connection.execute(
        "SELECT action, replays, event_json FROM sandbox_calendar_effects "
        "WHERE operation_id = ?",
        (OPERATION_ID,),
    ).fetchone()
    assert attempts == [(1, SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT.value)]
    assert effect is not None
    assert effect[0] == "accept"
    assert effect[1] == 1
    evidence_provider = SQLiteSandboxCalendarProvider(
        verification,
        lambda *_: pytest.fail("Evidence verification must not call the outcome plan."),
    )
    evidence = evidence_provider.verify_calendar_action(OPERATION_ID)
    assert evidence.state == CalendarEvidenceState.PRESENT
    assert evidence.event == accepted
    verification.close()

    restarted_database = SQLiteDatabase(str(path))
    restarted_provider = SQLiteSandboxCalendarProvider(
        restarted_database,
        lambda *_: pytest.fail("A durable replay must not invoke the outcome plan."),
    )
    replayed = restarted_provider.accept(OPERATION_ID, event)
    assert replayed == accepted
    assert restarted_database.connection.execute(
        "SELECT attempt_no, outcome FROM sandbox_calendar_attempts "
        "WHERE operation_id = ? ORDER BY attempt_no",
        (OPERATION_ID,),
    ).fetchall() == attempts
    assert restarted_database.connection.execute(
        "SELECT replays FROM sandbox_calendar_effects WHERE operation_id = ?",
        (OPERATION_ID,),
    ).fetchone() == (2,)
    restarted_database.close()


def test_candidate_ci_enforces_both_coverage_thresholds_in_order():
    """Keep the independent coverage gate after the full pytest coverage run."""
    workflow = (
        Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")
    assert "python -m pytest" in workflow
    assert "--cov-fail-under=90" in workflow
    assert "Enforce independent coverage threshold" in workflow
    assert "python -m coverage report --fail-under=90" in workflow
    assert workflow.index("Run test suite") < workflow.index(
        "Enforce independent coverage threshold"
    )
