"""Candidate-approved schedule decline API and sandbox recovery regressions."""

from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from threading import Thread
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

from career_agent_ai.application.api.candidate_approval_api import (
    candidate_approval_openapi_document,
)
from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
from career_agent_ai.application.scheduling.models import (
    ScheduleEvent,
    ScheduleEventType,
    ScheduleStatus,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_sandbox_calendar_provider import (
    SandboxCalendarOutcome,
)
from career_agent_ai.application.storage.sqlite_scheduling_repository import (
    SQLiteSchedulingRepository,
)


NOW = datetime(2026, 11, 26, 9, 0, tzinfo=timezone.utc)


def _seed_event(path, *, owner="candidate-1", event_id="event-1", version=1):
    database = SQLiteDatabase(str(path))
    event = ScheduleEvent(
        event_id=event_id,
        candidate_id=owner,
        employer_name="Sandbox Logistics",
        event_type=ScheduleEventType.INTERVIEW,
        location="Karlsruhe",
        start_at=NOW + timedelta(days=2),
        end_at=NOW + timedelta(days=2, hours=1),
        timezone_name="Europe/Berlin",
        status=ScheduleStatus.PROPOSED,
        version=version,
        created_at=NOW,
        updated_at=NOW,
    )
    SQLiteSchedulingRepository(database).create(event)
    database.close()
    return event


def _runtime(path, *, plan=None):
    return RuntimeCandidateApp(
        database_path=str(path),
        jobs=(),
        resolve_bearer=lambda token: "candidate-1" if token == "owner-token" else None,
        sandbox_calendar_outcome_plan=plan,
    )


def _request(app, *, path, method="GET", token="owner-token", body=None, query=""):
    encoded = b"" if body is None else json.dumps(body).encode("utf-8")
    statuses = []
    response_headers = []
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
            lambda status, headers: (
                statuses.append(status), response_headers.extend(headers)
            ),
        )
    )
    return statuses[0], dict(response_headers), json.loads(response)


def _decline_body(*, event_id="event-1", version=1):
    return {
        "schema_version": 1,
        "event_id": event_id,
        "state_version": version,
        "confirm_decline": True,
    }


def _reconciliation_body(*, event_id="event-1", version=1, completed=True):
    return {
        "schema_version": 1,
        "event_id": event_id,
        "state_version": version,
        "completed": completed,
        "confirm_reconciliation": True,
    }


def _counts(path):
    database = SQLiteDatabase(str(path))
    try:
        attempts = database.connection.execute(
            "SELECT COUNT(*) FROM sandbox_calendar_attempts"
        ).fetchone()[0]
        effects = database.connection.execute(
            "SELECT COUNT(*) FROM sandbox_calendar_effects"
        ).fetchone()[0]
        events = database.connection.execute(
            "SELECT status, version FROM scheduling_events WHERE event_id = ?",
            ("event-1",),
        ).fetchone()
        return attempts, effects, events
    finally:
        database.close()


def test_candidate_decline_is_explicit_owner_scoped_version_fenced_and_idempotent(tmp_path):
    path = tmp_path / "candidate-decline.sqlite"
    _seed_event(path)
    runtime = _runtime(path)

    status, headers, detail = _request(
        runtime,
        path="/v1/candidate/schedule-event",
        method="GET",
        query="event_id=event-1",
    )
    assert status == "200 OK"
    assert detail["event"]["state_version"] == 1
    assert not ({"provider_event_id", "candidate_id", "application_id"} & set(detail["event"]))

    invalid = _decline_body()
    invalid["confirm_decline"] = False
    status, _, payload = _request(
        runtime, path="/v1/candidate/schedule-event/decline",
        method="POST", body=invalid,
    )
    assert status == "400 Bad Request"
    assert payload == {"error": "invalid_request", "message": "confirm_decline must be true."}
    assert _counts(path)[:2] == (0, 0)

    status, _, payload = _request(
        runtime, path="/v1/candidate/schedule-event/decline",
        method="POST", body=_decline_body(),
    )
    assert status == "200 OK"
    assert payload == {
        "schema_version": 1,
        "event_id": "event-1",
        "status": "declined",
        "state_version": 2,
        "provider_status": "confirmed",
    }
    assert not ({"operation_id", "provider_event_id", "candidate_id"} & set(payload))

    replay_status, _, replay = _request(
        runtime, path="/v1/candidate/schedule-event/decline",
        method="POST", body=_decline_body(),
    )
    assert replay_status == "200 OK"
    assert replay == payload
    assert _counts(path) == (1, 1, ("declined", 2))
    runtime.close()


