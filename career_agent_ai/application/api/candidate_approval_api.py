"""Authenticated candidate approval API with a dependency-free WSGI adapter."""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable, Mapping, Protocol

from career_agent_ai.application.career import (
    CandidateApprovalConflictError,
    CandidateApprovalSubmission,
    CandidateApprovalUnavailableError,
    CareerLoopRequest,
    CareerLoopConflictError,
    HumanActionRequiredError,
)
from career_agent_ai.application.career.autonomous_loop_models import (
    validate_loop_identifier,
)


class CandidateApprovalLoop(Protocol):
    def start(self, request: CareerLoopRequest, *, run_id: str | None = None): ...
    def get_candidate_approval_prompt(self, run_id: str, *, user_id: str): ...
    def resume_candidate_submission(self, *, user_id: str, submission): ...
    def continue_run(self, run_id: str, *, user_id: str): ...


class CandidateApprovalWSGIApp:
    """Authenticate a candidate before reading or changing durable loop state."""

    # Accommodate every schema-valid string even when JSON escaping expands a
    # Unicode/control character to six ASCII bytes, while retaining a hard cap.
    MAX_BODY_BYTES = 100_000

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
        is_start = parts == ["v1", "candidate", "runs"]
        is_existing_run = (
            len(parts) == 5
            and parts[:3] == ["v1", "candidate", "runs"]
            and parts[4] in {"approval", "continue"}
        )
        if not is_start and not is_existing_run:
            return self._respond(start_response, "404 Not Found", {"error": "not_found"})
        authorization = str(environ.get("HTTP_AUTHORIZATION", ""))
        scheme, separator, credentials = authorization.partition(" ")
        token = credentials.strip() if separator and scheme.casefold() == "bearer" else ""
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        try:
            user_id = self._resolve_bearer(token) if token else None
        except Exception:
            return self._respond(
                start_response,
                "500 Internal Server Error",
                {"error": "internal_error"},
            )
        if not user_id:
            return self._respond(
                start_response,
                "401 Unauthorized",
                {"error": "unauthorized"},
                [("WWW-Authenticate", "Bearer")],
            )
        if is_existing_run:
            try:
                run_id = validate_loop_identifier(parts[3], "run_id", maximum=120)
            except (TypeError, ValueError) as exc:
                return self._respond(
                    start_response,
                    "400 Bad Request",
                    {"error": "invalid_request", "message": str(exc)[:500]},
                )
        try:
            if is_start:
                if method != "POST":
                    return self._respond(
                        start_response,
                        "405 Method Not Allowed",
                        {"error": "method_not_allowed"},
                        [("Allow", "POST")],
                    )
                media_type = str(environ.get("CONTENT_TYPE", "")).partition(";")[0]
                if media_type.strip().casefold() != "application/json":
                    return self._respond(
                        start_response,
                        "415 Unsupported Media Type",
                        {"error": "unsupported_media_type"},
                    )
                try:
                    payload = self._read_json(environ)
                    request, run_id = self._start_request(payload, user_id=user_id)
                except (TypeError, ValueError) as exc:
                    return self._respond(
                        start_response,
                        "400 Bad Request",
                        {"error": "invalid_request", "message": str(exc)[:500]},
                    )
                try:
                    result = self._loop.start(request, run_id=run_id)
                except CareerLoopConflictError:
                    return self._respond(
                        start_response,
                        "409 Conflict",
                        {"error": "run_conflict"},
                    )
                return self._respond(
                    start_response,
                    "201 Created",
                    {"run_id": result.run_id, "phase": result.phase.value},
                )
            if parts[4] == "continue":
                if method != "POST":
                    return self._respond(
                        start_response,
                        "405 Method Not Allowed",
                        {"error": "method_not_allowed"},
                        [("Allow", "POST")],
                    )
                try:
                    result = self._loop.continue_run(run_id, user_id=user_id)
                except HumanActionRequiredError:
                    return self._respond(
                        start_response,
                        "409 Conflict",
                        {"error": "recovery_unavailable"},
                    )
                return self._respond(
                    start_response,
                    "200 OK",
                    {"run_id": result.run_id, "phase": result.phase.value},
                )
            if method == "GET":
                return self._respond(
                    start_response,
                    "200 OK",
                    self._loop.get_candidate_approval_prompt(run_id, user_id=user_id).to_dict(),
                )
            if method == "POST":
                media_type = str(environ.get("CONTENT_TYPE", "")).partition(";")[0]
                if media_type.strip().casefold() != "application/json":
                    return self._respond(
                        start_response,
                        "415 Unsupported Media Type",
                        {"error": "unsupported_media_type"},
                    )
                try:
                    payload = self._read_json(environ)
                    submission = CandidateApprovalSubmission.from_mapping(payload)
                    if submission.run_id != run_id:
                        raise ValueError(
                            "approval payload run_id does not match the URL."
                        )
                except (TypeError, ValueError) as exc:
                    return self._respond(
                        start_response,
                        "400 Bad Request",
                        {"error": "invalid_request", "message": str(exc)[:500]},
                    )
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
        except (CandidateApprovalConflictError, CareerLoopConflictError):
            return self._respond(
                start_response,
                "409 Conflict",
                {"error": "approval_conflict"},
            )
        except CandidateApprovalUnavailableError:
            return self._respond(
                start_response,
                "409 Conflict",
                {"error": "approval_unavailable"},
            )
        except Exception:
            return self._respond(
                start_response,
                "500 Internal Server Error",
                {"error": "internal_error"},
            )

    @staticmethod
    def _start_request(
        payload: Mapping[str, Any], *, user_id: str
    ) -> tuple[CareerLoopRequest, str]:
        """Build a sandbox run request bound to the authenticated candidate."""
        allowed = {"schema_version", "run_id", "keyword", "candidate_profile", "location"}
        required = {"schema_version", "run_id", "keyword", "candidate_profile"}
        unknown = set(payload) - allowed
        missing = required - set(payload)
        if unknown:
            raise ValueError("start body contains unknown fields.")
        if missing:
            raise ValueError("start body is missing required fields.")
        if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
            raise ValueError("schema_version must equal 1.")
        raw_run_id = payload["run_id"]
        run_id = validate_loop_identifier(raw_run_id, "run_id", maximum=120)
        if run_id != raw_run_id:
            raise ValueError("run_id must match the published identifier pattern.")
        if (
            not isinstance(payload["candidate_profile"], str)
            or len(payload["candidate_profile"]) > 12_000
        ):
            raise ValueError("candidate_profile must not exceed 12000 characters.")
        request = CareerLoopRequest(
            user_id=user_id,
            keyword=payload["keyword"],
            candidate_profile=payload["candidate_profile"],
            location=payload.get("location", ""),
        )
        return request, run_id

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
            payload = json.loads(
                body.decode("utf-8"), object_pairs_hook=self._unique_json_object
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("approval body must be valid UTF-8 JSON.") from exc
        if not isinstance(payload, Mapping):
            raise ValueError("approval body must be a JSON object.")
        return payload

    @staticmethod
    def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        """Reject ambiguous JSON members, including escaped equivalent names."""
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("approval body contains duplicate JSON members.")
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
            "action_fingerprint": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
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
            "action_fingerprint": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "decision": {"type": "string", "enum": ["approve", "decline"]},
        },
        "additionalProperties": False,
    }
    start_schema = {
        "type": "object",
        "required": [
            "schema_version", "run_id", "keyword", "candidate_profile",
        ],
        "properties": {
            "schema_version": {"type": "integer", "const": 1},
            "run_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 120,
                "pattern": "^[A-Za-z0-9][A-Za-z0-9._:@+-]*$",
            },
            "keyword": {"type": "string", "minLength": 1, "maxLength": 500},
            "candidate_profile": {
                "type": "string", "minLength": 1, "maxLength": 12_000,
            },
            "location": {"type": "string", "maxLength": 500},
        },
        "additionalProperties": False,
    }
    recovery_conflict_schema = {
        "oneOf": [
            {
                "type": "object",
                "required": ["error"],
                "properties": {"error": {"const": "recovery_unavailable"}},
                "additionalProperties": False,
            },
            {
                "type": "object",
                "required": ["error"],
                "properties": {"error": {"const": "approval_conflict"}},
                "additionalProperties": False,
            },
        ]
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
            "/v1/candidate/runs": {
                "post": {
                    "operationId": "startCandidateRun",
                    "security": [{"bearerAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": content(start_schema),
                    },
                    "responses": {
                        "201": {
                            "description": "Sandbox candidate run started",
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
                            "description": "Run identifier already exists",
                            "content": content({
                                "type": "object",
                                "required": ["error"],
                                "properties": {"error": {"const": "run_conflict"}},
                                "additionalProperties": False,
                            }),
                        },
                        "415": {
                            "description": "Request body is not application/json",
                            "content": content(error_schema),
                        },
                    },
                },
            },
            "/v1/candidate/runs/{run_id}/approval": {
                "parameters": [{
                    "name": "run_id", "in": "path", "required": True,
                    "schema": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 120,
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9._:@+-]*$",
                    },
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
                        "409": {
                            "description": "Run has no ordinary approval prompt",
                            "content": content(error_schema),
                        },
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
                        "415": {
                            "description": "Request body is not application/json",
                            "content": content(error_schema),
                        },
                        "409": {
                            "description": "Approval conflicts with durable state",
                            "content": content(error_schema),
                        },
                    },
                },
            },
            "/v1/candidate/runs/{run_id}/continue": {
                "parameters": [{
                    "name": "run_id", "in": "path", "required": True,
                    "schema": {
                        "type": "string", "minLength": 1, "maxLength": 120,
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9._:@+-]*$",
                    },
                }],
                "post": {
                    "operationId": "continueCandidateRun",
                    "security": [{"bearerAuth": []}],
                    "responses": {
                        "200": {
                            "description": "Persisted non-human phase continued",
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
                            "description": (
                                "Run is waiting for human action or another worker "
                                "holds its execution lease"
                            ),
                            "content": content(recovery_conflict_schema),
                        },
                    },
                },
            },
        },
    }
