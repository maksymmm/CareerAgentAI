from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JobSearchRequest:
    """
    Immutable Job Search request.
    """

    keywords: str
    location: str = ""