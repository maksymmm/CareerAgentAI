from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JobSearchResponse:
    """
    Immutable Job Search response.
    """

    success: bool
    jobs_found: int