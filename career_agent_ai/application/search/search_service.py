from __future__ import annotations

from career_agent_ai.application.jobs.job_query import JobQuery
from career_agent_ai.application.jobs.job_repository import JobRepository
from career_agent_ai.application.search.job_deduplicator import JobDeduplicator
from career_agent_ai.application.search.job_ranking_service import JobRankingService
from career_agent_ai.application.search.search_pagination import SearchPagination
from career_agent_ai.application.search.search_response import SearchResponse
from career_agent_ai.application.search.search_sorting import SearchSorting


class SearchService:

    def __init__(
        self,
        repository: JobRepository,
    ) -> None:
        self._repository = repository
        self._deduplicator = JobDeduplicator()
        self._ranking = JobRankingService()
        self._sorting = SearchSorting()
        self._pagination = SearchPagination()

    def search(
        self,
        query: JobQuery,
    ) -> SearchResponse:
        result = self._repository.search(query)

        jobs = tuple(result.jobs)

        jobs = self._deduplicator.deduplicate(
            jobs
        )

        total = len(jobs)

        # Relevance is the primary ordering mechanism.
        # Explicit sorting options can override it.
        jobs = self._ranking.rank(
            jobs,
            query.filters,
        )

        jobs = self._sorting.sort(
            jobs,
            query.sort,
        )

        jobs = self._pagination.paginate(
            jobs,
            query.page,
            query.page_size,
        )

        return SearchResponse(
            jobs=jobs,
            total=total,
            page=query.page,
            page_size=query.page_size,
        )
