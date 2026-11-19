from career_agent_ai.application.agents.agent_factory import AgentFactory
from career_agent_ai.application.agents.agent_registry import AgentRegistry
from career_agent_ai.application.agents.resume.resume_agent import ResumeAgent
from career_agent_ai.application.brain.agent_brain import AgentBrain
from career_agent_ai.application.brain.agent_request import AgentRequest
from career_agent_ai.application.memory.memory_engine import MemoryEngine
from career_agent_ai.application.memory.memory_record import MemoryRecord
from career_agent_ai.application.workflow.workflow import Workflow
from career_agent_ai.application.workflow.workflow_engine import WorkflowEngine


def make_brain() -> AgentBrain:
    registry = AgentRegistry()
    registry.register(ResumeAgent())

    return AgentBrain(
        MemoryEngine(),
        WorkflowEngine(),
        AgentFactory(registry),
    )


def test_process():
    brain = make_brain()

    response = brain.process(
        AgentRequest(
            request_id="1",
            user_id="user",
            action="resume",
        )
    )

    assert response.success
    assert response.request_id == "1"


def test_snapshot():
    brain = make_brain()

    workflow = Workflow(
        workflow_id="wf-1",
        name="Test Workflow",
        description="Snapshot test",
        steps=(),
    )

    brain.start_workflow(workflow)

    snapshot = brain.snapshot()

    assert snapshot.workflow_id == "wf-1"

def test_process_scopes_memory_snapshot_to_request_user():
    class CapturingResumeAgent(ResumeAgent):
        def __init__(self):
            super().__init__()
            self.last_context = None

        def execute(self, context):
            self.last_context = context
            return super().execute(context)

    memory = MemoryEngine()
    memory.save(
        MemoryRecord(
            key="user-1:note",
            value="one",
            user_id="user-1",
            memory_type="note",
        )
    )
    memory.save(
        MemoryRecord(
            key="user-2:note",
            value="two",
            user_id="user-2",
            memory_type="note",
        )
    )
    agent = CapturingResumeAgent()
    registry = AgentRegistry()
    registry.register(agent)
    brain = AgentBrain(memory, WorkflowEngine(), AgentFactory(registry))

    response = brain.process(
        AgentRequest(
            request_id="tenant-scope",
            user_id="user-2",
            action="resume",
        )
    )

    assert response.success
    assert agent.last_context is not None
    snapshot = agent.last_context.memory_snapshot
    assert snapshot.get("user-2:note") == "two"
    assert snapshot.contains("user-1:note") is False
