from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping

from career_agent_ai.application.career.opportunity_signal import OpportunitySignal


@dataclass(frozen=True)
class EmployerIntelligence:
    """Explainable aggregation of sourced hiring evidence for one employer."""

    company: str
    signals: tuple[OpportunitySignal, ...]
    latest_observed_at: datetime
    sources: tuple[str, ...]
    signal_types: tuple[str, ...]
    confidence: float
    evidence_count: int
    metadata: Mapping[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        company = self.company.strip()
        if not company:
            raise ValueError("company must not be empty.")
        signals = tuple(self.signals)
        if not signals:
            raise ValueError("signals must not be empty.")
        if any(not isinstance(signal, OpportunitySignal) for signal in signals):
            raise TypeError("signals must contain OpportunitySignal values.")
        if not isinstance(self.latest_observed_at, datetime):
            raise TypeError("latest_observed_at must be a datetime.")
        if (
            self.latest_observed_at.tzinfo is None
            or self.latest_observed_at.utcoffset() is None
        ):
            raise ValueError("latest_observed_at must be timezone-aware.")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1.")
        if (
            not isinstance(self.evidence_count, int)
            or isinstance(self.evidence_count, bool)
            or self.evidence_count < 1
        ):
            raise ValueError("evidence_count must be a positive integer.")

        object.__setattr__(self, "company", company)
        object.__setattr__(self, "signals", signals)
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "signal_types", tuple(self.signal_types))
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )
