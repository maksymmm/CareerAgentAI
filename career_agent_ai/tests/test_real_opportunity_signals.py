from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from career_agent_ai.application.career.employer_intelligence import (
    EmployerIntelligenceService,
)
from career_agent_ai.application.career.opportunity_signal import (
    OpportunitySignal,
    deduplicate_opportunity_signals,
)
from career_agent_ai.application.career.opportunity_signal_provider import (
    OpportunitySignalProviderError,
)
from career_agent_ai.application.career.providers import (
    ArbeitnowOpportunitySignalProvider,
)
from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.search.providers.arbeitnow_provider import (
    ArbeitnowProvider,
)


OBSERVED = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def job(
    job_id: str = "arbeitnow:python-1",
    *,
    company: str = "Acme GmbH",
    title: str = "Python Developer",
    url: str = "https://example.test/jobs/python-1",
    created_at: datetime | None = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc),
) -> Job:
    return Job.create(
        job_id=job_id,
        title=title,
        company=Company(company_id=f"company:{company}", name=company),
        location=Location(country="Germany", city="Karlsruhe", remote=False),
        url=url,
        description="Public job posting",
        created_at=created_at,
        published_at=created_at,
    )


class FakeJobProvider:
    def __init__(self, jobs: tuple[Job, ...]) -> None:
        self.jobs = jobs
        self.queries: list[str] = []

    def search(self, query: str) -> tuple[Job, ...]:
        self.queries.append(query)
        return self.jobs


def test_arbeitnow_signal_provider_maps_real_postings_with_provenance():
    jobs = (
        job(),
        job(),  # duplicate API record
        job(
            "arbeitnow:logistics-2",
            title="Logistics Coordinator",
            url="not-a-public-url",
        ),
    )
    fake = FakeJobProvider(jobs)
    provider = ArbeitnowOpportunitySignalProvider(
        query="  Karlsruhe   logistics ",
        job_provider=fake,
        minimum_interval_seconds=0,
        clock=lambda: OBSERVED,
    )

    signals = provider.collect()

    assert fake.queries == ["Karlsruhe logistics"]
    assert len(signals) == 2
    first = next(signal for signal in signals if signal.metadata["job_id"] == "arbeitnow:python-1")
    assert first.signal_id == "arbeitnow-signal:arbeitnow:python-1"
    assert first.signal_type == "active_job_posting"
    assert first.strength == 1.0
    assert first.observed_at == OBSERVED
    assert first.source == "https://example.test/jobs/python-1"
    assert first.metadata["provider"] == "arbeitnow"
    assert first.metadata["job_title"] == "Python Developer"
    assert first.metadata["published_at"] == "2026-09-23T09:00:00+00:00"

    fallback = next(
        signal
        for signal in signals
        if signal.metadata["job_id"] == "arbeitnow:logistics-2"
    )
    assert fallback.source == ArbeitnowProvider.BASE_URL



def test_signal_provider_preserves_missing_publication_timestamp():
    source_job = job(created_at=None)
    assert source_job.created_at is not None
    assert source_job.published_at is None
    provider = ArbeitnowOpportunitySignalProvider(
        job_provider=FakeJobProvider((source_job,)),
        minimum_interval_seconds=0,
        clock=lambda: OBSERVED,
    )

    signal = provider.collect()[0]

    assert signal.metadata["published_at"] is None

def test_signal_provider_does_not_invent_unknown_company_evidence():
    provider = ArbeitnowOpportunitySignalProvider(
        job_provider=FakeJobProvider(
            (
                job(company="Unknown company"),
                job("arbeitnow:valid", company="Known GmbH"),
            )
        ),
        minimum_interval_seconds=0,
        clock=lambda: OBSERVED,
    )

    signals = provider.collect()

    assert tuple(signal.company for signal in signals) == ("Known GmbH",)


def test_signal_provider_retries_with_rate_limit_then_succeeds():
    class FlakyProvider:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str) -> tuple[Job, ...]:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary upstream failure")
            return (job(),)

    fake = FlakyProvider()
    monotonic_values = iter((0.0, 0.0, 1.0))
    sleeps: list[float] = []
    provider = ArbeitnowOpportunitySignalProvider(
        job_provider=fake,
        minimum_interval_seconds=1.0,
        max_attempts=2,
        clock=lambda: OBSERVED,
        monotonic=lambda: next(monotonic_values),
        sleeper=sleeps.append,
    )

    signals = provider.collect()

    assert len(signals) == 1
    assert fake.calls == 2
    assert sleeps == [1.0]


def test_signal_provider_raises_bounded_provider_error_after_retries():
    class FailingProvider:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str) -> tuple[Job, ...]:
            self.calls += 1
            raise RuntimeError("secret upstream detail")

    fake = FailingProvider()
    provider = ArbeitnowOpportunitySignalProvider(
        job_provider=fake,
        minimum_interval_seconds=0,
        max_attempts=2,
        clock=lambda: OBSERVED,
    )

    with pytest.raises(
        OpportunitySignalProviderError,
        match="failed after bounded retries",
    ):
        provider.collect()

    assert fake.calls == 2


def test_signal_provider_rejects_invalid_observation_clock():
    provider = ArbeitnowOpportunitySignalProvider(
        job_provider=FakeJobProvider((job(),)),
        minimum_interval_seconds=0,
        clock=lambda: datetime(2026, 9, 24, 12, 0),
    )

    with pytest.raises(OpportunitySignalProviderError, match="timezone-aware"):
        provider.collect()


@pytest.mark.parametrize(
    "kwargs,error_type",
    [
        ({"query": 42}, TypeError),
        ({"timeout": 0}, ValueError),
        ({"minimum_interval_seconds": -1}, ValueError),
        ({"max_attempts": 0}, ValueError),
        ({"max_attempts": True}, ValueError),
    ],
)
def test_signal_provider_configuration_validation(kwargs, error_type):
    with pytest.raises(error_type):
        ArbeitnowOpportunitySignalProvider(**kwargs)



def test_fallback_signal_id_is_stable_across_reobservations():
    first = OpportunitySignal(
        company="Acme GmbH",
        signal_type="funding",
        strength=0.8,
        observed_at=OBSERVED,
        source="https://source.test/evidence",
    )
    later = OpportunitySignal(
        company="Acme GmbH",
        signal_type="funding",
        strength=0.8,
        observed_at=OBSERVED + timedelta(hours=1),
        source="https://source.test/evidence",
    )

    assert first.signal_id == later.signal_id
    assert deduplicate_opportunity_signals((first, later)) == (later,)

def test_signal_deduplication_keeps_latest_reobservation():
    first = OpportunitySignal(
        signal_id="signal-1",
        company="Acme GmbH",
        signal_type="funding",
        strength=0.8,
        observed_at=OBSERVED,
        source="https://source.test/evidence",
        metadata={"round": "A"},
    )
    later = OpportunitySignal(
        signal_id="signal-1",
        company="Acme GmbH",
        signal_type="funding",
        strength=0.8,
        observed_at=OBSERVED + timedelta(hours=1),
        source="https://source.test/evidence",
        metadata={"round": "A"},
    )

    result = deduplicate_opportunity_signals((first, later))

    assert result == (later,)


def test_signal_deduplication_rejects_conflicting_reuse():
    first = OpportunitySignal(
        signal_id="signal-1",
        company="Acme GmbH",
        signal_type="funding",
        strength=0.8,
        observed_at=OBSERVED,
        source="https://source.test/evidence",
    )
    conflicting = OpportunitySignal(
        signal_id="signal-1",
        company="Other GmbH",
        signal_type="funding",
        strength=0.8,
        observed_at=OBSERVED,
        source="https://source.test/evidence",
    )

    with pytest.raises(ValueError, match="conflicting evidence"):
        deduplicate_opportunity_signals((first, conflicting))


def test_employer_intelligence_aggregates_only_observed_evidence():
    signals = (
        OpportunitySignal(
            signal_id="job-1",
            company="Acme GmbH",
            signal_type="active_job_posting",
            strength=1.0,
            observed_at=OBSERVED,
            source="https://jobs.test/1",
            metadata={
                "job_title": "Warehouse Lead",
                "city": "Karlsruhe",
                "country": "Germany",
            },
        ),
        OpportunitySignal(
            signal_id="job-2",
            company="ACME GMBH",
            signal_type="active_job_posting",
            strength=1.0,
            observed_at=OBSERVED + timedelta(minutes=5),
            source="https://jobs.test/2",
            metadata={
                "job_title": "Logistics Specialist",
                "city": "Ettlingen",
                "country": "Germany",
            },
        ),
    )

    result = EmployerIntelligenceService().aggregate(signals)

    assert len(result) == 1
    intelligence = result[0]
    assert intelligence.company == "ACME GMBH"
    assert intelligence.active_job_count == 2
    assert intelligence.signal_types == ("active_job_posting",)
    assert intelligence.sources == ("https://jobs.test/1", "https://jobs.test/2")
    assert intelligence.role_titles == ("Logistics Specialist", "Warehouse Lead")
    assert intelligence.locations == ("Ettlingen, Germany", "Karlsruhe, Germany")
    assert intelligence.latest_observed_at == OBSERVED + timedelta(minutes=5)
    assert "2 attributable hiring evidence item(s)" in intelligence.summary


def test_opportunity_signal_strict_validation_and_utc_normalization():
    signal = OpportunitySignal(
        company=" Acme ",
        signal_type=" funding ",
        strength=0.5,
        observed_at=datetime(
            2026, 9, 24, 14, 0, tzinfo=timezone(timedelta(hours=2))
        ),
    )
    assert signal.company == "Acme"
    assert signal.signal_type == "funding"
    assert signal.observed_at == OBSERVED
    assert signal.signal_id.startswith("signal:")

    with pytest.raises(TypeError):
        OpportunitySignal(company=42, signal_type="funding", strength=1.0)
    with pytest.raises(TypeError):
        OpportunitySignal(company="Acme", signal_type="funding", strength=True)
    with pytest.raises(ValueError):
        OpportunitySignal(
            company="Acme",
            signal_type="funding",
            strength=float("nan"),
        )
    with pytest.raises(ValueError):
        OpportunitySignal(
            company="Acme",
            signal_type="bad\ud800type",
            strength=1.0,
        )
