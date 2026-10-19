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
@pytest.mark.parametrize("microseconds", [0, 123456])
def test_owned_schedule_snapshot_after_restart_is_private_and_read_only(tmp_path, instant, offset, with_end, microseconds):
    path = tmp_path / "events.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteSchedulingRepository(database)
    start = datetime.fromisoformat(instant).replace(microsecond=microseconds)
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
            fraction = ".123456" if microseconds else ""
            assert event["local_date"] == "2026-10-25" and event["local_start_time"] == "02:30:00" + fraction
            if with_end:
                assert event["local_end_time"].endswith("00" + fraction)
                assert datetime.fromisoformat(event["end_at"]).microsecond == microseconds
            assert datetime.fromisoformat(event["start_at"]).microsecond == microseconds
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


def test_schedule_pages_restart_isolation_and_read_only(tmp_path):
    path = tmp_path / "pages.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteSchedulingRepository(database)
    for index in reversed(range(28)):
        repository.create(ScheduleEvent(event_id=f"event-{index:03}", candidate_id="owner" if index < 27 else "other",
            employer_name="Employer", event_type=ScheduleEventType.INTERVIEW, location="Location",
            start_at=datetime(2026, 10, 25, 0, 30, 0, index, tzinfo=timezone.utc),
            timezone_name="Europe/Berlin"))
    database.connection.execute("UPDATE scheduling_events SET provider_event_id=char(1)||event_id, application_id='private/bad', active_action_operation_id='private-'||event_id")
    database.connection.execute("UPDATE scheduling_events SET timezone_name='private-corrupt' WHERE candidate_id='other'")
    database.connection.commit()
    database.close()
    baseline = path.read_bytes()
    with RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: None) as runtime:
        first = runtime.list_schedule_events(user_id="owner")
        assert first["schema_version"] == 1 and len(first["items"]) == 25
        assert first["next_after"] == "event-024"
        assert first["items"][0] == runtime.get_schedule_event("event-000", user_id="owner")["event"]
        assert runtime.list_schedule_events(user_id="absent") == {"schema_version": 1, "items": [], "next_after": None}
    with RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: None) as runtime:
        second = runtime.list_schedule_events(user_id="owner", after=first["next_after"])
        assert [item["event_id"] for item in first["items"] + second["items"]] == [f"event-{i:03}" for i in range(27)]
        assert second["next_after"] is None
        assert len(runtime.list_schedule_events(user_id="owner", limit=100)["items"]) == 27
        # Arbitrary, missing and foreign cursors are range boundaries, not credentials.
        assert runtime.list_schedule_events(user_id="owner", after="event-027")["items"] == []
        assert runtime.list_schedule_events(user_id="owner", after="event-025x")["items"][0]["event_id"] == "event-026"
        assert runtime.list_schedule_events(user_id="owner", limit=1)["next_after"] == "event-000"
    with pytest.raises(RuntimeError, match="closed"):
        runtime.list_schedule_events(user_id="owner")
    assert "private" not in str(first) + str(second)
    assert path.read_bytes() == baseline


@pytest.mark.parametrize("options", [{"limit": 0}, {"limit": 101}, {"limit": True},
    {"limit": 1.0}, {"limit": "1"}, {"after": ""}, {"after": " padded"},
    {"after": "padded "}, {"after": "a/b"}, {"after": "\ud800"},
    {"after": "x" * 201}, {"user_id": ""}, {"user_id": "owner/bad"}])
def test_schedule_page_validation_before_missing_storage(tmp_path, options):
    path = tmp_path / "missing.sqlite"
    with RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: None) as runtime:
        with pytest.raises(ValueError):
            runtime.list_schedule_events(**({"user_id": "owner"} | options))
    assert not path.exists()


def test_schedule_page_missing_storage_never_created(tmp_path):
    path = tmp_path / "missing.sqlite"
    with RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: None) as runtime:
        with pytest.raises(sqlite3.OperationalError):
            runtime.list_schedule_events(user_id="owner")
    assert not path.exists()


def test_schedule_page_corruption_is_bounded_to_returned_rows(tmp_path):
    path = tmp_path / "corrupt.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteSchedulingRepository(database)
    for identifier in ("a", "b"):
        repository.create(ScheduleEvent(event_id=identifier, candidate_id="owner", employer_name="Employer",
            event_type=ScheduleEventType.INTERVIEW, location="Location",
            start_at=datetime.now(timezone.utc), timezone_name="UTC"))
    database.connection.execute("UPDATE scheduling_events SET start_epoch_us=0 WHERE event_id='b'")
    database.connection.commit()
    database.close()
    baseline = path.read_bytes()
    with RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: None) as runtime:
        assert runtime.list_schedule_events(user_id="owner", limit=1)["next_after"] == "a"
        with pytest.raises(ValueError, match="Persisted scheduling event is malformed"):
            runtime.list_schedule_events(user_id="owner", after="a")
    assert path.read_bytes() == baseline


def test_schedule_page_index_backfilled_by_writable_startup_only(tmp_path):
    path = tmp_path / "legacy.sqlite"
    database = SQLiteDatabase(str(path))
    SQLiteSchedulingRepository(database)
    database.connection.execute("DROP INDEX idx_scheduling_candidate_event")
    database.connection.commit()
    database.close()
    baseline = path.read_bytes()
    with RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: None) as runtime:
        assert runtime.list_schedule_events(user_id="owner")["items"] == []
    assert path.read_bytes() == baseline
    database = SQLiteDatabase(str(path))
    SQLiteSchedulingRepository(database)
    plan = database.connection.execute("EXPLAIN QUERY PLAN SELECT event_id FROM scheduling_events WHERE candidate_id=? AND event_id>? ORDER BY event_id LIMIT ?", ("owner", "a", 26)).fetchall()
    assert "SEARCH" in str(plan) and "idx_scheduling_candidate_event" in str(plan)
    assert "TEMP B-TREE" not in str(plan)
    database.close()
