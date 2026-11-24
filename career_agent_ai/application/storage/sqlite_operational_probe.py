"""SQLite read-only operational probe for failed and stuck durable work."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from career_agent_ai.application.observability.action_cursor import (
    decode_action_cursor,
    encode_action_cursor,
)
from career_agent_ai.application.observability.models import (
    OperationalIssue,
    OperationalSeverity,
)
from career_agent_ai.application.observability.structured_logging import redact_text
from career_agent_ai.application.observability.references import stable_reference
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
            *self._career_loop_issues(cutoff, now_utc),
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


    def list_action_statuses(
        self,
        *,
        limit: int = 25,
        after: str | None = None,
        candidate_run_ref: str | None = None,
    ) -> dict[str, object]:
        """List bounded action metadata, optionally scoped to one candidate run."""
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
            raise ValueError("candidate_run_ref must be a 24-character lowercase hexadecimal reference.")
        cursor = (
            None
            if after is None
            else decode_action_cursor(after, candidate_run_ref=candidate_run_ref)
        )
        if not self._table_exists("external_action_operations"):
            return {"items": [], "next_after": None}
        columns = {
            row[1]
            for row in self._database.connection.execute(
                "PRAGMA table_info(external_action_operations)"
            )
        }
        correlation_expression = "correlation_id" if "correlation_id" in columns else "NULL"
        correlation_ref_expression = (
            "correlation_ref" if "correlation_ref" in columns else "NULL"
        )
        if candidate_run_ref is not None and "correlation_ref" not in columns:
            raise RuntimeError(
                "Candidate-run correlation filtering is unavailable for this database schema."
            )

        conditions = [
            "status IN ('prepared', 'in_progress', 'failed', 'reconciliation_required')"
        ]
        parameters: list[object] = []
        if candidate_run_ref is not None:
            conditions.append("correlation_ref = ?")
            parameters.append(candidate_run_ref)
        if cursor is not None:
            conditions.append("operation_id > ?")
            parameters.append(cursor)
        parameters.append(limit + 1)
        rows = self._database.connection.execute(
            f"""
            SELECT operation_id, action_type, status, created_at, updated_at,
                   CASE WHEN error IS NULL THEN 0 ELSE 1 END,
                   {correlation_expression}, {correlation_ref_expression}
            FROM external_action_operations
            WHERE {" AND ".join(conditions)}
            ORDER BY operation_id ASC
            LIMIT ?
            """,
            tuple(parameters),
        ).fetchall()
        visible = rows[:limit]
        items: list[dict[str, object]] = []
        for (
            operation_id, action_type, status, created_at, updated_at,
            error_present, correlation_id, stored_correlation_ref,
        ) in visible:
            if (
                not isinstance(operation_id, str)
                or not operation_id
                or len(operation_id) > 200
                or operation_id != operation_id.strip()
                or any(ord(character) < 32 or ord(character) == 127 for character in operation_id)
                or any(0xD800 <= ord(character) <= 0xDFFF for character in operation_id)
            ):
                raise RuntimeError("Persisted external-action identifier is malformed.")
            if not isinstance(action_type, str) or not action_type or len(action_type) > 100:
                raise RuntimeError("Persisted external-action type is malformed.")
            if status not in {
                "prepared", "in_progress", "failed", "reconciliation_required"
            }:
                raise RuntimeError("Persisted external-action status is malformed.")
            try:
                created = self._parse_timestamp(created_at, "external action")
                updated = self._parse_timestamp(updated_at, "external action")
            except ValueError as exc:
                raise RuntimeError("Persisted external-action timestamp is malformed.") from exc
            if action_type.startswith("application."):
                family = "application_submission"
            elif action_type.startswith("communication."):
                family = "communication"
            elif action_type.startswith("calendar."):
                family = "calendar"
            else:
                family = "other"
            if correlation_id is None:
                expected_correlation_ref = None
            else:
                if (
                    not isinstance(correlation_id, str)
                    or not correlation_id
                    or len(correlation_id) > 200
                    or correlation_id != correlation_id.strip()
                    or any(ord(character) < 32 or ord(character) == 127 for character in correlation_id)
                    or any(0xD800 <= ord(character) <= 0xDFFF for character in correlation_id)
                ):
                    raise RuntimeError("Persisted external-action correlation identifier is malformed.")
                expected_correlation_ref = stable_reference(correlation_id)
            if "correlation_ref" in columns and stored_correlation_ref != expected_correlation_ref:
                raise RuntimeError("Persisted external-action correlation reference is inconsistent.")
            if stored_correlation_ref is not None and (
                not isinstance(stored_correlation_ref, str)
                or re.fullmatch(r"[a-f0-9]{24}", stored_correlation_ref) is None
            ):
                raise RuntimeError("Persisted external-action correlation reference is malformed.")
            candidate_ref = (
                stored_correlation_ref
                if "correlation_ref" in columns
                else expected_correlation_ref
            )
            items.append({
                "operation_ref": stable_reference(operation_id),
                "candidate_run_ref": candidate_ref,
                "action_family": family,
                "status": status,
                "created_at": created.isoformat(),
                "updated_at": updated.isoformat(),
                "error_present": bool(error_present),
            })
        next_after = (
            encode_action_cursor(
                visible[-1][0],
                candidate_run_ref=candidate_run_ref,
            )
            if len(rows) > limit and visible
            else None
        )
        return {"items": items, "next_after": next_after}

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
                        "error": None if error is None else redact_text(str(error))[:1000],
                    },
                )
            )
        return issues

    def _career_loop_issues(
        self, cutoff: datetime, now: datetime
    ) -> list[OperationalIssue]:
        if not self._table_exists("autonomous_career_loops"):
            return []
        rows = self._database.connection.execute(
            """
            SELECT run_id, payload_json, updated_at,
                   execution_claim_owner, execution_claim_expires_at
            FROM autonomous_career_loops
            ORDER BY updated_at, run_id
            """
        ).fetchall()
        issues: list[OperationalIssue] = []
        for run_id, raw_payload, raw_updated, claim_owner, raw_claim_expiry in rows:
            updated = self._parse_timestamp(raw_updated, "career loop")
            claim_expiry = (
                None
                if raw_claim_expiry is None
                else self._parse_timestamp(raw_claim_expiry, "career loop execution lease")
            )
            active_claim = (
                claim_owner is not None
                and claim_expiry is not None
                and claim_expiry > now
            )
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
                            "error": (
                                None
                                if payload.get("last_error") is None
                                else redact_text(str(payload.get("last_error")))[:1000]
                            ),
                        },
                    )
                )
            elif (
                phase != "complete"
                and pending is None
                and updated < cutoff
                and not active_claim
            ):
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
