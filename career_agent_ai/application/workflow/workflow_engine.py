from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

from .workflow import Workflow
from .workflow_result import WorkflowResult
from .workflow_state import WorkflowState
from .workflow_step import WorkflowStep


WorkflowStepExecutor = Callable[
    [WorkflowStep, Workflow],
    Any,
]


class WorkflowEngine:
    """Execute and coordinate immutable workflows."""

    def __init__(self, executor: WorkflowStepExecutor | None = None) -> None:
        self._workflow: Workflow | None = None
        self._executor = executor
        self._step_results: dict[str, Any] = {}

    @property
    def workflow(self) -> Workflow | None:
        """Return the current workflow, if one exists."""
        return self._workflow

    @property
    def is_running(self) -> bool:
        """Return whether the current workflow is running."""
        return (
            self._workflow is not None
            and self._workflow.status == WorkflowState.RUNNING
        )

    @property
    def is_finished(self) -> bool:
        """Return whether the current workflow has reached a terminal state."""
        return self._workflow is not None and self._workflow.is_finished()

    def start(self, workflow: Workflow) -> Workflow:
        """Start a workflow and normalize every step to pending."""
        if self._workflow is not None and not self._workflow.is_finished():
            raise RuntimeError("Workflow is already running.")

        self._step_results.clear()
        if not workflow.steps:
            self._workflow = replace(
                workflow,
                status=WorkflowState.COMPLETED,
                current_step=0,
            )
            return self._workflow

        normalized_steps = tuple(
            replace(step, status=WorkflowState.PENDING)
            for step in workflow.steps
        )
        self._workflow = replace(
            workflow,
            status=WorkflowState.RUNNING,
            current_step=0,
            steps=normalized_steps,
        )
        return self._workflow

    def current_step(self) -> WorkflowStep:
        """Return the currently selected workflow step."""
        workflow = self._require_workflow()
        self._require_running(workflow)
        if workflow.current_step >= len(workflow.steps):
            raise RuntimeError("Workflow already finished.")
        return workflow.steps[workflow.current_step]

    def execute_step(self) -> Any:
        """Execute the selected step and store its result."""
        workflow = self._require_workflow()
        self._require_running(workflow)
        if workflow.current_step >= len(workflow.steps):
            raise RuntimeError("Workflow already finished.")

        step = workflow.steps[workflow.current_step]
        if step.status == WorkflowState.COMPLETED:
            return self._step_results.get(step.id)

        running_step = replace(step, status=WorkflowState.RUNNING)
        self._replace_current_step(running_step)
        try:
            result = (
                running_step.task
                if self._executor is None
                else self._executor(running_step, self._require_workflow())
            )
        except Exception:
            self._replace_current_step(
                replace(running_step, status=WorkflowState.FAILED)
            )
            self._workflow = replace(
                self._require_workflow(),
                status=WorkflowState.FAILED,
            )
            raise

        self._replace_current_step(
            replace(running_step, status=WorkflowState.COMPLETED)
        )
        self._step_results[step.id] = result
        return result

    def complete_step(self) -> Workflow:
        """Mark the selected step complete and advance to the next one."""
        workflow = self._require_workflow()
        self._require_running(workflow)
        if workflow.current_step >= len(workflow.steps):
            raise RuntimeError("No current step.")

        steps = list(workflow.steps)
        steps[workflow.current_step] = replace(
            steps[workflow.current_step],
            status=WorkflowState.COMPLETED,
        )
        next_step_index = workflow.current_step + 1
        self._workflow = replace(
            workflow,
            steps=tuple(steps),
            current_step=next_step_index,
            status=(
                WorkflowState.COMPLETED
                if next_step_index >= len(steps)
                else WorkflowState.RUNNING
            ),
        )
        return self._workflow

    def next_step(self) -> Workflow:
        """Move the workflow cursor to the next step.

        The method preserves the historical workflow contract used by the
        project: callers may move the cursor without executing the current
        task. Autonomous execution should use ``execute_step`` followed by
        ``next_step`` or use ``complete_step`` when completion is externally
        controlled.
        """
        workflow = self._require_workflow()
        self._require_running(workflow)
        if workflow.current_step >= len(workflow.steps):
            raise RuntimeError("Workflow already finished.")

        next_step_index = workflow.current_step + 1
        if next_step_index >= len(workflow.steps):
            self._workflow = replace(
                workflow,
                current_step=next_step_index,
                status=WorkflowState.COMPLETED,
            )
            return self._workflow

        self._workflow = replace(
            workflow,
            current_step=next_step_index,
            status=WorkflowState.RUNNING,
        )
        return self._workflow

    def run(self) -> Workflow:
        """Execute every remaining workflow step through the configured executor."""
        workflow = self._require_workflow()
        self._require_running(workflow)
        while self.is_running:
            self.execute_step()
            if self._workflow is None:
                break
            if self._workflow.current_step >= len(self._workflow.steps):
                self._workflow = replace(
                    self._workflow,
                    status=WorkflowState.COMPLETED,
                )
                break
            self.next_step()
        return self._require_workflow()

    def fail_step(self) -> Workflow:
        """Mark the selected step and workflow as failed."""
        workflow = self._require_workflow()
        self._require_running(workflow)
        if workflow.current_step >= len(workflow.steps):
            raise RuntimeError("No current step.")

        steps = list(workflow.steps)
        steps[workflow.current_step] = replace(
            steps[workflow.current_step],
            status=WorkflowState.FAILED,
        )
        self._workflow = replace(
            workflow,
            steps=tuple(steps),
            status=WorkflowState.FAILED,
        )
        return self._workflow

    def pause(self) -> Workflow:
        """Pause a running workflow."""
        workflow = self._require_workflow()
        self._require_running(workflow)
        self._workflow = replace(workflow, status=WorkflowState.PAUSED)
        return self._workflow

    def resume(self) -> Workflow:
        """Resume a paused workflow."""
        workflow = self._require_workflow()
        if workflow.status != WorkflowState.PAUSED:
            raise RuntimeError("Only a paused workflow can be resumed.")
        self._workflow = replace(workflow, status=WorkflowState.RUNNING)
        return self._workflow

    def cancel(self) -> Workflow:
        """Cancel the current non-terminal workflow."""
        workflow = self._require_workflow()
        if workflow.is_finished():
            raise RuntimeError("Workflow is already finished.")
        self._workflow = replace(workflow, status=WorkflowState.CANCELLED)
        return self._workflow

    def step_result(self, step_id: str) -> Any:
        """Return the stored result for a completed step."""
        if step_id not in self._step_results:
            raise KeyError(f"No result exists for workflow step '{step_id}'.")
        return self._step_results[step_id]

    def snapshot(self) -> WorkflowResult:
        """Return an immutable workflow execution snapshot."""
        workflow = self._require_workflow()
        completed_steps = sum(
            1
            for step in workflow.steps
            if step.status == WorkflowState.COMPLETED
        )
        failed_step = next(
            (
                step.id
                for step in workflow.steps
                if step.status == WorkflowState.FAILED
            ),
            None,
        )
        return WorkflowResult(
            workflow_id=workflow.workflow_id,
            success=workflow.status == WorkflowState.COMPLETED,
            completed_steps=completed_steps,
            failed_step=failed_step,
            execution_time=0.0,
            metadata={
                "status": workflow.status.value,
                "total_steps": len(workflow.steps),
                "current_step": workflow.current_step,
                "step_results": dict(self._step_results),
            },
        )

    def reset(self) -> None:
        """Clear the current workflow and all execution results."""
        self._workflow = None
        self._step_results.clear()

    def _require_workflow(self) -> Workflow:
        if self._workflow is None:
            raise RuntimeError("No active workflow.")
        return self._workflow

    @staticmethod
    def _require_running(workflow: Workflow) -> None:
        if workflow.status != WorkflowState.RUNNING:
            raise RuntimeError("Workflow is not running.")

    def _replace_current_step(self, step: WorkflowStep) -> None:
        workflow = self._require_workflow()
        steps = list(workflow.steps)
        steps[workflow.current_step] = step
        self._workflow = replace(workflow, steps=tuple(steps))
