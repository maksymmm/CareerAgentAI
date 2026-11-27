"""SQLite read-only operational probe for failed and stuck durable work."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from career_agent_ai.application.observability.models import (
    OperationalIssue,
    OperationalIssuePage,
    OperationalSeverity,
)
from career_agent_ai.application.observability.structured_logging import redact_text
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


class SQLiteOperationalProbe:
    """Inspect durable SQLite state with indexed, bounded read-only queries."""

    MAX_LIMIT = 500
    DEFAULT_LIMIT = 100

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def inspect(
        self, *, now: datetime, stale_after_seconds: int
    ) -> tuple[OperationalIssue, ...]:
        """Return a bounded compatibility view of operational issues."""
        return self.inspect_page(
            now=now,
            stale_after_seconds=stale_after_seconds,
            limit=self.MAX_LIMIT,
        ).issues

    def inspect_page(
        self, *, now: datetime, stale_after_seconds: int, limit: int
    ) -> OperationalIssuePage:
        """Return a stable page without loading unbounded provider or run history."""
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
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= self.MAX_LIMIT
        ):
            raise ValueError(f"limit must be between 1 and {self.MAX_LIMIT}.")

        now_utc = now.astimezone(timezone.utc)
        cutoff = now_utc - timedelta(seconds=stale_after_seconds)
        action_issues, action_more = self._external_action_issues(cutoff, limit)
        loop_issues, loop_more = self._career_loop_issues(cutoff, now_utc, limit)
        issues = sorted(
            (*action_issues, *loop_issues),
            key=lambda item: (
                item.severity.value,
                item.issue_type,
                item.updated_at,
                item.entity_id,
            ),
        )
        truncated = action_more or loop_more or len(issues) > limit
        return OperationalIssuePage(tuple(issues[:limit]), truncated)

    def _external_action_issues(
        self, cutoff: datetime, limit: int
    ) -> tuple[list[OperationalIssue], bool]:
        if not self._table_exists("external_action_operations"):
            return [], False
        self._require_indexes(
            (
                "idx_external_actions_recovery_failed",
                "idx_external_actions_recovery_reconciliation_required",
                "idx_external_actions_recovery_in_progress",
                "idx_external_actions_recovery_prepared",
            )
        )

        specifications = (
            (
                "failed",
                "external_action_failed",
                OperationalSeverity.ERROR,
                False,
            ),
            (
                "reconciliation_required",
                "external_action_reconciliation_required",
                OperationalSeverity.ERROR,
                False,
            ),
            (
                "in_progress",
                "external_action_stuck_in_progress",
                OperationalSeverity.ERROR,
                True,
            ),
            (
                "prepared",
                "external_action_stale_prepared",
                OperationalSeverity.WARNING,
                True,
            ),
        )
        issues: list[OperationalIssue] = []
        truncated = False
        for status, issue_type, severity, stale_only in specifications:
            sql = (
                "SELECT operation_id, action_type, status, error, updated_at "
                "FROM external_action_operations WHERE status = ?"
            )
            parameters: tuple[object, ...]
            if stale_only:
                sql += " AND (julianday(updated_at) < julianday(?) OR julianday(updated_at) IS NULL)"
                parameters = (status, cutoff.isoformat())
            else:
                parameters = (status,)
            sql += " ORDER BY julianday(updated_at), operation_id LIMIT ?"
            rows = self._database.connection.execute(
                sql, (*parameters, limit + 1)
            ).fetchall()
            if len(rows) > limit:
                truncated = True
                rows = rows[:limit]
            for operation_id, action_type, stored_status, error, raw_updated in rows:
                updated = self._parse_timestamp(raw_updated, "external action")
                issues.append(
                    OperationalIssue(
                        issue_type=issue_type,
                        entity_id=str(operation_id),
                        severity=severity,
                        updated_at=updated,
                        details={
                            "action_type": str(action_type),
                            "status": str(stored_status),
                            "error": (
                                None
                                if error is None
                                else redact_text(str(error))[:1000]
                            ),
                        },
                    )
                )
        return issues, truncated

    def _career_loop_issues(
        self, cutoff: datetime, now: datetime, limit: int
    ) -> tuple[list[OperationalIssue], bool]:
        if not self._table_exists("autonomous_career_loops"):
            return [], False
        self._require_indexes(
            (
                "idx_autonomous_loop_failed_updated",
                "idx_autonomous_loop_stale_updated",
                "idx_autonomous_loop_malformed_updated",
            )
        )

        issues: list[OperationalIssue] =
        truncated = False

        malformed = self._database.connection.execute(
            """
            SELECT run_id, updated_at
            FROM autonomous_career_loops
            WHERE json_valid(payload_json) = 0
            ORDER BY julianday(updated_at), run_id
            LIMIT ?
            """,
            (limit + 1,),
        ).fetchall()
        if len(malformed) > limit:
            truncated = True
            malformed = malformed[:limit]
        for run_id, raw_updated in malformed:
            issues.append(
                OperationalIssue(
                    issue_type="career_loop_malformed",
                    entity_id=str(run_id),
                    severity=OperationalSeverity.ERROR,
                    updated_at=self._parse_timestamp(raw_updated, "career loop"),
                    details={"reason": "invalid_payload_json"},
                )
            )

        failed = self._database.connection.execute(
            """
            SELECT run_id, payload_json, phase, updated_at,
                   execution_claim_owner, execution_claim_expires_at
            FROM autonomous_career_loops
            WHERE phase = 'failed' AND json_valid(payload_json) = 1
            ORDER BY julianday(updated_at), run_id
            LIMIT ?
            """,
            (limit + 1,),
        ).fetchall()
        if len(failed) > limit:
            truncated = True
            failed = failed[:limit]
        for run_id, raw_payload, projected_phase, raw_updated, claim_owner, raw_expiry in failed:
            updated = self._parse_timestamp(raw_updated, "career loop")
            self._validate_loop_snapshot(raw_payload, projected_phase)
            if raw_expiry is not None:
                self._parse_timestamp(raw_expiry, "career loop execution lease")
            issues.append(
                OperationalIssue(
                    issue_type="career_loop_failed",
                    entity_id=str(run_id),
                    severity=OperationalSeverity.ERROR,
                    updated_at=updated,
                    details={
                        "phase": "failed",
                        "error": (
                            None
                            if self._loop_error(raw_payload) is None
                            else redact_text(self._loop_error(raw_payload))[:1000]
                        ),
                    },
                )
            )

        stuck = self._database.connection.execute(
            """
            SELECT run_id, payload_json, phase, updated_at,
                   execution_claim_owner, execution_claim_expires_at
            FROM autonomous_career_loops
            WHERE phase NOT IN ('complete', 'failed')
              AND (julianday(updated_at) < julianday(?) OR julianday(updated_at) IS NULL)
              AND (
                    execution_claim_owner IS NULL
                    OR execution_claim_expires_at IS NULL
                    OR julianday(execution_claim_expires_at) <= julianday(?)
                    OR julianday(execution_claim_expires_at) IS NULL
              )
              AND json_valid(payload_json) = 1
              AND CASE
                    WHEN json_valid(payload_json)
                    THEN json_extract(payload_json, '$.pending_human_action')
                  END IS NULL
            ORDER BY julianday(updated_at), run_id
            LIMIT ?
            """,
            (cutoff.isoformat(), now.isoformat(), limit + 1),
        ).fetchall()
        if len(stuck) > limit:
            truncated = True
            stuck = stuck[:limit]
        for run_id, raw_payload, projected_phase, raw_updated, claim_owner, raw_expiry in stuck:
            updated = self._parse_timestamp(raw_updated, "career loop")
            payload = self._validate_loop_snapshot(raw_payload, projected_phase)
            if raw_expiry is not None:
                expiry = self._parse_timestamp(raw_expiry, "career loop execution lease")
                if claim_owner is not None and expiry > now:
                    continue
            if payload.get("pending_human_action") is not None:
                continue
            issues.append(
                OperationalIssue(
                    issue_type="career_loop_stuck",
                    entity_id=str(run_id),
                    severity=OperationalSeverity.WARNING,
                    updated_at=updated,
                    details={"phase": projected_phase},
                )
            )
        return issues, truncated

    @staticmethod
    def _loop_error(raw_payload: str) -> str | None:
        payload = json.loads(raw_payload)
        error = payload.get("last_error")
        return None if error is None else str(error)

    @staticmethod
    def _validate_loop_snapshot(raw_payload: str, projected_phase: str) -> dict[str, object]:
        try:
            payload = json.loads(raw_payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Persisted autonomous-loop observability data is malformed.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Persisted autonomous-loop observability payload must be an object.")
        phase = payload.get("phase")
        if not isinstance(phase, str):
            raise ValueError("Persisted autonomous-loop observability phase is malformed.")
        if phase != projected_phase:
            raise ValueError("Persisted autonomous-loop phase projection is inconsistent.")
        return payload

    def _require_indexes(self, names: tuple[str, ...]) -> None:
        existing = {
            row[0]
            for row in self._database.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        if any(name not in existing for name in names):
            raise ValueError("Operational recovery indexes are missing; refusing an unbounded scan.")

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
