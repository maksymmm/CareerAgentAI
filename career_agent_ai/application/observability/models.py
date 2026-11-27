"""Operational observability models for failed and stuck work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class OperationalSeverity(str, Enum):
    """Severity assigned to one operational issue."""

    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class OperationalIssue:
    """One actionable failed, ambiguous, or stuck unit of work."""

    issue_type: str
    entity_id: str
    severity: OperationalSeverity
    updated_at: datetime
    details: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.issue_type, str) or not self.issue_type.strip():
            raise ValueError("issue_type must not be empty.")
        if not isinstance(self.entity_id, str) or not self.entity_id.strip():
            raise ValueError("entity_id must not be empty.")
        if not isinstance(self.severity, OperationalSeverity):
            raise TypeError("severity must be an OperationalSeverity.")
        if not isinstance(self.updated_at, datetime):
            raise TypeError("updated_at must be a datetime.")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("updated_at must be timezone-aware.")
        object.__setattr__(self, "issue_type", self.issue_type.strip())
        object.__setattr__(self, "entity_id", self.entity_id.strip())
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))


@dataclass(frozen=True)
class OperationalIssuePage:
    """One bounded page of operational issues and its truncation signal."""

    issues: tuple[OperationalIssue, ...]
    truncated: bool

    def __post_init__(self) -> None:
        if not isinstance(self.issues, tuple) or any(
            not isinstance(issue, OperationalIssue) for issue in self.issues
        ):
            raise TypeError("issues must be a tuple of OperationalIssue values.")
        if not isinstance(self.truncated, bool):
            raise TypeError("truncated must be a boolean.")
