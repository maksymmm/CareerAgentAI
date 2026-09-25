from __future__ import annotations

from dataclasses import dataclass

from career_agent_ai.application.career.employer_intelligence import (
    EmployerIntelligence,
    EmployerIntelligenceService,
)
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
    """One ranked opportunity, factual employer evidence, and bounded next action."""

    score: OpportunityScore
    action: PreVacancyAction
    outreach_draft: OutreachDraft | None = None
    employer_intelligence: EmployerIntelligence | None = None


class ProactiveOpportunityPipeline:
    """Run proactive employer analysis without consequential external side effects."""

    def __init__(
        self,
        signal_provider: OpportunitySignalProvider,
        opportunity_service: ProactiveOpportunityService | None = None,
        decision_engine: PreVacancyDecisionEngine | None = None,
        outreach_service: OutreachDraftService | None = None,
        intelligence_service: EmployerIntelligenceService | None = None,
    ) -> None:
        self._signal_provider = signal_provider
        self._opportunity_service = opportunity_service or ProactiveOpportunityService()
        self._decision_engine = decision_engine or PreVacancyDecisionEngine()
        self._outreach_service = outreach_service or OutreachDraftService()
        self._intelligence_service = intelligence_service or EmployerIntelligenceService()

    def evaluate(
        self,
        candidate_name: str,
        target_role: str,
    ) -> tuple[ProactiveOpportunity, ...]:
        """Collect evidence once, aggregate it, rank it, and prepare bounded actions."""
        signals = self._signal_provider.collect()
        intelligence = self._intelligence_service.aggregate(signals)
        intelligence_by_company = {
            item.company.casefold(): item for item in intelligence
        }
        opportunities = self._opportunity_service.rank(signals)
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
                    employer_intelligence=intelligence_by_company.get(
                        opportunity.company.casefold()
                    ),
                )
            )

        return tuple(results)
