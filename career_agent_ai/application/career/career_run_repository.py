from __future__ import annotations

from abc import ABC, abstractmethod

from career_agent_ai.application.career.career_run_state import CareerRunState


class CareerRunConflictError(RuntimeError):
    """Raised when a stale worker tries to overwrite a newer run snapshot."""


class CareerRunRepository(ABC):
    """Persistence boundary for resumable career runs."""

    @abstractmethod
    def save(self, state: CareerRunState) -> None:
        """Create or compare-and-swap one active run snapshot.

        Implementations advance state.version only after a durable write and raise
        CareerRunConflictError when another worker already changed the snapshot.
        """

    @abstractmethod
    def get(self, run_id: str) -> CareerRunState | None:
        """Return a persisted active run, or None when it does not exist."""

    @abstractmethod
    def delete(self, run_id: str) -> None:
        """Remove a persisted run."""
