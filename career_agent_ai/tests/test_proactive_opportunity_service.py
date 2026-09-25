from datetime import datetime, timezone

from career_agent_ai.application.career.opportunity_signal import OpportunitySignal
from career_agent_ai.application.career.proactive_opportunity_service import (
    ProactiveOpportunityService,
)


def test_rank_prioritizes_companies_with_multiple_strong_signals():
    observed_at = datetime.now(timezone.utc)
    signals = (
        OpportunitySignal(
            company="Alpha",
            signal_type="funding",
            strength=1.0,
            observed_at=observed_at,
        ),
        OpportunitySignal(
            company="Alpha",
            signal_type="team_growth",
            strength=1.0,
            observed_at=observed_at,
        ),
        OpportunitySignal(
            company="Beta",
            signal_type="funding",
            strength=0.5,
            observed_at=observed_at,
        ),
    )

    ranked = ProactiveOpportunityService().rank(signals)

    assert ranked[0].company == "Alpha"
    assert ranked[0].score > ranked[1].score


def test_rank_is_explainable():
    signal = OpportunitySignal(
        company="Alpha",
        signal_type="hiring_growth",
        strength=0.8,
        source="company-careers",
    )

    result = ProactiveOpportunityService().rank((signal,))[0]

    assert result.metadata["signal_count"] == 1
    assert "hiring_growth" in result.rationale


def test_signal_validation():
    import pytest

    with pytest.raises(ValueError):
        OpportunitySignal(company="", signal_type="funding", strength=1.0)

    with pytest.raises(ValueError):
        OpportunitySignal(company="Alpha", signal_type="funding", strength=1.1)


def test_rank_deduplicates_stable_signal_ids_before_scoring():
    observed_at = datetime.now(timezone.utc)
    duplicate = OpportunitySignal(
        signal_id="posting-1",
        company="Acme",
        signal_type="active_job_posting",
        strength=1.0,
        observed_at=observed_at,
        source="https://jobs.test/1",
    )
    repeated = OpportunitySignal(
        signal_id="posting-1",
        company="Acme",
        signal_type="active_job_posting",
        strength=1.0,
        observed_at=observed_at,
        source="https://jobs.test/1",
    )

    result = ProactiveOpportunityService().rank((duplicate, repeated))[0]

    assert result.metadata["signal_count"] == 1
    assert result.score == 0.12
    assert result.metadata["sources"] == ("https://jobs.test/1",)


def test_rank_groups_company_names_case_insensitively():
    observed_at = datetime.now(timezone.utc)
    signals = (
        OpportunitySignal(
            signal_id="signal-1",
            company="Acme GmbH",
            signal_type="funding",
            strength=1.0,
            observed_at=observed_at,
        ),
        OpportunitySignal(
            signal_id="signal-2",
            company="ACME GMBH",
            signal_type="team_growth",
            strength=1.0,
            observed_at=observed_at,
        ),
    )

    ranked = ProactiveOpportunityService().rank(signals)

    assert len(ranked) == 1
    assert ranked[0].metadata["signal_count"] == 2
    assert ranked[0].score == 0.35
