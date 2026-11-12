"""Communication use cases coordinated with crash-safe external actions."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from career_agent_ai.application.external_actions import (
    AmbiguousExternalActionError,
    ExternalActionOperation,
    ExternalActionService,
    ExternalActionStatus,
)

from .communication_adapter import CommunicationAdapter, PreDeliveryCommunicationError
from .communication_repository import CommunicationRepository
from .models import CommunicationMessage, MessageDirection, validate_identifier


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
        self._repository.claim_delivery(message.message_id, operation_id)
        try:
            if action_type == "communication.send":
                delivered = self._provider.send(operation_id, message)
            elif action_type == "communication.reply":
                parent = self._repository.get(str(payload["parent_message_id"]))
                if parent is None:
                    raise ValueError("Reply parent no longer exists.")
                delivered = self._provider.reply(operation_id, parent, message)
            else:
                raise ValueError("Unsupported communication action type.")
        except PreDeliveryCommunicationError:
            self._repository.release_delivery(message.message_id, operation_id)
            raise
        except Exception as exc:
            raise AmbiguousExternalActionError(
                "Provider delivery raised after its external outcome became uncertain."
            ) from exc
        try:
            self._validate_delivery(message, delivered)
            delivered = self._repository.save(delivered)
        except Exception as exc:
            raise AmbiguousExternalActionError(
                "Provider may have delivered the message, but its outcome could not be persisted."
            ) from exc
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
        """Create and persist only a provider draft matching the requested intent."""
        if message.direction != MessageDirection.DRAFT:
            raise ValueError("A new communication draft must have draft direction.")
        provider_draft = self._provider.draft(message)
        self._validate_provider_draft(message, provider_draft)
        return self._repository.save(provider_draft)

    @staticmethod
    def _validate_provider_draft(
        requested: CommunicationMessage,
        provider_draft: CommunicationMessage,
    ) -> None:
        """Reject provider-created drafts that alter any requested message field."""
        if not isinstance(provider_draft, CommunicationMessage):
            raise ValueError("Provider returned malformed draft data.")
        if provider_draft != requested or provider_draft.direction != MessageDirection.DRAFT:
            raise ValueError("Provider draft does not match the requested message intent.")

    def read(self, message_id: str) -> CommunicationMessage:
        """Read exactly one validated provider message and persist it locally."""
        normalized_id = validate_identifier(message_id, "message_id")
        provider_message = self._provider.read(normalized_id)
        if not isinstance(provider_message, CommunicationMessage):
            raise ValueError("Provider returned malformed message data.")
        if provider_message.message_id != normalized_id:
            raise ValueError(
                "Provider message does not match the requested message identifier."
            )
        return self._repository.save(provider_message)

    def get_persisted(self, message_id: str) -> CommunicationMessage | None:
        """Return one locally persisted message without contacting the provider."""
        return self._repository.get(message_id)

    def get_operation(self, operation_id: str) -> ExternalActionOperation | None:
        """Return durable communication operation state without executing it."""
        return self._external_actions.get(operation_id)

    def reopen_failed_send(self, operation_id: str, message_id: str) -> ExternalActionOperation:
        """Reopen one definite no-effect send failure for deliberate retry."""
        message = self._require_message(message_id)
        if message.direction != MessageDirection.DRAFT:
            raise ValueError("A failed send can only retry a persisted draft.")
        operation = self._external_actions.get(operation_id)
        if operation is None:
            raise KeyError(f"Unknown communication operation: {operation_id!r}")
        if (
            operation.action_type != "communication.send"
            or operation.payload.get("message_id") != message.message_id
        ):
            raise ValueError("Operation does not match the requested message send.")
        return self._external_actions.reopen_failed(operation_id)

    def resolve_reconciliation(
        self,
        operation_id: str,
        *,
        delivered: bool,
    ) -> ExternalActionOperation:
        """Resolve a verified ambiguous send/reply outcome without blind retry."""
        if not isinstance(delivered, bool):
            raise TypeError("delivered must be a boolean.")
        operation = self._external_actions.get(operation_id)
        if operation is None:
            raise KeyError(f"Unknown communication operation: {operation_id!r}")
        if operation.action_type not in {"communication.send", "communication.reply"}:
            raise ValueError("Operation is not a communication delivery.")
        message_id = str(operation.payload.get("message_id", ""))
        message = self._require_message(message_id)
        if delivered and operation.status == ExternalActionStatus.SUCCEEDED:
            if (
                operation.result is None
                or operation.result.get("message_id") != message.message_id
                or message.direction != MessageDirection.OUTBOUND
            ):
                raise ValueError("Resolved communication outcome is inconsistent.")
            return operation
        if not delivered and operation.status == ExternalActionStatus.PREPARED:
            if message.direction != MessageDirection.DRAFT:
                raise ValueError(
                    "Prepared retry requires the persisted message to be a draft."
                )
            return operation
        if operation.status != ExternalActionStatus.RECONCILIATION_REQUIRED:
            raise ValueError("Communication operation is not awaiting reconciliation.")
        if delivered:
            outbound = replace(message, direction=MessageDirection.OUTBOUND)
            persisted = self._repository.save(outbound)
            return self._external_actions.resolve_reconciliation(
                operation_id,
                confirmed_succeeded=True,
                result={
                    "message_id": persisted.message_id,
                    "thread_id": persisted.thread_id,
                },
            )
        self._repository.restore_delivery_for_retry(
            message.message_id,
            operation_id,
        )
        return self._external_actions.resolve_reconciliation(
            operation_id,
            confirmed_succeeded=False,
        )

    def send(
        self, operation_id: str, message_id: str, *, human_approved: bool
    ) -> CommunicationMessage | None:
        """Send a persisted draft once through the crash-safe action service."""
        self._validate_human_approval(human_approved)
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
        self._validate_human_approval(human_approved)
        parent = self._require_message(parent_message_id)
        payload = {"message_id": reply.message_id, "parent_message_id": parent.message_id}
        existing_operation = self._external_actions.get(operation_id)
        if existing_operation is not None:
            persisted_reply = self._require_message(reply.message_id)
            self._validate_retry_message(reply, persisted_reply)
            self._external_actions.prepare(
                operation_id, "communication.reply", payload
            )
            return self._execute(operation_id, human_approved)
        if reply.direction != MessageDirection.DRAFT:
            raise ValueError("A reply must initially be a draft.")
        if reply.thread_id != parent.thread_id or reply.in_reply_to != parent.message_id:
            raise ValueError("Reply identifiers do not match the persisted parent.")
        self._repository.save(reply)
        self._external_actions.prepare(
            operation_id,
            "communication.reply",
            payload,
        )
        return self._execute(operation_id, human_approved)

    @staticmethod
    def _validate_human_approval(value: bool) -> None:
        """Reject untyped truthy approval values before persisting communication intent."""
        if not isinstance(value, bool):
            raise TypeError("human_approved must be a boolean.")

    @staticmethod
    def _validate_retry_message(
        supplied: CommunicationMessage, persisted: CommunicationMessage
    ) -> None:
        """Ensure a repeated reply request describes the original durable intent."""
        supplied_content = (
            supplied.message_id, supplied.thread_id, supplied.sender, supplied.recipient,
            supplied.subject, supplied.body, supplied.created_at, supplied.in_reply_to,
        )
        persisted_content = (
            persisted.message_id, persisted.thread_id, persisted.sender,
            persisted.recipient, persisted.subject, persisted.body,
            persisted.created_at, persisted.in_reply_to,
        )
        if supplied_content != persisted_content:
            raise ValueError("Reply retry does not match the persisted message intent.")

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
