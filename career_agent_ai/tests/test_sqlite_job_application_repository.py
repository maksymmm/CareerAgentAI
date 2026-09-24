from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from career_agent_ai.application.jobs.in_memory_job_application_repository import (
    InMemoryJobApplicationRepository,
)
from career_agent_ai.application.jobs.job_application import (
    ApplicationTimelineEvent,
    JobApplication,
)
from career_agent_ai.application.jobs.job_application_repository import (
    ApplicationConflictError,
    ApplicationQuery,
)
from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_job_application_repository import (
    SQLiteJobApplicationRepository,
)


NOW = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)


def application(application_id: str = "app-1", *, job_id: str = "job-1") -> JobApplication:
    return JobApplication(
        application_id=application_id,
        user_id="candidate-1",
        job_id=job_id,
        company_id="company-1",
        status=JobApplicationStatus.SAVED,
        created_at=NOW,
        updated_at=NOW,
    )


def test_create_transition_and_restart_recovery(tmp_path):
    path = str(tmp_path / "applications.db")
    database = SQLiteDatabase(path)
    repository = SQLiteJobApplicationRepository(database)
    repository.add(application())
    applied = application().transition(
        JobApplicationStatus.APPLIED,
        occurred_at=NOW + timedelta(hours=1),
        operation_id="submit-application-1",
        event_id="event-applied",
        note="Approved submission completed",
    )
    repository.update(applied, expected_version=1)
    database.close()

    reopened = SQLiteDatabase(path)
    recovered = SQLiteJobApplicationRepository(reopened).get("app-1")
    assert recovered == applied
    assert recovered is not None
    assert recovered.timeline[0].note == "Approved submission completed"
    reopened.close()


@pytest.mark.parametrize("repository_factory", [InMemoryJobApplicationRepository, lambda: SQLiteJobApplicationRepository(SQLiteDatabase())])
def test_duplicate_id_and_user_job_are_prevented(repository_factory):
    repository = repository_factory()
    repository.add(application())
    with pytest.raises(ApplicationConflictError):
        repository.add(application())
    with pytest.raises(ApplicationConflictError):
        repository.add(application("another-id"))


def test_timeline_is_ordered_and_operation_is_linked():
    first = application().transition(
        JobApplicationStatus.APPLIED,
        occurred_at=NOW + timedelta(hours=1),
        operation_id="operation-submit",
        event_id="event-2",
    )
    second = first.transition(
        JobApplicationStatus.INTERVIEW,
        occurred_at=NOW + timedelta(hours=2),
        operation_id="operation-reply",
        event_id="event-1",
    )
    assert tuple(event.event_id for event in second.timeline) == ("event-2", "event-1")
    assert second.external_action_operation_ids == ("operation-submit", "operation-reply")


def test_query_filters_and_order_are_deterministic():
    repository = SQLiteJobApplicationRepository(SQLiteDatabase())
    later = JobApplication(
        application_id="b", user_id="candidate-1", job_id="job-b", company_id="company-2",
        status=JobApplicationStatus.APPLIED, created_at=NOW + timedelta(days=1), updated_at=NOW + timedelta(days=1),
        external_action_operation_ids=("operation-b",),
    )
    earlier = JobApplication(
        application_id="a", user_id="candidate-1", job_id="job-a", company_id="company-1",
        status=JobApplicationStatus.APPLIED, created_at=NOW, updated_at=NOW,
        external_action_operation_ids=("operation-a",),
    )
    repository.add(later)
    repository.add(earlier)

    assert repository.list("candidate-1") == (earlier, later)
    assert repository.find(ApplicationQuery(company_id="company-1")) == (earlier,)
    assert repository.find(ApplicationQuery(status=JobApplicationStatus.APPLIED)) == (earlier, later)
    assert repository.find(ApplicationQuery(job_id="job-b", user_id="candidate-1")) == (later,)
    assert repository.find(ApplicationQuery(external_action_operation_id="operation-a")) == (earlier,)


def test_update_conflicts_and_identity_changes_are_rejected():
    repository = SQLiteJobApplicationRepository(SQLiteDatabase())
    repository.add(application())
    changed = application().transition(JobApplicationStatus.APPLIED, occurred_at=NOW, event_id="event")
    repository.update(changed, expected_version=1)
    with pytest.raises(ApplicationConflictError):
        repository.update(changed, expected_version=1)
    with pytest.raises(ApplicationConflictError):
        repository.update(changed, expected_version=3)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (JobApplicationStatus.SAVED, JobApplicationStatus.OFFER),
        (JobApplicationStatus.REJECTED, JobApplicationStatus.APPLIED),
        (JobApplicationStatus.WITHDRAWN, JobApplicationStatus.INTERVIEW),
    ],
)
def test_invalid_lifecycle_transitions_are_rejected(source, target):
    item = JobApplication(
        application_id="app", user_id="user", job_id="job", status=source,
        created_at=NOW, updated_at=NOW,
    )
    with pytest.raises(ValueError, match="Invalid application transition"):
        item.transition(target, occurred_at=NOW)


def test_malformed_persisted_application_and_timeline_are_rejected():
    database = SQLiteDatabase()
    repository = SQLiteJobApplicationRepository(database)
    repository.add(application())
    database.connection.execute(
        "UPDATE job_applications SET created_at = 'not-a-time' WHERE application_id = 'app-1'"
    )
    database.connection.commit()
    with pytest.raises(ValueError, match="Persisted job application is malformed"):
        repository.get("app-1")

    database.connection.execute(
        "UPDATE job_applications SET created_at = ?, status = 'applied' WHERE application_id = 'app-1'",
        (NOW.isoformat(),),
    )
    database.connection.execute(
        """INSERT INTO application_timeline VALUES
           ('bad-event', 'app-1', 'saved', 'interview', ?, NULL, '', '[]', 1, 0)""",
        (NOW.isoformat(),),
    )
    database.connection.commit()
    with pytest.raises(ValueError, match="Persisted job application is malformed"):
        repository.get("app-1")


def test_validation_rejects_untrusted_values_and_non_json_metadata():
    with pytest.raises(ValueError, match="application_id"):
        JobApplication(application_id=" ", user_id="u", job_id="j", status=JobApplicationStatus.SAVED)
    with pytest.raises(ValueError, match="timezone-aware"):
        JobApplication(
            application_id="a", user_id="u", job_id="j", status=JobApplicationStatus.SAVED,
            created_at=datetime(2026, 1, 1),
        )
    item = application().transition(JobApplicationStatus.APPLIED, occurred_at=NOW, event_id="event")
    object.__setattr__(item.timeline[0], "metadata", {"bad": object()})
    repository = SQLiteJobApplicationRepository(SQLiteDatabase())
    with pytest.raises(ValueError, match="JSON-serializable"):
        repository.add(item)


def test_missing_get_clear_and_invalid_filters():
    repository = SQLiteJobApplicationRepository(SQLiteDatabase())
    assert repository.get("missing") is None
    with pytest.raises(ValueError):
        repository.get(" ")
    with pytest.raises(ValueError):
        ApplicationQuery(user_id=" ")
    repository.add(application())
    repository.clear()
    assert repository.find(ApplicationQuery()) == ()


@pytest.mark.parametrize(
    "repository_factory",
    [
        InMemoryJobApplicationRepository,
        lambda: SQLiteJobApplicationRepository(SQLiteDatabase()),
    ],
)
def test_repository_contract_normalizes_identifiers_and_rejects_empty_values(
    repository_factory,
):
    repository = repository_factory()
    repository.add(application())

    assert repository.get(" app-1 ") == application()
    assert repository.list(" candidate-1 ") == (application(),)
    with pytest.raises(ValueError):
        repository.get(" ")
    with pytest.raises(ValueError):
        repository.list(" ")


@pytest.mark.parametrize(
    "repository_factory",
    [
        InMemoryJobApplicationRepository,
        lambda: SQLiteJobApplicationRepository(SQLiteDatabase()),
    ],
)
def test_update_rejects_created_at_changes_consistently(repository_factory):
    repository = repository_factory()
    original = application()
    repository.add(original)
    transitioned = original.transition(
        JobApplicationStatus.APPLIED,
        occurred_at=NOW + timedelta(hours=1),
        event_id="event",
    )
    changed_creation = replace(
        transitioned,
        created_at=NOW - timedelta(days=1),
    )

    with pytest.raises(ApplicationConflictError, match="created_at"):
        repository.update(changed_creation, expected_version=1)
    assert repository.get("app-1") == original


def test_application_query_normalizes_all_string_filters():
    query = ApplicationQuery(
        user_id=" user ",
        job_id=" job ",
        company_id=" company ",
        external_action_operation_id=" operation ",
        status=JobApplicationStatus.APPLIED,
    )
    assert query.user_id == "user"
    assert query.job_id == "job"
    assert query.company_id == "company"
    assert query.external_action_operation_id == "operation"
    with pytest.raises(ValueError, match="status"):
        ApplicationQuery(status="applied")  # type: ignore[arg-type]


def test_aggregate_rejects_inconsistent_timeline_status_and_version():
    applied = application().transition(
        JobApplicationStatus.APPLIED,
        occurred_at=NOW + timedelta(hours=1),
        operation_id=" operation ",
        event_id="event",
    )
    assert applied.external_action_operation_ids == ("operation",)
    assert applied.timeline[0].operation_id == "operation"

    with pytest.raises(ValueError, match="Latest timeline event"):
        replace(applied, status=JobApplicationStatus.INTERVIEW)
    with pytest.raises(ValueError, match="version"):
        replace(applied, version=7)
    with pytest.raises(ValueError, match="updated_at"):
        replace(applied, updated_at=NOW + timedelta(hours=2))
    with pytest.raises(ValueError, match="linked"):
        replace(applied, external_action_operation_ids=())


def test_aggregate_rejects_invalid_timeline_transition_and_predated_event():
    with pytest.raises(ValueError, match="Invalid timeline transition"):
        ApplicationTimelineEvent(
            event_id="bad",
            from_status=JobApplicationStatus.SAVED,
            to_status=JobApplicationStatus.OFFER,
            occurred_at=NOW,
        )
    with pytest.raises(ValueError, match="predate"):
        application().transition(
            JobApplicationStatus.APPLIED,
            occurred_at=NOW - timedelta(seconds=1),
        )


