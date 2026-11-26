from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone

import career_agent_ai.application.runtime.composition as runtime_composition
from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopPhase,
    CareerLoopRequest,
)
from career_agent_ai.application.external_actions import ExternalActionStatus
from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.runtime.composition import (
    build_candidate_sandbox_app_from_env,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_scheduling_repository import (
    SQLiteSchedulingRepository,
)
from career_agent_ai.application.storage.sqlite_sandbox_communication_provider import (
    SandboxCommunicationOutcome,
)
from career_agent_ai.application.storage.sqlite_sandbox_submission_provider import (
    SandboxOutcome,
)


NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def test_candidate_message_no_effect_retries_after_restart_without_replaying_application(
    tmp_path, monkeypatch
):
    """A confirmed no-effect message attempt can resume after restart without resubmitting."""
    database_path = str(tmp_path / "candidate-message-no-effect.sqlite")
    environ = {
        "CAREER_AGENT_DB_PATH": database_path,
        "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
        "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
    }
    jobs = (
        Job.create(
            job_id="message-retry-job",
            title="Logistics Coordinator",
            company=Company("message-retry-company", "Sandbox Logistics"),
            location=Location("Germany", "Karlsruhe"),
            description="logistics coordination",
            created_at=NOW,
        ),
    )
    requested_run_id = "candidate-message-no-effect"
    event_id = "candidate-message-no-effect-event"
    application_attempts = []
    communication_attempts = []

    def application_plan(operation_id, attempt_no):
        application_attempts.append((operation_id, attempt_no))
        return SandboxOutcome.SUCCESS

    original_communication_init = (
        runtime_composition.SQLiteSandboxCommunicationProvider.__init__
    )

    def planned_communication_init(self, database, plan=None):
        def outcome_plan(operation_id, attempt_no):
            communication_attempts.append((operation_id, attempt_no))
            if attempt_no == 1:
                return SandboxCommunicationOutcome.NO_EFFECT
            return SandboxCommunicationOutcome.SUCCESS

        original_communication_init(self, database, plan or outcome_plan)

    monkeypatch.setattr(
        runtime_composition.SQLiteSandboxCommunicationProvider,
        "__init__",
        planned_communication_init,
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

    def close_runtime(runtime):
        if runtime in runtimes:
            runtimes.remove(runtime)
            runtime.close()

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

    def approve(runtime, approval_path, run_id):
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
            run_id=requested_run_id,
        )
        assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL
        assert isinstance(started.run_id, str) and started.run_id
        run_id = started.run_id

        database = SQLiteDatabase(database_path)
        try:
            SQLiteSchedulingRepository(database).create(
                __import__(
                    "career_agent_ai.application.scheduling.models",
                    fromlist=["ScheduleEvent", "ScheduleEventType"],
                ).ScheduleEvent(
                    event_id=event_id,
                    candidate_id="candidate-1",
                    employer_name="Sandbox Logistics",
                    event_type=__import__(
                        "career_agent_ai.application.scheduling.models",
                        fromlist=["ScheduleEventType"],
                    ).ScheduleEventType.INTERVIEW,
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

        application_result = approve(first, approval_path, run_id)
        assert application_result["phase"] == CareerLoopPhase.MESSAGE_APPROVAL.value

        message_result = approve(first, approval_path, run_id)
        assert message_result["phase"] == CareerLoopPhase.MESSAGE_SEND.value
        status, snapshot = call(first, status_path)
        assert status == "200 OK"
        assert snapshot["phase"] == CareerLoopPhase.MESSAGE_SEND.value
        assert snapshot["waiting_for_human"] is False
        close_runtime(first)

        database = SQLiteDatabase(database_path)
        try:
            failed_message = database.connection.execute(
                "SELECT status FROM external_action_operations WHERE action_type = ?",
                ("communication.send",),
            ).fetchone()
            message_attempts_before = database.connection.execute(
                "SELECT operation_id, attempt_no, outcome FROM sandbox_communication_attempts "
                "ORDER BY attempt_no"
            ).fetchall()
            deliveries_before = database.connection.execute(
                "SELECT COUNT(*) FROM sandbox_communication_deliveries"
            ).fetchone()[0]
            application_ledger_before = database.connection.execute(
                "SELECT COUNT(*), MIN(replays), MAX(replays) FROM sandbox_submission_ledger"
            ).fetchone()
        finally:
            database.close()

        assert failed_message == (ExternalActionStatus.FAILED.value,)
        assert message_attempts_before == [
            (
                message_attempts_before[0][0],
                1,
                SandboxCommunicationOutcome.NO_EFFECT.value,
            )
        ]
        assert deliveries_before == 0
        assert application_ledger_before == (1, 0, 0)

        second = build_runtime()
        continue_result = call(
            second,
            f"/v1/candidate/runs/{run_id}/continue",
            method="POST",
        )
        assert continue_result[0] == "200 OK"
        assert continue_result[1] == {
            "run_id": run_id,
            "phase": CareerLoopPhase.INTERVIEW_APPROVAL.value,
        }
        status, final_snapshot = call(second, status_path)
        assert status == "200 OK"
        assert final_snapshot["phase"] == CareerLoopPhase.INTERVIEW_APPROVAL.value
        assert final_snapshot["waiting_for_human"] is True
        close_runtime(second)
    finally:
        for runtime in tuple(runtimes):
            close_runtime(runtime)

    assert len(application_attempts) == 1
    assert application_attempts[0][1] == 1
    assert len(communication_attempts) == 2
    assert communication_attempts[0][0] == communication_attempts[1][0]
    assert [attempt[1] for attempt in communication_attempts] == [1, 2]

    database = SQLiteDatabase(database_path)
    try:
        operations = database.connection.execute(
            "SELECT action_type, status FROM external_action_operations ORDER BY action_type"
        ).fetchall()
        application_ledger = database.connection.execute(
            "SELECT COUNT(*), MIN(replays), MAX(replays) FROM sandbox_submission_ledger"
        ).fetchone()
        application_outcomes = database.connection.execute(
            "SELECT outcome FROM sandbox_submission_attempts ORDER BY attempt_no"
        ).fetchall()
        deliveries = database.connection.execute(
            "SELECT COUNT(*), MIN(replays), MAX(replays) FROM sandbox_communication_deliveries"
        ).fetchone()
        communication_outcomes = database.connection.execute(
            "SELECT outcome FROM sandbox_communication_attempts ORDER BY attempt_no"
        ).fetchall()
        outbound_messages = database.connection.execute(
            "SELECT COUNT(*) FROM sandbox_communication_messages WHERE direction = 'outbound'"
        ).fetchone()[0]
    finally:
        database.close()

    assert operations == [
        ("application.submit", ExternalActionStatus.SUCCEEDED.value),
        ("communication.send", ExternalActionStatus.SUCCEEDED.value),
    ]
    assert application_ledger == (1, 0, 0)
    assert application_outcomes == [(SandboxOutcome.SUCCESS.value,)]
    assert deliveries == (1, 0, 0)
    assert communication_outcomes == [
        (SandboxCommunicationOutcome.NO_EFFECT.value,),
        (SandboxCommunicationOutcome.SUCCESS.value,),
    ]
    assert outbound_messages == 1
