from dataclasses import dataclass, field

from career_agent_ai.application.jobs.job import Job


@dataclass(frozen=True)
class JobCollection:
    jobs: tuple[Job, ...] = field(default_factory=tuple)

    def __len__(self) -> int:
        return len(self.jobs)

    def empty(self) -> bool:
        return len(self.jobs) == 0