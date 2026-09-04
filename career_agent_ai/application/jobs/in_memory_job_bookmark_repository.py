from __future__ import annotations

from career_agent_ai.application.jobs.job_bookmark import JobBookmark
from career_agent_ai.application.jobs.job_bookmark_repository import (
    JobBookmarkRepository,
)


class InMemoryJobBookmarkRepository(JobBookmarkRepository):

    def __init__(self) -> None:
        self._items: list[JobBookmark] = []

    def add(self, bookmark: JobBookmark) -> None:
        if not self.exists(
            bookmark.user_id,
            bookmark.job_id,
        ):
            self._items.append(bookmark)

    def remove(
        self,
        user_id: str,
        job_id: str,
    ) -> None:
        self._items = [
            item
            for item in self._items
            if not (
                item.user_id == user_id
                and item.job_id == job_id
            )
        ]

    def exists(
        self,
        user_id: str,
        job_id: str,
    ) -> bool:
        return any(
            item.user_id == user_id
            and item.job_id == job_id
            for item in self._items
        )

    def list(
        self,
        user_id: str,
    ) -> tuple[JobBookmark, ...]:
        return tuple(
            item
            for item in self._items
            if item.user_id == user_id
        )