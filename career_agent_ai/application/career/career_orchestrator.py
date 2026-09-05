from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from career_agent_ai.application.agents.agent_factory import AgentFactory
from career_agent_ai.application.brain.agent_context import AgentContext
from career_agent_ai.application.career.career_plan import CareerPlan, CareerPlanStep
from career_agent_ai.application.career.career_step_result import CareerStepResult
from career_agent_ai.application.memory.memory_engine import MemoryEngine
from career_agent_ai.application.workflow.workflow import Workflow
from career_agent_ai.application.workflow.workflow_engine import WorkflowEngine


@dataclass(frozen=True)
class CareerRunResult:
    """Immutable aggregate returned by one bounded autonomous career run."""

    objective: str
    plan: CareerPlan
    steps: tuple[CareerStepResult, ...]
    success: bool
    stopped_reason: str | None = None


class CareerOrchestrator:
    """Turn a career objective into a bounded sequence of agent actions.

    The orchestrator deliberately owns orchestration, not business logic.
    Agents remain responsible for their domain actions while memory and the
    workflow engine provide shared state and lifecycle management.
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
        """Build the default career action plan from an objective."""
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
        plan = self.plan(objective, payload)
        context_payload = dict(payload or {})
        context_payload.setdefault("objective", plan.objective)
        context_payload.setdefault("career_plan", plan.actions())

        workflow = Workflow(
            workflow_id=f"career-{user_id}",
            name="Career Agent Run",
            description=plan.objective,
            steps=tuple(
                self._workflow_step(step)
                for step in plan.steps
            ),
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
                user_id=user_id,
                memory_snapshot=self._memory.snapshot(),
                active_workflow=self._workflow.workflow,
                payload=context_payload,
                metadata={"career_step_id": step.id, "objective": plan.objective},
            )
            agent = self._factory.resolve(step.action)
            result = agent.execute(context)
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

            self._workflow.complete_step()

        if len(plan.steps) > self._max_steps and stopped_reason is None:
            stopped_reason = "max_steps_reached"

        final_workflow = self._workflow.workflow
        success = bool(
            final_workflow is not None
            and final_workflow.status.value == "COMPLETED"
        )
        if not success and stopped_reason is None:
            stopped_reason = "workflow_not_completed"

        return CareerRunResult(
            objective=plan.objective,
            plan=plan,
            steps=tuple(results),
            success=success,
            stopped_reason=stopped_reason,
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
