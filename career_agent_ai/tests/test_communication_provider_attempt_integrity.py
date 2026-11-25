from __future__ import annotations

from datetime import datetime, timezone

import pytest

from career_agent_ai.application.communication import (
    CommunicationMessage,
    MessageDirection,
    PreDeliveryCommunicationError,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_sandbox_communication_provider import (
    DeliveryEvidenceState,
    SandboxCommunicationOutcome,
    SQLiteSandboxCommunicationProvider,
)


def _message() -> CommunicationMessage:
    return CommunicationMessage(
        message_id="message-attempt-integrity",
        thread_id="thread-attempt-integrity",
        sender="candidate@example.test",
        recipient="recruiter@example.test",
        subject="Sandbox opportunity",
        body="This message remains inside the local sandbox.",
        direction=MessageDirection.DRAFT,
        created_at=datetime(2026, 11, 25, tzinfo=timezone.utc),
    )


def _attempt_rows(database: SQLiteDatabase, operation_id: str):
    return database.connection.execute(
        "SELECT attempt_no, outcome FROM sandbox_communication_attempts "
        "WHERE operation_id = ? ORDER BY attempt_no",
        (operation_id,),
    ).fetchall()


@pytest.mark.parametrize(
    "corruption",
    ["intent_without_attempts", "orphan_attempts", "orphan_delivery"],
)
def test_provider_does_not_reconstruct_incomplete_communication_ledger(
    tmp_path, corruption
):
    path = str(tmp_path / f"{corruption}.sqlite")
    database = SQLiteDatabase(path)
    provider = SQLiteSandboxCommunicationProvider(database)
    message = _message()
    provider.draft(message)
    operation_id = f"send:{corruption}"
    fingerprint = provider._fingerprint("send", message)

    if corruption == "intent_without_attempts":
        database.connection.execute(
            "INSERT INTO sandbox_communication_intents VALUES (?, ?)",
            (operation_id, fingerprint),
        )
    elif corruption == "orphan_attempts":
        database.connection.execute(
            "INSERT INTO sandbox_communication_attempts VALUES (?, ?, ?)",
            (operation_id, 1, SandboxCommunicationOutcome.NO_EFFECT.value),
        )
    else:
        database.connection.execute(
            "INSERT INTO sandbox_communication_deliveries "
            "(operation_id, fingerprint, message_id, action) VALUES (?, ?, ?, ?)",
            (operation_id, fingerprint, message.message_id, "send"),
        )
    database.connection.commit()
    attempts_before = _attempt_rows(database, operation_id)
    deliveries_before = database.connection.execute(
        "SELECT operation_id, fingerprint, message_id, action, replays "
        "FROM sandbox_communication_deliveries WHERE operation_id = ?",
        (operation_id,),
    ).fetchall()
    database.close()

    restarted = SQLiteDatabase(path)
    plan_calls = []

    def must_not_run(key, attempt):
        plan_calls.append((key, attempt))
        return SandboxCommunicationOutcome.SUCCESS

    recovered = SQLiteSandboxCommunicationProvider(restarted, must_not_run)
    with pytest.raises(PreDeliveryCommunicationError, match="ledger|attempt history"):
        recovered.send(operation_id, message)

    assert plan_calls == []
    assert _attempt_rows(restarted, operation_id) == attempts_before
    assert restarted.connection.execute(
        "SELECT operation_id, fingerprint, message_id, action, replays "
        "FROM sandbox_communication_deliveries WHERE operation_id = ?",
        (operation_id,),
    ).fetchall() == deliveries_before
    assert recovered.delivery_count(operation_id) == len(deliveries_before)
    assert recovered.read(message.message_id).direction == MessageDirection.DRAFT
    restarted.close()


@pytest.mark.parametrize(
    "corruption",
    ["gapped_attempt", "unknown_outcome", "effect_without_delivery"],
)
def test_provider_fails_closed_before_new_attempt_when_history_is_not_retryable(
    tmp_path, corruption
):
    path = str(tmp_path / f"{corruption}.sqlite")
    database = SQLiteDatabase(path)
    message = _message()
    operation_id = f"send:{corruption}"

    if corruption == "effect_without_delivery":
        provider = SQLiteSandboxCommunicationProvider(
            database,
            lambda _key, _attempt: SandboxCommunicationOutcome.SUCCESS,
        )
        provider.draft(message)
        assert provider.send(operation_id, message).direction == MessageDirection.OUTBOUND
        database.connection.execute(
            "DELETE FROM sandbox_communication_deliveries WHERE operation_id = ?",
            (operation_id,),
        )
    else:
        provider = SQLiteSandboxCommunicationProvider(
            database,
            lambda _key, _attempt: SandboxCommunicationOutcome.NO_EFFECT,
        )
        provider.draft(message)
        with pytest.raises(PreDeliveryCommunicationError, match="no delivery"):
            provider.send(operation_id, message)
        if corruption == "gapped_attempt":
            database.connection.execute(
                "UPDATE sandbox_communication_attempts SET attempt_no = 2 "
                "WHERE operation_id = ?",
                (operation_id,),
            )
        else:
            database.connection.execute(
                "UPDATE sandbox_communication_attempts SET outcome = ? "
                "WHERE operation_id = ?",
                ("provider_claimed_absent", operation_id),
            )
    database.connection.commit()
    attempts_before = _attempt_rows(database, operation_id)
    deliveries_before = database.connection.execute(
        "SELECT operation_id, fingerprint, message_id, action, replays "
        "FROM sandbox_communication_deliveries WHERE operation_id = ?",
        (operation_id,),
    ).fetchall()
    database.close()

    restarted = SQLiteDatabase(path)
    plan_calls = []

    def must_not_run(key, attempt):
        plan_calls.append((key, attempt))
        return SandboxCommunicationOutcome.SUCCESS

    recovered = SQLiteSandboxCommunicationProvider(restarted, must_not_run)
    with pytest.raises(PreDeliveryCommunicationError, match="attempt history|retry"):
        recovered.send(operation_id, message)

    assert plan_calls == []
    assert _attempt_rows(restarted, operation_id) == attempts_before
    assert restarted.connection.execute(
        "SELECT operation_id, fingerprint, message_id, action, replays "
        "FROM sandbox_communication_deliveries WHERE operation_id = ?",
        (operation_id,),
    ).fetchall() == deliveries_before
    assert recovered.delivery_count(operation_id) == len(deliveries_before)
    restarted.close()


@pytest.mark.parametrize(
    ("first_outcome", "expected_error"),
    [
        (SandboxCommunicationOutcome.NO_EFFECT, PreDeliveryCommunicationError),
        (SandboxCommunicationOutcome.INTERRUPTED_BEFORE_EFFECT, ConnectionError),
    ],
)
def test_explicit_no_effect_history_allows_same_intent_retry_after_restart(
    tmp_path, first_outcome, expected_error
):
    path = str(tmp_path / f"{first_outcome.value}.sqlite")
    operation_id = "send:valid-no-effect-retry"
    message = _message()
    first_database = SQLiteDatabase(path)
    first_provider = SQLiteSandboxCommunicationProvider(
        first_database, lambda _key, _attempt: first_outcome
    )
    first_provider.draft(message)
    with pytest.raises(expected_error):
        first_provider.send(operation_id, message)
    assert _attempt_rows(first_database, operation_id) == [(1, first_outcome.value)]
    first_database.close()

    second_database = SQLiteDatabase(path)
    plan_calls = []

    def succeed_second_attempt(key, attempt):
        plan_calls.append((key, attempt))
        return SandboxCommunicationOutcome.SUCCESS

    second_provider = SQLiteSandboxCommunicationProvider(
        second_database, succeed_second_attempt
    )
    delivered = second_provider.send(operation_id, message)

    assert delivered.direction == MessageDirection.OUTBOUND
    assert plan_calls == [(operation_id, 2)]
    assert _attempt_rows(second_database, operation_id) == [
        (1, first_outcome.value),
        (2, SandboxCommunicationOutcome.SUCCESS.value),
    ]
    assert second_provider.delivery_count(operation_id) == 1
    assert second_provider.verify_delivery(operation_id).state == DeliveryEvidenceState.PRESENT
    second_database.close()
