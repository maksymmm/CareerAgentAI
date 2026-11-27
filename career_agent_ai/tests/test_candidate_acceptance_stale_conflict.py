from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopPhase,
    CareerLoopRequest,
)
from career_agent_ai.application.external_actions import (
    ExternalActionService,
    ExternalActionStatus,
)
from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
from career_agent_ai.application.scheduling import ScheduleStatus, SchedulingService
from career_agent_ai.application.scheduling.models import (
    ScheduleEvent,
    ScheduleEventType,
)
from career_agent_ai.application.scheduling.scheduling_repository import (
    SchedulingOperationConflict,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import (
    SQLiteExternalActionOperationRepository,
)
from career_agent_ai.application.storage.sqlite_sandbox_calendar_provider import (
    SQLiteSandboxCalendarProvider,
)
from career_agent_ai.application.storage.sqlite_sandbox_submission_provider import (
    SandboxOutcome,
)
from career_agent_ai.application.storage.sqlite_scheduling_repository import (
    SQLiteSchedulingRepository,
)


NOW = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)


def _request(runtime, path: str, *, method: str = "GET", body=None):
    """Send one authenticated JSON request through the real candidate WSGI runtime."""
    encoded = b"" if body is None else json.dumps(body).encode("utf-8")
    responses = []
    response = b"".join(
        runtime(
            {
                "REQUEST_METHOD": method,
                "PATH_INFO": path,
                "QUERY_STRING": "",
                "HTTP_AUTHORIZATION": "Bearer owner",
                "CONTENT_TYPE": "application/json",
                "CONTENT_LENGTH": str(len(encoded)),
                "wsgi.input": io.BytesIO(encoded),
            },
            lambda status, headers: responses.append((status, headers)),
        )
    )
    assert len(responses) == 1
    return responses[0][0], json.loads(response)


