from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchResult:
    query: str
    jobs: tuple

    @property
    def count(self) -> int:
        return len(self.jobs)