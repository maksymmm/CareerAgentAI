from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from threading import Thread
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

from career_agent_ai.application.api.candidate_approval_api import (
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
from career_agent_ai.application.external_actions.external_action_operation import (
    ExternalActionOperation,
    ExternalActionStatus,
)
from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
from career_agent_ai.application.scheduling.models import (
    HumanScheduleView,
    ScheduleEvent,
    ScheduleEventType,
    ScheduleStatus,
)
from career_agent_ai.application.storage.sqlite_candidate_interview_intents import (
    SQLiteCandidateInterviewIntents,
)
from career_agent_ai.application.storage.sqlite_career_loop_repository import (
    SQLiteCareerLoopRepository,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import (
    SQLiteExternalActionOperationRepository,
)
from career_agent_ai.application.storage.sqlite_scheduling_repository import (
    SQLiteSchedulingRepository,
)


NOW = datetime(2026, 11, 26, 9, 0, tzinfo=timezone.utc)


def _seed_interview_intent(
    path,
    *,
    owner: str = "candidate-1",
    run_id: str = "run-1",
    event_id: str = "event-1",
    operation_id: str = "run-1:interview-accept",
):
    database = SQLiteDatabase(str(path))
    event = ScheduleEvent(
        event_id=event_id,
        candidate_id=owner,
        employer_name="Sandbox Logistics",
        event_type=ScheduleEventType.TRIAL_DAY,
        location="Karlsruhe",
        start_at=NOW + timedelta(days=2),
        end_at=NOW + timedelta(days=2, hours=1),
        timezone_name="Europe/Berlin",
        status=ScheduleStatus.PROPOSED,
        application_id="private-application-id",
        created_at=NOW,
        updated_at=NOW,
    )
    scheduling = SQLiteSchedulingRepository(database)
    scheduling.create(event)
    scheduling.claim_action(
        event_id,
        operation_id,
        (ScheduleStatus.PROPOSED, ScheduleStatus.RESCHEDULE_REQUESTED),
        expected_version=event.version,
    )
    operation_payload = {
        "event_id": event_id,
        "event_version": event.version,
        "event_start_at": event.start_at.isoformat(),
        "event_end_at": event.end_at.isoformat(),
        "event_timezone_name": event.timezone_name,
        "event_status": event.status.value,
    }
    operations = SQLiteExternalActionOperationRepository(database)
    operations.create(
        ExternalActionOperation(
            operation_id=operation_id,
            action_type="calendar.accept",
            payload=operation_payload,
        )
    )
    database.connection.execute(
        "UPDATE external_action_operations SET status = ? WHERE operation_id = ?",
        (ExternalActionStatus.RECONCILIATION_REQUIRED.value, operation_id),
    )
    database.connection.commit()

    view = HumanScheduleView.from_event(event)
    state = CareerLoopState(
        run_id=run_id,
        request=CareerLoopRequest(
            user_id=owner,
            keyword="logistics",
            candidate_profile="Experienced logistics professional.",
        ),
        phase=CareerLoopPhase.INTERVIEW_RECONCILIATION,
        pending_human_action=HumanActionEvent(
            kind=HumanActionKind.RECONCILE_INTERVIEW_RESPONSE,
            title="Reconcile ambiguous interview/calendar response",
            details={
                "operation_id": operation_id,
                "event_id": event_id,
                "employer": view.employer_name,
                "local_date": view.local_date,
                "local_start_time": view.local_start_time,
                "timezone": view.timezone_name,
                "instruction": "Verify the calendar provider outcome.",
            },
        ),
    )
    SQLiteCareerLoopRepository(database).save(state)
    database.close()
    return run_id, event_id, operation_id


def _wsgi_request(app, *, path, method="GET", token="valid", query="", body=None):
    encoded = b"" if body is None else json.dumps(body).encode("utf-8")
    statuses = []
    response = b"".join(
        app(
            {
                "REQUEST_METHOD": method,
                "PATH_INFO": path,
                "QUERY_STRING": query,
                "HTTP_AUTHORIZATION": f"Bearer {token}" if token else "",
                "CONTENT_TYPE": "application/json",
                "CONTENT_LENGTH": str(len(encoded)),
                "wsgi.input": io.BytesIO(encoded),
            },
            lambda status, headers: statuses.append((status, headers)),
        )
    )
    return statuses[0], json.loads(response)


class FakeIntentLoop:
    def __init__(self):
        self.arguments = None

    def list_interview_intents(self, **arguments):
        self.arguments = arguments
        return {"schema_version": 1, "items": [], "next_after": None}


def test_projection_discovers_only_owned_pending_interview_intents_and_redacts_ids(tmp_path):
    path = tmp_path / "interview-intents.sqlite"
    run_id, event_id, operation_id = _seed_interview_intent(path)
    _seed_interview_intent(
        path,
        owner="other-candidate",
        run_id="foreign-run",
        event_id="foreign-event",
        operation_id="foreign-run:interview-accept",
    )
    database = SQLiteDatabase(str(path))
    database.connection.execute(
        "UPDATE autonomous_career_loops SET payload_json = ? WHERE owner_user_id = ?",
        ("not-json", "other-candidate"),
    )
    database.connection.commit()
    database.close()
    baseline = path.read_bytes()

    page = SQLiteCandidateInterviewIntents(str(path)).list_page(user_id="candidate-1")

    assert page["schema_version"] == 1
    assert page["next_after"] is None
    assert len(page["items"]) == 1
    item = page["items"][0]
    assert item["run_id"] == run_id
    assert item["state_version"] == 1
    assert item["action_type"] == "calendar.accept"
    assert item["operation_status"] == "reconciliation_required"
    assert item["event"]["event_id"] == event_id
    assert item["event"]["employer_name"] == "Sandbox Logistics"
    assert item["event"]["event_type"] == "trial_day"
    assert item["event"]["state_version"] == 1
    assert "operation_id" not in str(page)
    assert operation_id not in str(page)
    assert "provider_event_id" not in str(page)
    assert "application_id" not in str(page)
    assert "candidate_id" not in str(page)
    assert path.read_bytes() == baseline


def test_projection_keyset_pagination_is_owner_scoped_and_stable(tmp_path):
    path = tmp_path / "pages.sqlite"
    for suffix in ("a", "b", "c"):
        _seed_interview_intent(
            path,
            run_id=f"run-{suffix}",
            event_id=f"event-{suffix}",
            operation_id=f"run-{suffix}:interview-accept",
        )

    projection = SQLiteCandidateInterviewIntents(str(path))
    first = projection.list_page(user_id="candidate-1", limit=2)
    second = projection.list_page(
        user_id="candidate-1", limit=2, after=first["next_after"]
    )
    foreign = projection.list_page(user_id="missing-candidate")

    assert [item["run_id"] for item in first["items"]] == ["run-a", "run-b"]
    assert first["next_after"] == "run-b"
    assert [item["run_id"] for item in second["items"]] == ["run-c"]
    assert second["next_after"] is None
    assert foreign == {"schema_version": 1, "items": [], "next_after": None}


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE scheduling_events SET active_action_operation_id = NULL WHERE event_id = 'event-1'",
        "UPDATE external_action_operations SET action_type = 'calendar.decline' WHERE operation_id = 'run-1:interview-accept'",
        "UPDATE external_action_operations SET status = 'succeeded' WHERE operation_id = 'run-1:interview-accept'",
        "UPDATE scheduling_events SET version = 2 WHERE event_id = 'event-1'",
    ],
)
def test_projection_fails_closed_when_intent_event_or_operation_binding_is_stale(
    tmp_path, mutation
):
    path = tmp_path / "corrupt.sqlite"
    _seed_interview_intent(path)
    database = SQLiteDatabase(str(path))
    database.connection.execute(mutation)
    database.connection.commit()
    database.close()

    with pytest.raises(ValueError, match="Persisted candidate interview intent is malformed"):
        SQLiteCandidateInterviewIntents(str(path)).list_page(user_id="candidate-1")


