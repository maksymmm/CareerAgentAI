"""Provider boundary for calendar responses."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import ScheduleEvent


class PreCalendarActionError(RuntimeError):
    """Signal that a calendar provider failed before any external effect."""


class CalendarAdapter(Protocol):
    """Provider-neutral accept, decline, and reschedule contract."""

    @property
    def is_dry_run(self) -> bool:
        """Return whether this adapter is guaranteed to avoid external effects."""

    def accept(self, operation_id: str, event: ScheduleEvent) -> ScheduleEvent:
        """Accept an event using operation_id as the provider idempotency key."""

    def decline(self, operation_id: str, event: ScheduleEvent) -> ScheduleEvent:
        """Decline an event using operation_id as the provider idempotency key."""

    def reschedule(
        self,
        operation_id: str,
        event: ScheduleEvent,
        start_at: datetime,
        end_at: datetime | None,
        timezone_name: str,
    ) -> ScheduleEvent:
        """Request a new slot using operation_id as the idempotency key."""