def test_aggregate_rejects_invalid_timestamp_and_version_types():
    with pytest.raises(ValueError, match="datetime values"):
        replace(application(), created_at="2026-01-02")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="version"):
        replace(application(), version=True)
    with pytest.raises(ValueError, match="datetime value"):
        ApplicationTimelineEvent(
            event_id="event",
            from_status=JobApplicationStatus.SAVED,
            to_status=JobApplicationStatus.APPLIED,
            occurred_at="2026-01-02",  # type: ignore[arg-type]
        )


def test_equal_timestamp_transitions_preserve_lifecycle_insertion_order():
    repository = SQLiteJobApplicationRepository(SQLiteDatabase())
    applied = application().transition(
        JobApplicationStatus.APPLIED,
        occurred_at=NOW + timedelta(hours=1),
        event_id="z-first",
    )
    interviewing = applied.transition(
        JobApplicationStatus.INTERVIEW,
        occurred_at=NOW + timedelta(hours=1),
        event_id="a-second",
    )

    assert tuple(event.event_id for event in interviewing.timeline) == (
        "z-first",
        "a-second",
    )
    repository.add(interviewing)
    recovered = repository.get("app-1")
    assert recovered is not None
    assert tuple(event.event_id for event in recovered.timeline) == (
        "z-first",
        "a-second",
    )
    assert recovered.status == JobApplicationStatus.INTERVIEW


def test_sqlite_orders_timestamp_instants_across_timezone_offsets():
    database = SQLiteDatabase()
    repository = SQLiteJobApplicationRepository(database)
    plus_two = timezone(timedelta(hours=2))
    chronologically_first = JobApplication(
        application_id="first",
        user_id="candidate-1",
        job_id="job-first",
        status=JobApplicationStatus.SAVED,
        created_at=datetime(2026, 1, 2, 10, 0, tzinfo=plus_two),
        updated_at=datetime(2026, 1, 2, 10, 0, tzinfo=plus_two),
    )
    chronologically_second = JobApplication(
        application_id="second",
        user_id="candidate-1",
        job_id="job-second",
        status=JobApplicationStatus.SAVED,
        created_at=datetime(2026, 1, 2, 8, 30, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 2, 8, 30, tzinfo=timezone.utc),
    )
    repository.add(chronologically_second)
    repository.add(chronologically_first)

    assert tuple(item.application_id for item in repository.list("candidate-1")) == (
        "first",
        "second",
    )
    stored = database.connection.execute(
        "SELECT created_at FROM job_applications WHERE application_id = 'first'"
    ).fetchone()
    assert stored == ("2026-01-02T08:00:00+00:00",)


def test_sqlite_orders_timeline_instants_across_timezone_offsets():
    repository = SQLiteJobApplicationRepository(SQLiteDatabase())
    plus_two = timezone(timedelta(hours=2))
    original = JobApplication(
        application_id="offset-app",
        user_id="candidate",
        job_id="offset-job",
        status=JobApplicationStatus.SAVED,
        created_at=datetime(2026, 1, 2, 9, 0, tzinfo=plus_two),
        updated_at=datetime(2026, 1, 2, 9, 0, tzinfo=plus_two),
    )
    applied = original.transition(
        JobApplicationStatus.APPLIED,
        occurred_at=datetime(2026, 1, 2, 10, 0, tzinfo=plus_two),
        event_id="applied",
    )
    interviewing = applied.transition(
        JobApplicationStatus.INTERVIEW,
        occurred_at=datetime(2026, 1, 2, 8, 30, tzinfo=timezone.utc),
        event_id="interview",
    )
    repository.add(interviewing)

    recovered = repository.get("offset-app")
    assert recovered is not None
    assert tuple(event.event_id for event in recovered.timeline) == (
        "applied",
        "interview",
    )
    assert tuple(event.occurred_at.utcoffset() for event in recovered.timeline) == (
        timedelta(0),
        timedelta(0),
    )


def test_sqlite_migrates_existing_timeline_rows_to_stable_sequence_order():
    database = SQLiteDatabase()
    database.connection.executescript(
        """
        CREATE TABLE application_timeline (
            event_id TEXT PRIMARY KEY,
            application_id TEXT NOT NULL,
            from_status TEXT NOT NULL,
            to_status TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            operation_id TEXT,
            note TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            serialization_version INTEGER NOT NULL
        );
        INSERT INTO application_timeline VALUES
            ('z-first', 'app', 'saved', 'applied',
             '2026-01-02T08:00:00+00:00', NULL, '', '{}', 1),
            ('a-second', 'app', 'applied', 'interview',
             '2026-01-02T08:00:00+00:00', NULL, '', '{}', 1);
        """
    )
    database.connection.commit()

    SQLiteJobApplicationRepository(database)

    rows = database.connection.execute(
        """SELECT event_id, sequence FROM application_timeline
           ORDER BY sequence"""
    ).fetchall()
    assert rows == [("z-first", 0), ("a-second", 1)]
