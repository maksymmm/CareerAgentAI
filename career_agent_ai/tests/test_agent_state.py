from career_agent_ai.application.agents.agent_state import AgentState


def test_agent_states():
    assert AgentState.IDLE.value == "Idle"
    assert AgentState.READY.value == "Ready"
    assert AgentState.RUNNING.value == "Running"
    assert AgentState.WAITING.value == "Waiting"
    assert AgentState.FAILED.value == "Failed"
    assert AgentState.COMPLETED.value == "Completed"