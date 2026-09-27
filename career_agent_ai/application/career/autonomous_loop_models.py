"""Durable domain values for the end-to-end autonomous career loop."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from career_agent_ai.application.jobs.job import Job


def validate_loop_identifier(value: str, field: str) -> str:
    """Validate a stable loop identifier without accepting control characters."""
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text.")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty.")
    if len(normalized) > 200:
        raise ValueError(f"{field} must not exceed 200 characters.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in normalized):
        raise ValueError(f"{field} contains forbidden control characters.")
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in normalized):
        raise ValueError(f"{field} contains a forbidden Unicode surrogate.")
    return normalized


def sanitize_loop_text(value: str, field: str, maximum: int) -> str:
    """Normalize untrusted human-readable loop text."""
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


def normalize_loop_datetime(value: datetime, field: str) -> datetime:
    """Normalize a timezone-aware loop timestamp to UTC."""
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware.")
    return value.astimezone(timezone.utc)


def _json_safe(value: Any, field: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field} contains a non-finite number.")
        return value
    if isinstance(value, (list, tuple)):
        return tuple(_json_safe(item, field) for item in value)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field} keys must be text.")
            result[key] = _json_safe(item, field)
        return MappingProxyType(result)
    raise ValueError(f"{field} must contain JSON-safe values.")


class AutonomousLoopStage(str, Enum):
    """Durable stages of one bounded end-to-end career loop."""

    SEARCHING = "searching"
    PREPARING_APPLICATION = "preparing_application"
    AWAITING_APPLICATION_APPROVAL = "awaiting_application_approval"
    SUBMITTING_APPLICATION = "submitting_application"
    AWAITING_MESSAGE_APPROVAL = "awaiting_message_approval"
    SENDING_MESSAGE = "sending_message"
    AWAITING_INTERVIEW = "awaiting_interview"
    AWAITING_INTERVIEW_RESPONSE = "awaiting_interview_response"
    COORDINATING_INTERVIEW = "coordinating_interview"
    COMPLETED = "completed"
    FAILED = "failed"


class HumanActionType(str, Enum):
    """Human decisions that the autonomous loop is never allowed to assume."""

    APPLICATION_APPROVAL = "application_approval"
    MESSAGE_APPROVAL = "message_approval"
    INTERVIEW_RESPONSE = "interview_response"


@dataclass(frozen=True)
class AutonomousLoopRequest:
    """Validated search and communication intent for one autonomous loop."""

    user_id: str
    objective: str
    sender: str
    keyword: str = ""
    country: str = ""
    city: str = ""
    company: str = ""
    remote_only: bool = False
    max_results: int = 20

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "user_id", validate_loop_identifier(self.user_id, "user_id")
        )
        object.__setattr__(
            self,
            "objective",
            sanitize_loop_text(self.objective, "objective", 2_000),
        )
        object.__setattr__(
            self, "sender", validate_loop_identifier(self.sender, "sender")
        )
        for field_name in ("keyword", "country", "city", "company"):
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise TypeError(f"{field_name} must be text.")
            normalized = value.strip()
            if len(normalized) > 500:
                raise ValueError(f"{field_name} must not exceed 500 characters.")
            object.__setattr__(self, field_name, normalized)
        if not isinstance(self.remote_only, bool):
            raise TypeError("remote_only must be a boolean.")
        if (
            not isinstance(self.max_results, int)
            or isinstance(self.max_results, bool)
            or not 1 <= self.max_results <= 100
        ):
            raise ValueError("max_results must be between 1 and 100.")


@dataclass(frozen=True)
class LoopJobSnapshot:
    """JSON-safe immutable snapshot of the selected ranked job."""

    job_id: str
    title: str
    company_id: str
    company_name: str
    location: str
    url: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "job_id", validate_loop_identifier(self.job_id, "job_id")
        )
        object.__setattr__(
            self, "title", sanitize_loop_text(self.title, "title", 1_000)
        )
        company_id = self.company_id.strip() if isinstance(self.company_id, str) else ""
        if len(company_id) > 200:
            raise ValueError("company_id must not exceed 200 characters.")
        object.__setattr__(self, "company_id", company_id)
        object.__setattr__(
            self,
            "company_name",
            sanitize_loop_text(self.company_name, "company_name", 1_000),
        )
        object.__setattr__(
            self, "location", sanitize_loop_text(self.location, "location", 2_000)
        )
        if not isinstance(self.url, str) or len(self.url.strip()) > 4_000:
            raise ValueError("url must be text of at most 4000 characters.")
        object.__setattr__(self, "url", self.url.strip())

    @classmethod
    def from_job(cls, job: Job) -> "LoopJobSnapshot":
        """Capture only stable human-relevant job fields."""
        if not isinstance(job, Job):
            raise TypeError("job must be a Job.")
        company_name = getattr(job.company, "name", None) or str(job.company)
        company_id = getattr(job.company, "company_id", "") or ""
        city = getattr(job.location, "city", None)
        region = getattr(job.location, "region", None)
        country = getattr(job.location, "country", None)
        if city is None and country is None:
            location = str(job.location)
        else:
            location = ", ".join(
                str(value).strip()
                for value in (city, region, country)
                if value is not None and str(value).strip()
            )
        return cls(
            job_id=job.job_id,
            title=str(job.title),
            company_id=str(company_id),
            company_name=str(company_name),
            location=location,
            url=str(job.url or ""),
        )


@dataclass(frozen=True)
class PreparedResume:
    """Stable reference to resume material prepared for one selected job."""

    resume_id: str
    job_id: str
    summary: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "resume_id", validate_loop_identifier(self.resume_id, "resume_id")
        )
        object.__setattr__(
            self, "job_id", validate_loop_identifier(self.job_id, "job_id")
        )
        object.__setattr__(
            self, "summary", sanitize_loop_text(self.summary, "summary", 10_000)
        )


@dataclass(frozen=True)
class HumanActionEvent:
    """Durable, correlated event describing one decision required from the user."""

    event_id: str
    action_type: HumanActionType
    prompt: str
    options: tuple[str, ...]
    details: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "event_id", validate_loop_identifier(self.event_id, "event_id")
        )
        if not isinstance(self.action_type, HumanActionType):
            raise TypeError("action_type must be a HumanActionType.")
        object.__setattr__(
            self, "prompt", sanitize_loop_text(self.prompt, "prompt", 4_000)
        )
        options = tuple(self.options)
        if not options:
            raise ValueError("options must not be empty.")
        normalized_options = tuple(
            validate_loop_identifier(option, "option") for option in options
        )
        if len(set(normalized_options)) != len(normalized_options):
            raise ValueError("options must be unique.")
        object.__setattr__(self, "options", normalized_options)
        safe_details = _json_safe(dict(self.details), "details")
        object.__setattr__(self, "details", safe_details)
        object.__setattr__(
            self,
            "created_at",
            normalize_loop_datetime(self.created_at, "created_at"),
        )


@dataclass(frozen=True)
class AutonomousCareerLoopState:
    """Complete durable state required to continue a career loop after restart."""

    run_id: str
    request: AutonomousLoopRequest
    stage: AutonomousLoopStage
    selected_job: LoopJobSnapshot | None = None
    resume: PreparedResume | None = None
    recipient: str | None = None
    application_id: str | None = None
    application_operation_id: str | None = None
    message_id: str | None = None
    message_operation_id: str | None = None
    interview_event_id: str | None = None
    interview_operation_id: str | None = None
    interview_response: str | None = None
    reschedule_start_at: datetime | None = None
    reschedule_end_at: datetime | None = None
    reschedule_timezone: str | None = None
    human_action: HumanActionEvent | None = None
    decision_reason: str = ""
    completion_reason: str | None = None
    last_error: str | None = None
    transition_count: int = 0
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "run_id", validate_loop_identifier(self.run_id, "run_id")
        )
        if not isinstance(self.request, AutonomousLoopRequest):
            raise TypeError("request must be an AutonomousLoopRequest.")
        if not isinstance(self.stage, AutonomousLoopStage):
            raise TypeError("stage must be an AutonomousLoopStage.")
        if self.selected_job is not None and not isinstance(
            self.selected_job, LoopJobSnapshot
        ):
            raise TypeError("selected_job must be a LoopJobSnapshot.")
        if self.resume is not None and not isinstance(self.resume, PreparedResume):
            raise TypeError("resume must be a PreparedResume.")
        if self.human_action is not None and not isinstance(
            self.human_action, HumanActionEvent
        ):
            raise TypeError("human_action must be a HumanActionEvent.")
        for field_name in (
            "recipient",
            "application_id",
            "application_operation_id",
            "message_id",
            "message_operation_id",
            "interview_event_id",
            "interview_operation_id",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    validate_loop_identifier(value, field_name),
                )
        if self.interview_response is not None and self.interview_response not in {
            "accept",
            "decline",
            "reschedule",
        }:
            raise ValueError("interview_response is invalid.")
        for field_name in ("reschedule_start_at", "reschedule_end_at"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    normalize_loop_datetime(value, field_name),
                )
        if (
            self.reschedule_start_at is not None
            and self.reschedule_end_at is not None
            and self.reschedule_end_at <= self.reschedule_start_at
        ):
            raise ValueError("reschedule_end_at must be later than reschedule_start_at.")
        if self.reschedule_timezone is not None:
            object.__setattr__(
                self,
                "reschedule_timezone",
                validate_loop_identifier(
                    self.reschedule_timezone, "reschedule_timezone"
                ),
            )
        if not isinstance(self.decision_reason, str) or len(self.decision_reason) > 4_000:
            raise ValueError("decision_reason must be text of at most 4000 characters.")
        for field_name in ("completion_reason", "last_error"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    sanitize_loop_text(value, field_name, 4_000),
                )
        if (
            not isinstance(self.transition_count, int)
            or isinstance(self.transition_count, bool)
            or self.transition_count < 0
        ):
            raise ValueError("transition_count must be a non-negative integer.")
        if (
            not isinstance(self.version, int)
            or isinstance(self.version, bool)
            or self.version < 1
        ):
            raise ValueError("version must be a positive integer.")
        object.__setattr__(
            self,
            "created_at",
            normalize_loop_datetime(self.created_at, "created_at"),
        )
        object.__setattr__(
            self,
            "updated_at",
            normalize_loop_datetime(self.updated_at, "updated_at"),
        )
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at.")


@dataclass(frozen=True)
class AutonomousCareerLoopResult:
    """Public snapshot returned after one bounded loop advancement."""

    run_id: str
    stage: AutonomousLoopStage
    selected_job: LoopJobSnapshot | None
    application_id: str | None
    human_action: HumanActionEvent | None
    stopped_reason: str
    transition_count: int

    @property
    def completed(self) -> bool:
        """Return whether the loop has reached its successful terminal stage."""
        return self.stage == AutonomousLoopStage.COMPLETED
