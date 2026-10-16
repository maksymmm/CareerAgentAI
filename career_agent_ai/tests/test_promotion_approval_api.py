from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from io import BytesIO

import pytest

from career_agent_ai.application.api import (
    PromotionApprovalWSGIApp,
    ReleaseOperator,
    promotion_approval_openapi_document,
)
from career_agent_ai.application.external_actions import ExternalActionOperation, ExternalActionStatus
from career_agent_ai.application.release import DryRunPromotionPlan


class Operations:
    def __init__(self, operation):
        self.operation = operation

    def get(self, operation_id):
        return self.operation if operation_id == self.operation.operation_id else None


def operation():
    plan = DryRunPromotionPlan(
        source_commit="d" * 40,
        target_reference="registry.example/app@sha256:" + "e" * 64,
        rollback_reference="registry.example/app@sha256:" + "c" * 64,
        publication_receipt_sha256="sha256:" + "b" * 64,
    )
    payload = {
        **json.loads(plan.to_json()),
        "plan_sha256": "sha256:" + hashlib.sha256(plan.to_json().encode("ascii")).hexdigest(),
    }
    return ExternalActionOperation(
        operation_id="promote:release-1",
        action_type="release.promote.dry-run",
        payload=payload,
    )


def app(*, scopes=frozenset({"release:promote:dry-run"})):
    return PromotionApprovalWSGIApp(
        Operations(operation()),
        resolve_bearer=lambda token: ReleaseOperator("operator:release", scopes)
        if token == "valid"
        else None,
    )


def request(application, *, method="GET", token="valid", body=None, path=None, content_type="application/json"):
    encoded = b"" if body is None else json.dumps(body).encode("utf-8")
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path or "/v1/release/promotions/promote:release-1/approval",
        "HTTP_AUTHORIZATION": f"Bearer {token}" if token else "",
        "CONTENT_TYPE": content_type,
        "CONTENT_LENGTH": str(len(encoded)),
        "wsgi.input": BytesIO(encoded),
    }
    responses = []
    response = b"".join(application(environ, lambda status, headers: responses.append((status, headers))))
    return responses[0], json.loads(response)


def test_http_decision_is_durable_idempotent_and_never_executes(tmp_path):
    from career_agent_ai.application.release import PromotionApprovalReceiptService
    from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
    from career_agent_ai.application.storage.sqlite_external_action_repository import SQLiteExternalActionOperationRepository
    path = str(tmp_path / "http-receipt.sqlite")
    database = SQLiteDatabase(path)
    repository = SQLiteExternalActionOperationRepository(database)
    repository.create(operation())
    receipts = PromotionApprovalReceiptService(repository)
    application = PromotionApprovalWSGIApp(
        repository, receipts=receipts,
        resolve_bearer=lambda token: ReleaseOperator("operator:release", frozenset({"release:promote:dry-run"})),
    )
    _, prompt = request(application)
    body = {"schema_version": 1, "action_fingerprint": prompt["action_fingerprint"], "decision": "approve"}
    for _ in range(2):
        (status, _), result = request(application, method="POST", body=body)
        assert status == "200 OK"
    (status, _), result = request(application, method="POST", body={**body, "decision": "decline"})
    assert status == "409 Conflict"
    assert result == {"error": "approval_conflict"}
    assert repository.get("promote:release-1").status == ExternalActionStatus.PREPARED
    database.close()
    repository = SQLiteExternalActionOperationRepository(SQLiteDatabase(path))
    recovered = PromotionApprovalReceiptService(repository).consume(
        "promote:release-1", authenticated_operator_id="operator:release",
        granted_scopes=("release:promote:dry-run",),
    )
    assert recovered.status == ExternalActionStatus.SUCCEEDED
    application = PromotionApprovalWSGIApp(
        repository, receipts=PromotionApprovalReceiptService(repository),
        resolve_bearer=lambda token: ReleaseOperator("operator:release", frozenset({"release:promote:dry-run"})),
    )
    before_operation = repository.get("promote:release-1")
    before_receipt = repository.get(recovered.operation_id)
    (status, _), result = request(application, method="POST", body=body)
    assert status == "200 OK"
    assert result["validated"] is True
    (status, _), result = request(application, method="POST", body={**body, "decision": "decline"})
    assert status == "409 Conflict"
    assert result == {"error": "approval_conflict"}
    assert repository.get("promote:release-1") == before_operation
    assert repository.get(recovered.operation_id) == before_receipt