def test_projection_validates_arguments_before_opening_storage(tmp_path):
    path = tmp_path / "missing.sqlite"
    projection = SQLiteCandidateInterviewIntents(str(path))
    for arguments in (
        {"user_id": "bad/owner"},
        {"user_id": "candidate-1", "limit": True},
        {"user_id": "candidate-1", "limit": 101},
        {"user_id": "candidate-1", "after": " padded"},
    ):
        with pytest.raises(ValueError):
            projection.list_page(**arguments)
        assert not path.exists()


def test_projection_does_not_create_missing_storage(tmp_path):
    path = tmp_path / "missing.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        SQLiteCandidateInterviewIntents(str(path)).list_page(user_id="candidate-1")
    assert not path.exists()


def test_interview_intent_http_route_is_authenticated_bounded_and_strict():
    loop = FakeIntentLoop()
    app = CandidateApprovalWSGIApp(
        loop, resolve_bearer=lambda token: "candidate-1" if token == "valid" else None
    )
    (status, headers), payload = _wsgi_request(
        app,
        path="/v1/candidate/interview-intents",
        query="limit=7&after=run-4",
    )
    assert status == "200 OK"
    assert payload == {"schema_version": 1, "items": [], "next_after": None}
    assert loop.arguments == {"user_id": "candidate-1", "limit": 7, "after": "run-4"}
    assert ("Cache-Control", "no-store") in headers

    (status, _), payload = _wsgi_request(
        app, path="/v1/candidate/interview-intents", token="invalid"
    )
    assert status == "401 Unauthorized"
    assert payload == {"error": "unauthorized"}

    (status, headers), payload = _wsgi_request(
        app, path="/v1/candidate/interview-intents", method="POST"
    )
    assert status == "405 Method Not Allowed"
    assert ("Allow", "GET") in headers
    assert payload == {"error": "method_not_allowed"}

    for query in ("limit=1&limit=2", "unknown=1", "limit=0", "after=bad%2Fcursor"):
        (status, _), payload = _wsgi_request(
            app, path="/v1/candidate/interview-intents", query=query
        )
        assert status == "400 Bad Request"
        assert payload == {"error": "invalid_request"}


