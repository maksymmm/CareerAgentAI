"""Provider boundary for communication primitives."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .models import CommunicationMessage, MessageDirection


class PreDeliveryCommunicationError(RuntimeError):
    """Signal that a provider failed before any external delivery was attempted."""


class CommunicationReconciliationEvidenceError(RuntimeError):
    """Signal that provider evidence cannot verify a reconciliation claim."""


class DeliveryEvidenceState(str, Enum):
    PRESENT = "present"
    ABSENT_CONFIRMED = "absent_confirmed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DeliveryEvidence:
    state: DeliveryEvidenceState
    message: CommunicationMessage | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, DeliveryEvidenceState):
            raise TypeError("state must be a DeliveryEvidenceState.")
        if self.state == DeliveryEvidenceState.PRESENT:
            if not isinstance(self.message, CommunicationMessage):
                raise ValueError("Present delivery evidence requires a message.")
            if self.message.direction != MessageDirection.OUTBOUND:
                raise ValueError("Present delivery evidence must be outbound.")
        elif self.message is not None:
            raise ValueError("Absent or unknown evidence cannot contain a message.")


class CommunicationAdapter(Protocol):
    """Provider-neutral draft, send, read, and reply contract."""

    @property
    def is_dry_run(self) -> bool:
        """Return whether this adapter is guaranteed to avoid external effects."""

    def draft(self, message: CommunicationMessage) -> CommunicationMessage:
        """Create a provider-local draft without sending it."""

    def send(self, operation_id: str, message: CommunicationMessage) -> CommunicationMessage:
        """Send a message using the stable operation ID as idempotency key."""

    def read(self, message_id: str) -> CommunicationMessage:
        """Read one provider message by identifier."""

    def reply(
        self, operation_id: str, parent: CommunicationMessage, reply: CommunicationMessage
    ) -> CommunicationMessage:
        """Reply in an existing thread using a stable idempotency key."""


class VerifiableCommunicationProvider(Protocol):
    def verify_delivery(self, operation_id: str) -> DeliveryEvidence:
        """Return authoritative provider evidence for one delivery operation."""
