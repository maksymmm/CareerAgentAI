from __future__ import annotations

from career_agent_ai.application.jobs.job_application import JobApplication
from career_agent_ai.application.jobs.job_application_repository import (
    JobApplicationRepository,
)


class InMemoryJobApplicationRepository(JobApplicationRepository):

    def __init__(self) -> None:
        self._items: dict[str, JobApplication] = {}

    def add(
        self,
        application: JobApplication,
    ) -> None:
        self._items[application.application_id] = application

    def get(
        self,
        application_id: str,
    ) -> JobApplication | None:
        return self._items.get(application_id)

    def list(
        self,
        user_id: str,
    ) -> tuple[JobApplication, ...]:
        return tuple(
            item
            for item in self._items.values()
            if item.user_id == user_id
        )

    def clear(self) -> None:
        self._items.clear()