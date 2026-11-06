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
        if not isinstance(self.key, str):
            raise TypeError("Memory key must be text.")
        if not isinstance(self.user_id, str):
            raise TypeError("Memory user_id must be text.")
        if not isinstance(self.memory_type, str):
            raise TypeError("Memory type must be text.")
        key = self.key.strip()
        user_id = self.user_id.strip()
        memory_type = self.memory_type.strip()
        if not key:
            raise ValueError("Memory key must not be empty.")
        if not user_id:
            raise ValueError("Memory user_id must not be empty.")
        if not memory_type:
            raise ValueError("Memory type must not be empty.")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("Memory created_at must be timezone-aware.")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "user_id", user_id)
        object.__setattr__(self, "memory_type", memory_type)
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )
