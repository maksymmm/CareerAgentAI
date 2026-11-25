"""Regression tests for calendar sandbox replay accounting integrity."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from career_agent_ai.application.scheduling import (
    CalendarEvidenceState,
    PreCalendarActionError,
    ScheduleEvent,
    ScheduleEventType,
    ScheduleStatus,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_sandbox_calendar_provider import (
    SandboxCalendarOutcome,
    SQLiteSandboxCalendarProvider,
)


OPERATION_ID = "accept:replay-accounting-integrity"
MAX_SQLITE_INTEGER = (1 << 63) - 1


def _event() -> ScheduleEvent:
    start = datetime(2026, 11, 20, 17, 0, tzinfo=timezone.utc)
    return ScheduleEvent(
        event_id="interview-replay-accounting",
        candidate_id="candidate-replay-accounting",
        employer_name="Sandbox GmbH",
        event_type=ScheduleEventType.INTERVIEW,
        location="Sandbox video room",
        start_at=start,
        end_at=start + timedelta(hours=1),
        timezone_name="Europe/Berlin",
        status=ScheduleStatus.PROPOSED,
        application_id="application-replay-accounting",
        created_at=datetime(2026, 11, 19, tzinfo=timezone.utc),
        updated_at=datetime(2026, 11, 19, tzinfo=timezone.utc),
    )


def _stack(path, plan=None):
    database = SQLiteDatabase(str(path))
    provider = SQLiteSandboxCalendarProvider(database, plan)
    return database, provider


@pytest.mark.parametrize("replay_count", [1.5, -1, MAX_SQLITE_INTEGER])
def test_calendar_replay_rejects_malformed_or_exhausted_counter_without_mutation(
    tmp_path, replay_count
):
    path = tmp_path / "calendar-replay-counter.sqlite"
    plan_calls = []

    def plan(operation_id, attempt_no):
        plan_calls.append((operation_id, attempt_no))
        return SandboxCalendarOutcome.SUCCESS

    database, provider = _stack(path, plan)
    source = _event()
    delivered = provider.accept(OPERATION_ID, source)
    assert plan_calls == [(OPERATION_ID, 1)]
    database.connection.execute(
        "UPDATE sandbox_calendar_effects SET replays = ? WHERE operation_id = ?",
        (replay_count, OPERATION_ID),
    )
    database.connection.commit()
    before_effect = database.connection.execute(
        "SELECT fingerprint, action, event_json, replays "
        "FROM sandbox_calendar_effects WHERE operation_id = ?",
        (OPERATION_ID,),
    ).fetchone()
    before_attempts = database.connection.execute(
        "SELECT attempt_no, outcome FROM sandbox_calendar_attempts "
        "WHERE operation_id = ? ORDER BY attempt_no",
        (OPERATION_ID,),
    ).fetchall()
    database.close()

    restarted, resumed = _stack(
        path,
        lambda operation_id, attempt_no: pytest.fail(
            f"replay unexpectedly invoked the provider plan: {operation_id}/{attempt_no}"
        ),
    )
    with pytest.raises(PreCalendarActionError, match="replay counter"):
        resumed.accept(OPERATION_ID, source)

    after_effect = restarted.connection.execute(
        "SELECT fingerprint, action, event_json, replays "
        "FROM sandbox_calendar_effects WHERE operation_id = ?",
        (OPERATION_ID,),
    ).fetchone()
    after_attempts = restarted.connection.execute(
        "SELECT attempt_no, outcome FROM sandbox_calendar_attempts "
        "WHERE operation_id = ? ORDER BY attempt_no",
        (OPERATION_ID,),
    ).fetchall()
    assert after_effect == before_effect
    assert after_attempts == before_attempts
    assert resumed.calls == []
    assert resumed.verify_calendar_action(OPERATION_ID).state == CalendarEvidenceState.PRESENT
    restarted.close()


def test_calendar_replay_counter_can_reach_sqlite_maximum_but_cannot_overflow(tmp_path):
    database, provider = _stack(tmp_path / "calendar-replay-boundary.sqlite")
    source = _event()
    delivered = provider.accept(OPERATION_ID, source)
    database.connection.execute(
        "UPDATE sandbox_calendar_effects SET replays = ? WHERE operation_id = ?",
        (MAX_SQLITE_INTEGER - 1, OPERATION_ID),
    )
    database.connection.commit()

    assert provider.accept(OPERATION_ID, source) == delivered
    assert database.connection.execute(
        "SELECT replays FROM sandbox_calendar_effects WHERE operation_id = ?",
        (OPERATION_ID,),
    ).fetchone() == (MAX_SQLITE_INTEGER,)

    with pytest.raises(PreCalendarActionError, match="replay counter"):
        provider.accept(OPERATION_ID, source)
    assert database.connection.execute(
        "SELECT replays FROM sandbox_calendar_effects WHERE operation_id = ?",
        (OPERATION_ID,),
    ).fetchone() == (MAX_SQLITE_INTEGER,)
    assert provider.effect_count(OPERATION_ID) == 1
    assert provider.calls == [("accept", OPERATION_ID)]
    database.close()


def test_calendar_replay_compare_and_swap_miss_fails_closed_without_returning_effect(
    tmp_path,
):
    database, provider = _stack(tmp_path / "calendar-replay-cas.sqlite")
    source = _event()
    provider.accept(OPERATION_ID, source)
    database.connection.execute(
        """
        CREATE TRIGGER suppress_calendar_replay_update
        BEFORE UPDATE OF replays ON sandbox_calendar_effects
        BEGIN
            SELECT RAISE(IGNORE);
        END
        """
    )
    database.connection.commit()
    before_attempts = database.connection.execute(
        "SELECT attempt_no, outcome FROM sandbox_calendar_attempts "
        "WHERE operation_id = ? ORDER BY attempt_no",
        (OPERATION_ID,),
    ).fetchall()

    with pytest.raises(PreCalendarActionError, match="changed before replay accounting"):
        provider.accept(OPERATION_ID, source)

    assert database.connection.execute(
        "SELECT replays FROM sandbox_calendar_effects WHERE operation_id = ?",
        (OPERATION_ID,),
    ).fetchone() == (0,)
    assert database.connection.execute(
        "SELECT attempt_no, outcome FROM sandbox_calendar_attempts "
        "WHERE operation_id = ? ORDER BY attempt_no",
        (OPERATION_ID,),
    ).fetchall() == before_attempts
    assert provider.calls == [("accept", OPERATION_ID)]
    database.close()
