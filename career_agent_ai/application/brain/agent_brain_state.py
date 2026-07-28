from __future__ import annotations

from enum import StrEnum


class AgentBrainState(StrEnum):
    """
    Lifecycle states of the Agent Brain.
    """

    IDLE = "Idle"
    RUNNING = "Running"
    WAITING = "Waiting"
    FAILED = "Failed"
    SHUTDOWN = "Shutdown"