from career_agent_ai.application.career.opportunity_signal import OpportunitySignal
from career_agent_ai.application.career.opportunity_signal_provider import (
    StaticOpportunitySignalProvider,
)
from career_agent_ai.application.career.outreach_draft import OutreachDraft
from career_agent_ai.application.career.pre_vacancy_action import PreVacancyAction
from career_agent_ai.application.career.proactive_opportunity_pipeline import (
    ProactiveOpportunityPipeline,
)


def test_pipeline_prepares_but_does_not_send_outreach():
    signals = (
        OpportunitySignal(
            company="Alpha",
            signal_type="hiring_growth",
            strength=1.0,
            source="injected-public-signal",
        ),
        OpportunitySignal(
            company="Alpha",
            signal_type="leadership_hire",
            strength=1.0,
            source="injected-public-signal",
        ),
    )
    pipeline = ProactiveOpportunityPipeline(
        StaticOpportunitySignalProvider(signals)
    )

    result = pipeline.evaluate("Max", "Python Developer")

    assert len(result) == 1
    assert result[0].action == PreVacancyAction.PREPARE_OUTREACH
    assert isinstance(result[0].outreach_draft, OutreachDraft)
    assert result[0].outreach_draft.metadata["sent"] is False
    assert result[0].employer_intelligence is not None
    assert result[0].employer_intelligence.company == "Alpha"
    assert result[0].employer_intelligence.signal_types == (
        "hiring_growth",
        "leadership_hire",
    )


def test_pipeline_keeps_weak_opportunity_without_draft():
    signal = OpportunitySignal(
        company="Beta",
        signal_type="new_product",
        strength=0.5,
        source="injected-public-signal",
    )
    pipeline = ProactiveOpportunityPipeline(
        StaticOpportunitySignalProvider((signal,))
    )

    result = pipeline.evaluate("Max", "Python Developer")

    assert result[0].action == PreVacancyAction.IGNORE
    assert result[0].outreach_draft is None
