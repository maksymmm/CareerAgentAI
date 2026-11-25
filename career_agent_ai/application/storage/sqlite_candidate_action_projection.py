"""Owner-scoped read-only projection of candidate external-action status."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from career_agent_ai.application.observability.action_cursor import (
    decode_action_cursor,
    encode_action_cursor,
)
from career_agent_ai.application.observability.references import stable_reference
from career_agent_ai.application.storage.sqlite_career_loop_repository import (
    SQLiteCandidateRunStatusProjection,
    SQLiteCareerLoopRepository,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


_ACTION_STATUSES = frozenset({
    "prepared",
    "in_progress",
    "succeeded",
    "failed",
    "reconciliation_required",
})


class SQLiteCandidateActionProjection:
    """Read a candidate's own external-action lifecycle without private payloads."""

    def __init__(self, database: SQLiteDatabase) -> None:
        if database.is_memory:
            raise ValueError("Candidate action projection requires durable storage.")
        self._database = database

    def list_page(
        self,
        run_id: str,
        *,
        user_id: str,
        limit: int = 25,
        after: str | None = None,
    ) -> dict[str, Any]:
        """Return one bounded owner-verified action page for a durable candidate run."""
        identifier = SQLiteCareerLoopRepository._identifier(run_id, "run_id")
        owner = SQLiteCareerLoopRepository._identifier(user_id, "user_id")
        if type(limit) is not int or not 1 <= limit <= 25:
            raise ValueError("limit must be an integer between 1 and 25.")

        # Ownership is established from trusted projection columns before any action
        # record is selected. A foreign or missing run has the same public outcome.
        SQLiteCandidateRunStatusProjection(self._database).get(
            identifier, user_id=owner
        )
        run_ref = stable_reference(identifier)
        cursor_ref = (
            None
            if after is None
            else decode_action_cursor(after, candidate_run_ref=run_ref)
        )

        if not self._table_exists("external_action_operations"):
            raise RuntimeError("Candidate external-action storage is unavailable.")
        columns = {
            row[1]
            for row in self._database.connection.execute(
                "PRAGMA table_info(external_action_operations)"
            )
        }
        required = {
            "operation_id",
            "operation_ref",
            "action_type",
            "status",
            "created_at",
            "updated_at",
            "error",
            "correlation_id",
        }
        if not required.issubset(columns):
            raise RuntimeError("Candidate external-action schema is unavailable.")

        indexes = {
            row[1]
            for row in self._database.connection.execute(
                "PRAGMA index_list(external_action_operations)"
            )
        }
        if "idx_external_actions_operation_ref" not in indexes:
            raise RuntimeError("Candidate external-action operation-reference index is unavailable.")

        has_reference = "correlation_ref" in columns
        if has_reference:
            if "idx_external_actions_correlation_ref" not in indexes:
                raise RuntimeError("Candidate external-action reference index is unavailable.")
            cursor_index = "idx_external_actions_correlation_ref_operation_ref"
            inconsistent = self._database.connection.execute(
                """
                SELECT operation_id
                FROM external_action_operations
                WHERE correlation_id = ?
                  AND (correlation_ref IS NULL OR correlation_ref != ?)
                LIMIT 1
                """,
                (identifier, run_ref),
            ).fetchone()
            if inconsistent is not None:
                raise RuntimeError("Persisted candidate action correlation is inconsistent.")
            where = "correlation_ref = ?"
            filter_value = run_ref
        else:
            # Read-only compatibility for pre-reference schemas. The raw correlation
            # column is already indexed and is never included in the response.
            cursor_index = "idx_external_actions_correlation_operation_ref"
            where = "correlation_id = ?"
            filter_value = identifier
        if cursor_index not in indexes:
            raise RuntimeError("Candidate external-action cursor index is unavailable.")

        if cursor_ref is not None:
            cursor_row = self._database.connection.execute(
                f"""
                SELECT operation_id
                FROM external_action_operations
                WHERE {where} AND operation_ref = ?
                """,
                (filter_value, cursor_ref),
            ).fetchone()
            if cursor_row is None:
                raise ValueError("after cursor does not belong to the selected candidate run.")
            if stable_reference(cursor_row[0]) != cursor_ref:
                raise RuntimeError("Persisted candidate action operation reference is inconsistent.")

        cursor_clause = "" if cursor_ref is None else " AND operation_ref > ?"
        parameters: tuple[object, ...] = (
            (filter_value, limit + 1)
            if cursor_ref is None
            else (filter_value, cursor_ref, limit + 1)
        )
        rows = self._database.connection.execute(
            f"""
            SELECT operation_id, operation_ref, action_type, status, created_at, updated_at,
                   CASE WHEN error IS NULL THEN 0 ELSE 1 END,
                   correlation_id
            FROM external_action_operations
            WHERE {where}{cursor_clause}
            ORDER BY operation_ref ASC
            LIMIT ?
            """,
            parameters,
        ).fetchall()

        visible = rows[:limit]
        items: list[dict[str, object]] = []
        for (
            operation_id,
            operation_ref,
            action_type,
            status,
            created_at,
            updated_at,
            error_present,
            correlation_id,
        ) in visible:
            try:
                # The cursor carries this reference, never the private operation key.
                encode_action_cursor(operation_id, candidate_run_ref=run_ref)
                expected_reference = stable_reference(operation_id)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("Persisted candidate action identifier is malformed.") from exc
            if operation_ref != expected_reference:
                raise RuntimeError("Persisted candidate action operation reference is inconsistent.")
            if correlation_id != identifier:
                raise RuntimeError("Persisted candidate action correlation is inconsistent.")
            if (
                not isinstance(action_type, str)
                or not action_type
                or len(action_type) > 100
                or any(ord(character) < 32 or ord(character) == 127 for character in action_type)
                or any(0xD800 <= ord(character) <= 0xDFFF for character in action_type)
            ):
                raise RuntimeError("Persisted candidate action type is malformed.")
            if status not in _ACTION_STATUSES:
                raise RuntimeError("Persisted candidate action status is malformed.")
            if action_type.startswith("application."):
                family = "application_submission"
            elif action_type.startswith("communication."):
                family = "communication"
            elif action_type.startswith("calendar."):
                family = "calendar"
            else:
                family = "other"

            items.append({
                "operation_ref": operation_ref,
                "action_family": family,
                "status": status,
                "created_at": self._timestamp(created_at),
                "updated_at": self._timestamp(updated_at),
                "error_present": bool(error_present),
            })

        next_after = (
            encode_action_cursor(visible[-1][0], candidate_run_ref=run_ref)
            if len(rows) > limit and visible
            else None
        )
        return {
            "schema_version": 1,
            "run_ref": run_ref,
            "items": items,
            "next_after": next_after,
        }

    def _table_exists(self, table: str) -> bool:
        """Check schema presence without creating or changing database objects."""
        return self._database.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone() is not None

    @staticmethod
    def _timestamp(value: object) -> str:
        """Validate a persisted aware timestamp and normalize it to UTC."""
        if not isinstance(value, str) or not value or len(value) > 64:
            raise RuntimeError("Persisted candidate action timestamp is malformed.")
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("timestamp must be timezone-aware")
            return parsed.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("Persisted candidate action timestamp is malformed.") from exc
