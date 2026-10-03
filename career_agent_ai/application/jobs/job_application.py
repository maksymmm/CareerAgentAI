from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4

from career_agent_ai.application.jobs.job_application_status import (
    JobApplicationStatus,
)


_ALLOWED_TRANSITIONS: dict[JobApplicationStatus, frozenset[JobApplicationStatus]] = {
    JobApplicationStatus.SAVED: frozenset(
        {JobApplicationStatus.APPLIED, JobApplicationStatus.WITHDRAWN}
    ),
    JobApplicationStatus.APPLIED: frozenset(
        {
            JobApplicationStatus.INTERVIEW,
            JobApplicationStatus.REJECTED,
            JobApplicationStatus.WITHDRAWN,
        }
    ),
    JobApplicationStatus.INTERVIEW: frozenset(
        {
            JobApplicationStatus.OFFER,
            JobApplicationStatus.REJECTED,
            JobApplicationStatus.WITHDRAWN,
        }
    ),
    JobApplicationStatus.OFFER: frozenset({JobApplicationStatus.WITHDRAWN}),
    JobApplicationStatus.REJECTED: frozenset(),
    JobApplicationStatus.WITHDRAWN: frozenset(),
}


@dataclass(frozen=True)
class JobApplication:
    """Immutable aggregate for a candidate's application to one job."""

    application_id: str
    user_id: str
    job_id: str
    status: JobApplicationStatus
    company_id: str = ""
    external_action_operation_ids: tuple[str, ...] = ()
    timeline: tuple["ApplicationTimelineEvent", ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = 1

    def __post_init__(self) -> None:
        for name in ("application_id", "user_id", "job_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty.")
            if len(value.strip()) > 200:
                raise ValueError(f"{name} must not exceed 200 characters.")
            object.__setattr__(self, name, value.strip())
        if not isinstance(self.company_id, str) or len(self.company_id.strip()) > 200:
            raise ValueError("company_id must be a string of at most 200 characters.")
        object.__setattr__(self, "company_id", self.company_id.strip())
        if not isinstance(self.status, JobApplicationStatus):
            raise ValueError("status must be a JobApplicationStatus.")
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 1:
            raise ValueError("version must be positive.")
        if any(not isinstance(value, datetime) for value in (self.created_at, self.updated_at)):
            raise ValueError("Application timestamps must be datetime values.")
        if any(value.tzinfo is None or value.utcoffset() is None for value in (self.created_at, self.updated_at)):
            raise ValueError("Application timestamps must be timezone-aware.")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at.")
        operation_ids = tuple(self.external_action_operation_ids)
        if any(not isinstance(value, str) or not value.strip() or len(value.strip()) > 200 for value in operation_ids):
            raise ValueError("External-action operation IDs must be non-empty strings of at most 200 characters.")
        normalized_ids = tuple(value.strip() for value in operation_ids)
        if len(set(normalized_ids)) != len(normalized_ids):
            raise ValueError("External-action operation IDs must be unique.")
        events = tuple(self.timeline)
        if any(not isinstance(event, ApplicationTimelineEvent) for event in events):
            raise ValueError("timeline must contain ApplicationTimelineEvent values.")
        if any(
            previous.occurred_at > current.occurred_at
            for previous, current in zip(events, events[1:])
        ):
            raise ValueError("timeline events must be in deterministic chronological order.")
        if len({event.event_id for event in events}) != len(events):
            raise ValueError("timeline event IDs must be unique.")
        if any(previous.to_status != current.from_status for previous, current in zip(events, events[1:])):
            raise ValueError("timeline lifecycle states must form a continuous history.")
        if events and events[-1].to_status != self.status:
            raise ValueError("Latest timeline event must match application status.")
        if any(event.occurred_at < self.created_at for event in events):
            raise ValueError("Timeline events cannot predate application creation.")
        if events and events[-1].occurred_at != self.updated_at:
            raise ValueError("updated_at must match the latest timeline event.")
        if self.version != len(events) + 1:
            raise ValueError("version must equal the number of timeline events plus one.")
        event_operation_ids = {
            event.operation_id for event in events if event.operation_id is not None
        }
        if not event_operation_ids.issubset(normalized_ids):
            raise ValueError("Timeline operation IDs must be linked to the application.")
        object.__setattr__(self, "external_action_operation_ids", normalized_ids)
        object.__setattr__(self, "timeline", events)

    def transition(
        self,
        status: JobApplicationStatus,
        *,
        occurred_at: datetime | None = None,
        operation_id: str | None = None,
        note: str = "",
        event_id: str | None = None,
    ) -> "JobApplication":
        """Return a new aggregate after a validated lifecycle transition."""
        if not isinstance(status, JobApplicationStatus):
            raise ValueError("status must be a JobApplicationStatus.")
        if status not in _ALLOWED_TRANSITIONS[self.status]:
            raise ValueError(f"Invalid application transition: {self.status.value} -> {status.value}.")
        timestamp = occurred_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Timeline event timestamp must be timezone-aware.")
        if timestamp < self.updated_at:
            raise ValueError("Timeline events cannot predate the latest application update.")
        event = ApplicationTimelineEvent(
            event_id=event_id or str(uuid4()),
            from_status=self.status,
            to_status=status,
            occurred_at=timestamp,
            operation_id=operation_id,
            note=note,
        )
        normalized_operation_id = event.operation_id
        operation_ids = self.external_action_operation_ids
        if normalized_operation_id is not None and normalized_operation_id not in operation_ids:
            operation_ids += (normalized_operation_id,)
        return replace(
            self,
            status=status,
            # Transition order is authoritative when timestamps are equal. Sorting
            # by event ID here could invert an otherwise valid lifecycle chain.
            timeline=self.timeline + (event,),
            external_action_operation_ids=operation_ids,
            updated_at=timestamp,
            version=self.version + 1,
        )


@dataclass(frozen=True)
class ApplicationTimelineEvent:
    """A durable, ordered fact in an application's lifecycle."""

    event_id: str
    from_status: JobApplicationStatus
    to_status: JobApplicationStatus
    occurred_at: datetime
    operation_id: str | None = None
    note: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id.strip() or len(self.event_id.strip()) > 200:
            raise ValueError("event_id must be a non-empty string of at most 200 characters.")
        if not isinstance(self.occurred_at, datetime):
            raise ValueError("Timeline event timestamp must be a datetime value.")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("Timeline event timestamp must be timezone-aware.")
        if self.operation_id is not None and (not isinstance(self.operation_id, str) or not self.operation_id.strip() or len(self.operation_id.strip()) > 200):
            raise ValueError("operation_id must be a non-empty string of at most 200 characters.")
        if not isinstance(self.from_status, JobApplicationStatus) or not isinstance(self.to_status, JobApplicationStatus):
            raise ValueError("Timeline statuses must be JobApplicationStatus values.")
        if self.to_status not in _ALLOWED_TRANSITIONS[self.from_status]:
            raise ValueError(
                f"Invalid timeline transition: {self.from_status.value} -> {self.to_status.value}."
            )
        if not isinstance(self.note, str) or len(self.note) > 2000:
            raise ValueError("note must be a string of at most 2000 characters.")
        object.__setattr__(self, "event_id", self.event_id.strip())
        if self.operation_id is not None:
            object.__setattr__(self, "operation_id", self.operation_id.strip())
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
