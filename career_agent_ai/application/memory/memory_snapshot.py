from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class MemorySnapshot:
    """
    Immutable snapshot of the memory state.
    """

    records: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "records",
            MappingProxyType(dict(self.records)),
        )

    def get(self, key: str, default: Any = None) -> Any:
        """
        Returns a value from the snapshot.
        """
        return self.records.get(key, default)

    def contains(self, key: str) -> bool:
        """
        Returns True if the key exists.
        """
        return key in self.records

    def size(self) -> int:
        """
        Returns the number of stored records.
        """
        return len(self.records)