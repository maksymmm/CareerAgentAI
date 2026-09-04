from __future__ import annotations

from abc import ABC, abstractmethod

from career_agent_ai.application.jobs.job_application import JobApplication


class JobApplicationRepository(ABC):

    @abstractmethod
    def add(
        self,
        application: JobApplication,
    ) -> None:
        ...

    @abstractmethod
    def get(
        self,
        application_id: str,
    ) -> JobApplication | None:
        ...

    @abstractmethod
    def list(
        self,
        user_id: str,
    ) -> tuple[JobApplication, ...]:
        ...

    @abstractmethod
    def clear(self) -> None:
        ...