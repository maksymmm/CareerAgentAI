from __future__ import annotations

from abc import ABC, abstractmethod

from career_agent_ai.application.jobs.job_note import JobNote


class JobNoteRepository(ABC):

    @abstractmethod
    def add(
        self,
        note: JobNote,
    ) -> None:
        ...

    @abstractmethod
    def list(
        self,
        user_id: str,
    ) -> tuple[JobNote, ...]:
        ...

    @abstractmethod
    def clear(self) -> None:
        ...