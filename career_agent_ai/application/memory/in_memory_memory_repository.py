from __future__ import annotations

from .memory_record import MemoryRecord
from .memory_repository import MemoryRepository


class InMemoryMemoryRepository(MemoryRepository):
    """Process-local memory repository used by the compatible default engine."""

    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}

    def save(self, record: MemoryRecord) -> None:
        """Insert or replace a record."""
        self._records[record.key] = record

    def get(self, key: str) -> MemoryRecord | None:
        """Return a record by key."""
        return self._records.get(key)

    def find(
        self,
        *,
        user_id: str | None = None,
        memory_type: str | None = None,
    ) -> tuple[MemoryRecord, ...]:
        """Return matching records in deterministic key order."""
        return tuple(
            record
            for record in sorted(self._records.values(), key=lambda item: item.key)
            if (user_id is None or record.user_id == user_id)
            and (memory_type is None or record.memory_type == memory_type)
        )

    def clear(self) -> None:
        """Remove all records."""
        self._records.clear()
