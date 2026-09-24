from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from career_agent_ai.application.jobs.job_application import (
    ApplicationTimelineEvent,
    JobApplication,
)
from career_agent_ai.application.jobs.job_application_repository import (
    ApplicationConflictError,
    ApplicationQuery,
    JobApplicationRepository,
)
from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


class SQLiteJobApplicationRepository(JobApplicationRepository):
    """Restart-safe SQLite storage for application aggregates and history."""

    SERIALIZATION_VERSION = 1

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database
        self._database.connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def add(self, application: JobApplication) -> None:
        """Create an aggregate and reject duplicate IDs or user/job pairs."""
        connection = self._database.connection
        try:
            connection.execute("BEGIN")
            connection.execute(
                """INSERT INTO job_applications (
                       application_id, user_id, job_id, company_id, status,
                       created_at, updated_at, version, serialization_version
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                self._application_values(application),
            )
            self._insert_children(application)
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise ApplicationConflictError(
                "An application with this ID or user/job pair already exists."
            ) from exc
        except Exception:
            connection.rollback()
            raise

    def get(self, application_id: str) -> JobApplication | None:
        """Load and strictly validate an application and all child records."""
        normalized = self._required_filter(application_id, "application_id")
        row = self._database.connection.execute(
            """SELECT application_id, user_id, job_id, company_id, status,
                      created_at, updated_at, version, serialization_version
               FROM job_applications WHERE application_id = ?""",
            (normalized,),
        ).fetchone()
        return None if row is None else self._load(row)

    def list(self, user_id: str) -> tuple[JobApplication, ...]:
        """Return one user's applications in stable chronological order."""
        return self.find(ApplicationQuery(user_id=user_id))

    def update(self, application: JobApplication, *, expected_version: int) -> None:
        """Atomically replace mutable aggregate data using its prior version."""
        if expected_version < 1 or application.version != expected_version + 1:
            raise ApplicationConflictError("Application version does not follow expected_version.")
        connection = self._database.connection
        try:
            connection.execute("BEGIN")
            current = connection.execute(
                """SELECT user_id, job_id, created_at, version
                   FROM job_applications WHERE application_id = ?""",
                (application.application_id,),
            ).fetchone()
            if current is None or current[3] != expected_version:
                raise ApplicationConflictError("Application is missing or has changed.")
            if application.user_id != current[0] or application.job_id != current[1]:
                raise ApplicationConflictError("Application identity cannot be changed.")
            try:
                persisted_created_at = datetime.fromisoformat(current[2])
            except (TypeError, ValueError) as exc:
                raise ValueError("Persisted job application is malformed.") from exc
            if application.created_at != persisted_created_at:
                raise ApplicationConflictError("Application created_at cannot be changed.")
            cursor = connection.execute(
                """UPDATE job_applications
                   SET company_id = ?, status = ?, updated_at = ?, version = ?
                   WHERE application_id = ? AND user_id = ? AND job_id = ? AND version = ?""",
                (
                    application.company_id,
                    application.status.value,
                    application.updated_at.isoformat(),
                    application.version,
                    application.application_id,
                    application.user_id,
                    application.job_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise ApplicationConflictError("Application is missing or has changed.")
            connection.execute(
                "DELETE FROM application_external_actions WHERE application_id = ?",
                (application.application_id,),
            )
            connection.execute(
                "DELETE FROM application_timeline WHERE application_id = ?",
                (application.application_id,),
            )
            self._insert_children(application)
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def find(self, query: ApplicationQuery) -> tuple[JobApplication, ...]:
        """Return applications matching all filters in deterministic order."""
        clauses: list[str] = []
        parameters: list[str] = []
        fields = {
            "user_id": query.user_id,
            "job_id": query.job_id,
            "company_id": query.company_id,
            "status": None if query.status is None else query.status.value,
        }
        for column, value in fields.items():
            if value is not None:
                clauses.append(f"a.{column} = ?")
                parameters.append(self._required_filter(value, column))
        join = ""
        if query.external_action_operation_id is not None:
            join = " JOIN application_external_actions x ON x.application_id = a.application_id"
            clauses.append("x.operation_id = ?")
            parameters.append(self._required_filter(query.external_action_operation_id, "external_action_operation_id"))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._database.connection.execute(
            """SELECT a.application_id, a.user_id, a.job_id, a.company_id,
                      a.status, a.created_at, a.updated_at, a.version,
                      a.serialization_version
               FROM job_applications a""" + join + where
            + " ORDER BY a.created_at, a.application_id",
            tuple(parameters),
        ).fetchall()
        return tuple(self._load(row) for row in rows)

    def clear(self) -> None:
        """Delete every application; foreign-key cascades delete its history."""
        self._database.connection.execute("DELETE FROM job_applications")
        self._database.connection.commit()

    def _insert_children(self, application: JobApplication) -> None:
        connection = self._database.connection
        connection.executemany(
            """INSERT INTO application_external_actions (application_id, operation_id)
               VALUES (?, ?)""",
            ((application.application_id, item) for item in application.external_action_operation_ids),
        )
        connection.executemany(
            """INSERT INTO application_timeline (
                   event_id, application_id, from_status, to_status, occurred_at,
                   operation_id, note, metadata_json, serialization_version
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                (
                    event.event_id,
                    application.application_id,
                    event.from_status.value,
                    event.to_status.value,
                    event.occurred_at.isoformat(),
                    event.operation_id,
                    event.note,
                    self._dump_metadata(event.metadata),
                    self.SERIALIZATION_VERSION,
                )
                for event in application.timeline
            ),
        )

    def _load(self, row: tuple[Any, ...]) -> JobApplication:
        try:
            if row[8] != self.SERIALIZATION_VERSION:
                raise ValueError("Unsupported application serialization version.")
            operation_rows = self._database.connection.execute(
                """SELECT operation_id FROM application_external_actions
                   WHERE application_id = ? ORDER BY rowid""",
                (row[0],),
            ).fetchall()
            event_rows = self._database.connection.execute(
                """SELECT event_id, from_status, to_status, occurred_at,
                          operation_id, note, metadata_json, serialization_version
                   FROM application_timeline WHERE application_id = ?
                   ORDER BY occurred_at, event_id""",
                (row[0],),
            ).fetchall()
            events = tuple(self._load_event(item) for item in event_rows)
            application = JobApplication(
                application_id=row[0], user_id=row[1], job_id=row[2], company_id=row[3],
                status=JobApplicationStatus(row[4]), created_at=datetime.fromisoformat(row[5]),
                updated_at=datetime.fromisoformat(row[6]), version=row[7],
                external_action_operation_ids=tuple(item[0] for item in operation_rows),
                timeline=events,
            )
            if events and events[-1].to_status != application.status:
                raise ValueError("Latest timeline event does not match application status.")
            return application
        except (AttributeError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Persisted job application is malformed.") from exc

    def _load_event(self, row: tuple[Any, ...]) -> ApplicationTimelineEvent:
        if row[7] != self.SERIALIZATION_VERSION:
            raise ValueError("Unsupported timeline serialization version.")
        metadata = json.loads(row[6], parse_constant=self._reject_constant)
        if not isinstance(metadata, dict):
            raise ValueError("Timeline metadata must contain a JSON object.")
        return ApplicationTimelineEvent(
            event_id=row[0], from_status=JobApplicationStatus(row[1]),
            to_status=JobApplicationStatus(row[2]), occurred_at=datetime.fromisoformat(row[3]),
            operation_id=row[4], note=row[5], metadata=metadata,
        )

    def _create_schema(self) -> None:
        connection = self._database.connection
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS job_applications (
                application_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, job_id TEXT NOT NULL,
                company_id TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN
                ('saved','applied','interview','offer','rejected','withdrawn')),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                version INTEGER NOT NULL CHECK(version > 0), serialization_version INTEGER NOT NULL,
                UNIQUE(user_id, job_id)
            );
            CREATE INDEX IF NOT EXISTS idx_applications_user_status
                ON job_applications(user_id, status, created_at, application_id);
            CREATE INDEX IF NOT EXISTS idx_applications_company
                ON job_applications(company_id, created_at, application_id);
            CREATE TABLE IF NOT EXISTS application_external_actions (
                application_id TEXT NOT NULL REFERENCES job_applications(application_id) ON DELETE CASCADE,
                operation_id TEXT NOT NULL, PRIMARY KEY(application_id, operation_id)
            );
            CREATE INDEX IF NOT EXISTS idx_application_external_operation
                ON application_external_actions(operation_id);
            CREATE TABLE IF NOT EXISTS application_timeline (
                event_id TEXT PRIMARY KEY,
                application_id TEXT NOT NULL REFERENCES job_applications(application_id) ON DELETE CASCADE,
                from_status TEXT NOT NULL, to_status TEXT NOT NULL, occurred_at TEXT NOT NULL,
                operation_id TEXT, note TEXT NOT NULL, metadata_json TEXT NOT NULL,
                serialization_version INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_application_timeline_order
                ON application_timeline(application_id, occurred_at, event_id);
            """
        )
        connection.commit()

    @classmethod
    def _application_values(cls, application: JobApplication) -> tuple[Any, ...]:
        return (
            application.application_id, application.user_id, application.job_id,
            application.company_id, application.status.value, application.created_at.isoformat(),
            application.updated_at.isoformat(), application.version, cls.SERIALIZATION_VERSION,
        )

    @staticmethod
    def _required_filter(value: str, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must not be empty.")
        return value.strip()

    @staticmethod
    def _dump_metadata(value: Any) -> str:
        try:
            return json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("Timeline metadata must be JSON-serializable.") from exc

    @staticmethod
    def _reject_constant(value: str) -> None:
        raise ValueError(f"Invalid JSON constant: {value}.")
