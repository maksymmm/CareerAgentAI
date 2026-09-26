from __future__ import annotations

from datetime import datetime, timezone

import pytest

from career_agent_ai.application.career.opportunity_signal_provider import (
    OpportunitySignalProviderError,
)
from career_agent_ai.application.career.providers.arbeitnow_opportunity_signal_provider import (
    ArbeitnowOpportunitySignalProvider,
)
from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.location import Location


OBSERVED_AT = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def job(job_id: str, company: str, title: str, url: str = "") -> Job:
    return Job.create(
        job_id=job_id,
        title=title,
        company=Company(company_id=f"company:{company.lower()}", name=company),
        location=Location(country="Germany", city="Karlsruhe", remote=False),
        url=url,
    )


class QueryJobProvider:
    def __init__(self, results: dict[str, tuple[Job, ...]]) -> None:
        self.results = results
        self.calls: list[str] = []

    def search(self, query: str) -> tuple[Job, ...]:
        self.calls.append(query)
        return self.results.get(query, ())


def test_collect_builds_sourced_signals_and_deduplicates_jobs_across_queries():
    shared = job(
        "arbeitnow:shared",
        "Acme GmbH",
        "Python Engineer",
        "https://example.test/shared",
    )
    provider = QueryJobProvider(
        {
            "python": (
                shared,
                job(
                    "arbeitnow:second",
                    "Acme GmbH",
                    "Backend Engineer",
                    "https://example.test/second",
                ),
            ),
            "backend": (
                shared,
                job(
                    "arbeitnow:beta",
                    "Beta AG",
                    "Backend Developer",
                    "https://example.test/beta",
                ),
            ),
        }
    )
    signals = ArbeitnowOpportunitySignalProvider(
        provider,
        queries=(" python ", "backend", "python"),
        clock=lambda: OBSERVED_AT,
    ).collect()

    assert tuple(signal.company for signal in signals) == ("Acme GmbH", "Beta AG")
    acme = signals[0]
    assert acme.signal_type == "hiring_activity"
    assert acme.strength == pytest.approx(0.4)
    assert acme.observed_at == OBSERVED_AT
    assert acme.source == ArbeitnowOpportunitySignalProvider.SOURCE_URL
    assert acme.signal_id.startswith("arbeitnow:hiring-activity:")
    assert acme.metadata["provider"] == "arbeitnow"
    assert acme.metadata["observation_method"] == "active_public_job_count"
    assert acme.metadata["evidence_count"] == 2
    assert acme.metadata["job_ids"] == (
        "arbeitnow:second",
        "arbeitnow:shared",
    )
    assert acme.metadata["queries"] == ("backend", "python")
    assert provider.calls == ["python", "backend"]


def test_repeated_company_observations_keep_stable_signal_identity_as_evidence_changes():
    class ChangingProvider:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str) -> tuple[Job, ...]:
            self.calls += 1
            if self.calls == 1:
                return (job("arbeitnow:one", "Acme GmbH", "Engineer"),)
            return (
                job("arbeitnow:one", "Acme GmbH", "Engineer"),
                job("arbeitnow:two", "Acme GmbH", "Developer"),
            )

    provider = ChangingProvider()
    collector = ArbeitnowOpportunitySignalProvider(
        provider,
        clock=lambda: OBSERVED_AT,
    )

    first = collector.collect()[0]
    second = collector.collect()[0]

    assert first.signal_id == second.signal_id
    assert first.metadata["evidence_fingerprint"] != second.metadata["evidence_fingerprint"]
    assert first.metadata["evidence_count"] == 1
    assert second.metadata["evidence_count"] == 2


def test_collect_discards_anonymous_provider_placeholder_company():
    provider = QueryJobProvider(
        {
            "": (
                job("arbeitnow:anonymous", "Unknown company", "Engineer"),
                job("arbeitnow:trusted", "Acme GmbH", "Developer"),
            )
        }
    )

    signals = ArbeitnowOpportunitySignalProvider(
        provider,
        clock=lambda: OBSERVED_AT,
    ).collect()

    assert tuple(signal.company for signal in signals) == ("Acme GmbH",)
    assert signals[0].metadata["job_ids"] == ("arbeitnow:trusted",)


@pytest.mark.parametrize(
    "bad_job",
    [
        job("bad\x00id", "Acme GmbH", "Engineer"),
        job("arbeitnow:bad-title", "Acme GmbH", "bad\ud800title"),
        job("arbeitnow:bad-url", "Acme GmbH", "Engineer", "https://bad\x00url"),
    ],
)
def test_collect_rejects_malformed_provider_evidence(bad_job):
    provider = QueryJobProvider({"": (bad_job,)})

    with pytest.raises(OpportunitySignalProviderError, match="Arbeitnow"):
        ArbeitnowOpportunitySignalProvider(
            provider,
            clock=lambda: OBSERVED_AT,
        ).collect()


def test_collect_retries_transient_provider_failure_with_bounded_backoff():
    value = job("arbeitnow:one", "Acme GmbH", "Engineer")

    class FlakyProvider:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str) -> tuple[Job, ...]:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("HTTP 429")
            return (value,)

    sleeps: list[float] = []
    flaky = FlakyProvider()
    signals = ArbeitnowOpportunitySignalProvider(
        flaky,
        max_attempts=2,
        retry_delay_seconds=0.5,
        sleep=sleeps.append,
        clock=lambda: OBSERVED_AT,
    ).collect()

    assert len(signals) == 1
    assert flaky.calls == 2
    assert sleeps == [0.5]


def test_collect_surfaces_permanent_provider_failure_without_fabricating_signals():
    class BrokenProvider:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str) -> tuple[Job, ...]:
            self.calls += 1
            raise RuntimeError("service unavailable")

    provider = BrokenProvider()
    with pytest.raises(OpportunitySignalProviderError, match="Failed to collect"):
        ArbeitnowOpportunitySignalProvider(
            provider,
            max_attempts=2,
            retry_delay_seconds=0,
            clock=lambda: OBSERVED_AT,
        ).collect()

    assert provider.calls == 2


def test_collect_empty_evidence_returns_no_signal():
    provider = QueryJobProvider({"": ()})
    result = ArbeitnowOpportunitySignalProvider(
        provider,
        clock=lambda: OBSERVED_AT,
    ).collect()

    assert result == ()


def test_collect_rejects_naive_observation_clock():
    provider = QueryJobProvider({"": ()})
    with pytest.raises(ValueError, match="timezone-aware"):
        ArbeitnowOpportunitySignalProvider(
            provider,
            clock=lambda: datetime(2026, 9, 25, 12, 0),
        ).collect()


def test_collect_rejects_conflicting_company_for_same_evidence_id():
    provider = QueryJobProvider(
        {
            "first": (job("same-id", "Alpha", "Engineer"),),
            "second": (job("same-id", "Beta", "Engineer"),),
        }
    )

    with pytest.raises(OpportunitySignalProviderError, match="different companies"):
        ArbeitnowOpportunitySignalProvider(
            provider,
            queries=("first", "second"),
            clock=lambda: OBSERVED_AT,
        ).collect()


@pytest.mark.parametrize(
    "kwargs,error_type",
    [
        ({"queries": ()}, ValueError),
        ({"queries": (42,)}, TypeError),
        ({"max_attempts": 0}, ValueError),
        ({"retry_delay_seconds": -1}, ValueError),
    ],
)
def test_provider_configuration_is_validated(kwargs, error_type):
    with pytest.raises(error_type):
        ArbeitnowOpportunitySignalProvider(QueryJobProvider({}), **kwargs)
