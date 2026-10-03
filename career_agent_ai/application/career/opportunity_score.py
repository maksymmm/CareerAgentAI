from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from career_agent_ai.application.career.opportunity_signal import OpportunitySignal


@dataclass(frozen=True)
class OpportunityScore:
    """Explainable aggregate score for a pre-vacancy opportunity."""

    company: str
    score: float
    signals: tuple[OpportunitySignal, ...]
    rationale: str
    metadata: Mapping[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        company = self.company.strip()
        rationale = self.rationale.strip()

        if not company:
            raise ValueError("company must not be empty.")
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("score must be between 0 and 1.")
        if not rationale:
            raise ValueError("rationale must not be empty.")

        object.__setattr__(self, "company", company)
        object.__setattr__(self, "rationale", rationale)
        object.__setattr__(
            self,
            "signals",
            tuple(self.signals),
        )
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )
