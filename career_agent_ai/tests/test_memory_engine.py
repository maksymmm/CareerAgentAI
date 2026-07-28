from dataclasses import FrozenInstanceError

import pytest

from career_agent_ai.application.memory.memory_engine import MemoryEngine
from career_agent_ai.application.memory.memory_record import MemoryRecord
from career_agent_ai.application.memory.memory_state import MemoryState


def test_memory_engine_creation():
    engine = MemoryEngine()

    assert engine.state == MemoryState.EMPTY


def test_save_record():
    engine = MemoryEngine()

    result = engine.save(
        MemoryRecord(
            key="user",
            value="John",
        )
    )

    assert result.success is True
    assert engine.state == MemoryState.READY


def test_get_record():
    engine = MemoryEngine()

    record = MemoryRecord(
        key="city",
        value="Berlin",
    )

    engine.save(record)

    loaded = engine.get("city")

    assert loaded == record


def test_update_record():
    engine = MemoryEngine()

    engine.save(
        MemoryRecord(
            key="country",
            value="Germany",
        )
    )

    engine.update(
        MemoryRecord(
            key="country",
            value="Ukraine",
        )
    )

    assert engine.get("country").value == "Ukraine"
    assert engine.state == MemoryState.UPDATED


def test_snapshot():
    engine = MemoryEngine()

    engine.save(
        MemoryRecord(
            key="language",
            value="Python",
        )
    )

    snapshot = engine.snapshot()

    assert snapshot.get("language") == "Python"
    assert snapshot.contains("language")
    assert snapshot.size() == 1


def test_clear():
    engine = MemoryEngine()

    engine.save(
        MemoryRecord(
            key="x",
            value=1,
        )
    )

    engine.clear()

    assert engine.state == MemoryState.CLEARED
    assert engine.snapshot().size() == 0


def test_memory_record_is_immutable():
    record = MemoryRecord(
        key="a",
        value="b",
    )

    with pytest.raises(FrozenInstanceError):
        record.key = "c"