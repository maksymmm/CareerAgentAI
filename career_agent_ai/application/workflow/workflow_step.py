from __future__ import annotations

from dataclasses import dataclass

from .workflow_state import WorkflowState


@dataclass(frozen=True)
class WorkflowStep:
    """
    Represents exactly one workflow action.
    """

    id: str
    name: str
    order: int
    task: str
    status: WorkflowState = WorkflowState.PENDING