from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO
from threading import Barrier

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
    ScheduleStatus,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_sandbox_calendar_provider import (
    SandboxCalendarOutcome,
)
from career_agent_ai.application.storage.sqlite_scheduling_repository import (
    SQLiteSchedulingRepository,
)


def _request(app, *, method: str, path: str, body: dict | None = None):
    statuses = []
    encoded = b"" if body is None else json.dumps(body).encode("utf-8")
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "HTTP_AUTHORIZATION": "Bearer valid",
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(encoded)),
        "wsgi.input": BytesIO(encoded),
    }
    response = b"".join(
        app(environ, lambda status, headers: statuses.append((status, dict(headers))))
    )
    assert len(statuses) == 1
    return statuses[0], json.loads(response)


def _runtime(database_path, job, calendar_outcome_plan):
    return build_candidate_sandbox_app_from_env(
        resolve_bearer=lambda token: "candidate-1" if token == "valid" else None,
        jobs=(job,),
        environ={
            "CAREER_AGENT_DB_PATH": str(database_path),
            "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
            "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
        },
        calendar_outcome_plan=calendar_outcome_plan,
    )


def _approve_current_action(app, run_id: str) -> dict:
    path = f"/v1/candidate/runs/{run_id}/approval"
    (status, _), prompt = _request(app, method="GET", path=path)
    assert status == "200 OK"
    (status, _), result = _request(
        app,
        method="POST",
        path=path,
        body={
            "schema_version": 1,
            "run_id": prompt["run_id"],
            "state_version": prompt["state_version"],
            "action_fingerprint": prompt["action_fingerprint"],
            "decision": "approve",
        },
    )
    assert status == "200 OK"
    return result


def test_concurrent_candidate_calendar_reconciliation_is_single_winner_after_restart(
    tmp_path,
):
    database_path = tmp_path / "candidate-calendar-reconciliation-race.sqlite"
    event_id = "interview-calendar-race"
    created_at = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
    database = SQLiteDatabase(str(database_path))
    try:
        SQLiteSchedulingRepository(database).create(
            ScheduleEvent(
                event_id=event_id,
                candidate_id="candidate-1",
                employer_name="Acme Logistics",
                event_type=ScheduleEventType.INTERVIEW,
                location="Hauptstrasse 1, Karlsruhe",
                start_at=datetime(2026, 12, 1, 9, 0, tzinfo=timezone.utc),
                end_at=datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc),
                timezone_name="Europe/Berlin",
                created_at=created_at,
                updated_at=created_at,
            )
        )
    finally:
        database.close()

    provider_calls: list[tuple[str, int]] = []

    def timeout_after_effect(operation_id: str, attempt: int) -> SandboxCalendarOutcome:
        provider_calls.append((operation_id, attempt))
        return SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT

    job = Job.create(
        job_id="job-calendar-race",
        title="Logistics Coordinator",
        company=Company("company-1", "Acme Logistics"),
        location=Location("Germany", "Karlsruhe"),
        description="Warehouse logistics coordination",
        created_at=created_at,
    )
    candidate_request = CareerLoopRequest(
        user_id="candidate-1",
        keyword="Logistics",
        candidate_profile=(
            "Logistics professional with warehouse picking, packing, inventory "
            "control, and daily workflow coordination experience."
        ),
        location="Karlsruhe",
        sender="candidate@example.test",
        recipient="recruiter@example.test",
        schedule_event_id=event_id,
    )
    first_runtime = _runtime(database_path, job, timeout_after_effect)
    started = first_runtime.start(
        candidate_request, run_id="candidate-calendar-reconciliation-race"
    )
    assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL
    durable_run_id = started.run_id

    application = _approve_current_action(first_runtime, durable_run_id)
    assert application["phase"] == CareerLoopPhase.MESSAGE_APPROVAL.value
    message = _approve_current_action(first_runtime, durable_run_id)
    assert message["phase"] == CareerLoopPhase.INTERVIEW_APPROVAL.value
    ambiguous = _approve_current_action(first_runtime, durable_run_id)
    assert ambiguous["phase"] == CareerLoopPhase.INTERVIEW_RECONCILIATION.value
    assert len(provider_calls) == 1
    operation_id, attempt_number = provider_calls[0]
    assert attempt_number == 1
    first_runtime.close()

    database = SQLiteDatabase(str(database_path))
    try:
        effects_before = database.connection.execute(
            "SELECT operation_id, fingerprint, action, event_json, replays "
            "FROM sandbox_calendar_effects ORDER BY operation_id"
        ).fetchall()
        attempts_before = database.connection.execute(
            "SELECT operation_id, attempt_no, outcome "
            "FROM sandbox_calendar_attempts ORDER BY operation_id, attempt_no"
        ).fetchall()
        assert len(effects_before) == 1
        assert effects_before[0][0] == operation_id
        assert effects_before[0][4] == 0
        assert attempts_before == [
            (
                operation_id,
                1,
                SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT.value,
            )
        ]
        provider_event_id = json.loads(effects_before[0][3])["provider_event_id"]
        assert provider_event_id
    finally:
        database.close()

    restart_plan_calls: list[tuple[str, int]] = []

    def unexpected_provider_retry(
        operation_id: str, attempt: int
    ) -> SandboxCalendarOutcome:
        restart_plan_calls.append((operation_id, attempt))
        return SandboxCalendarOutcome.SUCCESS

    restarted_runtime = _runtime(database_path, job, unexpected_provider_retry)
    reconciliation_path = (
        f"/v1/candidate/runs/{durable_run_id}/interview-reconciliation"
    )
    (status, _), snapshot = _request(
        restarted_runtime,
        method="GET",
        path=f"/v1/candidate/runs/{durable_run_id}/status",
    )
    assert status == "200 OK"
    assert snapshot["phase"] == CareerLoopPhase.INTERVIEW_RECONCILIATION.value
    state_version = snapshot["state_version"]

    (status, _), wrong_evidence = _request(
        restarted_runtime,
        method="POST",
        path=reconciliation_path,
        body={
            "schema_version": 1,
            "state_version": state_version,
            "completed": True,
            "provider_event_id": "wrong-provider-event",
        },
    )
    assert status == "409 Conflict"
    assert wrong_evidence == {"error": "approval_conflict"}
    (status, _), unchanged_status = _request(
        restarted_runtime,
        method="GET",
        path=f"/v1/candidate/runs/{durable_run_id}/status",
    )
    assert status == "200 OK"
    assert unchanged_status["state_version"] == state_version
    assert unchanged_status["phase"] == CareerLoopPhase.INTERVIEW_RECONCILIATION.value

    database = SQLiteDatabase(str(database_path))
    try:
        assert database.connection.execute(
            "SELECT operation_id, fingerprint, action, event_json, replays "
            "FROM sandbox_calendar_effects ORDER BY operation_id"
        ).fetchall() == effects_before
        assert database.connection.execute(
            "SELECT operation_id, attempt_no, outcome "
            "FROM sandbox_calendar_attempts ORDER BY operation_id, attempt_no"
        ).fetchall() == attempts_before
        assert database.connection.execute(
            "SELECT status, provider_event_id, version FROM scheduling_events "
            "WHERE event_id = ?",
            (event_id,),
        ).fetchone() == (ScheduleStatus.PROPOSED.value, None, 1)
        assert database.connection.execute(
            "SELECT status FROM external_action_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone() == ("reconciliation_required",)
    finally:
        database.close()

    payload = {
        "schema_version": 1,
        "state_version": state_version,
        "completed": True,
        "provider_event_id": provider_event_id,
    }
    barrier = Barrier(3)

    def reconcile():
        barrier.wait(timeout=10)
        return _request(
            restarted_runtime,
            method="POST",
            path=reconciliation_path,
            body=payload,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(reconcile)
        second = executor.submit(reconcile)
        barrier.wait(timeout=10)
        responses = [first.result(timeout=20), second.result(timeout=20)]

    statuses = sorted(response[0][0] for response in responses)
    assert statuses == ["200 OK", "409 Conflict"]
    successful = next(body for (status, _), body in responses if status == "200 OK")
    rejected = next(body for (status, _), body in responses if status == "409 Conflict")
    assert successful == {
        "run_id": durable_run_id,
        "phase": CareerLoopPhase.COMPLETE.value,
    }
    assert rejected == {"error": "approval_conflict"}

    (status, _), final_status = _request(
        restarted_runtime,
        method="GET",
        path=f"/v1/candidate/runs/{durable_run_id}/status",
    )
    assert status == "200 OK"
    assert final_status["phase"] == CareerLoopPhase.COMPLETE.value
    assert final_status["terminal"] is True
    restarted_runtime.close()

    database = SQLiteDatabase(str(database_path))
    try:
        assert database.connection.execute(
            "SELECT status, provider_event_id, version FROM scheduling_events "
            "WHERE event_id = ?",
            (event_id,),
        ).fetchone() == (ScheduleStatus.ACCEPTED.value, provider_event_id, 2)
        assert database.connection.execute(
            "SELECT status FROM external_action_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone() == ("succeeded",)
        assert database.connection.execute(
            "SELECT operation_id, fingerprint, action, event_json, replays "
            "FROM sandbox_calendar_effects ORDER BY operation_id"
        ).fetchall() == effects_before
        assert database.connection.execute(
            "SELECT operation_id, attempt_no, outcome "
            "FROM sandbox_calendar_attempts ORDER BY operation_id, attempt_no"
        ).fetchall() == attempts_before
    finally:
        database.close()

    assert provider_calls == [(operation_id, 1)]
    assert restart_plan_calls == []
