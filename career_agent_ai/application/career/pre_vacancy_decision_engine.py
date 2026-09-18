from __future__ import annotations

from typing import Iterable

from career_agent_ai.application.career.opportunity_score import OpportunityScore
from career_agent_ai.application.career.pre_vacancy_action import PreVacancyAction


class PreVacancyDecisionEngine:
    """Convert explainable opportunity scores into bounded next actions."""

    PREPARE_THRESHOLD = 0.55
    MONITOR_THRESHOLD = 0.25

    def decide(
        self,
        opportunity: OpportunityScore,
    ) -> PreVacancyAction:
        """Select a bounded action from one opportunity score."""
        if opportunity.score >= self.PREPARE_THRESHOLD:
            return PreVacancyAction.PREPARE_OUTREACH

        if opportunity.score >= self.MONITOR_THRESHOLD:
            return PreVacancyAction.MONITOR

        return PreVacancyAction.IGNORE

    def decide_many(
        self,
        opportunities: Iterable[OpportunityScore],
    ) -> tuple[tuple[OpportunityScore, PreVacancyAction], ...]:
        """Select bounded actions for opportunities without changing their order."""
        return tuple(
            (opportunity, self.decide(opportunity))
            for opportunity in opportunities
        )
