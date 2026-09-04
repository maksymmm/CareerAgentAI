from dataclasses import FrozenInstanceError

import pytest

from career_agent_ai.application.agents.resume.resume_agent import ResumeAgent
from career_agent_ai.application.agents.resume.resume_request import ResumeRequest
from career_agent_ai.application.agents.resume.resume_response import ResumeResponse
from career_agent_ai.application.brain.agent_context import AgentContext
from career_agent_ai.application.memory.memory_snapshot import MemorySnapshot


def make_context() -> AgentContext:
    return AgentContext(
        user_id="user-1",
        memory_snapshot=MemorySnapshot(),
    )


def test_resume_agent_metadata():
    agent = ResumeAgent()

    assert agent.id == "resume"
    assert agent.name == "Resume Agent"
    assert agent.version == "1.0"
    assert agent.description == "Handles resume operations."


def test_resume_agent_supports():
    agent = ResumeAgent()

    assert agent.supports("resume")
    assert not agent.supports("jobs")
    assert not agent.supports("workflow")


def test_resume_execute():
    agent = ResumeAgent()

    result = agent.execute(make_context())

    assert result.success is True
    assert result.agent_id == "resume"
    assert result.messages == ("Resume Agent executed.",)


def test_resume_snapshot():
    agent = ResumeAgent()

    result = agent.snapshot()

    assert result.success is True
    assert result.agent_id == "resume"


def test_request_is_immutable():
    request = ResumeRequest(
        text="Hello",
    )

    with pytest.raises(FrozenInstanceError):
        request.text = "Changed"


def test_response_is_immutable():
    response = ResumeResponse(
        success=True,
        content="CV",
    )

    with pytest.raises(FrozenInstanceError):
        response.content = "Changed"