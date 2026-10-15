from __future__ import annotations

import hashlib
import json
from io import BytesIO

from career_agent_ai.application.api import (
    PromotionApprovalWSGIApp,
    ReleaseOperator,
    promotion_approval_openapi_document,
)
from career_agent_ai.application.external_actions import ExternalActionOperation
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


def test_boundary_sanitizes_operation_repository_failures():
    class FailingOperations:
        def get(self, operation_id):
            raise RuntimeError("database password must not escape")

    application = PromotionApprovalWSGIApp(
        FailingOperations(),
        resolve_bearer=lambda token: ReleaseOperator(
            "operator:release", frozenset({"release:promote:dry-run"})
        ),
    )

    (status, _), payload = request(application)

    assert status == "500 Internal Server Error"
    assert payload == {"error": "internal_error"}


def test_boundary_reports_authentication_backend_failures_as_server_errors():
    def failing_resolver(token):
        raise RuntimeError("identity provider secret must not escape")

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
