"""Authenticated candidate approval API with a dependency-free WSGI adapter."""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl
from hashlib import sha256, sha512
from decimal import Decimal, InvalidOperation
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
    CareerLoopResult,
    CareerLoopPhase,
    validate_loop_identifier,
)
from .candidate_sandbox_ui import candidate_sandbox_html
from career_agent_ai.application.scheduling.models import validate_schedule_identifier


_STRICT_START_TEXT_PATTERN = (
    r"^(?![\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680"
    r"\u2000-\u200A\u2028\u2029\u202F\u205F\u3000\uFEFF])"
    r"(?![\s\S]*[\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680"
    r"\u2000-\u200A\u2028\u2029\u202F\u205F\u3000\uFEFF]$)"
    r"(?![\s\S]*[\u0000-\u0008\u000B-\u001F\uD800-\uDFFF])"
    r"[\s\S]+$"
)


class CandidateApprovalLoop(Protocol):
    def start(self, request: CareerLoopRequest, *, run_id: str | None = None): ...
    def replay_existing_start(
        self, request: CareerLoopRequest, *, run_id: str, user_id: str
    ): ...
    def get_candidate_approval_prompt(self, run_id: str, *, user_id: str): ...
    def get_candidate_run_status(self, run_id: str, *, user_id: str) -> dict[str, Any]: ...
    def list_run_statuses(self, *, user_id: str, limit: int = 25, after: str | None = None) -> dict[str, Any]: ...

    def list_applications(self, *, user_id: str, limit: int = 25, after: str | None = None) -> dict[str, Any]: ...
    def get_application(self, application_id: str, *, user_id: str) -> dict[str, Any]: ...
    def get_application_history(self, application_id: str, *, user_id: str) -> dict[str, Any]: ...
    def get_schedule_event(self, event_id: str, *, user_id: str) -> dict[str, Any]: ...
    def list_schedule_events(self, *, user_id: str, limit: int = 25, after: str | None = None) -> dict[str, Any]: ...
    def resume_candidate_submission(self, *, user_id: str, submission): ...
    def continue_run(self, run_id: str, *, user_id: str): ...


def start_owner_scoped_run(
    loop: CandidateApprovalLoop,
    request: CareerLoopRequest,
    *,
    client_run_id: str,
) -> CareerLoopResult:
    """Start or replay a run through the shared owner-scoped ID transition."""
    legacy_ids = (
        client_run_id,
        CandidateApprovalWSGIApp._legacy_owner_scoped_run_id(
            request.user_id, client_run_id
        ),
    )
    for legacy_run_id in legacy_ids:
        try:
            return loop.replay_existing_start(
                request, run_id=legacy_run_id, user_id=request.user_id
            )
        except (KeyError, PermissionError):
            pass
    durable_run_id = CandidateApprovalWSGIApp._owner_scoped_run_id(
        request.user_id, client_run_id
    )
    return loop.start(request, run_id=durable_run_id)


class CandidateApprovalWSGIApp:
    """Authenticate a candidate before reading or changing durable loop state."""

    # Accommodate every schema-valid string even when an astral code point is
    # encoded as two six-byte JSON surrogate escapes, while retaining a hard cap.
    MAX_BODY_BYTES = 180_000

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
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        if path == "/sandbox/candidate":
            if method != "GET":
                return self._respond(
                    start_response,
                    "405 Method Not Allowed",
                    {"error": "method_not_allowed"},
                    [("Allow", "GET")],
                )
            return self._respond_html(start_response, candidate_sandbox_html())
        parts = path.strip("/").split("/")
        is_start = parts == ["v1", "candidate", "runs"]
        is_applications = parts == ["v1", "candidate", "applications"]
        is_history = parts == ["v1", "candidate", "application", "history"]
        is_application = parts == ["v1", "candidate", "application"] or is_history
        is_schedule = parts == ["v1", "candidate", "schedule-event"]
        is_schedule_list = parts == ["v1", "candidate", "schedule-events"]
        is_list = parts == ["v1", "candidate", "run-statuses"] or is_applications or is_schedule_list
        is_existing_run = (
            len(parts) == 5
            and parts[:3] == ["v1", "candidate", "runs"]
            and parts[4] in {"approval", "continue", "status"}
        )
        if not is_start and not is_existing_run and not is_list and not is_application and not is_schedule:
            return self._respond(start_response, "404 Not Found", {"error": "not_found"})
        authorization = str(environ.get("HTTP_AUTHORIZATION", ""))
        scheme, separator, credentials = authorization.partition(" ")
        token = credentials.strip() if separator and scheme.casefold() == "bearer" else ""
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
                run_id = validate_loop_identifier(parts[3], "run_id", maximum=200)
            except (TypeError, ValueError) as exc:
                return self._respond(
                    start_response,
                    "400 Bad Request",
                    {"error": "invalid_request", "message": str(exc)[:500]},
                )
        try:
            if is_application or is_schedule:
                if method != "GET":
                    return self._respond(start_response, "405 Method Not Allowed", {"error": "method_not_allowed"}, [("Allow", "GET")])
                try:
                    query = str(environ.get("QUERY_STRING", ""))
                    if len(query) > 2500 or re.search(r"%(?![0-9A-Fa-f]{2})", query):
                        raise ValueError("query too long")
                    pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True, max_num_fields=1, errors="strict")
                    parameter = "event_id" if is_schedule else "application_id"
                    if len(pairs) != 1 or pairs[0][0] != parameter:
                        raise ValueError("identity is required")
                    identifier = pairs[0][1]
                    if not identifier or len(identifier) > 200 or identifier != identifier.strip():
                        raise ValueError("invalid application_id")
                    identifier.encode("utf-8", errors="strict")
                    if is_schedule and validate_schedule_identifier(identifier, "event_id") != identifier:
                        raise ValueError("invalid event_id")
                except (TypeError, ValueError):
                    return self._respond(start_response, "400 Bad Request", {"error": "invalid_request"})
                if is_schedule:
                    projection = self._loop.get_schedule_event
                else:
                    projection = self._loop.get_application_history if is_history else self._loop.get_application
                return self._respond(start_response, "200 OK", projection(identifier, user_id=user_id))
            if is_list:
                if method != "GET":
                    return self._respond(start_response, "405 Method Not Allowed", {"error": "method_not_allowed"}, [("Allow", "GET")])
                try:
                    query = str(environ.get("QUERY_STRING", ""))
                    # 200 scalars * 4 UTF-8 bytes * 3 escape characters + overhead.
                    if len(query) > (2500 if is_applications else 1000):
                        raise ValueError("query too long")
                    if is_schedule_list and re.search(r"%(?![0-9A-Fa-f]{2})", query):
                        raise ValueError("invalid escape")
                    pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True, max_num_fields=2, errors="strict")
                    values = dict(pairs)
                    if len(values) != len(pairs) or set(values) - {"limit", "after"}:
                        raise ValueError("unknown or duplicate query parameter")
                    raw_limit = values.get("limit", "25")
                    if not raw_limit.isascii() or not raw_limit.isdecimal() or len(raw_limit) > 3:
                        raise ValueError("invalid limit")
                    limit = int(raw_limit)
                    if not 1 <= limit <= 100:
                        raise ValueError("invalid limit")
                    after = values.get("after")
                    if after is not None:
                        if is_applications:
                            if not after or len(after) > 200 or after != after.strip():
                                raise ValueError("invalid cursor")
                            after.encode("utf-8", errors="strict")
                        elif is_schedule_list:
                            if validate_schedule_identifier(after, "after") != after:
                                raise ValueError("invalid cursor")
                        elif validate_loop_identifier(after, "after") != after:
                            raise ValueError("invalid cursor")
                except (TypeError, ValueError):
                    return self._respond(start_response, "400 Bad Request", {"error": "invalid_request"})
                if is_schedule_list:
                    collection = self._loop.list_schedule_events
                else:
                    collection = self._loop.list_applications if is_applications else self._loop.list_run_statuses
                return self._respond(start_response, "200 OK", collection(user_id=user_id, limit=limit, after=after))
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
                    request, _ = self._start_request(payload, user_id=user_id)
                except (TypeError, ValueError, InvalidOperation) as exc:
                    return self._respond(
                        start_response,
                        "400 Bad Request",
                        {"error": "invalid_request", "message": str(exc)[:500]},
                    )
                try:
                    result = start_owner_scoped_run(
                        self._loop, request, client_run_id=payload["run_id"]
                    )
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
            if parts[4] == "status":
                if method != "GET":
                    return self._respond(
                        start_response, "405 Method Not Allowed",
                        {"error": "method_not_allowed"}, [("Allow", "GET")],
                    )
                return self._respond(
                    start_response, "200 OK",
                    self._loop.get_candidate_run_status(run_id, user_id=user_id),
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
                except (TypeError, ValueError, InvalidOperation) as exc:
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
        schema_version = payload["schema_version"]
        if type(schema_version) not in {int, float, Decimal} or schema_version != 1:
            raise ValueError("schema_version must equal 1.")
        raw_run_id = payload["run_id"]
        run_id = validate_loop_identifier(raw_run_id, "run_id", maximum=120)
        if run_id != raw_run_id:
            raise ValueError("run_id must match the published identifier pattern.")
        keyword = CandidateApprovalWSGIApp._strict_start_text(
            payload["keyword"], "keyword", maximum=500
        )
        candidate_profile = CandidateApprovalWSGIApp._strict_start_text(
            payload["candidate_profile"], "candidate_profile", maximum=12_000
        )
        location = CandidateApprovalWSGIApp._strict_start_text(
            payload.get("location", ""), "location", maximum=500, allow_empty=True
        )
        request = CareerLoopRequest(
            user_id=user_id,
            keyword=keyword,
            candidate_profile=candidate_profile,
            location=location,
        )
        return request, CandidateApprovalWSGIApp._owner_scoped_run_id(
            request.user_id, run_id
        )

    @staticmethod
    def _owner_scoped_run_id(user_id: str, client_run_id: str) -> str:
        """Derive a generation-namespaced key outside the legacy input space."""
        material = f"{len(user_id)}:{user_id}{len(client_run_id)}:{client_run_id}"
        return f"scoped-v2:{sha512(material.encode('utf-8')).hexdigest()}"

    @staticmethod
    def _legacy_owner_scoped_run_id(user_id: str, client_run_id: str) -> str:
        """Derive the transitional SHA-256 key for compatible replay only."""
        material = f"{len(user_id)}:{user_id}{len(client_run_id)}:{client_run_id}"
        return sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _strict_start_text(
        value: Any, field: str, *, maximum: int, allow_empty: bool = False
    ) -> str:
        """Validate raw text exactly as published, without hidden normalization."""
        if not isinstance(value, str) or len(value) > maximum:
            raise ValueError(f"{field} must not exceed {maximum} characters.")
        if value == "" and allow_empty:
            return value
        if not value or value[0].isspace() or value[-1].isspace() or "\ufeff" in {
            value[0], value[-1]
        }:
            raise ValueError(f"{field} must be non-empty without outer whitespace.")
        if any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
            raise ValueError(f"{field} contains forbidden control characters.")
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
            raise ValueError(f"{field} contains a forbidden Unicode surrogate.")
        return value

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
                body.decode("utf-8"),
                object_pairs_hook=self._unique_json_object,
                parse_float=Decimal,
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

    @staticmethod
    def _respond_html(start_response, body: bytes):
        headers = [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
            (
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; connect-src 'self'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            ),
        ]
        start_response("200 OK", headers)
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
            "keyword": {
                "type": "string", "minLength": 1, "maxLength": 500,
                "pattern": _STRICT_START_TEXT_PATTERN,
            },
            "candidate_profile": {
                "type": "string", "minLength": 1, "maxLength": 12_000,
                "pattern": _STRICT_START_TEXT_PATTERN,
            },
            "location": {
                "type": "string", "maxLength": 500,
                "pattern": f"^(?:|{_STRICT_START_TEXT_PATTERN[1:-1]})$",
            },
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

    health_schema = {
        "type": "object",
        "required": ["status"],
        "properties": {"status": {"const": "ok"}},
        "additionalProperties": False,
    }
    unavailable_schema = {
        "type": "object",
        "required": ["status"],
        "properties": {"status": {"const": "unavailable"}},
        "additionalProperties": False,
    }
    probe_method_response = {"description": "Only GET is allowed"}

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
    document = {
        "openapi": "3.1.0",
        "info": {"title": "CareerAgentAI Candidate Approval API", "version": "1.0.0"},
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}
        },
        "paths": {
            "/healthz": {
                "get": {
                    "operationId": "getCandidateServiceHealth",
                    "security": [],
                    "responses": {
                        "200": {
                            "description": "Candidate service is alive",
                            "content": content(health_schema),
                        },
                        "405": probe_method_response,
                    },
                },
            },
            "/readyz": {
                "get": {
                    "operationId": "getCandidateServiceReadiness",
                    "security": [],
                    "responses": {
                        "200": {
                            "description": "Candidate service storage is ready",
                            "content": content(health_schema),
                        },
                        "503": {
                            "description": "Candidate service storage is unavailable",
                            "content": content(unavailable_schema),
                        },
                        "405": probe_method_response,
                    },
                },
            },
            "/sandbox/candidate": {
                "get": {
                    "operationId": "getCandidateSandbox",
                    "security": [],
                    "responses": {
                        "200": {
                            "description": "Static candidate sandbox interface",
                            "content": {
                                "text/html": {"schema": {"type": "string"}}
                            },
                        },
                        "405": {
                            "description": "Only GET is allowed",
                            "content": content(error_schema),
                        },
                    },
                },
            },
            "/v1/candidate/applications": {
                "get": {
                    "operationId": "listCandidateApplications",
                    "security": [{"bearerAuth": []}],
                    "parameters": [
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25}},
                        {"name": "after", "in": "query", "description": "Exclusive application-ID cursor, not an ownership credential", "schema": {"type": "string", "minLength": 1, "maxLength": 200}},
                    ],
                    "responses": {
                        "200": {"description": "Read-only owner-only application snapshots; unknown/duplicate query parameters rejected",
                            "headers": {"Cache-Control": {"schema": {"const": "no-store"}}},
                            "content": content({"type": "object", "additionalProperties": False,
                                "required": ["schema_version", "items", "next_after"], "properties": {
                                    "schema_version": {"const": 1},
                                    "next_after": {"type": ["string", "null"]},
                                    "items": {"type": "array", "maxItems": 100, "items": {
                                        "type": "object", "additionalProperties": False,
                                        "required": ["application_id", "job_id", "company_id", "job_title", "status", "state_version", "created_at", "updated_at"],
                                        "properties": {
                                            "application_id": {"type": "string", "minLength": 1, "maxLength": 200},
                                            "job_id": {"type": "string", "minLength": 1, "maxLength": 200},
                                            "company_id": {"type": "string", "maxLength": 200},
                                            "job_title": {"type": "string", "maxLength": 500},
                                            "status": {"enum": ["saved", "applied", "interview", "offer", "rejected", "withdrawn"]},
                                            "state_version": {"type": "integer", "minimum": 1},
                                            "created_at": {"type": "string", "format": "date-time"},
                                            "updated_at": {"type": "string", "format": "date-time"},
                                        }}},
                                }})},
                        **common_responses,
                        "405": {"description": "Only GET is allowed", "headers": {"Allow": {"schema": {"const": "GET"}}}, "content": content(error_schema)},
                    },
                },
            },
            "/v1/candidate/run-statuses": {
                "get": {
                    "operationId": "listCandidateRunStatuses",
                    "security": [{"bearerAuth": []}],
                    "parameters": [
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25}},
                        {"name": "after", "in": "query", "description": "Exclusive run-ID keyset cursor; never an ownership credential", "schema": {"type": "string", "minLength": 1, "maxLength": 200}},
                    ],
                    "responses": {
                        "200": {"description": "Owner-only minimal status page; unknown/duplicate query parameters rejected",
                            "headers": {"Cache-Control": {"schema": {"const": "no-store"}}},
                            "content": content({"type": "object", "additionalProperties": False,
                                "required": ["schema_version", "items", "next_after"], "properties": {
                                    "schema_version": {"const": 1},
                                    "items": {"type": "array", "maxItems": 100, "items": {"$ref": "#/paths/~1v1~1candidate~1runs~1{run_id}~1status/get/responses/200/content/application~1json/schema"}},
                                    "next_after": {"type": ["string", "null"]},
                                }})},
                        **common_responses,
                        "405": {"description": "Only GET is allowed", "headers": {"Allow": {"schema": {"const": "GET"}}}, "content": content(error_schema)},
                    },
                },
            },
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
            "/v1/candidate/runs/{run_id}/status": {
                "parameters": [{
                    "name": "run_id", "in": "path", "required": True,
                    "schema": {
                        "type": "string", "minLength": 1, "maxLength": 200,
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9._:@+-]*$",
                    },
                }],
                "get": {
                    "operationId": "getCandidateRunStatus",
                    "security": [{"bearerAuth": []}],
                    "responses": {
                        "200": {
                            "description": "Read-only owner-scoped durable status snapshot",
                            "content": content({
                                "type": "object",
                                "required": ["schema_version", "run_id", "state_version",
                                             "phase", "waiting_for_human", "terminal"],
                                "properties": {
                                    "schema_version": {"type": "integer", "const": 1},
                                    "run_id": {"type": "string", "minLength": 1},
                                    "state_version": {"type": "integer", "minimum": 0},
                                    "phase": {"type": "string", "enum": [p.value for p in CareerLoopPhase]},
                                    "waiting_for_human": {"type": "boolean"},
                                    "terminal": {"type": "boolean"},
                                },
                                "additionalProperties": False,
                            }),
                        },
                        **common_responses,
                        "405": {
                            "description": "Only GET is allowed",
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
                        "maxLength": 200,
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
                        "type": "string", "minLength": 1, "maxLength": 200,
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
    application_schema = document["paths"]["/v1/candidate/applications"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["items"]["items"]
    document["paths"]["/v1/candidate/application"] = {"get": {
        "operationId": "getCandidateApplication", "security": [{"bearerAuth": []}],
        "parameters": [{"name": "application_id", "in": "query", "required": True,
                        "description": "Opaque application identity; no other or duplicate parameters accepted",
                        "schema": {"type": "string", "minLength": 1, "maxLength": 200}}],
        "responses": {
            "200": {"description": "Read-only owned application snapshot",
                    "headers": {"Cache-Control": {"schema": {"const": "no-store"}}},
                    "content": content({"type": "object", "additionalProperties": False,
                        "required": ["schema_version", "application"],
                        "properties": {"schema_version": {"const": 1}, "application": application_schema}})},
            **common_responses,
            "404": {"description": "Missing or foreign application", "content": content(error_schema)},
            "405": {"description": "Only GET is allowed", "headers": {"Allow": {"schema": {"const": "GET"}}}, "content": content(error_schema)},
        }}}
    detail = document["paths"]["/v1/candidate/application"]["get"]
    statuses = {"type": "string", "enum": ["saved", "applied", "interview", "offer", "rejected", "withdrawn"]}
    document["paths"]["/v1/candidate/application/history"] = {"get": {
        **detail, "operationId": "getCandidateApplicationHistory",
        "responses": {**detail["responses"], "200": {
            "description": "Bounded read-only owned lifecycle history; no notes or private metadata",
            "headers": {"Cache-Control": {"schema": {"const": "no-store"}}},
            "content": content({"type": "object", "additionalProperties": False,
                "required": ["schema_version", "application_id", "state_version", "items"],
                "properties": {"schema_version": {"const": 1},
                    "application_id": {"type": "string", "minLength": 1, "maxLength": 200},
                    "state_version": {"type": "integer", "minimum": 1},
                    "items": {"type": "array", "maxItems": 4, "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["sequence", "from_status", "to_status", "occurred_at"],
                        "properties": {"sequence": {"type": "integer", "minimum": 0},
                            "from_status": statuses, "to_status": statuses,
                            "occurred_at": {"type": "string", "format": "date-time"}}}}}})}}}}
    event_properties = {
        "event_id": {"type": "string", "minLength": 1, "maxLength": 200,
                     "pattern": "^[A-Za-z0-9][A-Za-z0-9._:@+-]*$"},
        "employer_name": {"type": "string"}, "location": {"type": "string"},
        "event_type": {"type": "string", "enum": ["interview", "trial_day", "phone_call", "video_call", "other"]},
        "status": {"type": "string", "enum": ["proposed", "accepted", "declined", "reschedule_requested", "cancelled"]},
        "state_version": {"type": "integer", "minimum": 1},
        "start_at": {"type": "string", "format": "date-time"},
        "end_at": {"type": ["string", "null"], "format": "date-time"},
        "local_date": {"type": "string", "format": "date"},
        "local_end_date": {"type": ["string", "null"], "format": "date"},
        "local_start_time": {"type": "string", "pattern": r"^\d{2}:\d{2}:\d{2}(\.\d{6})?$"},
        "local_end_time": {"type": ["string", "null"], "pattern": r"^\d{2}:\d{2}:\d{2}(\.\d{6})?$"},
        "timezone_name": {"type": "string"},
        "utc_offset": {"type": "string", "pattern": r"^[+-]\d{2}:\d{2}(:\d{2})?$"},
        "end_utc_offset": {"type": ["string", "null"], "pattern": r"^[+-]\d{2}:\d{2}(:\d{2})?$"},
    }
    document["paths"]["/v1/candidate/schedule-event"] = {"get": {
        "operationId": "getCandidateScheduleEvent", "security": [{"bearerAuth": []}],
        "parameters": [{"name": "event_id", "in": "query", "required": True,
                        "description": "Exact event identity; no duplicate or other parameters accepted",
                        "schema": event_properties["event_id"]}],
        "responses": {**detail["responses"],
            "404": {"description": "Missing or foreign event", "content": content(error_schema)},
            "200": {"description": "Read-only owned event with exact UTC and local times; no calendar actions",
                "headers": {"Cache-Control": {"schema": {"const": "no-store"}}},
                "content": content({"type": "object", "additionalProperties": False,
                    "required": ["schema_version", "event"], "properties": {
                        "schema_version": {"const": 1}, "event": {"type": "object",
                            "additionalProperties": False, "required": list(event_properties),
                            "properties": event_properties}}})}}}}
    schedule_detail = document["paths"]["/v1/candidate/schedule-event"]["get"]
    document["paths"]["/v1/candidate/schedule-events"] = {"get": {
        "operationId": "listCandidateScheduleEvents", "security": [{"bearerAuth": []}],
        "parameters": [
            {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25}},
            {"name": "after", "in": "query", "description": "Exclusive event-ID cursor, not an ownership credential",
             "schema": event_properties["event_id"]}],
        "responses": {**common_responses,
            "405": schedule_detail["responses"]["405"],
            "200": {"description": "Bounded read-only owned events in event-ID order; no calendar actions",
                "headers": {"Cache-Control": {"schema": {"const": "no-store"}}},
                "content": content({"type": "object", "additionalProperties": False,
                    "required": ["schema_version", "items", "next_after"], "properties": {
                        "schema_version": {"const": 1},
                        "items": {"type": "array", "maxItems": 100, "items": {
                            "type": "object", "additionalProperties": False,
                            "required": list(event_properties), "properties": event_properties}},
                        "next_after": {**event_properties["event_id"], "type": ["string", "null"]}}})}}}}
    return document
