"""Deterministic deduplication for externally observed opportunity signals."""

from __future__ import annotations

from datetime import timezone
from typing import Iterable

from .opportunity_signal import OpportunitySignal


class OpportunitySignalDeduplicator:
    """Remove duplicate signals without inventing or merging source facts."""

    def deduplicate(
        self, signals: Iterable[OpportunitySignal]
    ) -> tuple[OpportunitySignal, ...]:
        """Return the first occurrence of each stable signal identity."""
        seen: set[tuple[str, ...]] = set()
        result: list[OpportunitySignal] = []
        for signal in signals:
            key = self.identity(signal)
            if key in seen:
                continue
            seen.add(key)
            result.append(signal)
        return tuple(result)

    @staticmethod
    def identity(signal: OpportunitySignal) -> tuple[str, ...]:
        """Build a deterministic identity, preferring provider-native IDs."""
        external_id = signal.metadata.get("external_id")
        if isinstance(external_id, str) and external_id.strip():
            return (
                signal.company.casefold(),
                signal.signal_type.casefold(),
                signal.source,
                external_id.strip(),
            )
        return (
            signal.company.casefold(),
            signal.signal_type.casefold(),
            signal.source,
            signal.observed_at.astimezone(timezone.utc).isoformat(),
            str(signal.metadata.get("title", "")).strip(),
        )
