"""SQLite communication state persistence."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from career_agent_ai.application.communication.models import (
    CommunicationMessage,
    MessageDirection,
    validate_identifier,
)
from career_agent_ai.application.external_actions.external_action_repository import (
    OperationConflictError,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


class SQLiteCommunicationRepository:
    """Restart-safe persistence for messages and conversation identifiers."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database
        self._create_schema()

    def save(self, message: CommunicationMessage) -> CommunicationMessage:
        """Insert a message or update only its draft-to-outbound delivery state."""
        existing = self.get(message.message_id)
        if existing is not None:
            return self._resolve_existing(existing, message, allow_delivery=True)
        try:
            self._database.connection.execute(
                """
                INSERT INTO communication_messages (
                    message_id, thread_id, sender, recipient, subject, body,
                    direction, created_at, created_at_epoch_us, in_reply_to
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.message_id,
                    message.thread_id,
                    message.sender,
                    message.recipient,
                    message.subject,
                    message.body,
                    message.direction.value,
                    message.created_at.isoformat(),
                    self._epoch_microseconds(message.created_at),
                    message.in_reply_to,
                ),
            )
            self._database.connection.commit()
        except sqlite3.IntegrityError as exc:
            self._database.connection.rollback()
            winner = self.get(message.message_id)
            if winner is None:
                raise ValueError(
                    "Communication message violates persistence constraints."
                ) from exc
            return self._resolve_existing(winner, message, allow_delivery=False)
        return message

    def get(self, message_id: str) -> CommunicationMessage | None:
        """Load one message while validating every persisted field."""
        normalized = validate_identifier(message_id, "message_id")
        row = self._database.connection.execute(
            """
            SELECT message_id, thread_id, sender, recipient, subject, body,
                   direction, created_at, in_reply_to
            FROM communication_messages WHERE message_id = ?
            """,
            (normalized,),
        ).fetchone()
        if row is None:
            return None
        try:
            return CommunicationMessage(
                message_id=row[0], thread_id=row[1], sender=row[2], recipient=row[3],
                subject=row[4], body=row[5], direction=MessageDirection(row[6]),
                created_at=datetime.fromisoformat(row[7]), in_reply_to=row[8],
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Persisted communication message is malformed.") from exc

    def list_thread(self, thread_id: str) -> tuple[CommunicationMessage, ...]:
        """Return all messages in a validated thread deterministically."""
        normalized = validate_identifier(thread_id, "thread_id")
        rows = self._database.connection.execute(
            """SELECT message_id FROM communication_messages
               WHERE thread_id = ?
               ORDER BY created_at_epoch_us, message_id""",
            (normalized,),
        ).fetchall()
        messages = tuple(self.get(row[0]) for row in rows)
        if any(message is None for message in messages):
            raise ValueError("Communication thread changed while being read.")
        return tuple(message for message in messages if message is not None)

    def claim_delivery(self, message_id: str, operation_id: str) -> None:
        """Atomically grant one operation exclusive ownership of an unsent draft."""
        normalized_message = validate_identifier(message_id, "message_id")
        normalized_operation = validate_identifier(operation_id, "operation_id")
        try:
            cursor = self._database.connection.execute(
                """
                UPDATE communication_messages
                SET delivery_operation_id = ?
                WHERE message_id = ? AND direction = 'draft'
                  AND (delivery_operation_id IS NULL OR delivery_operation_id = ?)
                """,
                (normalized_operation, normalized_message, normalized_operation),
            )
            self._database.connection.commit()
        except sqlite3.IntegrityError as exc:
            self._database.connection.rollback()
            raise OperationConflictError(
                "Delivery operation is already bound to another message."
            ) from exc
        if cursor.rowcount != 1:
            raise OperationConflictError(
                "Message is not an unclaimed draft owned by this delivery operation."
            )

    def release_delivery(self, message_id: str, operation_id: str) -> None:
        """Atomically release the exact operation's still-unsent delivery claim."""
        normalized_message = validate_identifier(message_id, "message_id")
        normalized_operation = validate_identifier(operation_id, "operation_id")
        cursor = self._database.connection.execute(
            """
            UPDATE communication_messages
            SET delivery_operation_id = NULL
            WHERE message_id = ? AND direction = 'draft'
              AND delivery_operation_id = ?
            """,
            (normalized_message, normalized_operation),
        )
        self._database.connection.commit()
        if cursor.rowcount != 1:
            raise OperationConflictError(
                "Delivery claim is no longer releasable by this operation."
            )

    def _create_schema(self) -> None:
        self._database.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS communication_messages (
                message_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                sender TEXT NOT NULL,
                recipient TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                direction TEXT NOT NULL CHECK(direction IN ('draft', 'outbound', 'inbound')),
                created_at TEXT NOT NULL,
                created_at_epoch_us INTEGER NOT NULL,
                in_reply_to TEXT,
                delivery_operation_id TEXT
            )
            """
        )
        columns = {
            row[1]
            for row in self._database.connection.execute(
                "PRAGMA table_info(communication_messages)"
            ).fetchall()
        }
        if "delivery_operation_id" not in columns:
            self._database.connection.execute(
                "ALTER TABLE communication_messages ADD COLUMN delivery_operation_id TEXT"
            )
        if "created_at_epoch_us" not in columns:
            self._database.connection.execute(
                "ALTER TABLE communication_messages ADD COLUMN created_at_epoch_us INTEGER"
            )
        rows = self._database.connection.execute(
            """SELECT message_id, created_at FROM communication_messages
               WHERE created_at_epoch_us IS NULL"""
        ).fetchall()
        for message_id, created_at in rows:
            try:
                parsed = datetime.fromisoformat(created_at)
                epoch_microseconds = self._epoch_microseconds(parsed)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "Persisted communication timestamp is malformed."
                ) from exc
            self._database.connection.execute(
                """UPDATE communication_messages SET created_at_epoch_us = ?
                   WHERE message_id = ?""",
                (epoch_microseconds, message_id),
            )
        self._database.connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_communication_thread
               ON communication_messages(thread_id, created_at)"""
        )
        self._database.connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_communication_delivery_operation
               ON communication_messages(delivery_operation_id)
               WHERE delivery_operation_id IS NOT NULL"""
        )
        self._database.connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_communication_thread_instant
               ON communication_messages(thread_id, created_at_epoch_us, message_id)"""
        )
        self._database.connection.commit()

    @staticmethod
    def _content(message: CommunicationMessage) -> tuple[object, ...]:
        return (
            message.thread_id, message.sender, message.recipient, message.subject,
            message.body, message.created_at, message.in_reply_to,
        )

    def _resolve_existing(
        self,
        existing: CommunicationMessage,
        requested: CommunicationMessage,
        *,
        allow_delivery: bool,
    ) -> CommunicationMessage:
        if self._content(existing) != self._content(requested):
            raise ValueError("message_id is already bound to different message content.")
        if existing.direction == requested.direction:
            return existing
        if not (
            allow_delivery
            and existing.direction == MessageDirection.DRAFT
            and requested.direction == MessageDirection.OUTBOUND
        ):
            raise ValueError("A persisted message direction cannot be replaced.")
        self._database.connection.execute(
            "UPDATE communication_messages SET direction = ? WHERE message_id = ?",
            (requested.direction.value, requested.message_id),
        )
        self._database.connection.commit()
        return requested

    @staticmethod
    def _epoch_microseconds(value: datetime) -> int:
        """Convert an aware timestamp to an exact integer UTC microsecond instant."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware.")
        utc_value = value.astimezone(timezone.utc)
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        delta = utc_value - epoch
        return (
            delta.days * 86_400_000_000
            + delta.seconds * 1_000_000
            + delta.microseconds
        )
