from __future__ import annotations

from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_query import JobQuery
from career_agent_ai.application.jobs.job_repository import JobRepository
from career_agent_ai.application.jobs.job_search_result import JobSearchResult
from career_agent_ai.application.search.job_provider import JobProvider


class ProviderJobRepository(JobRepository):
    """
    Repository adapter for external job providers.

    Unlike InMemoryJobRepository, this repository does not
    permanently store the jobs. It asks the configured
    providers for fresh data whenever a search is performed.
    """

    def __init__(
        self,
        providers: list[JobProvider]
        | tuple[JobProvider, ...],
    ) -> None:
        self._providers = tuple(providers)

    def add(
        self,
        job: Job,
    ) -> None:
        raise NotImplementedError(
            "ProviderJobRepository is read-only."
        )

    def get(
        self,
        job_id: str,
    ) -> Job | None:
        for job in self.all():
            if job.job_id == job_id:
                return job

        return None

    def all(
        self,
    ) -> tuple[Job, ...]:
        jobs: list[Job] = []

        for provider in self._providers:
            jobs.extend(
                provider.search("")
            )

        return tuple(jobs)

    def search(
        self,
        query: JobQuery,
    ) -> JobSearchResult:
        search_text = self._build_search_text(
            query
        )

        jobs: list[Job] = []

        for provider in self._providers:
            jobs.extend(
                provider.search(search_text)
            )

        jobs = self._apply_filters(
            jobs,
            query,
        )

        result = tuple(jobs)

        return JobSearchResult(
            jobs=result,
            total=len(result),
            page=query.page,
            page_size=query.page_size,
        )

    def clear(
        self,
    ) -> None:
        raise NotImplementedError(
            "ProviderJobRepository is read-only."
        )

    @staticmethod
    def _build_search_text(
        query: JobQuery,
    ) -> str:
        filters = query.filters

        parts: list[str] = []

        if filters.keyword.strip():
            parts.append(
                filters.keyword.strip()
            )

        if filters.company.strip():
            parts.append(
                filters.company.strip()
            )

        if filters.city.strip():
            parts.append(
                filters.city.strip()
            )

        return " ".join(parts)

    @staticmethod
    def _apply_filters(
        jobs: list[Job],
        query: JobQuery,
    ) -> list[Job]:
        filters = query.filters

        result: list[Job] = []

        country = (
            filters.country.strip().lower()
        )

        city = (
            filters.city.strip().lower()
        )

        company = (
            filters.company.strip().lower()
        )

        for job in jobs:
            if country:
                job_country = (
                    job.location.country
                    or ""
                ).lower()

                if (
                    job_country
                    and country not in job_country
                ):
                    continue

            if city:
                job_city = (
                    job.location.city
                    or ""
                ).lower()

                if city not in job_city:
                    continue

            if company:
                company_name = (
                    job.company.name
                    or ""
                ).lower()

                if company not in company_name:
                    continue

            if filters.remote_only:
                if not job.location.remote:
                    continue

            if (
                filters.employment_type
                is not None
                and job.employment_type
                != filters.employment_type
            ):
                continue

            result.append(job)

        return result