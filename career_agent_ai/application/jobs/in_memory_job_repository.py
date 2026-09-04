from __future__ import annotations

from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_query import JobQuery
from career_agent_ai.application.jobs.job_repository import JobRepository
from career_agent_ai.application.jobs.job_search_result import JobSearchResult


class InMemoryJobRepository(JobRepository):

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def add(self, job: Job) -> None:
        self._jobs[job.job_id] = job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def all(self) -> tuple[Job, ...]:
        return tuple(self._jobs.values())

    def search(self, query: JobQuery) -> JobSearchResult:
        filters = query.filters

        keyword = filters.keyword.strip().lower()
        country = filters.country.strip().lower()
        city = filters.city.strip().lower()
        company = filters.company.strip().lower()

        jobs: list[Job] = []

        for job in self._jobs.values():

            if keyword:
                title_match = keyword in job.title.lower()
                company_match = keyword in job.company.name.lower()
                description_match = (
                    keyword in job.description.lower()
                    if job.description
                    else False
                )

                if (
                    not title_match
                    and not company_match
                    and not description_match
                ):
                    continue

            if country:
                if country not in job.location.country.lower():
                    continue

            if city:
                if city not in job.location.city.lower():
                    continue

            if company:
                if company not in job.company.name.lower():
                    continue

            if filters.employment_type is not None:
                if job.employment_type != filters.employment_type:
                    continue

            if filters.remote_only:
                is_remote = "remote" in job.location.city.lower()

                if not is_remote:
                    continue

            jobs.append(job)

        result = tuple(jobs)

        return JobSearchResult(
            jobs=result,
            total=len(result),
            page=query.page,
            page_size=query.page_size,
        )

    def clear(self) -> None:
        self._jobs.clear()
