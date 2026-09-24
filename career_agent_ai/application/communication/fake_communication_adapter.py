"""Deterministic dry-run communication provider."""

from __future__ import annotations

from dataclasses import replace

from .communication_adapter import PreDeliveryCommunicationError
from .models import CommunicationMessage, MessageDirection, validate_identifier


class FakeCommunicationAdapter:
    """In-memory provider that never contacts an external system."""

    def __init__(self) -> None:
        self._messages: dict[str, CommunicationMessage] = {}
        self._operations: dict[str, CommunicationMessage] = {}
        self.calls: list[tuple[str, str]] = []
        self.failure: Exception | None = None

    @property
    def is_dry_run(self) -> bool:
        """Always report the provider as dry-run only."""
        return True

    def draft(self, message: CommunicationMessage) -> CommunicationMessage:
        """Store a draft only in this process."""
        self.calls.append(("draft", message.message_id))
        self._messages[message.message_id] = message
        return message

    def send(self, operation_id: str, message: CommunicationMessage) -> CommunicationMessage:
        """Simulate a send idempotently without external I/O."""
        return self._deliver("send", operation_id, message)

    def read(self, message_id: str) -> CommunicationMessage:
        """Read a message from the isolated in-memory mailbox."""
        normalized = validate_identifier(message_id, "message_id")
        self.calls.append(("read", normalized))
        try:
            return self._messages[normalized]
        except KeyError as exc:
            raise KeyError(f"Unknown fake-provider message: {normalized!r}") from exc

    def reply(
        self, operation_id: str, parent: CommunicationMessage, reply: CommunicationMessage
    ) -> CommunicationMessage:
        """Simulate an idempotent reply in the parent's thread."""
        if reply.thread_id != parent.thread_id or reply.in_reply_to != parent.message_id:
            raise ValueError("Reply identifiers do not match the parent message.")
        return self._deliver("reply", operation_id, reply)

    def _deliver(
        self, action: str, operation_id: str, message: CommunicationMessage
    ) -> CommunicationMessage:
        operation_id = validate_identifier(operation_id, "operation_id")
        existing = self._operations.get(operation_id)
        if existing is not None:
            return existing
        self.calls.append((action, operation_id))
        if self.failure is not None:
            raise PreDeliveryCommunicationError(str(self.failure)) from self.failure
        delivered = replace(message, direction=MessageDirection.OUTBOUND)
        self._operations[operation_id] = delivered
        self._messages[delivered.message_id] = delivered
        return delivered
