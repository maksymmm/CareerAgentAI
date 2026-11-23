"""Authenticated candidate reschedule actions over the durable no-I/O calendar sandbox."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any, Callable, Iterable, Mapping

from career_agent_ai.application.external_actions import (
    ExternalActionService,
    ExternalActionStatus,
)
from career_agent_ai.application.scheduling import (
    CalendarReconciliationEvidenceError,
    ScheduleEvent,
    SchedulingService,
)
from career_agent_ai.application.scheduling.models import (
    HumanScheduleView,
    normalize_aware_datetime,
    validate_schedule_identifier,
    validate_timezone_name,
)
from career_agent_ai.application.scheduling.scheduling_repository import (
    SchedulingConflictError,
    SchedulingOperationConflict,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import (
    SQLiteExternalActionOperationRepository,
)
from career_agent_ai.application.storage.sqlite_sandbox_calendar_provider import (
    SandboxCalendarOutcomePlan,
    SQLiteSandboxCalendarProvider,
)
from career_agent_ai.application.storage.sqlite_scheduling_repository import (
    SQLiteSchedulingRepository,
)

_INSTANT = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)


class CandidateScheduleActionWSGIApp:
    """Expose one human-approved reschedule workflow using only a SQLite sandbox provider."""

    RESCHEDULE_PATH = "/v1/candidate/schedule-event/reschedule"
    RECONCILIATION_PATH = "/v1/candidate/schedule-event/reschedule-reconciliation"
    ROUTES = frozenset({RESCHEDULE_PATH, RECONCILIATION_PATH})
    MAX_BODY_BYTES = 16_384

    def __init__(
        self,
        *,
        database_path: str,
        resolve_bearer: Callable[[str], str | None],
        outcome_plan: SandboxCalendarOutcomePlan | None = None,
    ) -> None:
        if not isinstance(database_path, str) or not database_path:
            raise ValueError("database_path must be non-empty text.")
        if not callable(resolve_bearer):
            raise TypeError("resolve_bearer must be callable.")
        self._database_path = database_path
        self._resolve_bearer = resolve_bearer
        self._outcome_plan = outcome_plan

    def __call__(
        self,
        environ: Mapping[str, Any],
        start_response: Callable[[str, list[tuple[str, str]]], Any],
    ) -> Iterable[bytes]:
        path = environ.get("PATH_INFO")
        if path not in self.ROUTES:
            return self._respond(start_response, "404 Not Found", {"error": "not_found"})
        raw_method = environ.get("REQUEST_METHOD")
        if (
            not isinstance(raw_method, str)
            or not raw_method
            or not raw_method.isascii()
            or not raw_method.isalpha()
            or raw_method != raw_method.upper()
        ):
            return self._respond(
                start_response,
                "405 Method Not Allowed",
                {"error": "method_not_allowed"},
                [("Allow", "POST")],
            )
        if raw_method != "POST":
            return self._respond(
                start_response,
                "405 Method Not Allowed",
                {"error": "method_not_allowed"},
                [("Allow", "POST")],
            )

        authorization = environ.get("HTTP_AUTHORIZATION", "")
        scheme, separator, credentials = (
            authorization.partition(" ") if isinstance(authorization, str) else ("", "", "")
        )
        token = credentials.strip() if separator and scheme.casefold() == "bearer" else ""
        try:
            user_id = self._resolve_bearer(token) if token else None
        except Exception:
            return self._respond(
                start_response, "500 Internal Server Error", {"error": "internal_error"}
            )
        if not isinstance(user_id, str) or not user_id:
            return self._respond(
                start_response,
                "401 Unauthorized",
                {"error": "unauthorized"},
                [("WWW-Authenticate", "Bearer")],
            )
        try:
            validate_schedule_identifier(user_id, "user_id")
        except (TypeError, ValueError):
            return self._respond(
                start_response,
                "401 Unauthorized",
                {"error": "unauthorized"},
                [("WWW-Authenticate", "Bearer")],
            )

        media_type = str(environ.get("CONTENT_TYPE", "")).partition(";")[0]
        if media_type.strip().casefold() != "application/json":
            return self._respond(
                start_response, "415 Unsupported Media Type",
                {"error": "unsupported_media_type"},
            )
        try:
            payload = self._read_json(environ)
            if path == self.RESCHEDULE_PATH:
                request = self._reschedule_request(payload)
                return self._reschedule(start_response, user_id, request)
            request = self._reconciliation_request(payload)
            return self._reconcile(start_response, user_id, request)
        except (TypeError, ValueError, KeyError, OverflowError):
            return self._respond(
                start_response, "400 Bad Request", {"error": "invalid_request"}
            )
        except KeyError as exc:
            # The inner handlers translate missing or foreign resources to 404.
            if str(exc) == "'not_found'":
                return self._respond(start_response, "404 Not Found", {"error": "not_found"})
            return self._respond(
                start_response, "400 Bad Request", {"error": "invalid_request"}
            )
        except CalendarReconciliationEvidenceError:
            return self._respond(
                start_response, "409 Conflict", {"error": "provider_evidence_conflict"}
            )
        except (SchedulingConflictError, SchedulingOperationConflict):
            return self._respond(
                start_response, "409 Conflict", {"error": "schedule_conflict"}
            )
        except sqlite3.OperationalError:
            return self._respond(
                start_response, "503 Service Unavailable", {"error": "storage_unavailable"}
            )
        except Exception:
            return self._respond(
                start_response, "500 Internal Server Error", {"error": "internal_error"}
            )

    def _reschedule(
        self, start_response, user_id: str, request: Mapping[str, Any]
    ) -> Iterable[bytes]:
        event_id = request["event_id"]
        state_version = request["state_version"]
        start_at = request["start_at"]
        end_at = request["end_at"]
        timezone_name = request["timezone_name"]
        operation_id = self._operation_id(
            user_id, event_id, state_version, start_at, end_at, timezone_name
        )
        database, repository, service = self._open_stack()
        try:
            event = repository.get(event_id)
            if event is None or event.candidate_id != user_id:
                raise KeyError("not_found")
            operation = service.get_operation(operation_id)
            if operation is not None:
                self._require_matching_operation(
                    operation, event_id, state_version, start_at, end_at, timezone_name
                )
            elif event.version != state_version:
                return self._respond(
                    start_response, "409 Conflict", {"error": "approval_conflict"}
                )

            service.reschedule(
                operation_id,
                event_id,
                start_at=start_at,
                end_at=end_at,
                timezone_name=timezone_name,
                human_approved=True,
            )
            operation = service.get_operation(operation_id)
            if operation is None:
                raise RuntimeError("Reschedule intent was not persisted.")
            if operation.status == ExternalActionStatus.RECONCILIATION_REQUIRED:
                return self._respond(
                    start_response,
                    "202 Accepted",
                    {
                        "schema_version": 1,
                        "outcome": "reconciliation_required",
                        "event_id": event_id,
                        "state_version": state_version,
                    },
                )
            if operation.status == ExternalActionStatus.FAILED:
                return self._respond(
                    start_response,
                    "200 OK",
                    {
                        "schema_version": 1,
                        "outcome": "no_effect",
                        "event": self._event_snapshot(repository.get(event_id)),
                    },
                )
            if operation.status != ExternalActionStatus.SUCCEEDED:
                return self._respond(
                    start_response,
                    "202 Accepted",
                    {
                        "schema_version": 1,
                        "outcome": "reconciliation_required",
                        "event_id": event_id,
                        "state_version": state_version,
                    },
                )
            return self._respond(
                start_response,
                "200 OK",
                {
                    "schema_version": 1,
                    "outcome": "completed",
                    "event": self._event_snapshot(repository.get(event_id)),
                },
            )
        finally:
            database.close()

    def _reconcile(
        self, start_response, user_id: str, request: Mapping[str, Any]
    ) -> Iterable[bytes]:
        event_id = request["event_id"]
        state_version = request["state_version"]
        start_at = request["start_at"]
        end_at = request["end_at"]
        timezone_name = request["timezone_name"]
        completed = request["completed"]
        operation_id = self._operation_id(
            user_id, event_id, state_version, start_at, end_at, timezone_name
        )
        database, repository, service = self._open_stack()
        try:
            event = repository.get(event_id)
            if event is None or event.candidate_id != user_id:
                raise KeyError("not_found")
            operation = service.get_operation(operation_id)
            if operation is None:
                raise KeyError("not_found")
            self._require_matching_operation(
                operation, event_id, state_version, start_at, end_at, timezone_name
            )
            service.resolve_reconciliation(
                operation_id, completed=completed
            )
            if completed:
                return self._respond(
                    start_response,
                    "200 OK",
                    {
                        "schema_version": 1,
                        "outcome": "completed",
                        "event": self._event_snapshot(repository.get(event_id)),
                    },
                )
            return self._respond(
                start_response,
                "200 OK",
                {
                    "schema_version": 1,
                    "outcome": "no_effect",
                    "event": self._event_snapshot(repository.get(event_id)),
                },
            )
        finally:
            database.close()

    def _open_stack(self):
        database = SQLiteDatabase(self._database_path)
        try:
            repository = SQLiteSchedulingRepository(database)
            provider = SQLiteSandboxCalendarProvider(database, self._outcome_plan)
            operations = SQLiteExternalActionOperationRepository(database)
            service = SchedulingService(
                repository,
                provider,
                ExternalActionService(
                    operations,
                    SchedulingService.action_adapter(provider, repository),
                ),
                require_provider_evidence=True,
            )
            return database, repository, service
        except Exception:
            database.close()
            raise

    @staticmethod
    def _require_matching_operation(
        operation,
        event_id: str,
        state_version: int,
        start_at: datetime,
        end_at: datetime | None,
        timezone_name: str,
    ) -> None:
        payload = operation.payload
        if (
            operation.action_type != "calendar.reschedule"
            or payload.get("event_id") != event_id
            or payload.get("event_version") != state_version
            or payload.get("start_at") != start_at.isoformat()
            or payload.get("end_at") != (None if end_at is None else end_at.isoformat())
            or payload.get("timezone_name") != timezone_name
        ):
            raise ValueError("Reschedule operation does not match the approved request.")

    @staticmethod
    def _operation_id(
        user_id: str,
        event_id: str,
        state_version: int,
        start_at: datetime,
        end_at: datetime | None,
        timezone_name: str,
    ) -> str:
        material = json.dumps(
            [
                user_id,
                event_id,
                state_version,
                start_at.isoformat(),
                None if end_at is None else end_at.isoformat(),
                timezone_name,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return "candidate-reschedule:" + sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _event_snapshot(event: ScheduleEvent | None) -> dict[str, Any]:
        if event is None:
            raise RuntimeError("Rescheduled event disappeared.")
        view = asdict(HumanScheduleView.from_event(event))
        view["event_type"] = event.event_type.value
        view["status"] = event.status.value
        view["state_version"] = event.version
        view["start_at"] = event.start_at.isoformat()
        view["end_at"] = None if event.end_at is None else event.end_at.isoformat()
        return view

    @classmethod
    def _reschedule_request(cls, payload: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "schema_version", "event_id", "state_version", "start_at",
            "end_at", "timezone_name", "decision",
        }
        if set(payload) != allowed:
            raise ValueError("Reschedule body has unknown or missing fields.")
        cls._schema_version(payload["schema_version"])
        event_id = validate_schedule_identifier(payload["event_id"], "event_id")
        state_version = cls._positive_integer(payload["state_version"], "state_version")
        start_at = cls._instant(payload["start_at"], "start_at")
        raw_end = payload["end_at"]
        end_at = None if raw_end is None else cls._instant(raw_end, "end_at")
        if end_at is not None and end_at <= start_at:
            raise ValueError("end_at must be later than start_at.")
        raw_timezone = payload["timezone_name"]
        timezone_name = validate_timezone_name(raw_timezone)
        if timezone_name != raw_timezone:
            raise ValueError("timezone_name must be canonical.")
        if payload["decision"] != "approve":
            raise ValueError("An explicit approve decision is required.")
        return {
            "event_id": event_id,
            "state_version": state_version,
            "start_at": start_at,
            "end_at": end_at,
            "timezone_name": timezone_name,
        }

    @classmethod
    def _reconciliation_request(cls, payload: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "schema_version", "event_id", "state_version", "start_at",
            "end_at", "timezone_name", "completed", "independently_verified",
        }
        if set(payload) != allowed:
            raise ValueError("Reconciliation body has unknown or missing fields.")
        cls._schema_version(payload["schema_version"])
        event_id = validate_schedule_identifier(payload["event_id"], "event_id")
        state_version = cls._positive_integer(payload["state_version"], "state_version")
        start_at = cls._instant(payload["start_at"], "start_at")
        raw_end = payload["end_at"]
        end_at = None if raw_end is None else cls._instant(raw_end, "end_at")
        if end_at is not None and end_at <= start_at:
            raise ValueError("end_at must be later than start_at.")
        raw_timezone = payload["timezone_name"]
        timezone_name = validate_timezone_name(raw_timezone)
        if timezone_name != raw_timezone:
            raise ValueError("timezone_name must be canonical.")
        if type(payload["completed"]) is not bool:
            raise ValueError("completed must be a boolean.")
        if payload["independently_verified"] is not True:
            raise ValueError("Independent provider verification is required.")
        return {
            "event_id": event_id,
            "state_version": state_version,
            "start_at": start_at,
            "end_at": end_at,
            "timezone_name": timezone_name,
            "completed": payload["completed"],
        }

    @staticmethod
    def _schema_version(value: object) -> None:
        if type(value) not in {int, float, Decimal} or value != 1:
            raise ValueError("schema_version must equal 1.")

    @staticmethod
    def _positive_integer(value: object, field: str) -> int:
        if type(value) not in {int, float, Decimal}:
            raise ValueError(f"{field} must be a positive integer.")
        try:
            normalized = int(value)
        except (OverflowError, ValueError) as exc:
            raise ValueError(f"{field} must be a positive integer.") from exc
        if value != normalized or normalized < 1:
            raise ValueError(f"{field} must be a positive integer.")
        return normalized

    @staticmethod
    def _instant(value: object, field: str) -> datetime:
        if (
            not isinstance(value, str)
            or len(value) > 64
            or value != value.strip()
            or not _INSTANT.fullmatch(value)
        ):
            raise ValueError(f"{field} must be an ISO-8601 instant with an explicit offset.")
        normalized_text = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(normalized_text)
        except ValueError as exc:
            raise ValueError(f"{field} is malformed.") from exc
        return normalize_aware_datetime(parsed, field)

    def _read_json(self, environ: Mapping[str, Any]) -> Mapping[str, Any]:
        raw_length = environ.get("CONTENT_LENGTH", "")
        if (
            not isinstance(raw_length, str)
            or not raw_length.isascii()
            or not raw_length.isdecimal()
            or len(raw_length) > 6
        ):
            raise ValueError("Content-Length must be a bounded decimal integer.")
        length = int(raw_length)
        if length < 1 or length > self.MAX_BODY_BYTES:
            raise ValueError("Request body size is invalid.")
        stream = environ.get("wsgi.input")
        if stream is None or not callable(getattr(stream, "read", None)):
            raise ValueError("Request body stream is unavailable.")
        body = stream.read(length)
        if not isinstance(body, bytes) or len(body) != length:
            raise ValueError("Request body is incomplete.")
        try:
            payload = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=self._unique_json_object,
                parse_float=Decimal,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be UTF-8 JSON.") from exc
        if not isinstance(payload, Mapping):
            raise ValueError("Request body must be a JSON object.")
        return payload

    @staticmethod
    def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Request body contains duplicate JSON members.")
            result[key] = value
        return result

    @staticmethod
    def _respond(
        start_response,
        status: str,
        payload: Mapping[str, Any],
        extra: list[tuple[str, str]] | None = None,
    ) -> list[bytes]:
        body = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("Pragma", "no-cache"),
        ]
        if extra:
            headers.extend(extra)
        start_response(status, headers)
        return [body]


def candidate_schedule_action_openapi_paths() -> dict[str, Any]:
    """Return strict OpenAPI paths for human-approved sandbox rescheduling."""
    event_properties = {
        "event_id": {"type": "string", "minLength": 1, "maxLength": 200,
                     "pattern": "^[A-Za-z0-9][A-Za-z0-9._:@+-]*$"},
        "employer_name": {"type": "string"},
        "event_type": {"type": "string"},
        "location": {"type": "string"},
        "status": {"type": "string", "const": "reschedule_requested"},
        "state_version": {"type": "integer", "minimum": 2},
        "start_at": {"type": "string", "format": "date-time"},
        "end_at": {"type": ["string", "null"], "format": "date-time"},
        "local_date": {"type": "string", "format": "date"},
        "local_start_time": {"type": "string"},
        "local_end_date": {"type": ["string", "null"], "format": "date"},
        "local_end_time": {"type": ["string", "null"]},
        "timezone_name": {"type": "string"},
        "utc_offset": {"type": "string"},
        "end_utc_offset": {"type": ["string", "null"]},
    }
    event_schema = {
        "type": "object", "additionalProperties": False,
        "required": list(event_properties), "properties": event_properties,
    }
    body_properties = {
        "schema_version": {"const": 1},
        "event_id": event_properties["event_id"],
        "state_version": {"type": "integer", "minimum": 1},
        "start_at": {"type": "string", "format": "date-time"},
        "end_at": {"type": ["string", "null"], "format": "date-time"},
        "timezone_name": {"type": "string", "minLength": 1, "maxLength": 100},
    }
    reschedule_schema = {
        "type": "object", "additionalProperties": False,
        "required": list(body_properties) + ["decision"],
        "properties": {
            **body_properties,
            "decision": {"type": "string", "const": "approve"},
        },
    }
    reconciliation_schema = {
        "type": "object", "additionalProperties": False,
        "required": list(body_properties) + ["completed", "independently_verified"],
        "properties": {
            **body_properties,
            "completed": {"type": "boolean"},
            "independently_verified": {"type": "boolean", "const": True},
        },
    }
    json_content = lambda schema: {"application/json": {"schema": schema}}
    error_schema = {
        "type": "object", "additionalProperties": False,
        "required": ["error"], "properties": {"error": {"type": "string"}},
    }
    common = {
        "400": {"description": "Invalid request", "content": json_content(error_schema)},
        "401": {"description": "Authentication required", "content": json_content(error_schema)},
        "404": {"description": "Missing or foreign event", "content": json_content(error_schema)},
        "405": {"description": "Only POST is allowed", "content": json_content(error_schema)},
        "415": {"description": "Request body is not application/json", "content": json_content(error_schema)},
        "503": {"description": "Durable storage is unavailable", "content": json_content(error_schema)},
    }
    completed_response = {
        "type": "object", "additionalProperties": False,
        "required": ["schema_version", "outcome", "event"],
        "properties": {
            "schema_version": {"const": 1},
            "outcome": {"const": "completed"},
            "event": event_schema,
        },
    }
    pending_response = {
        "type": "object", "additionalProperties": False,
        "required": ["schema_version", "outcome", "event_id", "state_version"],
        "properties": {
            "schema_version": {"const": 1},
            "outcome": {"const": "reconciliation_required"},
            "event_id": event_properties["event_id"],
            "state_version": {"type": "integer", "minimum": 1},
        },
    }
    no_effect_response = {
        "type": "object", "additionalProperties": False,
        "required": ["schema_version", "outcome", "event"],
        "properties": {
            "schema_version": {"const": 1},
            "outcome": {"const": "no_effect"},
            "event": {
                "type": "object", "additionalProperties": True,
            },
        },
    }
    return {
        CandidateScheduleActionWSGIApp.RESCHEDULE_PATH: {
            "post": {
                "operationId": "requestCandidateInterviewReschedule",
                "security": [{"bearerAuth": []}],
                "requestBody": {"required": True, "content": json_content(reschedule_schema)},
                "responses": {
                    "200": {"description": "Sandbox reschedule completed or definitively had no effect",
                            "headers": {"Cache-Control": {"schema": {"const": "no-store"}}},
                            "content": json_content({"oneOf": [completed_response, no_effect_response]})},
                    "202": {"description": "Provider outcome is ambiguous and requires reconciliation",
                            "content": json_content(pending_response)},
                    **common,
                    "409": {"description": "Stale state, scheduling conflict or provider conflict",
                            "content": json_content(error_schema)},
                },
            },
        },
        CandidateScheduleActionWSGIApp.RECONCILIATION_PATH: {
            "post": {
                "operationId": "reconcileCandidateInterviewReschedule",
                "security": [{"bearerAuth": []}],
                "requestBody": {"required": True, "content": json_content(reconciliation_schema)},
                "responses": {
                    "200": {"description": "Provider evidence confirmed completion or no effect",
                            "headers": {"Cache-Control": {"schema": {"const": "no-store"}}},
                            "content": json_content({"oneOf": [completed_response, no_effect_response]})},
                    **common,
                    "409": {"description": "Provider evidence does not match the independently reported outcome",
                            "content": json_content(error_schema)},
                },
            },
        },
    }
