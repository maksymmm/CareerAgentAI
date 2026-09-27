"""Persistence boundary for durable autonomous career-loop state."""

from __future__ import annotations

from typing import Protocol

from .autonomous_loop_models import CareerLoopState


class CareerLoopRepository(Protocol):
    """Store and restore active or terminal end-to-end career-loop snapshots."""

    def save(self, state: CareerLoopState) -> None:
        """Create or replace one durable state snapshot."""

    def get(self, run_id: str) -> CareerLoopState | None:
        """Return one durable state snapshot or None."""

    def delete(self, run_id: str) -> None:
        """Remove one durable state snapshot."""
