from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import quote

from career_agent_ai.application.storage.database import Database


class SQLiteDatabase(Database):
    def __init__(self, path: str = ":memory:") -> None:
        self._path = path
        self._connection = sqlite3.connect(path, uri=True)
        self._is_memory = not self._connection.execute("PRAGMA database_list").fetchone()[2]

    @classmethod
    def open_read_only(cls, path: str) -> "SQLiteDatabase":
        """Open an existing SQLite database without permitting writes or creation."""
        if path == ":memory:":
            raise ValueError("Read-only SQLite requires a durable database path.")
        if path.startswith("file:"):
            base, separator, fragment = path.partition("#")
            delimiter = "&" if "?" in base else "?"
            uri = f"{base}{delimiter}mode=ro"
            if separator:
                uri = f"{uri}#{fragment}"
        else:
            absolute_path = Path(path).absolute()
            uri = f"file:{quote(str(absolute_path), safe='/')}?mode=ro"

        database = cls.__new__(cls)
        database._path = path
        database._connection = sqlite3.connect(uri, uri=True)
        database._is_memory = False
        return database

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
