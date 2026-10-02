from __future__ import annotations

from abc import ABC, abstractmethod

from career_agent_ai.application.career.career_run_state import CareerRunState


class CareerRunConflictError(RuntimeError):
    """Raised when a stale worker tries to overwrite a newer run snapshot."""


class CareerRunRepository(ABC):
    """Persistence boundary for resumable career runs."""

    @abstractmethod
    def save(self, state: CareerRunState, *, lease_owner: str | None = None) -> None:
        """Create or compare-and-swap one active run snapshot.

        Implementations advance state.version only after a durable write and raise
        CareerRunConflictError when another worker already changed the snapshot or
        when an active execution lease is owned by another worker.
        """

    @abstractmethod
    def get(self, run_id: str) -> CareerRunState | None:
        """Return a persisted active run, or None when it does not exist."""

    @abstractmethod
    def acquire_lease(
        self, run_id: str, owner_id: str, *, ttl_seconds: int
    ) -> CareerRunState:
        """Acquire or reclaim one execution lease and return its durable snapshot."""

    @abstractmethod
    def renew_lease(
        self, run_id: str, owner_id: str, *, ttl_seconds: int
    ) -> None:
        """Extend an execution lease while a worker is still active."""

    @abstractmethod
    def release_lease(self, run_id: str, owner_id: str) -> None:
        """Release one execution lease owned by the caller."""

    @abstractmethod
    def delete(self, run_id: str, *, lease_owner: str | None = None) -> None:
        """Remove a persisted run, respecting any active execution lease."""
