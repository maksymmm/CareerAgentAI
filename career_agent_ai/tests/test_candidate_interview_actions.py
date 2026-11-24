from __future__ import annotations

from io import BytesIO
import json
from threading import Thread
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

from career_agent_ai.application.api import (
    CandidateApprovalWSGIApp,
    candidate_approval_openapi_document,
)
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


def _action_state(run_id: str, owner: str, phase: CareerLoopPhase) -> CareerLoopState:
    event_id = "event-" + run_id
    request = CareerLoopRequest(
        user_id=owner,
        keyword="platform engineer",
        candidate_profile="Synthetic candidate profile",
        schedule_event_id=event_id,
    )
    if phase == CareerLoopPhase.INTERVIEW_APPROVAL:
        action = HumanActionEvent(
            kind=HumanActionKind.APPROVE_INTERVIEW,
            title="Approve interview/trial-day calendar response",
            details={
                "event_id": event_id,
                "employer": "Sandbox GmbH",
                "event_type": "interview",
                "location": "Berlin",
                "local_date": "2026-11-24",
                "local_start_time": "09:30:00",
                "local_end_date": None,
                "local_end_time": None,
                "timezone": "Europe/Berlin",
                "utc_offset": "+01:00",
                "end_utc_offset": None,
                "status": "proposed",
            },
        )
    elif phase == CareerLoopPhase.INTERVIEW_RECONCILIATION:
        action = HumanActionEvent(
            kind=HumanActionKind.RECONCILE_INTERVIEW_RESPONSE,
            title="Reconcile ambiguous interview/calendar response",
            details={
                "operation_id": run_id + ":interview-accept",
                "event_id": event_id,
                "employer": "Sandbox GmbH",
                "local_date": "2026-11-24",
                "local_start_time": "09:30:00",
                "timezone": "Europe/Berlin",
                "instruction": (
                    "Verify with the calendar provider whether the interview "
                    "acceptance was recorded before continuing."
                ),
            },
        )
    else:
        raise ValueError("unsupported test phase")
    return CareerLoopState(
        run_id=run_id,
        request=request,
        phase=phase,
        pending_human_action=action,
    )


def _seed(path, states: list[CareerLoopState]) -> None:
    database = SQLiteDatabase(str(path))
    repository = SQLiteCareerLoopRepository(database)
    for state in states:
        repository.save(state)
    database.close()


def _request(
    app,
    *,
    method: str = "GET",
    token: str | None = "valid",
    path: str = "/v1/candidate/pending-interview-actions",
    query: str = "",
    body=None,
):
    captured = []
    encoded = b"" if body is None else json.dumps(body).encode("utf-8")
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "HTTP_AUTHORIZATION": f"Bearer {token}" if token else "",
        "CONTENT_LENGTH": str(len(encoded)),
        "CONTENT_TYPE": "application/json",
        "wsgi.input": BytesIO(encoded),
    }
    payload = b"".join(app(environ, lambda status, headers: captured.append((status, headers))))
    status, headers = captured[0]
    return (status, headers), (json.loads(payload) if payload else None)


def _runtime(path) -> RuntimeCandidateApp:
    return RuntimeCandidateApp(
        database_path=str(path),
        jobs=(),
        resolve_bearer=lambda token: "candidate-1" if token == "valid" else None,
    )


def test_pending_interview_actions_are_owner_scoped_bounded_and_read_only_after_restart(tmp_path):
    path = tmp_path / "interview-actions.sqlite"
    _seed(path, [
        _action_state("run-a-approval", "candidate-1", CareerLoopPhase.INTERVIEW_APPROVAL),
        _action_state("run-b-reconcile", "candidate-1", CareerLoopPhase.INTERVIEW_RECONCILIATION),
        _action_state("run-c-reconcile", "candidate-1", CareerLoopPhase.INTERVIEW_RECONCILIATION),
        _action_state("run-foreign", "another-candidate", CareerLoopPhase.INTERVIEW_RECONCILIATION),
    ])
    database = SQLiteDatabase(str(path))
    database.connection.execute(
        "UPDATE autonomous_career_loops SET payload_json = ? WHERE run_id = ?",
        ("not-json-and-must-not-be-parsed", "run-foreign"),
    )
    database.connection.commit()
    database.close()
    baseline = path.read_bytes()

    runtime = _runtime(path)
    (status, headers), first = _request(runtime, query="limit=1")
    assert status == "200 OK"
    assert ("Cache-Control", "no-store") in headers
    assert first["schema_version"] == 1
    assert first["items"][0] == {
        "run_id": "run-a-approval",
        "state_version": 1,
        "phase": "interview_approval",
        "action_kind": "approve_interview",
        "event_id": "event-run-a-approval",
        "employer": "Sandbox GmbH",
        "local_date": "2026-11-24",
        "local_start_time": "09:30:00",
        "timezone": "Europe/Berlin",
    }
    assert first["next_after"] == "run-a-approval"

    (_, _), second = _request(runtime, query="limit=1&after=run-a-approval")
    assert [item["run_id"] for item in second["items"]] == ["run-b-reconcile"]
    assert second["items"][0]["action_kind"] == "reconcile_interview_response"
    assert second["next_after"] == "run-b-reconcile"

    (_, _), third = _request(runtime, query="limit=1&after=run-b-reconcile")
    assert [item["run_id"] for item in third["items"]] == ["run-c-reconcile"]
    assert third["next_after"] is None
    assert "operation_id" not in json.dumps([first, second, third])
    assert "interview-accept" not in json.dumps([first, second, third])
    assert "not-json-and-must-not-be-parsed" not in json.dumps([first, second, third])
    runtime.close()
    assert path.read_bytes() == baseline


