from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json

from career_agent_ai.application.career import CareerLoopRequest
from career_agent_ai.application.career.autonomous_loop_models import CareerLoopPhase
from career_agent_ai.application.external_actions import ExternalActionStatus
from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.runtime import composition as runtime_composition
from career_agent_ai.application.runtime.composition import (
    build_candidate_sandbox_app_from_env,
)
from career_agent_ai.application.scheduling.models import (
    ScheduleEvent,
    ScheduleEventType,
    ScheduleStatus,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_sandbox_calendar_provider import (
    SandboxCalendarOutcome,
)
from career_agent_ai.application.storage.sqlite_scheduling_repository import (
    SQLiteSchedulingRepository,
)


NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def test_candidate_calendar_no_effect_retries_same_approved_action_after_restart(
    tmp_path, monkeypatch
):
    """Retry only a durable pre-effect calendar failure after reopening the candidate runtime."""
    database_path = str(tmp_path / "candidate-calendar-no-effect.sqlite")
    environ = {
        "CAREER_AGENT_DB_PATH": database_path,
        "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
        "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
    }
    jobs = (
        Job.create(
            job_id="calendar-no-effect-job",
            title="Logistics Coordinator",
            company=Company("calendar-no-effect-company", "Sandbox Logistics"),
            location=Location("Germany", "Karlsruhe"),
            description="logistics coordination",
            created_at=NOW,
        ),
    )
    requested_run_id = "calendar-no-effect-recovery"
    event_id = "calendar-no-effect-interview"
    calendar_attempts = []

    original_init = runtime_composition.SQLiteSandboxCalendarProvider.__init__

    def planned_calendar_init(self, database, plan=None):
        def outcome_plan(operation_id, attempt_no):
            calendar_attempts.append((operation_id, attempt_no))
            if attempt_no == 1:
                return SandboxCalendarOutcome.NO_EFFECT
            return SandboxCalendarOutcome.SUCCESS

        original_init(self, database, plan or outcome_plan)

    monkeypatch.setattr(
        runtime_composition.SQLiteSandboxCalendarProvider,
        "__init__",
        planned_calendar_init,
    )

    resolve_bearer = lambda token: "candidate-1" if token == "owner" else None
    runtimes = []

    def build_runtime():
        runtime = build_candidate_sandbox_app_from_env(
            resolve_bearer=resolve_bearer,
            jobs=jobs,
            environ=environ,
        )
        runtimes.append(runtime)
        return runtime

    def call(runtime, path, *, method="GET", body=None):
        encoded = b"" if body is None else json.dumps(body).encode("utf-8")
        statuses = []
        response = runtime(
            {
                "REQUEST_METHOD": method,
                "PATH_INFO": path,
                "HTTP_AUTHORIZATION": "Bearer owner",
                "CONTENT_TYPE": "application/json",
                "CONTENT_LENGTH": str(len(encoded)),
                "wsgi.input": BytesIO(encoded),
            },
            lambda status, headers: statuses.append(status),
        )
        return statuses[0], json.loads(b"".join(response))

    def approve(runtime, approval_path, run_id):
        status, prompt = call(runtime, approval_path)
        assert status == "200 OK"
        assert prompt["run_id"] == run_id
        status, result = call(
            runtime,
            approval_path,
            method="POST",
            body={
                "schema_version": 1,
                "run_id": run_id,
                "state_version": prompt["state_version"],
                "action_fingerprint": prompt["action_fingerprint"],
                "decision": "approve",
            },
        )
        assert status == "200 OK"
        return result

    try:
        first = build_runtime()
        started = first.start(
            CareerLoopRequest(
                user_id="candidate-1",
                keyword="logistics",
                candidate_profile="Experienced logistics professional.",
                location="Karlsruhe",
                sender="candidate@example.test",
                recipient="recruiter@example.test",
                schedule_event_id=event_id,
            ),
            run_id=requested_run_id,
        )
        assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL
        run_id = started.run_id
        assert isinstance(run_id, str) and run_id

        database = SQLiteDatabase(database_path)
        try:
            SQLiteSchedulingRepository(database).create(
                ScheduleEvent(
                    event_id=event_id,
                    candidate_id="candidate-1",
                    employer_name="Sandbox Logistics",
                    event_type=ScheduleEventType.INTERVIEW,
                    location="Karlsruhe",
                    start_at=NOW + timedelta(days=2),
                    end_at=NOW + timedelta(days=2, hours=1),
                    timezone_name="Europe/Berlin",
                    application_id=f"{run_id}:application",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        finally:
            database.close()

        approval_path = f"/v1/candidate/runs/{run_id}/approval"
        status_path = f"/v1/candidate/runs/{run_id}/status"

        application = approve(first, approval_path, run_id)
        assert application["phase"] == CareerLoopPhase.MESSAGE_APPROVAL.value
        message = approve(first, approval_path, run_id)
        assert message["phase"] == CareerLoopPhase.INTERVIEW_APPROVAL.value

        # The provider durably records NO_EFFECT before returning a pre-effect failure.
        interview = approve(first, approval_path, run_id)
        assert interview["phase"] == CareerLoopPhase.INTERVIEW_ACCEPT.value
        first.close()

        database = SQLiteDatabase(database_path)
        try:
            calendar_operation = database.connection.execute(
                "SELECT operation_id, status FROM external_action_operations "
                "WHERE action_type = ?",
                ("calendar.accept",),
            ).fetchone()
            calendar_outcomes = database.connection.execute(
                "SELECT operation_id, attempt_no, outcome "
                "FROM sandbox_calendar_attempts ORDER BY attempt_no"
            ).fetchall()
            calendar_effects = database.connection.execute(
                "SELECT COUNT(*), MIN(replays), MAX(replays) "
                "FROM sandbox_calendar_effects"
            ).fetchone()
            event_before_retry = SQLiteSchedulingRepository(database).get(event_id)
            submission_before_retry = database.connection.execute(
                "SELECT COUNT(*), MIN(replays), MAX(replays) "
                "FROM sandbox_submission_ledger"
            ).fetchone()
            delivery_before_retry = database.connection.execute(
                "SELECT COUNT(*), MIN(replays), MAX(replays) "
                "FROM sandbox_communication_deliveries"
            ).fetchone()
        finally:
            database.close()

        assert calendar_operation is not None
        operation_id, operation_status = calendar_operation
        assert operation_status == ExternalActionStatus.FAILED.value
        assert calendar_outcomes == [
            (operation_id, 1, SandboxCalendarOutcome.NO_EFFECT.value)
        ]
        assert calendar_effects == (0, None, None)
        assert event_before_retry is not None
        assert event_before_retry.status == ScheduleStatus.PROPOSED
        assert event_before_retry.provider_event_id is None
        assert submission_before_retry == (1, 0, 0)
        assert delivery_before_retry == (1, 0, 0)

        second = build_runtime()
        status, snapshot = call(second, status_path)
        assert status == "200 OK"
        assert snapshot["phase"] == CareerLoopPhase.INTERVIEW_ACCEPT.value
        assert snapshot["waiting_for_human"] is False

        status, continued = call(
            second,
            f"/v1/candidate/runs/{run_id}/continue",
            method="POST",
        )
        assert status == "200 OK"
        assert continued == {
            "run_id": run_id,
            "phase": CareerLoopPhase.COMPLETE.value,
        }

        status, final_snapshot = call(second, status_path)
        assert status == "200 OK"
        assert final_snapshot["phase"] == CareerLoopPhase.COMPLETE.value
        assert final_snapshot["terminal"] is True
        second.close()
    finally:
        for runtime in tuple(runtimes):
            runtime.close()

    assert [attempt[1] for attempt in calendar_attempts] == [1, 2]
    assert calendar_attempts[0][0] == calendar_attempts[1][0] == operation_id

    database = SQLiteDatabase(database_path)
    try:
        operations = database.connection.execute(
            "SELECT action_type, status FROM external_action_operations "
            "ORDER BY action_type"
        ).fetchall()
        calendar_outcomes = database.connection.execute(
            "SELECT operation_id, attempt_no, outcome "
            "FROM sandbox_calendar_attempts ORDER BY attempt_no"
        ).fetchall()
        calendar_effects = database.connection.execute(
            "SELECT COUNT(*), MIN(replays), MAX(replays) "
            "FROM sandbox_calendar_effects"
        ).fetchone()
        submission_ledger = database.connection.execute(
            "SELECT COUNT(*), MIN(replays), MAX(replays) "
            "FROM sandbox_submission_ledger"
        ).fetchone()
        communication_deliveries = database.connection.execute(
            "SELECT COUNT(*), MIN(replays), MAX(replays) "
            "FROM sandbox_communication_deliveries"
        ).fetchone()
        outbound_messages = database.connection.execute(
            "SELECT COUNT(*) FROM sandbox_communication_messages "
            "WHERE direction = 'outbound'"
        ).fetchone()[0]
        event_after_retry = SQLiteSchedulingRepository(database).get(event_id)
    finally:
        database.close()

    assert operations == [
        ("application.submit", ExternalActionStatus.SUCCEEDED.value),
        ("calendar.accept", ExternalActionStatus.SUCCEEDED.value),
        ("communication.send", ExternalActionStatus.SUCCEEDED.value),
    ]
    assert calendar_outcomes == [
        (operation_id, 1, SandboxCalendarOutcome.NO_EFFECT.value),
        (operation_id, 2, SandboxCalendarOutcome.SUCCESS.value),
    ]
    assert calendar_effects == (1, 0, 0)
    assert submission_ledger == (1, 0, 0)
    assert communication_deliveries == (1, 0, 0)
    assert outbound_messages == 1
    assert event_after_retry is not None
    assert event_after_retry.status == ScheduleStatus.ACCEPTED
    assert event_after_retry.provider_event_id