def test_http_dry_run_requires_receipt_and_never_reuses_it():
    from career_agent_ai.application.release import PromotionApprovalReceiptService
    from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
    from career_agent_ai.application.storage.sqlite_external_action_repository import SQLiteExternalActionOperationRepository
    repository = SQLiteExternalActionOperationRepository(SQLiteDatabase())
    repository.create(operation())
    application = PromotionApprovalWSGIApp(repository, receipts=PromotionApprovalReceiptService(repository),
        resolve_bearer=lambda token: ReleaseOperator("operator:release", frozenset({"release:promote:dry-run"})))
    path = "/v1/release/promotions/promote:release-1/execute-dry-run"
    assert request(application, method="POST", path=path, body={"schema_version": 1})[0][0] == "403 Forbidden"
    _, prompt = request(application)
    assert request(application, method="POST", body={"schema_version": 1, "decision": "approve", "action_fingerprint": prompt["action_fingerprint"]})[0][0] == "200 OK"
    assert request(application, path=path)[0][0] == "405 Method Not Allowed"
    assert request(application, method="POST", path=path, body={"schema_version": 1, "approved": True})[0][0] == "400 Bad Request"
    (status, _), result = request(application, method="POST", path=path, body={"schema_version": 1})
    assert status == "200 OK"
    assert result["execution_mode"] == "dry-run"
    assert result["receipt_status"] == "succeeded"
    assert request(application, method="POST", path=path, body={"schema_version": 1})[0][0] == "409 Conflict"
    recovery = path.replace("execute-dry-run", "recover-dry-run")
    assert request(application, method="POST", path=recovery, body={"schema_version": 1})[0][0] == "200 OK"


@pytest.mark.parametrize("route", ["execute-dry-run", "recover-dry-run"])
def test_execution_routes_require_configured_receipts_and_json(route):
    path = "/v1/release/promotions/promote:release-1/" + route
    assert request(app(), method="POST", path=path, body={"schema_version": 1})[0][0] == "404 Not Found"
    application = app()
    application._receipts = object()
    assert request(application, method="POST", path=path, body={}, content_type="text/plain")[0][0] == "415 Unsupported Media Type"
    assert request(application, method="POST", path=path, body={"schema_version": 1})[0][0] == "500 Internal Server Error"


@pytest.mark.parametrize("route", ["execute-dry-run", "recover-dry-run"])
@pytest.mark.parametrize("identity", ["missing", "under-scoped", "foreign"])
def test_execution_routes_reject_unauthorized_operators_without_state_changes(route, identity):
    from career_agent_ai.application.release import PromotionApprovalReceiptService, PromotionApprovalSubmission, build_promotion_approval_prompt
    from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
    from career_agent_ai.application.storage.sqlite_external_action_repository import SQLiteExternalActionOperationRepository
    repository = SQLiteExternalActionOperationRepository(SQLiteDatabase())
    original = repository.create(operation())
    service = PromotionApprovalReceiptService(repository)
    scopes = frozenset({"release:promote:dry-run"})
    prompt = build_promotion_approval_prompt(original, operator_id="owner", granted_scopes=scopes)
    receipt = service.record(PromotionApprovalSubmission(original.operation_id, "owner", prompt.action_fingerprint, "approve"),
        authenticated_operator_id="owner", granted_scopes=tuple(scopes))
    operator = None if identity == "missing" else ReleaseOperator("foreign" if identity == "foreign" else "owner", scopes if identity == "foreign" else frozenset())
    application = PromotionApprovalWSGIApp(repository, receipts=service, resolve_bearer=lambda token: operator)
    (status, _), result = request(application, method="POST",
        path="/v1/release/promotions/promote:release-1/" + route, body={"schema_version": 1})
    assert status == ("401 Unauthorized" if identity == "missing" else "403 Forbidden")
    assert repository.get(original.operation_id) == original
    assert repository.get(receipt.operation_id) == receipt


