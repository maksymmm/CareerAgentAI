from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from hashlib import sha256
from time import sleep as _sleep

from career_agent_ai.application.career.opportunity_signal import OpportunitySignal
from career_agent_ai.application.career.opportunity_signal_provider import (
    OpportunitySignalProviderError,
)
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.search.job_provider import JobProvider
from career_agent_ai.application.search.providers.arbeitnow_provider import (
    ArbeitnowProvider,
)


class ArbeitnowOpportunitySignalProvider:
    """Derive sourced employer hiring-activity signals from the public Arbeitnow API."""

    SOURCE_URL = "https://www.arbeitnow.com/api/job-board-api"

    def __init__(
        self,
        job_provider: JobProvider | None = None,
        *,
        queries: tuple[str, ...] = ("",),
        max_attempts: int = 3,
        retry_delay_seconds: float = 0.25,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        if (
            not isinstance(max_attempts, int)
            or isinstance(max_attempts, bool)
            or max_attempts < 1
            or max_attempts > 10
        ):
            raise ValueError("max_attempts must be between 1 and 10.")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must not be negative.")
        normalized_queries: list[str] = []
        for query in queries:
            if not isinstance(query, str):
                raise TypeError("queries must contain text values.")
            normalized = " ".join(query.strip().split())
            if normalized not in normalized_queries:
                normalized_queries.append(normalized)
        if not normalized_queries:
            raise ValueError("queries must contain at least one query.")

        self._job_provider = job_provider or ArbeitnowProvider()
        self._queries = tuple(normalized_queries)
        self._max_attempts = max_attempts
        self._retry_delay_seconds = float(retry_delay_seconds)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep or _sleep

    def collect(self) -> tuple[OpportunitySignal, ...]:
        """Collect real public-job evidence and derive one signal per employer."""
        observed_at = self._clock()
        if not isinstance(observed_at, datetime):
            raise TypeError("clock must return a datetime.")
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime.")
        observed_at = observed_at.astimezone(timezone.utc)

        evidence_by_job: dict[str, tuple[Job, set[str]]] = {}
        for query in self._queries:
            jobs = self._search_with_retry(query)
            for job in jobs:
                job_id = self._trusted_job_id(job)
                company = self._company_name(job)
                if company is None:
                    continue
                existing = evidence_by_job.get(job_id)
                if existing is None:
                    evidence_by_job[job_id] = (job, {query})
                    continue
                existing_company = self._company_name(existing[0])
                if (
                    existing_company is None
                    or existing_company.casefold() != company.casefold()
                ):
                    raise OpportunitySignalProviderError(
                        "Arbeitnow returned one job identity for different companies."
                    )
                existing[1].add(query)

        grouped: dict[str, list[tuple[Job, set[str]]]] = defaultdict(list)
        display_names: dict[str, str] = {}
        for job, matched_queries in evidence_by_job.values():
            company = self._company_name(job)
            if company is None:  # Defensive: anonymous evidence is filtered above.
                continue
            key = company.casefold()
            display_names.setdefault(key, company)
            grouped[key].append((job, matched_queries))

        signals = [
            self._build_signal(
                display_names[key],
                tuple(company_evidence),
                observed_at,
            )
            for key, company_evidence in grouped.items()
        ]
        return tuple(
            sorted(
                signals,
                key=lambda signal: (
                    signal.company.casefold(),
                    signal.signal_id,
                ),
            )
        )

    def _search_with_retry(self, query: str) -> tuple[Job, ...]:
        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                result = self._job_provider.search(query)
                if not isinstance(result, tuple):
                    result = tuple(result)
                if any(not isinstance(job, Job) for job in result):
                    raise TypeError("job provider returned a non-Job value.")
                return result
            except Exception as exc:
                last_error = exc
                if attempt + 1 >= self._max_attempts:
                    break
                delay = self._retry_delay_seconds * (2**attempt)
                if delay:
                    self._sleep(delay)
        raise OpportunitySignalProviderError(
            f"Failed to collect Arbeitnow evidence for query {query!r}."
        ) from last_error

    @classmethod
    def _build_signal(
        cls,
        company: str,
        evidence: tuple[tuple[Job, set[str]], ...],
        observed_at: datetime,
    ) -> OpportunitySignal:
        ordered = tuple(
            sorted(
                evidence,
                key=lambda item: cls._trusted_job_id(item[0]),
            )
        )
        job_ids = tuple(cls._trusted_job_id(item[0]) for item in ordered)
        titles = tuple(item[0].title.strip() for item in ordered)
        urls = tuple(
            item[0].url.strip()
            for item in ordered
            if isinstance(item[0].url, str) and item[0].url.strip()
        )
        queries = tuple(
            sorted(
                {
                    query
                    for _, matched_queries in ordered
                    for query in matched_queries
                }
            )
        )
        evidence_hash = sha256(
            "\n".join(job_ids).encode("utf-8")
        ).hexdigest()
        company_hash = sha256(company.casefold().encode("utf-8")).hexdigest()
        signal_id = f"arbeitnow:hiring-activity:{company_hash}"
        count = len(job_ids)
        strength = min(count / 5.0, 1.0)

        return OpportunitySignal(
            company=company,
            signal_type="hiring_activity",
            strength=strength,
            observed_at=observed_at,
            source=cls.SOURCE_URL,
            signal_id=signal_id,
            metadata={
                "provider": "arbeitnow",
                "observation_method": "active_public_job_count",
                "evidence_count": count,
                "evidence_fingerprint": evidence_hash,
                "job_ids": job_ids,
                "job_titles": titles,
                "job_urls": urls,
                "queries": queries,
            },
        )

    @staticmethod
    def _trusted_job_id(job: Job) -> str:
        if not isinstance(job.job_id, str) or not job.job_id.strip():
            raise OpportunitySignalProviderError(
                "Arbeitnow evidence is missing a stable job identifier."
            )
        return job.job_id.strip()

    @staticmethod
    def _company_name(job: Job) -> str | None:
        """Return a trustworthy provider company name or discard anonymous evidence."""
        company = getattr(job.company, "name", None)
        if not isinstance(company, str):
            return None
        normalized = company.strip()
        if not normalized or normalized.casefold() == "unknown company":
            return None
        return normalized
