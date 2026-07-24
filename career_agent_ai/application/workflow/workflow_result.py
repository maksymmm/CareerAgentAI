from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from types import MappingProxyType


@dataclass(frozen=True)
class WorkflowResult:
    """
    Immutable workflow execution result.
    """

    workflow_id: str
    success: bool
    completed_steps: int
    failed_step: str | None = None
    execution_time: float = 0.0
    metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )