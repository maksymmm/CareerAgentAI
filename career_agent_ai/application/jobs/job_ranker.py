from __future__ import annotations

from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_filter import JobFilter
from career_agent_ai.application.jobs.job_relevance_scorer import (
    JobRelevanceScorer,
)
from career_agent_ai.application.jobs.job_score import JobScore


class JobRanker:

    def __init__(
        self,
        relevance_scorer: JobRelevanceScorer | None = None,
    ) -> None:
        self._relevance_scorer = (
            relevance_scorer
            or JobRelevanceScorer()
        )

    def rank(
        self,
        jobs: tuple[Job, ...],
        filters: JobFilter | None = None,
    ) -> tuple[JobScore, ...]:
        ranked: list[JobScore] = []

        for job in jobs:
            if filters is None:
                score = self._legacy_score(job)
            else:
                score = self._relevance_scorer.score(
                    job,
                    filters,
                ).total

            ranked.append(
                JobScore(
                    job=job,
                    score=score,
                )
            )

        ranked.sort(
            key=lambda item: (
                item.score,
                self._salary_value(
                    item.job
                ),
            ),
            reverse=True,
        )

        return tuple(ranked)

    @staticmethod
    def _legacy_score(
        job: Job,
    ) -> float:
        salary = getattr(
            job,
            "salary",
            None,
        )

        maximum = getattr(
            salary,
            "maximum",
            None,
        ) if salary is not None else None

        score = 0.0

        if maximum is not None:
            try:
                score += (
                    float(maximum)
                    / 100000.0
                )
            except (
                TypeError,
                ValueError,
            ):
                pass

        location = getattr(
            job,
            "location",
            None,
        )

        if location is not None:
            city = (
                getattr(
                    location,
                    "city",
                    "",
                )
                or ""
            ).strip()

            if city:
                score += 0.1

        return score

    @staticmethod
    def _salary_value(
        job: Job,
    ) -> float:
        salary = getattr(
            job,
            "salary",
            None,
        )

        if salary is None:
            return 0.0

        maximum = getattr(
            salary,
            "maximum",
            None,
        )

        if maximum is None:
            return 0.0

        try:
            return float(maximum)
        except (
            TypeError,
            ValueError,
        ):
            return 0.0