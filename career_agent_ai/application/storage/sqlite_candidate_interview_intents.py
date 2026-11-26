"""Read-only owner-scoped discovery for pending candidate interview actions."""

from __future__ import annotations

import json
from typing import Any

from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopPhase,
    validate_loop_identifier,
)
from career_agent_ai.application.external_actions.external_action_operation import (
    ExternalActionStatus,
)
from career_agent_ai.application.storage.sqlite_candidate_schedule import (
    SQLiteCandidateSchedule,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase

_EVENT_COLUMNS = (
    "event_id, candidate_id, employer_name, event_type, location, "
    "start_at, start_epoch_us, end_at, end_epoch_us, timezone_name, "
    "status, NULL, NULL, version, created_at, updated_at, "
)
_REQUIRED_OPERATION_FIELDS = {
    "event_id",
    "event_version",
    "event_start_at",
    "event_end_at",
    "event_timezone_name",
    "event_status",
}


class SQLiteCandidateInterviewIntents:
    """Discover pending interview reconciliation without exposing provider internals."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def list_page(
        self,
        *,
        user_id: str,
        limit: int = 25,
        after: str | None = None,
    ) -> dict[str, Any]:
        """Return a bounded keyset page after filtering by trusted owner metadata."""
        owner = validate_loop_identifier(user_id, "user_id")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100.")
        cursor = "" if after is None else validate_loop_identifier(after, "after")
        if after is not None and cursor != after:
            raise ValueError("after must be a canonical run identity.")

        database = SQLiteDatabase.open_read_only(self._database_path)
        try:
            connection = database.connection
            # Keep the loop, event and operation reads on one SQLite snapshot.
            connection.execute("BEGIN")
            rows = connection.execute(
                """
                SELECT run_id, owner_user_id, payload_json, version
                FROM autonomous_career_loops
                WHERE owner_user_id = ? AND phase = ? AND run_id > ?
                ORDER BY run_id
                LIMIT ?
                """,
                (
                    owner,
                    CareerLoopPhase.INTERVIEW_RECONCILIATION.value,
                    cursor,
                    limit + 1,
                ),
            ).fetchall()
            items = [
                self._intent_from_row(row, owner=owner, connection=connection)
                for row in rows[:limit]
            ]
            return {
                "schema_version": 1,
                "items": items,
                "next_after": items[-1]["run_id"] if len(rows) > limit else None,
            }
        finally:
            if database.connection.in_transaction:
                database.connection.rollback()
            database.close()

    @classmethod
    def _intent_from_row(
        cls,
        row: tuple[Any, ...],
        *,
        owner: str,
        connection: Any,
    ) -> dict[str, Any]:
        run_id, row_owner, encoded_state, storage_version = row
        if row_owner != owner:
            raise ValueError("Persisted candidate interview intent is malformed.")
        try:
            run_id = validate_loop_identifier(run_id, "run_id")
            state = cls._json_object(encoded_state)
            request = state["request"]
            action = state["pending_human_action"]
            if (
                type(state.get("serialization_version")) is not int
                or state.get("serialization_version") not in {1, 2, 3}
                or state.get("run_id") != run_id
                or state.get("phase") != CareerLoopPhase.INTERVIEW_RECONCILIATION.value
                or type(storage_version) is not int
                or type(state.get("version")) is not int
                or state["version"] != storage_version
                or not isinstance(request, dict)
                or request.get("user_id") != owner
                or not isinstance(action, dict)
                or action.get("kind") != "reconcile_interview_response"
                or not isinstance(action.get("details"), dict)
            ):
                raise ValueError("loop snapshot mismatch")
            details = action["details"]
            operation_id = cls._identifier(details["operation_id"], "operation_id")
            event_id = cls._identifier(details["event_id"], "event_id")
            if request.get("schedule_event_id") != event_id:
                raise ValueError("run request event mismatch")

            event_row = connection.execute(
                "SELECT " + _EVENT_COLUMNS +
                "active_action_operation_id FROM scheduling_events "
                "WHERE candidate_id = ? AND event_id = ?",
                (owner, event_id),
            ).fetchone()
            if event_row is None or event_row[16] != operation_id:
                raise ValueError("event claim mismatch")
            event = SQLiteCandidateSchedule._snapshot(event_row[:16])
            if (
                event["status"] not in {"proposed", "reschedule_requested"}
                or details.get("employer") != event["employer_name"]
                or details.get("local_date") != event["local_date"]
                or details.get("local_start_time") != event["local_start_time"]
                or details.get("timezone") != event["timezone_name"]
            ):
                raise ValueError("event snapshot mismatch")

            operation_row = connection.execute(
                """
                SELECT action_type, status, payload_json
                FROM external_action_operations
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if (
                operation_row is None
                or operation_row[0] != "calendar.accept"
                or operation_row[1] != ExternalActionStatus.RECONCILIATION_REQUIRED.value
            ):
                raise ValueError("operation state mismatch")
            operation_payload = cls._json_object(operation_row[2])
            if (
                set(operation_payload) != _REQUIRED_OPERATION_FIELDS
                or operation_payload.get("event_id") != event_id
                or type(operation_payload.get("event_version")) is not int
                or operation_payload.get("event_version") != event["state_version"]
                or operation_payload.get("event_start_at") != event["start_at"]
                or operation_payload.get("event_end_at") != event["end_at"]
                or operation_payload.get("event_timezone_name") != event["timezone_name"]
                or operation_payload.get("event_status") != event["status"]
            ):
                raise ValueError("operation intent mismatch")
            return {
                "run_id": run_id,
                "state_version": storage_version,
                "action_type": operation_row[0],
                "operation_status": operation_row[1],
                "event": event,
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                "Persisted candidate interview intent is malformed."
            ) from exc

    @staticmethod
    def _identifier(value: Any, field: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field} is malformed.")
        normalized = validate_loop_identifier(value, field)
        if normalized != value:
            raise ValueError(f"{field} is not canonical.")
        return normalized

    @staticmethod
    def _json_object(encoded: Any) -> dict[str, Any]:
        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate persisted JSON member")
                result[key] = value
            return result

        value = json.loads(
            encoded,
            object_pairs_hook=unique_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {token}")
            ),
        )
        if not isinstance(value, dict):
            raise ValueError("persisted JSON must be an object")
        return value
