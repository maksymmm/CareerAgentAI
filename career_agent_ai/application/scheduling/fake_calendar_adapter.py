"""Deterministic no-I/O calendar provider."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from .calendar_adapter import PreCalendarActionError
from .models import ScheduleEvent, ScheduleStatus, validate_schedule_identifier


class FakeCalendarAdapter:
    """In-memory provider used only for tests and local dry runs."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self._operations: dict[str, ScheduleEvent] = {}
        self.failure: Exception | None = None

    @property
    def is_dry_run(self) -> bool:
        return True

    def accept(self, operation_id: str, event: ScheduleEvent) -> ScheduleEvent:
        return self._deliver(
            "accept",
            operation_id,
            replace(
                event,
                status=ScheduleStatus.ACCEPTED,
                provider_event_id=event.provider_event_id or f"fake-{event.event_id}",
            ),
        )

    def decline(self, operation_id: str, event: ScheduleEvent) -> ScheduleEvent:
        return self._deliver(
            "decline",
            operation_id,
            replace(
                event,
                status=ScheduleStatus.DECLINED,
                provider_event_id=event.provider_event_id or f"fake-{event.event_id}",
            ),
        )

    def reschedule(
        self,
        operation_id: str,
        event: ScheduleEvent,
        start_at: datetime,
        end_at: datetime | None,
        timezone_name: str,
    ) -> ScheduleEvent:
        return self._deliver(
            "reschedule",
            operation_id,
            replace(
                event,
                status=ScheduleStatus.RESCHEDULE_REQUESTED,
                start_at=start_at,
                end_at=end_at,
                timezone_name=timezone_name,
                provider_event_id=event.provider_event_id or f"fake-{event.event_id}",
            ),
        )

    def _deliver(
        self, action: str, operation_id: str, delivered: ScheduleEvent
    ) -> ScheduleEvent:
        operation_id = validate_schedule_identifier(operation_id, "operation_id")
        existing = self._operations.get(operation_id)
        if existing is not None:
            return existing
        self.calls.append((action, operation_id))
        if self.failure is not None:
            raise PreCalendarActionError(str(self.failure)) from self.failure
        self._operations[operation_id] = delivered
        return delivered
