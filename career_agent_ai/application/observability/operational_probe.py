"""Provider-neutral boundary for operational health inspection."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import OperationalIssue


class OperationalProbe(Protocol):
    """Read-only probe for failed, ambiguous, and stale durable work."""

    def inspect(self, *, now: datetime, stale_after_seconds: int) -> tuple[OperationalIssue, ...]:
        """Return deterministic operational issues without mutating durable state."""

    def list_action_statuses(
        self,
        *,
        limit: int = 25,
        after: str | None = None,
        candidate_run_ref: str | None = None,
    ) -> dict[str, object]:
        """Return bounded action metadata without payloads or provider results."""
