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


@pytest.mark.parametrize("status", list(JobApplicationStatus))
def test_domain_supported_initial_statuses_remain_readable(tmp_path, status):
    path = tmp_path / "initial.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteJobApplicationRepository(database)
    application = JobApplication(application_id="a", user_id="owner", job_id="job", status=status)
    repository.add(application)
    assert repository.get("a") == application
    database.close()
    item = SQLiteCandidateApplications(str(path)).list_page(user_id="owner")["items"][0]
    assert (item["status"], item["state_version"]) == (status.value, 1)
    assert SQLiteCandidateApplications(str(path)).get_history("a", user_id="owner")["items"] == []


@pytest.mark.parametrize("status,version", [("saved", 2), ("applied", 3),
    ("interview", 4), ("offer", 5), ("rejected", 5), ("withdrawn", 6), ("offer", 1000000)])
def test_unreachable_status_version_pairs_fail_after_restart(tmp_path, status, version):
    path = tmp_path / "unreachable.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteJobApplicationRepository(database)
    repository.add(JobApplication(application_id="a", user_id="owner", job_id="job",
                                  status=JobApplicationStatus.SAVED))
    database.connection.execute("UPDATE job_applications SET status = ?, version = ?", (status, version))
    database.connection.commit()
    database.close()
    with pytest.raises(ValueError, match="snapshot is malformed"):
        SQLiteCandidateApplications(str(path)).list_page(user_id="owner")


def test_owner_keyset_index_is_backfilled_and_used_without_sorting(tmp_path):
    path = tmp_path / "indexed.sqlite"
    database = SQLiteDatabase(str(path))
    SQLiteJobApplicationRepository(database)
    # Simulate a database initialized by the previous schema version.
    database.connection.execute("DROP INDEX idx_applications_user_id")
    database.connection.commit()
    database.close()
    database = SQLiteDatabase(str(path))
    SQLiteJobApplicationRepository(database)
    SQLiteJobApplicationRepository(database)  # repeated initialization is safe
    columns = database.connection.execute("PRAGMA index_info(idx_applications_user_id)").fetchall()
    assert [row[2] for row in columns] == ["user_id", "application_id"]
    database.close()
    baseline = path.read_bytes()
    database = SQLiteDatabase.open_read_only(str(path))
    try:
        plan = database.connection.execute(
            "EXPLAIN QUERY PLAN SELECT application_id, job_id, company_id, job_title, "
            "status, version, created_at, updated_at, serialization_version "
            "FROM job_applications WHERE user_id = ? AND application_id > ? "
            "ORDER BY application_id LIMIT ?", ("owner", "cursor", 26),
        ).fetchall()
        details = " ".join(row[3] for row in plan)
        assert "idx_applications_user_id" in details
        assert "SEARCH" in details
        assert "TEMP B-TREE" not in details
    finally:
        database.close()
    assert path.read_bytes() == baseline


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
    with pytest.raises(ValueError, match="snapshot is malformed"):
        SQLiteCandidateApplications(str(path)).get_snapshot("a", user_id="owner")


def test_single_owned_snapshot_is_restart_safe_and_tenant_hiding(tmp_path):
    from career_agent_ai.application.runtime.composition import RuntimeCandidateApp

    path = tmp_path / "detail.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteJobApplicationRepository(database)
    application = JobApplication(application_id="😀" * 200, user_id="owner", job_id="job",
                                 status=JobApplicationStatus.SAVED)
    repository.add(application)
    application = application.transition(JobApplicationStatus.APPLIED, note="private note")
    repository.update(application, expected_version=1)
    repository.add(JobApplication(application_id="foreign", user_id="other", job_id="job",
                                  status=JobApplicationStatus.SAVED))
    database.connection.execute("UPDATE application_timeline SET metadata_json = 'private invalid JSON'")
    database.connection.execute("UPDATE job_applications SET job_skills_json = 'private invalid JSON'")
    database.connection.execute("UPDATE job_applications SET serialization_version = 999 WHERE user_id = 'other'")
    database.connection.commit()
    database.close()
    baseline = path.read_bytes()
    projection = SQLiteCandidateApplications(str(path))
    expected = {"schema_version": 1, "application": projection.list_page(user_id="owner")["items"][0]}
    for _ in range(2):
        runtime = RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: None)
        assert runtime.get_application(application.application_id, user_id="owner") == expected
        assert expected["application"]["state_version"] == 2
        errors = []
        for identifier in ("foreign", "missing", "' OR 1=1 --"):
            with pytest.raises(KeyError) as error:
                runtime.get_application(identifier, user_id="owner")
            errors.append(error.value.args)
        assert errors == [("Application not found.",)] * 3
        runtime.close()
        with pytest.raises(RuntimeError, match="closed"):
            runtime.get_application(application.application_id, user_id="owner")
    assert "private" not in str(expected)
    assert path.read_bytes() == baseline


