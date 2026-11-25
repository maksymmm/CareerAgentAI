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
