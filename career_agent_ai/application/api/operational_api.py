"""Read-only production operational API with a dependency-free WSGI adapter."""

from __future__ import annotations

import hmac
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import parse_qs

from career_agent_ai.application.observability import OperationalProbe
from career_agent_ai.application.observability.action_cursor import decode_action_cursor


class OperationalApiService:
    """Expose JSON-safe health and durable-work observations."""

    def __init__(
        self,
        probe: OperationalProbe,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._probe = probe
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def health(self) -> dict[str, Any]:
        """Return a minimal liveness payload without exposing secrets."""
        return {"status": "ok"}

    def issues(self, *, stale_after_seconds: int) -> dict[str, Any]:
        """Return failed/ambiguous/stuck work as a JSON-safe document."""
        if (
            not isinstance(stale_after_seconds, int)
            or isinstance(stale_after_seconds, bool)
            or stale_after_seconds < 1
            or stale_after_seconds > 604_800
        ):
            raise ValueError("stale_after_seconds must be between 1 and 604800.")
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("operational API clock must return a timezone-aware datetime.")
        now = now.astimezone(timezone.utc)
        issues = self._probe.inspect(
            now=now,
            stale_after_seconds=stale_after_seconds,
        )
        return {
            "generated_at": now.isoformat(),
            "issues": [
                {
                    "issue_type": issue.issue_type,
                    "entity_id": issue.entity_id,
                    "severity": issue.severity.value,
                    "updated_at": issue.updated_at.astimezone(timezone.utc).isoformat(),
                    "details": dict(issue.details),
                }
                for issue in issues
            ],
        }


    def actions(
        self,
        *,
        limit: int = 25,
        after: str | None = None,
        candidate_run_ref: str | None = None,
    ) -> dict[str, Any]:
        """Return a bounded, read-only projection optionally scoped to one candidate run."""
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 25
        ):
            raise ValueError("limit must be between 1 and 25.")
        if candidate_run_ref is not None and (
            not isinstance(candidate_run_ref, str)
            or re.fullmatch(r"[a-f0-9]{24}", candidate_run_ref) is None
        ):
            raise ValueError(
                "candidate_run_ref must be a 24-character lowercase hexadecimal reference."
            )
        if after is not None:
            decode_action_cursor(after, candidate_run_ref=candidate_run_ref)
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("operational API clock must return a timezone-aware datetime.")
        projection = getattr(self._probe, "list_action_statuses", None)
        if not callable(projection):
            raise RuntimeError("Operational action projection is unavailable.")
        page = projection(
            limit=limit,
            after=after,
            candidate_run_ref=candidate_run_ref,
        )
        if (
            not isinstance(page, dict)
            or set(page) != {"items", "next_after"}
            or not isinstance(page["items"], list)
            or len(page["items"]) > limit
            or not (
                page["next_after"] is None
                or (
                    isinstance(page["next_after"], str)
                    and 0 < len(page["next_after"]) <= 1200
                )
            )
        ):
            raise RuntimeError("Operational action projection returned an invalid page.")
        if page["next_after"] is not None:
            try:
                decode_action_cursor(
                    page["next_after"],
                    candidate_run_ref=candidate_run_ref,
                )
            except ValueError as exc:
                raise RuntimeError(
                    "Operational action projection returned an invalid cursor."
                ) from exc
        return {
            "generated_at": now.astimezone(timezone.utc).isoformat(),
            "schema_version": 1,
            "items": page["items"],
            "next_after": page["next_after"],
        }


class OperationalWSGIApp:
    """Serve the read-only operational API using the standard WSGI contract."""

    def __init__(self, service: OperationalApiService, *, bearer_token: str) -> None:
        token_bytes = self.validate_bearer_token(bearer_token)
        self._service = service
        self._bearer_token = token_bytes

    @staticmethod
    def validate_bearer_token(bearer_token: str) -> bytes:
        """Validate and encode an operational API bearer token."""
        if not isinstance(bearer_token, str):
            raise TypeError("bearer_token must be text.")
        if len(bearer_token) < 32 or len(bearer_token) > 4096:
            raise ValueError("bearer_token must contain 32 to 4096 characters.")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in bearer_token):
            raise ValueError("bearer_token contains forbidden control characters.")
        try:
            token_bytes = bearer_token.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("bearer_token must contain ASCII characters only.") from exc
        return token_bytes

    def __call__(
        self,
        environ: Mapping[str, Any],
        start_response: Callable[[str, list[tuple[str, str]]], Any],
    ) -> Iterable[bytes]:
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = str(environ.get("PATH_INFO", ""))
        try:
            if method != "GET":
                return self._respond(
                    start_response,
                    "405 Method Not Allowed",
                    {"error": "method_not_allowed"},
                    extra_headers=[("Allow", "GET")],
                )
            if path == "/healthz":
                return self._respond(start_response, "200 OK", self._service.health())
            if path == "/v1/operational/issues":
                authorization = str(environ.get("HTTP_AUTHORIZATION", ""))
                scheme, separator, credentials = authorization.partition(" ")
                supplied = (
                    credentials
                    if separator and scheme.casefold() == "bearer"
                    else ""
                )
                try:
                    supplied_bytes = supplied.encode("ascii")
                except UnicodeEncodeError:
                    supplied_bytes = b""
                exact_match = hmac.compare_digest(supplied_bytes, self._bearer_token)
                token_length = len(self._bearer_token)
                prefix = supplied_bytes[:-token_length]
                suffix = supplied_bytes[-token_length:]
                spaced_match = hmac.compare_digest(suffix, self._bearer_token)
                spaced_match = spaced_match and bool(prefix) and not prefix.strip(b" ")
                if not (exact_match or spaced_match):
                    return self._respond(
                        start_response,
                        "401 Unauthorized",
                        {"error": "unauthorized"},
                        extra_headers=[("WWW-Authenticate", "Bearer")],
                    )
                query = parse_qs(
                    str(environ.get("QUERY_STRING", "")),
                    keep_blank_values=True,
                    max_num_fields=20,
                )
                values = query.get("stale_after_seconds", ["3600"])
                if len(values) != 1:
                    raise ValueError("stale_after_seconds must be supplied at most once.")
                try:
                    stale = int(values[0])
                except (TypeError, ValueError) as exc:
                    raise ValueError("stale_after_seconds must be an integer.") from exc
                if stale < 1 or stale > 604_800:
                    raise ValueError(
                        "stale_after_seconds must be between 1 and 604800."
                    )
                try:
                    payload = self._service.issues(stale_after_seconds=stale)
                    return self._respond(
                        start_response,
                        "200 OK",
                        payload,
                    )
                except Exception:
                    return self._respond(
                        start_response,
                        "500 Internal Server Error",
                        {"error": "internal_error"},
                    )
            if path == "/v1/operational/actions":
                authorization = str(environ.get("HTTP_AUTHORIZATION", ""))
                scheme, separator, credentials = authorization.partition(" ")
                supplied = (
                    credentials
                    if separator and scheme.casefold() == "bearer"
                    else ""
                )
                try:
                    supplied_bytes = supplied.encode("ascii")
                except UnicodeEncodeError:
                    supplied_bytes = b""
                exact_match = hmac.compare_digest(supplied_bytes, self._bearer_token)
                token_length = len(self._bearer_token)
                prefix = supplied_bytes[:-token_length]
                suffix = supplied_bytes[-token_length:]
                spaced_match = hmac.compare_digest(suffix, self._bearer_token)
                spaced_match = spaced_match and bool(prefix) and not prefix.strip(b" ")
                if not (exact_match or spaced_match):
                    return self._respond(
                        start_response,
                        "401 Unauthorized",
                        {"error": "unauthorized"},
                        extra_headers=[("WWW-Authenticate", "Bearer")],
                    )
                query = parse_qs(
                    str(environ.get("QUERY_STRING", "")),
                    keep_blank_values=True,
                    max_num_fields=4,
                    strict_parsing=True,
                    errors="strict",
                )
                if set(query) - {"limit", "after", "candidate_run_ref"} or any(
                    len(values) != 1 for values in query.values()
                ):
                    raise ValueError("unknown or duplicate operational action query parameter.")
                raw_limit = query.get("limit", ["25"])[0]
                if not raw_limit.isascii() or not raw_limit.isdecimal() or len(raw_limit) > 2:
                    raise ValueError("limit must be between 1 and 25.")
                limit = int(raw_limit)
                if not 1 <= limit <= 25:
                    raise ValueError("limit must be between 1 and 25.")
                after = query.get("after", [None])[0]
                if after is not None and (not after or len(after) > 1200):
                    raise ValueError("after must be a bounded opaque cursor.")
                candidate_run_ref = query.get("candidate_run_ref", [None])[0]
                if candidate_run_ref is not None and re.fullmatch(
                    r"[a-f0-9]{24}", candidate_run_ref
                ) is None:
                    raise ValueError(
                        "candidate_run_ref must be a 24-character lowercase hexadecimal reference."
                    )
                try:
                    payload = self._service.actions(
                        limit=limit,
                        after=after,
                        candidate_run_ref=candidate_run_ref,
                    )
                    return self._respond(start_response, "200 OK", payload)
                except ValueError:
                    raise
                except Exception:
                    return self._respond(
                        start_response,
                        "500 Internal Server Error",
                        {"error": "internal_error"},
                    )
            return self._respond(
                start_response,
                "404 Not Found",
                {"error": "not_found"},
            )
        except ValueError as exc:
            return self._respond(
                start_response,
                "400 Bad Request",
                {"error": "invalid_request", "message": str(exc)[:500]},
            )

    @staticmethod
    def _respond(
        start_response: Callable[[str, list[tuple[str, str]]], Any],
        status: str,
        payload: Mapping[str, Any],
        *,
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> tuple[bytes]:
        body = json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
        ]
        if extra_headers:
            headers.extend(extra_headers)
        start_response(status, headers)
        return (body,)


