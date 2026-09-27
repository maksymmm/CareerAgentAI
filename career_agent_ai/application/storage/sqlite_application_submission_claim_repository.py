"""SQLite ownership records for consequential application submissions."""

from __future__ import annotations

import sqlite3

from career_agent_ai.application.jobs.application_submission_adapter import (
    validate_submission_identifier,
)
from career_agent_ai.application.jobs.application_submission_claim_repository import (
    ApplicationSubmissionClaimError,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


class SQLiteApplicationSubmissionClaimRepository:
    """Prevent one application from being externally submitted by competing operations."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database
        self._create_schema()

    def claim(self, application_id: str, operation_id: str) -> None:
        """Create or replay the exact claim; reject every competing operation."""
        application_id = validate_submission_identifier(
            application_id, "application_id"
        )
        operation_id = validate_submission_identifier(operation_id, "operation_id")
        try:
            self._database.connection.execute(
                """
                INSERT INTO application_submission_claims (
                    application_id, operation_id, status
                ) VALUES (?, ?, 'claimed')
                """,
                (application_id, operation_id),
            )
            self._database.connection.commit()
            return
        except sqlite3.IntegrityError:
            self._database.connection.rollback()

        row = self._database.connection.execute(
            """
            SELECT operation_id, status
            FROM application_submission_claims
            WHERE application_id = ?
            """,
            (application_id,),
        ).fetchone()
        if row is not None and row[0] == operation_id and row[1] in {
            "claimed",
            "completed",
        }:
            return
        raise ApplicationSubmissionClaimError(
            "Application submission is already owned by another operation."
        )

    def release(self, application_id: str, operation_id: str) -> None:
        """Delete only the exact still-claimed operation."""
        application_id = validate_submission_identifier(
            application_id, "application_id"
        )
        operation_id = validate_submission_identifier(operation_id, "operation_id")
        cursor = self._database.connection.execute(
            """
            DELETE FROM application_submission_claims
            WHERE application_id = ? AND operation_id = ? AND status = 'claimed'
            """,
            (application_id, operation_id),
        )
        self._database.connection.commit()
        if cursor.rowcount != 1:
            raise ApplicationSubmissionClaimError(
                "Application submission claim is no longer releasable."
            )

    def complete(self, application_id: str, operation_id: str) -> None:
        """Mark the exact claim completed without releasing exclusivity."""
        application_id = validate_submission_identifier(
            application_id, "application_id"
        )
        operation_id = validate_submission_identifier(operation_id, "operation_id")
        cursor = self._database.connection.execute(
            """
            UPDATE application_submission_claims
            SET status = 'completed'
            WHERE application_id = ? AND operation_id = ? AND status = 'claimed'
            """,
            (application_id, operation_id),
        )
        self._database.connection.commit()
        if cursor.rowcount == 1:
            return
        row = self._database.connection.execute(
            """
            SELECT operation_id, status
            FROM application_submission_claims
            WHERE application_id = ?
            """,
            (application_id,),
        ).fetchone()
        if row == (operation_id, "completed"):
            return
        raise ApplicationSubmissionClaimError(
            "Application submission claim changed before completion."
        )

    def _create_schema(self) -> None:
        connection = self._database.connection
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS application_submission_claims (
                application_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL CHECK(status IN ('claimed', 'completed'))
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_application_submission_claim_status
            ON application_submission_claims(status)
            """
        )
        connection.commit()
