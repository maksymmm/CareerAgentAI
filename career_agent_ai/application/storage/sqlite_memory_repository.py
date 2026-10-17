from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from career_agent_ai.application.memory.memory_record import MemoryRecord
from career_agent_ai.application.memory.memory_repository import MemoryRepository
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_migrations import (
    SQLiteMigration,
    SQLiteMigrationRunner,
)


class SQLiteMemoryRepository(MemoryRepository):
    """Restart-safe SQLite career memory with versioned JSON serialization."""

    SERIALIZATION_VERSION = 1
    LEGACY_COMPOSITE_KEY_MIGRATION = SQLiteMigration(
        version=2026100301,
        name="career_memory_composite_primary_key",
        statements=(
            "DROP TABLE IF EXISTS career_memory_v2",
            """
            CREATE TABLE career_memory_v2 (
                user_id TEXT NOT NULL,
                memory_key TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                value_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                serialization_version INTEGER NOT NULL,
                PRIMARY KEY (user_id, memory_key)
            )
            """,
            """
            INSERT INTO career_memory_v2 (
                user_id, memory_key, memory_type, value_json, metadata_json,
                created_at, serialization_version
            )
            SELECT user_id, memory_key, memory_type, value_json, metadata_json,
                   created_at, serialization_version
            FROM career_memory
            """,
            "DROP TABLE career_memory",
            "ALTER TABLE career_memory_v2 RENAME TO career_memory",
        ),
        destructive=True,
    )

    def __init__(
        self,
        database: SQLiteDatabase,
        *,
        allow_destructive_migration: bool = False,
    ) -> None:
        self._database = database
        self._allow_destructive_migration = allow_destructive_migration
        self._create_schema()

    def save(self, record: MemoryRecord) -> None:
        """Upsert one user's key without overwriting another user's memory."""
        value_json = self._dump(record.value, "value")
        metadata_json = self._dump(dict(record.metadata), "metadata")
        self._database.connection.execute(
            """
            INSERT INTO career_memory (
                user_id, memory_key, memory_type, value_json, metadata_json,
                created_at, serialization_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, memory_key) DO UPDATE SET
                memory_type = excluded.memory_type,
                value_json = excluded.value_json,
                metadata_json = excluded.metadata_json,
                created_at = excluded.created_at,
                serialization_version = excluded.serialization_version
            """,
            (
                record.user_id,
                record.key,
                record.memory_type,
                value_json,
                metadata_json,
                record.created_at.isoformat(),
                self.SERIALIZATION_VERSION,
            ),
        )
        self._database.connection.commit()

    def get(
        self, key: str, *, user_id: str | None = None
    ) -> MemoryRecord | None:
        """Load one record, requiring a user scope for an ambiguous shared key."""
        normalized_key = key.strip()
        if not normalized_key:
            raise ValueError("Memory key must not be empty.")
        if user_id is not None:
            normalized_user = user_id.strip()
            if not normalized_user:
                raise ValueError("Memory user_id filter must not be empty.")
            row = self._database.connection.execute(
                """SELECT memory_key, user_id, memory_type, value_json, metadata_json,
                          created_at, serialization_version
                   FROM career_memory
                   WHERE user_id = ? AND memory_key = ?""",
                (normalized_user, normalized_key),
            ).fetchone()
            return None if row is None else self._load(row)

        rows = self._database.connection.execute(
            """SELECT memory_key, user_id, memory_type, value_json, metadata_json,
                      created_at, serialization_version
               FROM career_memory
               WHERE memory_key = ?
               ORDER BY user_id
               LIMIT 2""",
            (normalized_key,),
        ).fetchall()
        if len(rows) > 1:
            raise ValueError(
                "Memory key exists for multiple users; user_id is required."
            )
        return None if not rows else self._load(rows[0])

    def find(
        self,
        *,
        user_id: str | None = None,
        memory_type: str | None = None,
    ) -> tuple[MemoryRecord, ...]:
        """Load validated records filtered by user and/or memory type."""
        normalized_user = None
        if user_id is not None:
            normalized_user = user_id.strip()
            if not normalized_user:
                raise ValueError("Memory user_id filter must not be empty.")
        normalized_type = None
        if memory_type is not None:
            normalized_type = memory_type.strip()
            if not normalized_type:
                raise ValueError("Memory type filter must not be empty.")

        clauses: list[str] = []
        parameters: list[str] = []
        if normalized_user is not None:
            clauses.append("user_id = ?")
            parameters.append(normalized_user)
        if normalized_type is not None:
            clauses.append("memory_type = ?")
            parameters.append(normalized_type)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._database.connection.execute(
            """SELECT memory_key, user_id, memory_type, value_json, metadata_json,
                      created_at, serialization_version
               FROM career_memory"""
            + where
            + " ORDER BY user_id, memory_key",
            tuple(parameters),
        ).fetchall()
        return tuple(self._load(row) for row in rows)

    def clear(self) -> None:
        """Delete every career memory record."""
        self._database.connection.execute("DELETE FROM career_memory")
        self._database.connection.commit()

    def _create_schema(self) -> None:
        connection = self._database.connection
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS career_memory (
                user_id TEXT NOT NULL,
                memory_key TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                value_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                serialization_version INTEGER NOT NULL,
                PRIMARY KEY (user_id, memory_key)
            )
            """
        )
        primary_key = tuple(
            (row[1], row[5])
            for row in connection.execute("PRAGMA table_info(career_memory)").fetchall()
            if row[5]
        )
        if primary_key != (("user_id", 1), ("memory_key", 2)):
            SQLiteMigrationRunner(self._database).apply(
                (self.LEGACY_COMPOSITE_KEY_MIGRATION,),
                allow_destructive=self._allow_destructive_migration,
            )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_career_memory_user_type
               ON career_memory(user_id, memory_type)"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_career_memory_type
               ON career_memory(memory_type)"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_career_memory_key
               ON career_memory(memory_key)"""
        )
        connection.commit()

    @classmethod
    def _load(cls, row: tuple[Any, ...]) -> MemoryRecord:
        try:
            if row[6] != cls.SERIALIZATION_VERSION:
                raise ValueError("Unsupported serialization version.")
            value = json.loads(row[3], parse_constant=cls._reject_constant)
            metadata = json.loads(row[4], parse_constant=cls._reject_constant)
            if not isinstance(metadata, dict):
                raise ValueError("metadata_json must contain a JSON object.")
            return MemoryRecord(
                key=row[0],
                user_id=row[1],
                memory_type=row[2],
                value=value,
                metadata=metadata,
                created_at=datetime.fromisoformat(row[5]),
            )
        except (AttributeError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Persisted memory record is malformed.") from exc

    @staticmethod
    def _dump(value: Any, field: str) -> str:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Memory {field} must be JSON-serializable.") from exc

    @staticmethod
    def _reject_constant(value: str) -> None:
        raise ValueError(f"Invalid JSON constant: {value}.")
