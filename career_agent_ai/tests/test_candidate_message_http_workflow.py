from __future__ import annotations

from io import BytesIO
import json
import re

from career_agent_ai.application.api.candidate_approval_api import (
    candidate_approval_openapi_document,
)
from career_agent_ai.application.api.candidate_sandbox_ui import candidate_sandbox_html
from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


def _request(runtime, method: str, path: str, body=None, *, token: str = "valid"):
    encoded = b"" if body is None else json.dumps(body).encode("utf-8")
    statuses = []
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "HTTP_AUTHORIZATION": "Bearer " + token,
        "CONTENT_LENGTH": str(len(encoded)),
        "CONTENT_TYPE": "application/json",
        "wsgi.input": BytesIO(encoded),
    }
    response = b"".join(
        runtime(environ, lambda status, headers: statuses.append((status, headers)))
    )
    assert len(statuses) == 1
    status, headers = statuses[0]
    return status, dict(headers), json.loads(response)


def _runtime(path):
    job = Job.create(
        job_id="job-1",
        title="Logistics Coordinator",
        company=Company("company-1", "Acme Logistics"),
        location=Location("Germany", "Karlsruhe"),
        description="Warehouse logistics coordination",
    )
    return RuntimeCandidateApp(
        database_path=str(path),
        jobs=(job,),
        resolve_bearer=lambda token: {
            "valid": "candidate-1",
            "foreign": "candidate-2",
        }.get(token),
    )


def _communication_rows(path):
    database = SQLiteDatabase.open_read_only(str(path))
    try:
        attempts = database.connection.execute(
            "SELECT operation_id, attempt_no, outcome "
            "FROM sandbox_communication_attempts ORDER BY operation_id, attempt_no"
        ).fetchall()
        intents = database.connection.execute(
            "SELECT operation_id, fingerprint "
            "FROM sandbox_communication_intents ORDER BY operation_id"
        ).fetchall()
        deliveries = database.connection.execute(
            "SELECT operation_id, fingerprint, message_id, action, replays "
            "FROM sandbox_communication_deliveries ORDER BY operation_id"
        ).fetchall()
        messages = database.connection.execute(
            "SELECT message_id, sender, recipient, subject, body, direction "
            "FROM communication_messages ORDER BY message_id"
        ).fetchall()
        provider_messages = database.connection.execute(
            "SELECT message_id, direction "
            "FROM sandbox_communication_messages ORDER BY message_id"
        ).fetchall()
        return attempts, intents, deliveries, messages, provider_messages
    finally:
        database.close()


def _approve(runtime, run_id: str):
    status, _, prompt = _request(
        runtime, "GET", f"/v1/candidate/runs/{run_id}/approval"
    )
    assert status == "200 OK"
    status, _, result = _request(
        runtime,
        "POST",
        f"/v1/candidate/runs/{run_id}/approval",
        {
            "schema_version": 1,
            "run_id": run_id,
            "state_version": prompt["state_version"],
            "action_fingerprint": prompt["action_fingerprint"],
            "decision": "approve",
        },
    )
    assert status == "200 OK"
    return prompt, result


def test_candidate_message_start_api_requires_separate_approval_before_sandbox_delivery(
    tmp_path,
):
    path = tmp_path / "candidate-message-http.sqlite"
    first = _runtime(path)
    try:
        status, headers, started = _request(
            first,
            "POST",
            "/v1/candidate/runs",
            {
                "schema_version": 1,
                "run_id": "candidate-message-http",
                "keyword": "Logistics",
                "candidate_profile": (
                    "Logistics professional with warehouse picking, packing, "
                    "inventory control, and daily workflow coordination experience."
                ),
                "location": "Karlsruhe",
                "sender": "candidate@example.test",
                "recipient": "recruiter@example.test",
                "message_subject": "Application follow-up",
                "message_body": "I would welcome a conversation about the logistics role.",
            },
        )
        assert status == "201 Created"
        assert headers["Cache-Control"] == "no-store"
        assert started["phase"] == "application_approval"
        run_id = started["run_id"]

        application_prompt, application_result = _approve(first, run_id)
        assert application_prompt["action_kind"] == "approve_application"
        assert application_result == {"run_id": run_id, "phase": "message_approval"}

        before_message_approval = _communication_rows(path)
        assert before_message_approval[0] == []
        assert before_message_approval[2] == []
        assert len(before_message_approval[3]) == 1
        assert before_message_approval[3][0][-1] == "draft"
        assert before_message_approval[4] == [
            (f"{run_id}:message", "draft")
        ]

        message_prompt, message_result = _approve(first, run_id)
        assert message_prompt["action_kind"] == "approve_message"
        assert message_prompt["details"] == {
            "message_id": f"{run_id}:message",
            "sender": "candidate@example.test",
            "recipient": "recruiter@example.test",
            "subject": "Application follow-up",
            "body": "I would welcome a conversation about the logistics role.",
        }
        assert message_result == {"run_id": run_id, "phase": "complete"}
    finally:
        first.close()

    operation_id = f"{run_id}:message-send"
    message_id = f"{run_id}:message"
    completed_rows = _communication_rows(path)
    attempts, intents, deliveries, messages, provider_messages = completed_rows
    assert attempts == [(operation_id, 1, "success")]
    assert len(intents) == 1 and intents[0][0] == operation_id
    assert len(deliveries) == 1
    assert deliveries[0][0] == operation_id
    assert deliveries[0][1] == intents[0][1]
    assert deliveries[0][2:] == (message_id, "send", 0)
    assert messages == [
        (
            message_id,
            "candidate@example.test",
            "recruiter@example.test",
            "Application follow-up",
            "I would welcome a conversation about the logistics role.",
            "outbound",
        )
    ]
    assert provider_messages == [(message_id, "outbound")]

    second = _runtime(path)
    try:
        status, _, foreign = _request(
            second, "GET", f"/v1/candidate/runs/{run_id}/status", token="foreign"
        )
        assert status == "404 Not Found"
        assert foreign == {"error": "not_found"}

        status, _, terminal = _request(
            second, "GET", f"/v1/candidate/runs/{run_id}/status"
        )
        assert status == "200 OK"
        assert terminal["phase"] == "complete"
        assert terminal["terminal"] is True

        status, _, replayed = _request(
            second, "POST", f"/v1/candidate/runs/{run_id}/continue"
        )
        assert status == "200 OK"
        assert replayed == {"run_id": run_id, "phase": "complete"}
    finally:
        second.close()

    assert _communication_rows(path) == completed_rows


