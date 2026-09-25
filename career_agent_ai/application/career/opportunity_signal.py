from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from math import isfinite
from types import MappingProxyType
from typing import Any, Iterable, Mapping


def _clean_text(value: str, field_name: str, *, allow_empty: bool = False) -> str:
    """Validate and normalize external signal text."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be text.")
    normalized = value.strip()
    if not normalized and not allow_empty:
        raise ValueError(f"{field_name} must not be empty.")
    if len(normalized) > 2_000:
        raise ValueError(f"{field_name} must not exceed 2000 characters.")
    if any(ord(character) < 32 for character in normalized):
        raise ValueError(f"{field_name} contains forbidden control characters.")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in normalized):
        raise ValueError(f"{field_name} contains a forbidden Unicode surrogate.")
    return normalized


def _fallback_signal_id(
    company: str,
    signal_type: str,
    source: str,
    strength: float,
) -> str:
    """Build a stable identifier for providers that do not supply one."""
    raw = "|".join(
        (
            company.casefold(),
            signal_type.casefold(),
            source,
            format(strength, ".17g"),
        )
    )
    return f"signal:{sha256(raw.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True)
class OpportunitySignal:
    """One attributable hiring signal observed from an external or injected source."""

    company: str
    signal_type: str
    strength: float
    observed_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    source: str = ""
    metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    signal_id: str = ""

    def __post_init__(self) -> None:
        company = _clean_text(self.company, "company")
        signal_type = _clean_text(self.signal_type, "signal_type")
        source = _clean_text(self.source, "source", allow_empty=True)

        if isinstance(self.strength, bool) or not isinstance(
            self.strength, (int, float)
        ):
            raise TypeError("strength must be a number.")
        strength = float(self.strength)
        if not isfinite(strength) or not 0.0 <= strength <= 1.0:
            raise ValueError("strength must be between 0 and 1.")

        if not isinstance(self.observed_at, datetime):
            raise TypeError("observed_at must be a datetime.")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware.")
        observed_at = self.observed_at.astimezone(timezone.utc)

        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping.")
        metadata = MappingProxyType(dict(self.metadata))

        signal_id = (
            _clean_text(self.signal_id, "signal_id")
            if self.signal_id
            else _fallback_signal_id(company, signal_type, source, strength)
        )

        object.__setattr__(self, "company", company)
        object.__setattr__(self, "signal_type", signal_type)
        object.__setattr__(self, "strength", strength)
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "metadata", metadata)
        object.__setattr__(self, "signal_id", signal_id)

    def evidence_signature(self) -> tuple[object, ...]:
        """Return fields that must agree for repeated observations of one signal ID."""
        return (
            self.company.casefold(),
            self.signal_type,
            self.strength,
            self.source,
            dict(self.metadata),
        )


def deduplicate_opportunity_signals(
    signals: Iterable[OpportunitySignal],
) -> tuple[OpportunitySignal, ...]:
    """Deduplicate evidence by stable signal ID and keep the newest observation.

    Reusing one signal ID for materially different evidence is rejected instead of
    silently merging unrelated external facts.
    """
    by_id: dict[str, OpportunitySignal] = {}
    for signal in signals:
        if not isinstance(signal, OpportunitySignal):
            raise TypeError("signals must contain OpportunitySignal values.")
        existing = by_id.get(signal.signal_id)
        if existing is None:
            by_id[signal.signal_id] = signal
            continue
        if existing.evidence_signature() != signal.evidence_signature():
            raise ValueError(
                f"signal_id {signal.signal_id!r} is bound to conflicting evidence."
            )
        if signal.observed_at > existing.observed_at:
            by_id[signal.signal_id] = signal

    return tuple(
        sorted(
            by_id.values(),
            key=lambda signal: (
                signal.company.casefold(),
                signal.signal_type,
                signal.signal_id,
            ),
        )
    )
