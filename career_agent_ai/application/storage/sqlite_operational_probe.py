"""SQLite read-only operational probe for failed and stuck durable work."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from career_agent_ai.application.observability.models import (
    OperationalIssue,
    OperationalSeverity,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


class SQLiteOperationalProbe:
    """Inspect durable SQLite state without changing workflow or action status."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def inspect(
        self, *, now: datetime, stale_after_seconds: int
    ) -> tuple[OperationalIssue, ...]:
        """Return deterministic failed, ambiguous, and stale-work observations."""
        if not isinstance(now, datetime):
            raise TypeError("now must be a datetime.")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware.")
        if (
            not isinstance(stale_after_seconds, int)
            or isinstance(stale_after_seconds, bool)
            or stale_after_seconds < 1
        ):
            raise ValueError("stale_after_seconds must be a positive integer.")
        now_utc = now.astimezone(timezone.utc)
        cutoff = now_utc - timedelta(seconds=stale_after_seconds)
        issues = [
            *self._external_action_issues(cutoff),
            *self._career_loop_issues(cutoff),
        ]
        return tuple(
            sorted(
                issues,
                key=lambda item: (
                    item.severity.value,
                    item.issue_type,
                    item.updated_at,
                    item.entity_id,
                ),
            )
        )

    def _external_action_issues(self, cutoff: datetime) -> list[OperationalIssue]:
        if not self._table_exists("external_action_operations"):
            return []
        rows = self._database.connection.execute(
            """
            SELECT operation_id, action_type, status, error, updated_at
            FROM external_action_operations
            WHERE status IN ('failed', 'reconciliation_required', 'in_progress', 'prepared')
            ORDER BY updated_at, operation_id
            """
        ).fetchall()
        issues: list[OperationalIssue] = []
        for operation_id, action_type, status, error, raw_updated in rows:
            updated = self._parse_timestamp(raw_updated, "external action")
            if status == "reconciliation_required":
                issue_type = "external_action_reconciliation_required"
                severity = OperationalSeverity.ERROR
            elif status == "failed":
                issue_type = "external_action_failed"
                severity = OperationalSeverity.ERROR
            elif updated < cutoff:
                issue_type = (
                    "external_action_stuck_in_progress"
                    if status == "in_progress"
                    else "external_action_stale_prepared"
                )
                severity = (
                    OperationalSeverity.ERROR
                    if status == "in_progress"
                    else OperationalSeverity.WARNING
                )
            else:
                continue
            issues.append(
                OperationalIssue(
                    issue_type=issue_type,
                    entity_id=str(operation_id),
                    severity=severity,
                    updated_at=updated,
                    details={
                        "action_type": str(action_type),
                        "status": str(status),
                        "error": None if error is None else str(error)[:1000],
                    },
                )
            )
        return issues

    def _career_loop_issues(self, cutoff: datetime) -> list[OperationalIssue]:
        if not self._table_exists("autonomous_career_loops"):
            return []
        rows = self._database.connection.execute(
            """
            SELECT run_id, payload_json, updated_at
            FROM autonomous_career_loops
            ORDER BY updated_at, run_id
            """
        ).fetchall()
        issues: list[OperationalIssue] = []
        for run_id, raw_payload, raw_updated in rows:
            updated = self._parse_timestamp(raw_updated, "career loop")
            try:
                payload = json.loads(raw_payload)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("Persisted autonomous-loop observability data is malformed.") from exc
            if not isinstance(payload, dict):
                raise ValueError("Persisted autonomous-loop observability payload must be an object.")
            phase = payload.get("phase")
            if not isinstance(phase, str):
                raise ValueError("Persisted autonomous-loop observability phase is malformed.")
            pending = payload.get("pending_human_action")
            if phase == "failed":
                issues.append(
                    OperationalIssue(
                        issue_type="career_loop_failed",
                        entity_id=str(run_id),
                        severity=OperationalSeverity.ERROR,
                        updated_at=updated,
                        details={
                            "phase": phase,
                            "error": payload.get("last_error"),
                        },
                    )
                )
            elif phase != "complete" and pending is None and updated < cutoff:
                issues.append(
                    OperationalIssue(
                        issue_type="career_loop_stuck",
                        entity_id=str(run_id),
                        severity=OperationalSeverity.WARNING,
                        updated_at=updated,
                        details={"phase": phase},
                    )
                )
        return issues

    def _table_exists(self, table: str) -> bool:
        row = self._database.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _parse_timestamp(value: object, entity: str) -> datetime:
        if not isinstance(value, str):
            raise ValueError(f"Persisted {entity} timestamp must be text.")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"Persisted {entity} timestamp is malformed.") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"Persisted {entity} timestamp must be timezone-aware.")
        return parsed.astimezone(timezone.utc)
