from __future__ import annotations

from career_agent_ai.application.jobs.job import Job


class SearchPagination:
    def paginate(
        self,
        jobs: tuple[Job, ...],
        page: int,
        page_size: int,
    ) -> tuple[Job, ...]:
        if page < 1:
            raise ValueError("page must be greater than or equal to 1")

        if page_size < 1:
            raise ValueError("page_size must be greater than or equal to 1")

        start = (page - 1) * page_size
        end = start + page_size

        return jobs[start:end]