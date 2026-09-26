from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping

from .external_action_operation import ExternalActionOperation, ExternalActionStatus


class OperationConflictError(RuntimeError):
    """Raised when an operation cannot make the requested atomic transition."""


class ExternalActionOperationRepository(ABC):
    """Persistence boundary for crash-safe external-action operations."""

    @abstractmethod
    def create(self, operation: ExternalActionOperation) -> ExternalActionOperation:
        """Persist a new intent, or return its identical existing operation."""

    @abstractmethod
    def get(self, operation_id: str) -> ExternalActionOperation | None:
        """Return an operation by its stable identifier."""

    @abstractmethod
    def transition(
        self,
        operation_id: str,
        expected_status: ExternalActionStatus,
        status: ExternalActionStatus,
        *,
        result: Mapping[str, Any] | None = None,
        error: str | None = None,
    ) -> ExternalActionOperation:
        """Atomically change an operation from the expected lifecycle state."""
