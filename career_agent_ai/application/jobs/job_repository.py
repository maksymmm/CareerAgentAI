from __future__ import annotations

from abc import ABC, abstractmethod

from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_query import JobQuery
from career_agent_ai.application.jobs.job_search_result import JobSearchResult


class JobRepository(ABC):

    @abstractmethod
    def add(self, job: Job) -> None:
        ...

    @abstractmethod
    def get(self, job_id: str) -> Job | None:
        ...

    @abstractmethod
    def all(self) -> tuple[Job, ...]:
        ...

    @abstractmethod
    def search(self, query: JobQuery) -> JobSearchResult:
        ...

    @abstractmethod
    def clear(self) -> None:
        ...