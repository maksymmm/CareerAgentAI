"""Scheduling domain models and human-facing projections."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,199}$")


def validate_schedule_identifier(value: str, field: str) -> str:
    """Validate and normalize a stable scheduling identifier."""
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text.")
    normalized = value.strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{field} is malformed.")
    return normalized


def sanitize_schedule_text(value: str, field: str, *, maximum: int) -> str:
    """Validate untrusted human-readable scheduling text."""
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text.")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty.")
    if len(normalized) > maximum:
        raise ValueError(f"{field} must not exceed {maximum} characters.")
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in normalized):
        raise ValueError(f"{field} contains forbidden control characters.")
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in normalized):
        raise ValueError(f"{field} contains a forbidden Unicode surrogate.")
    return normalized


def normalize_aware_datetime(value: datetime, field: str) -> datetime:
    """Normalize an aware datetime to UTC without losing microseconds."""
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware.")
    return value.astimezone(timezone.utc)


def validate_timezone_name(value: str) -> str:
    """Validate an IANA timezone name."""
    if not isinstance(value, str):
        raise TypeError("timezone_name must be text.")
    normalized = value.strip()
    if not normalized or len(normalized) > 100:
        raise ValueError("timezone_name is malformed.")
    try:
        ZoneInfo(normalized)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("timezone_name must be a valid IANA timezone.") from exc
    return normalized


class ScheduleEventType(str, Enum):
    """Kinds of career events that require human participation."""

    INTERVIEW = "interview"
    TRIAL_DAY = "trial_day"
    PHONE_CALL = "phone_call"
    VIDEO_CALL = "video_call"
    OTHER = "other"


class ScheduleStatus(str, Enum):
    """Durable lifecycle for one scheduled human-participation event."""

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    RESCHEDULE_REQUESTED = "reschedule_requested"
    CANCELLED = "cancelled"


_ALLOWED_TRANSITIONS: dict[ScheduleStatus, frozenset[ScheduleStatus]] = {
    ScheduleStatus.PROPOSED: frozenset(
        {
            ScheduleStatus.ACCEPTED,
            ScheduleStatus.DECLINED,
            ScheduleStatus.RESCHEDULE_REQUESTED,
            ScheduleStatus.CANCELLED,
        }
    ),
    ScheduleStatus.ACCEPTED: frozenset(
        {ScheduleStatus.RESCHEDULE_REQUESTED, ScheduleStatus.CANCELLED}
    ),
    ScheduleStatus.RESCHEDULE_REQUESTED: frozenset(
        {ScheduleStatus.ACCEPTED, ScheduleStatus.DECLINED, ScheduleStatus.CANCELLED}
    ),
    ScheduleStatus.DECLINED: frozenset(),
    ScheduleStatus.CANCELLED: frozenset(),
}


@dataclass(frozen=True)
class ScheduleEvent:
    """Validated, restart-safe scheduling aggregate."""

    event_id: str
    candidate_id: str
    employer_name: str
    event_type: ScheduleEventType
    location: str
    start_at: datetime
    timezone_name: str
    status: ScheduleStatus = ScheduleStatus.PROPOSED
    end_at: datetime | None = None
    provider_event_id: str | None = None
    application_id: str | None = None
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, ScheduleEventType):
            raise TypeError("event_type must be a ScheduleEventType.")
        if not isinstance(self.status, ScheduleStatus):
            raise TypeError("status must be a ScheduleStatus.")
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 1:
            raise ValueError("version must be a positive integer.")

        object.__setattr__(
            self, "event_id", validate_schedule_identifier(self.event_id, "event_id")
        )
        object.__setattr__(
            self,
            "candidate_id",
            validate_schedule_identifier(self.candidate_id, "candidate_id"),
        )
        object.__setattr__(
            self,
            "employer_name",
            sanitize_schedule_text(self.employer_name, "employer_name", maximum=500),
        )
        object.__setattr__(
            self,
            "location",
            sanitize_schedule_text(self.location, "location", maximum=2_000),
        )
        object.__setattr__(self, "timezone_name", validate_timezone_name(self.timezone_name))
        object.__setattr__(
            self, "start_at", normalize_aware_datetime(self.start_at, "start_at")
        )
        if self.end_at is not None:
            object.__setattr__(
                self, "end_at", normalize_aware_datetime(self.end_at, "end_at")
            )
            if self.end_at <= self.start_at:
                raise ValueError("end_at must be later than start_at.")
        if self.provider_event_id is not None:
            object.__setattr__(
                self,
                "provider_event_id",
                validate_schedule_identifier(
                    self.provider_event_id, "provider_event_id"
                ),
            )
        if self.application_id is not None:
            object.__setattr__(
                self,
                "application_id",
                validate_schedule_identifier(self.application_id, "application_id"),
            )
        object.__setattr__(
            self,
            "created_at",
            normalize_aware_datetime(self.created_at, "created_at"),
        )
        object.__setattr__(
            self,
            "updated_at",
            normalize_aware_datetime(self.updated_at, "updated_at"),
        )
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at.")

    def transition(
        self,
        status: ScheduleStatus,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        clear_end: bool = False,
        timezone_name: str | None = None,
        provider_event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> "ScheduleEvent":
        """Return the next validated aggregate version."""
        if status not in _ALLOWED_TRANSITIONS[self.status]:
            raise ValueError(f"Invalid scheduling transition: {self.status} -> {status}.")
        next_start = self.start_at if start_at is None else start_at
        next_end = None if clear_end else (self.end_at if end_at is None else end_at)
        next_timezone = self.timezone_name if timezone_name is None else timezone_name
        next_provider = (
            self.provider_event_id if provider_event_id is None else provider_event_id
        )
        timestamp = occurred_at or datetime.now(timezone.utc)
        return replace(
            self,
            status=status,
            start_at=next_start,
            end_at=next_end,
            timezone_name=next_timezone,
            provider_event_id=next_provider,
            version=self.version + 1,
            updated_at=timestamp,
        )


@dataclass(frozen=True)
class HumanScheduleView:
    """Exact local event details intended for a human participant."""

    event_id: str
    employer_name: str
    event_type: ScheduleEventType
    location: str
    local_date: str
    local_start_time: str
    local_end_time: str | None
    timezone_name: str
    utc_offset: str
    status: ScheduleStatus

    @classmethod
    def from_event(cls, event: ScheduleEvent) -> "HumanScheduleView":
        zone = ZoneInfo(event.timezone_name)
        local_start = event.start_at.astimezone(zone)
        local_end = None if event.end_at is None else event.end_at.astimezone(zone)
        return cls(
            event_id=event.event_id,
            employer_name=event.employer_name,
            event_type=event.event_type,
            location=event.location,
            local_date=local_start.date().isoformat(),
            local_start_time=local_start.time().isoformat(timespec="seconds"),
            local_end_time=(
                None
                if local_end is None
                else local_end.time().isoformat(timespec="seconds")
            ),
            timezone_name=event.timezone_name,
            utc_offset=local_start.strftime("%z")[:3] + ":" + local_start.strftime("%z")[3:],
            status=event.status,
        )
