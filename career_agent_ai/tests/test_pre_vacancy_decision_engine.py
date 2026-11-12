from career_agent_ai.application.career.opportunity_score import OpportunityScore
from career_agent_ai.application.career.pre_vacancy_action import PreVacancyAction
from career_agent_ai.application.career.pre_vacancy_decision_engine import (
    PreVacancyDecisionEngine,
)


def make_opportunity(score: float) -> OpportunityScore:
    return OpportunityScore(
        company="Alpha",
        score=score,
        signals=(),
        rationale="Test opportunity.",
    )


def test_strong_signal_prepares_outreach():
    decision = PreVacancyDecisionEngine().decide(make_opportunity(0.8))
    assert decision == PreVacancyAction.PREPARE_OUTREACH


def test_medium_signal_is_monitored():
    decision = PreVacancyDecisionEngine().decide(make_opportunity(0.4))
    assert decision == PreVacancyAction.MONITOR


def test_weak_signal_is_ignored():
    decision = PreVacancyDecisionEngine().decide(make_opportunity(0.1))
    assert decision == PreVacancyAction.IGNORE


def test_decide_many_preserves_opportunity_order():
    opportunities = (
        make_opportunity(0.8),
        make_opportunity(0.4),
        make_opportunity(0.1),
    )
    decisions = PreVacancyDecisionEngine().decide_many(opportunities)

    assert tuple(action for _, action in decisions) == (
        PreVacancyAction.PREPARE_OUTREACH,
        PreVacancyAction.MONITOR,
        PreVacancyAction.IGNORE,
    )
