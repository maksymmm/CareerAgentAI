from __future__ import annotations

from abc import ABC, abstractmethod

from dataclasses import dataclass

from career_agent_ai.application.jobs.job_application import JobApplication
from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus


class ApplicationConflictError(RuntimeError):
    """Raised when uniqueness or optimistic concurrency is violated."""


@dataclass(frozen=True)
class ApplicationQuery:
    """Deterministic filters for tracked applications."""

    user_id: str | None = None
    job_id: str | None = None
    company_id: str | None = None
    status: JobApplicationStatus | None = None
    external_action_operation_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("user_id", "job_id", "company_id", "external_action_operation_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} filter must not be empty.")
            if value is not None:
                object.__setattr__(self, name, value.strip())
        if self.status is not None and not isinstance(self.status, JobApplicationStatus):
            raise ValueError("status filter must be a JobApplicationStatus.")


class JobApplicationRepository(ABC):
    """Provider-neutral application aggregate persistence boundary."""

    @abstractmethod
    def add(
        self,
        application: JobApplication,
    ) -> None:
        """Create an application, rejecting duplicate IDs and user/job pairs."""

    @abstractmethod
    def get(
        self,
        application_id: str,
    ) -> JobApplication | None:
        """Return an application by identifier."""

    @abstractmethod
    def list(
        self,
        user_id: str,
    ) -> tuple[JobApplication, ...]:
        """Return a user's applications in deterministic order."""

    @abstractmethod
    def update(self, application: JobApplication, *, expected_version: int) -> None:
        """Replace an application using optimistic concurrency."""

    @abstractmethod
    def find(self, query: ApplicationQuery) -> tuple[JobApplication, ...]:
        """Return applications matching all supplied filters."""

    def claim_submission(
        self,
        application_id: str,
        operation_id: str,
        *,
        expected_version: int,
        claim_owner_id: str | None = None,
    ) -> JobApplication:
        """Atomically reserve one saved version for one operation and owner.

        Legacy repository adapters may omit submission coordination; autonomous
        submission requires an adapter that overrides this method.
        """
        raise NotImplementedError("Repository does not support submission coordination.")

    def release_submission(
        self,
        application_id: str,
        operation_id: str,
        *,
        claim_owner_id: str | None = None,
    ) -> None:
        """Release a matching owner claim after a definite pre-provider failure."""
        raise NotImplementedError("Repository does not support submission coordination.")

    def complete_submission(
        self,
        application: JobApplication,
        operation_id: str,
        *,
        expected_version: int,
        claim_owner_id: str | None = None,
    ) -> None:
        """Persist an applied transition only for the matching operation owner."""
        raise NotImplementedError("Repository does not support submission coordination.")

    @abstractmethod
    def clear(self) -> None:
        """Remove all tracked applications."""
