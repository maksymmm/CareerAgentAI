"""SQLite persistence for end-to-end autonomous career-loop state."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopPhase,
    CareerLoopRequest,
    CareerLoopState,
    HumanActionEvent,
    HumanActionKind,
)
from career_agent_ai.application.career.autonomous_loop_repository import CareerLoopConflictError
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


class SQLiteCareerLoopRepository:
    """Strict JSON, restart-safe persistence for one active career loop."""

    SERIALIZATION_VERSION = 1

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
                        run_id, payload_json, updated_at, version
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        state.run_id,
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
                        SET payload_json = ?, updated_at = ?, version = ?
                        WHERE run_id = ? AND version = ?
                          AND execution_claim_owner IS NULL
                        """,
                        (
                            serialized,
                            state.updated_at.isoformat(),
                            next_version,
                            state.run_id,
                            current_version,
                        ),
                    )
                else:
                    cursor = connection.execute(
                        """
                        UPDATE autonomous_career_loops
                        SET payload_json = ?, updated_at = ?, version = ?
                        WHERE run_id = ? AND version = ?
                          AND execution_claim_owner = ?
                        """,
                        (
                            serialized,
                            state.updated_at.isoformat(),
                            next_version,
                            state.run_id,
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
        if self._is_private_memory_path(self._database.path):
            return

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=lease_seconds)
        connection = sqlite3.connect(self._database.path, timeout=5)
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


    @staticmethod
    def _is_private_memory_path(value: str) -> bool:
        """Return whether SQLite actually opens value as a private in-memory database."""
        raw = value.strip()
        if raw == ":memory:":
            return True
        # SQLite URI filenames require the case-sensitive file: prefix.
        # FILE::MEMORY: is therefore a normal filesystem filename.
        if not raw.startswith("file:"):
            return False
        uri = raw[5:]
        normalized_uri = uri.lower()
        if normalized_uri.startswith(":memory:"):
            return True
        query = normalized_uri.partition("?")[2]
        return any(
            key == "mode" and setting == "memory"
            for part in query.split("&")
            if part
            for key, _, setting in (part.partition("="),)
        )

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
        self._database.connection.commit()

    @classmethod
    def _serialize(
        cls, state: CareerLoopState, *, version: int | None = None
    ) -> dict[str, Any]:
        action = state.pending_human_action
        approved_action = state.approved_human_action
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
        if value.get("serialization_version") != cls.SERIALIZATION_VERSION:
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
            return CareerLoopState(
                run_id=cls._identifier(value["run_id"], "run_id"),
                request=request,
                phase=CareerLoopPhase(value["phase"]),
                selected_job_id=cls._optional_text(value.get("selected_job_id")),
                selected_job_title=cls._optional_text(value.get("selected_job_title")),
                selected_company=cls._optional_text(value.get("selected_company")),
                selected_company_id=cls._optional_text(value.get("selected_company_id")),
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
