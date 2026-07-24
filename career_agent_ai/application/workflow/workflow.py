from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Tuple

from .workflow_state import WorkflowState
from .workflow_step import WorkflowStep


@dataclass(frozen=True)
class Workflow:
    """
    Immutable workflow execution plan.
    """

    workflow_id: str
    name: str
    description: str

    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    current_step: int = 0

    status: WorkflowState = WorkflowState.PENDING

    steps: Tuple[WorkflowStep, ...] = field(default_factory=tuple)

    def total_steps(self) -> int:
        return len(self.steps)

    def is_finished(self) -> bool:
        return self.status in (
            WorkflowState.COMPLETED,
            WorkflowState.FAILED,
            WorkflowState.CANCELLED,
        )