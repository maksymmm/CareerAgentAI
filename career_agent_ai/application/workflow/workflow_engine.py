from __future__ import annotations

from dataclasses import replace
from typing import Callable, Any

from .workflow import Workflow
from .workflow_result import WorkflowResult
from .workflow_state import WorkflowState
from .workflow_step import WorkflowStep


WorkflowStepExecutor = Callable[
    [WorkflowStep, Workflow],
    Any,
]


class WorkflowEngine:
    """
    Executes and coordinates immutable workflows.

    The engine owns workflow lifecycle state while keeping actual business
    logic outside of the workflow layer.

    A WorkflowStep contains a task identifier. An optional executor can
    resolve that task identifier into an actual application action.
    """

    def __init__(
        self,
        executor: WorkflowStepExecutor | None = None,
    ) -> None:
        self._workflow: Workflow | None = None
        self._executor = executor
        self._step_results: dict[str, Any] = {}

    @property
    def workflow(self) -> Workflow | None:
        return self._workflow

    @property
    def is_running(self) -> bool:
        return (
            self._workflow is not None
            and self._workflow.status == WorkflowState.RUNNING
        )

    @property
    def is_finished(self) -> bool:
        return (
            self._workflow is not None
            and self._workflow.is_finished()
        )

    def start(self, workflow: Workflow) -> Workflow:
        """
        Starts a new workflow.

        A workflow can only be started when there is no active workflow.
        """

        if self._workflow is not None and not self._workflow.is_finished():
            raise RuntimeError("Workflow is already running.")

        if not workflow.steps:
            self._workflow = replace(
                workflow,
                status=WorkflowState.COMPLETED,
                current_step=0,
            )
            self._step_results.clear()
            return self._workflow

        self._step_results.clear()

        normalized_steps = tuple(
            replace(
                step,
                status=WorkflowState.PENDING,
            )
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
        """
        Returns the currently active workflow step.
        """

        workflow = self._require_workflow()

        if workflow.status != WorkflowState.RUNNING:
            raise RuntimeError(
                "Workflow is not running."
            )

        if workflow.current_step >= len(workflow.steps):
            raise RuntimeError(
                "Workflow already finished."
            )

        return workflow.steps[workflow.current_step]

    def execute_step(self) -> Any:
        """
        Executes the current workflow step.

        The configured executor receives both the current step and the
        complete workflow. The returned value is stored under the step id.

        If no executor is configured, the step is considered successfully
        processed and its task identifier is returned.
        """

        workflow = self._require_workflow()

        if workflow.status != WorkflowState.RUNNING:
            raise RuntimeError(
                "Workflow is not running."
            )

        if workflow.current_step >= len(workflow.steps):
            raise RuntimeError(
                "Workflow already finished."
            )

        step = workflow.steps[workflow.current_step]

        if step.status == WorkflowState.COMPLETED:
            return self._step_results.get(step.id)

        running_step = replace(
            step,
            status=WorkflowState.RUNNING,
        )

        self._replace_current_step(running_step)

        try:
            if self._executor is None:
                result = step.task
            else:
                result = self._executor(
                    running_step,
                    self._require_workflow(),
                )

        except Exception:
            failed_step = replace(
                running_step,
                status=WorkflowState.FAILED,
            )

            self._replace_current_step(failed_step)

            self._workflow = replace(
                self._require_workflow(),
                status=WorkflowState.FAILED,
            )

            raise

        completed_step = replace(
            running_step,
            status=WorkflowState.COMPLETED,
        )

        self._replace_current_step(completed_step)
        self._step_results[step.id] = result

        return result

    def complete_step(self) -> Workflow:
        """
        Marks the current step as completed and advances the workflow.

        This method is intentionally separate from execute_step so callers
        can either execute through the engine or control completion
        externally.
        """

        workflow = self._require_workflow()

        if workflow.status != WorkflowState.RUNNING:
            raise RuntimeError(
                "Workflow is not running."
            )

        if workflow.current_step >= len(workflow.steps):
            raise RuntimeError(
                "No current step."
            )

        current = workflow.steps[workflow.current_step]

        completed = replace(
            current,
            status=WorkflowState.COMPLETED,
        )

        steps = list(workflow.steps)
        steps[workflow.current_step] = completed

        next_step_index = workflow.current_step + 1

        if next_step_index >= len(steps):
            self._workflow = replace(
                workflow,
                steps=tuple(steps),
                current_step=next_step_index,
                status=WorkflowState.COMPLETED,
            )
            return self._workflow

        self._workflow = replace(
            workflow,
            steps=tuple(steps),
            current_step=next_step_index,
            status=WorkflowState.RUNNING,
        )

        return self._workflow

    def next_step(self) -> Workflow:
        """
        Moves to the next step.

        The current step must already be completed.
        """

        workflow = self._require_workflow()

        if workflow.status != WorkflowState.RUNNING:
            raise RuntimeError(
                "Workflow is not running."
            )

        if workflow.current_step >= len(workflow.steps):
            raise RuntimeError(
                "Workflow already finished."
            )

        current = workflow.steps[workflow.current_step]

        if current.status != WorkflowState.COMPLETED:
            raise RuntimeError(
                "Current workflow step must be completed first."
            )

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
        """
        Executes all remaining workflow steps.

        This is the first autonomous execution primitive used by higher-level
        agents. Each step is executed through the configured executor.
        """

        workflow = self._require_workflow()

        if workflow.status != WorkflowState.RUNNING:
            raise RuntimeError(
                "Workflow is not running."
            )

        while (
            self._workflow is not None
            and self._workflow.status == WorkflowState.RUNNING
        ):
            self.execute_step()

            if self._workflow is None:
                break

            if self._workflow.current_step >= len(
                self._workflow.steps
            ):
                break

            self.next_step()

        return self._require_workflow()

    def fail_step(self) -> Workflow:
        """
        Marks the current step and workflow as failed.
        """

        workflow = self._require_workflow()

        if workflow.status != WorkflowState.RUNNING:
            raise RuntimeError(
                "Workflow is not running."
            )

        if workflow.current_step >= len(workflow.steps):
            raise RuntimeError(
                "No current step."
            )

        failed_step = replace(
            workflow.steps[workflow.current_step],
            status=WorkflowState.FAILED,
        )

        steps = list(workflow.steps)
        steps[workflow.current_step] = failed_step

        self._workflow = replace(
            workflow,
            steps=tuple(steps),
            status=WorkflowState.FAILED,
        )

        return self._workflow

    def pause(self) -> Workflow:
        """
        Pauses a running workflow.
        """

        workflow = self._require_workflow()

        if workflow.status != WorkflowState.RUNNING:
            raise RuntimeError(
                "Only a running workflow can be paused."
            )

        self._workflow = replace(
            workflow,
            status=WorkflowState.PAUSED,
        )

        return self._workflow

    def resume(self) -> Workflow:
        """
        Resumes a paused workflow.
        """

        workflow = self._require_workflow()

        if workflow.status != WorkflowState.PAUSED:
            raise RuntimeError(
                "Only a paused workflow can be resumed."
            )

        self._workflow = replace(
            workflow,
            status=WorkflowState.RUNNING,
        )

        return self._workflow

    def cancel(self) -> Workflow:
        """
        Cancels the current workflow.
        """

        workflow = self._require_workflow()

        if workflow.is_finished():
            raise RuntimeError(
                "Workflow is already finished."
            )

        self._workflow = replace(
            workflow,
            status=WorkflowState.CANCELLED,
        )

        return self._workflow

    def step_result(self, step_id: str) -> Any:
        """
        Returns the result produced by a completed step.
        """

        if step_id not in self._step_results:
            raise KeyError(
                f"No result exists for workflow step '{step_id}'."
            )

        return self._step_results[step_id]

    def snapshot(self) -> WorkflowResult:
        """
        Returns an immutable workflow execution snapshot.
        """

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
        """
        Clears the current workflow and its execution results.
        """

        self._workflow = None
        self._step_results.clear()

    def _require_workflow(self) -> Workflow:
        if self._workflow is None:
            raise RuntimeError(
                "No active workflow."
            )

        return self._workflow

    def _replace_current_step(
        self,
        step: WorkflowStep,
    ) -> None:
        workflow = self._require_workflow()

        steps = list(workflow.steps)
        steps[workflow.current_step] = step

        self._workflow = replace(
            workflow,
            steps=tuple(steps),
        )