from dataclasses import dataclass

from career_agent_ai.application.jobs.job import Job


@dataclass(frozen=True)
class JobMatch:
    job: Job

    score: float