from __future__ import annotations

from datetime import datetime, timezone

import pytest

from career_agent_ai.application.memory.memory_engine import MemoryEngine
from career_agent_ai.application.memory.memory_record import MemoryRecord
from career_agent_ai.application.memory.memory_state import MemoryState
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_memory_repository import SQLiteMemoryRepository


def make_engine(database: SQLiteDatabase) -> MemoryEngine:
    return MemoryEngine(SQLiteMemoryRepository(database))


def test_memory_survives_process_restart(tmp_path):
    path = str(tmp_path / "memory.sqlite")
    first_database = SQLiteDatabase(path)
    first = make_engine(first_database)
    record = MemoryRecord(
        key="profile:user-7",
        value={"roles": ["engineer"], "remote": True},
        metadata={"source": "candidate"},
        user_id="user-7",
        memory_type="profile",
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    first.save(record)
    first_database.close()

    restarted_database = SQLiteDatabase(path)
    restarted = make_engine(restarted_database)

    assert restarted.get(record.key) == record
    assert restarted.snapshot().get(record.key) == record.value


def test_retrieval_filters_by_user_and_memory_type():
    engine = make_engine(SQLiteDatabase())
    records = (
        MemoryRecord("a", "Python", user_id="u1", memory_type="skill"),
        MemoryRecord("b", "Rust", user_id="u1", memory_type="skill"),
        MemoryRecord("c", "Remote", user_id="u1", memory_type="preference"),
        MemoryRecord("d", "Go", user_id="u2", memory_type="skill"),
    )
    for record in reversed(records):
        engine.save(record)

    assert engine.find(user_id="u1", memory_type="skill") == records[:2]
    assert engine.find(user_id="u1") == records[:3]
    assert engine.find(memory_type="skill") == (records[0], records[1], records[3])
    assert engine.find(user_id="missing") == ()


def test_save_update_clear_and_default_engine_contract_are_compatible():
    engine = MemoryEngine()
    original = MemoryRecord("name", "Ada")
    replacement = MemoryRecord("name", "Grace")

    assert engine.save(original).success
    assert engine.state == MemoryState.READY
    assert engine.get("name") == original
    assert engine.update(replacement).success
    assert engine.state == MemoryState.UPDATED
    assert engine.snapshot().get("name") == "Grace"
    assert engine.clear().success
    assert engine.state == MemoryState.CLEARED
    assert engine.snapshot().size() == 0


@pytest.mark.parametrize(
    "column,value",
    [
        ("value_json", "not-json"),
        ("value_json", "NaN"),
        ("metadata_json", "[]"),
        ("created_at", "not-a-date"),
        ("serialization_version", 999),
        ("user_id", ""),
    ],
)
def test_malformed_persisted_state_is_rejected(column, value):
    database = SQLiteDatabase()
    repository = SQLiteMemoryRepository(database)
    repository.save(MemoryRecord("key", {"valid": True}))
    database.connection.execute(
        f"UPDATE career_memory SET {column} = ? WHERE memory_key = ?",
        (value, "key"),
    )
    database.connection.commit()

    with pytest.raises(ValueError, match="malformed"):
        repository.get("key")


def test_non_json_safe_values_and_metadata_are_rejected_without_writing():
    repository = SQLiteMemoryRepository(SQLiteDatabase())

    with pytest.raises(ValueError, match="value must be JSON-serializable"):
        repository.save(MemoryRecord("bad-value", object()))
    with pytest.raises(ValueError, match="metadata must be JSON-serializable"):
        repository.save(MemoryRecord("bad-metadata", "ok", metadata={"bad": object()}))
    with pytest.raises(ValueError, match="value must be JSON-serializable"):
        repository.save(MemoryRecord("nan", float("nan")))
    assert repository.find() == ()


@pytest.mark.parametrize(
    "kwargs",
    [{"key": ""}, {"user_id": " "}, {"memory_type": ""}],
)
def test_record_identifiers_are_validated(kwargs):
    values = {"key": "key", "value": "value", "user_id": "user", "memory_type": "type"}
    values.update(kwargs)

    with pytest.raises(ValueError):
        MemoryRecord(**values)


def test_naive_timestamp_and_invalid_filters_are_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        MemoryRecord("key", "value", created_at=datetime(2026, 1, 1))

    repository = SQLiteMemoryRepository(SQLiteDatabase())
    with pytest.raises(ValueError, match="key"):
        repository.get(" ")
    with pytest.raises(ValueError, match="user_id filter"):
        repository.find(user_id="")
    with pytest.raises(ValueError, match="type filter"):
        repository.find(memory_type=" ")


def test_upsert_is_durable_and_does_not_duplicate_records(tmp_path):
    path = str(tmp_path / "memory.sqlite")
    database = SQLiteDatabase(path)
    engine = make_engine(database)
    engine.save(MemoryRecord("goal", "developer", user_id="u1", memory_type="goal"))
    engine.update(MemoryRecord("goal", "staff developer", user_id="u1", memory_type="goal"))
    database.close()

    restarted = make_engine(SQLiteDatabase(path))
    assert restarted.get("goal").value == "staff developer"
    assert len(restarted.find(user_id="u1", memory_type="goal")) == 1
