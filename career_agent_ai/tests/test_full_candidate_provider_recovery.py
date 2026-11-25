from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone

from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopPhase,
    CareerLoopRequest,
)
from career_agent_ai.application.external_actions import ExternalActionStatus
from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.location import Location
import career_agent_ai.application.runtime.composition as runtime_composition
from career_agent_ai.application.runtime.composition import build_candidate_sandbox_app_from_env
from career_agent_ai.application.scheduling.models import (
    ScheduleEvent,
    ScheduleEventType,
    ScheduleStatus,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_scheduling_repository import (
    SQLiteSchedulingRepository,
)
from career_agent_ai.application.storage.sqlite_sandbox_calendar_provider import (
    SandboxCalendarOutcome,
)
from career_agent_ai.application.storage.sqlite_sandbox_communication_provider import (
    SandboxCommunicationOutcome,
)
from career_agent_ai.application.storage.sqlite_sandbox_submission_provider import (
    SandboxOutcome,
)


NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def test_complete_candidate_workflow_reconciles_all_sandbox_effects_across_restarts(
    tmp_path, monkeypatch
):
    """Prove all three durable sandbox providers recover one candidate run without replay."""
    database_path = str(tmp_path / "full-provider-recovery.sqlite")
    environ = {
        "CAREER_AGENT_DB_PATH": database_path,
        "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
        "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
    }
    jobs = (
        Job.create(
            job_id="full-recovery-job",
            title="Logistics Coordinator",
            company=Company("full-recovery-company", "Sandbox Logistics"),
            location=Location("Germany", "Karlsruhe"),
            description="logistics coordination",
            created_at=NOW,
        ),
    )
    run_id = "full-provider-recovery"
    event_id = "full-provider-recovery-event"
    application_attempts = []
    communication_attempts = []
    calendar_attempts = []

    def application_plan(operation_id, attempt_no):
        application_attempts.append((operation_id, attempt_no))
        return SandboxOutcome.TIMEOUT_AFTER_EFFECT

    original_communication_init = (
        runtime_composition.SQLiteSandboxCommunicationProvider.__init__
    )

    def planned_communication_init(self, database, plan=None):
        def outcome_plan(operation_id, attempt_no):
            communication_attempts.append((operation_id, attempt_no))
            return SandboxCommunicationOutcome.TIMEOUT_AFTER_EFFECT

        original_communication_init(self, database, plan or outcome_plan)

    monkeypatch.setattr(
        runtime_composition.SQLiteSandboxCommunicationProvider,
        "__init__",
        planned_communication_init,
    )

    original_calendar_init = runtime_composition.SQLiteSandboxCalendarProvider.__init__

    def planned_calendar_init(self, database, plan=None):
        def outcome_plan(operation_id, attempt_no):
            calendar_attempts.append((operation_id, attempt_no))
            return SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT

        original_calendar_init(self, database, plan or outcome_plan)

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
            sandbox_outcome_plan=application_plan,
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
                "wsgi.input": io.BytesIO(encoded),
            },
            lambda status, headers: statuses.append(status),
        )
        return statuses[0], json.loads(b"".join(response))

    def approve(runtime, approval_path):
        status, prompt = call(runtime, approval_path)
        assert status == "200 OK"
        assert prompt["run_id"] == run_id
        result = call(
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
        assert result[0] == "200 OK"
        return result[1]

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
            run_id=run_id,
        )
        assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL

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

        # Application provider commits one effect, then simulates a lost response.
        assert approve(first, approval_path)["phase"] == (
            CareerLoopPhase.APPLICATION_RECONCILIATION.value
        )
        first.close()

        database = SQLiteDatabase(database_path)
        try:
            submission_row = database.connection.execute(
                "SELECT provider_submission_id FROM sandbox_submission_ledger"
            ).fetchone()
            assert submission_row is not None
            provider_submission_id = submission_row[0]
        finally:
            database.close()

        second = build_runtime()
        status, snapshot = call(second, status_path)
        assert status == "200 OK"
        assert snapshot["phase"] == CareerLoopPhase.APPLICATION_RECONCILIATION.value
        application_result = call(
            second,
            f"/v1/candidate/runs/{run_id}/application-reconciliation",
            method="POST",
            body={
                "schema_version": 1,
                "state_version": snapshot["state_version"],
                "submitted": True,
                "provider_submission_id": provider_submission_id,
            },
        )
        assert application_result == (
            "200 OK",
            {"run_id": run_id, "phase": CareerLoopPhase.MESSAGE_APPROVAL.value},
        )

        # Communication provider commits one outbound message, then times out.
        assert approve(second, approval_path)["phase"] == (
            CareerLoopPhase.MESSAGE_RECONCILIATION.value
        )
        second.close()

        third = build_runtime()
        status, snapshot = call(third, status_path)
        assert status == "200 OK"
        assert snapshot["phase"] == CareerLoopPhase.MESSAGE_RECONCILIATION.value
        message_result = call(
            third,
            f"/v1/candidate/runs/{run_id}/message-reconciliation",
            method="POST",
            body={
                "schema_version": 1,
                "state_version": snapshot["state_version"],
                "delivered": True,
            },
        )
        assert message_result == (
            "200 OK",
            {"run_id": run_id, "phase": CareerLoopPhase.INTERVIEW_APPROVAL.value},
        )

        # Calendar provider commits one accepted event, then times out.
        assert approve(third, approval_path)["phase"] == (
            CareerLoopPhase.INTERVIEW_RECONCILIATION.value
        )
        third.close()

        database = SQLiteDatabase(database_path)
        try:
            effect_row = database.connection.execute(
                "SELECT event_json FROM sandbox_calendar_effects"
            ).fetchone()
            assert effect_row is not None
            provider_event_id = json.loads(effect_row[0])["provider_event_id"]
            assert provider_event_id
        finally:
            database.close()

        fourth = build_runtime()
        status, snapshot = call(fourth, status_path)
        assert status == "200 OK"
        assert snapshot["phase"] == CareerLoopPhase.INTERVIEW_RECONCILIATION.value
        interview_result = call(
            fourth,
            f"/v1/candidate/runs/{run_id}/interview-reconciliation",
            method="POST",
            body={
                "schema_version": 1,
                "state_version": snapshot["state_version"],
                "completed": True,
                "provider_event_id": provider_event_id,
            },
        )
        assert interview_result == (
            "200 OK",
            {"run_id": run_id, "phase": CareerLoopPhase.COMPLETE.value},
        )
        status, final_snapshot = call(fourth, status_path)
        assert status == "200 OK"
        assert final_snapshot["terminal"] is True
        fourth.close()

        assert len(application_attempts) == 1
        assert len(communication_attempts) == 1
        assert len(calendar_attempts) == 1
        assert [attempt[1] for attempt in application_attempts] == [1]
        assert [attempt[1] for attempt in communication_attempts] == [1]
        assert [attempt[1] for attempt in calendar_attempts] == [1]

        database = SQLiteDatabase(database_path)
        try:
            operations = database.connection.execute(
                "SELECT action_type, status FROM external_action_operations ORDER BY action_type"
            ).fetchall()
            submission = database.connection.execute(
                "SELECT COUNT(*), MIN(replays), MAX(replays) FROM sandbox_submission_ledger"
            ).fetchone()
            submission_outcomes = database.connection.execute(
                "SELECT outcome FROM sandbox_submission_attempts ORDER BY attempt_no"
            ).fetchall()
            deliveries = database.connection.execute(
                "SELECT COUNT(*), MIN(replays), MAX(replays) "
                "FROM sandbox_communication_deliveries"
            ).fetchone()
            communication_outcomes = database.connection.execute(
                "SELECT outcome FROM sandbox_communication_attempts ORDER BY attempt_no"
            ).fetchall()
            outbound_messages = database.connection.execute(
                "SELECT COUNT(*) FROM sandbox_communication_messages WHERE direction = 'outbound'"
            ).fetchone()[0]
            calendar_effects = database.connection.execute(
                "SELECT COUNT(*), MIN(replays), MAX(replays) FROM sandbox_calendar_effects"
            ).fetchone()
            calendar_outcomes = database.connection.execute(
                "SELECT outcome FROM sandbox_calendar_attempts ORDER BY attempt_no"
            ).fetchall()
            persisted_event = SQLiteSchedulingRepository(database).get(event_id)
        finally:
            database.close()

        assert operations == [
            ("application.submit", ExternalActionStatus.SUCCEEDED.value),
            ("calendar.accept", ExternalActionStatus.SUCCEEDED.value),
            ("communication.send", ExternalActionStatus.SUCCEEDED.value),
        ]
        assert submission == (1, 0, 0)
        assert submission_outcomes == [(SandboxOutcome.TIMEOUT_AFTER_EFFECT.value,)]
        assert deliveries == (1, 0, 0)
        assert communication_outcomes == [
            (SandboxCommunicationOutcome.TIMEOUT_AFTER_EFFECT.value,)
        ]
        assert outbound_messages == 1
        assert calendar_effects == (1, 0, 0)
        assert calendar_outcomes == [(SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT.value,)]
        assert persisted_event is not None
        assert persisted_event.status == ScheduleStatus.ACCEPTED
        assert persisted_event.provider_event_id == provider_event_id
    finally:
        for runtime in runtimes:
            runtime.close()
