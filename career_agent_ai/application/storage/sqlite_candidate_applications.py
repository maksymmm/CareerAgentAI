"""Bounded candidate-visible application snapshots without repository migrations."""

from datetime import datetime
from typing import Any

from career_agent_ai.application.jobs.job_application import JobApplication
from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


class SQLiteCandidateApplications:
    """Read minimal owned application pages; callers supply authenticated identity."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def list_page(self, *, user_id: str, limit: int = 25,
                  after: str | None = None) -> dict[str, Any]:
        """Return ID-ordered snapshots without children, claims, writes or execution."""
        owner = self._identifier(user_id)
        cursor = "" if after is None else self._identifier(after)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        database = SQLiteDatabase.open_read_only(self._database_path)
        try:
            # Bound and scope in SQL before loading any candidate-visible fields.
            rows = database.connection.execute(
                "SELECT application_id, job_id, company_id, job_title, status, "
                "version, created_at, updated_at, serialization_version "
                "FROM job_applications WHERE user_id = ? AND application_id > ? "
                "ORDER BY application_id LIMIT ?", (owner, cursor, limit + 1),
            ).fetchall()
            items = [self._snapshot(row, owner) for row in rows[:limit]]
            return {"schema_version": 1, "items": items,
                    "next_after": items[-1]["application_id"] if len(rows) > limit else None}
        finally:
            database.close()

    @staticmethod
    def _identifier(value: str) -> str:
        if (not isinstance(value, str) or not value.strip() or len(value) > 200
                or value != value.strip()):
            raise ValueError("identifier must be a canonical non-empty string of at most 200 characters")
        return value

    @staticmethod
    def _snapshot(row: tuple[Any, ...], owner: str) -> dict[str, Any]:
        try:
            if row[8] != 1:
                raise ValueError("Unsupported application serialization")
            application = JobApplication(
                application_id=row[0], user_id=owner, job_id=row[1],
                company_id=row[2], job_title=row[3], status=JobApplicationStatus(row[4]),
                version=row[5], created_at=datetime.fromisoformat(row[6]),
                updated_at=datetime.fromisoformat(row[7]),
            )
            if (application.application_id, application.job_id, application.company_id,
                    application.job_title) != tuple(row[:4]):
                raise ValueError("Non-canonical application snapshot")
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("Persisted candidate application snapshot is malformed.") from exc
        return {"application_id": application.application_id, "job_id": application.job_id,
                "company_id": application.company_id, "job_title": application.job_title,
                "status": application.status.value, "state_version": application.version,
                "created_at": application.created_at.isoformat(),
                "updated_at": application.updated_at.isoformat()}
