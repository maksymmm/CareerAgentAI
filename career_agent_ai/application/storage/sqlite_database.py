from __future__ import annotations

import sqlite3

from career_agent_ai.application.storage.database import Database


class SQLiteDatabase(Database):
    def __init__(self, path: str = ":memory:") -> None:
        self._path = path
        self._connection = sqlite3.connect(path)

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    @property
    def path(self) -> str:
        """Return the configured SQLite database path."""
        return self._path

    def close(self) -> None:
        self._connection.close()