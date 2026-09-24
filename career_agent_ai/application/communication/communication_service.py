"""Communication use cases coordinated with crash-safe external actions."""

from __future__ import annotations

from typing import Any, Mapping

from career_agent_ai.application.external_actions import ExternalActionService, ExternalActionStatus

from .communication_adapter import CommunicationAdapter
from .communication_repository import CommunicationRepository
from .models import CommunicationMessage, MessageDirection


class _CommunicationActionAdapter:
    def __init__(self, provider: CommunicationAdapter, repository: CommunicationRepository) -> None:
        self._provider = provider
        self._repository = repository

    def execute(
        self, operation_id: str, action_type: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        message = self._repository.get(str(payload["message_id"]))
        if message is None:
            raise ValueError("Prepared communication message no longer exists.")
        if action_type == "communication.send":
            delivered = self._provider.send(operation_id, message)
        elif action_type == "communication.reply":
            parent = self._repository.get(str(payload["parent_message_id"]))
            if parent is None:
                raise ValueError("Reply parent no longer exists.")
            delivered = self._provider.reply(operation_id, parent, message)
        else:
            raise ValueError("Unsupported communication action type.")
        self._validate_delivery(message, delivered)
        delivered = self._repository.save(delivered)
        return {"message_id": delivered.message_id, "thread_id": delivered.thread_id}

    @staticmethod
    def _validate_delivery(
        prepared: CommunicationMessage, delivered: CommunicationMessage
    ) -> None:
        """Reject provider output that does not exactly represent the prepared intent."""
        if not isinstance(delivered, CommunicationMessage):
            raise ValueError("Provider returned malformed delivery data.")
        expected = (
            prepared.message_id,
            prepared.thread_id,
            prepared.sender,
            prepared.recipient,
            prepared.subject,
            prepared.body,
            prepared.created_at,
            prepared.in_reply_to,
        )
        actual = (
            delivered.message_id,
            delivered.thread_id,
            delivered.sender,
            delivered.recipient,
            delivered.subject,
            delivered.body,
            delivered.created_at,
            delivered.in_reply_to,
        )
        if delivered.direction != MessageDirection.OUTBOUND or actual != expected:
            raise ValueError("Provider delivery does not match the prepared message intent.")


class CommunicationService:
    """Create/read messages and safely coordinate approved send/reply actions."""

    def __init__(
        self,
        repository: CommunicationRepository,
        provider: CommunicationAdapter,
        external_actions: ExternalActionService,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._external_actions = external_actions

    @staticmethod
    def action_adapter(
        provider: CommunicationAdapter, repository: CommunicationRepository
    ) -> _CommunicationActionAdapter:
        """Build the external-action adapter bridge for dependency injection."""
        return _CommunicationActionAdapter(provider, repository)

    def create_draft(self, message: CommunicationMessage) -> CommunicationMessage:
        """Create and durably persist an unsent draft."""
        if message.direction != MessageDirection.DRAFT:
            raise ValueError("A new communication draft must have draft direction.")
        return self._repository.save(self._provider.draft(message))

    def read(self, message_id: str) -> CommunicationMessage:
        """Read, validate, and persist a provider message for restart continuation."""
        return self._repository.save(self._provider.read(message_id))

    def send(
        self, operation_id: str, message_id: str, *, human_approved: bool
    ) -> CommunicationMessage | None:
        """Send a persisted draft once through the crash-safe action service."""
        message = self._require_message(message_id)
        existing_operation = self._external_actions.get(operation_id)
        if message.direction != MessageDirection.DRAFT and existing_operation is None:
            raise ValueError("Only a draft message can be sent.")
        self._external_actions.prepare(
            operation_id, "communication.send", {"message_id": message.message_id}
        )
        return self._execute(operation_id, human_approved)

    def reply(
        self,
        operation_id: str,
        parent_message_id: str,
        reply: CommunicationMessage,
        *,
        human_approved: bool,
    ) -> CommunicationMessage | None:
        """Persist and send a reply once, retaining its conversation identifiers."""
        parent = self._require_message(parent_message_id)
        if reply.direction != MessageDirection.DRAFT:
            raise ValueError("A reply must initially be a draft.")
        if reply.thread_id != parent.thread_id or reply.in_reply_to != parent.message_id:
            raise ValueError("Reply identifiers do not match the persisted parent.")
        self._repository.save(reply)
        self._external_actions.prepare(
            operation_id,
            "communication.reply",
            {"message_id": reply.message_id, "parent_message_id": parent.message_id},
        )
        return self._execute(operation_id, human_approved)

    def _execute(self, operation_id: str, human_approved: bool) -> CommunicationMessage | None:
        operation = self._external_actions.execute(
            operation_id, human_approved=human_approved
        )
        if operation.status != ExternalActionStatus.SUCCEEDED:
            return None
        if operation.result is None:
            raise ValueError("Successful communication operation has no result.")
        return self._require_message(str(operation.result["message_id"]))

    def _require_message(self, message_id: str) -> CommunicationMessage:
        message = self._repository.get(message_id)
        if message is None:
            raise KeyError(f"Unknown communication message: {message_id!r}")
        return message
