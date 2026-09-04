from __future__ import annotations

from abc import ABC, abstractmethod

from career_agent_ai.application.jobs.job_bookmark import JobBookmark


class JobBookmarkRepository(ABC):

    @abstractmethod
    def add(self, bookmark: JobBookmark) -> None:
        ...

    @abstractmethod
    def remove(
        self,
        user_id: str,
        job_id: str,
    ) -> None:
        ...

    @abstractmethod
    def exists(
        self,
        user_id: str,
        job_id: str,
    ) -> bool:
        ...

    @abstractmethod
    def list(
        self,
        user_id: str,
    ) -> tuple[JobBookmark, ...]:
        ...