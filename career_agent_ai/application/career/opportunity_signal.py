from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping


def _validated_text(
    value: str,
    field_name: str,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be text.")
    normalized = value.strip()
    if not normalized and not allow_empty:
        raise ValueError(f"{field_name} must not be empty.")
    if len(normalized) > maximum:
        raise ValueError(f"{field_name} must not exceed {maximum} characters.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in normalized):
        raise ValueError(f"{field_name} contains forbidden control characters.")
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in normalized):
        raise ValueError(f"{field_name} contains a forbidden Unicode surrogate.")
    return normalized


@dataclass(frozen=True)
class OpportunitySignal:
    """A sourced, time-stamped hiring signal used for proactive opportunity analysis."""

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
        company = _validated_text(self.company, "company", maximum=500)
        signal_type = _validated_text(
            self.signal_type, "signal_type", maximum=100
        )
        source = _validated_text(
            self.source, "source", maximum=2_000, allow_empty=True
        )
        signal_id = _validated_text(
            self.signal_id, "signal_id", maximum=500, allow_empty=True
        )
        if (
            not isinstance(self.strength, (int, float))
            or isinstance(self.strength, bool)
        ):
            raise TypeError("strength must be numeric.")
        strength = float(self.strength)
        if not 0.0 <= strength <= 1.0:
            raise ValueError("strength must be between 0 and 1.")
        if not isinstance(self.observed_at, datetime):
            raise TypeError("observed_at must be a datetime.")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware.")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping.")

        object.__setattr__(self, "company", company)
        object.__setattr__(self, "signal_type", signal_type)
        object.__setattr__(self, "strength", strength)
        object.__setattr__(
            self,
            "observed_at",
            self.observed_at.astimezone(timezone.utc),
        )
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "signal_id", signal_id)
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )

    def dedupe_key(self) -> tuple[str, ...]:
        """Return a deterministic identity key for duplicate suppression."""
        if self.signal_id:
            return ("signal_id", self.signal_id)
        return (
            "observation",
            self.company.casefold(),
            self.signal_type.casefold(),
            self.source.casefold(),
            self.observed_at.isoformat(),
        )