def test_candidate_decline_timeout_after_effect_recovers_from_durable_sandbox_evidence(tmp_path):
    path = tmp_path / "candidate-decline-timeout.sqlite"
    _seed_event(path)
    runtime = _runtime(
        path,
        plan=lambda _operation_id, _attempt: SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT,
    )

    status, _, pending = _request(
        runtime, path="/v1/candidate/schedule-event/decline",
        method="POST", body=_decline_body(),
    )
    assert status == "202 Accepted"
    assert pending == {
        "schema_version": 1,
        "event_id": "event-1",
        "status": "proposed",
        "state_version": 1,
        "provider_status": "reconciliation_required",
    }
    assert _counts(path) == (1, 1, ("proposed", 1))
    runtime.close()

    restarted = _runtime(path)
    status, _, resolved = _request(
        restarted,
        path="/v1/candidate/schedule-event/decline-reconciliation",
        method="POST",
        body=_reconciliation_body(completed=True),
    )
    assert status == "200 OK"
    assert resolved == {
        "schema_version": 1,
        "event_id": "event-1",
        "status": "declined",
        "state_version": 2,
        "provider_status": "confirmed",
    }

    replay_status, _, replay = _request(
        restarted,
        path="/v1/candidate/schedule-event/decline-reconciliation",
        method="POST",
        body=_reconciliation_body(completed=True),
    )
    assert replay_status == "200 OK"
    assert replay == resolved
    assert _counts(path) == (1, 1, ("declined", 2))
    restarted.close()


def test_candidate_decline_rejects_foreign_and_stale_events_before_provider_io(tmp_path):
    path = tmp_path / "candidate-decline-isolation.sqlite"
    _seed_event(path, owner="other-candidate", event_id="foreign-event")
    _seed_event(path, event_id="stale-event", version=2)
    runtime = _runtime(path)

    status, _, payload = _request(
        runtime,
        path="/v1/candidate/schedule-event/decline",
        method="POST",
        body=_decline_body(event_id="foreign-event", version=1),
    )
    assert status == "404 Not Found"
    assert payload == {"error": "not_found"}

    status, _, payload = _request(
        runtime,
        path="/v1/candidate/schedule-event/decline",
        method="POST",
        body=_decline_body(event_id="stale-event", version=1),
    )
    assert status == "409 Conflict"
    assert payload == {"error": "approval_conflict"}

    database = SQLiteDatabase(str(path))
    try:
        attempts = database.connection.execute(
            "SELECT COUNT(*) FROM sandbox_calendar_attempts"
        ).fetchone()[0]
        effects = database.connection.execute(
            "SELECT COUNT(*) FROM sandbox_calendar_effects"
        ).fetchone()[0]
    finally:
        database.close()
    assert (attempts, effects) == (0, 0)
    runtime.close()


def test_candidate_decline_no_effect_requires_a_fresh_explicit_retry(tmp_path):
    path = tmp_path / "candidate-decline-no-effect.sqlite"
    _seed_event(path)
    runtime = _runtime(
        path,
        plan=lambda _operation_id, attempt: (
            SandboxCalendarOutcome.NO_EFFECT
            if attempt == 1
            else SandboxCalendarOutcome.SUCCESS
        ),
    )

    status, _, first = _request(
        runtime, path="/v1/candidate/schedule-event/decline",
        method="POST", body=_decline_body(),
    )
    assert status == "200 OK"
    assert first["provider_status"] == "no_effect_confirmed"
    assert first["status"] == "proposed"
    assert first["state_version"] == 1
    assert _counts(path) == (1, 0, ("proposed", 1))

    # A new explicit candidate confirmation reuses the same version-bound intent.
    status, _, second = _request(
        runtime, path="/v1/candidate/schedule-event/decline",
        method="POST", body=_decline_body(),
    )
    assert status == "200 OK"
    assert second["provider_status"] == "confirmed"
    assert second["status"] == "declined"
    assert second["state_version"] == 2
    assert _counts(path) == (2, 1, ("declined", 2))
    runtime.close()


