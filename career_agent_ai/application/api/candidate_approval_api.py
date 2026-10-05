"""Authenticated candidate approval API with a dependency-free WSGI adapter."""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable, Mapping, Protocol

from career_agent_ai.application.career import (
    CandidateApprovalSubmission,
    CareerLoopConflictError,
)


class CandidateApprovalLoop(Protocol):
    def get_candidate_approval_prompt(self, run_id: str, *, user_id: str): ...
    def resume_candidate_submission(self, *, user_id: str, submission): ...


class CandidateApprovalWSGIApp:
    """Authenticate a candidate before reading or changing durable loop state."""

    MAX_BODY_BYTES = 16_384

    def __init__(
        self,
        loop: CandidateApprovalLoop,
        *,
        resolve_bearer: Callable[[str], str | None],
    ) -> None:
        if not callable(resolve_bearer):
            raise TypeError("resolve_bearer must be callable.")
        self._loop = loop
        self._resolve_bearer = resolve_bearer

    def __call__(self, environ: Mapping[str, Any], start_response) -> Iterable[bytes]:
        path = str(environ.get("PATH_INFO", ""))
        parts = path.strip("/").split("/")
        if len(parts) != 5 or parts[:3] != ["v1", "candidate", "runs"] or parts[4] != "approval":
            return self._respond(start_response, "404 Not Found", {"error": "not_found"})
        authorization = str(environ.get("HTTP_AUTHORIZATION", ""))
        token = authorization[7:] if authorization.startswith("Bearer ") else ""
        user_id = self._resolve_bearer(token) if token else None
        if not user_id:
            return self._respond(
                start_response,
                "401 Unauthorized",
                {"error": "unauthorized"},
                [("WWW-Authenticate", "Bearer")],
            )
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        run_id = parts[3]
        try:
            if method == "GET":
                return self._respond(
                    start_response,
                    "200 OK",
                    self._loop.get_candidate_approval_prompt(run_id, user_id=user_id).to_dict(),
                )
            if method == "POST":
                payload = self._read_json(environ)
                submission = CandidateApprovalSubmission.from_mapping(payload)
                if submission.run_id != run_id:
                    raise ValueError("approval payload run_id does not match the URL.")
                result = self._loop.resume_candidate_submission(
                    user_id=user_id, submission=submission
                )
                return self._respond(
                    start_response,
                    "200 OK",
                    {"run_id": result.run_id, "phase": result.phase.value},
                )
            return self._respond(
                start_response,
                "405 Method Not Allowed",
                {"error": "method_not_allowed"},
                [("Allow", "GET, POST")],
            )
        except (KeyError, PermissionError):
            return self._respond(start_response, "404 Not Found", {"error": "not_found"})
        except (TypeError, ValueError) as exc:
            return self._respond(
                start_response,
                "400 Bad Request",
                {"error": "invalid_request", "message": str(exc)[:500]},
            )
        except CareerLoopConflictError:
            return self._respond(
                start_response,
                "409 Conflict",
                {"error": "approval_conflict"},
            )
        except Exception:
            return self._respond(
                start_response,
                "500 Internal Server Error",
                {"error": "internal_error"},
            )

    def _read_json(self, environ: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            length = int(str(environ.get("CONTENT_LENGTH", "0")))
        except ValueError as exc:
            raise ValueError("Content-Length must be an integer.") from exc
        if length < 1 or length > self.MAX_BODY_BYTES:
            raise ValueError("approval body size is invalid.")
        body = environ["wsgi.input"].read(length)
        if len(body) != length:
            raise ValueError("approval body is incomplete.")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("approval body must be valid UTF-8 JSON.") from exc
        if not isinstance(payload, Mapping):
            raise ValueError("approval body must be a JSON object.")
        return payload

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


def candidate_approval_openapi_document() -> dict[str, Any]:
    """Return the OpenAPI 3.1 contract for the candidate approval boundary."""
    error_schema = {
        "type": "object",
        "required": ["error"],
        "properties": {"error": {"type": "string"}},
        "additionalProperties": True,
    }
    prompt_schema = {
        "type": "object",
        "required": [
            "schema_version", "run_id", "state_version", "action_kind", "title",
            "details", "action_fingerprint", "allowed_decisions",
        ],
        "properties": {
            "schema_version": {"type": "integer", "const": 1},
            "run_id": {"type": "string", "minLength": 1},
            "state_version": {"type": "integer", "minimum": 0},
            "action_kind": {"type": "string"},
            "title": {"type": "string"},
            "details": {"type": "object", "additionalProperties": True},
            "action_fingerprint": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
            "allowed_decisions": {
                "type": "array",
                "items": {"type": "string", "enum": ["approve", "decline"]},
                "uniqueItems": True,
            },
        },
        "additionalProperties": False,
    }
    submission_schema = {
        "type": "object",
        "required": [
            "schema_version", "run_id", "state_version", "action_fingerprint", "decision",
        ],
        "properties": {
            "schema_version": {"type": "integer", "const": 1},
            "run_id": {"type": "string", "minLength": 1},
            "state_version": {"type": "integer", "minimum": 0},
            "action_fingerprint": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
            "decision": {"type": "string", "enum": ["approve", "decline"]},
        },
        "additionalProperties": False,
    }

    def content(schema: Mapping[str, Any]) -> dict[str, Any]:
        return {"application/json": {"schema": schema}}

    common_responses = {
        "400": {"description": "Malformed approval request", "content": content(error_schema)},
        "401": {"description": "Missing or invalid bearer token", "content": content(error_schema)},
        "404": {
            "description": "Run is missing or not owned by the candidate",
            "content": content(error_schema),
        },
        "500": {
            "description": "Sanitized internal service failure",
            "content": content(error_schema),
        },
    }
    return {
        "openapi": "3.1.0",
        "info": {"title": "CareerAgentAI Candidate Approval API", "version": "1.0.0"},
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}
        },
        "paths": {
            "/v1/candidate/runs/{run_id}/approval": {
                "parameters": [{
                    "name": "run_id", "in": "path", "required": True,
                    "schema": {"type": "string", "minLength": 1},
                }],
                "get": {
                    "operationId": "getCandidateApprovalPrompt",
                    "security": [{"bearerAuth": []}],
                    "responses": {
                        "200": {
                            "description": "Pending candidate approval prompt",
                            "content": content(prompt_schema),
                        },
                        **common_responses,
                    },
                },
                "post": {
                    "operationId": "submitCandidateApproval",
                    "security": [{"bearerAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": content(submission_schema),
                    },
                    "responses": {
                        "200": {
                            "description": "Approval accepted and loop resumed",
                            "content": content({
                                "type": "object",
                                "required": ["run_id", "phase"],
                                "properties": {
                                    "run_id": {"type": "string"},
                                    "phase": {"type": "string"},
                                },
                                "additionalProperties": False,
                            }),
                        },
                        **common_responses,
                        "409": {
                            "description": "Approval conflicts with durable state",
                            "content": content(error_schema),
                        },
                    },
                },
            }
        },
    }
