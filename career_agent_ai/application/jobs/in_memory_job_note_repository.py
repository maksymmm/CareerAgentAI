from career_agent_ai.application.jobs.job_note import JobNote
from career_agent_ai.application.jobs.job_note_repository import (
    JobNoteRepository,
)


class InMemoryJobNoteRepository(JobNoteRepository):

    def __init__(self) -> None:
        self._items: list[JobNote] = []

    def add(
        self,
        note: JobNote,
    ) -> None:
        self._items.append(note)

    def list(
        self,
        user_id: str,
    ) -> tuple[JobNote, ...]:
        return tuple(
            item
            for item in self._items
            if item.user_id == user_id
        )

    def clear(self) -> None:
        self._items.clear()