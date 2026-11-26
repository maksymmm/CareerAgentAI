from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path

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
OWNER = "candidate-terminal-restart"
TOKEN = "candidate-terminal-restart-token"
ENV = {
    "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
    "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
}


def _call(runtime, path: str, *, method: str = "GET", body: dict | None = None):
    encoded = b"" if body is None else json.dumps(body).encode("utf-8")
    statuses = []
    response = runtime(
        {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "HTTP_AUTHORIZATION": f"Bearer {TOKEN}",
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": str(len(encoded)),
            "wsgi.input": BytesIO(encoded),
        },
        lambda status, headers: statuses.append(status),
    )
    assert len(statuses) == 1
    return statuses[0], json.loads(b"".join(response))


def _ledger_counts(database_path: str) -> dict[str, int]:
    tables = (
        "sandbox_submission_attempts",
        "sandbox_submission_ledger",
        "sandbox_communication_attempts",
        "sandbox_communication_deliveries",
        "sandbox_calendar_attempts",
        "sandbox_calendar_effects",
        "external_action_operations",
    )
    database = SQLiteDatabase.open_read_only(database_path)
    try:
        return {
            table: database.connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            for table in tables
        }
    finally:
        database.close()


@pytest.mark.parametrize(
    ("decline_at", "expected_submission_effects", "expected_message_effects"),
    [
        ("application", 0, 0),
        ("message", 1, 0),
        ("interview", 1, 1),
    ],
)
def test_candidate_decline_remains_terminal_after_runtime_restart(
    tmp_path,
    decline_at: str,
    expected_submission_effects: int,
    expected_message_effects: int,
):
    """A declined run stays terminal after restart and cannot replay downstream work."""
    database_path = str(tmp_path / f"candidate-terminal-{decline_at}.sqlite")
    run_id = f"candidate-terminal-{decline_at}"
    event_id = f"{run_id}-interview"
    job = Job.create(
        job_id=f"{run_id}-job",
        title="Logistics Coordinator",
        company=Company(f"{run_id}-company", "Sandbox Logistics"),
        location=Location("Germany", "Karlsruhe"),
        description="Warehouse logistics coordination",
        created_at=NOW,
    )
    submission_attempts: list[tuple[str, int]] = []

    def submission_plan(operation_id: str, attempt_no: int) -> SandboxOutcome:
        submission_attempts.append((operation_id, attempt_no))
        return SandboxOutcome.SUCCESS

    def build_runtime():
        return build_candidate_sandbox_app_from_env(
            resolve_bearer=lambda token: OWNER if token == TOKEN else None,
            jobs=(job,),
            environ={"CAREER_AGENT_DB_PATH": database_path, **ENV},
            sandbox_outcome_plan=submission_plan,
        )

    runtime = build_runtime()
    started = runtime.start(
        CareerLoopRequest(
            user_id=OWNER,
            keyword="logistics",
            candidate_profile="Experienced logistics professional.",
            location="Karlsruhe",
            sender="candidate@example.test",
            recipient="recruiter@example.test",
            message_subject="Application follow-up",
            message_body="Thank you for considering my application.",
            schedule_event_id=event_id,
        ),
        run_id=run_id,
    )
    assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL

    database = SQLiteDatabase(database_path)
    try:
        SQLiteSchedulingRepository(database).create(
            ScheduleEvent(
                event_id=event_id,
                candidate_id=OWNER,
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
    continue_path = f"/v1/candidate/runs/{run_id}/continue"

    def decide(app, action_kind: str, decision: str) -> dict:
        status, prompt = _call(app, approval_path)
        assert status == "200 OK"
        assert prompt["run_id"] == run_id
        assert prompt["action_kind"] == action_kind
        status, result = _call(
            app,
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
            application = decide(runtime, "approve_application", "approve")
            assert application["phase"] == CareerLoopPhase.MESSAGE_APPROVAL.value

        if decline_at == "interview":
            message = decide(runtime, "approve_message", "approve")
            assert message["phase"] == CareerLoopPhase.INTERVIEW_APPROVAL.value

        declined = decide(runtime, expected_action, "decline")
        assert declined == {"run_id": run_id, "phase": CareerLoopPhase.FAILED.value}

        status, before_restart = _call(runtime, status_path)
        assert status == "200 OK"
        assert before_restart["phase"] == CareerLoopPhase.FAILED.value
        assert before_restart["waiting_for_human"] is False
        assert before_restart["terminal"] is True
    finally:
        runtime.close()

    restarted = build_runtime()
    try:
        status, restored = _call(restarted, status_path)
        assert status == "200 OK"
        assert restored == before_restart
        assert restored["terminal"] is True

        before_continue_counts = _ledger_counts(database_path)
        status, continued = _call(restarted, continue_path, method="POST")
        assert status == "200 OK"
        assert continued == {
            "run_id": run_id,
            "phase": CareerLoopPhase.FAILED.value,
        }

        status, after_continue = _call(restarted, status_path)
        assert status == "200 OK"
        assert after_continue == restored
        assert _ledger_counts(database_path) == before_continue_counts
        assert len(submission_attempts) == expected_submission_effects
        assert before_continue_counts["sandbox_submission_attempts"] == expected_submission_effects
        assert before_continue_counts["sandbox_submission_ledger"] == expected_submission_effects
        assert before_continue_counts["sandbox_communication_attempts"] == expected_message_effects
        assert before_continue_counts["sandbox_communication_deliveries"] == expected_message_effects
        assert before_continue_counts["sandbox_calendar_attempts"] == 0
        assert before_continue_counts["sandbox_calendar_effects"] == 0
    finally:
        restarted.close()


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
