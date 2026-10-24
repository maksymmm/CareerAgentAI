"""Owner-scoped read-only scheduling projections without schema initialization."""

from dataclasses import asdict
from typing import Any

from career_agent_ai.application.scheduling.models import HumanScheduleView, ScheduleStatus, validate_schedule_identifier
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_scheduling_repository import SQLiteSchedulingRepository

_VISIBLE_COLUMNS = (
    "event_id, candidate_id, employer_name, event_type, location, "
    "start_at, start_epoch_us, end_at, end_epoch_us, timezone_name, "
    "status, NULL, NULL, version, created_at, updated_at "
)


class SQLiteCandidateSchedule:
    """Read existing candidate events without provider identities or action claims."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def get_snapshot(self, event_id: str, *, user_id: str) -> dict[str, Any]:
        """Return exact local-time details only after SQL ownership filtering."""
        owner = validate_schedule_identifier(user_id, "user_id")
        identifier = validate_schedule_identifier(event_id, "event_id")
        database = SQLiteDatabase.open_read_only(self._database_path)
        try:
            # Match the domain deserializer's scalar layout, substituting NULL for
            # private provider/application identifiers rather than loading them.
            row = database.connection.execute(
                "SELECT " + _VISIBLE_COLUMNS +
                "FROM scheduling_events WHERE candidate_id = ? AND event_id = ?",
                (owner, identifier),
            ).fetchone()
            if row is None:
                raise KeyError("Schedule event not found.")
            return {"schema_version": 1, "event": self._snapshot(row)}
        finally:
            database.close()

    def list_page(self, *, user_id: str, limit: int = 25,
                  after: str | None = None,
                  status: ScheduleStatus | None = None) -> dict[str, Any]:
        """Read bounded ID-ordered owned events without private fields or calendar I/O."""
        owner = validate_schedule_identifier(user_id, "user_id")
        cursor = "" if after is None else validate_schedule_identifier(after, "after")
        if after is not None and cursor != after:
            raise ValueError("after must be a canonical event identity.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100.")
        if status is not None and not isinstance(status, ScheduleStatus):
            raise TypeError("status must be a ScheduleStatus.")
        database = SQLiteDatabase.open_read_only(self._database_path)
        try:
            # Bound in SQL, using the owner/ID index installed only by writable startup.
            where = "candidate_id = ? AND event_id > ?"
            parameters: tuple[Any, ...] = (owner, cursor)
            if status is not None:
                where += " AND status = ?"
                parameters += (status.value,)
            rows = database.connection.execute(
                "SELECT " + _VISIBLE_COLUMNS + "FROM scheduling_events WHERE " +
                where + " ORDER BY event_id LIMIT ?", parameters + (limit + 1,),
            ).fetchall()
            items = [self._snapshot(row) for row in rows[:limit]]
            return {"schema_version": 1, "items": items,
                    "next_after": items[-1]["event_id"] if len(rows) > limit else None}
        finally:
            database.close()

    @staticmethod
    def _snapshot(row: tuple[Any, ...]) -> dict[str, Any]:
        event = SQLiteSchedulingRepository._event_from_row(row)
        view = asdict(HumanScheduleView.from_event(event))
        view["event_type"] = event.event_type.value
        view["status"] = event.status.value
        view["state_version"] = event.version
        view["start_at"] = event.start_at.isoformat()
        view["end_at"] = None if event.end_at is None else event.end_at.isoformat()
        return view
