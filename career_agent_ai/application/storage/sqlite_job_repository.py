from __future__ import annotations

from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_query import JobQuery
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


class SQLiteJobRepository:
    def __init__(self, database: SQLiteDatabase):
        self._database = database
        self._jobs: list[Job] = []

    def add(self, job: Job) -> None:
        self._jobs.append(job)

    def all(self) -> tuple[Job, ...]:
        return tuple(self._jobs)

    def search(self, query: JobQuery) -> tuple[Job, ...]:
        result: list[Job] = []

        for job in self._jobs:
            if query.keyword:
                text = (
                    f"{job.title} "
                    f"{job.company} "
                    f"{job.location}"
                ).lower()

                if query.keyword.lower() not in text:
                    continue

            result.append(job)

        return tuple(result)

    def clear(self) -> None:
        self._jobs.clear()