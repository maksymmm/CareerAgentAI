from __future__ import annotations

from collections.abc import Iterable

from career_agent_ai.application.career.opportunity_signal import OpportunitySignal


class OpportunitySignalDeduplicator:
    """Remove duplicate observations without inventing or merging evidence."""

    def deduplicate(
        self,
        signals: Iterable[OpportunitySignal],
    ) -> tuple[OpportunitySignal, ...]:
        """Return one signal per deterministic identity key.

        When the same identity appears repeatedly, the newest observation wins.
        Equal timestamps preserve the first occurrence to keep output stable.
        """
        by_key: dict[tuple[str, ...], OpportunitySignal] = {}
        order: list[tuple[str, ...]] = []

        for signal in signals:
            if not isinstance(signal, OpportunitySignal):
                raise TypeError("signals must contain OpportunitySignal values.")
            key = signal.dedupe_key()
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = signal
                order.append(key)
                continue
            if signal.observed_at > existing.observed_at:
                by_key[key] = signal

        return tuple(by_key[key] for key in order)