def openapi_document() -> dict[str, Any]:
    """Return the OpenAPI 3.1 contract for the read-only operational endpoints."""
    issue_schema = {
        "type": "object",
        "required": ["issue_type", "entity_id", "severity", "updated_at", "details"],
        "properties": {
            "issue_type": {"type": "string"},
            "entity_id": {"type": "string"},
            "severity": {"type": "string", "enum": ["warning", "error"]},
            "updated_at": {"type": "string", "format": "date-time"},
            "details": {"type": "object", "additionalProperties": True},
        },
        "additionalProperties": False,
    }
    action_item_schema = {
        "type": "object",
        "required": [
            "operation_ref", "candidate_run_ref", "action_family", "status",
            "created_at", "updated_at", "error_present",
        ],
        "properties": {
            "operation_ref": {"type": "string", "pattern": "^[a-f0-9]{24}$"},
            "candidate_run_ref": {
                "type": ["string", "null"],
                "pattern": "^[a-f0-9]{24}$",
            },
            "action_family": {
                "type": "string",
                "enum": ["application_submission", "communication", "calendar", "other"],
            },
            "status": {
                "type": "string",
                "enum": ["prepared", "in_progress", "failed", "reconciliation_required"],
            },
            "created_at": {"type": "string", "format": "date-time"},
            "updated_at": {"type": "string", "format": "date-time"},
            "error_present": {"type": "boolean"},
        },
        "additionalProperties": False,
    }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "CareerAgentAI Operational API",
            "version": "1.0.0",
        },
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                }
            }
        },
        "paths": {
            "/healthz": {
                "get": {
                    "operationId": "health",
                    "responses": {
                        "200": {
                            "description": "Process is alive",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["status"],
                                        "properties": {
                                            "status": {"type": "string", "const": "ok"}
                                        },
                                        "additionalProperties": False,
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/v1/operational/issues": {
                "get": {
                    "operationId": "listOperationalIssues",
                    "security": [{"bearerAuth": []}],
                    "parameters": [
                        {
                            "name": "stale_after_seconds",
                            "in": "query",
                            "required": False,
                            "schema": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 604800,
                                "default": 3600,
                            },
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Operational issues",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["generated_at", "issues"],
                                        "properties": {
                                            "generated_at": {
                                                "type": "string",
                                                "format": "date-time",
                                            },
                                            "issues": {
                                                "type": "array",
                                                "items": issue_schema,
                                            },
                                        },
                                        "additionalProperties": False,
                                    }
                                }
                            },
                        },
                        "400": {"description": "Invalid query input"},
                        "401": {"description": "Missing or invalid bearer token"},
                        "500": {
                            "description": "Sanitized internal operational error",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["error"],
                                        "properties": {
                                            "error": {
                                                "type": "string",
                                                "const": "internal_error",
                                            }
                                        },
                                        "additionalProperties": False,
                                    }
                                }
                            },
                        },
                    },
                }
            },
            "/v1/operational/actions": {
                "get": {
                    "operationId": "listOperationalActionStatuses",
                    "security": [{"bearerAuth": []}],
                    "parameters": [
                        {
                            "name": "limit",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "integer", "minimum": 1, "maximum": 25, "default": 25},
                        },
                        {
                            "name": "after",
                            "in": "query",
                            "required": False,
                            "description": "URL-safe encoded keyset cursor bound to the active candidate-run filter; not an authorization credential",
                            "schema": {"type": "string", "minLength": 1, "maxLength": 1200,
                                       "pattern": "^[A-Za-z0-9_-]+$"},
                        },
                        {
                            "name": "candidate_run_ref",
                            "in": "query",
                            "required": False,
                            "description": "Stable candidate-run reference from the owner-scoped candidate status response",
                            "schema": {"type": "string", "minLength": 24, "maxLength": 24,
                                       "pattern": "^[a-f0-9]{24}$"},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": (
                                "Bounded lifecycle metadata for non-terminal external actions; "
                                "payloads, results, errors and raw operation identifiers are omitted"
                            ),
                            "headers": {"Cache-Control": {"schema": {"const": "no-store"}}},
                            "content": {"application/json": {"schema": {
                                "type": "object",
                                "required": ["generated_at", "schema_version", "items", "next_after"],
                                "properties": {
                                    "generated_at": {"type": "string", "format": "date-time"},
                                    "schema_version": {"const": 1},
                                    "items": {
                                        "type": "array", "maxItems": 25,
                                        "items": action_item_schema,
                                    },
                                    "next_after": {
                                        "type": ["string", "null"], "maxLength": 1200,
                                        "pattern": "^[A-Za-z0-9_-]+$",
                                    },
                                },
                                "additionalProperties": False,
                            }}},
                        },
                        "400": {"description": "Invalid query or cursor"},
                        "401": {"description": "Missing or invalid bearer token"},
                        "500": {"description": "Sanitized internal operational error"},
                    },
                }
            },
        },
    }
