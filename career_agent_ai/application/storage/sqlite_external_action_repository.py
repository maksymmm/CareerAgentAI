from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from career_agent_ai.application.external_actions.external_action_operation import (
    ExternalActionOperation,
    ExternalActionStatus,
)
from career_agent_ai.application.external_actions.external_action_repository import (
    ExternalActionOperationRepository,
    OperationConflictError,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


class SQLiteExternalActionOperationRepository(ExternalActionOperationRepository):
    """SQLite persistence with atomic lifecycle transitions and JSON validation."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database
        self._create_schema()

    def create(self, operation: ExternalActionOperation) -> ExternalActionOperation:
        """Persist intent idempotently while rejecting identifier reuse."""
        if operation.status != ExternalActionStatus.PREPARED:
            raise ValueError("A new external-action operation must be prepared.")
        payload_json = self._dump(operation.payload)
        try:
            self._database.connection.execute(
                """
                INSERT INTO external_action_operations (
                    operation_id, action_type, payload_json, status, result_json,
                    error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    operation.operation_id,
                    operation.action_type,
                    payload_json,
                    operation.status.value,
                    operation.created_at.isoformat(),
                    operation.updated_at.isoformat(),
                ),
            )
            self._database.connection.commit()
            return operation
        except sqlite3.IntegrityError:
            self._database.connection.rollback()
        existing = self.get(operation.operation_id)
        if (
            existing is None
            or existing.action_type != operation.action_type
            or dict(existing.payload) != dict(operation.payload)
        ):
            raise OperationConflictError(
                "operation_id is already bound to a different external-action intent."
            )
        return existing

    def get(self, operation_id: str) -> ExternalActionOperation | None:
        """Load and strictly validate one persisted operation."""
        normalized = operation_id.strip()
        if not normalized:
            raise ValueError("operation_id must not be empty.")
        row = self._database.connection.execute(
            """
            SELECT operation_id, action_type, payload_json, status, result_json,
                   error, created_at, updated_at
            FROM external_action_operations WHERE operation_id = ?
            """,
            (normalized,),
        ).fetchone()
        if row is None:
            return None
        try:
            payload = self._load_object(row[2], "payload_json")
            result = None if row[4] is None else self._load_object(row[4], "result_json")
            return ExternalActionOperation(
                operation_id=row[0],
                action_type=row[1],
                payload=payload,
                status=ExternalActionStatus(row[3]),
                result=result,
                error=row[5],
                created_at=datetime.fromisoformat(row[6]),
                updated_at=datetime.fromisoformat(row[7]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Persisted external-action operation is malformed.") from exc

    def transition(
        self,
        operation_id: str,
        expected_status: ExternalActionStatus,
        status: ExternalActionStatus,
        *,
        result: Mapping[str, Any] | None = None,
        error: str | None = None,
    ) -> ExternalActionOperation:
        """Atomically transition one operation using compare-and-swap semantics."""
        self._validate_transition(expected_status, status, result, error)
        result_json = None if result is None else self._dump(result)
        cursor = self._database.connection.execute(
            """
            UPDATE external_action_operations
            SET status = ?, result_json = ?, error = ?, updated_at = ?
            WHERE operation_id = ? AND status = ?
            """,
            (
                status.value,
                result_json,
                error,
                datetime.now(timezone.utc).isoformat(),
                operation_id,
                expected_status.value,
            ),
        )
        self._database.connection.commit()
        if cursor.rowcount != 1:
            raise OperationConflictError(
                "Operation is missing or no longer has the expected status."
            )
        operation = self.get(operation_id)
        if operation is None:  # Defensive: the successful update guarantees a row.
            raise OperationConflictError("Operation disappeared after transition.")
        return operation

    def _create_schema(self) -> None:
        connection = self._database.connection
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS external_action_operations (
                operation_id TEXT PRIMARY KEY,
                action_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN (
                    'prepared', 'in_progress', 'succeeded', 'failed',
                    'reconciliation_required'
                )),
                result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_external_actions_status
               ON external_action_operations(status)"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_external_actions_type_status
               ON external_action_operations(action_type, status)"""
        )
        connection.commit()

    @staticmethod
    def _dump(value: Mapping[str, Any]) -> str:
        try:
            return json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("External-action data must be JSON-serializable.") from exc

    @staticmethod
    def _load_object(value: str, field: str) -> dict[str, Any]:
        loaded = json.loads(value)
        if not isinstance(loaded, dict):
            raise ValueError(f"{field} must contain a JSON object.")
        return loaded

    @staticmethod
    def _validate_transition(
        expected: ExternalActionStatus,
        status: ExternalActionStatus,
        result: Mapping[str, Any] | None,
        error: str | None,
    ) -> None:
        allowed = {
            (ExternalActionStatus.PREPARED, ExternalActionStatus.IN_PROGRESS),
            (ExternalActionStatus.IN_PROGRESS, ExternalActionStatus.SUCCEEDED),
            (ExternalActionStatus.IN_PROGRESS, ExternalActionStatus.FAILED),
            (ExternalActionStatus.IN_PROGRESS, ExternalActionStatus.RECONCILIATION_REQUIRED),
        }
        if (expected, status) not in allowed:
            raise ValueError(f"Invalid external-action transition: {expected} -> {status}.")
        if status == ExternalActionStatus.SUCCEEDED and result is None:
            raise ValueError("A succeeded operation requires a result.")
        if status == ExternalActionStatus.FAILED and not error:
            raise ValueError("A failed operation requires an error.")
