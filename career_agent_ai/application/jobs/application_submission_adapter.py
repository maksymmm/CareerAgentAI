"""Provider boundary for consequential job-application submission."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


def validate_submission_identifier(value: str, field: str) -> str:
    """Validate a stable application-submission identifier."""
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text.")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty.")
    if len(normalized) > 200:
        raise ValueError(f"{field} must not exceed 200 characters.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in normalized):
        raise ValueError(f"{field} contains forbidden control characters.")
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in normalized):
        raise ValueError(f"{field} contains a forbidden Unicode surrogate.")
    return normalized


class PreApplicationSubmissionError(RuntimeError):
    """Signal a definite failure before any external application was submitted."""


@dataclass(frozen=True)
class ApplicationSubmissionReceipt:
    """Provider-confirmed immutable receipt for one submitted application."""

    application_id: str
    job_id: str
    provider_submission_id: str
    submitted_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "application_id",
            validate_submission_identifier(self.application_id, "application_id"),
        )
        object.__setattr__(
            self, "job_id", validate_submission_identifier(self.job_id, "job_id")
        )
        object.__setattr__(
            self,
            "provider_submission_id",
            validate_submission_identifier(
                self.provider_submission_id, "provider_submission_id"
            ),
        )
        if not isinstance(self.submitted_at, datetime):
            raise TypeError("submitted_at must be a datetime.")
        if self.submitted_at.tzinfo is None or self.submitted_at.utcoffset() is None:
            raise ValueError("submitted_at must be timezone-aware.")
        object.__setattr__(
            self, "submitted_at", self.submitted_at.astimezone(timezone.utc)
        )


class ApplicationSubmissionAdapter(Protocol):
    """Provider-neutral external application submission contract."""

    @property
    def is_dry_run(self) -> bool:
        """Return whether the adapter is guaranteed to avoid external effects."""

    def submit(
        self,
        operation_id: str,
        application_id: str,
        job_id: str,
        resume_id: str,
    ) -> ApplicationSubmissionReceipt:
        """Submit once using operation_id as the provider idempotency key."""
