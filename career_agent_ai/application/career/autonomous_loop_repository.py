"""Persistence boundary for durable autonomous career-loop state."""

from __future__ import annotations

from typing import Protocol

from .autonomous_loop_models import CareerLoopState


class CareerLoopConflictError(RuntimeError):
    """Raised when a stale autonomous-loop snapshot tries to overwrite newer state."""


class CareerLoopRepository(Protocol):
    """Store and restore active or terminal end-to-end career-loop snapshots."""

    def save(self, state: CareerLoopState) -> None:
        """Create or compare-and-swap one durable state snapshot.

        Implementations advance the state's version only after a successful write and
        raise CareerLoopConflictError when a stale snapshot loses the race.
        """

    def get(self, run_id: str) -> CareerLoopState | None:
        """Return one durable state snapshot or None."""

    def delete(self, run_id: str) -> None:
        """Remove one durable state snapshot."""
