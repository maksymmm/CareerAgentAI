from __future__ import annotations

import json
from datetime import datetime, timezone
from io import BytesIO

from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopPhase,
    CareerLoopRequest,
)
from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.runtime.composition import build_candidate_sandbox_app_from_env
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_sandbox_communication_provider import (
    SandboxCommunicationOutcome,
)


def _request(app, *, method: str, path: str, body: dict | None = None, token: str = "valid"):
    statuses = []
    encoded = b"" if body is None else json.dumps(body).encode("utf-8")
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "HTTP_AUTHORIZATION": f"Bearer {token}" if token else "",
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(encoded)),
        "wsgi.input": BytesIO(encoded),
    }
    response = b"".join(
        app(environ, lambda status, headers: statuses.append((status, headers)))
    )
    return statuses[0], json.loads(response)


def _runtime(database_path, job, resolve_bearer, outcome_plan):
    return build_candidate_sandbox_app_from_env(
        resolve_bearer=resolve_bearer,
        jobs=(job,),
        environ={
            "CAREER_AGENT_DB_PATH": str(database_path),
            "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
            "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
        },
        communication_outcome_plan=outcome_plan,
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


def test_candidate_message_reconciliation_completes_over_http_after_restart(
    tmp_path,
):
    database_path = tmp_path / "candidate-message-reconciliation.sqlite"
    run_id = "candidate-message-reconciliation"
    provider_attempts: list[tuple[str, int]] = []

    def timeout_after_delivery(
        operation_id: str, attempt_number: int
    ) -> SandboxCommunicationOutcome:
        provider_attempts.append((operation_id, attempt_number))
        return SandboxCommunicationOutcome.TIMEOUT_AFTER_EFFECT

    job = Job.create(
        job_id="job-1",
        title="Logistics Coordinator",
        company=Company("company-1", "Acme Logistics"),
        location=Location("Germany", "Karlsruhe"),
        description="Warehouse logistics coordination",
        created_at=datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc),
    )
    request = CareerLoopRequest(
        user_id="candidate-1",
        keyword="Logistics",
        candidate_profile=(
            "Logistics professional with warehouse picking, packing, inventory "
            "control, and daily workflow coordination experience."
        ),
        location="Karlsruhe",
        sender="candidate@example.test",
        recipient="recruiter@example.test",
    )
    resolve_owner = lambda token: "candidate-1" if token == "valid" else None
    first_runtime = _runtime(
        database_path, job, resolve_owner, timeout_after_delivery
    )

    started = first_runtime.start(request, run_id=run_id)
    assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL

    message_gate = _approve_current_action(first_runtime, run_id)
    assert message_gate["phase"] == CareerLoopPhase.MESSAGE_APPROVAL.value

    ambiguous = _approve_current_action(first_runtime, run_id)
    assert ambiguous["phase"] == CareerLoopPhase.MESSAGE_RECONCILIATION.value
    assert len(provider_attempts) == 1
    assert provider_attempts[0][1] == 1
    operation_id = provider_attempts[0][0]
    first_runtime.close()

    database = SQLiteDatabase(str(database_path))
    try:
        attempts_before = database.connection.execute(
            "SELECT operation_id, attempt_no, outcome "
            "FROM sandbox_communication_attempts ORDER BY operation_id, attempt_no"
        ).fetchall()
        deliveries_before = database.connection.execute(
            "SELECT operation_id, message_id, action, replays "
            "FROM sandbox_communication_deliveries ORDER BY operation_id"
        ).fetchall()
        assert attempts_before == [
            (operation_id, 1, SandboxCommunicationOutcome.TIMEOUT_AFTER_EFFECT.value)
        ]
        assert len(deliveries_before) == 1
        assert deliveries_before[0][0] == operation_id
        assert deliveries_before[0][2:] == ("send", 0)
        message_id = deliveries_before[0][1]
        assert message_id
        assert database.connection.execute(
            "SELECT direction FROM sandbox_communication_messages WHERE message_id = ?",
            (message_id,),
        ).fetchone() == ("outbound",)
    finally:
        database.close()

    restarted_runtime = _runtime(
        database_path, job, resolve_owner, timeout_after_delivery
    )
    reconciliation_path = (
        f"/v1/candidate/runs/{run_id}/message-reconciliation"
    )
    before_read = database_path.read_bytes()
    (status, headers), intent = _request(
        restarted_runtime, method="GET", path=reconciliation_path
    )
    assert status == "200 OK"
    assert ("Cache-Control", "no-store") in headers
    assert intent["schema_version"] == 1
    assert intent["run_id"] == run_id
    assert intent["sender"] == "candidate@example.test"
    assert intent["recipient"] == "recruiter@example.test"
    assert intent["subject"] == "Application follow-up"
    assert "operation_id" not in intent
    assert "message_id" not in intent
    assert "body" not in intent
    assert database_path.read_bytes() == before_read

    foreign_runtime = _runtime(
        database_path,
        job,
        lambda token: "different-candidate" if token == "valid" else None,
        timeout_after_delivery,
    )
    (status, _), foreign_body = _request(
        foreign_runtime, method="GET", path=reconciliation_path
    )
    assert status == "404 Not Found"
    assert foreign_body == {"error": "not_found"}
    foreign_runtime.close()

    (status, _), stale_body = _request(
        restarted_runtime,
        method="POST",
        path=reconciliation_path,
        body={
            "schema_version": 1,
            "state_version": intent["state_version"] + 1,
            "delivered": True,
        },
    )
    assert status == "409 Conflict"
    assert stale_body == {"error": "approval_conflict"}

    (status, _), completed = _request(
        restarted_runtime,
        method="POST",
        path=reconciliation_path,
        body={
            "schema_version": 1,
            "state_version": intent["state_version"],
            "delivered": True,
        },
    )
    assert status == "200 OK"
    assert completed == {
        "run_id": run_id,
        "phase": CareerLoopPhase.COMPLETE.value,
    }

    (status, _), final_status = _request(
        restarted_runtime,
        method="GET",
        path=f"/v1/candidate/runs/{run_id}/status",
    )
    assert status == "200 OK"
    assert final_status["phase"] == CareerLoopPhase.COMPLETE.value
    assert final_status["terminal"] is True
    restarted_runtime.close()

    assert provider_attempts == [(operation_id, 1)]
    database = SQLiteDatabase(str(database_path))
    try:
        assert database.connection.execute(
            "SELECT operation_id, attempt_no, outcome "
            "FROM sandbox_communication_attempts ORDER BY operation_id, attempt_no"
        ).fetchall() == attempts_before
        assert database.connection.execute(
            "SELECT operation_id, message_id, action, replays "
            "FROM sandbox_communication_deliveries ORDER BY operation_id"
        ).fetchall() == deliveries_before
        assert database.connection.execute(
            "SELECT status FROM external_action_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone() == ("succeeded",)
    finally:
        database.close()
