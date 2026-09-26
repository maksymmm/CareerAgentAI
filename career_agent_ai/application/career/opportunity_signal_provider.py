from __future__ import annotations

from typing import Protocol

from career_agent_ai.application.career.opportunity_signal import OpportunitySignal


class OpportunitySignalProviderError(RuntimeError):
    """Raised when a live signal provider cannot produce a trustworthy observation."""


class OpportunitySignalProvider(Protocol):
    """Provide externally observed pre-vacancy hiring signals."""

    def collect(self) -> tuple[OpportunitySignal, ...]:
        """Return the currently available sourced hiring signals."""
        ...


class StaticOpportunitySignalProvider:
    """Provide explicitly supplied signals for deterministic execution and tests."""

    def __init__(self, signals: tuple[OpportunitySignal, ...] = ()) -> None:
        self._signals = tuple(signals)

    def collect(self) -> tuple[OpportunitySignal, ...]:
        """Return configured signals without mutating or fabricating them."""
        return self._signals
