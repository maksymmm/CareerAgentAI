from dataclasses import dataclass, field

from career_agent_ai.application.jobs.job_filter import JobFilter
from career_agent_ai.application.jobs.job_sort import JobSort


@dataclass(frozen=True)
class JobQuery:
    filters: JobFilter = field(
        default_factory=JobFilter
    )

    page: int = 1

    page_size: int = 20

    sort: JobSort = JobSort.RELEVANCE
