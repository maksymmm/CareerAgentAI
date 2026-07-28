import pytest

from career_agent_ai.application.brain.agent_brain import AgentBrain
from career_agent_ai.application.brain.agent_brain_state import AgentBrainState
from career_agent_ai.application.brain.agent_request import AgentRequest
from career_agent_ai.application.memory.memory_engine import MemoryEngine
from career_agent_ai.application.workflow.workflow import Workflow
from career_agent_ai.application.workflow.workflow_engine import WorkflowEngine
from career_agent_ai.application.workflow.workflow_step import WorkflowStep


def make_workflow() -> Workflow:
    return Workflow(
        workflow_id="wf-001",
        name="Demo",
        description="Demo workflow",
        steps=(
            WorkflowStep(
                id="1",
                name="Step 1",
                order=1,
                task="DemoTask",
            ),
        ),
    )


def make_brain() -> AgentBrain:
    return AgentBrain(
        memory_engine=MemoryEngine(),
        workflow_engine=WorkflowEngine(),
    )


def test_creation():
    brain = make_brain()

    assert brain.state == AgentBrainState.IDLE


def test_start_workflow():
    brain = make_brain()

    workflow = brain.start_workflow(make_workflow())

    assert workflow.workflow_id == "wf-001"


def test_pause_resume_cancel():
    brain = make_brain()

    brain.start_workflow(make_workflow())

    assert brain.pause_workflow().status.value == "Paused"
    assert brain.resume_workflow().status.value == "Running"
    assert brain.cancel_workflow().status.value == "Cancelled"


def test_process():
    brain = make_brain()

    request = AgentRequest(
        request_id="req-1",
        user_id="user-1",
        action="start",
    )

    response = brain.process(request)

    assert response.success
    assert response.request_id == "req-1"


def test_snapshot():
    brain = make_brain()

    brain.start_workflow(make_workflow())

    snapshot = brain.snapshot()

    assert snapshot.workflow_id == "wf-001"


def test_continue_workflow():
    brain = make_brain()

    brain.start_workflow(make_workflow())

    workflow = brain.continue_workflow()

    assert workflow.current_step == 1