from __future__ import annotations

from typing import Mapping, Any

from career_agent_ai.application.career.opportunity_score import OpportunityScore
from career_agent_ai.application.career.outreach_draft import OutreachDraft


class OutreachDraftService:
    """Create personalized proactive outreach drafts without sending them."""

    def create(
        self,
        opportunity: OpportunityScore,
        candidate_name: str,
        target_role: str,
    ) -> OutreachDraft:
        """Build a concise outreach draft from an explainable opportunity."""

        name = candidate_name.strip()
        role = target_role.strip()
        if not name:
            raise ValueError("candidate_name must not be empty.")
        if not role:
            raise ValueError("target_role must not be empty.")

        signal_types = tuple(
            dict.fromkeys(signal.signal_type for signal in opportunity.signals)
        )
        signal_summary = ", ".join(signal_types) or "relevant hiring activity"

        subject = f"Potential {role} contribution at {opportunity.company}"
        body = (
            f"Hello {opportunity.company} team,\n\n"
            f"My name is {name}, and I am interested in contributing as a {role}. "
            f"I noticed signals consistent with {signal_summary}. "
            "If your team is considering hiring for this area, I would be glad "
            "to share my background and discuss where I could contribute.\n\n"
            f"Best regards,\n{name}"
        )
        rationale = (
            f"Prepared because {opportunity.company} reached an opportunity "
            f"score of {opportunity.score:.2f}. "
            f"{opportunity.rationale}"
        )
        return OutreachDraft(
            company=opportunity.company,
            subject=subject,
            body=body,
            opportunity_score=opportunity.score,
            rationale=rationale,
            metadata={
                "signal_types": signal_types,
                "signal_count": len(opportunity.signals),
                "sent": False,
            },
        )
