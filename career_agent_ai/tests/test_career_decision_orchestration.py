from career_agent_ai.application.agents.agent import Agent
from career_agent_ai.application.agents.agent_factory import AgentFactory
from career_agent_ai.application.agents.agent_registry import AgentRegistry
from career_agent_ai.application.agents.agent_result import AgentResult
from career_agent_ai.application.career.career_orchestrator import CareerOrchestrator
from career_agent_ai.application.memory.memory_engine import MemoryEngine
from career_agent_ai.application.workflow.workflow_engine import WorkflowEngine


class RecordingAgent(Agent):
    def __init__(self, agent_id: str) -> None:
        self._id = agent_id
        self.contexts = []

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
        return "recording"

    def execute(self, context):
        self.contexts.append(context)
        return AgentResult(
            success=True,
            agent_id=self._id,
        )

    def supports(self, action: str) -> bool:
        return action == self._id

    def snapshot(self) -> AgentResult:
        return AgentResult(success=True, agent_id=self._id)


def make_orchestrator():
    registry = AgentRegistry()
    agents = (
        RecordingAgent("job_search"),
        RecordingAgent("resume"),
        RecordingAgent("job_application"),
    )
    for agent in agents:
        registry.register(agent)
    return (
        CareerOrchestrator(
            memory_engine=MemoryEngine(),
            workflow_engine=WorkflowEngine(),
            agent_factory=AgentFactory(registry),
        ),
        agents,
    )


def test_apply_objective_is_planned_as_a_career_sequence():
    orchestrator, _ = make_orchestrator()

    plan = orchestrator.plan("I want to apply for a backend role")

    assert plan.actions() == (
        "job_search",
        "resume",
        "job_application",
    )


def test_orchestrator_records_decision_rationale_in_agent_context():
    orchestrator, agents = make_orchestrator()

    result = orchestrator.run(
        "user-1",
        "I want to apply for a backend role",
    )

    assert result.success is True
    assert len(result.steps) == 3
    assert all(
        "decision_reason" in context.metadata
        for agent in agents
        for context in agent.contexts
    )
