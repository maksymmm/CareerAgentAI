from __future__ import annotations

from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.search.job_provider import JobProvider
from career_agent_ai.application.search.job_deduplicator import JobDeduplicator
from career_agent_ai.application.search.search_result import SearchResult


class JobSearchEngine:

    def __init__(
        self,
        providers: list[JobProvider] | tuple[JobProvider, ...],
        deduplicator: JobDeduplicator | None = None,
    ) -> None:
        self._providers = tuple(providers)
        self._deduplicator = deduplicator or JobDeduplicator()

    def search(
        self,
        query: str,
    ) -> SearchResult:

        jobs: list[Job] = []

        for provider in self._providers:
            results = provider.search(query)

            jobs.extend(results)

        combined = tuple(jobs)

        deduplicated = self._deduplicator.deduplicate(
            combined,
        )

        return SearchResult(
            query=query,
            jobs=deduplicated,
        )
