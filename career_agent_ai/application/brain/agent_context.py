from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from career_agent_ai.application.memory.memory_snapshot import MemorySnapshot
from career_agent_ai.application.workflow.workflow import Workflow


@dataclass(frozen=True)
class AgentContext:
    """
    Immutable execution context used by the Agent Brain.
    """

    user_id: str

    memory_snapshot: MemorySnapshot

    active_workflow: Workflow | None = None

    metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )