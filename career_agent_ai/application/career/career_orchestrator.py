from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from career_agent_ai.application.agents.agent_factory import AgentFactory
from career_agent_ai.application.brain.agent_context import AgentContext
from career_agent_ai.application.career.career_plan import CareerPlan, CareerPlanStep
from career_agent_ai.application.career.career_step_result import CareerStepResult
from career_agent_ai.application.memory.memory_engine import MemoryEngine
from career_agent_ai.application.memory.memory_record import MemoryRecord
from career_agent_ai.application.workflow.workflow import Workflow
from career_agent_ai.application.workflow.workflow_engine import WorkflowEngine
from career_agent_ai.application.workflow.workflow_state import WorkflowState


@dataclass(frozen=True)
class CareerRunResult:
    """Immutable aggregate returned by one bounded autonomous career run."""

    run_id: str
    objective: str
    plan: CareerPlan
    steps: tuple[CareerStepResult, ...]
    success: bool
    stopped_reason: str | None = None


class CareerOrchestrator:
    """Turn a career objective into a bounded sequence of agent actions.

    The orchestrator owns lifecycle and coordination while domain agents own
    their business actions. Each run receives a unique identifier, survives
    agent failures without leaking an exception through the workflow state,
    and records its final summary in memory for later autonomous decisions.
    """

    DEFAULT_MAX_STEPS = 8

    def __init__(
        self,
        memory_engine: MemoryEngine,
        workflow_engine: WorkflowEngine,
        agent_factory: AgentFactory,
        max_steps: int = DEFAULT_MAX_STEPS,
    ) -> None:
        if max_steps <= 0:
            raise ValueError("max_steps must be greater than zero.")
        self._memory = memory_engine
        self._workflow = workflow_engine
        self._factory = agent_factory
        self._max_steps = max_steps

    def plan(self, objective: str, payload: dict[str, Any] | None = None) -> CareerPlan:
        """Build a bounded career action plan from an objective."""
        normalized = objective.strip()
        if not normalized:
            raise ValueError("objective must not be empty.")

        data = dict(payload or {})
        actions = self._requested_actions(data)
        steps = tuple(
            CareerPlanStep(
                id=f"career-step-{index}",
                action=action,
                description=self._describe(action),
            )
            for index, action in enumerate(actions, start=1)
        )
        return CareerPlan(objective=normalized, steps=steps)

    def run(
        self,
        user_id: str,
        objective: str,
        payload: dict[str, Any] | None = None,
    ) -> CareerRunResult:
        """Execute a bounded plan through registered agents."""
        normalized_user_id = user_id.strip()
        if not normalized_user_id:
            raise ValueError("user_id must not be empty.")

        run_id = uuid4().hex
        plan = self.plan(objective, payload)
        context_payload = dict(payload or {})
        context_payload.setdefault("objective", plan.objective)
        context_payload.setdefault("career_plan", plan.actions())
        context_payload["career_run_id"] = run_id

        workflow = Workflow(
            workflow_id=f"career-{run_id}",
            name="Career Agent Run",
            description=plan.objective,
            steps=tuple(self._workflow_step(step) for step in plan.steps),
        )
        self._workflow.start(workflow)

        results: list[CareerStepResult] = []
        stopped_reason: str | None = None

        for step in plan.steps[: self._max_steps]:
            if self._workflow.workflow is None:
                stopped_reason = "workflow_missing"
                break
            if self._workflow.workflow.is_finished():
                break

            context = AgentContext(
                user_id=normalized_user_id,
                memory_snapshot=self._memory.snapshot(),
                active_workflow=self._workflow.workflow,
                payload=context_payload,
                metadata={
                    "career_run_id": run_id,
                    "career_step_id": step.id,
                    "objective": plan.objective,
                },
            )

            try:
                agent = self._factory.resolve(step.action)
                result = agent.execute(context)
            except Exception as exc:
                results.append(
                    CareerStepResult(
                        step_id=step.id,
                        action=step.action,
                        success=False,
                        messages=(f"Agent execution failed: {exc}",),
                        metadata={"exception_type": type(exc).__name__},
                    )
                )
                self._workflow.fail_step()
                stopped_reason = "agent_exception"
                break

            results.append(
                CareerStepResult(
                    step_id=step.id,
                    action=step.action,
                    success=result.success,
                    messages=result.messages,
                    metadata=result.metadata,
                )
            )

            if not result.success:
                self._workflow.fail_step()
                stopped_reason = "agent_failed"
                break

            if bool(result.metadata.get("requires_human")):
                self._workflow.pause()
                stopped_reason = "human_action_required"
                break

            self._workflow.complete_step()

        if len(plan.steps) > self._max_steps and stopped_reason is None:
            stopped_reason = "max_steps_reached"

        final_workflow = self._workflow.workflow
        success = (
            final_workflow is not None
            and final_workflow.status == WorkflowState.COMPLETED
        )
        if not success and stopped_reason is None:
            stopped_reason = "workflow_not_completed"

        run_result = CareerRunResult(
            run_id=run_id,
            objective=plan.objective,
            plan=plan,
            steps=tuple(results),
            success=success,
            stopped_reason=stopped_reason,
        )
        self._remember_run(normalized_user_id, run_result)
        return run_result

    def _remember_run(self, user_id: str, result: CareerRunResult) -> None:
        self._memory.save(
            MemoryRecord(
                key=f"career_run:{user_id}:{result.run_id}",
                value={
                    "run_id": result.run_id,
                    "objective": result.objective,
                    "success": result.success,
                    "stopped_reason": result.stopped_reason,
                    "steps": tuple(
                        {
                            "step_id": step.step_id,
                            "action": step.action,
                            "success": step.success,
                            "messages": step.messages,
                            "metadata": dict(step.metadata),
                        }
                        for step in result.steps
                    ),
                },
                metadata={"user_id": user_id, "type": "career_run"},
            )
        )

    @staticmethod
    def _workflow_step(step: CareerPlanStep):
        from career_agent_ai.application.workflow.workflow_step import WorkflowStep

        return WorkflowStep(
            id=step.id,
            name=step.action,
            order=int(step.id.rsplit("-", 1)[-1]),
            task=step.action,
        )

    @staticmethod
    def _requested_actions(payload: dict[str, Any]) -> tuple[str, ...]:
        requested = payload.get("actions")
        if isinstance(requested, (list, tuple)):
            actions = tuple(
                str(value).strip()
                for value in requested
                if str(value).strip()
            )
            if actions:
                return actions

        return ("job_search",)

    @staticmethod
    def _describe(action: str) -> str:
        descriptions = {
            "job_search": "Discover suitable jobs for the candidate.",
            "resume": "Prepare or improve the candidate resume.",
            "job_application": "Process the next job application action.",
        }
        return descriptions.get(action, f"Execute career action '{action}'.")
