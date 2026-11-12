from __future__ import annotations

from .memory_record import MemoryRecord
from .memory_repository import MemoryRepository


class InMemoryMemoryRepository(MemoryRepository):
    """Process-local memory repository used by the compatible default engine."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], MemoryRecord] = {}

    def save(self, record: MemoryRecord) -> None:
        """Insert or replace one user's record without changing another user's key."""
        self._records[(record.user_id, record.key)] = record

    def get(
        self, key: str, *, user_id: str | None = None
    ) -> MemoryRecord | None:
        """Return a record by key, requiring a user scope when the key is ambiguous."""
        normalized_key = key.strip()
        if not normalized_key:
            raise ValueError("Memory key must not be empty.")
        if user_id is not None:
            normalized_user = user_id.strip()
            if not normalized_user:
                raise ValueError("Memory user_id filter must not be empty.")
            return self._records.get((normalized_user, normalized_key))
        matches = tuple(
            record
            for (record_user, record_key), record in sorted(self._records.items())
            if record_key == normalized_key
        )
        if len(matches) > 1:
            raise ValueError(
                "Memory key exists for multiple users; user_id is required."
            )
        return matches[0] if matches else None

    def find(
        self,
        *,
        user_id: str | None = None,
        memory_type: str | None = None,
    ) -> tuple[MemoryRecord, ...]:
        """Return matching records in deterministic user/key order."""
        normalized_user = None
        if user_id is not None:
            normalized_user = user_id.strip()
            if not normalized_user:
                raise ValueError("Memory user_id filter must not be empty.")
        normalized_type = None
        if memory_type is not None:
            normalized_type = memory_type.strip()
            if not normalized_type:
                raise ValueError("Memory type filter must not be empty.")
        return tuple(
            record
            for record in sorted(
                self._records.values(), key=lambda item: (item.user_id, item.key)
            )
            if (normalized_user is None or record.user_id == normalized_user)
            and (normalized_type is None or record.memory_type == normalized_type)
        )

    def clear(self) -> None:
        """Remove all records."""
        self._records.clear()
