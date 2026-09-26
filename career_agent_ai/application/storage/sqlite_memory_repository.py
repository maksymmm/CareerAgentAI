from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from career_agent_ai.application.memory.memory_record import MemoryRecord
from career_agent_ai.application.memory.memory_repository import MemoryRepository
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


class SQLiteMemoryRepository(MemoryRepository):
    """Restart-safe SQLite career memory with versioned JSON serialization."""

    SERIALIZATION_VERSION = 1

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database
        self._create_schema()

    def save(self, record: MemoryRecord) -> None:
        """Atomically insert or replace a validated JSON-safe record."""
        value_json = self._dump(record.value, "value")
        metadata_json = self._dump(dict(record.metadata), "metadata")
        self._database.connection.execute(
            """
            INSERT INTO career_memory (
                memory_key, user_id, memory_type, value_json, metadata_json,
                created_at, serialization_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(memory_key) DO UPDATE SET
                user_id = excluded.user_id,
                memory_type = excluded.memory_type,
                value_json = excluded.value_json,
                metadata_json = excluded.metadata_json,
                created_at = excluded.created_at,
                serialization_version = excluded.serialization_version
            """,
            (
                record.key,
                record.user_id,
                record.memory_type,
                value_json,
                metadata_json,
                record.created_at.isoformat(),
                self.SERIALIZATION_VERSION,
            ),
        )
        self._database.connection.commit()

    def get(self, key: str) -> MemoryRecord | None:
        """Load and validate one persisted record by key."""
        normalized = key.strip()
        if not normalized:
            raise ValueError("Memory key must not be empty.")
        row = self._database.connection.execute(
            """SELECT memory_key, user_id, memory_type, value_json, metadata_json,
                      created_at, serialization_version
               FROM career_memory WHERE memory_key = ?""",
            (normalized,),
        ).fetchone()
        return None if row is None else self._load(row)

    def find(
        self,
        *,
        user_id: str | None = None,
        memory_type: str | None = None,
    ) -> tuple[MemoryRecord, ...]:
        """Load validated records filtered by user and/or memory type."""
        if user_id is not None and not user_id.strip():
            raise ValueError("Memory user_id filter must not be empty.")
        if memory_type is not None and not memory_type.strip():
            raise ValueError("Memory type filter must not be empty.")
        clauses: list[str] = []
        parameters: list[str] = []
        if user_id is not None:
            clauses.append("user_id = ?")
            parameters.append(user_id)
        if memory_type is not None:
            clauses.append("memory_type = ?")
            parameters.append(memory_type)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._database.connection.execute(
            """SELECT memory_key, user_id, memory_type, value_json, metadata_json,
                      created_at, serialization_version
               FROM career_memory""" + where + " ORDER BY memory_key",
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
                memory_key TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                value_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                serialization_version INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_career_memory_user_type
               ON career_memory(user_id, memory_type)"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_career_memory_type
               ON career_memory(memory_type)"""
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
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Memory {field} must be JSON-serializable.") from exc

    @staticmethod
    def _reject_constant(value: str) -> None:
        raise ValueError(f"Invalid JSON constant: {value}.")
