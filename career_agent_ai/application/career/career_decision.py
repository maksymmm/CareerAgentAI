from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class CareerDecision:
    """One explainable decision made before executing a career action."""

    action: str
    reason: str
    confidence: float = 1.0
    metadata: Mapping[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        action = self.action.strip()
        reason = self.reason.strip()

        if not action:
            raise ValueError("action must not be empty.")
        if not reason:
            raise ValueError("reason must not be empty.")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1.")

        object.__setattr__(self, "action", action)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )
