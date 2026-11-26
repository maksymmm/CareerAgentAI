from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json

import pytest

from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopPhase,
    CareerLoopRequest,
)
from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.runtime.composition import (
    build_candidate_sandbox_app_from_env,
)
from career_agent_ai.application.scheduling.models import (
    ScheduleEvent,
    ScheduleEventType,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_sandbox_submission_provider import (
    SandboxOutcome,
)
from career_agent_ai.application.storage.sqlite_scheduling_repository import (
    SQLiteSchedulingRepository,
)


NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("decline_at", "expected_submission_effects", "expected_message_effects"),
    [
        ("application", 0, 0),
        ("message", 1, 0),
        ("interview", 1, 1),
    ],
)
def test_candidate_decline_stops_downstream_sandbox_provider_effects(
    tmp_path,
    decline_at,
    expected_submission_effects,
    expected_message_effects,
):
    """A decline at any human gate must fence off that and all later provider effects."""
    database_path = str(tmp_path / f"candidate-decline-{decline_at}.sqlite")
    environ = {
        "CAREER_AGENT_DB_PATH": database_path,
        "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
        "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
    }
    job = Job.create(
        job_id=f"candidate-decline-{decline_at}-job",
        title="Logistics Coordinator",
        company=Company("candidate-decline-company", "Sandbox Logistics"),
        location=Location("Germany", "Karlsruhe"),
        description="Warehouse logistics coordination",
        created_at=NOW,
    )
    event_id = f"candidate-decline-{decline_at}-interview"
    submission_attempts = []

    def submission_plan(operation_id, attempt_no):
        submission_attempts.append((operation_id, attempt_no))
        return SandboxOutcome.SUCCESS

    runtime = build_candidate_sandbox_app_from_env(
        resolve_bearer=lambda token: "candidate-1" if token == "owner" else None,
        jobs=(job,),
        environ=environ,
        sandbox_outcome_plan=submission_plan,
    )

    started = runtime.start(
        CareerLoopRequest(
            user_id="candidate-1",
            keyword="logistics",
            candidate_profile="Experienced logistics professional.",
            location="Karlsruhe",
            sender="candidate@example.test",
            recipient="recruiter@example.test",
            message_subject="Application follow-up",
            message_body="Thank you for considering my application.",
            schedule_event_id=event_id,
        ),
        run_id=f"candidate-decline-{decline_at}",
    )
    assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL
    run_id = started.run_id
    approval_path = f"/v1/candidate/runs/{run_id}/approval"

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

    def call(path, *, method="GET", body=None):
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
        assert len(statuses) == 1
        return statuses[0], json.loads(b"".join(response))

    def decide(action_kind, decision):
        status, prompt = call(approval_path)
        assert status == "200 OK"
        assert prompt["run_id"] == run_id
        assert prompt["action_kind"] == action_kind
        status, result = call(
            approval_path,
            method="POST",
            body={
                "schema_version": 1,
                "run_id": run_id,
                "state_version": prompt["state_version"],
                "action_fingerprint": prompt["action_fingerprint"],
                "decision": decision,
            },
        )
        assert status == "200 OK"
        return result

    expected_action = {
        "application": "approve_application",
        "message": "approve_message",
        "interview": "approve_interview",
    }[decline_at]

    try:
        if decline_at != "application":
            application = decide("approve_application", "approve")
            assert application["phase"] == CareerLoopPhase.MESSAGE_APPROVAL.value

        if decline_at == "interview":
            message = decide("approve_message", "approve")
            assert message["phase"] == CareerLoopPhase.INTERVIEW_APPROVAL.value

        declined = decide(expected_action, "decline")
        assert declined == {"run_id": run_id, "phase": CareerLoopPhase.FAILED.value}

        status, snapshot = call(f"/v1/candidate/runs/{run_id}/status")
        assert status == "200 OK"
        assert set(snapshot) == {
            "schema_version", "run_id", "state_version", "phase",
            "waiting_for_human", "terminal",
        }
        assert snapshot["schema_version"] == 1
        assert snapshot["run_id"] == run_id
        assert isinstance(snapshot["state_version"], int)
        assert snapshot["state_version"] > 0
        assert snapshot["phase"] == CareerLoopPhase.FAILED.value
        assert snapshot["waiting_for_human"] is False
        assert snapshot["terminal"] is True
    finally:
        runtime.close()

    database = SQLiteDatabase(database_path)
    try:
        def count(table, where=""):
            row = database.connection.execute(
                f"SELECT COUNT(*) FROM {table} {where}"
            ).fetchone()
            return row[0]

        assert count("sandbox_submission_attempts") == expected_submission_effects
        assert count("sandbox_submission_ledger") == expected_submission_effects
        assert count("sandbox_communication_attempts") == expected_message_effects
        assert count("sandbox_communication_deliveries") == expected_message_effects
        assert count("sandbox_calendar_attempts") == 0
        assert count("sandbox_calendar_effects") == 0
        assert count(
            "external_action_operations",
            "WHERE action_type = 'calendar.accept'",
        ) == 0
        assert len(submission_attempts) == expected_submission_effects
    finally:
        database.close()
