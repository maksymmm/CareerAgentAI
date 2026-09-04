from __future__ import annotations

from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_sort import JobSort


class SearchSorting:

    def sort(
        self,
        jobs: tuple[Job, ...],
        sort: JobSort,
    ) -> tuple[Job, ...]:

        if not jobs:
            return ()

        if sort == JobSort.RELEVANCE:
            return tuple(jobs)

        if sort == JobSort.TITLE:
            return tuple(
                sorted(
                    jobs,
                    key=lambda job: (
                        job.title or ""
                    ).lower(),
                )
            )

        if sort == JobSort.COMPANY:
            return tuple(
                sorted(
                    jobs,
                    key=lambda job: (
                        job.company.name
                        if job.company is not None
                        else ""
                    ).lower(),
                )
            )

        if sort == JobSort.CITY:
            return tuple(
                sorted(
                    jobs,
                    key=lambda job: (
                        job.location.city
                        if job.location is not None
                        else ""
                    ).lower()
                )
            )

        if sort == JobSort.SALARY:
            return tuple(
                sorted(
                    jobs,
                    key=lambda job: (
                        job.salary.maximum
                        if job.salary is not None
                        else 0
                    ),
                    reverse=True,
                )
            )

        if sort == JobSort.SALARY_HIGH:
            return tuple(
                sorted(
                    jobs,
                    key=lambda job: (
                        job.salary.maximum
                        if job.salary is not None
                        else 0
                    ),
                    reverse=True,
                )
            )

        if sort == JobSort.SALARY_LOW:
            return tuple(
                sorted(
                    jobs,
                    key=lambda job: (
                        job.salary.minimum
                        if job.salary is not None
                        else 0
                    )
                )
            )

        if sort == JobSort.NEWEST:
            return tuple(
                sorted(
                    jobs,
                    key=lambda job: (
                        job.created_at
                        if job.created_at is not None
                        else 0
                    ),
                    reverse=True,
                )
            )

        return tuple(jobs)
