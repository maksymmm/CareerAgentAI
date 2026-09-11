from career_agent_ai.application.career.career_decision import CareerDecision
from career_agent_ai.application.career.career_decision_engine import CareerDecisionEngine


def test_decision_engine_defaults_to_job_search():
    decision = CareerDecisionEngine().decide("Help me find a Python role")

    assert decision.action == "job_search"
    assert decision.confidence > 0
    assert decision.metadata["source"] == "objective"


def test_decision_engine_detects_application_objective():
    decision = CareerDecisionEngine().decide("I want to apply for a backend job")

    assert decision.action == "job_application"


def test_decision_engine_detects_resume_objective():
    decision = CareerDecisionEngine().decide("Improve my CV for software roles")

    assert decision.action == "resume"


def test_decision_engine_respects_explicit_action():
    decision = CareerDecisionEngine().decide(
        "Find a job",
        {"action": "resume"},
    )

    assert decision.action == "resume"
    assert decision.metadata["source"] == "explicit"


def test_next_action_progresses_after_search():
    decision = CareerDecisionEngine().next_action(
        "Find me a job",
        completed_actions=("job_search",),
    )

    assert decision.action == "resume"


def test_next_action_progresses_after_resume():
    decision = CareerDecisionEngine().next_action(
        "Improve my CV",
        completed_actions=("resume",),
    )

    assert decision.action == "job_application"


def test_decision_rejects_empty_objective():
    import pytest

    with pytest.raises(ValueError):
        CareerDecisionEngine().decide("   ")


def test_career_decision_is_validated():
    import pytest

    with pytest.raises(ValueError):
        CareerDecision(action="", reason="reason")

    with pytest.raises(ValueError):
        CareerDecision(action="job_search", reason="reason", confidence=1.1)
