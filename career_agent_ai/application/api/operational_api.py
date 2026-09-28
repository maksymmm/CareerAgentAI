"""Read-only production operational API with a dependency-free WSGI adapter."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import parse_qs

from career_agent_ai.application.observability import OperationalProbe


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


class OperationalWSGIApp:
    """Serve the read-only operational API using the standard WSGI contract."""

    def __init__(self, service: OperationalApiService) -> None:
        self._service = service

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
                return self._respond(
                    start_response,
                    "200 OK",
                    self._service.issues(stale_after_seconds=stale),
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
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "CareerAgentAI Operational API",
            "version": "1.0.0",
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
                    },
                }
            },
        },
    }