@pytest.mark.parametrize("field,value", [("user_id", ""), ("user_id", " padded"),
    ("application_id", ""), ("application_id", " padded"), ("application_id", "a" * 201),
    ("application_id", None), ("user_id", None),
    ("application_id", "\ud800"), ("application_id", "\udfff"),
    ("user_id", "\ud800"), ("user_id", "\udfff")])
def test_single_snapshot_validates_before_opening_missing_storage(tmp_path, field, value):
    path = tmp_path / "missing.sqlite"
    arguments = {"application_id": "a", "user_id": "owner", field: value}
    with pytest.raises(ValueError):
        SQLiteCandidateApplications(str(path)).get_snapshot(**arguments)
    assert not path.exists()


def test_single_snapshot_does_not_create_missing_storage(tmp_path):
    path = tmp_path / "missing.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        SQLiteCandidateApplications(str(path)).get_snapshot("a", user_id="owner")
    assert not path.exists()


@pytest.mark.parametrize("states", [[], ["applied"], ["applied", "interview"],
    ["applied", "interview", "offer"], ["applied", "rejected"],
    ["applied", "interview", "offer", "withdrawn"]])
def test_owned_history_after_restart_omits_private_children(tmp_path, states):
    from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
    path = tmp_path / "history.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteJobApplicationRepository(database)
    application = JobApplication(application_id="😀" * 200, user_id="owner", job_id="job",
                                 status=JobApplicationStatus.SAVED)
    repository.add(application)
    for state in states:
        old_version = application.version
        application = application.transition(JobApplicationStatus(state), note="private-note")
        repository.update(application, expected_version=old_version)
    repository.add(JobApplication(application_id="foreign", user_id="other", job_id="job",
                                  status=JobApplicationStatus.SAVED))
    database.connection.execute("UPDATE application_timeline SET metadata_json = 'private invalid JSON', operation_id = 'private-operation'")
    database.connection.execute("UPDATE job_applications SET job_skills_json = 'private invalid JSON'")
    database.connection.execute("UPDATE job_applications SET serialization_version = 999 WHERE user_id = 'other'")
    database.connection.commit()
    database.close()
    baseline = path.read_bytes()
    for _ in range(2):
        runtime = RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: None)
        result = runtime.get_application_history(application.application_id, user_id="owner")
        assert result["schema_version"] == 1 and result["state_version"] == len(states) + 1
        assert result["application_id"] == application.application_id
        assert [item["to_status"] for item in result["items"]] == states
        for index, item in enumerate(result["items"]):
            assert set(item) == {"sequence", "from_status", "to_status", "occurred_at"}
            assert item["sequence"] == index
            assert item["from_status"] == (["saved"] + states)[index]
        assert "private" not in str(result)
        for identifier in ("foreign", "missing", "' OR 1=1 --"):
            with pytest.raises(KeyError, match="Application not found"):
                runtime.get_application_history(identifier, user_id="owner")
        runtime.close()
        with pytest.raises(RuntimeError, match="closed"):
            runtime.get_application_history(application.application_id, user_id="owner")
    assert path.read_bytes() == baseline


@pytest.mark.parametrize("mutation", [
    "DELETE FROM application_timeline",
    "INSERT INTO application_timeline SELECT event_id || '-extra', application_id, from_status, to_status, occurred_at, operation_id, note, metadata_json, serialization_version, sequence + 1 FROM application_timeline",
    "UPDATE application_timeline SET sequence = 2",
    "UPDATE application_timeline SET serialization_version = 9",
    "UPDATE application_timeline SET from_status = 'offer'",
    "UPDATE application_timeline SET to_status = 'withdrawn'",
    "UPDATE application_timeline SET occurred_at = 'bad'",
    "UPDATE application_timeline SET occurred_at = '2000-01-01T00:00:00+00:00'",
    "UPDATE application_timeline SET occurred_at = '2999-01-01T00:00:00+00:00'",
])
def test_owned_malformed_history_fails_closed_after_restart(tmp_path, mutation):
    path = tmp_path / "malformed-history.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteJobApplicationRepository(database)
    repository.add(JobApplication(application_id="a", user_id="owner", job_id="job",
        status=JobApplicationStatus.SAVED).transition(JobApplicationStatus.APPLIED))
    database.connection.execute(mutation)
    database.connection.commit()
    database.close()
    baseline = path.read_bytes()
    with pytest.raises(ValueError, match="history is malformed"):
        SQLiteCandidateApplications(str(path)).get_history("a", user_id="owner")
    assert path.read_bytes() == baseline


@pytest.mark.parametrize("identifier,owner", [("", "owner"), ("a", ""),
    ("\ud800", "owner"), ("a", "\udfff"), ("a" * 201, "owner"), (" padded", "owner")])
def test_history_validates_before_opening_missing_storage(tmp_path, identifier, owner):
    path = tmp_path / "missing-history.sqlite"
    with pytest.raises(ValueError):
        SQLiteCandidateApplications(str(path)).get_history(identifier, user_id=owner)
    assert not path.exists()


def test_history_never_creates_missing_storage(tmp_path):
    path = tmp_path / "missing-history.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        SQLiteCandidateApplications(str(path)).get_history("a", user_id="owner")
    assert not path.exists()