def test_candidate_decline_reconciliation_rejects_a_claim_that_disagrees_with_evidence(tmp_path):
    path = tmp_path / "candidate-decline-evidence.sqlite"
    _seed_event(path)
    runtime = _runtime(
        path,
        plan=lambda _operation_id, _attempt: SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT,
    )
    status, _, _ = _request(
        runtime, path="/v1/candidate/schedule-event/decline",
        method="POST", body=_decline_body(),
    )
    assert status == "202 Accepted"

    status, _, payload = _request(
        runtime,
        path="/v1/candidate/schedule-event/decline-reconciliation",
        method="POST",
        body=_reconciliation_body(completed=False),
    )
    assert status == "409 Conflict"
    assert payload == {"error": "approval_conflict"}
    assert _counts(path) == (1, 1, ("proposed", 1))

    status, _, resolved = _request(
        runtime,
        path="/v1/candidate/schedule-event/decline-reconciliation",
        method="POST",
        body=_reconciliation_body(completed=True),
    )
    assert status == "200 OK"
    assert resolved["status"] == "declined"
    assert _counts(path) == (1, 1, ("declined", 2))
    runtime.close()


def test_candidate_schedule_decline_openapi_is_strict_and_documents_reconciliation():
    document = candidate_approval_openapi_document()
    decline = document["paths"]["/v1/candidate/schedule-event/decline"]["post"]
    request_schema = decline["requestBody"]["content"]["application/json"]["schema"]
    assert request_schema["additionalProperties"] is False
    assert set(request_schema["required"]) == {
        "schema_version", "event_id", "state_version", "confirm_decline"
    }
    assert decline["responses"]["202"]["description"].startswith("Provider effect is ambiguous")

    reconciliation = document["paths"][
        "/v1/candidate/schedule-event/decline-reconciliation"
    ]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert reconciliation["additionalProperties"] is False
    assert set(reconciliation["required"]) == {
        "schema_version", "event_id", "state_version",
        "completed", "confirm_reconciliation",
    }


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


def test_candidate_dashboard_restarts_and_reconciles_interview_decline_without_replay(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from playwright.sync_api import expect

    path = tmp_path / "candidate-decline-browser.sqlite"
    _seed_event(path)
    plan = lambda _operation_id, _attempt: SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT

    first = _runtime(path, plan=plan)
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
                page.locator("#list-events").click()
                expect(page.locator("#owned-events button")).to_have_count(1)
                page.locator("#owned-events button").click()
                expect(page.locator("#event-detail")).to_contain_text("event-1")
                page.locator("#confirm-event-decline").check()
                page.locator("#decline-event").click()
                expect(page.locator("#event-decline-state")).to_contain_text("provider response was ambiguous")

                _stop_server(server, thread)
                server = None
                first.close()
                second = _runtime(path, plan=plan)
                server = make_server("127.0.0.1", port, second, handler_class=QuietHandler)
                second_thread = Thread(target=server.serve_forever, daemon=True)
                second_thread.start()

                page.reload(wait_until="domcontentloaded")
                expect(page.locator("#token")).to_have_value("")
                page.locator("#token").fill("owner-token")
                page.locator("#list-events").click()
                expect(page.locator("#owned-events button")).to_have_count(1)
                page.locator("#owned-events button").click()
                expect(page.locator("#event-detail")).to_contain_text("event-1")
                page.locator("#confirm-event-decline").check()
                page.locator("#decline-event").click()
                expect(page.locator("#event-decline-state")).to_contain_text("provider response was ambiguous")

                page.locator("#event-decline-reconciliation-verified").check()
                page.locator("#confirm-event-decline-completed").click()
                expect(page.locator("#event-decline-state")).to_contain_text("Sandbox provider evidence confirmed the decline")
            finally:
                browser.close()
    finally:
        _stop_server(server, second_thread if second_thread is not None else thread)
        first.close()
        if second is not None:
            second.close()

    assert _counts(path) == (1, 1, ("declined", 2))
