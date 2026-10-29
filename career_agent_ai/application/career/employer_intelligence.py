"""Employer intelligence aggregated from verifiable opportunity signals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .opportunity_signal import OpportunitySignal
from .signal_deduplicator import OpportunitySignalDeduplicator


@dataclass(frozen=True)
class EmployerIntelligence:
    """Auditable employer-level view of observed hiring-related signals."""

    company: str
    signals: tuple[OpportunitySignal, ...]
    latest_observed_at: datetime
    signal_types: tuple[str, ...]
    sources: tuple[str, ...]


class EmployerIntelligenceService:
    """Aggregate source-backed signals into deterministic employer intelligence."""

    def __init__(
        self, deduplicator: OpportunitySignalDeduplicator | None = None
    ) -> None:
        self._deduplicator = deduplicator or OpportunitySignalDeduplicator()

    def aggregate(
        self, signals: Iterable[OpportunitySignal]
    ) -> tuple[EmployerIntelligence, ...]:
        """Group deduplicated signals by employer with provenance intact."""
        deduplicated = self._deduplicator.deduplicate(signals)
        grouped: dict[str, list[OpportunitySignal]] = {}
        company_names: dict[str, str] = {}
        for signal in deduplicated:
            key = signal.company.casefold()
            grouped.setdefault(key, []).append(signal)
            company_names.setdefault(key, signal.company)

        intelligence: list[EmployerIntelligence] = []
        for key, items in grouped.items():
            ordered = tuple(
                sorted(
                    items,
                    key=lambda item: (
                        item.observed_at,
                        item.signal_type,
                        item.source,
                    ),
                )
            )
            intelligence.append(
                EmployerIntelligence(
                    company=company_names[key],
                    signals=ordered,
                    latest_observed_at=max(
                        item.observed_at for item in ordered
                    ),
                    signal_types=tuple(
                        dict.fromkeys(item.signal_type for item in ordered)
                    ),
                    sources=tuple(
                        dict.fromkeys(
                            item.source for item in ordered if item.source
                        )
                    ),
                )
            )

        return tuple(
            sorted(
                intelligence,
                key=lambda item: (
                    -item.latest_observed_at.timestamp(),
                    item.company.casefold(),
                ),
            )
        )
