"""Persistence boundary for scheduling aggregates."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence

from .models import ScheduleEvent, ScheduleStatus


class SchedulingConflictError(RuntimeError):
    """Raised when an event conflicts with persisted scheduling state."""


class SchedulingOperationConflict(RuntimeError):
    """Raised when a competing action owns or changed an event."""


class SchedulingRepository(Protocol):
    """Provider-neutral persistence contract for scheduling events."""

    def create(self, event: ScheduleEvent) -> ScheduleEvent:
        """Persist an event idempotently or reject conflicting reuse."""

    def get(self, event_id: str) -> ScheduleEvent | None:
        """Return one validated event."""

    def list_candidate(self, candidate_id: str) -> tuple[ScheduleEvent, ...]:
        """Return one candidate's events in chronological order."""

    def find_conflicts(
        self,
        candidate_id: str,
        start_at: datetime,
        end_at: datetime | None,
        *,
        exclude_event_id: str | None = None,
    ) -> tuple[ScheduleEvent, ...]:
        """Return non-terminal events whose time ranges overlap the requested slot."""

    def claim_action(
        self,
        event_id: str,
        operation_id: str,
        allowed_statuses: Sequence[ScheduleStatus],
    ) -> ScheduleEvent:
        """Atomically bind the current event state to one operation."""

    def release_action(self, event_id: str, operation_id: str) -> None:
        """Release an action claim after a definite pre-provider failure."""

    def complete_action(
        self,
        event: ScheduleEvent,
        operation_id: str,
        *,
        expected_version: int,
    ) -> ScheduleEvent:
        """Persist one provider-confirmed transition and release its claim atomically."""
