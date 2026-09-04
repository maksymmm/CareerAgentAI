from __future__ import annotations

from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_match import JobMatch


class JobMatcher:

    def match(
        self,
        keyword: str,
        jobs: tuple[Job, ...],
    ) -> tuple[JobMatch, ...]:

        keyword = keyword.lower()

        matches: list[JobMatch] = []

        for job in jobs:

            score = 0.0

            if keyword in job.title.lower():
                score += 1.0

            if keyword in job.company.name.lower():
                score += 0.5

            if score > 0:
                matches.append(
                    JobMatch(
                        job=job,
                        score=score,
                    )
                )

        matches.sort(
            key=lambda m: m.score,
            reverse=True,
        )

        return tuple(matches)