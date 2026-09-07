from __future__ import annotations

import pytest

from career_agent_ai.application.agents.agent import Agent
from career_agent_ai.application.agents.agent_factory import AgentFactory
from career_agent_ai.application.agents.agent_registry import AgentRegistry
from career_agent_ai.application.agents.agent_result import AgentResult
from career_agent_ai.application.career.career_orchestrator import CareerOrchestrator
from career_agent_ai.application.memory.memory_engine import MemoryEngine
from career_agent_ai.application.workflow.workflow_engine import WorkflowEngine
from career_agent_ai.application.workflow.workflow_state import WorkflowState


class FakeAgent(Agent):
    def __init__(
        self,
        agent_id: str,
        *,
        result: AgentResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self._id = agent_id
        self._result = result
        self._error = error

    @property
    def id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._id

    @property
    def version(self) -> str:
        return "1.0"

    @property
    def description(self) -> str:
        return "fake"

    def execute(self, context):
        if self._error is not None:
            raise self._error
        if self._result is not None:
            return self._result
        return AgentResult(
            success=True,
            agent_id=self._id,
            messages=(f"executed:{self._id}",),
        )

    def supports(self, action: str) -> bool:
        return action == self._id

    def snapshot(self) -> AgentResult:
        return AgentResult(success=True, agent_id=self._id)


def make_orchestrator(
    max_steps: int = 8,
    agents: tuple[FakeAgent, ...] | None = None,
) -> CareerOrchestrator:
    registry = AgentRegistry()
    configured_agents = agents or (
        FakeAgent("job_search"),
        FakeAgent("resume"),
        FakeAgent("job_application"),
    )
    for agent in configured_agents:
        registry.register(agent)
    factory = AgentFactory(registry)
    return CareerOrchestrator(
        memory_engine=MemoryEngine(),
        workflow_engine=WorkflowEngine(),
        agent_factory=factory,
        max_steps=max_steps,
    )


def test_plan_defaults_to_job_search():
    plan = make_orchestrator().plan("Find me a Python job")

    assert plan.objective == "Find me a Python job"
    assert plan.actions() == ("job_search",)


def test_plan_accepts_explicit_actions():
    plan = make_orchestrator().plan(
        "Find and apply",
        {"actions": ["job_search", "resume", "job_application"]},
    )

    assert plan.actions() == (
        "job_search",
        "resume",
        "job_application",
    )


def test_run_executes_all_planned_actions():
    result = make_orchestrator().run(
        user_id="user-1",
        objective="Find and apply",
        payload={"actions": ["job_search", "resume", "job_application"]},
    )

    assert result.success is True
    assert result.stopped_reason is None
    assert len(result.run_id) == 32
    assert tuple(step.action for step in result.steps) == (
        "job_search",
        "resume",
        "job_application",
    )


def test_run_creates_unique_run_ids():
    orchestrator = make_orchestrator()

    first = orchestrator.run("user-1", "Find a job")
    second = orchestrator.run("user-1", "Find another job")

    assert first.run_id != second.run_id


def test_run_respects_max_steps():
    result = make_orchestrator(max_steps=2).run(
        user_id="user-1",
        objective="Find and apply",
        payload={"actions": ["job_search", "resume", "job_application"]},
    )

    assert result.success is False
    assert result.stopped_reason == "max_steps_reached"
    assert len(result.steps) == 2


def test_run_persists_summary_in_memory():
    memory = MemoryEngine()
    registry = AgentRegistry()
    registry.register(FakeAgent("job_search"))
    orchestrator = CareerOrchestrator(
        memory_engine=memory,
        workflow_engine=WorkflowEngine(),
        agent_factory=AgentFactory(registry),
    )

    result = orchestrator.run("user-1", "Find a job")
    record = memory.get(f"career_run:user-1:{result.run_id}")

    assert record is not None
    assert record.value["run_id"] == result.run_id
    assert record.value["success"] is True


def test_run_pauses_for_human_action():
    agent = FakeAgent(
        "job_application",
        result=AgentResult(
            success=True,
            agent_id="job_application",
            metadata={"requires_human": True},
        ),
    )

    orchestrator = make_orchestrator(agents=(agent,))
    result = orchestrator.run(
        "user-1",
        "Apply to the selected job",
        {"actions": ["job_application"]},
    )

    assert result.success is False
    assert result.stopped_reason == "human_action_required"
    assert orchestrator._workflow.workflow.status == WorkflowState.PAUSED


def test_run_contains_agent_failure_without_raising():
    agent = FakeAgent(
        "job_search",
        error=RuntimeError("provider unavailable"),
    )

    orchestrator = make_orchestrator(agents=(agent,))
    result = orchestrator.run("user-1", "Find a job")

    assert result.success is False
    assert result.stopped_reason == "agent_exception"
    assert result.steps[0].success is False
    assert "provider unavailable" in result.steps[0].messages[0]


def test_empty_objective_is_rejected():
    with pytest.raises(ValueError):
        make_orchestrator().plan("   ")


def test_empty_user_id_is_rejected():
    with pytest.raises(ValueError):
        make_orchestrator().run("   ", "Find a job")


def test_invalid_max_steps_is_rejected():
    with pytest.raises(ValueError):
        make_orchestrator(max_steps=0)
