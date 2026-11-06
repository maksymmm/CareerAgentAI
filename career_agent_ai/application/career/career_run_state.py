from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from career_agent_ai.application.career.career_plan import CareerPlan
from career_agent_ai.application.career.career_step_result import CareerStepResult
from career_agent_ai.application.workflow.workflow_engine import WorkflowEngine


@dataclass
class CareerRunState:
    """Mutable execution state retained while a career run awaits continuation."""

    run_id: str
    user_id: str
    objective: str
    plan: CareerPlan
    payload: dict[str, Any]
    workflow_engine: WorkflowEngine
    steps: list[CareerStepResult] = field(default_factory=list)
    version: int = 0

    def __post_init__(self) -> None:
        """Validate the optimistic persistence version."""
        if (
            not isinstance(self.version, int)
            or isinstance(self.version, bool)
            or self.version < 0
        ):
            raise ValueError("Career run version must be a non-negative integer.")

    def add_step(self, result: CareerStepResult) -> None:
        """Append one completed execution result to the run state."""
        self.steps.append(result)
