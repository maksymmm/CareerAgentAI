from __future__ import annotations

import sqlite3

import pytest

from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


def _create_records_database(path) -> None:
    database = SQLiteDatabase(str(path))
    database.connection.execute(
        "CREATE TABLE records (value TEXT NOT NULL)"
    )
    database.connection.commit()
    database.close()


def test_open_read_only_blocks_primary_and_temporary_database_writes(tmp_path):
    path = tmp_path / "primary.sqlite"
    _create_records_database(path)

    database = SQLiteDatabase.open_read_only(str(path))
    try:
        connection = database.connection
        assert connection.execute("PRAGMA query_only").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM records").fetchone() == (0,)

        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("INSERT INTO records(value) VALUES ('blocked')")

        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("CREATE TEMP TABLE temporary_records (value TEXT)")
    finally:
        database.close()


def test_open_read_only_blocks_writes_to_attached_databases(tmp_path):
    primary_path = tmp_path / "primary.sqlite"
    attached_path = tmp_path / "attached.sqlite"
    _create_records_database(primary_path)
    _create_records_database(attached_path)

    database = SQLiteDatabase.open_read_only(str(primary_path))
    try:
        connection = database.connection
        connection.execute("ATTACH DATABASE ? AS auxiliary", (str(attached_path),))
        assert connection.execute(
            "SELECT COUNT(*) FROM auxiliary.records"
        ).fetchone() == (0,)

        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute(
                "INSERT INTO auxiliary.records(value) VALUES ('blocked')"
            )
    finally:
        database.close()

    attached = SQLiteDatabase(str(attached_path))
    try:
        assert attached.connection.execute(
            "SELECT COUNT(*) FROM records"
        ).fetchone() == (0,)
    finally:
        attached.close()


def test_open_read_only_does_not_create_missing_database(tmp_path):
    path = tmp_path / "missing.sqlite"

    with pytest.raises(sqlite3.OperationalError):
        SQLiteDatabase.open_read_only(str(path))

    assert not path.exists()


def test_open_read_only_rejects_memory_database():
    with pytest.raises(ValueError, match="durable database path"):
        SQLiteDatabase.open_read_only(":memory:")
