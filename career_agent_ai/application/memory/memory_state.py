from __future__ import annotations

from enum import StrEnum


class MemoryState(StrEnum):
    """
    Lifecycle state of the memory engine.
    """

    EMPTY = "Empty"
    READY = "Ready"
    UPDATED = "Updated"
    CLEARED = "Cleared"
