from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import json
import sqlite3

from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopPhase,
    CareerLoopRequest,
    CareerLoopState,
    HumanActionEvent,
    HumanActionKind,
)
from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
from career_agent_ai.application.storage.sqlite_career_loop_repository import (
    SQLiteCareerLoopRepository,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


OWNER = "read-only-prompt-candidate"
FOREIGN_OWNER = "different-candidate"
TOKEN = "read-only-prompt-token"
RUN_ID = "read-only-prompt-run"
NOW = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)
APPROVAL_PATH = f"/v1/candidate/runs/{RUN_ID}/approval"


def _seed_pending_prompt(path, *, owner: str = OWNER) -> None:
    """Persist one synthetic interview approval without initializing provider schemas."""
    database = SQLiteDatabase(str(path))
    try:
        state = CareerLoopState(
            run_id=RUN_ID,
            request=CareerLoopRequest(
                user_id=owner,
                keyword="logistics",
                candidate_profile="Synthetic logistics candidate.",
                schedule_event_id="read-only-prompt-event",
            ),
            phase=CareerLoopPhase.INTERVIEW_APPROVAL,
            application_id="read-only-prompt-application",
            pending_human_action=HumanActionEvent(
                kind=HumanActionKind.APPROVE_INTERVIEW,
                title="Approve interview calendar response",
                details={
                    "event_id": "read-only-prompt-event",
                    "employer": "Sandbox Logistics",
                    "event_type": "interview",
                    "location": "Karlsruhe",
                    "local_date": "2026-12-02",
                    "local_start_time": "10:00:00",
                    "local_end_date": "2026-12-02",
                    "local_end_time": "11:00:00",
                    "timezone": "Europe/Berlin",
                    "utc_offset": "+01:00",
                    "end_utc_offset": "+01:00",
                    "status": "proposed",
                },
            ),
            created_at=NOW,
            updated_at=NOW,
        )
        SQLiteCareerLoopRepository(database).save(state)
    finally:
        database.close()


def _runtime(path) -> RuntimeCandidateApp:
    """Compose the authenticated synthetic candidate runtime."""
    return RuntimeCandidateApp(
        database_path=str(path),
        jobs=(),
        resolve_bearer=lambda token: OWNER if token == TOKEN else None,
    )


def _request(app, path: str = APPROVAL_PATH):
    """Call one authenticated WSGI route and decode its JSON response."""
    statuses = []
    response = b"".join(
        app(
            {
                "REQUEST_METHOD": "GET",
                "PATH_INFO": path,
                "QUERY_STRING": "",
                "CONTENT_TYPE": "application/json",
                "CONTENT_LENGTH": "0",
                "HTTP_AUTHORIZATION": "Bearer " + TOKEN,
                "wsgi.input": BytesIO(b""),
            },
            lambda status, headers: statuses.append((status, headers)),
        )
    )
    return statuses[0], json.loads(response)


def _table_names(path) -> tuple[str, ...]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
    finally:
        connection.close()


def test_candidate_approval_prompt_get_is_read_only_and_does_not_initialize_providers(
    tmp_path,
):
    path = tmp_path / "candidate-approval-prompt.sqlite"
    _seed_pending_prompt(path)
    original_bytes = path.read_bytes()
    original_tables = _table_names(path)
    assert original_tables == ("autonomous_career_loops",)

    with _runtime(path) as runtime:
        (status, headers), prompt = _request(runtime)

    assert status == "200 OK"
    assert ("Cache-Control", "no-store") in headers
    assert prompt["schema_version"] == 1
    assert prompt["run_id"] == RUN_ID
    assert prompt["state_version"] == 1
    assert prompt["action_kind"] == HumanActionKind.APPROVE_INTERVIEW.value
    assert prompt["details"]["event_id"] == "read-only-prompt-event"
    assert "provider_event_id" not in prompt["details"]
    assert _table_names(path) == original_tables
    assert path.read_bytes() == original_bytes


def test_candidate_approval_prompt_get_does_not_create_missing_storage(tmp_path):
    path = tmp_path / "missing-candidate-approval.sqlite"
    assert not path.exists()

    with _runtime(path) as runtime:
        (status, _), body = _request(runtime)

    assert status == "500 Internal Server Error"
    assert body == {"error": "internal_error"}
    assert not path.exists()


def test_foreign_malformed_candidate_prompt_is_rejected_before_payload_decode(tmp_path):
    path = tmp_path / "foreign-candidate-approval.sqlite"
    _seed_pending_prompt(path, owner=FOREIGN_OWNER)
    database = SQLiteDatabase(str(path))
    try:
        database.connection.execute(
            "UPDATE autonomous_career_loops SET payload_json = ? WHERE run_id = ?",
            ("not-json", RUN_ID),
        )
        database.connection.commit()
    finally:
        database.close()
    original_bytes = path.read_bytes()

    with _runtime(path) as runtime:
        (status, _), body = _request(runtime)

    assert status == "404 Not Found"
    assert body == {"error": "not_found"}
    assert path.read_bytes() == original_bytes


def test_owned_malformed_candidate_prompt_is_sanitized_without_storage_mutation(tmp_path):
    path = tmp_path / "malformed-candidate-approval.sqlite"
    _seed_pending_prompt(path)
    database = SQLiteDatabase(str(path))
    try:
        database.connection.execute(
            "UPDATE autonomous_career_loops SET payload_json = ? WHERE run_id = ?",
            ("not-json", RUN_ID),
        )
        database.connection.commit()
    finally:
        database.close()
    original_bytes = path.read_bytes()

    with _runtime(path) as runtime:
        (status, headers), body = _request(runtime)

    assert status == "500 Internal Server Error"
    assert ("Cache-Control", "no-store") in headers
    assert body == {"error": "internal_error"}
    assert path.read_bytes() == original_bytes
