from __future__ import annotations

from dataclasses import replace

from .workflow import Workflow
from .workflow_result import WorkflowResult
from .workflow_state import WorkflowState


class WorkflowEngine:
    """
    Coordinates workflow execution.

    Contains no business logic.
    """

    def __init__(self) -> None:
        self._workflow: Workflow | None = None

    @property
    def workflow(self) -> Workflow | None:
        return self._workflow

    def start(self, workflow: Workflow) -> Workflow:
        """
        Starts a workflow.
        """

        if self._workflow is not None:
            raise RuntimeError("Workflow is already running.")

        self._workflow = replace(
            workflow,
            status=WorkflowState.RUNNING,
        )

        return self._workflow

    def next_step(self) -> Workflow:
        """
        Moves workflow to the next step.
        """

        if self._workflow is None:
            raise RuntimeError("No active workflow.")

        if self._workflow.current_step >= len(self._workflow.steps):
            raise RuntimeError("Workflow already finished.")

        self._workflow = replace(
            self._workflow,
            current_step=self._workflow.current_step + 1,
        )

        return self._workflow

    def complete_step(self) -> Workflow:
        """
        Completes the current workflow.
        """

        if self._workflow is None:
            raise RuntimeError("No active workflow.")

        if self._workflow.current_step >= len(self._workflow.steps):
            raise RuntimeError("No current step.")

        if self._workflow.current_step == len(self._workflow.steps) - 1:
            self._workflow = replace(
                self._workflow,
                status=WorkflowState.COMPLETED,
            )

        return self._workflow

    def fail_step(self) -> Workflow:
        """
        Marks the workflow as failed.
        """

        if self._workflow is None:
            raise RuntimeError("No active workflow.")

        self._workflow = replace(
            self._workflow,
            status=WorkflowState.FAILED,
        )

        return self._workflow

    def pause(self) -> Workflow:
        """
        Pauses the workflow.
        """

        if self._workflow is None:
            raise RuntimeError("No active workflow.")

        self._workflow = replace(
            self._workflow,
            status=WorkflowState.PAUSED,
        )

        return self._workflow

    def resume(self) -> Workflow:
        """
        Resumes the workflow.
        """

        if self._workflow is None:
            raise RuntimeError("No active workflow.")

        self._workflow = replace(
            self._workflow,
            status=WorkflowState.RUNNING,
        )

        return self._workflow

    def cancel(self) -> Workflow:
        """
        Cancels the workflow.
        """

        if self._workflow is None:
            raise RuntimeError("No active workflow.")

        self._workflow = replace(
            self._workflow,
            status=WorkflowState.CANCELLED,
        )

        return self._workflow

    def snapshot(self) -> WorkflowResult:
        """
        Returns an immutable snapshot of the workflow.
        """

        if self._workflow is None:
            raise RuntimeError("No active workflow.")

        return WorkflowResult(
            workflow_id=self._workflow.workflow_id,
            success=self._workflow.status == WorkflowState.COMPLETED,
            completed_steps=self._workflow.current_step,
            failed_step=None,
            execution_time=0.0,
            metadata={
                "status": self._workflow.status.value,
                "total_steps": len(self._workflow.steps),
            },
        )