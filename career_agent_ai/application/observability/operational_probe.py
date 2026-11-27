"""Provider-neutral boundary for operational health inspection."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import OperationalIssue, OperationalIssuePage


class OperationalProbe(Protocol):
    """Read-only probe for failed, ambiguous, and stale durable work."""

    def inspect_page(
        self, *, now: datetime, stale_after_seconds: int, limit: int
    ) -> OperationalIssuePage:
        """Return one bounded, deterministic issue page without mutating durable state."""

    def inspect(self, *, now: datetime, stale_after_seconds: int) -> tuple[OperationalIssue, ...]:
        """Return a bounded compatibility view of operational issues."""
