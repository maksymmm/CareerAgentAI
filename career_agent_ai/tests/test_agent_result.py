from dataclasses import FrozenInstanceError

import pytest

from career_agent_ai.application.agents.agent_result import AgentResult


def test_agent_result_creation():
    result = AgentResult(
        success=True,
        agent_id="resume",
    )

    assert result.success
    assert result.agent_id == "resume"


def test_agent_result_is_immutable():
    result = AgentResult(
        success=True,
        agent_id="resume",
    )

    with pytest.raises(FrozenInstanceError):
        result.agent_id = "changed"