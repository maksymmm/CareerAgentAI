from career_agent_ai.application.jobs.job_note import JobNote
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_job_note_repository import (
    SQLiteJobNoteRepository,
)


def test_sqlite_note_repository():
    repo = SQLiteJobNoteRepository(SQLiteDatabase())

    repo.add(
        JobNote(
            note_id="1",
            user_id="user",
            job_id="job",
            text="hello",
        )
    )

    assert len(repo.list("user")) == 1