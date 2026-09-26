from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from career_agent_ai.application.career.opportunity_score import OpportunityScore
from career_agent_ai.application.career.opportunity_signal import OpportunitySignal
from career_agent_ai.application.career.opportunity_signal_deduplicator import (
    OpportunitySignalDeduplicator,
)


class ProactiveOpportunityService:
    """Turn sourced hiring signals into ranked, explainable opportunities."""

    _WEIGHTS = {
        "hiring_activity": 0.30,
        "hiring_growth": 0.30,
        "leadership_hire": 0.25,
        "team_growth": 0.20,
        "funding": 0.15,
        "new_product": 0.10,
    }

    def __init__(
        self,
        deduplicator: OpportunitySignalDeduplicator | None = None,
    ) -> None:
        self._deduplicator = deduplicator or OpportunitySignalDeduplicator()

    def rank(
        self,
        signals: Iterable[OpportunitySignal],
    ) -> tuple[OpportunityScore, ...]:
        """Deduplicate and rank employer signals deterministically."""
        grouped: dict[str, list[OpportunitySignal]] = defaultdict(list)
        display_names: dict[str, str] = {}

        for signal in self._deduplicator.deduplicate(signals):
            key = signal.company.casefold()
            display_names.setdefault(key, signal.company)
            grouped[key].append(signal)

        scored = [
            self._score(display_names[key], tuple(company_signals))
            for key, company_signals in grouped.items()
        ]

        return tuple(
            sorted(
                scored,
                key=lambda item: (-item.score, item.company.casefold()),
            )
        )

    def _score(
        self,
        company: str,
        signals: tuple[OpportunitySignal, ...],
    ) -> OpportunityScore:
        """Calculate one bounded, explainable company score."""
        weighted = 0.0
        matched_types: list[str] = []
        sources: list[str] = []

        for signal in signals:
            weight = self._WEIGHTS.get(signal.signal_type, 0.05)
            weighted += weight * signal.strength
            if signal.signal_type not in matched_types:
                matched_types.append(signal.signal_type)
            if signal.source and signal.source not in sources:
                sources.append(signal.source)

        score = min(weighted, 1.0)
        rationale = (
            f"{company} has {len(signals)} sourced hiring signal(s): "
            + ", ".join(matched_types)
            + "."
        )

        return OpportunityScore(
            company=company,
            score=score,
            signals=signals,
            rationale=rationale,
            metadata={
                "signal_count": len(signals),
                "signal_types": tuple(matched_types),
                "sources": tuple(sources),
            },
        )
