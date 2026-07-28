from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping, Tuple


@dataclass(frozen=True)
class AgentResponse:
    """
    Immutable response returned by the Agent Brain.
    """

    request_id: str
    success: bool
    workflow_id: str | None = None

    messages: Tuple[str, ...] = field(default_factory=tuple)

    metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
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