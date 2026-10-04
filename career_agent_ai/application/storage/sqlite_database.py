from __future__ import annotations

import sqlite3

from career_agent_ai.application.storage.database import Database


class SQLiteDatabase(Database):
    def __init__(self, path: str = ":memory:") -> None:
        self._path = path
        self._connection = sqlite3.connect(path, uri=True)
        self._is_memory = not self._connection.execute("PRAGMA database_list").fetchone()[2]

    @property
    def is_memory(self) -> bool:
        """Return SQLite's actual backing type without reinterpreting URI tokens."""
        return self._is_memory

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    @property
    def path(self) -> str:
        """Return the configured SQLite database path."""
        return self._path

    def close(self) -> None:
        self._connection.close()