def _approve(runtime, run_id: str):
    """Submit one fresh prompt-bound candidate approval."""
    path = f"/v1/candidate/runs/{run_id}/approval"
    status, prompt = _request(runtime, path)
    assert status == "200 OK"
    assert prompt["run_id"] == run_id
    status, result = _request(
        runtime,
        path,
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


def test_candidate_stale_acceptance_reconciliation_returns_conflict_after_restart(tmp_path):
    """A later same-slot acceptance cannot be resolved by an older candidate attestation."""
    database_path = str(tmp_path / "candidate-stale-acceptance-conflict.sqlite")
    event_id = "candidate-stale-acceptance-interview"
    jobs = (
        Job.create(
            job_id="candidate-stale-acceptance-job",
            title="Logistics Coordinator",
            company=Company("candidate-stale-acceptance-company", "Sandbox Logistics"),
            location=Location("Germany", "Karlsruhe"),
            description="logistics coordination",
            created_at=NOW,
        ),
    )

    def application_plan(_operation_id, _attempt_no):
        return SandboxOutcome.SUCCESS

    first = RuntimeCandidateApp(
        database_path=database_path,
        jobs=jobs,
        resolve_bearer=lambda token: "candidate-1" if token == "owner" else None,
        sandbox_outcome_plan=application_plan,
    )
    second = None
    try:
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
            run_id="candidate-stale-acceptance-run",
        )
        assert started.phase is CareerLoopPhase.APPLICATION_APPROVAL
        run_id = started.run_id

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
            SQLiteExternalActionOperationRepository(database)
            database.connection.execute(
                """
                CREATE TRIGGER fail_calendar_accept_terminal_write
                BEFORE UPDATE OF status ON external_action_operations
                WHEN NEW.action_type = 'calendar.accept'
                  AND NEW.status = 'succeeded'
                BEGIN
                    SELECT RAISE(ABORT, 'simulated terminal persistence failure');
                END
                """
            )
            database.connection.commit()
        finally:
            database.close()

        assert _approve(first, run_id)["phase"] == CareerLoopPhase.MESSAGE_APPROVAL.value
        assert _approve(first, run_id)["phase"] == CareerLoopPhase.INTERVIEW_APPROVAL.value
        assert _approve(first, run_id)["phase"] == CareerLoopPhase.INTERVIEW_RECONCILIATION.value

        database = SQLiteDatabase(database_path)
        try:
            operation_rows = database.connection.execute(
                "SELECT operation_id, status FROM external_action_operations "
                "WHERE action_type = 'calendar.accept'"
            ).fetchall()
            assert len(operation_rows) == 1
            old_operation_id, old_status = operation_rows[0]
            assert old_status == ExternalActionStatus.RECONCILIATION_REQUIRED.value
            effect = database.connection.execute(
                "SELECT event_json, replays FROM sandbox_calendar_effects "
                "WHERE operation_id = ?",
                (old_operation_id,),
            ).fetchone()
            assert effect is not None and effect[1] == 0
            provider_event_id = json.loads(effect[0])["provider_event_id"]
            assert provider_event_id
            attempts = database.connection.execute(
                "SELECT attempt_no, outcome FROM sandbox_calendar_attempts "
                "WHERE operation_id = ? ORDER BY attempt_no",
                (old_operation_id,),
            ).fetchall()
            assert [(row[0], row[1]) for row in attempts] == [(1, "success")]
            assert tuple(database.connection.execute(
                "SELECT status, version, active_action_operation_id "
                "FROM scheduling_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()) == ("accepted", 2, None)
        finally:
            database.close()

        first.close()

        database = SQLiteDatabase(database_path)
        try:
            database.connection.execute(
                "DROP TRIGGER fail_calendar_accept_terminal_write"
            )
            database.connection.commit()
            events = SQLiteSchedulingRepository(database)
            provider = SQLiteSandboxCalendarProvider(database)
            operations = SQLiteExternalActionOperationRepository(database)
            service = SchedulingService(
                events,
                provider,
                ExternalActionService(
                    operations, SchedulingService.action_adapter(provider, events)
                ),
                require_provider_evidence=True,
            )
            source = events.get(event_id)
            assert source is not None
            assert source.status is ScheduleStatus.ACCEPTED
            assert source.version == 2

            moved_start = source.start_at + timedelta(days=2)
            moved = service.reschedule(
                "candidate-stale-acceptance-move-away",
                event_id,
                start_at=moved_start,
                end_at=moved_start + timedelta(hours=1),
                timezone_name=source.timezone_name,
                human_approved=True,
            )
            assert moved is not None and moved.version == 3
            returned = service.reschedule(
                "candidate-stale-acceptance-return",
                event_id,
                start_at=source.start_at,
                end_at=source.end_at,
                timezone_name=source.timezone_name,
                human_approved=True,
            )
            assert returned is not None and returned.version == 4
            reaccepted = service.accept(
                "candidate-stale-acceptance-later",
                event_id,
                human_approved=True,
            )
            assert reaccepted is not None
            assert reaccepted.status is ScheduleStatus.ACCEPTED
            assert reaccepted.version == 5
            assert reaccepted.start_at == source.start_at
            assert reaccepted.end_at == source.end_at

            with pytest.raises(
                SchedulingOperationConflict,
                match="Prepared scheduling intent is stale for the current event version",
            ):
                service.resolve_reconciliation(
                    old_operation_id,
                    completed=True,
                    provider_event_id=provider_event_id,
                )
            assert operations.get(old_operation_id).status is ExternalActionStatus.RECONCILIATION_REQUIRED

            attempts_before = database.connection.execute(
                "SELECT COUNT(*) FROM sandbox_calendar_attempts"
            ).fetchone()[0]
            effects_before = database.connection.execute(
                "SELECT COUNT(*) FROM sandbox_calendar_effects"
            ).fetchone()[0]
            replays_before = database.connection.execute(
                "SELECT COALESCE(SUM(replays), 0) FROM sandbox_calendar_effects"
            ).fetchone()[0]
            assert attempts_before == 4
            assert effects_before == 4
            assert replays_before == 0
            assert events.get(event_id) == reaccepted
        finally:
            database.close()

        second = RuntimeCandidateApp(
            database_path=database_path,
            jobs=jobs,
            resolve_bearer=lambda token: "candidate-1" if token == "owner" else None,
            sandbox_outcome_plan=application_plan,
        )
        status_path = f"/v1/candidate/runs/{run_id}/status"
        status, snapshot = _request(second, status_path)
        assert status == "200 OK"
        assert snapshot["phase"] == CareerLoopPhase.INTERVIEW_RECONCILIATION.value
        observed_version = snapshot["state_version"]

        status, result = _request(
            second,
            f"/v1/candidate/runs/{run_id}/interview-reconciliation",
            method="POST",
            body={
                "schema_version": 1,
                "state_version": observed_version,
                "completed": True,
                "provider_event_id": provider_event_id,
            },
        )
        assert status == "409 Conflict"
        assert result == {"error": "approval_conflict"}

        status, after = _request(second, status_path)
        assert status == "200 OK"
        assert after["phase"] == CareerLoopPhase.INTERVIEW_RECONCILIATION.value
        assert after["state_version"] == observed_version

        database = SQLiteDatabase(database_path)
        try:
            assert tuple(database.connection.execute(
                "SELECT status FROM external_action_operations WHERE operation_id = ?",
                (old_operation_id,),
            ).fetchone()) == (ExternalActionStatus.RECONCILIATION_REQUIRED.value,)
            assert tuple(database.connection.execute(
                "SELECT status, version, active_action_operation_id "
                "FROM scheduling_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()) == ("accepted", 5, None)
            assert database.connection.execute(
                "SELECT COUNT(*) FROM sandbox_calendar_attempts"
            ).fetchone()[0] == 4
            assert database.connection.execute(
                "SELECT COUNT(*) FROM sandbox_calendar_effects"
            ).fetchone()[0] == 4
            assert database.connection.execute(
                "SELECT COALESCE(SUM(replays), 0) FROM sandbox_calendar_effects"
            ).fetchone()[0] == 0
            assert tuple(database.connection.execute(
                "SELECT status FROM external_action_operations "
                "WHERE operation_id = 'candidate-stale-acceptance-later'"
            ).fetchone()) == (ExternalActionStatus.SUCCEEDED.value,)
        finally:
            database.close()
    finally:
        first.close()
        if second is not None:
            second.close()


def test_ci_runs_independent_coverage_gate_after_full_pytest():
    """The workflow must retain a separate CLI coverage gate after pytest."""
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")
    assert "name: Run test suite" in workflow
    assert "id: pytest" in workflow
    assert "name: Enforce independent coverage threshold" in workflow
    assert "python -m pytest" in workflow
    assert "python -m coverage report --precision=2 --fail-under=90" in workflow
    assert workflow.index("python -m pytest") < workflow.index(
        "python -m coverage report --precision=2 --fail-under=90"
    )
    assert "steps.pytest.outcome != 'skipped'" in workflow
