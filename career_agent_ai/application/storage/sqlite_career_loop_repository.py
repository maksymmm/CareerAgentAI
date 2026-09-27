"""SQLite persistence for end-to-end autonomous career-loop state."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopPhase,
    CareerLoopRequest,
    CareerLoopState,
    HumanActionEvent,
    HumanActionKind,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


class SQLiteCareerLoopRepository:
    """Strict JSON, restart-safe persistence for one active career loop."""

    SERIALIZATION_VERSION = 1

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database
        self._create_schema()

    def save(self, state: CareerLoopState) -> None:
        """Create or replace one validated active-loop snapshot."""
        payload = self._serialize(state)
        self._database.connection.execute(
            """
            INSERT INTO autonomous_career_loops (run_id, payload_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (
                state.run_id,
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                state.updated_at.isoformat(),
            ),
        )
        self._database.connection.commit()

    def get(self, run_id: str) -> CareerLoopState | None:
        """Return one persisted active loop or None."""
        run_id = self._identifier(run_id, "run_id")
        row = self._database.connection.execute(
            "SELECT payload_json FROM autonomous_career_loops WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row[0])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Persisted autonomous-loop JSON is malformed.") from exc
        state = self._deserialize(value)
        if state.run_id != run_id:
            raise ValueError("Persisted autonomous-loop run_id does not match its storage key.")
        return state

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
                updated_at TEXT NOT NULL
            )
            """
        )
        self._database.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_autonomous_loop_updated
            ON autonomous_career_loops(updated_at, run_id)
            """
        )
        self._database.connection.commit()

    @classmethod
    def _serialize(cls, state: CareerLoopState) -> dict[str, Any]:
        action = state.pending_human_action
        approved_action = state.approved_human_action
        return {
            "serialization_version": cls.SERIALIZATION_VERSION,
            "run_id": state.run_id,
            "request": {
                "user_id": state.request.user_id,
                "keyword": state.request.keyword,
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
    def _deserialize(cls, value: Any) -> CareerLoopState:
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
