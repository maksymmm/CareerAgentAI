"""SQLite communication state persistence."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from career_agent_ai.application.communication.models import (
    CommunicationMessage,
    MessageDirection,
    validate_identifier,
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
            comparable_existing = self._content(existing)
            comparable_message = self._content(message)
            if comparable_existing != comparable_message:
                raise ValueError("message_id is already bound to different message content.")
            if existing.direction == message.direction:
                return existing
            if not (
                existing.direction == MessageDirection.DRAFT
                and message.direction == MessageDirection.OUTBOUND
            ):
                raise ValueError("A persisted message direction cannot be replaced.")
            self._database.connection.execute(
                "UPDATE communication_messages SET direction = ? WHERE message_id = ?",
                (message.direction.value, message.message_id),
            )
            self._database.connection.commit()
            return message
        try:
            self._database.connection.execute(
                """
                INSERT INTO communication_messages (
                    message_id, thread_id, sender, recipient, subject, body,
                    direction, created_at, in_reply_to
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    message.in_reply_to,
                ),
            )
            self._database.connection.commit()
        except sqlite3.IntegrityError as exc:
            self._database.connection.rollback()
            raise ValueError("Communication message violates persistence constraints.") from exc
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
               ORDER BY julianday(created_at), message_id""",
            (normalized,),
        ).fetchall()
        messages = tuple(self.get(row[0]) for row in rows)
        if any(message is None for message in messages):
            raise ValueError("Communication thread changed while being read.")
        return tuple(message for message in messages if message is not None)

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
                in_reply_to TEXT
            )
            """
        )
        self._database.connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_communication_thread
               ON communication_messages(thread_id, created_at)"""
        )
        self._database.connection.commit()

    @staticmethod
    def _content(message: CommunicationMessage) -> tuple[object, ...]:
        return (
            message.thread_id, message.sender, message.recipient, message.subject,
            message.body, message.created_at, message.in_reply_to,
        )
