"""Durable state and human-action models for the end-to-end career loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from types import MappingProxyType
from typing import Any, Mapping

from career_agent_ai.application.communication.models import validate_identifier as validate_communication_identifier


_INTERNAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,199}$")


def _safe_text(value: str, field: str, *, maximum: int, required: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text.")
    normalized = value.strip()
    if required and not normalized:
        raise ValueError(f"{field} must not be empty.")
    if len(normalized) > maximum:
        raise ValueError(f"{field} must not exceed {maximum} characters.")
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in normalized):
        raise ValueError(f"{field} contains forbidden control characters.")
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in normalized):
        raise ValueError(f"{field} contains a forbidden Unicode surrogate.")
    return normalized


def validate_loop_identifier(value: str, field: str, *, maximum: int = 200) -> str:
    """Validate one internal identifier used to derive stable loop operation IDs."""
    normalized = _safe_text(value, field, maximum=maximum, required=True)
    if not _INTERNAL_ID.fullmatch(normalized):
        raise ValueError(f"{field} is malformed.")
    return normalized


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
        object.__setattr__(self, "user_id", validate_loop_identifier(self.user_id, "user_id"))
        object.__setattr__(self, "keyword", _safe_text(self.keyword, "keyword", maximum=500, required=True))
        object.__setattr__(self, "location", _safe_text(self.location, "location", maximum=500))
        sender = _safe_text(self.sender, "sender", maximum=200)
        recipient = _safe_text(self.recipient, "recipient", maximum=200)
        if bool(sender) != bool(recipient):
            raise ValueError(
                "sender and recipient must either both be provided or both be empty."
            )
        messaging_enabled = bool(sender and recipient)
        if messaging_enabled:
            sender = validate_communication_identifier(sender, "sender")
            recipient = validate_communication_identifier(recipient, "recipient")
        object.__setattr__(self, "sender", sender)
        object.__setattr__(self, "recipient", recipient)
        object.__setattr__(
            self,
            "message_subject",
            _safe_text(
                self.message_subject,
                "message_subject",
                maximum=500,
                required=messaging_enabled,
            ),
        )
        object.__setattr__(
            self,
            "message_body",
            _safe_text(
                self.message_body,
                "message_body",
                maximum=100_000,
                required=messaging_enabled,
            ),
        )
        if self.schedule_event_id is not None:
            object.__setattr__(
                self,
                "schedule_event_id",
                validate_loop_identifier(self.schedule_event_id, "schedule_event_id"),
            )


@dataclass
class CareerLoopState:
    """Mutable durable execution state for one bounded autonomous cycle."""

    run_id: str
    request: CareerLoopRequest
    phase: CareerLoopPhase = CareerLoopPhase.SEARCH
    selected_job_id: str | None = None
    selected_job_title: str | None = None
    selected_company: str | None = None
    selected_company_id: str | None = None
    application_id: str | None = None
    application_artifact_content: str | None = None
    application_artifact_sha256: str | None = None
    message_id: str | None = None
    iterations: int = 0
    pending_human_action: HumanActionEvent | None = None
    approved_human_action: HumanActionEvent | None = None
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
