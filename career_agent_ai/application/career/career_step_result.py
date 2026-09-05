from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class CareerStepResult:
    """Immutable outcome of one planned career action."""

    step_id: str
    action: str
    success: bool
    messages: tuple[str, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", tuple(self.messages))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
