"""SQLite persistence for end-to-end autonomous career-loop state."""

from __future__ import annotations

import json
import sqlite3
from math import isfinite
from datetime import date, datetime, time, timedelta, timezone
from collections.abc import Mapping
from typing import Any
from zoneinfo import ZoneInfo

from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopPhase,
    CareerLoopRequest,
    CareerLoopState,
    HumanActionEvent,
    HumanActionKind,
    candidate_snapshot_sha256,
)
from career_agent_ai.application.career.autonomous_loop_repository import CareerLoopConflictError
from career_agent_ai.application.jobs.adaptive_job_ranker import AdaptiveJobRanker
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


class SQLiteCareerLoopRepository:
    """Strict JSON, restart-safe persistence for one active career loop."""

    SERIALIZATION_VERSION = 3

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database
        self._create_schema()

    def save(
        self, state: CareerLoopState, *, expected_owner_id: str | None = None
    ) -> None:
        """Create or compare-and-swap one validated active-loop snapshot."""
        if expected_owner_id is not None:
            expected_owner_id = self._identifier(expected_owner_id, "expected_owner_id")
        if not isinstance(state.version, int) or isinstance(state.version, bool) or state.version < 0:
            raise ValueError("state.version must be a non-negative integer.")
        current_version = state.version
        next_version = current_version + 1
        payload = self._serialize(state, version=next_version)
        serialized = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        connection = self._database.connection
        try:
            if current_version == 0:
                connection.execute(
                    """
                    INSERT INTO autonomous_career_loops (
                        run_id, owner_user_id, phase, payload_json, updated_at, version
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.run_id,
                        state.request.user_id,
                        state.phase.value,
                        serialized,
                        state.updated_at.isoformat(),
                        next_version,
                    ),
                )
            else:
                if expected_owner_id is None:
                    cursor = connection.execute(
                        """
                        UPDATE autonomous_career_loops
                        SET phase = ?, payload_json = ?, updated_at = ?, version = ?
                        WHERE run_id = ? AND owner_user_id = ? AND version = ?
                          AND execution_claim_owner IS NULL
                        """,
                        (
                            state.phase.value,
                            serialized,
                            state.updated_at.isoformat(),
                            next_version,
                            state.run_id,
                            state.request.user_id,
                            current_version,
                        ),
                    )
                else:
                    cursor = connection.execute(
                        """
                        UPDATE autonomous_career_loops
                        SET phase = ?, payload_json = ?, updated_at = ?, version = ?
                        WHERE run_id = ? AND owner_user_id = ? AND version = ?
                          AND execution_claim_owner = ?
                        """,
                        (
                            state.phase.value,
                            serialized,
                            state.updated_at.isoformat(),
                            next_version,
                            state.run_id,
                            state.request.user_id,
                            current_version,
                            expected_owner_id,
                        ),
                    )
                if cursor.rowcount != 1:
                    raise CareerLoopConflictError(
                        "Autonomous-loop snapshot is stale and cannot overwrite newer state."
                    )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            if connection.in_transaction:
                connection.rollback()
            raise CareerLoopConflictError(
                "Autonomous-loop run already exists or changed concurrently."
            ) from exc
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        state.version = next_version

    def get(self, run_id: str) -> CareerLoopState | None:
        """Return one persisted active loop or None."""
        run_id = self._identifier(run_id, "run_id")
        row = self._database.connection.execute(
            "SELECT payload_json, version FROM autonomous_career_loops WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(
                row[0],
                parse_constant=lambda token: (_ for _ in ()).throw(
                    ValueError(f"Invalid JSON constant: {token}.")
                ),
            )
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Persisted autonomous-loop JSON is malformed.") from exc
        state = self._deserialize(value, storage_version=row[1])
        if state.run_id != run_id:
            raise ValueError("Persisted autonomous-loop run_id does not match its storage key.")
        return state

    def claim_execution(
        self,
        run_id: str,
        owner_id: str,
        *,
        expected_version: int,
        lease_seconds: int,
    ) -> None:
        """Acquire an expiring execution lease without changing snapshot version."""
        run_id = self._identifier(run_id, "run_id")
        owner_id = self._identifier(owner_id, "owner_id")
        if (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or expected_version < 1
        ):
            raise ValueError("expected_version must be a positive integer.")
        if (
            not isinstance(lease_seconds, int)
            or isinstance(lease_seconds, bool)
            or lease_seconds < 1
            or lease_seconds > 3600
        ):
            raise ValueError("lease_seconds must be between 1 and 3600.")
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=lease_seconds)
        connection = self._database.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT version, execution_claim_owner, execution_claim_expires_at
                   FROM autonomous_career_loops WHERE run_id = ?""",
                (run_id,),
            ).fetchone()
            if row is None or row[0] != expected_version:
                raise CareerLoopConflictError(
                    "Autonomous-loop snapshot changed before execution claim."
                )
            active_owner = row[1]
            active_expiry = (
                None if row[2] is None else datetime.fromisoformat(row[2])
            )
            if (
                active_expiry is not None
                and (active_expiry.tzinfo is None or active_expiry.utcoffset() is None)
            ):
                raise ValueError("Persisted execution lease timestamp is malformed.")
            if (
                active_owner is not None
                and active_owner != owner_id
                and active_expiry is not None
                and active_expiry > now
            ):
                raise CareerLoopConflictError(
                    "Autonomous-loop run is already executing in another worker."
                )
            cursor = connection.execute(
                """UPDATE autonomous_career_loops
                   SET execution_claim_owner = ?, execution_claim_expires_at = ?
                   WHERE run_id = ? AND version = ?""",
                (owner_id, expires_at.isoformat(), run_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise CareerLoopConflictError(
                    "Autonomous-loop execution claim lost its snapshot race."
                )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise

    def renew_execution(
        self,
        run_id: str,
        owner_id: str,
        *,
        lease_seconds: int,
    ) -> None:
        """Extend an unexpired execution lease without touching snapshot version.

        File-backed SQLite uses a short-lived independent connection so a heartbeat
        thread never shares the worker's SQLite connection. In-memory repositories
        are process-local and cannot be safely renewed from another thread; their
        initial lease remains sufficient for unit-test/local single-worker execution.
        """
        run_id = self._identifier(run_id, "run_id")
        owner_id = self._identifier(owner_id, "owner_id")
        if (
            not isinstance(lease_seconds, int)
            or isinstance(lease_seconds, bool)
            or lease_seconds < 1
            or lease_seconds > 3600
        ):
            raise ValueError("lease_seconds must be between 1 and 3600.")
        if self._database.is_memory:
            return

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=lease_seconds)
        connection = sqlite3.connect(self._database.path, timeout=5, uri=True)
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE autonomous_career_loops
                   SET execution_claim_expires_at = ?
                   WHERE run_id = ?
                     AND execution_claim_owner = ?
                     AND execution_claim_expires_at IS NOT NULL
                     AND execution_claim_expires_at > ?""",
                (expires_at.isoformat(), run_id, owner_id, now.isoformat()),
            )
            if cursor.rowcount != 1:
                raise CareerLoopConflictError(
                    "Autonomous-loop execution lease expired or changed owner."
                )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()


    def release_execution(self, run_id: str, owner_id: str) -> None:
        """Release only the execution lease owned by owner_id."""
        run_id = self._identifier(run_id, "run_id")
        owner_id = self._identifier(owner_id, "owner_id")
        cursor = self._database.connection.execute(
            """UPDATE autonomous_career_loops
               SET execution_claim_owner = NULL, execution_claim_expires_at = NULL
               WHERE run_id = ? AND execution_claim_owner = ?""",
            (run_id, owner_id),
        )
        self._database.connection.commit()
        if cursor.rowcount != 1:
            raise CareerLoopConflictError(
                "Autonomous-loop execution lease is no longer owned by this worker."
            )

    def delete(self, run_id: str) -> None:
        """Delete one terminal loop snapshot."""
        run_id = self._identifier(run_id, "run_id")
        self._database.connection.execute(
            "DELETE FROM autonomous_career_loops WHERE run_id = ?", (run_id,)
        )
        self._database.connection.commit()

    def _create_schema(self) -> None:
        self._database.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS autonomous_career_loops (
                run_id TEXT PRIMARY KEY,
                owner_user_id TEXT,
                phase TEXT,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
                execution_claim_owner TEXT,
                execution_claim_expires_at TEXT
            )
            """
        )
        columns = {
            row[1]
            for row in self._database.connection.execute(
                "PRAGMA table_info(autonomous_career_loops)"
            ).fetchall()
        }
        if "version" not in columns:
            self._database.connection.execute(
                "ALTER TABLE autonomous_career_loops "
                "ADD COLUMN version INTEGER NOT NULL DEFAULT 1"
            )
        if "owner_user_id" not in columns:
            self._database.connection.execute(
                "ALTER TABLE autonomous_career_loops ADD COLUMN owner_user_id TEXT"
            )
        # Resume this projection independently of column creation. SQLite may
        # persist ALTER TABLE before initialization reaches the backfill, so a
        # restarted process must repair every still-unowned valid snapshot.
        self._database.connection.execute(
            """
            UPDATE autonomous_career_loops
            SET owner_user_id = json_extract(payload_json, '$.request.user_id')
            WHERE owner_user_id IS NULL
              AND json_valid(payload_json)
              AND json_type(payload_json, '$.request.user_id') = 'text'
            """
        )
        if "phase" not in columns:
            self._database.connection.execute(
                "ALTER TABLE autonomous_career_loops ADD COLUMN phase TEXT"
            )
        # This backfill is intentionally independent of column creation. If a
        # process stops after ALTER TABLE has committed but before initialization
        # finishes, the next startup resumes every still-unprojected valid row.
        self._database.connection.execute(
            """
            UPDATE autonomous_career_loops
            SET phase = json_extract(payload_json, '$.phase')
            WHERE phase IS NULL
              AND json_valid(payload_json)
              AND json_type(payload_json, '$.phase') = 'text'
            """
        )
        if "execution_claim_owner" not in columns:
            self._database.connection.execute(
                "ALTER TABLE autonomous_career_loops "
                "ADD COLUMN execution_claim_owner TEXT"
            )
        if "execution_claim_expires_at" not in columns:
            self._database.connection.execute(
                "ALTER TABLE autonomous_career_loops "
                "ADD COLUMN execution_claim_expires_at TEXT"
            )
        self._database.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_autonomous_loop_updated
            ON autonomous_career_loops(updated_at, run_id)
            """
        )
        self._database.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_autonomous_loop_owner
            ON autonomous_career_loops(owner_user_id, run_id)
            """
        )
        self._database.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_autonomous_loop_owner_phase
            ON autonomous_career_loops(owner_user_id, phase, run_id)
            """
        )
        self._database.connection.commit()

    @classmethod
    def _validated_candidate_snapshot_digest(
        cls,
        *,
        job_id: Any,
        job_title: Any,
        company: Any,
        company_id: Any,
        digest: Any,
        allow_missing_digest: bool,
    ) -> str | None:
        normalized_job_id = cls._optional_text(job_id)
        normalized_title = cls._optional_text(job_title)
        normalized_company = cls._optional_text(company)
        normalized_company_id = cls._optional_text(company_id)
        normalized_digest = cls._optional_text(digest)
        if normalized_job_id is None:
            if any(
                value is not None
                for value in (
                    normalized_title,
                    normalized_company,
                    normalized_company_id,
                    normalized_digest,
                )
            ):
                raise ValueError("Persisted candidate snapshot is incomplete.")
            return None
        if normalized_title is None or normalized_company is None:
            raise ValueError("Persisted candidate snapshot is incomplete.")
        expected = candidate_snapshot_sha256(
            normalized_job_id,
            normalized_title,
            normalized_company,
            normalized_company_id,
        )
        if normalized_digest is None and allow_missing_digest:
            return expected
        if normalized_digest != expected:
            raise ValueError("Persisted candidate snapshot digest does not match.")
        return normalized_digest

    @staticmethod
    def _upgrade_legacy_application_action(
        action: HumanActionEvent | None,
        *,
        serialization_version: int,
        job_id: str | None,
        job_title: str | None,
        company: str | None,
        company_id: str | None,
        snapshot_digest: str | None,
    ) -> HumanActionEvent | None:
        if (
            action is None
            or action.kind != HumanActionKind.APPROVE_APPLICATION
            or serialization_version >= SQLiteCareerLoopRepository.SERIALIZATION_VERSION
        ):
            return action
        details = dict(action.details)
        if details.get("candidate_snapshot_sha256") is not None:
            return action
        if (
            snapshot_digest is None
            or details.get("job_id") != job_id
            or details.get("job_title") != job_title
            or details.get("company") != company
            or details.get("company_id") != company_id
        ):
            return action
        details["candidate_snapshot_sha256"] = snapshot_digest
        return HumanActionEvent(
            kind=action.kind,
            title=action.title,
            details=details,
        )

    @classmethod
    def _serialize(
        cls, state: CareerLoopState, *, version: int | None = None
    ) -> dict[str, Any]:
        action = state.pending_human_action
        approved_action = state.approved_human_action
        snapshot_digest = cls._validated_candidate_snapshot_digest(
            job_id=state.selected_job_id,
            job_title=state.selected_job_title,
            company=state.selected_company,
            company_id=state.selected_company_id,
            digest=state.selected_job_snapshot_sha256,
            allow_missing_digest=True,
        )
        return {
            "serialization_version": cls.SERIALIZATION_VERSION,
            "run_id": state.run_id,
            "version": state.version if version is None else version,
            "request": {
                "user_id": state.request.user_id,
                "keyword": state.request.keyword,
                "candidate_profile": state.request.candidate_profile,
                "location": state.request.location,
                "sender": state.request.sender,
                "recipient": state.request.recipient,
                "message_subject": state.request.message_subject,
                "message_body": state.request.message_body,
                "schedule_event_id": state.request.schedule_event_id,
            },
            "phase": state.phase.value,
            "selected_job_id": state.selected_job_id,
            "selected_job_title": state.selected_job_title,
            "selected_company": state.selected_company,
            "selected_company_id": state.selected_company_id,
            "selected_job_snapshot_sha256": snapshot_digest,
            "selected_job_relevance_score": state.selected_job_relevance_score,
            "selected_job_outcome_adjustment": state.selected_job_outcome_adjustment,
            "selected_job_score": state.selected_job_score,
            "selected_job_evidence_count": state.selected_job_evidence_count,
            "selected_job_decision_reason": state.selected_job_decision_reason,
            "application_id": state.application_id,
            "application_artifact_content": state.application_artifact_content,
            "application_artifact_sha256": state.application_artifact_sha256,
            "message_id": state.message_id,
            "iterations": state.iterations,
            "pending_human_action": cls._serialize_human_action(action),
            "approved_human_action": cls._serialize_human_action(approved_action),
            "last_error": state.last_error,
            "created_at": state.created_at.isoformat(),
            "updated_at": state.updated_at.isoformat(),
        }

    @classmethod
    def _deserialize(
        cls, value: Any, *, storage_version: int | None = None
    ) -> CareerLoopState:
        if not isinstance(value, dict):
            raise ValueError("Persisted autonomous-loop payload must be an object.")
        serialization_version = value.get("serialization_version")
        if serialization_version not in {1, 2, cls.SERIALIZATION_VERSION}:
            raise ValueError("Unsupported autonomous-loop serialization version.")
        try:
            request_data = value["request"]
            if not isinstance(request_data, dict):
                raise ValueError("Persisted request must be an object.")
            request = CareerLoopRequest(**request_data)
            action = cls._deserialize_human_action(value.get("pending_human_action"))
            approved_action = cls._deserialize_human_action(
                value.get("approved_human_action")
            )
            created_at = datetime.fromisoformat(value["created_at"])
            updated_at = datetime.fromisoformat(value["updated_at"])
            if (
                created_at.tzinfo is None
                or created_at.utcoffset() is None
                or updated_at.tzinfo is None
                or updated_at.utcoffset() is None
            ):
                raise ValueError("Persisted loop timestamps must be timezone-aware.")
            version = value.get(
                "version",
                0 if storage_version is None else storage_version,
            )
            minimum_version = 0 if storage_version is None else 1
            if (
                not isinstance(version, int)
                or isinstance(version, bool)
                or version < minimum_version
                or (storage_version is not None and version != storage_version)
            ):
                raise ValueError("Persisted loop version is malformed.")
            iterations = value["iterations"]
            if not isinstance(iterations, int) or isinstance(iterations, bool) or iterations < 0:
                raise ValueError("Persisted iterations must be a non-negative integer.")
            (
                relevance_score,
                outcome_adjustment,
                adaptive_score,
                evidence_count,
                decision_reason,
            ) = cls._adaptive_snapshot(value)
            selected_job_id = cls._optional_text(value.get("selected_job_id"))
            selected_job_title = cls._optional_text(value.get("selected_job_title"))
            selected_company = cls._optional_text(value.get("selected_company"))
            selected_company_id = cls._optional_text(value.get("selected_company_id"))
            snapshot_digest = cls._validated_candidate_snapshot_digest(
                job_id=selected_job_id,
                job_title=selected_job_title,
                company=selected_company,
                company_id=selected_company_id,
                digest=value.get("selected_job_snapshot_sha256"),
                allow_missing_digest=serialization_version == 1,
            )
            action = cls._upgrade_legacy_application_action(
                action,
                serialization_version=serialization_version,
                job_id=selected_job_id,
                job_title=selected_job_title,
                company=selected_company,
                company_id=selected_company_id,
                snapshot_digest=snapshot_digest,
            )
            approved_action = cls._upgrade_legacy_application_action(
                approved_action,
                serialization_version=serialization_version,
                job_id=selected_job_id,
                job_title=selected_job_title,
                company=selected_company,
                company_id=selected_company_id,
                snapshot_digest=snapshot_digest,
            )
            return CareerLoopState(
                run_id=cls._identifier(value["run_id"], "run_id"),
                request=request,
                phase=CareerLoopPhase(value["phase"]),
                selected_job_id=selected_job_id,
                selected_job_title=selected_job_title,
                selected_company=selected_company,
                selected_company_id=selected_company_id,
                selected_job_snapshot_sha256=snapshot_digest,
                selected_job_relevance_score=relevance_score,
                selected_job_outcome_adjustment=outcome_adjustment,
                selected_job_score=adaptive_score,
                selected_job_evidence_count=evidence_count,
                selected_job_decision_reason=decision_reason,
                application_id=cls._optional_text(value.get("application_id")),
                application_artifact_content=cls._optional_text(
                    value.get("application_artifact_content")
                ),
                application_artifact_sha256=cls._optional_text(
                    value.get("application_artifact_sha256")
                ),
                message_id=cls._optional_text(value.get("message_id")),
                version=version,
                iterations=iterations,
                pending_human_action=action,
                approved_human_action=approved_action,
                last_error=cls._optional_text(value.get("last_error")),
                created_at=created_at,
                updated_at=updated_at,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Persisted autonomous-loop state is malformed.") from exc

    @staticmethod
    def _serialize_human_action(action: HumanActionEvent | None) -> dict[str, Any] | None:
        if action is None:
            return None
        return {
            "kind": action.kind.value,
            "title": action.title,
            "details": dict(action.details),
        }

    @staticmethod
    def _deserialize_human_action(value: Any) -> HumanActionEvent | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("Persisted human action must be an object.")
        details = value.get("details")
        if not isinstance(details, dict):
            raise ValueError("Persisted human-action details must be an object.")
        return HumanActionEvent(
            kind=HumanActionKind(value["kind"]),
            title=value["title"],
            details=details,
        )

    @staticmethod
    def _identifier(value: Any, field: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field} must be text.")
        normalized = value.strip()
        if not normalized or len(normalized) > 200:
            raise ValueError(f"{field} is malformed.")
        return normalized

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Persisted optional text must be text or null.")
        return value

    @staticmethod
    def _optional_number(value: Any) -> float | None:
        if value is None:
            return None
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError("Persisted optional score must be numeric or null.")
        normalized = float(value)
        if not isfinite(normalized):
            raise ValueError("Persisted optional score must be finite.")
        return normalized

    @staticmethod
    def _non_negative_integer(value: Any, field: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"Persisted {field} must be a non-negative integer.")
        return value

    @classmethod
    def _adaptive_snapshot(
        cls, value: dict[str, Any]
    ) -> tuple[float | None, float | None, float | None, int, str | None]:
        fields = (
            "selected_job_relevance_score",
            "selected_job_outcome_adjustment",
            "selected_job_score",
            "selected_job_evidence_count",
            "selected_job_decision_reason",
        )
        present = tuple(field in value for field in fields)
        if not any(present):
            return None, None, None, 0, None
        if not all(present):
            raise ValueError("Persisted adaptive score snapshot is incomplete.")
        raw = tuple(value.get(field) for field in fields)
        # Legacy and pre-search states have no decision snapshot. New pre-search
        # payloads include the keys with the same neutral values.
        if raw[:3] == (None, None, None) and raw[4] is None:
            cls._non_negative_integer(raw[3], fields[3])
            if raw[3] != 0:
                raise ValueError("Neutral adaptive snapshot cannot contain evidence.")
            return None, None, None, 0, None
        if any(item is None for item in raw):
            raise ValueError("Persisted adaptive score snapshot is incomplete.")
        relevance = cls._optional_number(raw[0])
        adjustment = cls._optional_number(raw[1])
        score = cls._optional_number(raw[2])
        evidence = cls._non_negative_integer(raw[3], fields[3])
        reason = cls._optional_text(raw[4])
        if relevance is None or adjustment is None or score is None or not reason:
            raise ValueError("Persisted adaptive score snapshot is incomplete.")
        if not abs(score - (relevance + adjustment)) <= 1e-6:
            raise ValueError("Persisted adaptive score snapshot is inconsistent.")
        if evidence == 0:
            exploration = AdaptiveJobRanker._exploration_adjustment(relevance)
            if adjustment not in (0.0, exploration):
                raise ValueError("Zero-evidence adjustment must be a bounded exploration boost.")
            return relevance, adjustment, score, evidence, reason
        confidence = evidence / (evidence + AdaptiveJobRanker.PRIOR_STRENGTH)
        active_cap = min(
            AdaptiveJobRanker.MAX_ADJUSTMENT,
            max(abs(relevance) * 0.1, 0.01),
        )
        if abs(adjustment) > active_cap * confidence + 1e-6:
            raise ValueError("Adaptive adjustment exceeds its evidence bound.")
        return relevance, adjustment, score, evidence, reason


class SQLiteCandidateRunStatusProjection:
    """Read a minimal owner-scoped run status from an existing SQLite database."""

    def __init__(self, database: SQLiteDatabase) -> None:
        if database.is_memory:
            raise ValueError("Candidate status projection requires durable storage.")
        self._database = database

    def list_page(self, *, user_id: str, limit: int = 25, after: str | None = None,
                  phase: CareerLoopPhase | None = None) -> dict[str, Any]:
        """Return a bounded owner-only keyset page without parsing foreign snapshots."""
        owner = SQLiteCareerLoopRepository._identifier(user_id, "user_id")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        cursor = "" if after is None else SQLiteCareerLoopRepository._identifier(after, "after")
        if phase is not None and not isinstance(phase, CareerLoopPhase):
            raise TypeError("phase must be a CareerLoopPhase or None")
        if phase is None:
            rows = self._database.connection.execute(
                "SELECT run_id, owner_user_id, payload_json, version "
                "FROM autonomous_career_loops "
                "WHERE owner_user_id = ? AND run_id > ? ORDER BY run_id LIMIT ?",
                (owner, cursor, limit + 1),
            ).fetchall()
        else:
            rows = self._database.connection.execute(
                "SELECT run_id, owner_user_id, payload_json, version "
                "FROM autonomous_career_loops "
                "WHERE owner_user_id = ? AND phase = ? AND run_id > ? "
                "ORDER BY run_id LIMIT ?",
                (owner, phase.value, cursor, limit + 1),
            ).fetchall()
        # The filter projection and serialized state are read by one SELECT so a
        # concurrent optimistic transition cannot move the run between two reads.
        items = [self._status_from_row(row[0], row[1:], owner=owner) for row in rows[:limit]]
        if phase is not None and any(item["phase"] != phase.value for item in items):
            raise ValueError("Persisted autonomous-loop phase projection is malformed.")
        return {"schema_version": 1, "items": items,
                "next_after": rows[limit - 1][0] if len(rows) > limit else None}

    def get(self, run_id: str, *, user_id: str) -> dict[str, Any]:
        """Return status after checking trusted ownership metadata before JSON parsing."""
        identifier = SQLiteCareerLoopRepository._identifier(run_id, "run_id")
        owner = SQLiteCareerLoopRepository._identifier(user_id, "user_id")
        row = self._database.connection.execute(
            """
            SELECT owner_user_id, payload_json, version
            FROM autonomous_career_loops
            WHERE run_id = ?
            """,
            (identifier,),
        ).fetchone()
        if row is None or row[0] != owner:
            raise KeyError("Unknown autonomous career loop for this user.")
        return self._status_from_row(identifier, row, owner=owner)

    def get_interview_reconciliation(
        self, run_id: str, *, user_id: str
    ) -> dict[str, Any] | None:
        """Restore an owned pending interview intent only when all durable evidence agrees."""
        identifier = SQLiteCareerLoopRepository._identifier(run_id, "run_id")
        owner = SQLiteCareerLoopRepository._identifier(user_id, "user_id")
        connection = self._database.connection
        if connection.in_transaction:
            raise RuntimeError("Interview intent projection requires a request-local transaction.")
        connection.execute("BEGIN")
        try:
            row = connection.execute(
                """SELECT owner_user_id, payload_json, version
                   FROM autonomous_career_loops WHERE run_id = ?""",
                (identifier,),
            ).fetchone()
            if row is None or row[0] != owner:
                raise KeyError("Unknown autonomous career loop for this user.")

            def unique_object(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError("Persisted JSON contains duplicate members.")
                    value[key] = item
                return value

            def strict_object(raw):
                value = json.loads(
                    raw,
                    object_pairs_hook=unique_object,
                    parse_constant=lambda token: (_ for _ in ()).throw(
                        ValueError(f"Invalid JSON constant: {token}.")
                    ),
                )
                if not isinstance(value, dict):
                    raise ValueError("Persisted JSON must be an object.")
                return value

            try:
                payload = strict_object(row[1])
                state = SQLiteCareerLoopRepository._deserialize(
                    payload, storage_version=row[2]
                )
                if (
                    state.run_id != identifier
                    or state.request.user_id != owner
                    or type(row[2]) is not int
                    or state.version != row[2]
                ):
                    raise ValueError("Persisted interview run identity or version is malformed.")
                if state.phase is not CareerLoopPhase.INTERVIEW_RECONCILIATION:
                    connection.commit()
                    return None
                action = state.pending_human_action
                if (
                    action is None
                    or action.kind is not HumanActionKind.RECONCILE_INTERVIEW_RESPONSE
                    or not isinstance(action.details, Mapping)
                ):
                    raise ValueError("Pending interview reconciliation action is malformed.")
                details = action.details
                raw_operation_id = details.get("operation_id")
                raw_event_id = details.get("event_id")
                operation_id = SQLiteCareerLoopRepository._identifier(
                    raw_operation_id, "operation_id"
                )
                event_id = SQLiteCareerLoopRepository._identifier(raw_event_id, "event_id")
                for raw_identifier in (raw_operation_id, raw_event_id):
                    if raw_identifier != raw_identifier.strip() or any(
                        ord(character) < 32 or ord(character) == 127
                        or 0xD800 <= ord(character) <= 0xDFFF
                        for character in raw_identifier
                    ):
                        raise ValueError("Persisted interview identifier is malformed.")

                employer = details.get("employer")
                local_date = details.get("local_date")
                local_start_time = details.get("local_start_time")
                timezone_name = details.get("timezone")
                if (
                    not isinstance(employer, str)
                    or not employer
                    or len(employer) > 200
                    or employer != employer.strip()
                    or any(ord(character) < 32 or ord(character) == 127
                           or 0xD800 <= ord(character) <= 0xDFFF for character in employer)
                ):
                    raise ValueError("Persisted interview employer is malformed.")
                if (
                    not isinstance(local_date, str)
                    or len(local_date) != 10
                    or date.fromisoformat(local_date).isoformat() != local_date
                ):
                    raise ValueError("Persisted interview date is malformed.")
                if not isinstance(local_start_time, str) or len(local_start_time) > 32:
                    raise ValueError("Persisted interview local time is malformed.")
                parsed_time = time.fromisoformat(local_start_time)
                if parsed_time.tzinfo is not None or parsed_time.isoformat() != local_start_time:
                    raise ValueError("Persisted interview local time is malformed.")
                if (
                    not isinstance(timezone_name, str)
                    or not timezone_name
                    or len(timezone_name) > 100
                    or timezone_name != timezone_name.strip()
                    or any(ord(character) < 32 or ord(character) == 127
                           or 0xD800 <= ord(character) <= 0xDFFF for character in timezone_name)
                ):
                    raise ValueError("Persisted interview timezone is malformed.")
                zone = ZoneInfo(timezone_name)
                application_id = SQLiteCareerLoopRepository._identifier(
                    state.application_id, "application_id"
                )

                operation_row = connection.execute(
                    """SELECT action_type, payload_json, status, result_json
                       FROM external_action_operations WHERE operation_id = ?""",
                    (operation_id,),
                ).fetchone()
                if (
                    operation_row is None
                    or operation_row[0] != "calendar.accept"
                    or operation_row[2] != "reconciliation_required"
                    or operation_row[3] is not None
                ):
                    raise ValueError("Persisted calendar operation does not match pending intent.")
                operation_payload = strict_object(operation_row[1])
                expected_keys = {
                    "event_id", "event_version", "event_start_at", "event_end_at",
                    "event_timezone_name", "event_status",
                }
                if set(operation_payload) != expected_keys:
                    raise ValueError("Persisted calendar operation payload is malformed.")
                event_version = operation_payload["event_version"]
                event_status = operation_payload["event_status"]
                event_start_at = operation_payload["event_start_at"]
                event_end_at = operation_payload["event_end_at"]
                event_timezone = operation_payload["event_timezone_name"]
                if (
                    operation_payload["event_id"] != event_id
                    or type(event_version) is not int
                    or event_version < 1
                    or event_status not in {"proposed", "reschedule_requested"}
                    or not isinstance(event_start_at, str)
                    or (event_end_at is not None and not isinstance(event_end_at, str))
                    or event_timezone != timezone_name
                ):
                    raise ValueError("Persisted calendar operation does not match pending intent.")

                event_row = connection.execute(
                    """SELECT candidate_id, employer_name, event_type, start_at, end_at,
                              timezone_name, status, provider_event_id, application_id,
                              version, active_action_operation_id
                       FROM scheduling_events WHERE event_id = ?""",
                    (event_id,),
                ).fetchone()
                if (
                    event_row is None
                    or event_row[0] != owner
                    or event_row[1] != employer
                    or event_row[2] != "interview"
                    or event_row[3] != event_start_at
                    or event_row[4] != event_end_at
                    or event_row[5] != timezone_name
                    or event_row[6] != event_status
                    or event_row[7] is not None
                    or event_row[8] != application_id
                    or event_row[9] != event_version
                    or event_row[10] != operation_id
                ):
                    raise ValueError("Persisted schedule event does not match pending operation.")

                start = datetime.fromisoformat(event_start_at)
                if start.tzinfo is None or start.utcoffset() is None:
                    raise ValueError("Persisted interview start must be timezone-aware.")
                local_start = start.astimezone(zone)
                if (
                    local_start.date().isoformat() != local_date
                    or local_start.time().isoformat() != local_start_time
                ):
                    raise ValueError("Persisted interview local time does not match event.")
                if event_end_at is not None:
                    end = datetime.fromisoformat(event_end_at)
                    if end.tzinfo is None or end.utcoffset() is None or end <= start:
                        raise ValueError("Persisted interview end is malformed.")

                result = {
                    "schema_version": 1,
                    "run_id": identifier,
                    "state_version": state.version,
                    "event_id": event_id,
                    "employer": employer,
                    "local_date": local_date,
                    "local_start_time": local_start_time,
                    "timezone": timezone_name,
                }
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(
                    "Persisted candidate interview reconciliation is malformed."
                ) from exc
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _status_from_row(identifier: str, row: tuple[Any, ...], *, owner: str) -> dict[str, Any]:
        """Validate one owner-filtered storage row into the public projection."""
        if row[0] != owner:
            raise KeyError("Unknown autonomous career loop for this user.")
        try:
            payload = json.loads(row[1])
            if not isinstance(payload, dict):
                raise ValueError("status payload must be an object")
            phase = CareerLoopPhase(payload["phase"])
            payload_version = payload["version"]
            if (
                not isinstance(payload_version, int)
                or isinstance(payload_version, bool)
                or payload_version != row[2]
            ):
                raise ValueError("status version is malformed")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Persisted autonomous-loop status is malformed.") from exc
        return {
            "schema_version": 1,
            "run_id": identifier,
            "state_version": payload_version,
            "phase": phase.value,
            "waiting_for_human": payload.get("pending_human_action") is not None,
            "terminal": phase in {CareerLoopPhase.COMPLETE, CareerLoopPhase.FAILED},
        }
