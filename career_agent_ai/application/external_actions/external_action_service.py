from __future__ import annotations

from typing import Any, Mapping, Protocol

from .external_action_operation import ExternalActionOperation, ExternalActionStatus
from .external_action_repository import ExternalActionOperationRepository


class AmbiguousExternalActionError(RuntimeError):
    """Raised after a provider may have completed an action without a durable outcome."""


class ExternalActionAdapter(Protocol):
    """Provider-neutral boundary for a consequential external action."""

    def execute(
        self,
        operation_id: str,
        action_type: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Execute once using operation_id as the provider idempotency key."""


class ExternalActionService:
    """Persist intent and coordinate approved adapter execution without blind retry."""

    def __init__(
        self,
        repository: ExternalActionOperationRepository,
        adapter: ExternalActionAdapter,
    ) -> None:
        self._repository = repository
        self._adapter = adapter

    def prepare(
        self,
        operation_id: str,
        action_type: str,
        payload: Mapping[str, Any],
    ) -> ExternalActionOperation:
        """Durably record an external-action intent before any provider call."""
        return self._repository.create(
            ExternalActionOperation(
                operation_id=operation_id,
                action_type=action_type,
                payload=payload,
            )
        )

    def get(self, operation_id: str) -> ExternalActionOperation | None:
        """Return an existing operation without changing its durable state."""
        return self._repository.get(operation_id)

    def execute(
        self,
        operation_id: str,
        *,
        human_approved: bool,
    ) -> ExternalActionOperation:
        """Execute a prepared action once after explicit human approval.

        An operation already left in ``in_progress`` is ambiguous: it is moved to
        ``reconciliation_required`` and the adapter is deliberately not called.
        """
        operation = self._require_operation(operation_id)
        if operation.status in {
            ExternalActionStatus.SUCCEEDED,
            ExternalActionStatus.FAILED,
            ExternalActionStatus.RECONCILIATION_REQUIRED,
        }:
            return operation
        if operation.status == ExternalActionStatus.IN_PROGRESS:
            return self._repository.transition(
                operation.operation_id,
                ExternalActionStatus.IN_PROGRESS,
                ExternalActionStatus.RECONCILIATION_REQUIRED,
                error="Previous execution has an ambiguous outcome; reconcile with the provider.",
            )
        if not human_approved:
            raise PermissionError("Explicit human approval is required before execution.")

        operation = self._repository.transition(
            operation.operation_id,
            ExternalActionStatus.PREPARED,
            ExternalActionStatus.IN_PROGRESS,
        )
        try:
            result = self._adapter.execute(
                operation.operation_id,
                operation.action_type,
                operation.payload,
            )
        except AmbiguousExternalActionError as exc:
            return self._repository.transition(
                operation.operation_id,
                ExternalActionStatus.IN_PROGRESS,
                ExternalActionStatus.RECONCILIATION_REQUIRED,
                error=self._safe_error(exc),
            )
        except Exception as exc:
            return self._repository.transition(
                operation.operation_id,
                ExternalActionStatus.IN_PROGRESS,
                ExternalActionStatus.FAILED,
                error=self._safe_error(exc),
            )
        return self._repository.transition(
            operation.operation_id,
            ExternalActionStatus.IN_PROGRESS,
            ExternalActionStatus.SUCCEEDED,
            result=result,
        )

    def _require_operation(self, operation_id: str) -> ExternalActionOperation:
        operation = self._repository.get(operation_id)
        if operation is None:
            raise KeyError(f"Unknown external-action operation: {operation_id!r}")
        return operation

    @staticmethod
    def _safe_error(error: Exception) -> str:
        message = str(error).strip()
        return message[:1000] if message else type(error).__name__
