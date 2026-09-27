"""Durable state and human-action models for the end-to-end career loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class CareerLoopPhase(str, Enum):
    """Bounded state-machine phases for one autonomous career loop."""

    SEARCH = "search"
    DECISION = "decision"
    RESUME = "resume"
    APPLICATION_PREPARE = "application_prepare"
    APPLICATION_APPROVAL = "application_approval"
    APPLICATION_SUBMIT = "application_submit"
    MESSAGE_PREPARE = "message_prepare"
    MESSAGE_APPROVAL = "message_approval"
    MESSAGE_SEND = "message_send"
    TRACK = "track"
    INTERVIEW_COORDINATION = "interview_coordination"
    INTERVIEW_APPROVAL = "interview_approval"
    INTERVIEW_ACCEPT = "interview_accept"
    COMPLETE = "complete"
    FAILED = "failed"


class HumanActionKind(str, Enum):
    """Human decisions that the autonomous loop must never make implicitly."""

    APPROVE_APPLICATION = "approve_application"
    APPROVE_MESSAGE = "approve_message"
    APPROVE_INTERVIEW = "approve_interview"


@dataclass(frozen=True)
class HumanActionEvent:
    """A durable request for one explicit human decision."""

    kind: HumanActionKind
    title: str
    details: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, HumanActionKind):
            raise TypeError("kind must be a HumanActionKind.")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("title must not be empty.")
        object.__setattr__(self, "title", self.title.strip())
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))


@dataclass(frozen=True)
class CareerLoopRequest:
    """Validated input required to run a bounded end-to-end career cycle."""

    user_id: str
    keyword: str
    location: str = ""
    sender: str = ""
    recipient: str = ""
    message_subject: str = "Application follow-up"
    message_body: str = "I am interested in this opportunity."
    schedule_event_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("user_id", "keyword"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty.")
            object.__setattr__(self, name, value.strip())
        for name in ("location", "sender", "recipient", "message_subject", "message_body"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise TypeError(f"{name} must be text.")
            object.__setattr__(self, name, value.strip())
        if self.schedule_event_id is not None:
            if not isinstance(self.schedule_event_id, str) or not self.schedule_event_id.strip():
                raise ValueError("schedule_event_id must not be empty.")
            object.__setattr__(self, "schedule_event_id", self.schedule_event_id.strip())


@dataclass
class CareerLoopState:
    """Mutable durable execution state for one bounded autonomous cycle."""

    run_id: str
    request: CareerLoopRequest
    phase: CareerLoopPhase = CareerLoopPhase.SEARCH
    selected_job_id: str | None = None
    selected_job_title: str | None = None
    selected_company: str | None = None
    application_id: str | None = None
    message_id: str | None = None
    iterations: int = 0
    pending_human_action: HumanActionEvent | None = None
    last_error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        """Advance the state timestamp after a durable state-machine change."""
        self.updated_at = datetime.now(timezone.utc)


@dataclass(frozen=True)
class CareerLoopResult:
    """Snapshot returned to callers after each bounded execution segment."""

    run_id: str
    phase: CareerLoopPhase
    completed: bool
    human_action: HumanActionEvent | None
    selected_job_id: str | None
    application_id: str | None
    message_id: str | None
    iterations: int
    error: str | None = None
