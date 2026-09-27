"""Exclusive ownership boundary for application submission attempts."""

from __future__ import annotations

from typing import Protocol


class ApplicationSubmissionClaimError(RuntimeError):
    """Raised when another operation already owns an application submission."""


class ApplicationSubmissionClaimRepository(Protocol):
    """Persist the sole external-submission owner for one application."""

    def claim(self, application_id: str, operation_id: str) -> None:
        """Atomically bind an application to exactly one submission operation."""

    def release(self, application_id: str, operation_id: str) -> None:
        """Release a claim after a definite pre-delivery failure."""

    def complete(self, application_id: str, operation_id: str) -> None:
        """Mark an externally completed submission while retaining exclusivity."""