def test_interview_intent_openapi_is_explicit_and_redacts_provider_identifiers():
    operation = candidate_approval_openapi_document()["paths"][
        "/v1/candidate/interview-intents"
    ]["get"]
    assert operation["operationId"] == "listCandidateInterviewIntents"
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    item = schema["properties"]["items"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {
        "run_id", "state_version", "action_type", "operation_status", "event"
    }
    assert "provider_event_id" not in str(item)
    assert "operation_id" not in str(item)


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


def _stop_server(server, thread):
    if server is None:
        return
    server.shutdown()
    thread.join(timeout=5)
    assert not thread.is_alive()
    server.server_close()


def test_candidate_can_discover_interview_intent_after_runtime_and_browser_restart_and_reject_stale_version(
    tmp_path,
):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from playwright.sync_api import expect

    path = tmp_path / "candidate-interview-intents.sqlite"
    run_id, _, _ = _seed_interview_intent(path)
    owner = "candidate-1"

    def build_runtime():
        return RuntimeCandidateApp(
            database_path=str(path),
            jobs=(),
            resolve_bearer=lambda token: owner if token == "owner-token" else None,
        )

    first = build_runtime()
    server = make_server("127.0.0.1", 0, first, handler_class=QuietHandler)
    port = server.server_port
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    second = None
    second_thread = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                url = f"http://127.0.0.1:{port}/sandbox/candidate"
                page.goto(url)
                page.locator("#token").fill("owner-token")
                page.locator("#load-interview-intents").click()
                expect(page.locator("#pending-interview-intents button")).to_have_count(1)

                _stop_server(server, thread)
                server = None
                first.close()
                second = build_runtime()
                server = make_server("127.0.0.1", port, second, handler_class=QuietHandler)
                second_thread = Thread(target=server.serve_forever, daemon=True)
                second_thread.start()

                page.reload(wait_until="domcontentloaded")
                expect(page.locator("#token")).to_have_value("")
                expect(page.locator("#run-id")).to_have_value("")
                page.locator("#token").fill("owner-token")
                page.locator("#load-interview-intents").click()
                expect(page.locator("#pending-interview-intents button")).to_have_count(1)

                # Simulate a concurrent durable transition after the intent page was read.
                database = SQLiteDatabase(str(path))
                row = database.connection.execute(
                    "SELECT payload_json, version FROM autonomous_career_loops WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                payload = json.loads(row[0])
                payload["version"] = row[1] + 1
                database.connection.execute(
                    "UPDATE autonomous_career_loops SET payload_json = ?, version = ? WHERE run_id = ?",
                    (json.dumps(payload, sort_keys=True, separators=(",", ":")),
                     row[1] + 1, run_id),
                )
                database.connection.commit()
                database.close()

                page.locator("#pending-interview-intents button").click()
                expect(page.locator("#run-id")).to_have_value(run_id)
                expect(page.locator("#interview-reconciliation-state")).to_contain_text("stale")
                expect(page.locator("#confirm-calendar-completed")).to_be_disabled()

                page.locator("#load-interview-intents").click()
                expect(page.locator("#pending-interview-intents button")).to_have_count(1)
                page.locator("#pending-interview-intents button").click()
                expect(page.locator("#run-overview")).to_contain_text("interview reconciliation")
                expect(page.locator("#interview-intent-detail")).to_contain_text("Sandbox Logistics")
                expect(page.locator("#interview-intent-detail")).to_contain_text(run_id)
                expect(page.locator("#provider-event-id")).to_be_enabled()
                expect(page.locator("#interview-reconciliation-verified")).to_be_enabled()
                expect(page.locator("#confirm-calendar-completed")).to_be_disabled()

                page.locator("#provider-event-id").fill("sandbox-provider-event-1")
                page.locator("#interview-reconciliation-verified").check()
                expect(page.locator("#confirm-calendar-completed")).to_be_enabled()

                page.locator("#clear-session").click()
                expect(page.locator("#token")).to_have_value("")
                expect(page.locator("#run-id")).to_have_value("")
                expect(page.locator("#pending-interview-intents")).to_be_empty()
                expect(page.locator("#confirm-calendar-completed")).to_be_disabled()
            finally:
                browser.close()
    finally:
        _stop_server(server, second_thread if second_thread is not None else thread)
        first.close()
        if second is not None:
            second.close()
