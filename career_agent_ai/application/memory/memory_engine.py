from __future__ import annotations

from .in_memory_memory_repository import InMemoryMemoryRepository
from .memory_record import MemoryRecord
from .memory_repository import MemoryRepository
from .memory_result import MemoryResult
from .memory_snapshot import MemorySnapshot
from .memory_state import MemoryState


class MemoryEngine:
    """
    Deterministic career memory facade over an injected repository.

    Construction without a repository retains the original in-memory behavior.
    """

    def __init__(self, repository: MemoryRepository | None = None) -> None:
        self._repository = repository or InMemoryMemoryRepository()
        self._state = MemoryState.EMPTY

    @property
    def state(self) -> MemoryState:
        return self._state

    def save(self, record: MemoryRecord) -> MemoryResult:
        """
        Stores a new memory record.
        """
        self._repository.save(record)
        self._state = MemoryState.READY

        return MemoryResult(
            success=True,
            state=self._state.value,
        )

    def get(
        self, key: str, *, user_id: str | None = None
    ) -> MemoryRecord | None:
        """Return one memory record, optionally scoped to a user."""
        return self._repository.get(key, user_id=user_id)

    def find(
        self,
        *,
        user_id: str | None = None,
        memory_type: str | None = None,
    ) -> tuple[MemoryRecord, ...]:
        """Return memory records filtered by user and/or memory type."""
        return self._repository.find(user_id=user_id, memory_type=memory_type)

    def update(self, record: MemoryRecord) -> MemoryResult:
        """
        Replaces an existing record.
        """
        self._repository.save(record)
        self._state = MemoryState.UPDATED

        return MemoryResult(
            success=True,
            state=self._state.value,
        )

    def clear(self) -> MemoryResult:
        """
        Removes all records.
        """
        self._repository.clear()
        self._state = MemoryState.CLEARED

        return MemoryResult(
            success=True,
            state=self._state.value,
        )

    def load_snapshot(self, *, user_id: str | None = None) -> MemorySnapshot:
        """Return an immutable snapshot optionally scoped to one user.

        Unscoped snapshots remain supported when every visible key is unique. If
        multiple users expose the same key, the caller must provide user_id so
        one user's value cannot silently overwrite another user's memory.
        """
        records = self._repository.find(user_id=user_id)
        values: dict[str, object] = {}
        owners: dict[str, str] = {}
        for record in records:
            previous_owner = owners.get(record.key)
            if (
                user_id is None
                and previous_owner is not None
                and previous_owner != record.user_id
            ):
                raise ValueError(
                    "Unscoped memory snapshot contains the same key for multiple users; "
                    "provide user_id."
                )
            owners[record.key] = record.user_id
            values[record.key] = record.value
        return MemorySnapshot(records=values)

    def snapshot(self, *, user_id: str | None = None) -> MemorySnapshot:
        """Alias for load_snapshot with the same optional user scope."""
        return self.load_snapshot(user_id=user_id)
