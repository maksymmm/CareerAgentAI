from __future__ import annotations

from dataclasses import dataclass

from career_agent_ai.application.career.opportunity_score import OpportunityScore
from career_agent_ai.application.career.outreach_draft import OutreachDraft
from career_agent_ai.application.career.outreach_draft_service import OutreachDraftService
from career_agent_ai.application.career.pre_vacancy_action import PreVacancyAction
from career_agent_ai.application.career.pre_vacancy_decision_engine import (
    PreVacancyDecisionEngine,
)
from career_agent_ai.application.career.proactive_opportunity_service import (
    ProactiveOpportunityService,
)
from career_agent_ai.application.career.opportunity_signal_provider import (
    OpportunitySignalProvider,
)


@dataclass(frozen=True)
class ProactiveOpportunity:
    """One ranked opportunity together with its bounded action and optional draft."""

    score: OpportunityScore
    action: PreVacancyAction
    outreach_draft: OutreachDraft | None = None


class ProactiveOpportunityPipeline:
    """Run the complete pre-vacancy analysis pipeline without external side effects."""

    def __init__(
        self,
        signal_provider: OpportunitySignalProvider,
        opportunity_service: ProactiveOpportunityService | None = None,
        decision_engine: PreVacancyDecisionEngine | None = None,
        outreach_service: OutreachDraftService | None = None,
    ) -> None:
        self._signal_provider = signal_provider
        self._opportunity_service = opportunity_service or ProactiveOpportunityService()
        self._decision_engine = decision_engine or PreVacancyDecisionEngine()
        self._outreach_service = outreach_service or OutreachDraftService()

    def evaluate(
        self,
        candidate_name: str,
        target_role: str,
    ) -> tuple[ProactiveOpportunity, ...]:
        """Collect signals, score opportunities, choose bounded actions, and draft outreach."""

        opportunities = self._opportunity_service.rank(
            self._signal_provider.collect()
        )
        results: list[ProactiveOpportunity] = []

        for opportunity in opportunities:
            action = self._decision_engine.decide(opportunity)
            draft = None
            if action == PreVacancyAction.PREPARE_OUTREACH:
                draft = self._outreach_service.create(
                    opportunity,
                    candidate_name=candidate_name,
                    target_role=target_role,
                )
            results.append(
                ProactiveOpportunity(
                    score=opportunity,
                    action=action,
                    outreach_draft=draft,
                )
            )

        return tuple(results)
