from __future__ import annotations

from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_filter import JobFilter
from career_agent_ai.application.jobs.job_ranker import JobRanker


class JobRankingService:
    def __init__(
        self,
        ranker: JobRanker | None = None,
    ) -> None:
        self._ranker = ranker or JobRanker()

    def rank(
        self,
        jobs: tuple[Job, ...],
        filters: JobFilter | None = None,
    ) -> tuple[Job, ...]:
        if not jobs:
            return ()

        ranked = self._ranker.rank(
            jobs,
            filters,
        )

        return tuple(
            item.job
            for item in ranked
        )