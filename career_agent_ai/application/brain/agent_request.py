from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class AgentRequest:
    """
    Immutable request received by the Agent Brain.
    """

    request_id: str
    user_id: str
    action: str

    payload: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "payload",
            MappingProxyType(dict(self.payload)),
        )

        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )