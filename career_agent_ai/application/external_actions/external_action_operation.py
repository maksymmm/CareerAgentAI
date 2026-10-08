from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class ExternalActionStatus(str, Enum):
    """Durable lifecycle states for one consequential external action."""

    PREPARED = "prepared"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RECONCILIATION_REQUIRED = "reconciliation_required"


@dataclass(frozen=True)
class ExternalActionOperation:
    """Immutable intent and outcome for one idempotent external action."""

    operation_id: str
    action_type: str
    payload: Mapping[str, Any]
    status: ExternalActionStatus = ExternalActionStatus.PREPARED
    result: Mapping[str, Any] | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        operation_id = self.operation_id.strip()
        action_type = self.action_type.strip()
        if not operation_id:
            raise ValueError("operation_id must not be empty.")
        if len(operation_id) > 200:
            raise ValueError("operation_id must not exceed 200 characters.")
        if not action_type:
            raise ValueError("action_type must not be empty.")
        if len(action_type) > 100:
            raise ValueError("action_type must not exceed 100 characters.")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("Operation timestamps must be timezone-aware.")
        if self.status == ExternalActionStatus.SUCCEEDED and self.result is None:
            raise ValueError("A succeeded operation must contain a result.")
        if self.status == ExternalActionStatus.FAILED and not self.error:
            raise ValueError("A failed operation must contain an error.")
        object.__setattr__(self, "operation_id", operation_id)
        object.__setattr__(self, "action_type", action_type)
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        if self.result is not None:
            object.__setattr__(self, "result", MappingProxyType(dict(self.result)))
