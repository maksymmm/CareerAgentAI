from __future__ import annotations

import pytest

from career_agent_ai.application.api import openapi_document
from career_agent_ai.application.communication.models import (
    CommunicationMessage,
    MessageDirection,
)
from career_agent_ai.application.runtime import RuntimeConfig
from career_agent_ai.application.storage.sqlite_career_loop_repository import (
    SQLiteCareerLoopRepository,
)
from career_agent_ai.application.storage.sqlite_career_run_repository import (
    SQLiteCareerRunRepository,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


@pytest.mark.parametrize(
    "uri, expected_memory",
    [
        (":memory:", True),
        ("FILE::MEMORY:", False),
        ("file:case.db?mode=memory#fragment", True),
        ("file:case.db?MODE=MEMORY", False),
        ("file:case.db?MODE=memory", False),
        ("file::MEMORY:", False),
        ("file::memory:extra", False),
        ("file::memory:", True),
        ("file:%3Amemory%3A", True),
        ("file:case.db?%6dode=memory", True),
        ("file:case.db?mode=rwc&mode=memory", True),
        ("file:case.db?mode=memory&mode=rwc", False),
    ],
)
def test_sqlite_uri_detection_matches_actual_database_and_lease_renewal(
    tmp_path, monkeypatch, uri, expected_memory
):
    monkeypatch.chdir(tmp_path)
    database = SQLiteDatabase(uri)
    actual_path = database.connection.execute("PRAGMA database_list").fetchone()[2]
    assert (actual_path == "") is expected_memory
    assert database.is_memory is expected_memory
    run_repository = SQLiteCareerRunRepository(database)
    assert run_repository.supports_background_lease_renewal is not expected_memory
    database.close()


@pytest.mark.parametrize(
    "database_path",
    [
        ":memory:",
        "file::memory:",
        "file:career?mode=memory&cache=shared",
        "file:career?cache=shared&mode=memory",
        "file:%3Amemory%3A",
        "file:career?mode=memory#fragment",
        "file:career?%6dode=%6demory",
        "file:career?mode=rwc&mode=memory",
    ],
)
def test_production_runtime_rejects_all_sqlite_memory_paths(database_path):
    with pytest.raises(ValueError, match="durable"):
        RuntimeConfig.from_env(
            {
                "CAREER_AGENT_ENV": "production",
                "CAREER_AGENT_DB_PATH": database_path,
            }
        )


@pytest.mark.parametrize(
    "subject",
    [
        "Approved\r\nBcc: attacker@example.com",
        "Approved\nBcc: attacker@example.com",
        "Approved\rBcc: attacker@example.com",
        "Approved\tBcc: attacker@example.com",
        "Approved\x7fBcc: attacker@example.com",
    ],
)
def test_communication_subject_rejects_header_controls(subject):
    with pytest.raises(ValueError, match="header control"):
        CommunicationMessage(
            message_id="message-1",
            thread_id="thread-1",
            sender="candidate@example.test",
            recipient="recruiter@example.test",
            subject=subject,
            body="Body text may contain\nline breaks.",
            direction=MessageDirection.DRAFT,
        )


def test_communication_body_still_accepts_plain_text_line_breaks():
    value = CommunicationMessage(
        message_id="message-1",
        thread_id="thread-1",
        sender="candidate@example.test",
        recipient="recruiter@example.test",
        subject="Approved",
        body="First line\r\nSecond line",
        direction=MessageDirection.DRAFT,
    )

    assert value.body == "First line\nSecond line"


def test_operational_openapi_documents_sanitized_500_response():
    document = openapi_document()
    response = document["paths"]["/v1/operational/issues"]["get"]["responses"]["500"]

    assert response["description"] == "Sanitized internal operational error"
    schema = response["content"]["application/json"]["schema"]
    assert schema["required"] == ["error"]
    assert schema["properties"]["error"]["const"] == "internal_error"
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize(
    "database_path",
    [
        "file::memory:",
        "file:career?mode=memory",
        "file:career?cache=shared&mode=memory",
        "file:%3Amemory%3A",
        "file:career?mode=memory#fragment",
        "file:career?%6dode=%6demory",
        "file:career?mode=rwc&mode=memory",
    ],
)
def test_private_sqlite_memory_uri_skips_independent_heartbeat_connection(database_path):
    database = SQLiteDatabase(database_path)
    repository = SQLiteCareerLoopRepository(database)

    repository.renew_execution(
        "not-materialized-in-private-memory",
        "worker-1",
        lease_seconds=60,
    )

    database.close()


@pytest.mark.parametrize("database_path", [
    "FILE::MEMORY:", "file:career?MODE=MEMORY",
    "file:career?mode=memory&mode=rwc", "file:///career.sqlite",
])
def test_production_config_preserves_durable_uri_semantics(tmp_path, monkeypatch, database_path):
    monkeypatch.chdir(tmp_path)
    if database_path == "file:///career.sqlite":
        database_path = (tmp_path / "career.sqlite").as_uri()
    config = RuntimeConfig.from_env({
        "CAREER_AGENT_ENV": "production", "CAREER_AGENT_DB_PATH": database_path,
    })
    assert config.database_path == database_path
    database = SQLiteDatabase(config.database_path)
    assert database.is_memory is False
    database.close()


def test_production_config_rejects_invalid_sqlite_uri(tmp_path):
    with pytest.raises(ValueError, match="openable SQLite URI"):
        RuntimeConfig.from_env({
            "CAREER_AGENT_ENV": "production",
            "CAREER_AGENT_DB_PATH": f"file:{tmp_path}/career?mode=invalid",
        })
