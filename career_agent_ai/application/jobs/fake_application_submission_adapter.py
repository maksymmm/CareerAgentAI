"""Deterministic no-I/O job-application submission provider."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from .application_submission_adapter import (
    ApplicationSubmissionReceipt,
    PreApplicationSubmissionError,
    validate_submission_identifier,
)


class FakeApplicationSubmissionAdapter:
    """In-memory application provider that never contacts an employer."""

    def __init__(
        self,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._now = now
        self._operations: dict[str, ApplicationSubmissionReceipt] = {}
        self.calls: list[tuple[str, str]] = []
        self.failure: Exception | None = None

    @property
    def is_dry_run(self) -> bool:
        """Return True because this adapter has no external side effects."""
        return True

    def submit(
        self,
        operation_id: str,
        application_id: str,
        job_id: str,
        resume_id: str,
    ) -> ApplicationSubmissionReceipt:
        """Return one stable fake receipt per operation ID."""
        operation_id = validate_submission_identifier(operation_id, "operation_id")
        application_id = validate_submission_identifier(
            application_id, "application_id"
        )
        job_id = validate_submission_identifier(job_id, "job_id")
        validate_submission_identifier(resume_id, "resume_id")
        existing = self._operations.get(operation_id)
        if existing is not None:
            return existing
        self.calls.append((operation_id, application_id))
        if self.failure is not None:
            raise PreApplicationSubmissionError(str(self.failure)) from self.failure
        receipt = ApplicationSubmissionReceipt(
            application_id=application_id,
            job_id=job_id,
            provider_submission_id=f"fake-{application_id}",
            submitted_at=self._now(),
        )
        self._operations[operation_id] = receipt
        return receipt
