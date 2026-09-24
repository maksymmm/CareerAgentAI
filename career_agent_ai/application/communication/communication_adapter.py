"""Provider boundary for communication primitives."""

from __future__ import annotations

from typing import Protocol

from .models import CommunicationMessage


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
