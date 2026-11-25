from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from threading import Event, Lock

import pytest

from career_agent_ai.application.communication import (
    CommunicationMessage,
    MessageDirection,
    PreDeliveryCommunicationError,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_sandbox_communication_provider import (
    SQLiteSandboxCommunicationProvider,
)


def _message() -> CommunicationMessage:
    return CommunicationMessage(
        message_id="message-replay-integrity",
        thread_id="thread-replay-integrity",
        sender="candidate@example.test",
        recipient="recruiter@example.test",
        subject="Sandbox opportunity",
        body="The persisted delivery must remain bound to this exact intent.",
        direction=MessageDirection.DRAFT,
        created_at=datetime(2026, 11, 25, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("corruption", ["body", "direction", "action", "message_id"])
def test_replay_fails_closed_on_corrupted_delivery_evidence(tmp_path, corruption):
    path = str(tmp_path / "communication-replay.sqlite")
    database = SQLiteDatabase(path)
    provider = SQLiteSandboxCommunicationProvider(database)
    message = _message()
    provider.draft(message)

    delivered = provider.send("send:replay-integrity", message)
    assert delivered.direction == MessageDirection.OUTBOUND
    assert provider.delivery_count("send:replay-integrity") == 1

    if corruption == "body":
        database.connection.execute(
            "UPDATE sandbox_communication_messages SET body = ? WHERE message_id = ?",
            ("tampered body", message.message_id),
        )
    elif corruption == "direction":
        database.connection.execute(
            "UPDATE sandbox_communication_messages SET direction = ? WHERE message_id = ?",
            (MessageDirection.DRAFT.value, message.message_id),
        )
    elif corruption == "action":
        database.connection.execute(
            "UPDATE sandbox_communication_deliveries SET action = ? WHERE operation_id = ?",
            ("reply", "send:replay-integrity"),
        )
    else:
        database.connection.execute(
            "UPDATE sandbox_communication_deliveries SET message_id = ? WHERE operation_id = ?",
            ("missing-message", "send:replay-integrity"),
        )
    database.connection.commit()
    database.close()

    restarted_database = SQLiteDatabase(path)
    restarted_provider = SQLiteSandboxCommunicationProvider(restarted_database)
    with pytest.raises(PreDeliveryCommunicationError, match="delivery evidence"):
        restarted_provider.send("send:replay-integrity", message)

    row = restarted_database.connection.execute(
        "SELECT replays FROM sandbox_communication_deliveries WHERE operation_id = ?",
        ("send:replay-integrity",),
    ).fetchone()
    attempts = restarted_database.connection.execute(
        "SELECT COUNT(*) FROM sandbox_communication_attempts WHERE operation_id = ?",
        ("send:replay-integrity",),
    ).fetchone()[0]
    assert row == (0,)
    assert attempts == 1
    assert restarted_provider.delivery_count("send:replay-integrity") == 1
    restarted_database.close()



def test_concurrent_duplicate_delivery_replays_one_verified_effect(tmp_path):
    path = str(tmp_path / "communication-concurrent-replay.sqlite")
    setup_database = SQLiteDatabase(path)
    setup_provider = SQLiteSandboxCommunicationProvider(setup_database)
    message = _message()
    setup_provider.draft(message)
    setup_database.close()

    start = Event()
    ready = (Event(), Event())
    calling = (Event(), Event())
    plan_entered = Event()
    release_plan = Event()
    plan_lock = Lock()
    plan_calls: list[tuple[str, int]] = []

    def plan(operation_id: str, attempt_no: int) -> SandboxCommunicationOutcome:
        with plan_lock:
            plan_calls.append((operation_id, attempt_no))
        plan_entered.set()
        if not release_plan.wait(timeout=5):
            raise TimeoutError("Test did not release the sandbox provider plan.")
        return SandboxCommunicationOutcome.SUCCESS

    def deliver(index: int):
        database = SQLiteDatabase(path)
        provider = SQLiteSandboxCommunicationProvider(database, plan)
        ready[index].set()
        if not start.wait(timeout=5):
            database.close()
            raise TimeoutError("Concurrent delivery start was not released.")
        calling[index].set()
        try:
            return provider.send("send:concurrent-replay", message)
        finally:
            database.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(deliver, 0)
        second = executor.submit(deliver, 1)
        assert ready[0].wait(timeout=5)
        assert ready[1].wait(timeout=5)
        start.set()
        assert calling[0].wait(timeout=5)
        assert calling[1].wait(timeout=5)
        assert plan_entered.wait(timeout=5)

        # One provider owns the SQLite writer transaction while the duplicate
        # waits. Neither request may finish before the first durable commit.
        with pytest.raises(FutureTimeoutError):
            first.result(timeout=0.05)
        with pytest.raises(FutureTimeoutError):
            second.result(timeout=0.05)
        release_plan.set()

        first_result = first.result(timeout=5)
        second_result = second.result(timeout=5)

    assert first_result == second_result
    assert first_result.direction == MessageDirection.OUTBOUND
    assert plan_calls == [("send:concurrent-replay", 1)]

    verification_database = SQLiteDatabase(path)
    assert verification_database.connection.execute(
        "SELECT COUNT(*) FROM sandbox_communication_attempts "
        "WHERE operation_id = ?",
        ("send:concurrent-replay",),
    ).fetchone() == (1,)
    assert verification_database.connection.execute(
        "SELECT replays FROM sandbox_communication_deliveries "
        "WHERE operation_id = ?",
        ("send:concurrent-replay",),
    ).fetchone() == (1,)
    assert verification_database.connection.execute(
        "SELECT COUNT(*) FROM sandbox_communication_deliveries "
        "WHERE operation_id = ?",
        ("send:concurrent-replay",),
    ).fetchone() == (1,)
    verification_database.close()
