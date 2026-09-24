"""Scheduling and interview coordination use cases."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from career_agent_ai.application.external_actions import (
    AmbiguousExternalActionError,
    ExternalActionService,
    ExternalActionStatus,
)

from .calendar_adapter import CalendarAdapter, PreCalendarActionError
from .models import (
    HumanScheduleView,
    ScheduleEvent,
    ScheduleStatus,
    normalize_aware_datetime,
    validate_timezone_name,
)
from .scheduling_repository import SchedulingConflictError, SchedulingRepository


class _CalendarActionAdapter:
    def __init__(
        self, provider: CalendarAdapter, repository: SchedulingRepository
    ) -> None:
        self._provider = provider
        self._repository = repository

    def execute(
        self, operation_id: str, action_type: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        event_id = str(payload["event_id"])
        event = self._repository.get(event_id)
        if event is None:
            raise ValueError("Prepared scheduling event no longer exists.")

        allowed = self._allowed_statuses(action_type)
        expected_version = self._prepared_version(payload)
        target_start: datetime | None = None
        target_end: datetime | None = None
        target_timezone: str | None = None
        if action_type == "calendar.accept":
            target_start, target_end, target_timezone = self._prepared_source_slot(payload)
        elif action_type == "calendar.reschedule":
            target_start = normalize_aware_datetime(
                datetime.fromisoformat(str(payload["start_at"])), "start_at"
            )
            raw_end = payload.get("end_at")
            target_end = (
                None
                if raw_end is None
                else normalize_aware_datetime(
                    datetime.fromisoformat(str(raw_end)), "end_at"
                )
            )
            target_timezone = validate_timezone_name(str(payload["timezone_name"]))
        claimed = self._repository.claim_action(
            event_id,
            operation_id,
            allowed,
            expected_version=expected_version,
            reservation_start=target_start,
            reservation_end=target_end,
            enforce_conflicts=action_type in {"calendar.accept", "calendar.reschedule"},
        )
        self._validate_prepared_source(claimed, payload)

        try:
            if action_type == "calendar.accept":
                delivered = self._provider.accept(operation_id, claimed)
            elif action_type == "calendar.decline":
                delivered = self._provider.decline(operation_id, claimed)
            elif action_type == "calendar.reschedule":
                if target_start is None or target_timezone is None:
                    raise ValueError("Reschedule target was not prepared.")
                delivered = self._provider.reschedule(
                    operation_id,
                    claimed,
                    target_start,
                    target_end,
                    target_timezone,
                )
            else:
                raise ValueError("Unsupported calendar action type.")
        except PreCalendarActionError:
            self._repository.release_action(event_id, operation_id)
            raise
        except Exception as exc:
            raise AmbiguousExternalActionError(
                "Calendar provider outcome is uncertain and requires reconciliation."
            ) from exc

        try:
            updated = self._validated_transition(action_type, claimed, delivered, payload)
            persisted = self._repository.complete_action(
                updated, operation_id, expected_version=claimed.version
            )
        except Exception as exc:
            raise AmbiguousExternalActionError(
                "Calendar provider may have completed the action, but its outcome "
                "could not be persisted."
            ) from exc
        return {
            "event_id": persisted.event_id,
            "status": persisted.status.value,
            "provider_event_id": persisted.provider_event_id,
        }

    @staticmethod
    def _prepared_version(payload: Mapping[str, Any]) -> int:
        raw = payload.get("event_version")
        if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
            raise ValueError("Prepared scheduling intent has an invalid event_version.")
        return raw

    @staticmethod
    def _prepared_source_slot(
        payload: Mapping[str, Any],
    ) -> tuple[datetime, datetime | None, str]:
        start = normalize_aware_datetime(
            datetime.fromisoformat(str(payload["event_start_at"])), "event_start_at"
        )
        raw_end = payload.get("event_end_at")
        end = (
            None
            if raw_end is None
            else normalize_aware_datetime(
                datetime.fromisoformat(str(raw_end)), "event_end_at"
            )
        )
        timezone_name = validate_timezone_name(str(payload["event_timezone_name"]))
        return start, end, timezone_name

    @classmethod
    def _validate_prepared_source(
        cls, event: ScheduleEvent, payload: Mapping[str, Any]
    ) -> None:
        expected_start, expected_end, expected_timezone = cls._prepared_source_slot(payload)
        expected_status = ScheduleStatus(str(payload["event_status"]))
        if (
            event.version != cls._prepared_version(payload)
            or event.start_at != expected_start
            or event.end_at != expected_end
            or event.timezone_name != expected_timezone
            or event.status != expected_status
        ):
            raise ValueError("Prepared scheduling intent no longer matches the event.")

    @staticmethod
    def _allowed_statuses(action_type: str) -> tuple[ScheduleStatus, ...]:
        if action_type in {"calendar.accept", "calendar.decline"}:
            return (ScheduleStatus.PROPOSED, ScheduleStatus.RESCHEDULE_REQUESTED)
        if action_type == "calendar.reschedule":
            return (
                ScheduleStatus.PROPOSED,
                ScheduleStatus.ACCEPTED,
                ScheduleStatus.RESCHEDULE_REQUESTED,
            )
        raise ValueError("Unsupported calendar action type.")

    @staticmethod
    def _validated_transition(
        action_type: str,
        prepared: ScheduleEvent,
        delivered: ScheduleEvent,
        payload: Mapping[str, Any],
    ) -> ScheduleEvent:
        if not isinstance(delivered, ScheduleEvent):
            raise ValueError("Calendar provider returned malformed event data.")
        immutable_expected = (
            prepared.event_id,
            prepared.candidate_id,
            prepared.employer_name,
            prepared.event_type,
            prepared.location,
            prepared.application_id,
            prepared.created_at,
        )
        immutable_actual = (
            delivered.event_id,
            delivered.candidate_id,
            delivered.employer_name,
            delivered.event_type,
            delivered.location,
            delivered.application_id,
            delivered.created_at,
        )
        if immutable_actual != immutable_expected:
            raise ValueError("Calendar provider changed immutable event intent.")
        if (
            prepared.provider_event_id is not None
            and delivered.provider_event_id != prepared.provider_event_id
        ):
            raise ValueError("Calendar provider changed provider_event_id.")

        if action_type == "calendar.accept":
            expected_status = ScheduleStatus.ACCEPTED
            start_at = prepared.start_at
            end_at = prepared.end_at
            timezone_name = prepared.timezone_name
        elif action_type == "calendar.decline":
            expected_status = ScheduleStatus.DECLINED
            start_at = prepared.start_at
            end_at = prepared.end_at
            timezone_name = prepared.timezone_name
        else:
            expected_status = ScheduleStatus.RESCHEDULE_REQUESTED
            start_at = normalize_aware_datetime(
                datetime.fromisoformat(str(payload["start_at"])), "start_at"
            )
            raw_end = payload.get("end_at")
            end_at = (
                None
                if raw_end is None
                else normalize_aware_datetime(
                    datetime.fromisoformat(str(raw_end)), "end_at"
                )
            )
            timezone_name = validate_timezone_name(str(payload["timezone_name"]))

        if delivered.status != expected_status:
            raise ValueError("Calendar provider returned an unexpected status.")
        if (
            delivered.start_at != start_at
            or delivered.end_at != end_at
            or delivered.timezone_name != timezone_name
        ):
            raise ValueError("Calendar provider changed the requested event slot.")

        return prepared.transition(
            expected_status,
            start_at=start_at,
            end_at=end_at,
            clear_end=(action_type == "calendar.reschedule" and end_at is None),
            timezone_name=timezone_name,
            provider_event_id=delivered.provider_event_id,
        )


class SchedulingService:
    """Coordinate durable scheduling while preserving explicit human control."""

    def __init__(
        self,
        repository: SchedulingRepository,
        provider: CalendarAdapter,
        external_actions: ExternalActionService,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._external_actions = external_actions

    @staticmethod
    def action_adapter(
        provider: CalendarAdapter, repository: SchedulingRepository
    ) -> _CalendarActionAdapter:
        """Build the crash-safe external-action bridge for calendar operations."""
        return _CalendarActionAdapter(provider, repository)

    def add_event(self, event: ScheduleEvent) -> ScheduleEvent:
        """Persist a proposed/imported event without any external side effect."""
        return self._repository.create(event)

    def get_event(self, event_id: str) -> ScheduleEvent:
        """Return one persisted event or raise KeyError when it does not exist."""
        event = self._repository.get(event_id)
        if event is None:
            raise KeyError(f"Unknown scheduling event: {event_id!r}")
        return event

    def human_view(self, event_id: str) -> HumanScheduleView:
        """Return exact employer/location/local-time details for human action."""
        return HumanScheduleView.from_event(self.get_event(event_id))

    def conflicts_for(self, event_id: str) -> tuple[ScheduleEvent, ...]:
        """Return active events that overlap the supplied event's current slot."""
        event = self.get_event(event_id)
        return self._repository.find_conflicts(
            event.candidate_id,
            event.start_at,
            event.end_at,
            exclude_event_id=event.event_id,
        )

    def accept(
        self, operation_id: str, event_id: str, *, human_approved: bool
    ) -> ScheduleEvent | None:
        """Accept one exact persisted slot after explicit human approval.

        A repeated operation ID replays its durable outcome. A prepared operation is
        bound to the event version and slot that the human was asked to approve.
        """
        event = self.get_event(event_id)
        existing = self._external_actions.get(operation_id)
        if existing is not None:
            self._validate_existing_operation(existing.action_type, existing.payload, "calendar.accept", event_id)
            return self._execute(operation_id, human_approved)
        if event.status not in {
            ScheduleStatus.PROPOSED,
            ScheduleStatus.RESCHEDULE_REQUESTED,
        }:
            raise ValueError("Event is not awaiting acceptance.")
        conflicts = tuple(
            item
            for item in self.conflicts_for(event_id)
            if item.status
            in {ScheduleStatus.ACCEPTED, ScheduleStatus.RESCHEDULE_REQUESTED}
        )
        if conflicts:
            raise SchedulingConflictError(
                "Event conflicts with another committed scheduling event."
            )
        payload = {"event_id": event.event_id, **self._source_snapshot(event)}
        self._external_actions.prepare(operation_id, "calendar.accept", payload)
        return self._execute(operation_id, human_approved)

    def decline(
        self, operation_id: str, event_id: str, *, human_approved: bool
    ) -> ScheduleEvent | None:
        """Decline one exact persisted proposal after explicit human approval."""
        event = self.get_event(event_id)
        existing = self._external_actions.get(operation_id)
        if existing is not None:
            self._validate_existing_operation(existing.action_type, existing.payload, "calendar.decline", event_id)
            return self._execute(operation_id, human_approved)
        if event.status not in {
            ScheduleStatus.PROPOSED,
            ScheduleStatus.RESCHEDULE_REQUESTED,
        }:
            raise ValueError("Event is not awaiting a response.")
        payload = {"event_id": event.event_id, **self._source_snapshot(event)}
        self._external_actions.prepare(operation_id, "calendar.decline", payload)
        return self._execute(operation_id, human_approved)

    def reschedule(
        self,
        operation_id: str,
        event_id: str,
        *,
        start_at: datetime,
        end_at: datetime | None,
        timezone_name: str,
        human_approved: bool,
    ) -> ScheduleEvent | None:
        """Request one exact replacement slot after explicit human approval.

        Duplicate calls with the same operation ID must describe the same event and
        requested target slot; execution is bound to the original source version.
        """
        event = self.get_event(event_id)
        target_start = normalize_aware_datetime(start_at, "start_at")
        target_end = (
            None if end_at is None else normalize_aware_datetime(end_at, "end_at")
        )
        if target_end is not None and target_end <= target_start:
            raise ValueError("end_at must be later than start_at.")
        target_timezone = validate_timezone_name(timezone_name)
        existing = self._external_actions.get(operation_id)
        if existing is not None:
            self._validate_existing_operation(
                existing.action_type,
                existing.payload,
                "calendar.reschedule",
                event_id,
                target_start=target_start,
                target_end=target_end,
                target_timezone=target_timezone,
            )
            return self._execute(operation_id, human_approved)
        if event.status not in {
            ScheduleStatus.PROPOSED,
            ScheduleStatus.ACCEPTED,
            ScheduleStatus.RESCHEDULE_REQUESTED,
        }:
            raise ValueError("Event cannot be rescheduled from its current status.")
        conflicts = tuple(
            item
            for item in self._repository.find_conflicts(
                event.candidate_id,
                target_start,
                target_end,
                exclude_event_id=event.event_id,
            )
            if item.status
            in {ScheduleStatus.ACCEPTED, ScheduleStatus.RESCHEDULE_REQUESTED}
        )
        if conflicts:
            raise SchedulingConflictError(
                "Requested slot conflicts with another committed scheduling event."
            )
        payload: dict[str, Any] = {
            "event_id": event.event_id,
            **self._source_snapshot(event),
            "start_at": target_start.isoformat(),
            "end_at": None if target_end is None else target_end.isoformat(),
            "timezone_name": target_timezone,
        }
        self._external_actions.prepare(operation_id, "calendar.reschedule", payload)
        return self._execute(operation_id, human_approved)

    @staticmethod
    def _source_snapshot(event: ScheduleEvent) -> dict[str, Any]:
        """Return immutable durable source-slot fields for a prepared action."""
        return {
            "event_version": event.version,
            "event_start_at": event.start_at.isoformat(),
            "event_end_at": None if event.end_at is None else event.end_at.isoformat(),
            "event_timezone_name": event.timezone_name,
            "event_status": event.status.value,
        }

    @staticmethod
    def _validate_existing_operation(
        actual_action_type: str,
        payload: Mapping[str, Any],
        expected_action_type: str,
        event_id: str,
        *,
        target_start: datetime | None = None,
        target_end: datetime | None = None,
        target_timezone: str | None = None,
    ) -> None:
        """Reject reuse of one operation ID for a different scheduling intent."""
        if actual_action_type != expected_action_type or str(payload.get("event_id")) != event_id:
            raise ValueError("operation_id is already bound to different scheduling intent.")
        if expected_action_type == "calendar.reschedule":
            stored_start = normalize_aware_datetime(
                datetime.fromisoformat(str(payload["start_at"])), "start_at"
            )
            raw_end = payload.get("end_at")
            stored_end = (
                None
                if raw_end is None
                else normalize_aware_datetime(
                    datetime.fromisoformat(str(raw_end)), "end_at"
                )
            )
            stored_timezone = validate_timezone_name(str(payload["timezone_name"]))
            if (
                stored_start != target_start
                or stored_end != target_end
                or stored_timezone != target_timezone
            ):
                raise ValueError(
                    "operation_id is already bound to a different reschedule target."
                )

    def _execute(
        self, operation_id: str, human_approved: bool
    ) -> ScheduleEvent | None:
        operation = self._external_actions.execute(
            operation_id, human_approved=human_approved
        )
        if operation.status != ExternalActionStatus.SUCCEEDED:
            return None
        if operation.result is None:
            raise ValueError("Successful calendar operation has no result.")
        return self.get_event(str(operation.result["event_id"]))
