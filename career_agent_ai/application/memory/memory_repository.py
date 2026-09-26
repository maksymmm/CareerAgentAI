from __future__ import annotations

from abc import ABC, abstractmethod

from .memory_record import MemoryRecord


class MemoryRepository(ABC):
    """Provider-neutral persistence boundary for career memory records."""

    @abstractmethod
    def save(self, record: MemoryRecord) -> None:
        """Insert or replace a memory record by its stable key."""

    @abstractmethod
    def get(self, key: str) -> MemoryRecord | None:
        """Return one record by key, or ``None`` when it does not exist."""

    @abstractmethod
    def find(
        self,
        *,
        user_id: str | None = None,
        memory_type: str | None = None,
    ) -> tuple[MemoryRecord, ...]:
        """Return records filtered by user and/or memory type."""

    @abstractmethod
    def clear(self) -> None:
        """Remove all memory records."""
