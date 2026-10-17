"""Persistence boundary for durable autonomous career-loop state."""

from __future__ import annotations

from typing import Protocol

from .autonomous_loop_models import CareerLoopState


class CareerLoopConflictError(RuntimeError):
    """Raised when a stale autonomous-loop snapshot tries to overwrite newer state."""


class CareerLoopRepository(Protocol):
    """Store and restore active or terminal end-to-end career-loop snapshots."""

    def save(
        self, state: CareerLoopState, *, expected_owner_id: str | None = None
    ) -> None:
        """Create or compare-and-swap one durable state snapshot.

        When expected_owner_id is provided, the write must still be owned by that
        execution lease. Unowned writes must not overwrite a snapshot currently leased
        by another worker. Implementations advance the state's version only after a
        successful write and raise CareerLoopConflictError on stale ownership/version.
        """

    def get(self, run_id: str) -> CareerLoopState | None:
        """Return one durable state snapshot or None."""

    def claim_execution(
        self,
        run_id: str,
        owner_id: str,
        *,
        expected_version: int,
        lease_seconds: int,
    ) -> None:
        """Acquire one expiring per-run execution lease for the expected snapshot."""

    def renew_execution(
        self,
        run_id: str,
        owner_id: str,
        *,
        lease_seconds: int,
    ) -> None:
        """Extend an execution lease only while owner_id still owns it."""

    def release_execution(self, run_id: str, owner_id: str) -> None:
        """Release the matching per-run execution lease."""

    def delete(self, run_id: str) -> None:
        """Remove one durable state snapshot."""
