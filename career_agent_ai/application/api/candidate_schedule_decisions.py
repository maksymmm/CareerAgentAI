"""Authenticated candidate interview-decline workflow over the SQLite sandbox calendar."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import parse_qsl

from career_agent_ai.application.external_actions import (
    ExternalActionService,
    ExternalActionStatus,
)
from career_agent_ai.application.external_actions.external_action_repository import (
    OperationConflictError,
)
from career_agent_ai.application.scheduling import (
    CalendarReconciliationEvidenceError,
    ScheduleEvent,
    ScheduleStatus,
    SchedulingService,
)
from career_agent_ai.application.scheduling.models import (
    HumanScheduleView,
    validate_schedule_identifier,
)
from career_agent_ai.application.scheduling.scheduling_repository import (
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

_MAX_STATE_VERSION = (1 << 63) - 1


class CandidateScheduleDeclineWSGIApp:
    """Expose a candidate-approved decline with durable idempotency and evidence."""

    PENDING_PATH = "/v1/candidate/schedule-event/decline-pending"
    DECLINE_PATH = "/v1/candidate/schedule-event/decline"
    RECONCILIATION_PATH = "/v1/candidate/schedule-event/decline-reconciliation"
    ROUTES = frozenset({PENDING_PATH, DECLINE_PATH, RECONCILIATION_PATH})
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
        required_method = "GET" if path == self.PENDING_PATH else "POST"
        raw_method = environ.get("REQUEST_METHOD")
        if (
            not isinstance(raw_method, str)
            or not raw_method
            or not raw_method.isascii()
            or not raw_method.isalpha()
            or raw_method != raw_method.upper()
            or raw_method != required_method
        ):
            return self._respond(
                start_response,
                "405 Method Not Allowed",
                {"error": "method_not_allowed"},
                [("Allow", required_method)],
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
            normalized_user = validate_schedule_identifier(user_id, "user_id")
            if normalized_user != user_id:
                raise ValueError("user_id must be canonical.")
        except (TypeError, ValueError):
            return self._respond(
                start_response,
                "401 Unauthorized",
                {"error": "unauthorized"},
                [("WWW-Authenticate", "Bearer")],
            )

        try:
            if path == self.PENDING_PATH:
                request = self._pending_request(environ)
            else:
                media_type = str(environ.get("CONTENT_TYPE", "")).partition(";")[0]
                if media_type.strip().casefold() != "application/json":
                    return self._respond(
                        start_response, "415 Unsupported Media Type",
                        {"error": "unsupported_media_type"},
                    )
                payload = self._read_json(environ)
                request = (
                    self._decline_request(payload)
                    if path == self.DECLINE_PATH
                    else self._reconciliation_request(payload)
                )
        except (TypeError, ValueError, KeyError, OverflowError, InvalidOperation):
            return self._respond(
                start_response, "400 Bad Request", {"error": "invalid_request"}
            )

        try:
            if path == self.PENDING_PATH:
                return self._pending(start_response, user_id, request)
            if path == self.DECLINE_PATH:
                return self._decline(start_response, user_id, request)
            return self._reconcile(start_response, user_id, request)
        except KeyError as exc:
            if exc.args and exc.args[0] == "not_found":
                return self._respond(start_response, "404 Not Found", {"error": "not_found"})
            return self._respond(
                start_response, "409 Conflict", {"error": "approval_conflict"}
            )
        except CalendarReconciliationEvidenceError:
            return self._respond(
                start_response, "409 Conflict", {"error": "provider_evidence_conflict"}
            )
        except (SchedulingOperationConflict, OperationConflictError):
            return self._respond(
                start_response, "409 Conflict", {"error": "approval_conflict"}
            )
        except sqlite3.OperationalError:
            return self._respond(
                start_response, "503 Service Unavailable", {"error": "storage_unavailable"}
            )
        except (TypeError, ValueError, OverflowError, InvalidOperation):
            return self._respond(
                start_response, "409 Conflict", {"error": "approval_conflict"}
            )
        except Exception:
            return self._respond(
                start_response, "500 Internal Server Error", {"error": "internal_error"}
            )

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
            return database, repository, service, operations
        except Exception:
            database.close()
            raise

    def _decline(
        self, start_response, user_id: str, request: Mapping[str, Any]
    ) -> Iterable[bytes]:
        event_id = request["event_id"]
        state_version = request["state_version"]
        operation_id = self._operation_id(user_id, event_id, state_version)
        database, repository, service, _operations = self._open_stack()
        try:
            event = repository.get(event_id)
            if event is None or event.candidate_id != user_id:
                raise KeyError("not_found")
            operation = service.get_operation(operation_id)
            if operation is not None:
                self._require_matching_operation(operation, user_id, event_id, state_version)
                self._require_event_matches_intent(event, operation.payload, state_version)
                if operation.status in {
                    ExternalActionStatus.IN_PROGRESS,
                    ExternalActionStatus.RECONCILIATION_REQUIRED,
                }:
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
            elif event.version != state_version or event.status not in {
                ScheduleStatus.PROPOSED,
                ScheduleStatus.RESCHEDULE_REQUESTED,
            }:
                return self._respond(
                    start_response, "409 Conflict", {"error": "approval_conflict"}
                )

            service.decline(
                operation_id,
                event_id,
                human_approved=True,
                expected_version=state_version,
            )
            operation = service.get_operation(operation_id)
            if operation is None:
                raise RuntimeError("Decline intent was not persisted.")
            if operation.status in {
                ExternalActionStatus.IN_PROGRESS,
                ExternalActionStatus.RECONCILIATION_REQUIRED,
            }:
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
        completed = request["completed"]
        operation_id = self._operation_id(user_id, event_id, state_version)
        database, repository, service, _operations = self._open_stack()
        try:
            event = repository.get(event_id)
            if event is None or event.candidate_id != user_id:
                raise KeyError("not_found")
            operation = service.get_operation(operation_id)
            if operation is None:
                raise KeyError("not_found")
            self._require_matching_operation(operation, user_id, event_id, state_version)
            self._require_event_matches_intent(event, operation.payload, state_version)
            service.resolve_reconciliation(operation_id, completed=completed)
            return self._respond(
                start_response,
                "200 OK",
                {
                    "schema_version": 1,
                    "outcome": "completed" if completed else "no_effect",
                    "event": self._event_snapshot(repository.get(event_id)),
                },
            )
        finally:
            database.close()

    @classmethod
    def _pending_operation_ids(cls, connection, user_id: str, event_id: str) -> tuple[str, ...]:
        """Verify every ambiguous row's identity before filtering, with bounded indexed work."""
        rows = connection.execute(
            """
            SELECT operation_id, payload_json
            FROM external_action_operations
            WHERE action_type = ?
              AND status IN (?, ?)
            LIMIT 1001
            """,
            (
                "calendar.decline",
                ExternalActionStatus.IN_PROGRESS.value,
                ExternalActionStatus.RECONCILIATION_REQUIRED.value,
            ),
        ).fetchall()
        if len(rows) > 1000:
            raise SchedulingOperationConflict(
                "Too many ambiguous calendar operations to recover safely."
            )

        def object_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Persisted operation payload contains duplicate members.")
                result[key] = value
            return result

        def reject_constant(_value):
            raise ValueError("Persisted operation payload contains a non-finite number.")

        matches = []
        for operation_id, payload_json in rows:
            try:
                payload = json.loads(
                    payload_json,
                    object_pairs_hook=object_pairs,
                    parse_constant=reject_constant,
                )
                if not isinstance(payload, dict):
                    raise ValueError("Persisted operation payload must be an object.")
                raw_event_id = payload.get("event_id")
                normalized_event_id = validate_schedule_identifier(
                    raw_event_id, "event_id"
                )
                if normalized_event_id != raw_event_id:
                    raise ValueError("Persisted operation event identity is not canonical.")
                raw_candidate_id = payload.get("candidate_id")
                candidate_id = validate_schedule_identifier(
                    raw_candidate_id, "candidate_id"
                )
                if candidate_id != raw_candidate_id:
                    raise ValueError("Persisted operation candidate identity is not canonical.")
                state_version = payload.get("event_version")
                if type(state_version) is not int or not 1 <= state_version <= _MAX_STATE_VERSION:
                    raise ValueError("Persisted operation source version is invalid.")
                expected_operation_id = cls._operation_id(
                    candidate_id, normalized_event_id, state_version
                )
                if operation_id != expected_operation_id:
                    raise ValueError("Persisted operation identity does not match its payload.")
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise SchedulingOperationConflict(
                    "An ambiguous calendar operation has an unmatchable payload."
                ) from exc
            if normalized_event_id == event_id:
                if candidate_id != user_id:
                    raise SchedulingOperationConflict(
                        "An ambiguous decline has a conflicting candidate owner."
                    )
                matches.append(operation_id)
                if len(matches) > 1:
                    raise SchedulingOperationConflict(
                        "More than one ambiguous decline is bound to this event."
                    )
        return tuple(matches)

    def _pending(
        self, start_response, user_id: str, request: Mapping[str, str]
    ) -> Iterable[bytes]:
        """Restore an owned ambiguous decline without replaying the provider."""
        event_id = request["event_id"]
        read_database = SQLiteDatabase.open_read_only(self._database_path)
        try:
            event_row = read_database.connection.execute(
                """
                SELECT event_id, candidate_id, employer_name, event_type, location,
                       start_at, start_epoch_us, end_at, end_epoch_us, timezone_name,
                       status, provider_event_id, application_id, version,
                       created_at, updated_at
                FROM scheduling_events
                WHERE event_id = ? AND candidate_id = ?
                """,
                (event_id, user_id),
            ).fetchone()
            if event_row is None:
                raise KeyError("not_found")
            operation_table = read_database.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'external_action_operations'"
            ).fetchone()
            if operation_table is None:
                return self._respond(
                    start_response,
                    "200 OK",
                    {"schema_version": 1, "outcome": "none", "event_id": event_id},
                )
            pending_ids = self._pending_operation_ids(
                read_database.connection, user_id, event_id
            )
            if not pending_ids:
                return self._respond(
                    start_response,
                    "200 OK",
                    {"schema_version": 1, "outcome": "none", "event_id": event_id},
                )
        finally:
            read_database.close()

        database, repository, _service, operations = self._open_stack()
        try:
            event = repository.get(event_id)
            if event is None or event.candidate_id != user_id:
                raise KeyError("not_found")
            pending_ids = self._pending_operation_ids(database.connection, user_id, event_id)
            pending = tuple(operations.get(operation_id) for operation_id in pending_ids)
            if any(operation is None for operation in pending):
                raise ValueError("Pending decline operation disappeared.")
            if not pending:
                return self._respond(
                    start_response,
                    "200 OK",
                    {"schema_version": 1, "outcome": "none", "event_id": event_id},
                )
            if len(pending) != 1:
                raise SchedulingOperationConflict(
                    "More than one ambiguous decline is bound to this event."
                )
            operation = pending[0]
            if operation.status == ExternalActionStatus.IN_PROGRESS:
                operation = operations.transition(
                    operation.operation_id,
                    ExternalActionStatus.IN_PROGRESS,
                    ExternalActionStatus.RECONCILIATION_REQUIRED,
                    error=(
                        "Process restarted while the calendar provider action was in "
                        "progress; provider evidence is required before retry."
                    ),
                )
            if operation.status != ExternalActionStatus.RECONCILIATION_REQUIRED:
                raise ValueError("Pending decline is not in a recoverable state.")
            payload = operation.payload
            raw_version = payload.get("event_version")
            if type(raw_version) is not int or not 1 <= raw_version <= _MAX_STATE_VERSION:
                raise ValueError("Persisted decline source version is invalid.")
            state_version = raw_version
            self._require_matching_operation(operation, user_id, event_id, state_version)
            self._require_event_matches_intent(event, payload, state_version)
            expected_operation_id = self._operation_id(user_id, event_id, state_version)
            if operation.operation_id != expected_operation_id:
                raise ValueError("Persisted decline operation identity is invalid.")
            return self._respond(
                start_response,
                "200 OK",
                {
                    "schema_version": 1,
                    "outcome": "pending",
                    "event_id": event_id,
                    "state_version": state_version,
                },
            )
        finally:
            database.close()

    @staticmethod
    def _require_matching_operation(
        operation, user_id: str, event_id: str, state_version: int
    ) -> None:
        payload = operation.payload
        if (
            operation.action_type != "calendar.decline"
            or payload.get("candidate_id") != user_id
            or payload.get("event_id") != event_id
            or payload.get("event_version") != state_version
        ):
            raise ValueError("Decline operation does not match the approved request.")

    @staticmethod
    def _require_event_matches_intent(
        event: ScheduleEvent, payload: Mapping[str, Any], state_version: int
    ) -> None:
        source_matches = (
            event.version == state_version
            and payload.get("event_start_at") == event.start_at.isoformat()
            and payload.get("event_end_at")
                == (None if event.end_at is None else event.end_at.isoformat())
            and payload.get("event_timezone_name") == event.timezone_name
            and payload.get("event_status") == event.status.value
        )
        target_already_applied = (
            state_version < _MAX_STATE_VERSION
            and event.version == state_version + 1
            and event.status == ScheduleStatus.DECLINED
            and payload.get("event_start_at") == event.start_at.isoformat()
            and payload.get("event_end_at")
                == (None if event.end_at is None else event.end_at.isoformat())
            and payload.get("event_timezone_name") == event.timezone_name
        )
        if not (source_matches or target_already_applied):
            raise ValueError("Decline source or applied target no longer matches the event.")

    @staticmethod
    def _operation_id(user_id: str, event_id: str, state_version: int) -> str:
        material = json.dumps(
            [user_id, event_id, state_version],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return "candidate-decline:" + sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _event_snapshot(event: ScheduleEvent | None) -> dict[str, Any]:
        if event is None:
            raise RuntimeError("Declined event disappeared.")
        view = asdict(HumanScheduleView.from_event(event))
        view["event_type"] = event.event_type.value
        view["status"] = event.status.value
        view["state_version"] = event.version
        view["start_at"] = event.start_at.isoformat()
        view["end_at"] = None if event.end_at is None else event.end_at.isoformat()
        return view

    @classmethod
    def _decline_request(cls, payload: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"schema_version", "event_id", "state_version", "decision"}
        if set(payload) != allowed:
            raise ValueError("Decline body has unknown or missing fields.")
        cls._schema_version(payload["schema_version"])
        event_id = validate_schedule_identifier(payload["event_id"], "event_id")
        if event_id != payload["event_id"]:
            raise ValueError("event_id must be canonical.")
        state_version = cls._positive_integer(payload["state_version"], "state_version")
        if payload["decision"] != "decline":
            raise ValueError("An explicit decline decision is required.")
        return {"event_id": event_id, "state_version": state_version}

    @classmethod
    def _reconciliation_request(cls, payload: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "schema_version", "event_id", "state_version",
            "completed", "independently_verified",
        }
        if set(payload) != allowed:
            raise ValueError("Reconciliation body has unknown or missing fields.")
        cls._schema_version(payload["schema_version"])
        event_id = validate_schedule_identifier(payload["event_id"], "event_id")
        if event_id != payload["event_id"]:
            raise ValueError("event_id must be canonical.")
        state_version = cls._positive_integer(payload["state_version"], "state_version")
        if type(payload["completed"]) is not bool:
            raise ValueError("completed must be a boolean.")
        if payload["independently_verified"] is not True:
            raise ValueError("Independent provider verification is required.")
        return {
            "event_id": event_id,
            "state_version": state_version,
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
        if isinstance(value, Decimal):
            if (
                not value.is_finite()
                or value < 1
                or value > _MAX_STATE_VERSION
                or value != value.to_integral_value()
            ):
                raise ValueError(f"{field} must be a positive integer.")
            return int(value)
        if isinstance(value, float):
            if (
                not math.isfinite(value)
                or value < 1
                or value > _MAX_STATE_VERSION
                or not value.is_integer()
            ):
                raise ValueError(f"{field} must be a positive integer.")
            return int(value)
        if value < 1 or value > _MAX_STATE_VERSION:
            raise ValueError(f"{field} must be a positive integer.")
        return value

    @classmethod
    def _pending_request(cls, environ: Mapping[str, Any]) -> dict[str, str]:
        query = environ.get("QUERY_STRING", "")
        if (
            not isinstance(query, str)
            or len(query) > 1000
            or re.search(r"%(?![0-9A-Fa-f]{2})", query)
        ):
            raise ValueError("Pending lookup query is invalid.")
        pairs = parse_qsl(
            query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=1,
            errors="strict",
        )
        if len(pairs) != 1 or pairs[0][0] != "event_id":
            raise ValueError("Exactly one event_id query parameter is required.")
        event_id = validate_schedule_identifier(pairs[0][1], "event_id")
        if event_id != pairs[0][1]:
            raise ValueError("event_id must be canonical.")
        return {"event_id": event_id}

    @classmethod
    def _read_json(cls, environ: Mapping[str, Any]) -> Mapping[str, Any]:
        raw_length = environ.get("CONTENT_LENGTH", "")
        if (
            not isinstance(raw_length, str)
            or not raw_length.isascii()
            or not raw_length.isdecimal()
            or len(raw_length) > 6
        ):
            raise ValueError("Content-Length must be a bounded decimal.")
        length = int(raw_length)
        if length > cls.MAX_BODY_BYTES:
            raise ValueError("Request body is too large.")
        stream = environ.get("wsgi.input")
        if stream is None or not callable(getattr(stream, "read", None)):
            raise ValueError("Request body is unavailable.")
        raw = stream.read(length)
        if not isinstance(raw, bytes) or len(raw) != length:
            raise ValueError("Request body is incomplete.")

        def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Request body contains duplicate JSON members.")
                result[key] = value
            return result

        def reject_constant(value: str):
            raise ValueError(f"Non-finite JSON number is not allowed: {value}.")

        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=object_pairs,
            parse_int=Decimal,
            parse_float=Decimal,
            parse_constant=reject_constant,
        )
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

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


def candidate_schedule_decision_openapi_paths() -> dict[str, Any]:
    """Return strict OpenAPI paths for candidate-approved sandbox interview decline."""
    error_schema = {
        "type": "object",
        "required": ["error"],
        "properties": {"error": {"type": "string"}},
        "additionalProperties": True,
    }
    event_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "event_id", "employer_name", "event_type", "location", "status",
            "state_version", "start_at", "end_at", "local_date",
            "local_start_time", "local_end_date", "local_end_time",
            "timezone_name", "utc_offset", "end_utc_offset",
        ],
        "properties": {
            "event_id": {"type": "string"},
            "employer_name": {"type": "string"},
            "event_type": {"type": "string"},
            "location": {"type": "string"},
            "status": {"enum": ["proposed", "reschedule_requested", "declined"]},
            "state_version": {"type": "integer", "minimum": 1},
            "start_at": {"type": "string", "format": "date-time"},
            "end_at": {"type": ["string", "null"], "format": "date-time"},
            "local_date": {"type": "string", "format": "date"},
            "local_start_time": {"type": "string"},
            "local_end_date": {"type": ["string", "null"], "format": "date"},
            "local_end_time": {"type": ["string", "null"]},
            "timezone_name": {"type": "string"},
            "utc_offset": {"type": "string"},
            "end_utc_offset": {"type": ["string", "null"]},
        },
    }
    decline_request = {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "event_id", "state_version", "decision"],
        "properties": {
            "schema_version": {"const": 1},
            "event_id": {"type": "string", "minLength": 1, "maxLength": 200},
            "state_version": {"type": "integer", "minimum": 1, "maximum": _MAX_STATE_VERSION},
            "decision": {"const": "decline"},
        },
    }
    reconciliation_request = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version", "event_id", "state_version",
            "completed", "independently_verified",
        ],
        "properties": {
            "schema_version": {"const": 1},
            "event_id": {"type": "string", "minLength": 1, "maxLength": 200},
            "state_version": {"type": "integer", "minimum": 1, "maximum": _MAX_STATE_VERSION},
            "completed": {"type": "boolean"},
            "independently_verified": {"const": True},
        },
    }
    content = lambda schema: {"application/json": {"schema": schema}}
    common = {
        "400": {"description": "Malformed request", "content": content(error_schema)},
        "401": {"description": "Missing or invalid bearer token", "content": content(error_schema)},
        "404": {"description": "Event or operation not found", "content": content(error_schema)},
        "409": {"description": "Stale approval or provider-evidence conflict", "content": content(error_schema)},
        "503": {"description": "Durable storage unavailable", "content": content(error_schema)},
    }
    security = [{"bearerAuth": []}]
    return {
        CandidateScheduleDeclineWSGIApp.DECLINE_PATH: {
            "post": {
                "operationId": "declineCandidateInterview",
                "security": security,
                "requestBody": {"required": True, "content": content(decline_request)},
                "responses": {
                    "200": {"description": "Decline completed or definite no-effect",
                            "content": content({"type": "object", "properties": {
                                "schema_version": {"const": 1},
                                "outcome": {"enum": ["completed", "no_effect"]},
                                "event": event_schema}})},
                    "202": {"description": "Provider outcome requires reconciliation",
                            "content": content({"type": "object", "required": [
                                "schema_version", "outcome", "event_id", "state_version"],
                                "properties": {"schema_version": {"const": 1},
                                    "outcome": {"const": "reconciliation_required"},
                                    "event_id": {"type": "string"},
                                    "state_version": {"type": "integer"}}})},
                    **common,
                    "405": {"description": "Only POST is allowed"},
                    "415": {"description": "JSON is required"},
                },
            },
        },
        CandidateScheduleDeclineWSGIApp.RECONCILIATION_PATH: {
            "post": {
                "operationId": "reconcileCandidateInterviewDecline",
                "security": security,
                "requestBody": {"required": True, "content": content(reconciliation_request)},
                "responses": {
                    "200": {"description": "Provider evidence recorded",
                            "content": content({"type": "object", "properties": {
                                "schema_version": {"const": 1},
                                "outcome": {"enum": ["completed", "no_effect"]},
                                "event": event_schema}})},
                    **common,
                    "405": {"description": "Only POST is allowed"},
                    "415": {"description": "JSON is required"},
                },
            },
        },
        CandidateScheduleDeclineWSGIApp.PENDING_PATH: {
            "get": {
                "operationId": "getPendingCandidateInterviewDecline",
                "security": security,
                "parameters": [{"name": "event_id", "in": "query", "required": True,
                                "schema": {"type": "string", "minLength": 1, "maxLength": 200}}],
                "responses": {
                    "200": {"description": "Owned pending decline intent, or none",
                            "content": content({"type": "object", "required": [
                                "schema_version", "outcome", "event_id"],
                                "properties": {"schema_version": {"const": 1},
                                    "outcome": {"enum": ["none", "pending"]},
                                    "event_id": {"type": "string"},
                                    "state_version": {"type": "integer"}}})},
                    **common,
                    "405": {"description": "Only GET is allowed"},
                },
            },
        },
    }
