from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from career_agent_ai.application.career.career_plan import CareerPlan, CareerPlanStep
from career_agent_ai.application.career.career_run_repository import CareerRunRepository
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

    def save(self, state: CareerRunState) -> None:
        """Persist the complete state required to resume a career run."""
        workflow = state.workflow_engine.workflow
        if workflow is None:
            raise ValueError("Career run must contain a workflow before it can be saved.")

        connection = self._database.connection
        connection.execute(
            """
            INSERT INTO career_runs (
                run_id, user_id, objective, plan_json, payload_json,
                workflow_json, steps_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                user_id = excluded.user_id,
                objective = excluded.objective,
                plan_json = excluded.plan_json,
                payload_json = excluded.payload_json,
                workflow_json = excluded.workflow_json,
                steps_json = excluded.steps_json,
                updated_at = excluded.updated_at
            """,
            (
                state.run_id,
                state.user_id,
                state.objective,
                self._dump(self._plan_to_dict(state.plan)),
                self._dump(state.payload),
                self._dump(self._workflow_to_dict(workflow)),
                self._dump([self._step_result_to_dict(item) for item in state.steps]),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()

    def get(self, run_id: str) -> CareerRunState | None:
        """Load one persisted run and rebuild its workflow engine."""
        normalized = run_id.strip()
        if not normalized:
            raise ValueError("run_id must not be empty.")

        row = self._database.connection.execute(
            """
            SELECT run_id, user_id, objective, plan_json, payload_json,
                   workflow_json, steps_json
            FROM career_runs
            WHERE run_id = ?
            """,
            (normalized,),
        ).fetchone()
        if row is None:
            return None

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
        )

    def delete(self, run_id: str) -> None:
        """Delete one persisted career run."""
        normalized = run_id.strip()
        if not normalized:
            raise ValueError("run_id must not be empty.")
        self._database.connection.execute(
            "DELETE FROM career_runs WHERE run_id = ?",
            (normalized,),
        )
        self._database.connection.commit()

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
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_career_runs_user_id
            ON career_runs(user_id)
            """
        )
        connection.commit()

    @staticmethod
    def _dump(value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Career run state must contain JSON-serializable values."
            ) from exc

    @staticmethod
    def _load(value: str) -> Any:
        return json.loads(value)

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
