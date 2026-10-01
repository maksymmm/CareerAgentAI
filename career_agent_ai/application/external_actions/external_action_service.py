from __future__ import annotations

from collections.abc import Callable
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
        *,
        execution_allowed: Callable[[], bool] | None = None,
    ) -> None:
        self._repository = repository
        self._adapter = adapter
        self._execution_allowed = execution_allowed

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
        if not isinstance(human_approved, bool):
            raise TypeError("human_approved must be a boolean.")
        if human_approved is not True:
            raise PermissionError("Explicit human approval is required before execution.")
        if self._execution_allowed is not None and not self._execution_allowed():
            raise PermissionError("Consequential external actions are disabled by runtime policy.")

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
            return self._persist_failure_outcome(
                operation.operation_id,
                ExternalActionStatus.RECONCILIATION_REQUIRED,
                exc,
            )
        except Exception as exc:
            return self._persist_failure_outcome(
                operation.operation_id,
                ExternalActionStatus.FAILED,
                exc,
            )
        try:
            return self._repository.transition(
                operation.operation_id,
                ExternalActionStatus.IN_PROGRESS,
                ExternalActionStatus.SUCCEEDED,
                result=result,
            )
        except Exception as persistence_error:
            # The provider has already returned success. If the terminal write did
            # not become durable, never classify the action as safely retryable.
            current = self._repository.get(operation.operation_id)
            if current is not None and current.status == ExternalActionStatus.SUCCEEDED:
                return current
            if current is not None and current.status == ExternalActionStatus.RECONCILIATION_REQUIRED:
                return current
            if current is not None and current.status == ExternalActionStatus.IN_PROGRESS:
                try:
                    return self._repository.transition(
                        operation.operation_id,
                        ExternalActionStatus.IN_PROGRESS,
                        ExternalActionStatus.RECONCILIATION_REQUIRED,
                        error=(
                            "Provider returned success but the durable success write "
                            f"failed: {self._safe_error(persistence_error)}"
                        )[:1000],
                    )
                except Exception:
                    pass
            raise

    def _persist_failure_outcome(
        self,
        operation_id: str,
        target_status: ExternalActionStatus,
        error: Exception,
    ) -> ExternalActionOperation:
        """Persist a provider failure without stranding an in-progress action.

        If the intended terminal write fails, the action is conservatively moved to
        reconciliation_required. This prevents a transient local persistence failure
        from making an externally ambiguous action look safely retryable or terminal.
        """
        if target_status not in {
            ExternalActionStatus.FAILED,
            ExternalActionStatus.RECONCILIATION_REQUIRED,
        }:
            raise ValueError("target_status must represent a provider failure outcome.")
        safe_error = self._safe_error(error)
        try:
            return self._repository.transition(
                operation_id,
                ExternalActionStatus.IN_PROGRESS,
                target_status,
                error=safe_error,
            )
        except Exception as persistence_error:
            current = self._repository.get(operation_id)
            if current is not None and current.status == target_status:
                return current
            if (
                current is not None
                and current.status == ExternalActionStatus.RECONCILIATION_REQUIRED
            ):
                return current
            if current is not None and current.status == ExternalActionStatus.IN_PROGRESS:
                return self._repository.transition(
                    operation_id,
                    ExternalActionStatus.IN_PROGRESS,
                    ExternalActionStatus.RECONCILIATION_REQUIRED,
                    error=(
                        "Provider failure outcome could not be durably classified: "
                        f"{self._safe_error(persistence_error)}"
                    )[:1000],
                )
            raise

    def replace_succeeded_result(
        self,
        operation_id: str,
        result: Mapping[str, Any],
    ) -> ExternalActionOperation:
        """Replace the durable result of an already-succeeded operation."""
        return self._repository.replace_succeeded_result(operation_id, result)

    def reopen_failed(self, operation_id: str) -> ExternalActionOperation:
        """Reopen a definite no-effect failure for a deliberate retry.

        Callers must use this only when their adapter contract guarantees that a
        FAILED outcome means no external side effect occurred.
        """
        operation = self._require_operation(operation_id)
        if operation.status == ExternalActionStatus.PREPARED:
            return operation
        if operation.status != ExternalActionStatus.FAILED:
            raise ValueError("Only a failed operation can be reopened for retry.")
        return self._repository.transition(
            operation.operation_id,
            ExternalActionStatus.FAILED,
            ExternalActionStatus.PREPARED,
        )

    def resolve_reconciliation(
        self,
        operation_id: str,
        *,
        confirmed_succeeded: bool,
        result: Mapping[str, Any] | None = None,
    ) -> ExternalActionOperation:
        """Resolve an ambiguous provider outcome after explicit external verification.

        A confirmed success becomes terminal succeeded with a durable result.
        A confirmed no-effect outcome returns to prepared so the same stable
        idempotency key can be deliberately retried without creating a new intent.
        """
        operation = self._require_operation(operation_id)
        if operation.status != ExternalActionStatus.RECONCILIATION_REQUIRED:
            raise ValueError("Operation is not awaiting reconciliation.")
        if confirmed_succeeded:
            if result is None:
                raise ValueError("Confirmed success requires a durable result.")
            return self._repository.transition(
                operation.operation_id,
                ExternalActionStatus.RECONCILIATION_REQUIRED,
                ExternalActionStatus.SUCCEEDED,
                result=result,
            )
        if result is not None:
            raise ValueError("Confirmed no-effect reconciliation must not include a result.")
        return self._repository.transition(
            operation.operation_id,
            ExternalActionStatus.RECONCILIATION_REQUIRED,
            ExternalActionStatus.PREPARED,
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
