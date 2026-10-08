from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class OpportunitySignal:
    """A weak hiring signal observed before a public vacancy exists."""

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

    def __post_init__(self) -> None:
        company = self.company.strip()
        signal_type = self.signal_type.strip()
        source = self.source.strip()

        if not company:
            raise ValueError("company must not be empty.")
        if not signal_type:
            raise ValueError("signal_type must not be empty.")
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError("strength must be between 0 and 1.")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware.")

        object.__setattr__(self, "company", company)
        object.__setattr__(self, "signal_type", signal_type)
        object.__setattr__(self, "source", source)
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )
