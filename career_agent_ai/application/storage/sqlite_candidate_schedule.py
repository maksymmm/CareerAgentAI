"""Owner-scoped read-only scheduling projections without schema initialization."""

from dataclasses import asdict
from typing import Any

from career_agent_ai.application.scheduling.models import HumanScheduleView, validate_schedule_identifier
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_scheduling_repository import SQLiteSchedulingRepository


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
                "SELECT event_id, candidate_id, employer_name, event_type, location, "
                "start_at, start_epoch_us, end_at, end_epoch_us, timezone_name, "
                "status, NULL, NULL, version, created_at, updated_at "
                "FROM scheduling_events WHERE candidate_id = ? AND event_id = ?",
                (owner, identifier),
            ).fetchone()
            if row is None:
                raise KeyError("Schedule event not found.")
            event = SQLiteSchedulingRepository._event_from_row(row)
            view = asdict(HumanScheduleView.from_event(event))
            view["event_type"] = event.event_type.value
            view["status"] = event.status.value
            view["state_version"] = event.version
            view["start_at"] = event.start_at.isoformat()
            view["end_at"] = None if event.end_at is None else event.end_at.isoformat()
            return {"schema_version": 1, "event": view}
        finally:
            database.close()
