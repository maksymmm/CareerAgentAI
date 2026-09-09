from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from career_agent_ai.application.agents.agent_factory import AgentFactory
from career_agent_ai.application.brain.agent_context import AgentContext
from career_agent_ai.application.career.career_plan import CareerPlan, CareerPlanStep
from career_agent_ai.application.career.career_run_state import CareerRunState
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
    """Turn a career objective into bounded, independently resumable runs."""

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
        self._runs: dict[str, CareerRunState] = {}

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
        """Start a new isolated career run and execute it until a stop condition."""
        normalized_user_id = user_id.strip()
        if not normalized_user_id:
            raise ValueError("user_id must not be empty.")

        run_id = uuid4().hex
        plan = self.plan(objective, payload)
        context_payload = dict(payload or {})
        context_payload.setdefault("objective", plan.objective)
        context_payload.setdefault("career_plan", plan.actions())
        context_payload["career_run_id"] = run_id

        engine = WorkflowEngine()
        workflow = Workflow(
            workflow_id=f"career-{run_id}",
            name="Career Agent Run",
            description=plan.objective,
            steps=tuple(self._workflow_step(step) for step in plan.steps),
        )
        engine.start(workflow)
        state = CareerRunState(
            run_id=run_id,
            user_id=normalized_user_id,
            objective=plan.objective,
            plan=plan,
            payload=context_payload,
            workflow_engine=engine,
        )
        self._runs[run_id] = state
        self._workflow = engine
        return self._continue(state)

    def resume(
        self,
        run_id: str,
        human_result: Any | None = None,
    ) -> CareerRunResult:
        """Resume a paused human-gated run after recording optional human input."""
        state = self._runs.get(run_id)
        if state is None:
            raise KeyError(f"Unknown career run '{run_id}'.")

        engine = state.workflow_engine
        if engine.workflow is None:
            raise RuntimeError("Career run has no workflow.")
        if engine.workflow.status != WorkflowState.PAUSED:
            raise RuntimeError("Only a human-gated career run can be resumed.")

        if human_result is not None:
            state.payload["human_result"] = human_result
        engine.resume()
        engine.complete_step()
        self._workflow = engine
        return self._continue(state)

    def _continue(self, state: CareerRunState) -> CareerRunResult:
        """Execute remaining steps for an existing isolated run state."""
        engine = state.workflow_engine
        stopped_reason: str | None = None

        while engine.workflow is not None and engine.is_running:
            if engine.workflow.current_step >= len(state.plan.steps):
                break
            if len(state.steps) >= self._max_steps:
                stopped_reason = "max_steps_reached"
                break

            step = state.plan.steps[engine.workflow.current_step]
            context = AgentContext(
                user_id=state.user_id,
                memory_snapshot=self._memory.snapshot(),
                active_workflow=engine.workflow,
                payload=dict(state.payload),
                metadata={
                    "career_run_id": state.run_id,
                    "career_step_id": step.id,
                    "objective": state.objective,
                },
            )

            try:
                agent = self._factory.resolve(step.action)
                result = agent.execute(context)
            except Exception as exc:
                step_result = CareerStepResult(
                    step_id=step.id,
                    action=step.action,
                    success=False,
                    messages=(f"Agent execution failed: {exc}",),
                    metadata={"exception_type": type(exc).__name__},
                )
                state.add_step(step_result)
                engine.fail_step()
                stopped_reason = "agent_exception"
                break

            step_result = CareerStepResult(
                step_id=step.id,
                action=step.action,
                success=result.success,
                messages=result.messages,
                metadata=result.metadata,
            )
            state.add_step(step_result)

            if not result.success:
                engine.fail_step()
                stopped_reason = "agent_failed"
                break

            if bool(result.metadata.get("requires_human")):
                engine.pause()
                stopped_reason = "human_action_required"
                break

            engine.complete_step()

        if len(state.steps) >= self._max_steps and not engine.is_finished:
            stopped_reason = stopped_reason or "max_steps_reached"

        final_workflow = engine.workflow
        success = (
            final_workflow is not None
            and final_workflow.status == WorkflowState.COMPLETED
        )
        if not success and stopped_reason is None:
            stopped_reason = "workflow_not_completed"

        result = CareerRunResult(
            run_id=state.run_id,
            objective=state.objective,
            plan=state.plan,
            steps=tuple(state.steps),
            success=success,
            stopped_reason=stopped_reason,
        )
        self._remember_run(state.user_id, result)
        if success or final_workflow is None or final_workflow.is_finished():
            self._runs.pop(state.run_id, None)
        return result

    def _remember_run(self, user_id: str, result: CareerRunResult) -> None:
        """Persist a compact run summary in the current memory engine."""
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
        """Convert a career plan step into a workflow step."""
        from career_agent_ai.application.workflow.workflow_step import WorkflowStep

        return WorkflowStep(
            id=step.id,
            name=step.action,
            order=int(step.id.rsplit("-", 1)[-1]),
            task=step.action,
        )

    @staticmethod
    def _requested_actions(payload: dict[str, Any]) -> tuple[str, ...]:
        """Read explicitly requested actions or use job search as the default."""
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
        """Return a human-readable description for a career action."""
        descriptions = {
            "job_search": "Discover suitable jobs for the candidate.",
            "resume": "Prepare or improve the candidate resume.",
            "job_application": "Process the next job application action.",
        }
        return descriptions.get(action, f"Execute career action '{action}'.")
