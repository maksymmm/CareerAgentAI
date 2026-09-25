"""Deterministic aggregation of attributable employer hiring evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .opportunity_signal import OpportunitySignal, deduplicate_opportunity_signals


@dataclass(frozen=True)
class EmployerIntelligence:
    """Factual employer evidence summary derived only from observed signals."""

    company: str
    signals: tuple[OpportunitySignal, ...]
    latest_observed_at: datetime
    signal_types: tuple[str, ...]
    sources: tuple[str, ...]
    active_job_count: int
    role_titles: tuple[str, ...]
    locations: tuple[str, ...]
    summary: str


class EmployerIntelligenceService:
    """Aggregate signal evidence without inventing unsupported employer facts."""

    def aggregate(
        self,
        signals: Iterable[OpportunitySignal],
    ) -> tuple[EmployerIntelligence, ...]:
        """Return deterministic employer intelligence snapshots from observed evidence."""
        deduplicated = deduplicate_opportunity_signals(signals)
        grouped: dict[str, list[OpportunitySignal]] = {}
        display_names: dict[str, set[str]] = {}

        for signal in deduplicated:
            key = signal.company.casefold()
            grouped.setdefault(key, []).append(signal)
            display_names.setdefault(key, set()).add(signal.company)

        results: list[EmployerIntelligence] = []
        for key in sorted(grouped):
            company_signals = tuple(
                sorted(
                    grouped[key],
                    key=lambda signal: (
                        signal.observed_at,
                        signal.signal_type,
                        signal.signal_id,
                    ),
                )
            )
            company = sorted(
                display_names[key],
                key=lambda value: (value.casefold(), value),
            )[0]
            sources = tuple(
                sorted({signal.source for signal in company_signals if signal.source})
            )
            signal_types = tuple(
                sorted({signal.signal_type for signal in company_signals})
            )
            role_titles = tuple(
                sorted(
                    {
                        str(signal.metadata.get("job_title", "")).strip()
                        for signal in company_signals
                        if str(signal.metadata.get("job_title", "")).strip()
                    }
                )
            )
            locations = tuple(
                sorted(
                    {
                        self._location_label(signal)
                        for signal in company_signals
                        if self._location_label(signal)
                    }
                )
            )
            active_job_count = sum(
                signal.signal_type == "active_job_posting"
                for signal in company_signals
            )
            latest_observed_at = max(
                signal.observed_at for signal in company_signals
            )
            summary = (
                f"{company}: {len(company_signals)} attributable hiring evidence "
                f"item(s) observed across {len(sources)} source(s)."
            )
            results.append(
                EmployerIntelligence(
                    company=company,
                    signals=company_signals,
                    latest_observed_at=latest_observed_at,
                    signal_types=signal_types,
                    sources=sources,
                    active_job_count=active_job_count,
                    role_titles=role_titles,
                    locations=locations,
                    summary=summary,
                )
            )

        return tuple(results)

    @staticmethod
    def _location_label(signal: OpportunitySignal) -> str:
        city = str(signal.metadata.get("city", "")).strip()
        country = str(signal.metadata.get("country", "")).strip()
        if city and country and city.casefold() != country.casefold():
            return f"{city}, {country}"
        return city or country
