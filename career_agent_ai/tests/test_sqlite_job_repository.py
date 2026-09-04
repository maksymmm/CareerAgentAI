from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_job_repository import (
    SQLiteJobRepository,
)


def test_sqlite_job_repository():
    repo = SQLiteJobRepository(SQLiteDatabase())

    repo.add(
        Job.create(
            user_id="user",
            title="Python",
            company="OpenAI",
            location="Remote",
        )
    )

    assert len(repo.all()) == 1