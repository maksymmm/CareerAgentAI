from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class OutreachDraft:
    """A reviewable, unsent proactive outreach message."""

    company: str
    subject: str
    body: str
    opportunity_score: float
    rationale: str
    metadata: Mapping[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        company = self.company.strip()
        subject = self.subject.strip()
        body = self.body.strip()
        rationale = self.rationale.strip()

        if not company:
            raise ValueError("company must not be empty.")
        if not subject:
            raise ValueError("subject must not be empty.")
        if not body:
            raise ValueError("body must not be empty.")
        if not 0.0 <= self.opportunity_score <= 1.0:
            raise ValueError("opportunity_score must be between 0 and 1.")
        if not rationale:
            raise ValueError("rationale must not be empty.")

        object.__setattr__(self, "company", company)
        object.__setattr__(self, "subject", subject)
        object.__setattr__(self, "body", body)
        object.__setattr__(self, "rationale", rationale)
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )
