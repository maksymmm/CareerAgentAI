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
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_sandbox_submission_provider import (
    SandboxOutcome,
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


def _runtime(database_path, job, outcome_plan):
    return build_candidate_sandbox_app_from_env(
        resolve_bearer=lambda token: "candidate-1" if token == "valid" else None,
        jobs=(job,),
        environ={
            "CAREER_AGENT_DB_PATH": str(database_path),
            "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
            "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
        },
        sandbox_outcome_plan=outcome_plan,
    )


def test_concurrent_authenticated_application_reconciliation_is_single_winner(
    tmp_path,
):
    database_path = tmp_path / "candidate-application-reconciliation-race.sqlite"
    provider_calls: list[tuple[str, int]] = []

    def timeout_after_effect(operation_id: str, attempt: int) -> SandboxOutcome:
        provider_calls.append((operation_id, attempt))
        return SandboxOutcome.TIMEOUT_AFTER_EFFECT

    job = Job.create(
        job_id="job-application-race",
        title="Logistics Coordinator",
        company=Company("company-1", "Acme Logistics"),
        location=Location("Germany", "Karlsruhe"),
        description="Warehouse logistics coordination",
        created_at=datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc),
    )
    candidate_request = CareerLoopRequest(
        user_id="candidate-1",
        keyword="Logistics",
        candidate_profile=(
            "Logistics professional with warehouse picking, packing, inventory "
            "control, and daily workflow coordination experience."
        ),
        location="Karlsruhe",
    )
    first_runtime = _runtime(database_path, job, timeout_after_effect)
    started = first_runtime.start(
        candidate_request, run_id="candidate-application-reconciliation-race"
    )
    assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL
    durable_run_id = started.run_id

    ambiguous = _approve_current_action(first_runtime, durable_run_id)
    assert ambiguous["phase"] == CareerLoopPhase.APPLICATION_RECONCILIATION.value
    assert len(provider_calls) == 1
    operation_id, attempt_number = provider_calls[0]
    assert attempt_number == 1
    first_runtime.close()

    database = SQLiteDatabase(str(database_path))
    try:
        evidence = database.connection.execute(
            "SELECT idempotency_key, application_id, provider_submission_id, replays "
            "FROM sandbox_submission_ledger"
        ).fetchall()
        attempts = database.connection.execute(
            "SELECT idempotency_key, attempt_no, outcome "
            "FROM sandbox_submission_attempts ORDER BY idempotency_key, attempt_no"
        ).fetchall()
        assert len(evidence) == 1
        assert evidence[0][0] == operation_id
        assert evidence[0][2]
        assert evidence[0][3] == 0
        assert attempts == [
            (operation_id, 1, SandboxOutcome.TIMEOUT_AFTER_EFFECT.value)
        ]
        provider_submission_id = evidence[0][2]
    finally:
        database.close()

    restarted_runtime = _runtime(
        database_path,
        job,
        lambda _operation_id, _attempt: SandboxOutcome.SUCCESS,
    )
    reconciliation_path = (
        f"/v1/candidate/runs/{durable_run_id}/application-reconciliation"
    )
    (status, _), snapshot = _request(
        restarted_runtime,
        method="GET",
        path=f"/v1/candidate/runs/{durable_run_id}/status",
    )
    assert status == "200 OK"
    assert snapshot["phase"] == CareerLoopPhase.APPLICATION_RECONCILIATION.value
    state_version = snapshot["state_version"]

    payload = {
        "schema_version": 1,
        "state_version": state_version,
        "submitted": True,
        "provider_submission_id": provider_submission_id,
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

    (status, _), final_snapshot = _request(
        restarted_runtime,
        method="GET",
        path=f"/v1/candidate/runs/{durable_run_id}/status",
    )
    assert status == "200 OK"
    assert final_snapshot["phase"] == CareerLoopPhase.COMPLETE.value
    assert final_snapshot["terminal"] is True
    restarted_runtime.close()

    assert provider_calls == [(operation_id, 1)]
    database = SQLiteDatabase(str(database_path))
    try:
        assert database.connection.execute(
            "SELECT idempotency_key, application_id, provider_submission_id, replays "
            "FROM sandbox_submission_ledger"
        ).fetchall() == evidence
        assert database.connection.execute(
            "SELECT idempotency_key, attempt_no, outcome "
            "FROM sandbox_submission_attempts ORDER BY idempotency_key, attempt_no"
        ).fetchall() == attempts
        assert database.connection.execute(
            "SELECT status FROM external_action_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone() == ("succeeded",)
    finally:
        database.close()
