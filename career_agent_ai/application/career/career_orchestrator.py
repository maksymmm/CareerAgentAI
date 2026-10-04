from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from enum import Enum
import math
import sqlite3
from threading import Event, RLock, Thread
from time import monotonic
from typing import Any
from uuid import uuid4

from career_agent_ai.application.agents.agent_factory import AgentFactory
from career_agent_ai.application.brain.agent_context import AgentContext
from career_agent_ai.application.career.career_decision_engine import CareerDecisionEngine
from career_agent_ai.application.career.career_plan import CareerPlan, CareerPlanStep
from career_agent_ai.application.career.career_run_repository import (
    CareerRunConflictError,
    CareerRunRepository,
)
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
    LEASE_TTL_SECONDS = 30
    LEASE_HEARTBEAT_SECONDS = 10

    def __init__(
        self,
        memory_engine: MemoryEngine,
        workflow_engine: WorkflowEngine,
        agent_factory: AgentFactory,
        max_steps: int = DEFAULT_MAX_STEPS,
        run_repository: CareerRunRepository | None = None,
    ) -> None:
        if max_steps <= 0:
            raise ValueError("max_steps must be greater than zero.")
        self._memory = memory_engine
        self._workflow = workflow_engine
        self._factory = agent_factory
        self._max_steps = max_steps
        self._decision_engine = CareerDecisionEngine()
        self._runs: dict[str, CareerRunState] = {}
        self._run_repository = run_repository
        self._resume_lock = RLock()
        self._lease_owner = uuid4().hex
        self._leased_runs: set[str] = set()
        self._lease_errors: dict[str, list[Exception]] = {}

    def plan(self, objective: str, payload: dict[str, Any] | None = None) -> CareerPlan:
        """Build a bounded career action plan from an objective."""
        normalized = objective.strip()
        if not normalized:
            raise ValueError("objective must not be empty.")

        data = dict(payload or {})
        actions = self._requested_actions(data)
        if actions is None:
            actions = self._objective_actions(normalized, data)
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
        self._persist_state(state)
        if self._run_repository is None:
            return self._continue(state)
        with self._execution_lease(run_id) as leased_state:
            return self._continue(leased_state)

    def resume(
        self,
        run_id: str,
        human_result: Any | None = None,
    ) -> CareerRunResult:
        """Resume one paused run after its human gate validates explicit approval."""
        with self._resume_lock:
            try:
                return self._resume_locked(run_id, human_result)
            except CareerRunConflictError:
                # A different process advanced the durable snapshot. Discard any
                # stale in-process cache so a deliberate retry reloads fresh state.
                self._runs.pop(run_id, None)
                raise

    def continue_run(self, run_id: str) -> CareerRunResult:
        """Reclaim and continue one durable RUNNING run under an execution lease."""
        with self._resume_lock:
            try:
                if self._run_repository is None:
                    state = self._runs.get(run_id)
                    if state is None:
                        raise KeyError(f"Unknown career run '{run_id}'.")
                    return self._continue_running_state(state)
                with self._execution_lease(run_id) as state:
                    return self._continue_running_state(state)
            except CareerRunConflictError:
                self._runs.pop(run_id, None)
                raise

    def _continue_running_state(self, state: CareerRunState) -> CareerRunResult:
        """Continue a running state or idempotently finalize a completed checkpoint."""
        engine = state.workflow_engine
        if engine.workflow is None:
            raise RuntimeError("Career run has no workflow.")
        if engine.workflow.status == WorkflowState.COMPLETED:
            result = CareerRunResult(
                run_id=state.run_id,
                objective=state.objective,
                plan=state.plan,
                steps=tuple(state.steps),
                success=True,
                stopped_reason=None,
            )
            self._remember_run(state.user_id, result)
            self._forget_state(state.run_id)
            return result
        if engine.workflow.status != WorkflowState.RUNNING:
            raise RuntimeError("Only a durable running career run can be continued.")
        return self._continue(state)

    def _resume_locked(
        self,
        run_id: str,
        human_result: Any | None,
    ) -> CareerRunResult:
        """Resume a paused human-gated run while holding local and durable fencing."""
        if self._run_repository is not None:
            with self._execution_lease(run_id) as state:
                return self._resume_state(state, human_result)

        state = self._runs.get(run_id)
        if state is None:
            raise KeyError(f"Unknown career run '{run_id}'.")
        return self._resume_state(state, human_result)

    def _resume_state(
        self, state: CareerRunState, human_result: Any | None
    ) -> CareerRunResult:
        """Persist approved human input, complete the gate, and continue the run."""
        if not self._is_affirmative_human_result(human_result):
            raise PermissionError(
                "Explicit affirmative human approval is required to resume a gated run."
            )
        engine = state.workflow_engine
        if engine.workflow is None:
            raise RuntimeError("Career run has no workflow.")
        if engine.workflow.status != WorkflowState.PAUSED:
            raise RuntimeError("Only a human-gated career run can be resumed.")

        try:
            durable_human_result = self._json_safe(human_result)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "human_result is not durably serializable."
            ) from exc
        state.payload["human_result"] = durable_human_result
        engine.resume()
        engine.complete_step()
        self._workflow = engine
        self._persist_state(state)
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
            decision = self._decision_engine.next_action(
                state.objective,
                tuple(item.action for item in state.steps),
                state.payload,
            )
            context = AgentContext(
                user_id=state.user_id,
                memory_snapshot=self._memory.snapshot(user_id=state.user_id),
                active_workflow=engine.workflow,
                payload=dict(state.payload),
                metadata={
                    "career_run_id": state.run_id,
                    "career_step_id": step.id,
                    "objective": state.objective,
                    "decision": decision.action,
                    "decision_reason": decision.reason,
                    "decision_confidence": decision.confidence,
                },
            )

            try:
                self._assert_lease_healthy(state.run_id)
                agent = self._factory.resolve(step.action)
                result = agent.execute(context)
                self._assert_lease_healthy(state.run_id)
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
                self._persist_state(state)
                stopped_reason = "agent_exception"
                break

            try:
                durable_metadata = self._json_safe(
                    {
                        **dict(result.metadata),
                        "decision": decision.action,
                        "decision_reason": decision.reason,
                        "decision_confidence": decision.confidence,
                    }
                )
            except (TypeError, ValueError) as exc:
                step_result = CareerStepResult(
                    step_id=step.id,
                    action=step.action,
                    success=False,
                    messages=(f"Agent metadata is not durably serializable: {exc}",),
                    metadata={"exception_type": type(exc).__name__},
                )
                state.add_step(step_result)
                engine.fail_step()
                self._persist_state(state)
                stopped_reason = "invalid_agent_metadata"
                break

            step_result = CareerStepResult(
                step_id=step.id,
                action=step.action,
                success=result.success,
                messages=result.messages,
                metadata=durable_metadata,
            )
            state.add_step(step_result)

            if not result.success:
                engine.fail_step()
                self._persist_state(state)
                stopped_reason = "agent_failed"
                break

            if bool(result.metadata.get("requires_human")):
                engine.pause()
                self._persist_state(state)
                stopped_reason = "human_action_required"
                break

            engine.complete_step()
            self._persist_state(state)

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
            self._forget_state(state.run_id)
        else:
            self._persist_state(state)
        return result

    def _persist_state(self, state: CareerRunState) -> None:
        """Persist a resumable run while respecting any held execution lease."""
        if self._run_repository is not None:
            self._assert_lease_healthy(state.run_id)
            lease_owner = (
                self._lease_owner if state.run_id in self._leased_runs else None
            )
            self._run_repository.save(state, lease_owner=lease_owner)

    def _forget_state(self, run_id: str) -> None:
        """Evict terminal state while respecting any held execution lease."""
        self._runs.pop(run_id, None)
        if self._run_repository is not None:
            lease_owner = self._lease_owner if run_id in self._leased_runs else None
            self._run_repository.delete(run_id, lease_owner=lease_owner)

    @contextmanager
    def _execution_lease(self, run_id: str) -> Iterator[CareerRunState]:
        """Hold durable ownership across agent execution and checkpoint writes."""
        if self._run_repository is None:
            state = self._runs.get(run_id)
            if state is None:
                raise KeyError(f"Unknown career run '{run_id}'.")
            yield state
            return

        state = self._run_repository.acquire_lease(
            run_id, self._lease_owner, ttl_seconds=self.LEASE_TTL_SECONDS
        )
        self._runs[run_id] = state
        self._leased_runs.add(run_id)
        errors: list[Exception] = []
        self._lease_errors[run_id] = errors
        stop = Event()
        heartbeat: Thread | None = None
        if self._run_repository.supports_background_lease_renewal:
            heartbeat = Thread(
                target=self._lease_heartbeat,
                args=(run_id, stop, errors),
                name=f"career-run-lease-{run_id[:8]}",
                daemon=True,
            )
            heartbeat.start()

        active_exception = False
        try:
            yield state
            self._assert_lease_healthy(run_id)
        except BaseException:
            active_exception = True
            raise
        finally:
            stop.set()
            if heartbeat is not None:
                heartbeat.join(timeout=self.LEASE_HEARTBEAT_SECONDS + 1)
            try:
                self._run_repository.release_lease(run_id, self._lease_owner)
            except CareerRunConflictError:
                if not active_exception:
                    raise
            finally:
                self._leased_runs.discard(run_id)
                self._lease_errors.pop(run_id, None)

    def _lease_heartbeat(
        self, run_id: str, stop: Event, errors: list[Exception]
    ) -> None:
        """Renew a file-backed execution lease until the owning action completes.

        SQLite lock/busy errors are transient writer-contention signals. Retry them
        within the remaining lease budget instead of poisoning an otherwise valid
        execution lease after the first lock timeout. Ownership conflicts and other
        errors remain terminal for the heartbeat.
        """
        assert self._run_repository is not None
        retry_delay = min(1.0, max(0.05, self.LEASE_HEARTBEAT_SECONDS / 4))
        retry_budget = max(
            retry_delay,
            self.LEASE_TTL_SECONDS - self.LEASE_HEARTBEAT_SECONDS,
        )
        while not stop.wait(self.LEASE_HEARTBEAT_SECONDS):
            deadline = monotonic() + retry_budget
            while True:
                try:
                    self._run_repository.renew_lease(
                        run_id,
                        self._lease_owner,
                        ttl_seconds=self.LEASE_TTL_SECONDS,
                    )
                    break
                except sqlite3.OperationalError as exc:
                    message = str(exc).lower()
                    if (
                        "locked" not in message
                        and "busy" not in message
                    ):
                        errors.append(exc)
                        return
                    if monotonic() >= deadline:
                        errors.append(exc)
                        return
                    if stop.wait(retry_delay):
                        return
                except Exception as exc:
                    errors.append(exc)
                    return

    def _assert_lease_healthy(self, run_id: str) -> None:
        """Fail closed when durable lease renewal has been lost."""
        errors = self._lease_errors.get(run_id)
        if errors:
            raise CareerRunConflictError(
                "Career run execution lease heartbeat failed."
            ) from errors[0]

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
                user_id=user_id,
                memory_type="career_run",
            )
        )

    @staticmethod
    def _is_affirmative_human_result(human_result: Any | None) -> bool:
        """Return whether human input explicitly grants approval to cross the gate."""
        if human_result is True:
            return True
        if not isinstance(human_result, Mapping):
            return False
        return human_result.get("approved") is True

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        """Convert supported agent metadata into deterministic JSON-safe values."""
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("Agent metadata floating-point values must be finite.")
            return value
        if isinstance(value, Enum):
            return cls._json_safe(value.value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if is_dataclass(value) and not isinstance(value, type):
            return cls._json_safe(asdict(value))
        if isinstance(value, Mapping):
            normalized: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("Agent metadata mapping keys must be strings.")
                normalized[key] = cls._json_safe(item)
            return normalized
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        raise TypeError(
            f"Unsupported agent metadata type: {type(value).__name__}."
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
    def _requested_actions(payload: dict[str, Any]) -> tuple[str, ...] | None:
        """Read explicitly requested actions, preserving caller control."""
        requested = payload.get("actions")
        if isinstance(requested, (list, tuple)):
            actions = tuple(
                str(value).strip()
                for value in requested
                if str(value).strip()
            )
            if actions:
                return actions
        return None

    def _objective_actions(
        self,
        objective: str,
        payload: dict[str, Any],
    ) -> tuple[str, ...]:
        """Derive a useful multi-step plan when actions were not explicit."""
        decision = self._decision_engine.decide(objective, payload)
        if decision.action == "job_application":
            return ("job_search", "resume", "job_application")
        return (decision.action,)

    @staticmethod
    def _describe(action: str) -> str:
        """Return a human-readable description for a career action."""
        descriptions = {
            "job_search": "Discover suitable jobs for the candidate.",
            "resume": "Prepare or improve the candidate resume.",
            "job_application": "Process the next job application action.",
        }
        return descriptions.get(action, f"Execute career action '{action}'.")
