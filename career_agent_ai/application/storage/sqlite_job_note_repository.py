from __future__ import annotations

from career_agent_ai.application.jobs.job_note import JobNote
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


class SQLiteJobNoteRepository:
    def __init__(self, database: SQLiteDatabase):
        self._database = database
        self._notes: list[JobNote] = []

    def add(self, note: JobNote) -> None:
        self._notes.append(note)

    def get(self, note_id: str) -> JobNote | None:
        for note in self._notes:
            if note.note_id == note_id:
                return note
        return None

    def list(self, user_id: str) -> tuple[JobNote, ...]:
        return tuple(
            note
            for note in self._notes
            if note.user_id == user_id
        )

    def remove(self, note_id: str) -> None:
        self._notes = [
            note
            for note in self._notes
            if note.note_id != note_id
        ]

    def all(self) -> tuple[JobNote, ...]:
        return tuple(self._notes)

    def clear(self) -> None:
        self._notes.clear()