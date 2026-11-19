"""Provider boundary for calendar responses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from .models import ScheduleEvent


class PreCalendarActionError(RuntimeError):
    """Signal that a calendar provider failed before any external effect."""


class CalendarReconciliationEvidenceError(RuntimeError):
    """Signal that provider evidence cannot verify a calendar claim."""


class CalendarEvidenceState(str, Enum):
    PRESENT = "present"
    ABSENT_CONFIRMED = "absent_confirmed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CalendarEvidence:
    state: CalendarEvidenceState
    event: ScheduleEvent | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, CalendarEvidenceState):
            raise TypeError("state must be a CalendarEvidenceState.")
        if self.state == CalendarEvidenceState.PRESENT:
            if not isinstance(self.event, ScheduleEvent):
                raise ValueError("Present calendar evidence requires an event.")
        elif self.event is not None:
            raise ValueError("Absent or unknown calendar evidence cannot contain an event.")


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


class VerifiableCalendarProvider(Protocol):
    def verify_calendar_action(self, operation_id: str) -> CalendarEvidence:
        """Return authoritative evidence for one calendar operation."""
