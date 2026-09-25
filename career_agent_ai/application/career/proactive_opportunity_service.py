from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from career_agent_ai.application.career.opportunity_score import OpportunityScore
from career_agent_ai.application.career.opportunity_signal import (
    OpportunitySignal,
    deduplicate_opportunity_signals,
)


class ProactiveOpportunityService:
    """Turn attributable hiring evidence into ranked, explainable opportunities."""

    _WEIGHTS = {
        "hiring_growth": 0.30,
        "leadership_hire": 0.25,
        "team_growth": 0.20,
        "funding": 0.15,
        "active_job_posting": 0.12,
        "new_product": 0.10,
    }

    def rank(
        self,
        signals: Iterable[OpportunitySignal],
    ) -> tuple[OpportunityScore, ...]:
        """Deduplicate, group by employer, and rank evidence deterministically."""
        grouped: dict[str, list[OpportunitySignal]] = defaultdict(list)
        display_names: dict[str, set[str]] = defaultdict(set)

        for signal in deduplicate_opportunity_signals(signals):
            key = signal.company.casefold()
            grouped[key].append(signal)
            display_names[key].add(signal.company)

        scored = [
            self._score(
                sorted(display_names[key], key=lambda value: (value.casefold(), value))[0],
                tuple(grouped[key]),
            )
            for key in sorted(grouped)
        ]

        return tuple(
            sorted(
                scored,
                key=lambda item: (-item.score, item.company.casefold(), item.company),
            )
        )

    def _score(
        self,
        company: str,
        signals: tuple[OpportunitySignal, ...],
    ) -> OpportunityScore:
        """Calculate one bounded score while preserving source evidence."""
        weighted = 0.0
        matched_types: list[str] = []

        for signal in signals:
            weight = self._WEIGHTS.get(signal.signal_type, 0.05)
            weighted += weight * signal.strength
            if signal.signal_type not in matched_types:
                matched_types.append(signal.signal_type)

        score = min(weighted, 1.0)
        sources = tuple(sorted({signal.source for signal in signals if signal.source}))
        latest_observed_at = max(signal.observed_at for signal in signals)
        rationale = (
            f"{company} has {len(signals)} attributable hiring evidence signal(s): "
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
                "sources": sources,
                "latest_observed_at": latest_observed_at.isoformat(),
            },
        )
