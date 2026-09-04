from __future__ import annotations

from dataclasses import dataclass

from career_agent_ai.application.search.search_filter import SearchFilter


@dataclass(frozen=True)
class JobSearchRequest:
    user_id: str
    filter: SearchFilter