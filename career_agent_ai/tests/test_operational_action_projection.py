from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json

from career_agent_ai.application.api import openapi_document
from career_agent_ai.application.external_actions.external_action_operation import (
    ExternalActionOperation,
    ExternalActionStatus,
)
from career_agent_ai.application.runtime.composition import RuntimeOperationalApp
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import (
    SQLiteExternalActionOperationRepository,
)

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
TOKEN = "operator-status-test-token-" + "x" * 32


def _seed(path) -> None:
    database = SQLiteDatabase(str(path))
    repository = SQLiteExternalActionOperationRepository(database)
    records = (
        ("operation-application", "application.submit", "prepared"),
        ("operation-calendar", "calendar.accept", "in_progress"),
        ("operation-communication", "communication.send", "failed"),
        ("operation-unknown", "unknown.private_action", "reconciliation_required"),
        ("operation-succeeded", "calendar.accept", "succeeded"),
    )
    for operation_id, action_type, final_status in records:
        repository.create(ExternalActionOperation(
            operation_id=operation_id,
            action_type=action_type,
            payload={"private_payload": "payload-secret"},
            created_at=NOW,
            updated_at=NOW,
        ))
        if final_status == "prepared":
            continue
        repository.transition(
            operation_id,
            ExternalActionStatus.PREPARED,
            ExternalActionStatus.IN_PROGRESS,
        )
        if final_status == "failed":
            repository.transition(
                operation_id,
                ExternalActionStatus.IN_PROGRESS,
                ExternalActionStatus.FAILED,
                error="Bearer provider-error-secret",
            )
        elif final_status == "reconciliation_required":
            repository.transition(
                operation_id,
                ExternalActionStatus.IN_PROGRESS,
                ExternalActionStatus.RECONCILIATION_REQUIRED,
            )
        elif final_status == "succeeded":
            repository.transition(
                operation_id,
                ExternalActionStatus.IN_PROGRESS,
                ExternalActionStatus.SUCCEEDED,
                result={"provider_id": "provider-result-secret"},
            )
    database.close()


def _request(
    app,
    *,
    token: str | None = TOKEN,
    method: str = "GET",
    query: str = "",
):
    captured = []
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": "/v1/operational/actions",
        "QUERY_STRING": query,
        "wsgi.input": BytesIO(b""),
    }
    if token is not None:
        environ["HTTP_AUTHORIZATION"] = f"Bearer {token}"

    def start_response(status, headers):
        captured.append((status, dict(headers)))

    body = b"".join(app(environ, start_response))
    status, headers = captured[0]
    return status, headers, json.loads(body.decode("utf-8"))


def test_operational_action_projection_is_bounded_private_and_read_only(tmp_path):
    path = tmp_path / "operational-actions.sqlite"
    _seed(path)
    baseline = path.read_bytes()
    runtime = RuntimeOperationalApp(database_path=str(path), bearer_token=TOKEN)
    try:
        status, headers, first = _request(runtime, query="limit=2")
        assert status == "200 OK"
        assert headers["Cache-Control"] == "no-store"
        assert first["schema_version"] == 1
        assert [item["action_family"] for item in first["items"]] == [
            "application_submission", "calendar"
        ]
        assert [item["status"] for item in first["items"]] == ["prepared", "in_progress"]
        assert first["next_after"] is not None
        assert first["items"][0]["operation_ref"] == hashlib.sha256(
            b"operation-application"
        ).hexdigest()[:24]

        status, _, second = _request(runtime, query="limit=2&after=" + first["next_after"])
        assert status == "200 OK"
        assert [item["action_family"] for item in second["items"]] == [
            "communication", "other"
        ]
        assert second["items"][0]["status"] == "failed"
        assert second["items"][0]["error_present"] is True
        assert second["next_after"] is None

        rendered = json.dumps([first, second])
        for secret in (
            "operation-application",
            "operation-calendar",
            "operation-communication",
            "operation-unknown",
            "payload-secret",
            "provider-error-secret",
            "provider-result-secret",
            "unknown.private_action",
            "application.submit",
            "calendar.accept",
            "communication.send",
        ):
            assert secret not in rendered
    finally:
        runtime.close()
    assert path.read_bytes() == baseline


