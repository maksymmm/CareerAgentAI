from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from threading import Event, Lock, Thread
import time

import pytest

from career_agent_ai.application.communication import (
    CommunicationMessage,
    DeliveryEvidenceState,
    MessageDirection,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_sandbox_communication_provider import (
    SandboxCommunicationOutcome,
    SQLiteSandboxCommunicationProvider,
)


_OPERATION_ID = "send:concurrent-timeout"
_MESSAGE = CommunicationMessage(
    message_id="message-concurrent-timeout",
    thread_id="thread-concurrent-timeout",
    sender="candidate@example.test",
    recipient="recruiter@example.test",
    subject="Sandbox opportunity",
    body="This message remains inside the SQLite sandbox.",
    direction=MessageDirection.DRAFT,
    created_at=datetime(2026, 11, 26, tzinfo=timezone.utc),
)


class _ObservedProvider(SQLiteSandboxCommunicationProvider):
    def __init__(self, database, plan, *, before_begin=None):
        self._before_begin = before_begin
        super().__init__(database, plan)

    def _begin(self):
        if self._before_begin is not None:
            self._before_begin()
        super()._begin()


def test_concurrent_send_waits_for_committed_timeout_effect_and_replays_after_restart(
    tmp_path,
):
    """A same-key duplicate observes one committed delivery across concurrency and restart."""
    path = str(tmp_path / "communication-concurrent-timeout.sqlite")
    bootstrap = SQLiteDatabase(path)
    try:
        SQLiteSandboxCommunicationProvider(bootstrap).draft(_MESSAGE)
    finally:
        bootstrap.close()

    first_ready = Event()
    second_ready = Event()
    start_first = Event()
    start_second = Event()
    first_plan_entered = Event()
    second_begin_attempted = Event()
    release_first = Event()
    plan_lock = Lock()
    result_lock = Lock()
    plan_calls: list[tuple[str, int]] = []
    results: dict[str, CommunicationMessage] = {}
    errors: dict[str, BaseException] = {}

    def outcome_plan(operation_id: str, attempt_no: int) -> SandboxCommunicationOutcome:
        with plan_lock:
            plan_calls.append((operation_id, attempt_no))
        if attempt_no == 1:
            first_plan_entered.set()
            if not release_first.wait(timeout=10):
                raise AssertionError("The first provider transaction was not released.")
            return SandboxCommunicationOutcome.TIMEOUT_AFTER_EFFECT
        return SandboxCommunicationOutcome.SUCCESS

    def worker(
        name: str,
        ready: Event,
        start: Event,
        *,
        before_begin=None,
    ) -> None:
        database = None
        try:
            database = SQLiteDatabase(path)
            provider = _ObservedProvider(
                database,
                outcome_plan,
                before_begin=before_begin,
            )
            ready.set()
            if not start.wait(timeout=10):
                raise AssertionError(f"{name} was not released to start.")
            delivered = provider.send(_OPERATION_ID, _MESSAGE)
            with result_lock:
                results[name] = delivered
        except BaseException as exc:
            with result_lock:
                errors[name] = exc
        finally:
            if database is not None:
                database.close()

    first_thread = Thread(
        target=worker,
        name="communication-first-writer",
        args=("first", first_ready, start_first),
    )
    second_thread = Thread(
        target=worker,
        name="communication-duplicate-writer",
        args=("second", second_ready, start_second),
        kwargs={"before_begin": second_begin_attempted.set},
    )
    first_thread.start()
    second_thread.start()
    try:
        assert first_ready.wait(timeout=10)
        assert second_ready.wait(timeout=10)

        start_first.set()
        assert first_plan_entered.wait(timeout=10)

        # The second independent connection reaches BEGIN IMMEDIATE while the first
        # transaction is still held inside its outcome plan.
        start_second.set()
        assert second_begin_attempted.wait(timeout=10)
        time.sleep(0.05)
    finally:
        release_first.set()
        start_first.set()
        start_second.set()
        first_thread.join(timeout=15)
        second_thread.join(timeout=15)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert isinstance(errors.get("first"), TimeoutError)
    assert "second" not in errors
    assert results == {"second": replace(_MESSAGE, direction=MessageDirection.OUTBOUND)}
    assert plan_calls == [(_OPERATION_ID, 1)]

    restarted = SQLiteDatabase(path)

    def unexpected_plan(_operation_id: str, _attempt_no: int):
        raise AssertionError("A durable delivery replay must not invoke the provider plan.")

    provider = SQLiteSandboxCommunicationProvider(restarted, unexpected_plan)
    try:
        evidence = provider.verify_delivery(_OPERATION_ID)
        assert evidence.state is DeliveryEvidenceState.PRESENT
        assert evidence.message == replace(_MESSAGE, direction=MessageDirection.OUTBOUND)

        replayed = provider.send(_OPERATION_ID, _MESSAGE)
        assert replayed == evidence.message
        assert provider.delivery_count(_OPERATION_ID) == 1

        attempts = restarted.connection.execute(
            "SELECT operation_id, attempt_no, outcome "
            "FROM sandbox_communication_attempts ORDER BY attempt_no"
        ).fetchall()
        delivery = restarted.connection.execute(
            "SELECT message_id, action, replays "
            "FROM sandbox_communication_deliveries WHERE operation_id = ?",
            (_OPERATION_ID,),
        ).fetchone()
        messages = restarted.connection.execute(
            "SELECT message_id, direction FROM sandbox_communication_messages"
        ).fetchall()

        assert attempts == [
            (_OPERATION_ID, 1, SandboxCommunicationOutcome.TIMEOUT_AFTER_EFFECT.value)
        ]
        assert delivery == (_MESSAGE.message_id, "send", 2)
        assert messages == [(_MESSAGE.message_id, MessageDirection.OUTBOUND.value)]
    finally:
        restarted.close()
