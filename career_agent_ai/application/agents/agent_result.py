from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class AgentResult:
    """
    Immutable result returned by an Agent.
    """

    success: bool
    agent_id: str

    messages: tuple[str, ...] = field(default_factory=tuple)

    execution_time: float = 0.0

    metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "messages",
            tuple(self.messages),
        )

        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )