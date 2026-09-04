from __future__ import annotations

from enum import StrEnum


class AgentState(StrEnum):
    """
    Lifecycle states of an Agent.
    """

    IDLE = "Idle"
    READY = "Ready"
    RUNNING = "Running"
    WAITING = "Waiting"
    FAILED = "Failed"
    COMPLETED = "Completed"