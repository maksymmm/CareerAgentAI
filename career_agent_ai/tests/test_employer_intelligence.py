from __future__ import annotations

from datetime import datetime, timedelta, timezone

from career_agent_ai.application.career.employer_intelligence import (
    EmployerIntelligenceService,
)
from career_agent_ai.application.career.opportunity_signal import OpportunitySignal
from career_agent_ai.application.career.proactive_opportunity_service import (
    ProactiveOpportunityService,
)
from career_agent_ai.application.career.signal_deduplicator import (
    OpportunitySignalDeduplicator,
)


NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def signal(
    *,
    company: str = "Acme",
    signal_type: str = "funding",
    external_id: str = "story-1",
    source: str = "https://example.com/story",
    observed_at: datetime = NOW,
    strength: float = 1.0,
) -> OpportunitySignal:
    return OpportunitySignal(
        company=company,
        signal_type=signal_type,
        strength=strength,
        observed_at=observed_at,
        source=source,
        metadata={"external_id": external_id, "title": "Evidence"},
    )


def test_deduplicator_prefers_provider_native_external_identity():
    first = signal()
    duplicate = signal(observed_at=NOW + timedelta(minutes=5))

    result = OpportunitySignalDeduplicator().deduplicate((first, duplicate))

    assert result == (first,)


def test_deduplicator_keeps_distinct_signal_types_for_same_source_item():
    funding = signal(signal_type="funding")
    growth = signal(signal_type="team_growth")

    result = OpportunitySignalDeduplicator().deduplicate((funding, growth))

    assert result == (funding, growth)


def test_employer_intelligence_aggregates_provenance_and_latest_observation():
    earlier = signal(company="Acme", external_id="one")
    later = signal(
        company="acme",
        signal_type="new_product",
        external_id="two",
        source="https://example.com/two",
        observed_at=NOW + timedelta(hours=1),
    )

    result = EmployerIntelligenceService().aggregate((later, earlier))

    assert len(result) == 1
    intelligence = result[0]
    assert intelligence.company == "acme"
    assert intelligence.latest_observed_at == later.observed_at
    assert intelligence.signal_types == ("funding", "new_product")
    assert intelligence.sources == (
        "https://example.com/story",
        "https://example.com/two",
    )
    assert intelligence.signals == (earlier, later)


def test_employer_intelligence_is_deterministically_ordered_by_latest_signal():
    alpha = signal(company="Alpha", external_id="a")
    beta = signal(
        company="Beta",
        external_id="b",
        observed_at=NOW + timedelta(hours=2),
    )

    result = EmployerIntelligenceService().aggregate((alpha, beta))

    assert tuple(item.company for item in result) == ("Beta", "Alpha")




def test_deduplicator_scopes_external_ids_to_feed_namespace():
    first = OpportunitySignal(
        company="Acme",
        signal_type="funding",
        strength=1.0,
        observed_at=NOW,
        source="https://example.com/first",
        metadata={
            "external_id": "shared",
            "feed_url": "https://example.com/feed-a.xml",
        },
    )
    second = OpportunitySignal(
        company="Acme",
        signal_type="funding",
        strength=1.0,
        observed_at=NOW,
        source="https://example.com/second",
        metadata={
            "external_id": "shared",
            "feed_url": "https://example.com/feed-b.xml",
        },
    )

    assert OpportunitySignalDeduplicator().deduplicate((first, second)) == (
        first,
        second,
    )


def test_proactive_scoring_groups_company_name_case_insensitively():
    funding = signal(company="Acme", signal_type="funding", external_id="fund")
    product = signal(
        company="acme",
        signal_type="new_product",
        external_id="product",
        source="https://example.com/product",
    )

    ranked = ProactiveOpportunityService().rank((funding, product))

    assert len(ranked) == 1
    assert ranked[0].metadata["signal_count"] == 2

def test_proactive_scoring_does_not_double_count_duplicate_signal():
    duplicate = signal(strength=1.0)

    ranked = ProactiveOpportunityService().rank((duplicate, duplicate))

    assert ranked[0].metadata["signal_count"] == 1
    assert ranked[0].score == 0.15