def test_http_recovery_after_claim_restart_never_executes(tmp_path):
    from career_agent_ai.application.release import PromotionApprovalReceiptService, PromotionApprovalSubmission, build_promotion_approval_prompt
    from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
    from career_agent_ai.application.storage.sqlite_external_action_repository import SQLiteExternalActionOperationRepository
    path = str(tmp_path / "http-recovery.sqlite")
    database = SQLiteDatabase(path)
    repository = SQLiteExternalActionOperationRepository(database)
    original = repository.create(operation())
    scopes = frozenset({"release:promote:dry-run"})
    prompt = build_promotion_approval_prompt(original, operator_id="owner", granted_scopes=scopes)
    receipt = PromotionApprovalReceiptService(repository).record(
        PromotionApprovalSubmission(original.operation_id, "owner", prompt.action_fingerprint, "approve"),
        authenticated_operator_id="owner", granted_scopes=tuple(scopes))
    repository.transition(receipt.operation_id, ExternalActionStatus.PREPARED, ExternalActionStatus.IN_PROGRESS)
    database.close()
    repository = SQLiteExternalActionOperationRepository(SQLiteDatabase(path))
    service = PromotionApprovalReceiptService(repository)
    application = PromotionApprovalWSGIApp(repository, receipts=service,
        resolve_bearer=lambda token: ReleaseOperator("owner", scopes))
    route = "/v1/release/promotions/promote:release-1/recover-dry-run"
    for _ in range(2):
        (status, _), result = request(application, method="POST", path=route, body={"schema_version": 1})
        assert status == "200 OK"
        assert result["receipt_status"] == "reconciliation_required"
    assert repository.get(original.operation_id).status == ExternalActionStatus.PREPARED
    assert service._executor._external_actions._adapter._adapter._results == {}


@pytest.mark.parametrize("error", [ValueError, TypeError, PermissionError, RuntimeError])
def test_receipt_backend_failures_are_sanitized(error):
    class BrokenReceipts:
        def record(self, *args, **kwargs):
            raise error("api_key=synthetic-secret")
    application = app()
    application._receipts = BrokenReceipts()
    _, prompt = request(application)
    (status, _), result = request(application, method="POST", body={
        "schema_version": 1, "action_fingerprint": prompt["action_fingerprint"], "decision": "approve",
    })
    assert status == "500 Internal Server Error"
    assert result == {"error": "internal_error"}


@pytest.mark.parametrize("route", ["execute-dry-run", "recover-dry-run"])
@pytest.mark.parametrize("corruption", ["receipt", "plan"])
def test_execution_corrupt_durable_state_is_sanitized_server_error(route, corruption):
    from career_agent_ai.application.release import PromotionApprovalReceiptService, PromotionApprovalSubmission, build_promotion_approval_prompt
    from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
    from career_agent_ai.application.storage.sqlite_external_action_repository import SQLiteExternalActionOperationRepository
    repository = SQLiteExternalActionOperationRepository(SQLiteDatabase())
    original = repository.create(operation())
    scopes = frozenset({"release:promote:dry-run"})
    service = PromotionApprovalReceiptService(repository)
    prompt = build_promotion_approval_prompt(original, operator_id="owner", granted_scopes=scopes)
    receipt = service.record(PromotionApprovalSubmission(original.operation_id, "owner", prompt.action_fingerprint, "approve"),
        authenticated_operator_id="owner", granted_scopes=tuple(scopes))

    class CorruptReader:
        def get(self, operation_id):
            value = repository.get(operation_id)
            corrupt_id = receipt.operation_id if corruption == "receipt" else original.operation_id
            return replace(value, payload={"private": "synthetic-secret"}) if operation_id == corrupt_id else value

    service = PromotionApprovalReceiptService(CorruptReader())
    application = PromotionApprovalWSGIApp(repository, receipts=service,
        resolve_bearer=lambda token: ReleaseOperator("owner", scopes))
    (status, _), payload = request(application, method="POST",
        path="/v1/release/promotions/promote:release-1/" + route, body={"schema_version": 1})
    assert status == "500 Internal Server Error"
    assert payload == {"error": "internal_error"}
    assert repository.get(original.operation_id) == original
    assert repository.get(receipt.operation_id) == receipt


