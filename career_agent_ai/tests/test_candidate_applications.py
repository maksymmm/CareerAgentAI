"""Candidate tracker snapshots remain bounded, isolated and read-only."""

from datetime import datetime, timezone
import sqlite3

import pytest

from career_agent_ai.application.jobs.job_application import JobApplication
from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_job_application_repository import SQLiteJobApplicationRepository
from career_agent_ai.application.storage.sqlite_candidate_applications import SQLiteCandidateApplications


def test_owned_pages_survive_restart_without_loading_private_children(tmp_path):
    path = tmp_path / "applications.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteJobApplicationRepository(database)
    now = datetime.now(timezone.utc)
    for identifier, owner in [("a", "owner"), ("b", "foreign"), ("c", "owner"), ("d", "owner")]:
        repository.add(JobApplication(application_id=identifier, user_id=owner,
            job_id=identifier, company_id="employer", job_title="Engineer",
            status=JobApplicationStatus.SAVED, created_at=now, updated_at=now))
    database.connection.execute("UPDATE job_applications SET job_skills_json = 'not-json'")
    database.connection.execute("UPDATE job_applications SET serialization_version = 999 WHERE user_id = 'foreign'")
    database.connection.commit()
    database.close()
    baseline = path.read_bytes()
    first = SQLiteCandidateApplications(str(path)).list_page(user_id="owner", limit=2)
    assert [item["application_id"] for item in first["items"]] == ["a", "c"]
    assert first["next_after"] == "c"
    assert set(first["items"][0]) == {"application_id", "job_id", "company_id", "job_title", "status", "state_version", "created_at", "updated_at"}
    second = SQLiteCandidateApplications(str(path)).list_page(user_id="owner", limit=2, after="c")
    assert [item["application_id"] for item in second["items"]] == ["d"]
    assert second["next_after"] is None
    assert SQLiteCandidateApplications(str(path)).list_page(user_id="missing")["items"] == []
    assert path.read_bytes() == baseline
    from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
    runtime = RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: None)
    assert runtime.list_applications(user_id="owner", limit=2) == first
    runtime.close()
    with pytest.raises(RuntimeError, match="closed"):
        runtime.list_applications(user_id="owner")
    with pytest.raises(ValueError, match="snapshot is malformed"):
        SQLiteCandidateApplications(str(path)).list_page(user_id="foreign")


@pytest.mark.parametrize("kwargs", [{"limit": True}, {"limit": 0}, {"limit": 101}, {"limit": 1.5},
    {"after": ""}, {"after": " padded"}, {"after": "a" * 201}, {"user_id": ""}])
def test_validation_precedes_missing_storage(tmp_path, kwargs):
    path = tmp_path / "missing.sqlite"
    with pytest.raises(ValueError):
        SQLiteCandidateApplications(str(path)).list_page(**({"user_id": "owner"} | kwargs))
    assert not path.exists()


@pytest.mark.parametrize("states", [[], ["applied"], ["applied", "interview"],
    ["applied", "interview", "offer"], ["applied", "rejected"], ["withdrawn"]])
def test_progressed_snapshots_after_restart_do_not_require_private_timeline(tmp_path, states):
    path = tmp_path / "lifecycle.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteJobApplicationRepository(database)
    application = JobApplication(application_id="a", user_id="owner", job_id="job",
                                 status=JobApplicationStatus.SAVED)
    repository.add(application)
    for state in states:
        previous = application.version
        application = application.transition(JobApplicationStatus(state), note="private")
        repository.update(application, expected_version=previous)
    database.connection.execute("UPDATE application_timeline SET metadata_json = 'not-json'")
    database.connection.commit()
    database.close()
    baseline = path.read_bytes()
    item = SQLiteCandidateApplications(str(path)).list_page(user_id="owner")["items"][0]
    assert item["status"] == (states[-1] if states else "saved")
    assert item["state_version"] == len(states) + 1
    assert item["updated_at"] == application.updated_at.isoformat()
    assert "private" not in str(item)
    assert path.read_bytes() == baseline


def test_missing_storage_is_not_created(tmp_path):
    path = tmp_path / "missing.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        SQLiteCandidateApplications(str(path)).list_page(user_id="owner")
    assert not path.exists()


@pytest.mark.parametrize("column,value", [("version", "bad"), ("created_at", "bad"),
    ("updated_at", "2000-01-01T00:00:00"), ("job_title", " padded"), ("serialization_version", 2)])
def test_owned_corrupt_snapshots_fail_closed(tmp_path, column, value):
    path = tmp_path / "corrupt.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteJobApplicationRepository(database)
    repository.add(JobApplication(application_id="a", user_id="owner", job_id="job",
                                  status=JobApplicationStatus.SAVED))
    database.connection.execute(f"UPDATE job_applications SET {column} = ?", (value,))
    database.connection.commit()
    database.close()
    with pytest.raises(ValueError, match="snapshot is malformed"):
        SQLiteCandidateApplications(str(path)).list_page(user_id="owner")
