from __future__ import annotations

from career_agent_ai.application.jobs.job_query import JobQuery
from career_agent_ai.application.jobs.job_repository import JobRepository


class SearchEngine:
    def __init__(self, repository: JobRepository):
        self._repository = repository

    def search(self, query: JobQuery) -> tuple:
        return tuple(self._repository.search(query))