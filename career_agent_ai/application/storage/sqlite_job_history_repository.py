from career_agent_ai.application.jobs.in_memory_job_history_repository import (
    InMemoryJobHistoryRepository,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


class SQLiteJobHistoryRepository(
    InMemoryJobHistoryRepository
):

    def __init__(
        self,
        database: SQLiteDatabase,
    ) -> None:
        super().__init__()
        self._database = database