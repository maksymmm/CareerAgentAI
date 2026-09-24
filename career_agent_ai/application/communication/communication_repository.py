"""Persistence boundary for communication continuation state."""

from __future__ import annotations

from typing import Protocol

from .models import CommunicationMessage


class CommunicationRepository(Protocol):
    """Store and retrieve validated messages and provider identifiers."""

    def save(self, message: CommunicationMessage) -> CommunicationMessage:
        """Persist a message idempotently or reject conflicting reuse."""

    def get(self, message_id: str) -> CommunicationMessage | None:
        """Return one persisted message."""

    def list_thread(self, thread_id: str) -> tuple[CommunicationMessage, ...]:
        """Return a thread in chronological order."""
