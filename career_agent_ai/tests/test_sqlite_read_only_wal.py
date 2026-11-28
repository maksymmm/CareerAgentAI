from __future__ import annotations

import pathlib
import sqlite3

import pytest

from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


def _create_wal_database(path):
    connection = sqlite3.connect(path)
    mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
    assert mode == "wal"
    connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
    connection.execute("INSERT INTO records(value) VALUES ('visible in WAL')")
    connection.commit()
    return connection


def _sidecar_paths(path):
    return (pathlib.Path(f"{path}-wal"), pathlib.Path(f"{path}-shm"))


def test_open_read_only_rejects_live_wal_without_changing_sidecars(tmp_path):
    import pathlib

    path = tmp_path / "live-wal.sqlite"
    writer = _create_wal_database(path)
    sidecars = _sidecar_paths(path)
    assert all(item.exists() for item in sidecars)
    before = {
        item: (item.read_bytes(), item.stat().st_mtime_ns)
        for item in (path, *sidecars)
    }

    with pytest.raises(ValueError, match="WAL-mode"):
        SQLiteDatabase.open_read_only(str(path))

    after = {
        item: (item.read_bytes(), item.stat().st_mtime_ns)
        for item in (path, *sidecars)
    }
    assert after == before
    assert writer.execute("SELECT value FROM records").fetchall() == [
        ("visible in WAL",)
    ]
    writer.close()


def test_open_read_only_rejects_wal_uri_before_touching_shared_memory(tmp_path):
    import pathlib

    path = tmp_path / "wal-uri.sqlite"
    writer = _create_wal_database(path)
    sidecars = _sidecar_paths(path)
    before = {
        item: (item.read_bytes(), item.stat().st_mtime_ns)
        for item in (path, *sidecars)
    }

    with pytest.raises(ValueError, match="WAL-mode"):
        SQLiteDatabase.open_read_only(f"file:{path}?mode=ro")

    after = {
        item: (item.read_bytes(), item.stat().st_mtime_ns)
        for item in (path, *sidecars)
    }
    assert after == before
    writer.close()


def test_open_read_only_rejects_closed_wal_without_creating_sidecars(tmp_path):
    import pathlib

    path = tmp_path / "closed-wal.sqlite"
    writer = _create_wal_database(path)
    writer.close()
    sidecars = _sidecar_paths(path)
    assert not any(item.exists() for item in sidecars)

    with pytest.raises(ValueError, match="WAL-mode"):
        SQLiteDatabase.open_read_only(str(path))

    assert path.exists()
    assert not any(item.exists() for item in sidecars)


def test_open_read_only_keeps_rollback_journal_database_readable(tmp_path):
    import pathlib

    path = tmp_path / "rollback.sqlite"
    writer = sqlite3.connect(path)
    mode = writer.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
    assert mode == "delete"
    writer.execute("CREATE TABLE records (value TEXT NOT NULL)")
    writer.execute("INSERT INTO records(value) VALUES ('stable snapshot')")
    writer.commit()
    writer.close()

    database = SQLiteDatabase.open_read_only(str(path))
    try:
        assert database.connection.execute("PRAGMA query_only").fetchone() == (1,)
        assert database.connection.execute(
            "SELECT value FROM records"
        ).fetchall() == [("stable snapshot",)]
    finally:
        database.close()

    assert not pathlib.Path(f"{path}-wal").exists()
    assert not pathlib.Path(f"{path}-shm").exists()


def test_open_read_only_rejects_memory_mode_file_uri():
    with pytest.raises(ValueError, match="durable database path"):
        SQLiteDatabase.open_read_only("file:shared-memory?mode=memory&cache=shared")
