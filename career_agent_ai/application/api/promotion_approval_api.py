"""Authenticated sandbox HTTP boundary for dry-run promotion approval."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping, Protocol

from career_agent_ai.application.external_actions import ExternalActionOperation, ExternalActionStatus
from career_agent_ai.application.release import (
    PromotionApprovalSubmission,
    build_promotion_approval_prompt,
    validate_promotion_approval_submission,
    validate_promotion_operation_id,
    PromotionApprovalReceiptService,
)
from career_agent_ai.application.external_actions.external_action_repository import OperationConflictError


@dataclass(frozen=True, slots=True)
class ReleaseOperator:
    """Authenticated operator identity and authorization scopes."""

    operator_id: str
    scopes: frozenset[str]


class PromotionOperationReader(Protocol):
    """Read prepared promotion operations without executing them."""

    def get(self, operation_id: str) -> ExternalActionOperation | None:
        """Return a durable operation by identifier."""


class PromotionApprovalWSGIApp:
    """Expose approval recording and explicit no-I/O dry-run execution routes."""

    MAX_BODY_BYTES = 4_096

    def __init__(
        self,
        operations: PromotionOperationReader,
        *,
        resolve_bearer: Callable[[str], ReleaseOperator | None],
        receipts: PromotionApprovalReceiptService | None = None,
    ) -> None:
        if not callable(resolve_bearer):
            raise TypeError("resolve_bearer must be callable")
        self._operations = operations
        self._resolve_bearer = resolve_bearer
        self._receipts = receipts

    def __call__(self, environ: Mapping[str, Any], start_response) -> Iterable[bytes]:
        """Handle one authenticated WSGI approval request."""

        parts = str(environ.get("PATH_INFO", "")).strip("/").split("/")
        if len(parts) != 5 or parts[:3] != ["v1", "release", "promotions"] or parts[4] not in {"approval", "status", "execute-dry-run", "recover-dry-run"}:
            return self._respond(start_response, "404 Not Found", {"error": "not_found"})
        execution_route = parts[4] in {"execute-dry-run", "recover-dry-run"}
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        allowed = {"GET"} if parts[4] == "status" else ({"POST"} if execution_route else {"GET", "POST"})
        if method not in allowed:
            return self._respond(
                start_response,
                "405 Method Not Allowed",
                {"error": "method_not_allowed"},
                [("Allow", ", ".join(sorted(allowed)))],
            )
        try:
            operation_id = validate_promotion_operation_id(parts[3])
        except (TypeError, ValueError) as exc:
            return self._respond(
                start_response,
                "400 Bad Request",
                {"error": "invalid_request", "message": str(exc)[:500]},
            )
        try:
            operator = self._authenticate(environ)
            if operator is None:
                return self._respond(
                    start_response,
                    "401 Unauthorized",
                    {"error": "unauthorized"},
                    [("WWW-Authenticate", "Bearer")],
                )
            if "release:promote:dry-run" not in operator.scopes:
                return self._respond(start_response, "403 Forbidden", {"error": "forbidden"})
            operation = self._operations.get(operation_id)
        except Exception:
            return self._respond(
                start_response,
                "500 Internal Server Error",
                {"error": "internal_error"},
            )
        try:
            if operation is None or operation.action_type != "release.promote.dry-run":
                return self._respond(start_response, "404 Not Found", {"error": "not_found"})
            if parts[4] == "status":
                # Validate intent even for terminal snapshots; never classify or execute.
                build_promotion_approval_prompt(
                    replace(operation, status=ExternalActionStatus.PREPARED),
                    operator_id=operator.operator_id, granted_scopes=operator.scopes,
                )
                return self._respond(start_response, "200 OK", {
                    "schema_version": 1, "operation_id": operation.operation_id,
                    "operation_status": operation.status.value, "execution_mode": "dry-run",
                })
            if execution_route:
                return self._execute_request(environ, start_response, operation_id, operator, parts[4])
            if operation.status != ExternalActionStatus.PREPARED and not (
                method == "POST" and self._receipts is not None
            ):
                return self._respond(
                    start_response, "409 Conflict", {"error": "operation_not_awaiting_approval"}
                )
            prompt = build_promotion_approval_prompt(
                replace(operation, status=ExternalActionStatus.PREPARED),
                operator_id=operator.operator_id,
                granted_scopes=operator.scopes,
            )
        except PermissionError:
            return self._respond(start_response, "403 Forbidden", {"error": "forbidden"})
        except Exception:
            return self._respond(
                start_response,
                "500 Internal Server Error",
                {"error": "internal_error"},
            )
        try:
            if method == "GET":
                return self._respond(start_response, "200 OK", prompt.to_dict())
            if str(environ.get("CONTENT_TYPE", "")).partition(";")[0].strip().casefold() != "application/json":
                return self._respond(
                    start_response,
                    "415 Unsupported Media Type",
                    {"error": "unsupported_media_type"},
                )
            payload = self._read_json(environ)
            expected = {"schema_version", "action_fingerprint", "decision"}
            if set(payload) != expected:
                raise ValueError("promotion approval fields do not match HTTP schema version 1")
            submission = PromotionApprovalSubmission.from_mapping(
                {
                    **payload,
                    "operation_id": operation_id,
                    "operator_id": operator.operator_id,
                }
            )
            decision = validate_promotion_approval_submission(
                replace(operation, status=ExternalActionStatus.PREPARED),
                authenticated_operator_id=operator.operator_id,
                granted_scopes=operator.scopes,
                submission=submission,
            )
            if self._receipts is not None:
                try:
                    self._receipts.record(
                        submission, authenticated_operator_id=operator.operator_id,
                        granted_scopes=tuple(operator.scopes),
                    )
                except OperationConflictError:
                    return self._respond(
                        start_response, "409 Conflict", {"error": "approval_conflict"},
                    )
                except Exception:
                    return self._respond(
                        start_response, "500 Internal Server Error", {"error": "internal_error"},
                    )
            return self._respond(
                start_response,
                "200 OK",
                {"operation_id": operation_id, "decision": decision.value, "validated": True},
            )
        except PermissionError:
            return self._respond(start_response, "403 Forbidden", {"error": "forbidden"})
        except (TypeError, ValueError) as exc:
            return self._respond(
                start_response,
                "400 Bad Request",
                {"error": "invalid_request", "message": str(exc)[:500]},
            )
        except Exception:
            return self._respond(
                start_response,
                "500 Internal Server Error",
                {"error": "internal_error"},
            )

    def _execute_request(
        self, environ: Mapping[str, Any], start_response: Callable,
        operation_id: str, operator: ReleaseOperator, route: str,
    ) -> Iterable[bytes]:
        """Consume or recover a durable approval through the fixed no-I/O service."""
        if self._receipts is None:
            return self._respond(start_response, "404 Not Found", {"error": "not_found"})
        if str(environ.get("CONTENT_TYPE", "")).partition(";")[0].strip().casefold() != "application/json":
            return self._respond(start_response, "415 Unsupported Media Type", {"error": "unsupported_media_type"})
        try:
            payload = self._read_json(environ)
            if set(payload) != {"schema_version"} or isinstance(payload["schema_version"], bool) or payload["schema_version"] != 1:
                raise ValueError("execution request must contain only schema_version 1")
        except (TypeError, ValueError):
            return self._respond(start_response, "400 Bad Request", {"error": "invalid_request"})
        try:
            action = self._receipts.consume if route == "execute-dry-run" else self._receipts.recover_claim
            receipt = action(operation_id, authenticated_operator_id=operator.operator_id,
                             granted_scopes=tuple(operator.scopes))
        except PermissionError:
            return self._respond(start_response, "403 Forbidden", {"error": "forbidden"})
        except OperationConflictError:
            return self._respond(start_response, "409 Conflict", {"error": "execution_conflict"})
        except Exception:
            return self._respond(start_response, "500 Internal Server Error", {"error": "internal_error"})
        return self._respond(start_response, "200 OK", {
            "operation_id": operation_id, "receipt_status": receipt.status.value,
            "execution_mode": "dry-run",
        })

    def _authenticate(self, environ: Mapping[str, Any]) -> ReleaseOperator | None:
        authorization = str(environ.get("HTTP_AUTHORIZATION", ""))
        scheme, separator, credentials = authorization.partition(" ")
        token = credentials.strip() if separator and scheme.casefold() == "bearer" else ""
        if not token:
            return None
        operator = self._resolve_bearer(token)
        return operator if isinstance(operator, ReleaseOperator) else None

    def _read_json(self, environ: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            length = int(str(environ.get("CONTENT_LENGTH", "0")))
        except ValueError as exc:
            raise ValueError("Content-Length must be an integer") from exc
        if length < 1 or length > self.MAX_BODY_BYTES:
            raise ValueError("promotion approval body size is invalid")
        body = environ["wsgi.input"].read(length)
        if len(body) != length:
            raise ValueError("promotion approval body is incomplete")
        try:
            payload = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=self._unique_json_object,
                parse_float=Decimal,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("promotion approval body must be valid UTF-8 JSON") from exc
        if not isinstance(payload, Mapping):
            raise ValueError("promotion approval body must be a JSON object")
        return payload

    @staticmethod
    def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("promotion approval body contains duplicate JSON members")
            result[key] = value
        return result

    @staticmethod
    def _respond(start_response, status: str, payload: Mapping[str, Any], extra=None):
        body = json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode()
        headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
        ]
        headers.extend(extra or [])
        start_response(status, headers)
        return (body,)


def promotion_approval_openapi_document() -> dict[str, Any]:
    """Return the OpenAPI 3.1 contract for sandbox promotion approval."""

    operation_parameter = {
        "name": "operation_id",
        "in": "path",
        "required": True,
        "schema": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"},
    }
    prompt_properties = {
        "schema_version": {"type": "integer", "const": 1},
        "operation_id": operation_parameter["schema"],
        "action_type": {"type": "string", "const": "release.promote.dry-run"},
        "required_scope": {"type": "string", "const": "release:promote:dry-run"},
        "details": {
            "type": "object",
            "required": ["environment", "execution_mode", "plan_sha256", "rollback_reference", "source_commit", "target_reference"],
            "properties": {key: {"type": "string"} for key in (
                "environment", "execution_mode", "plan_sha256", "rollback_reference", "source_commit", "target_reference"
            )},
            "additionalProperties": False,
        },
        "action_fingerprint": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "allowed_decisions": {"type": "array", "items": {"type": "string", "enum": ["approve", "decline"]}},
    }
    result_properties = {
        "operation_id": operation_parameter["schema"],
        "decision": {"type": "string", "enum": ["approve", "decline"]},
        "validated": {"type": "boolean", "const": True},
    }
    def response(description: str, properties: dict[str, Any]) -> dict[str, Any]:
        return {
            "description": description,
            "content": {"application/json": {"schema": {
                "type": "object", "properties": properties,
                "required": list(properties), "additionalProperties": False,
            }}},
        }
    error_responses = {}
    for code, error in {
        "400": "invalid_request", "401": "unauthorized", "403": "forbidden",
        "404": "not_found", "405": "method_not_allowed",
        "409": "operation_not_awaiting_approval", "415": "unsupported_media_type",
        "500": "internal_error",
    }.items():
        error_responses[code] = response(error, {"error": {"type": "string", "const": error}})
        if code == "400":
            error_responses[code]["content"]["application/json"]["schema"]["properties"]["message"] = {
                "type": "string", "maxLength": 500,
            }
    error_responses["409"]["content"]["application/json"]["schema"]["properties"]["error"] = {
        "type": "string", "enum": ["operation_not_awaiting_approval", "approval_conflict"],
    }
    document = {
        "openapi": "3.1.0",
        "info": {"title": "CareerAgentAI Promotion Approval", "version": "1.0.0"},
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer"},
            }
        },
        "paths": {
            "/v1/release/promotions/{operation_id}/approval": {
                "parameters": [operation_parameter],
                "get": {
                    "security": [{"bearerAuth": []}],
                    "responses": {"200": response("Immutable promotion approval prompt", prompt_properties),
                                  **error_responses},
                },
                "post": {
                    "security": [{"bearerAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["schema_version", "action_fingerprint", "decision"],
                                    "properties": {
                                        "schema_version": {"type": "integer", "const": 1},
                                        "action_fingerprint": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                                        "decision": {"type": "string", "enum": ["approve", "decline"]},
                                    },
                                    "additionalProperties": False,
                                }
                            }
                        },
                    },
                    "responses": {"200": response("Decision validated; durably recorded when receipt persistence is configured; never executes promotion", result_properties),
                                  **error_responses},
                },
            }
        },
    }
    for route in ("execute-dry-run", "recover-dry-run"):
        responses = dict(error_responses)
        responses["409"] = response("Execution conflict", {"error": {"const": "execution_conflict", "type": "string"}})
        responses["200"] = response("Dry-run receipt outcome", {
            "operation_id": operation_parameter["schema"],
            "receipt_status": {"type": "string", "enum": ["succeeded", "reconciliation_required"]},
            "execution_mode": {"const": "dry-run", "type": "string"},
        })
        document["paths"]["/v1/release/promotions/{operation_id}/" + route] = {
            "parameters": [operation_parameter],
            "post": {"security": [{"bearerAuth": []}], "responses": responses,
                     "requestBody": {"required": True, "content": {"application/json": {"schema": {
                         "type": "object", "required": ["schema_version"],
                         "properties": {"schema_version": {"type": "integer", "const": 1}},
                         "additionalProperties": False,
                     }}}}},
        }
    document["paths"]["/v1/release/promotions/{operation_id}/status"] = {
        "parameters": [operation_parameter],
        "get": {"security": [{"bearerAuth": []}], "responses": {
            "200": response("Read-only operation snapshot; not receipt state or permission to execute", {
                "schema_version": {"type": "integer", "const": 1},
                "operation_id": operation_parameter["schema"],
                "operation_status": {"type": "string", "enum": [status.value for status in ExternalActionStatus]},
                "execution_mode": {"type": "string", "const": "dry-run"},
            }), **{code: error_responses[code] for code in ("400", "401", "403", "404", "405", "500")},
        }},
    }
    return document