def test_candidate_start_rejects_incomplete_message_identity_pair_before_storage(tmp_path):
    path = tmp_path / "incomplete-message.sqlite"
    runtime = _runtime(path)
    try:
        status, _, response = _request(
            runtime,
            "POST",
            "/v1/candidate/runs",
            {
                "schema_version": 1,
                "run_id": "incomplete-message",
                "keyword": "Logistics",
                "candidate_profile": "Experienced logistics coordinator.",
                "sender": "candidate@example.test",
            },
        )
        assert status == "400 Bad Request"
        assert response["error"] == "invalid_request"
        assert not path.exists()
    finally:
        runtime.close()


def test_candidate_start_openapi_and_browser_ui_publish_optional_message_workflow():
    document = candidate_approval_openapi_document()
    schema = document["paths"]["/v1/candidate/runs"]["post"]["requestBody"][
        "content"
    ]["application/json"]["schema"]
    for field in ("sender", "recipient", "message_subject", "message_body"):
        assert field in schema["properties"]
    assert schema["properties"]["sender"]["maxLength"] == 200
    assert schema["properties"]["recipient"]["maxLength"] == 200
    assert schema["properties"]["message_subject"]["maxLength"] == 500
    assert schema["properties"]["message_body"]["maxLength"] == 100_000
    assert re.fullmatch(
        schema["properties"]["sender"]["pattern"], "candidate@example.test"
    )
    assert re.fullmatch(
        schema["properties"]["sender"]["pattern"], "candidate name"
    ) is None
    assert re.fullmatch(
        schema["properties"]["message_subject"]["pattern"], "Application follow-up"
    )
    assert re.fullmatch(
        schema["properties"]["message_subject"]["pattern"], "Application\nBcc: x"
    ) is None
    assert len(schema["allOf"]) == 3
    assert schema["additionalProperties"] is False

    html = candidate_sandbox_html().decode("utf-8")
    for element_id in (
        "message-sender",
        "message-recipient",
        "message-subject",
        "message-body",
    ):
        assert f'id="{element_id}"' in html
    assert "message_subject: messageSubject" in html
    assert "message_body: messageBody" in html
    assert "Provide both sender and recipient" in html


def test_candidate_ci_enforces_independent_coverage_gate_after_pytest():
    from pathlib import Path

    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    test_job = workflow.split("  release-evidence:", 1)[0]
    pytest_command = "python -m pytest"
    coverage_command = "python -m coverage report --fail-under=90"

    assert pytest_command in test_job
    assert coverage_command in test_job
    assert test_job.index(pytest_command) < test_job.index(coverage_command)


def test_candidate_start_rejects_invalid_message_identifiers_and_header_injection(
    tmp_path,
):
    cases = (
        {
            "sender": "candidate name",
            "recipient": "recruiter@example.test",
        },
        {
            "sender": "candidate@example.test",
            "recipient": "recruiter@example.test",
            "message_subject": "Application\nBcc: attacker@example.test",
        },
    )
    for index, message_fields in enumerate(cases):
        path = tmp_path / f"invalid-message-{index}.sqlite"
        runtime = _runtime(path)
        body = {
            "schema_version": 1,
            "run_id": f"invalid-message-{index}",
            "keyword": "Logistics",
            "candidate_profile": "Experienced logistics coordinator.",
            **message_fields,
        }
        try:
            status, _, response = _request(
                runtime, "POST", "/v1/candidate/runs", body
            )
            assert status == "400 Bad Request"
            assert response["error"] == "invalid_request"
            assert not path.exists()
        finally:
            runtime.close()