def test_authenticated_operator_can_read_and_validate_current_prompt():
    (status, headers), prompt = request(app())
    assert status == "200 OK"
    assert ("Cache-Control", "no-store") in headers
    assert "operator_id" not in prompt

    (status, _), result = request(
        app(),
        method="POST",
        body={
            "schema_version": 1,
            "action_fingerprint": prompt["action_fingerprint"],
            "decision": "approve",
        },
    )
    assert status == "200 OK"
    assert result == {
        "operation_id": "promote:release-1",
        "decision": "approve",
        "validated": True,
    }


def test_boundary_rejects_missing_auth_scope_and_unknown_operation():
    assert request(app(), token="")[0][0] == "401 Unauthorized"
    assert request(app(scopes=frozenset()))[0][0] == "403 Forbidden"
    assert request(app(), path="/v1/release/promotions/missing/approval")[0][0] == "404 Not Found"


@pytest.mark.parametrize("failure", [RuntimeError, ValueError, TypeError, PermissionError])
def test_boundary_sanitizes_operation_repository_failures(failure):
    class FailingOperations:
        def get(self, operation_id):
            raise failure("database password must not escape")

    application = PromotionApprovalWSGIApp(
        FailingOperations(),
        resolve_bearer=lambda token: ReleaseOperator(
            "operator:release", frozenset({"release:promote:dry-run"})
        ),
    )

    (status, _), payload = request(application)

    assert status == "500 Internal Server Error"
    assert payload == {"error": "internal_error"}


@pytest.mark.parametrize("failure", [RuntimeError, ValueError, TypeError, PermissionError])
def test_boundary_reports_authentication_backend_failures_as_server_errors(failure):
    def failing_resolver(token):
        raise failure("identity provider secret must not escape")

    application = PromotionApprovalWSGIApp(
        Operations(operation()),
        resolve_bearer=failing_resolver,
    )

    (status, _), payload = request(application)

    assert status == "500 Internal Server Error"
    assert payload == {"error": "internal_error"}


def test_boundary_rejects_stale_or_client_supplied_identity_fields():
    _, prompt = request(app())
    stale = {"schema_version": 1, "action_fingerprint": "0" * 64, "decision": "approve"}
    assert request(app(), method="POST", body=stale)[0][0] == "400 Bad Request"
    injected = {
        "schema_version": 1,
        "action_fingerprint": prompt["action_fingerprint"],
        "decision": "approve",
        "operator_id": "operator:other",
    }
    assert request(app(), method="POST", body=injected)[0][0] == "400 Bad Request"


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("corruption", ["missing_fields", "bad_checksum"])
def test_corrupt_persisted_intent_returns_sanitized_server_error(method, corruption):
    persisted = operation()
    if corruption == "missing_fields":
        persisted = replace(persisted, payload={"corrupt": "internal storage detail"})
    else:
        persisted = replace(
            persisted, payload={**persisted.payload, "plan_sha256": "sha256:" + "0" * 64}
        )
    application = PromotionApprovalWSGIApp(
        Operations(persisted),
        resolve_bearer=lambda token: ReleaseOperator(
            "operator:release", frozenset({"release:promote:dry-run"})
        ),
    )

    (status, _), payload = request(application, method=method, body={})

    assert status == "500 Internal Server Error"
    assert payload == {"error": "internal_error"}


