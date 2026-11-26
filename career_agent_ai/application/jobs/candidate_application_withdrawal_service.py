"""Explicit candidate-declared application withdrawal in the local tracker.

This service changes only the durable application aggregate. It never contacts an
employer, sends a message, or attempts to retract a previously submitted application.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from career_agent_ai.application.jobs.job_application import JobApplication
from career_agent_ai.application.jobs.job_application_repository import (
    ApplicationConflictError,
    JobApplicationRepository,
)
from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus


class CandidateApplicationWithdrawalService:
    """Record one version-bound candidate decision in the application tracker."""

    _WITHDRAWABLE = frozenset({
        JobApplicationStatus.SAVED,
        JobApplicationStatus.APPLIED,
        JobApplicationStatus.INTERVIEW,
        JobApplicationStatus.OFFER,
    })

    def __init__(
        self,
        repository: JobApplicationRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def record_withdrawal(
        self,
        application_id: str,
        *,
        user_id: str,
        expected_state_version: int,
    ) -> dict[str, object]:
        """Record a candidate's local withdrawal using owner/version/CAS fencing."""
        self._identifier(application_id, "application_id")
        self._identifier(user_id, "user_id")
        if (
            type(expected_state_version) is not int
            or not 1 <= expected_state_version <= (2**63 - 1)
        ):
            raise ValueError("expected_state_version must be a positive SQLite integer.")

        application = self._repository.get_owned(application_id, user_id=user_id)
        if application is None:
            raise KeyError("Application not found.")
        if application.version != expected_state_version:
            raise ApplicationConflictError("Application state has changed.")
        if application.status not in self._WITHDRAWABLE:
            raise ApplicationConflictError("Application is not withdrawable in its current state.")

        occurred_at = self._clock()
        if (
            not isinstance(occurred_at, datetime)
            or occurred_at.tzinfo is None
            or occurred_at.utcoffset() is None
        ):
            raise ValueError("Withdrawal timestamp must be timezone-aware.")
        if occurred_at < application.updated_at:
            occurred_at = application.updated_at

        updated: JobApplication = application.transition(
            JobApplicationStatus.WITHDRAWN,
            occurred_at=occurred_at,
            note=(
                "Candidate marked this application as withdrawn in the tracker; "
                "no employer notification was sent."
            ),
        )
        # The repository's version CAS and submission-claim check serialize this
        # decision against concurrent status changes and an in-flight submission.
        self._repository.update(updated, expected_version=expected_state_version)
        return {
            "application_id": updated.application_id,
            "status": updated.status.value,
            "state_version": updated.version,
            "updated_at": updated.updated_at.isoformat(),
        }

    @staticmethod
    def _identifier(value: str, name: str) -> None:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 200
        ):
            raise ValueError(f"{name} must be a canonical non-empty string of at most 200 characters.")
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValueError(f"{name} must contain only Unicode scalar values.") from exc
