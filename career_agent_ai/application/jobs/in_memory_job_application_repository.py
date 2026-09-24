from __future__ import annotations

from career_agent_ai.application.jobs.job_application import JobApplication
from career_agent_ai.application.jobs.job_application_repository import (
    ApplicationConflictError,
    ApplicationQuery,
    JobApplicationRepository,
)


class InMemoryJobApplicationRepository(JobApplicationRepository):

    def __init__(self) -> None:
        self._items: dict[str, JobApplication] = {}

    def add(
        self,
        application: JobApplication,
    ) -> None:
        if application.application_id in self._items or any(
            item.user_id == application.user_id and item.job_id == application.job_id
            for item in self._items.values()
        ):
            raise ApplicationConflictError("An application for this user and job already exists.")
        self._items[application.application_id] = application

    def get(
        self,
        application_id: str,
    ) -> JobApplication | None:
        return self._items.get(self._required_identifier(application_id, "application_id"))

    def list(
        self,
        user_id: str,
    ) -> tuple[JobApplication, ...]:
        return self.find(ApplicationQuery(user_id=user_id))

    def update(self, application: JobApplication, *, expected_version: int) -> None:
        """Replace an existing aggregate using optimistic concurrency."""
        current = self._items.get(application.application_id)
        if (
            current is None
            or expected_version < 1
            or current.version != expected_version
            or application.version != expected_version + 1
        ):
            raise ApplicationConflictError("Application is missing or has changed.")
        if application.user_id != current.user_id or application.job_id != current.job_id:
            raise ApplicationConflictError("Application identity cannot be changed.")
        if application.created_at != current.created_at:
            raise ApplicationConflictError("Application created_at cannot be changed.")
        self._items[application.application_id] = application

    def find(self, query: ApplicationQuery) -> tuple[JobApplication, ...]:
        """Return matching applications in deterministic order."""
        items = self._items.values()
        result = (
            item for item in items
            if (query.user_id is None or item.user_id == query.user_id)
            and (query.job_id is None or item.job_id == query.job_id)
            and (query.company_id is None or item.company_id == query.company_id)
            and (query.status is None or item.status == query.status)
            and (query.external_action_operation_id is None or query.external_action_operation_id in item.external_action_operation_ids)
        )
        return tuple(sorted(result, key=lambda item: (item.created_at, item.application_id)))

    def clear(self) -> None:
        self._items.clear()

    @staticmethod
    def _required_identifier(value: str, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must not be empty.")
        return value.strip()
