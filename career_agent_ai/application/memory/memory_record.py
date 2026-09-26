from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class MemoryRecord:
    """
    Immutable memory record.
    """

    key: str
    value: Any

    metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    user_id: str = "default"
    memory_type: str = "general"

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("Memory key must not be empty.")
        if not self.user_id.strip():
            raise ValueError("Memory user_id must not be empty.")
        if not self.memory_type.strip():
            raise ValueError("Memory type must not be empty.")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("Memory created_at must be timezone-aware.")
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )
