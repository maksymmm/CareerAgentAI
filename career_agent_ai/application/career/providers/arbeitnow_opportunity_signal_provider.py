"""Arbeitnow-backed hiring-evidence signal provider."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlparse

from career_agent_ai.application.career.opportunity_signal import (
    OpportunitySignal,
    deduplicate_opportunity_signals,
)
from career_agent_ai.application.career.opportunity_signal_provider import (
    OpportunitySignalProviderError,
)
from career_agent_ai.application.search.job_provider import JobProvider
from career_agent_ai.application.search.providers.arbeitnow_provider import (
    ArbeitnowProvider,
)


class ArbeitnowOpportunitySignalProvider:
    """Collect attributable hiring evidence from the public Arbeitnow API.

    Each emitted signal corresponds to an actual public job record. The adapter does
    not infer funding, growth, or hidden vacancies from unrelated data.
    """

    ACTIVE_JOB_SIGNAL_TYPE = "active_job_posting"

    def __init__(
        self,
        *,
        query: str = "",
        job_provider: JobProvider | None = None,
        timeout: float = 20.0,
        minimum_interval_seconds: float = 1.0,
        max_attempts: int = 2,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        if not isinstance(query, str):
            raise TypeError("query must be text.")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero.")
        if minimum_interval_seconds < 0:
            raise ValueError("minimum_interval_seconds must not be negative.")
        if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer.")

        self._query = " ".join(query.strip().split())
        self._job_provider = job_provider or ArbeitnowProvider(
            timeout=timeout,
            max_pages=1,
        )
        self._minimum_interval_seconds = float(minimum_interval_seconds)
        self._max_attempts = max_attempts
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        self._sleeper = sleeper or time.sleep
        self._last_request_at: float | None = None

    def collect(self) -> tuple[OpportunitySignal, ...]:
        """Fetch current public postings and convert them to attributable signals."""
        jobs = self._search_with_retries()
        observed_at = self._observation_time()

        signals: list[OpportunitySignal] = []
        for job in jobs:
            company = str(getattr(getattr(job, "company", None), "name", "")).strip()
            if not company or company.casefold() == "unknown company":
                continue

            job_id = str(getattr(job, "job_id", "")).strip()
            title = str(getattr(job, "title", "")).strip()
            if not job_id or not title:
                continue

            job_url = str(getattr(job, "url", "")).strip()
            source = (
                job_url
                if self._is_http_url(job_url)
                else ArbeitnowProvider.BASE_URL
            )
            created_at = getattr(job, "created_at", None)
            published_at = (
                created_at.astimezone(timezone.utc).isoformat()
                if isinstance(created_at, datetime)
                and created_at.tzinfo is not None
                and created_at.utcoffset() is not None
                else None
            )
            location = getattr(job, "location", None)
            city = str(getattr(location, "city", "")).strip()
            country = str(getattr(location, "country", "")).strip()
            remote = bool(getattr(location, "remote", False))

            signals.append(
                OpportunitySignal(
                    signal_id=f"arbeitnow-signal:{job_id}",
                    company=company,
                    signal_type=self.ACTIVE_JOB_SIGNAL_TYPE,
                    strength=1.0,
                    observed_at=observed_at,
                    source=source,
                    metadata={
                        "provider": "arbeitnow",
                        "job_id": job_id,
                        "job_title": title,
                        "posting_url": job_url,
                        "published_at": published_at,
                        "city": city,
                        "country": country,
                        "remote": remote,
                    },
                )
            )

        return deduplicate_opportunity_signals(signals)

    def _search_with_retries(self):
        last_error: Exception | None = None
        for _attempt in range(1, self._max_attempts + 1):
            self._respect_rate_limit()
            try:
                return self._job_provider.search(self._query)
            except (RuntimeError, OSError, TimeoutError) as exc:
                last_error = exc

        message = "Arbeitnow signal collection failed after bounded retries."
        if last_error is None:
            raise OpportunitySignalProviderError(message)
        raise OpportunitySignalProviderError(message) from last_error

    def _respect_rate_limit(self) -> None:
        now = self._monotonic()
        if self._last_request_at is not None:
            elapsed = max(0.0, now - self._last_request_at)
            wait_for = self._minimum_interval_seconds - elapsed
            if wait_for > 0:
                self._sleeper(wait_for)
                now = self._monotonic()
        self._last_request_at = now

    def _observation_time(self) -> datetime:
        observed_at = self._clock()
        if not isinstance(observed_at, datetime):
            raise OpportunitySignalProviderError("Signal clock returned a non-datetime value.")
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise OpportunitySignalProviderError("Signal clock must return timezone-aware time.")
        return observed_at.astimezone(timezone.utc)

    @staticmethod
    def _is_http_url(value: str) -> bool:
        if not value:
            return False
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
