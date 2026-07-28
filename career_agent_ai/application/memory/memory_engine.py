from __future__ import annotations

from dataclasses import replace

from .memory_record import MemoryRecord
from .memory_result import MemoryResult
from .memory_snapshot import MemorySnapshot
from .memory_state import MemoryState


class MemoryEngine:
    """
    Deterministic in-memory storage.

    No database.
    No files.
    No networking.
    """

    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}
        self._state = MemoryState.EMPTY

    @property
    def state(self) -> MemoryState:
        return self._state

    def save(self, record: MemoryRecord) -> MemoryResult:
        """
        Stores a new memory record.
        """
        self._records[record.key] = record
        self._state = MemoryState.READY

        return MemoryResult(
            success=True,
            state=self._state.value,
        )

    def get(self, key: str) -> MemoryRecord | None:
        """
        Returns a memory record.
        """
        return self._records.get(key)

    def update(self, record: MemoryRecord) -> MemoryResult:
        """
        Replaces an existing record.
        """
        self._records[record.key] = record
        self._state = MemoryState.UPDATED

        return MemoryResult(
            success=True,
            state=self._state.value,
        )

    def clear(self) -> MemoryResult:
        """
        Removes all records.
        """
        self._records.clear()
        self._state = MemoryState.CLEARED

        return MemoryResult(
            success=True,
            state=self._state.value,
        )

    def load_snapshot(self) -> MemorySnapshot:
        """
        Returns an immutable snapshot.
        """
        return MemorySnapshot(
            records={
                key: record.value
                for key, record in self._records.items()
            }
        )

    def snapshot(self) -> MemorySnapshot:
        """
        Alias for load_snapshot().
        """
        return self.load_snapshot()