from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from career_agent_ai.application.career.employer_intelligence import (
    EmployerIntelligence,
)
from career_agent_ai.application.career.opportunity_signal import OpportunitySignal
from career_agent_ai.application.career.opportunity_signal_deduplicator import (
    OpportunitySignalDeduplicator,
)


class EmployerIntelligenceService:
    """Aggregate sourced signals into deterministic employer intelligence."""

    def __init__(
        self,
        deduplicator: OpportunitySignalDeduplicator | None = None,
    ) -> None:
        self._deduplicator = deduplicator or OpportunitySignalDeduplicator()

    def aggregate(
        self,
        signals: Iterable[OpportunitySignal],
    ) -> tuple[EmployerIntelligence, ...]:
        """Deduplicate, group, and summarize signals by normalized employer name."""
        unique = self._deduplicator.deduplicate(signals)
        grouped: dict[str, list[OpportunitySignal]] = defaultdict(list)
        display_names: dict[str, str] = {}

        for signal in unique:
            key = signal.company.casefold()
            grouped[key].append(signal)
            display_names.setdefault(key, signal.company)

        result = [
            self._build(display_names[key], tuple(company_signals))
            for key, company_signals in grouped.items()
        ]
        return tuple(
            sorted(
                result,
                key=lambda item: (
                    -item.confidence,
                    -item.latest_observed_at.timestamp(),
                    item.company.casefold(),
                ),
            )
        )

    @staticmethod
    def _build(
        company: str,
        signals: tuple[OpportunitySignal, ...],
    ) -> EmployerIntelligence:
        ordered = tuple(
            sorted(
                signals,
                key=lambda item: (
                    item.observed_at,
                    item.signal_type.casefold(),
                    item.source.casefold(),
                    item.signal_id,
                ),
            )
        )
        sources = tuple(
            dict.fromkeys(signal.source for signal in ordered if signal.source)
        )
        signal_types = tuple(
            dict.fromkeys(signal.signal_type for signal in ordered)
        )
        latest = max(signal.observed_at for signal in ordered)
        average_strength = sum(signal.strength for signal in ordered) / len(ordered)
        diversity = min(len(signal_types) / 3.0, 1.0)
        confidence = min(1.0, 0.75 * average_strength + 0.25 * diversity)
        evidence_count = sum(
            EmployerIntelligenceService._evidence_count(signal)
            for signal in ordered
        )

        return EmployerIntelligence(
            company=company,
            signals=ordered,
            latest_observed_at=latest,
            sources=sources,
            signal_types=signal_types,
            confidence=confidence,
            evidence_count=evidence_count,
            metadata={
                "signal_count": len(ordered),
                "source_count": len(sources),
                "signal_type_count": len(signal_types),
            },
        )

    @staticmethod
    def _evidence_count(signal: OpportunitySignal) -> int:
        raw = signal.metadata.get("evidence_count")
        if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
            return raw
        return 1
