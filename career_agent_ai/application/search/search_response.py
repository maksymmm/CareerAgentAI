from __future__ import annotations

from dataclasses import dataclass

from career_agent_ai.application.jobs.job import Job


@dataclass(frozen=True)
class SearchResponse:
    jobs: tuple[Job, ...]

    total: int | None = None

    page: int = 1

    page_size: int = 20

    @property
    def count(self) -> int:
        return len(self.jobs)

    @property
    def empty(self) -> bool:
        return self.count == 0

    @property
    def has_next_page(self) -> bool:
        if self.total is None:
            return False

        return self.page * self.page_size < self.total

    @property
    def has_previous_page(self) -> bool:
        return self.page > 1
