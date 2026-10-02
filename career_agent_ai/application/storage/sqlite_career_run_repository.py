from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from career_agent_ai.application.career.career_plan import CareerPlan, CareerPlanStep
from career_agent_ai.application.career.career_run_repository import (
    CareerRunConflictError,
    CareerRunRepository,
)
from career_agent_ai.application.career.career_run_state import CareerRunState
from career_agent_ai.application.career.career_step_result import CareerStepResult
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.workflow.workflow import Workflow
from career_agent_ai.application.workflow.workflow_engine import WorkflowEngine
from career_agent_ai.application.workflow.workflow_state import WorkflowState
from career_agent_ai.application.workflow.workflow_step import WorkflowStep


class SQLiteCareerRunRepository(CareerRunRepository):
    """SQLite-backed persistence for resumable career runs."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database
        self._create_schema()

    @property
    def supports_background_lease_renewal(self) -> bool:
        """Use a separate SQLite connection for file-backed lease heartbeats."""
        return self._database.path != ":memory:"

    def save(self, state: CareerRunState, *, lease_owner: str | None = None) -> None:
        """Persist one run snapshot with optimistic version and lease fencing."""
        workflow = state.workflow_engine.workflow
        if workflow is None:
            raise ValueError("Career run must contain a workflow before it can be saved.")

        plan_json = self._dump(self._plan_to_dict(state.plan))
        payload_json = self._dump(state.payload)
        workflow_json = self._dump(self._workflow_to_dict(workflow))
        steps_json = self._dump(
            [self._step_result_to_dict(item) for item in state.steps]
        )
        updated_at = datetime.now(timezone.utc).isoformat()
        connection = self._database.connection
        next_version = 1 if state.version == 0 else state.version + 1
        try:
            if state.version == 0:
                connection.execute(
                    """
                    INSERT INTO career_runs (
                        run_id, user_id, objective, plan_json, payload_json,
                        workflow_json, steps_json, updated_at, version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.run_id,
                        state.user_id,
                        state.objective,
                        plan_json,
                        payload_json,
                        workflow_json,
                        steps_json,
                        updated_at,
                        next_version,
                    ),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE career_runs
                    SET user_id = ?, objective = ?, plan_json = ?, payload_json = ?,
                        workflow_json = ?, steps_json = ?, updated_at = ?, version = ?
                    WHERE run_id = ? AND version = ?
                      AND (lease_owner IS NULL OR lease_owner = ?)
                    """,
                    (
                        state.user_id,
                        state.objective,
                        plan_json,
                        payload_json,
                        workflow_json,
                        steps_json,
                        updated_at,
                        next_version,
                        state.run_id,
                        state.version,
                        lease_owner,
                    ),
                )
                if cursor.rowcount != 1:
                    raise CareerRunConflictError(
                        "Career run snapshot changed or is leased by another worker."
                    )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            if connection.in_transaction:
                connection.rollback()
            raise CareerRunConflictError(
                "Career run snapshot already exists with another version."
            ) from exc
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise

        state.version = next_version

    def get(self, run_id: str) -> CareerRunState | None:
        """Load one persisted run and rebuild its workflow engine."""
        normalized = run_id.strip()
        if not normalized:
            raise ValueError("run_id must not be empty.")

        row = self._database.connection.execute(
            """
            SELECT run_id, user_id, objective, plan_json, payload_json,
                   workflow_json, steps_json, version
            FROM career_runs
            WHERE run_id = ?
            """,
            (normalized,),
        ).fetchone()
        if row is None:
            return None

        return self._state_from_row(row)

    def acquire_lease(
        self, run_id: str, owner_id: str, *, ttl_seconds: int
    ) -> CareerRunState:
        """Acquire or reclaim an execution lease without a post-commit reread."""
        normalized_run = self._normalize_token(run_id, "run_id")
        normalized_owner = self._normalize_token(owner_id, "owner_id")
        self._validate_ttl(ttl_seconds)
        connection = self._database.connection
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT run_id, user_id, objective, plan_json, payload_json,
                       workflow_json, steps_json, version,
                       lease_owner, lease_expires_at
                FROM career_runs WHERE run_id = ?
                """,
                (normalized_run,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown career run '{normalized_run}'.")
            current_owner = row[8]
            raw_expiry = row[9]
            if current_owner is not None and current_owner != normalized_owner:
                if raw_expiry is None:
                    raise CareerRunConflictError(
                        "Career run has a malformed active execution lease."
                    )
                expiry = datetime.fromisoformat(str(raw_expiry))
                if expiry.tzinfo is None or expiry.utcoffset() is None:
                    raise CareerRunConflictError(
                        "Career run has a malformed active execution lease."
                    )
                if expiry > now:
                    raise CareerRunConflictError(
                        "Career run is actively leased by another worker."
                    )
            cursor = connection.execute(
                """
                UPDATE career_runs
                SET lease_owner = ?, lease_expires_at = ?
                WHERE run_id = ?
                  AND (
                    lease_owner IS NULL
                    OR lease_owner = ?
                    OR lease_expires_at <= ?
                  )
                """,
                (
                    normalized_owner,
                    expires_at,
                    normalized_run,
                    normalized_owner,
                    now.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                raise CareerRunConflictError(
                    "Career run execution lease changed in another worker."
                )
            state = self._state_from_row(row)
            connection.commit()
            return state
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise

    def renew_lease(
        self, run_id: str, owner_id: str, *, ttl_seconds: int
    ) -> None:
        """Extend one execution lease; file-backed SQLite uses its own connection."""
        normalized_run = self._normalize_token(run_id, "run_id")
        normalized_owner = self._normalize_token(owner_id, "owner_id")
        self._validate_ttl(ttl_seconds)
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        ).isoformat()
        if self._database.path == ":memory:":
            connection = self._database.connection
            close_connection = False
        else:
            connection = sqlite3.connect(self._database.path, timeout=5)
            close_connection = True
        try:
            cursor = connection.execute(
                """
                UPDATE career_runs
                SET lease_expires_at = ?
                WHERE run_id = ? AND lease_owner = ?
                """,
                (expires_at, normalized_run, normalized_owner),
            )
            connection.commit()
            if cursor.rowcount != 1:
                row = connection.execute(
                    "SELECT lease_owner FROM career_runs WHERE run_id = ?",
                    (normalized_run,),
                ).fetchone()
                if row is None:
                    return
                raise CareerRunConflictError(
                    "Career run execution lease is no longer owned by this worker."
                )
        finally:
            if close_connection:
                connection.close()

    def release_lease(self, run_id: str, owner_id: str) -> None:
        """Release an owned execution lease; missing terminal rows are harmless."""
        normalized_run = self._normalize_token(run_id, "run_id")
        normalized_owner = self._normalize_token(owner_id, "owner_id")
        connection = self._database.connection
        cursor = connection.execute(
            """
            UPDATE career_runs
            SET lease_owner = NULL, lease_expires_at = NULL
            WHERE run_id = ? AND lease_owner = ?
            """,
            (normalized_run, normalized_owner),
        )
        connection.commit()
        if cursor.rowcount == 1:
            return
        row = connection.execute(
            "SELECT lease_owner FROM career_runs WHERE run_id = ?",
            (normalized_run,),
        ).fetchone()
        if row is None:
            return
        if row[0] is not None:
            raise CareerRunConflictError(
                "Career run execution lease is owned by another worker."
            )

    def delete(self, run_id: str, *, lease_owner: str | None = None) -> None:
        """Delete one persisted career run while respecting active lease ownership."""
        normalized = self._normalize_token(run_id, "run_id")
        cursor = self._database.connection.execute(
            """
            DELETE FROM career_runs
            WHERE run_id = ? AND (lease_owner IS NULL OR lease_owner = ?)
            """,
            (normalized, lease_owner),
        )
        self._database.connection.commit()
        if cursor.rowcount == 0:
            row = self._database.connection.execute(
                "SELECT 1 FROM career_runs WHERE run_id = ?",
                (normalized,),
            ).fetchone()
            if row is not None:
                raise CareerRunConflictError(
                    "Career run is leased by another worker."
                )

    def _create_schema(self) -> None:
        connection = self._database.connection
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS career_runs (
                run_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                objective TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                workflow_json TEXT NOT NULL,
                steps_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                version INTEGER NOT NULL CHECK(version >= 1),
                lease_owner TEXT,
                lease_expires_at TEXT
            )
            """
        )
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(career_runs)").fetchall()
        }
        if "version" not in columns:
            connection.execute(
                "ALTER TABLE career_runs ADD COLUMN version INTEGER NOT NULL DEFAULT 1"
            )
        if "lease_owner" not in columns:
            connection.execute(
                "ALTER TABLE career_runs ADD COLUMN lease_owner TEXT"
            )
        if "lease_expires_at" not in columns:
            connection.execute(
                "ALTER TABLE career_runs ADD COLUMN lease_expires_at TEXT"
            )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_career_runs_user_id
            ON career_runs(user_id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_career_runs_lease_expiry
            ON career_runs(lease_expires_at)
            """
        )
        connection.commit()

    def _state_from_row(self, row: tuple[Any, ...]) -> CareerRunState:
        """Rebuild a validated run state from the durable snapshot columns."""
        engine = WorkflowEngine()
        engine.restore(self._workflow_from_dict(self._load(row[5])))
        return CareerRunState(
            run_id=row[0],
            user_id=row[1],
            objective=row[2],
            plan=self._plan_from_dict(self._load(row[3])),
            payload=dict(self._load(row[4])),
            workflow_engine=engine,
            steps=[
                self._step_result_from_dict(item)
                for item in self._load(row[6])
            ],
            version=int(row[7]),
        )

    @staticmethod
    def _normalize_token(value: str, field: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{field} must be text.")
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field} must not be empty.")
        if len(normalized) > 200:
            raise ValueError(f"{field} must not exceed 200 characters.")
        return normalized

    @staticmethod
    def _validate_ttl(ttl_seconds: int) -> None:
        if (
            not isinstance(ttl_seconds, int)
            or isinstance(ttl_seconds, bool)
            or ttl_seconds <= 0
            or ttl_seconds > 3600
        ):
            raise ValueError("ttl_seconds must be an integer from 1 to 3600.")

    @staticmethod
    def _dump(value: Any) -> str:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Career run state must contain JSON-serializable values."
            ) from exc

    @staticmethod
    def _load(value: str) -> Any:
        return json.loads(
            value,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"Invalid JSON constant: {token}.")
            ),
        )

    @staticmethod
    def _plan_to_dict(plan: CareerPlan) -> dict[str, Any]:
        return {
            "objective": plan.objective,
            "steps": [
                {
                    "id": step.id,
                    "action": step.action,
                    "description": step.description,
                    "required": step.required,
                }
                for step in plan.steps
            ],
        }

    @staticmethod
    def _plan_from_dict(data: dict[str, Any]) -> CareerPlan:
        return CareerPlan(
            objective=str(data["objective"]),
            steps=tuple(
                CareerPlanStep(
                    id=str(item["id"]),
                    action=str(item["action"]),
                    description=str(item["description"]),
                    required=bool(item["required"]),
                )
                for item in data["steps"]
            ),
        )

    @staticmethod
    def _workflow_to_dict(workflow: Workflow) -> dict[str, Any]:
        return {
            "workflow_id": workflow.workflow_id,
            "name": workflow.name,
            "description": workflow.description,
            "created_at": workflow.created_at.isoformat(),
            "current_step": workflow.current_step,
            "status": workflow.status.value,
            "steps": [
                {
                    "id": step.id,
                    "name": step.name,
                    "order": step.order,
                    "task": step.task,
                    "status": step.status.value,
                }
                for step in workflow.steps
            ],
        }

    @staticmethod
    def _workflow_from_dict(data: dict[str, Any]) -> Workflow:
        return Workflow(
            workflow_id=str(data["workflow_id"]),
            name=str(data["name"]),
            description=str(data["description"]),
            created_at=datetime.fromisoformat(str(data["created_at"])),
            current_step=int(data["current_step"]),
            status=WorkflowState(str(data["status"])),
            steps=tuple(
                WorkflowStep(
                    id=str(item["id"]),
                    name=str(item["name"]),
                    order=int(item["order"]),
                    task=str(item["task"]),
                    status=WorkflowState(str(item["status"])),
                )
                for item in data["steps"]
            ),
        )

    @staticmethod
    def _step_result_to_dict(result: CareerStepResult) -> dict[str, Any]:
        return {
            "step_id": result.step_id,
            "action": result.action,
            "success": result.success,
            "messages": list(result.messages),
            "metadata": dict(result.metadata),
        }

    @staticmethod
    def _step_result_from_dict(data: dict[str, Any]) -> CareerStepResult:
        return CareerStepResult(
            step_id=str(data["step_id"]),
            action=str(data["action"]),
            success=bool(data["success"]),
            messages=tuple(str(item) for item in data.get("messages", [])),
            metadata=dict(data.get("metadata", {})),
        )
