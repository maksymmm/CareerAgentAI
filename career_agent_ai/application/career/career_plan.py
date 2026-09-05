from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CareerPlanStep:
    """One deterministic action in a career plan."""

    id: str
    action: str
    description: str
    required: bool = True


@dataclass(frozen=True)
class CareerPlan:
    """Immutable plan produced from a user's career objective."""

    objective: str
    steps: tuple[CareerPlanStep, ...] = field(default_factory=tuple)

    def total_steps(self) -> int:
        return len(self.steps)

    def actions(self) -> tuple[str, ...]:
        return tuple(step.action for step in self.steps)
