"""Bounded candidate-visible application snapshots without repository migrations."""

from datetime import datetime
from typing import Any
from career_agent_ai.application.jobs.job_application import (
    ApplicationTimelineEvent, JobApplication, application_status_version_is_reachable,
)

from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


class SQLiteCandidateApplications:
    """Read minimal owned application pages; callers supply authenticated identity."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def get_snapshot(self, application_id: str, *, user_id: str) -> dict[str, Any]:
        """Read one owned snapshot; foreign and absent identities are indistinguishable."""
        owner = self._identifier(user_id)
        identifier = self._identifier(application_id)
        database = SQLiteDatabase.open_read_only(self._database_path)
        try:
            row = database.connection.execute(
                "SELECT application_id, job_id, company_id, job_title, status, "
                "version, created_at, updated_at, serialization_version "
                "FROM job_applications WHERE user_id = ? AND application_id = ?",
                (owner, identifier),
            ).fetchone()
            if row is None:
                raise KeyError("Application not found.")
            return {"schema_version": 1, "application": self._snapshot(row)}
        finally:
            database.close()

    def get_history(self, application_id: str, *, user_id: str) -> dict[str, Any]:
        """Read bounded owned lifecycle transitions, never notes, metadata or operation IDs."""
        owner = self._identifier(user_id)
        identifier = self._identifier(application_id)
        database = SQLiteDatabase.open_read_only(self._database_path)
        try:
            # Pin ownership, aggregate version and children to one SQLite snapshot.
            database.connection.execute("BEGIN")
            row = database.connection.execute(
                "SELECT application_id, job_id, company_id, job_title, status, "
                "version, created_at, updated_at, serialization_version "
                "FROM job_applications WHERE user_id = ? AND application_id = ?",
                (owner, identifier),
            ).fetchone()
            if row is None:
                raise KeyError("Application not found.")
            snapshot = self._snapshot(row)
            rows = database.connection.execute(
                "SELECT sequence, from_status, to_status, occurred_at, serialization_version "
                "FROM application_timeline WHERE application_id = ? ORDER BY sequence LIMIT ?",
                (identifier, len(JobApplicationStatus) + 1),
            ).fetchall()
            items = self._history_items(snapshot, rows, owner)
            return {"schema_version": 1, "application_id": identifier,
                    "state_version": snapshot["state_version"], "items": items}
        finally:
            database.close()

    @staticmethod
    def _history_items(snapshot: dict[str, Any], rows: list[tuple[Any, ...]],
                       owner: str) -> list[dict[str, Any]]:
        try:
            if len(rows) != snapshot["state_version"] - 1:
                raise ValueError("History length mismatch")
            events = []
            for sequence, row in enumerate(rows):
                if type(row[0]) is not int or row[0] != sequence or row[4] != 1:
                    raise ValueError("History sequence or serialization mismatch")
                events.append(ApplicationTimelineEvent(
                    event_id=str(sequence), from_status=JobApplicationStatus(row[1]),
                    to_status=JobApplicationStatus(row[2]),
                    occurred_at=datetime.fromisoformat(row[3])))
            # Reuse domain continuity/chronology/version invariants without private children.
            JobApplication(application_id=snapshot["application_id"], user_id=owner,
                job_id=snapshot["job_id"], status=JobApplicationStatus(snapshot["status"]),
                version=snapshot["state_version"], timeline=tuple(events),
                created_at=datetime.fromisoformat(snapshot["created_at"]),
                updated_at=datetime.fromisoformat(snapshot["updated_at"]))
            return [{"sequence": index, "from_status": event.from_status.value,
                     "to_status": event.to_status.value, "occurred_at": event.occurred_at.isoformat()}
                    for index, event in enumerate(events)]
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("Persisted candidate application history is malformed.") from exc

    def list_page(self, *, user_id: str, limit: int = 25,
                  after: str | None = None,
                  status: JobApplicationStatus | None = None) -> dict[str, Any]:
        """Return ID-ordered snapshots without children, claims, writes or execution."""
        owner = self._identifier(user_id)
        cursor = "" if after is None else self._identifier(after)
        if status is not None and not isinstance(status, JobApplicationStatus):
            raise ValueError("status must be a supported application status")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        database = SQLiteDatabase.open_read_only(self._database_path)
        try:
            # Bound and scope in SQL before loading any candidate-visible fields.
            columns = (
                "SELECT application_id, job_id, company_id, job_title, status, "
                "version, created_at, updated_at, serialization_version "
                "FROM job_applications WHERE user_id = ? AND application_id > ? "
            )
            if status is None:
                query = columns + "ORDER BY application_id LIMIT ?"
                parameters: tuple[Any, ...] = (owner, cursor, limit + 1)
            else:
                query = columns + "AND status = ? ORDER BY application_id LIMIT ?"
                parameters = (owner, cursor, status.value, limit + 1)
            rows = database.connection.execute(query, parameters).fetchall()
            items = [self._snapshot(row) for row in rows[:limit]]
            return {"schema_version": 1, "items": items,
                    "next_after": items[-1]["application_id"] if len(rows) > limit else None}
        finally:
            database.close()

    @staticmethod
    def _identifier(value: str) -> str:
        if (not isinstance(value, str) or not value.strip() or len(value) > 200
                or value != value.strip()):
            raise ValueError("identifier must be a canonical non-empty string of at most 200 characters")
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValueError("identifier must contain only Unicode scalar values") from exc
        return value

    @staticmethod
    def _snapshot(row: tuple[Any, ...]) -> dict[str, Any]:
        try:
            if row[8] != 1:
                raise ValueError("Unsupported application serialization")
            SQLiteCandidateApplications._identifier(row[0])
            SQLiteCandidateApplications._identifier(row[1])
            for value, maximum in ((row[2], 200), (row[3], 500)):
                if not isinstance(value, str) or len(value) > maximum or value != value.strip():
                    raise ValueError("Non-canonical application snapshot")
            status = JobApplicationStatus(row[4])
            if not application_status_version_is_reachable(status, row[5]):
                raise ValueError("Invalid snapshot version")
            created_at, updated_at = (datetime.fromisoformat(value) for value in row[6:8])
            if (created_at.utcoffset() is None or updated_at.utcoffset() is None
                    or updated_at < created_at):
                raise ValueError("Invalid snapshot timestamps")
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("Persisted candidate application snapshot is malformed.") from exc
        return {"application_id": row[0], "job_id": row[1],
                "company_id": row[2], "job_title": row[3],
                "status": status.value, "state_version": row[5],
                "created_at": created_at.isoformat(), "updated_at": updated_at.isoformat()}
