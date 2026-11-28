from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlsplit

from career_agent_ai.application.storage.database import Database



def _read_only_filesystem_path(path: str) -> Path:
    """Resolve the local file behind a SQLite path or file URI for safe preflight."""
    if path.startswith("file:"):
        parsed = urlsplit(path)
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            raise ValueError("Read-only SQLite requires a local file path.")
        if any(
            key.casefold() == "mode" and value.casefold() == "memory"
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ):
            raise ValueError("Read-only SQLite requires a durable database path.")
        filename = unquote(parsed.path)
    else:
        filename = path
    if not filename or filename == ":memory:":
        raise ValueError("Read-only SQLite requires a durable database path.")
    return Path(filename).absolute()


def _reject_wal_mode(database_path: Path) -> None:
    """Reject WAL files before SQLite can create or update shared-memory sidecars."""
    if not database_path.is_file():
        return
    try:
        with database_path.open("rb") as stream:
            header = stream.read(20)
    except OSError:
        # Let sqlite3.connect preserve its normal, non-creating open error.
        return
    if (
        header[:16] == b"SQLite format 3\x00"
        and len(header) >= 20
        and 2 in header[18:20]
    ):
        raise ValueError(
            "Read-only SQLite does not support WAL-mode databases because opening "
            "them can create or update -wal/-shm sidecar files."
        )


class SQLiteDatabase(Database):
    def __init__(self, path: str = ":memory:") -> None:
        self._path = path
        self._connection = sqlite3.connect(path, uri=True)
        self._is_memory = not self._connection.execute("PRAGMA database_list").fetchone()[2]

    @classmethod
    def open_read_only(cls, path: str) -> "SQLiteDatabase":
        """Open an existing database read-only, including attached/temp databases."""
        if path == ":memory:":
            raise ValueError("Read-only SQLite requires a durable database path.")
        database_path = _read_only_filesystem_path(path)
        _reject_wal_mode(database_path)
        if path.startswith("file:"):
            base, separator, fragment = path.partition("#")
            delimiter = "&" if "?" in base else "?"
            uri = f"{base}{delimiter}mode=ro"
            if separator:
                uri = f"{uri}#{fragment}"
        else:
            uri = f"file:{quote(str(database_path), safe='/')}?mode=ro"

        database = cls.__new__(cls)
        database._path = path
        connection = sqlite3.connect(uri, uri=True)
        try:
            # mode=ro protects the primary file; query_only also prevents writes
            # to temporary or attached databases through this connection.
            connection.execute("PRAGMA query_only = ON")
        except Exception:
            connection.close()
            raise
        database._connection = connection
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
