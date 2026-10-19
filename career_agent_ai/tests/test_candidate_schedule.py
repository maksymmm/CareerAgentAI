"""Read-only candidate scheduling boundary regressions."""

from datetime import datetime, timedelta, timezone
import sqlite3
import pytest

from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
from career_agent_ai.application.scheduling.models import ScheduleEvent, ScheduleEventType
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_scheduling_repository import SQLiteSchedulingRepository


@pytest.mark.parametrize("instant,offset", [("2026-10-25T00:30:00+00:00", "+02:00"),
                                           ("2026-10-25T01:30:00+00:00", "+01:00")])
@pytest.mark.parametrize("with_end", [False, True])
def test_owned_schedule_snapshot_after_restart_is_private_and_read_only(tmp_path, instant, offset, with_end):
    path = tmp_path / "events.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteSchedulingRepository(database)
    start = datetime.fromisoformat(instant)
    for identifier, owner in [("owned", "candidate"), ("foreign", "other")]:
        repository.create(ScheduleEvent(event_id=identifier, candidate_id=owner,
            employer_name="Employer", event_type=ScheduleEventType.INTERVIEW,
            location="Karlsruhe", start_at=start, end_at=start + timedelta(hours=2) if with_end else None,
            timezone_name="Europe/Berlin", provider_event_id="private-provider-" + identifier, application_id="private-app"))
    database.connection.execute("UPDATE scheduling_events SET timezone_name='private-corrupt' WHERE candidate_id='other'")
    database.connection.execute("UPDATE scheduling_events SET provider_event_id=char(1) || event_id, application_id='private/bad'")
    database.connection.commit()
    database.close()
    baseline = path.read_bytes()
    for _ in range(2):
        with RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: None) as runtime:
            result = runtime.get_schedule_event("owned", user_id="candidate")
            event = result["event"]
            assert result["schema_version"] == 1 and event["state_version"] == 1
            assert event["local_date"] == "2026-10-25" and event["local_start_time"] == "02:30:00"
            assert event["utc_offset"] == offset and event["timezone_name"] == "Europe/Berlin"
            assert (event["end_at"] is not None) == with_end
            assert "private" not in str(result) and "candidate_id" not in event
            assert not {"provider_event_id", "application_id", "active_action_operation_id"} & event.keys()
            for identifier in ("foreign", "missing"):
                with pytest.raises(KeyError, match="Schedule event not found"):
                    runtime.get_schedule_event(identifier, user_id="candidate")
        with pytest.raises(RuntimeError, match="closed"):
            runtime.get_schedule_event("owned", user_id="candidate")
    assert path.read_bytes() == baseline


@pytest.mark.parametrize("mutation", ["timezone_name='Invalid/Zone'", "start_at='bad'",
                                    "start_epoch_us=0", "end_epoch_us=0"])
def test_owned_corrupt_schedule_fails_closed(tmp_path, mutation):
    path = tmp_path / "bad.sqlite"
    database = SQLiteDatabase(str(path))
    SQLiteSchedulingRepository(database).create(ScheduleEvent(event_id="event", candidate_id="owner",
        employer_name="Employer", event_type=ScheduleEventType.INTERVIEW, location="Location",
        start_at=datetime.now(timezone.utc), timezone_name="UTC"))
    database.connection.execute("UPDATE scheduling_events SET " + mutation)
    database.connection.commit()
    database.close()
    baseline = path.read_bytes()
    with RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: None) as runtime:
        with pytest.raises(ValueError, match="Persisted scheduling event is malformed"):
            runtime.get_schedule_event("event", user_id="owner")
    assert path.read_bytes() == baseline


@pytest.mark.parametrize("event_id,owner", [("", "owner"), ("a/b", "owner"), ("event", ""), ("event", "\ud800")])
def test_schedule_validation_precedes_missing_storage(tmp_path, event_id, owner):
    path = tmp_path / "missing.sqlite"
    with RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: None) as runtime:
        with pytest.raises(ValueError):
            runtime.get_schedule_event(event_id, user_id=owner)
    assert not path.exists()


def test_schedule_read_never_creates_storage(tmp_path):
    path = tmp_path / "missing.sqlite"
    with RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: None) as runtime:
        with pytest.raises(sqlite3.OperationalError):
            runtime.get_schedule_event("event", user_id="owner")
    assert not path.exists()
