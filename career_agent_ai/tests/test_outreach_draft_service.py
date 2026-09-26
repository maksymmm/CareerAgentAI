from career_agent_ai.application.career.opportunity_score import OpportunityScore
from career_agent_ai.application.career.opportunity_signal import OpportunitySignal
from career_agent_ai.application.career.outreach_draft import OutreachDraft
from career_agent_ai.application.career.outreach_draft_service import OutreachDraftService


def test_create_builds_unsent_reviewable_draft():
    opportunity = OpportunityScore(
        company="Alpha",
        score=0.7,
        signals=(
            OpportunitySignal(
                company="Alpha",
                signal_type="funding",
                strength=1.0,
                source="public-report",
            ),
        ),
        rationale="Alpha shows funding-related hiring potential.",
    )

    draft = OutreachDraftService().create(
        opportunity,
        candidate_name="Max",
        target_role="Python Developer",
    )

    assert isinstance(draft, OutreachDraft)
    assert draft.company == "Alpha"
    assert "Python Developer" in draft.subject
    assert "Max" in draft.body
    assert draft.metadata["sent"] is False
    assert draft.opportunity_score == 0.7


def test_create_rejects_empty_candidate_name():
    opportunity = OpportunityScore(
        company="Alpha",
        score=0.7,
        signals=(),
        rationale="Test opportunity.",
    )

    import pytest

    with pytest.raises(ValueError):
        OutreachDraftService().create(opportunity, " ", "Python Developer")


def test_create_rejects_empty_role():
    opportunity = OpportunityScore(
        company="Alpha",
        score=0.7,
        signals=(),
        rationale="Test opportunity.",
    )

    import pytest

    with pytest.raises(ValueError):
        OutreachDraftService().create(opportunity, "Max", " ")
