from dataclasses import dataclass, field

from career_agent_ai.application.jobs.job import Job


@dataclass(frozen=True)
class JobSearchResult:
    jobs: tuple[Job, ...] = field(default_factory=tuple)

    total: int = 0

    page: int = 1

    page_size: int = 20

    @property
    def empty(self) -> bool:
        return self.total == 0