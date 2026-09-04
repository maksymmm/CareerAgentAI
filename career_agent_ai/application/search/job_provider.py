from __future__ import annotations

from abc import ABC, abstractmethod

from career_agent_ai.application.jobs.job import Job


class JobProvider(ABC):

    @abstractmethod
    def search(
        self,
        query: str,
    ) -> tuple[Job, ...]:
        raise NotImplementedError
