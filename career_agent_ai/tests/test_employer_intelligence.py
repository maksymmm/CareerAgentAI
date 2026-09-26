from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from career_agent_ai.application.career.employer_intelligence_service import (
    EmployerIntelligenceService,
)
from career_agent_ai.application.career.opportunity_signal import OpportunitySignal
from career_agent_ai.application.career.opportunity_signal_deduplicator import (
    OpportunitySignalDeduplicator,
)
from career_agent_ai.application.career.proactive_opportunity_service import (
    ProactiveOpportunityService,
)


BASE = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def signal(
    company: str = "Acme GmbH",
    *,
    signal_id: str = "signal-1",
    signal_type: str = "hiring_activity",
    strength: float = 0.8,
    observed_at: datetime = BASE,
    source: str = "https://source.example",
    evidence_count: int = 1,
) -> OpportunitySignal:
    return OpportunitySignal(
        company=company,
        signal_type=signal_type,
        strength=strength,
        observed_at=observed_at,
        source=source,
        signal_id=signal_id,
        metadata={"evidence_count": evidence_count},
    )


def test_deduplicator_keeps_newest_observation_for_same_signal_identity():
    older = signal(strength=0.4)
    newer = signal(
        strength=0.9,
        observed_at=BASE + timedelta(minutes=5),
    )

    result = OpportunitySignalDeduplicator().deduplicate((older, newer))

    assert result == (newer,)


def test_deduplicator_rejects_conflicting_reuse_of_signal_identity():
    first = signal(company="Acme", signal_id="shared-id")
    conflicting = signal(
        company="Beta",
        signal_id="shared-id",
        observed_at=BASE + timedelta(minutes=1),
    )

    with pytest.raises(ValueError, match="conflicting opportunity evidence"):
        OpportunitySignalDeduplicator().deduplicate((first, conflicting))


def test_deduplicator_preserves_distinct_sourced_observations_without_signal_ids():
    first = OpportunitySignal(
        company="Acme",
        signal_type="funding",
        strength=0.5,
        observed_at=BASE,
        source="source-a",
    )
    second = OpportunitySignal(
        company="Acme",
        signal_type="funding",
        strength=0.5,
        observed_at=BASE,
        source="source-b",
    )

    assert OpportunitySignalDeduplicator().deduplicate((first, second)) == (
        first,
        second,
    )


def test_employer_intelligence_aggregates_case_insensitively_with_provenance():
    values = (
        signal(evidence_count=3),
        signal(
            company="acme gmbh",
            signal_id="signal-2",
            signal_type="funding",
            strength=0.6,
            observed_at=BASE + timedelta(hours=1),
            source="https://funding.example",
            evidence_count=2,
        ),
    )

    result = EmployerIntelligenceService().aggregate(values)

    assert len(result) == 1
    intelligence = result[0]
    assert intelligence.company == "Acme GmbH"
    assert intelligence.latest_observed_at == BASE + timedelta(hours=1)
    assert intelligence.sources == (
        "https://source.example",
        "https://funding.example",
    )
    assert intelligence.signal_types == ("hiring_activity", "funding")
    assert intelligence.evidence_count == 5
    assert intelligence.metadata["signal_count"] == 2
    assert 0.0 <= intelligence.confidence <= 1.0


def test_employer_intelligence_order_is_deterministic():
    values = (
        signal(company="Zulu", signal_id="z", strength=0.5),
        signal(company="Alpha", signal_id="a", strength=0.5),
    )

    result = EmployerIntelligenceService().aggregate(values)

    assert tuple(item.company for item in result) == ("Alpha", "Zulu")


def test_proactive_scoring_does_not_double_count_duplicate_signal_evidence():
    duplicate = signal(strength=1.0)
    score = ProactiveOpportunityService().rank((duplicate, duplicate))[0]

    assert score.score == pytest.approx(0.30)
    assert score.metadata["signal_count"] == 1
    assert score.metadata["sources"] == ("https://source.example",)


@pytest.mark.parametrize(
    "changes,error_type",
    [
        ({"company": 123}, TypeError),
        ({"signal_type": ""}, ValueError),
        ({"strength": True}, TypeError),
        ({"observed_at": datetime(2026, 9, 25, 12, 0)}, ValueError),
        ({"metadata": []}, TypeError),
        ({"signal_id": "bad\x00id"}, ValueError),
    ],
)
def test_opportunity_signal_strict_validation(changes, error_type):
    values = {
        "company": "Acme",
        "signal_type": "funding",
        "strength": 0.5,
        "observed_at": BASE,
        "source": "source",
        "signal_id": "signal-id",
        "metadata": {},
    }
    values.update(changes)

    with pytest.raises(error_type):
        OpportunitySignal(**values)
