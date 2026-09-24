"""Validated communication domain values."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,199}$")


def validate_identifier(value: str, field: str) -> str:
    """Return a normalized, provider-safe identifier."""
    normalized = value.strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{field} is malformed.")
    return normalized


def sanitize_text(value: str, field: str, *, maximum: int) -> str:
    """Normalize untrusted plain text and reject controls and excessive input."""
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text.")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty.")
    if len(normalized) > maximum:
        raise ValueError(f"{field} must not exceed {maximum} characters.")
    if any(ord(character) < 32 and character not in "\n\t" for character in normalized):
        raise ValueError(f"{field} contains forbidden control characters.")
    return normalized


class MessageDirection(str, Enum):
    """Direction of a communication message."""

    DRAFT = "draft"
    OUTBOUND = "outbound"
    INBOUND = "inbound"


@dataclass(frozen=True)
class CommunicationMessage:
    """Provider-neutral communication message with continuation identifiers."""

    message_id: str
    thread_id: str
    sender: str
    recipient: str
    subject: str
    body: str
    direction: MessageDirection
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    in_reply_to: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_id", validate_identifier(self.message_id, "message_id"))
        object.__setattr__(self, "thread_id", validate_identifier(self.thread_id, "thread_id"))
        object.__setattr__(self, "sender", validate_identifier(self.sender, "sender"))
        object.__setattr__(self, "recipient", validate_identifier(self.recipient, "recipient"))
        object.__setattr__(self, "subject", sanitize_text(self.subject, "subject", maximum=500))
        object.__setattr__(self, "body", sanitize_text(self.body, "body", maximum=100_000))
        if self.in_reply_to is not None:
            object.__setattr__(
                self, "in_reply_to", validate_identifier(self.in_reply_to, "in_reply_to")
            )
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware.")