def test_pending_interview_actions_auth_and_query_validation_precede_storage(tmp_path):
    path = tmp_path / "not-created.sqlite"
    runtime = _runtime(path)
    assert _request(runtime, token=None)[0][0] == "401 Unauthorized"
    assert not path.exists()
    assert _request(runtime, method="POST")[0][0] == "405 Method Not Allowed"
    assert not path.exists()
    for query in (
        "limit=0",
        "limit=26",
        "limit=1&limit=2",
        "user_id=another-candidate",
        "after=../foreign",
        "phase=interview_reconciliation",
    ):
        assert _request(runtime, query=query)[0][0] == "400 Bad Request"
        assert not path.exists()
    runtime.close()


def test_pending_interview_actions_openapi_is_owner_scoped_and_minimal():
    route = "/v1/candidate/pending-interview-actions"
    operation = candidate_approval_openapi_document()["paths"][route]["get"]
    assert operation["security"] == [{"bearerAuth": []}]
    assert {parameter["name"] for parameter in operation["parameters"]} == {"limit", "after"}
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["properties"]["items"]["maxItems"] == 25
    required = set(schema["properties"]["items"]["items"]["required"])
    assert required == {
        "run_id", "state_version", "phase", "action_kind", "event_id",
        "employer", "local_date", "local_start_time", "timezone",
    }
    assert "operation_id" not in required
    assert "provider_event_id" not in required


def test_malformed_owned_pending_interview_action_fails_closed(tmp_path):
    path = tmp_path / "malformed-owned.sqlite"
    _seed(path, [
        _action_state("run-owned", "candidate-1", CareerLoopPhase.INTERVIEW_RECONCILIATION),
    ])
    database = SQLiteDatabase(str(path))
    database.connection.execute(
        "UPDATE autonomous_career_loops SET payload_json = ? WHERE run_id = ?",
        ("{malformed", "run-owned"),
    )
    database.connection.commit()
    database.close()

    runtime = _runtime(path)
    (status, _), body = _request(runtime)
    runtime.close()
    assert status == "500 Internal Server Error"
    assert body == {"error": "internal_error"}


def test_discovered_interview_reconciliation_rejects_stale_state_version(tmp_path):
    path = tmp_path / "stale-interview-action.sqlite"
    _seed(path, [
        _action_state("run-stale", "candidate-1", CareerLoopPhase.INTERVIEW_RECONCILIATION),
    ])
    runtime = _runtime(path)
    (_, _), page = _request(runtime)
    discovered = page["items"][0]
    assert discovered["state_version"] == 1

    database = SQLiteDatabase(str(path))
    repository = SQLiteCareerLoopRepository(database)
    current = repository.get("run-stale")
    repository.save(current)
    database.close()

    (status, _), body = _request(
        runtime,
        method="POST",
        path="/v1/candidate/runs/run-stale/interview-reconciliation",
        body={
            "schema_version": 1,
            "state_version": discovered["state_version"],
            "completed": False,
        },
    )
    runtime.close()
    assert status == "409 Conflict"
    assert body == {"error": "approval_conflict"}

    database = SQLiteDatabase(str(path))
    count = database.connection.execute(
        "SELECT COUNT(*) FROM external_action_operations"
    ).fetchone()[0]
    database.close()
    assert count == 0


def test_candidate_browser_rediscovers_pending_interview_action_after_reload():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

    class RecoveryLoop:
        def list_pending_interview_actions(self, *, user_id, limit=25, after=None):
            assert user_id == "candidate-1"
            assert limit == 25
            return {
                "schema_version": 1,
                "items": [{
                    "run_id": "run-browser",
                    "state_version": 7,
                    "phase": "interview_reconciliation",
                    "action_kind": "reconcile_interview_response",
                    "event_id": "event-browser",
                    "employer": "Sandbox GmbH",
                    "local_date": "2026-11-24",
                    "local_start_time": "09:30:00",
                    "timezone": "Europe/Berlin",
                }],
                "next_after": None,
            }

        def get_candidate_run_status(self, run_id, *, user_id):
            assert (run_id, user_id) == ("run-browser", "candidate-1")
            return {
                "schema_version": 1,
                "run_id": run_id,
                "state_version": 7,
                "phase": "interview_reconciliation",
                "waiting_for_human": True,
                "terminal": False,
            }

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, format, *args):
            pass

    loop = RecoveryLoop()
    app = CandidateApprovalWSGIApp(
        loop, resolve_bearer=lambda token: "candidate-1" if token == "valid" else None
    )
    server = make_server("127.0.0.1", 0, app, handler_class=QuietHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/sandbox/candidate")
            page.locator("#token").fill("valid")
            page.locator("#list-interview-actions").click()
            page.locator("#pending-interview-actions").get_by_text(
                "Sandbox GmbH", exact=False
            ).wait_for()
            page.get_by_role("button", name="Inspect reconciliation").click()
            assert page.locator("#run-id").input_value() == "run-browser"
            page.locator("#status").click()
            page.locator("#interview-reconciliation-state").get_by_text(
                "Verify the calendar provider outcome outside this sandbox", exact=False
            ).wait_for()
            assert page.locator("#confirm-calendar-completed").is_disabled()
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
