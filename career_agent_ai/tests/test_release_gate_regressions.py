from __future__ import annotations

import pytest

from career_agent_ai.application.api import openapi_document
from career_agent_ai.application.communication.models import (
    CommunicationMessage,
    MessageDirection,
)
from career_agent_ai.application.runtime import RuntimeConfig


@pytest.mark.parametrize(
    "database_path",
    [
        ":memory:",
        "file::memory:",
        "FILE::MEMORY:",
        "file:career?mode=memory&cache=shared",
        "file:career?cache=shared&mode=memory",
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