def test_operational_action_auth_method_and_query_validation_precede_storage(tmp_path):
    path = tmp_path / "not-created.sqlite"
    runtime = RuntimeOperationalApp(database_path=str(path), bearer_token=TOKEN)
    try:
        assert _request(runtime, token=None)[0] == "401 Unauthorized"
        assert not path.exists()
        assert _request(runtime, method="POST")[0] == "405 Method Not Allowed"
        assert not path.exists()
        for query in (
            "limit=0",
            "limit=26",
            "limit=1&limit=2",
            "user_id=another-operator",
            "after=",
        ):
            assert _request(runtime, query=query)[0] == "400 Bad Request"
            assert not path.exists()
    finally:
        runtime.close()


def test_operational_action_cursor_supports_maximum_unicode_operation_ids(tmp_path):
    path = tmp_path / "unicode-cursor.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteExternalActionOperationRepository(database)
    for operation_id in ("é" * 200, "ê" * 200):
        repository.create(ExternalActionOperation(
            operation_id=operation_id,
            action_type="calendar.accept",
            payload={},
            created_at=NOW,
            updated_at=NOW,
        ))
    database.close()
    baseline = path.read_bytes()
    runtime = RuntimeOperationalApp(database_path=str(path), bearer_token=TOKEN)
    try:
        status, _, first = _request(runtime, query="limit=1")
        assert status == "200 OK"
        assert first["next_after"] is not None
        assert 512 < len(first["next_after"]) <= 1200
        status, _, second = _request(
            runtime, query="limit=1&after=" + first["next_after"]
        )
        assert status == "200 OK"
        assert len(second["items"]) == 1
        assert second["next_after"] is None
    finally:
        runtime.close()
    assert path.read_bytes() == baseline


def test_operational_action_projection_rejects_invalid_cursor_without_mutation(tmp_path):
    path = tmp_path / "invalid-cursor.sqlite"
    _seed(path)
    baseline = path.read_bytes()
    runtime = RuntimeOperationalApp(database_path=str(path), bearer_token=TOKEN)
    try:
        status, _, body = _request(runtime, query="after=not-a-cursor")
        assert status == "400 Bad Request"
        assert body["error"] == "invalid_request"
    finally:
        runtime.close()
    assert path.read_bytes() == baseline


def test_operational_action_projection_fails_closed_on_corrupt_timestamps(tmp_path):
    path = tmp_path / "corrupt-action.sqlite"
    _seed(path)
    database = SQLiteDatabase(str(path))
    database.connection.execute(
        "UPDATE external_action_operations SET updated_at = ? WHERE operation_id = ?",
        ("not-a-timestamp", "operation-application"),
    )
    database.connection.commit()
    database.close()
    runtime = RuntimeOperationalApp(database_path=str(path), bearer_token=TOKEN)
    try:
        status, _, body = _request(runtime)
        assert status == "500 Internal Server Error"
        assert body == {"error": "internal_error"}
        assert "not-a-timestamp" not in json.dumps(body)
    finally:
        runtime.close()


def test_operational_action_openapi_contract_is_bounded_and_minimal():
    operation = openapi_document()["paths"]["/v1/operational/actions"]["get"]
    assert operation["security"] == [{"bearerAuth": []}]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert parameters["limit"]["schema"]["maximum"] == 25
    assert parameters["after"]["schema"]["maxLength"] == 1200
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["properties"]["items"]["maxItems"] == 25
    item = schema["properties"]["items"]["items"]
    assert set(item["required"]) == {
        "operation_ref", "action_family", "status", "created_at",
        "updated_at", "error_present",
    }
    assert item["additionalProperties"] is False
    assert "operation_id" not in item["properties"]
    assert "payload" not in item["properties"]
    assert "result" not in item["properties"]
    assert "error" not in item["properties"]
