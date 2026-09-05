from __future__ import annotations

from career_agent_ai.application.agents.agent import Agent
from career_agent_ai.application.agents.agent_factory import AgentFactory
from career_agent_ai.application.agents.agent_registry import AgentRegistry
from career_agent_ai.application.agents.agent_result import AgentResult
from career_agent_ai.application.career.career_orchestrator import CareerOrchestrator
from career_agent_ai.application.memory.memory_engine import MemoryEngine
from career_agent_ai.application.workflow.workflow_engine import WorkflowEngine


class FakeAgent(Agent):
    def __init__(self, agent_id: str) -> None:
        self._id = agent_id

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
        return AgentResult(
            success=True,
            agent_id=self._id,
            messages=(f"executed:{self._id}",),
        )

    def supports(self, action: str) -> bool:
        return action == self._id

    def snapshot(self) -> AgentResult:
        return AgentResult(success=True, agent_id=self._id)


def make_orchestrator(max_steps: int = 8) -> CareerOrchestrator:
    registry = AgentRegistry()
    registry.register(FakeAgent("job_search"))
    registry.register(FakeAgent("resume"))
    registry.register(FakeAgent("job_application"))
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
    assert tuple(step.action for step in result.steps) == (
        "job_search",
        "resume",
        "job_application",
    )


def test_run_respects_max_steps():
    result = make_orchestrator(max_steps=2).run(
        user_id="user-1",
        objective="Find and apply",
        payload={"actions": ["job_search", "resume", "job_application"]},
    )

    assert result.success is False
    assert result.stopped_reason == "max_steps_reached"
    assert len(result.steps) == 2


def test_empty_objective_is_rejected():
    import pytest

    with pytest.raises(ValueError):
        make_orchestrator().plan("   ")
