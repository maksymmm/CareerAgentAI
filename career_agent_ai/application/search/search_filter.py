from __future__ import annotations

from dataclasses import dataclass

from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_filter import JobFilter


@dataclass(frozen=True)
class SearchFilter:
    keyword: str = ""
    location: str = ""
    remote_only: bool = False
    company: str = ""

    def apply(
        self,
        jobs: tuple[Job, ...],
        filters: JobFilter,
    ) -> tuple[Job, ...]:

        result = jobs

        if filters.keyword:
            keyword = filters.keyword.strip().lower()

            result = tuple(
                job
                for job in result
                if (
                    keyword in (job.title or "").lower()
                    or keyword in (job.description or "").lower()
                    or (
                        job.company is not None
                        and keyword
                        in (job.company.name or "").lower()
                    )
                )
            )

        if filters.country:
            country = filters.country.strip().lower()

            result = tuple(
                job
                for job in result
                if (
                    job.location is not None
                    and (job.location.country or "").lower() == country
                )
            )

        if filters.city:
            city = filters.city.strip().lower()

            result = tuple(
                job
                for job in result
                if (
                    job.location is not None
                    and (job.location.city or "").lower() == city
                )
            )

        if filters.remote_only:
            result = tuple(
                job
                for job in result
                if (
                    job.location is not None
                    and (
                        getattr(job.location, "remote", False)
                        or (
                            getattr(job.location, "city", "") or ""
                        ).lower() == "remote"
                    )
                )
            )

        if filters.employment_type is not None:
            result = tuple(
                job
                for job in result
                if job.employment_type == filters.employment_type
            )

        if filters.company:
            company = filters.company.strip().lower()

            result = tuple(
                job
                for job in result
                if (
                    job.company is not None
                    and (job.company.name or "").lower() == company
                )
            )

        return result


class SearchFilterEngine(SearchFilter):
    pass