def test_boundary_rejects_invalid_transport_inputs_and_methods():
    assert request(app(), method="PUT")[0][0] == "405 Method Not Allowed"
    assert request(app(), method="POST", body={}, content_type="text/plain")[0][0] == "415 Unsupported Media Type"
    assert request(app(), path="/v1/release/promotions/promote/release-1/approval")[0][0] == "404 Not Found"


def test_openapi_documents_bearer_auth_and_server_bound_identity():
    document = promotion_approval_openapi_document()
    path = document["paths"]["/v1/release/promotions/{operation_id}/approval"]
    schema = path["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert path["get"]["security"] == [{"bearerAuth": []}]
    assert set(schema["required"]) == {"schema_version", "action_fingerprint", "decision"}
    assert "operator_id" not in schema["properties"]
    assert "operation_id" not in schema["properties"]


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("status", [s for s in ExternalActionStatus if s != ExternalActionStatus.PREPARED])
def test_non_prepared_operations_are_conflicts(method, status):
    application = PromotionApprovalWSGIApp(
        Operations(replace(operation(), status=status, result={}, error="previous failure")),
        resolve_bearer=lambda token: ReleaseOperator("operator:release", frozenset({"release:promote:dry-run"})),
    )
    (response_status, _), payload = request(application, method=method, body={})
    assert response_status == "409 Conflict"
    assert payload == {"error": "operation_not_awaiting_approval"}


def test_under_scoped_operator_cannot_probe_repository():
    class UnreadableOperations:
        def get(self, operation_id):
            raise AssertionError("under-scoped caller must not access storage")

    application = PromotionApprovalWSGIApp(
        UnreadableOperations(), resolve_bearer=lambda token: ReleaseOperator("operator:release", frozenset())
    )
    for identifier in ("promote:release-1", "missing"):
        assert request(application, path=f"/v1/release/promotions/{identifier}/approval")[0][0] == "403 Forbidden"


def test_openapi_success_schemas_describe_actual_json_fields():
    path = promotion_approval_openapi_document()["paths"]["/v1/release/promotions/{operation_id}/approval"]
    _, prompt = request(app())
    schema = path["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert set(schema["required"]) == set(prompt)
    assert set(schema["properties"]["details"]["required"]) == set(prompt["details"])
    result = path["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert set(result["required"]) == {"operation_id", "decision", "validated"}


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("status", list(ExternalActionStatus))
@pytest.mark.parametrize("action_type", ["application.submit", "message.send", "calendar.respond"])
def test_foreign_workflow_operations_are_uniformly_unavailable(method, status, action_type):
    foreign = replace(operation(), action_type=action_type, status=status, result={}, error="previous failure")
    application = PromotionApprovalWSGIApp(
        Operations(foreign),
        resolve_bearer=lambda token: ReleaseOperator("operator:release", frozenset({"release:promote:dry-run"})),
    )
    (response_status, _), payload = request(application, method=method, body={})
    assert response_status == "404 Not Found"
    assert payload == {"error": "not_found"}


def test_openapi_documents_json_error_contracts_for_both_operations():
    path = promotion_approval_openapi_document()["paths"]["/v1/release/promotions/{operation_id}/approval"]
    for method in ("get", "post"):
        for code in ("400", "401", "403", "404", "405", "409", "415", "500"):
            schema = path[method]["responses"][code]["content"]["application/json"]["schema"]
            assert schema["required"] == ["error"]
            assert schema["properties"]["error"]["type"] == "string"
            assert schema["additionalProperties"] is False
        bad_request = path[method]["responses"]["400"]["content"]["application/json"]["schema"]
        assert bad_request["properties"]["message"]["maxLength"] == 500
