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
        self._submission_claims: dict[str, str] = {}

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
        if application.application_id in self._submission_claims:
            raise ApplicationConflictError(
                "Application is reserved for an in-flight submission."
            )
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

    def claim_submission(
        self,
        application_id: str,
        operation_id: str,
        *,
        expected_version: int,
    ) -> JobApplication:
        """Atomically reserve a saved application version for submission."""
        normalized_id = self._required_identifier(application_id, "application_id")
        normalized_operation = self._required_identifier(operation_id, "operation_id")
        current = self._items.get(normalized_id)
        existing_claim = self._submission_claims.get(normalized_id)
        if (
            current is None
            or current.version != expected_version
            or current.status.value != "saved"
            or existing_claim not in (None, normalized_operation)
        ):
            raise ApplicationConflictError(
                "Application cannot be claimed for submission."
            )
        if any(
            claimed_operation == normalized_operation and claimed_id != normalized_id
            for claimed_id, claimed_operation in self._submission_claims.items()
        ):
            raise ApplicationConflictError(
                "Submission operation is already bound to another application."
            )
        self._submission_claims[normalized_id] = normalized_operation
        return current

    def release_submission(self, application_id: str, operation_id: str) -> None:
        """Release a matching submission claim."""
        normalized_id = self._required_identifier(application_id, "application_id")
        normalized_operation = self._required_identifier(operation_id, "operation_id")
        if self._submission_claims.get(normalized_id) != normalized_operation:
            raise ApplicationConflictError("Submission claim is no longer releasable.")
        del self._submission_claims[normalized_id]

    def complete_submission(
        self,
        application: JobApplication,
        operation_id: str,
        *,
        expected_version: int,
    ) -> None:
        """Apply the claimed saved-to-applied transition atomically."""
        normalized_operation = self._required_identifier(operation_id, "operation_id")
        current = self._items.get(application.application_id)
        if (
            current is None
            or current.version != expected_version
            or current.status.value != "saved"
            or self._submission_claims.get(application.application_id)
            != normalized_operation
            or application.version != expected_version + 1
            or application.status.value != "applied"
            or normalized_operation not in application.external_action_operation_ids
        ):
            raise ApplicationConflictError(
                "Claimed application submission cannot be completed."
            )
        if (
            application.user_id != current.user_id
            or application.job_id != current.job_id
            or application.created_at != current.created_at
        ):
            raise ApplicationConflictError("Application identity cannot be changed.")
        self._items[application.application_id] = application
        del self._submission_claims[application.application_id]

    def clear(self) -> None:
        self._items.clear()
        self._submission_claims.clear()

    @staticmethod
    def _required_identifier(value: str, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must not be empty.")
        return value.strip()
