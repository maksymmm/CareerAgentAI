"""Authenticated candidate rescheduling and provider-evidence recovery regressions."""

from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from career_agent_ai.application.api import (
    CandidateScheduleActionWSGIApp,
    candidate_approval_openapi_document,
)
from career_agent_ai.application.api.candidate_sandbox_ui import candidate_sandbox_html
from career_agent_ai.application.runtime.composition import build_candidate_sandbox_app_from_env
from career_agent_ai.application.scheduling import ScheduleEvent, ScheduleEventType, ScheduleStatus
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_sandbox_calendar_provider import SandboxCalendarOutcome
from career_agent_ai.application.storage.sqlite_scheduling_repository import SQLiteSchedulingRepository

OWNER = "candidate-1"
TOKENS = {"owner-token": OWNER, "foreign-token": "candidate-2"}
ENV = {
    "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
    "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
}
EVENT_ID = "interview-reschedule-1"
START = "2026-12-02T10:00:00+01:00"
END = "2026-12-02T11:00:00+01:00"
ZONE = "Europe/Berlin"


def _event(event_id=EVENT_ID, candidate_id=OWNER, *, status=ScheduleStatus.ACCEPTED,
           start_at=datetime(2026, 12, 1, 9, 0, tzinfo=timezone.utc)):
    return ScheduleEvent(
        event_id=event_id,
        candidate_id=candidate_id,
        employer_name="Sandbox GmbH",
        event_type=ScheduleEventType.INTERVIEW,
        location="Sandbox video room",
        start_at=start_at,
        end_at=start_at + timedelta(hours=1),
        timezone_name=ZONE,
        status=status,
        provider_event_id="fake-" + event_id,
        application_id="application-1",
        created_at=datetime(2026, 11, 20, tzinfo=timezone.utc),
        updated_at=datetime(2026, 11, 20, tzinfo=timezone.utc),
    )


def _seed(path, *events):
    database = SQLiteDatabase(str(path))
    repository = SQLiteSchedulingRepository(database)
    for event in events:
        repository.create(event)
    database.close()


def _body(**overrides):
    return {
        "schema_version": 1,
        "event_id": EVENT_ID,
        "state_version": 1,
        "start_at": START,
        "end_at": END,
        "timezone_name": ZONE,
        "decision": "approve",
        **overrides,
    }


class Candidate:
    def __init__(self, path, plan=None):
        self.path = str(path)
        self.app = build_candidate_sandbox_app_from_env(
            resolve_bearer=TOKENS.get,
            environ={"CAREER_AGENT_DB_PATH": self.path, **ENV},
            sandbox_calendar_outcome_plan=plan,
        )

    def call(self, route, *, token="owner-token", method="POST", body=None, raw=None,
             content_type="application/json"):
        encoded = raw if raw is not None else json.dumps(body).encode("utf-8")
        statuses = []
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": route,
            "QUERY_STRING": "",
            "CONTENT_TYPE": content_type,
            "CONTENT_LENGTH": str(len(encoded)),
            "wsgi.input": io.BytesIO(encoded),
        }
        if token is not None:
            environ["HTTP_AUTHORIZATION"] = "Bearer " + token
        payload = b"".join(self.app(environ, lambda status, headers: statuses.append(status)))
        return statuses[0], json.loads(payload)

    def reschedule(self, body=None):
        return self.call(
            CandidateScheduleActionWSGIApp.RESCHEDULE_PATH,
            body=_body() if body is None else body,
        )

    def reconcile(self, completed, body=None, *, verified=True):
        source = _body() if body is None else body
        request = {
            key: source[key]
            for key in ("schema_version", "event_id", "state_version", "start_at", "end_at", "timezone_name")
        }
        request.update(completed=completed, independently_verified=verified)
        return self.call(CandidateScheduleActionWSGIApp.RECONCILIATION_PATH, body=request)

    def close(self):
        self.app.close()


def _effect_count(path):
    connection = sqlite3.connect(path)
    try:
        return connection.execute("SELECT COUNT(*) FROM sandbox_calendar_effects").fetchone()[0]
    finally:
        connection.close()


def _stored_event(path):
    database = SQLiteDatabase(str(path))
    try:
        return SQLiteSchedulingRepository(database).get(EVENT_ID)
    finally:
        database.close()


def test_candidate_can_reschedule_owned_interview_and_replay_after_restart(tmp_path):
    path = tmp_path / "candidate-reschedule.sqlite"
    _seed(path, _event())
    first = Candidate(path)
    status, result = first.reschedule()
    assert status == "200 OK"
    assert result["outcome"] == "completed"
    assert result["event"]["event_id"] == EVENT_ID
    assert result["event"]["status"] == "reschedule_requested"
    assert result["event"]["state_version"] == 2
    assert result["event"]["local_date"] == "2026-12-02"
    assert result["event"]["local_start_time"] == "10:00:00"
    assert "provider_event_id" not in result["event"]
    assert _effect_count(path) == 1
    first.close()

    restarted = Candidate(path)
    replay_status, replay = restarted.reschedule()
    assert replay_status == "200 OK"
    assert replay == result
    assert _effect_count(path) == 1
    restarted.close()


def test_foreign_event_is_indistinguishable_from_missing_and_never_reaches_provider(tmp_path):
    path = tmp_path / "foreign.sqlite"
    _seed(path, _event(candidate_id="candidate-2"))
    candidate = Candidate(path)
    status, result = candidate.reschedule()
    assert (status, result) == ("404 Not Found", {"error": "not_found"})
    assert _effect_count(path) == 0
    candidate.close()


def test_stale_event_version_is_rejected_before_provider_effect(tmp_path):
    path = tmp_path / "stale.sqlite"
    stale = _event().transition(ScheduleStatus.RESCHEDULE_REQUESTED,
                                start_at=datetime(2026, 12, 1, 9, 0, tzinfo=timezone.utc))
    _seed(path, stale)
    candidate = Candidate(path)
    status, result = candidate.reschedule()
    assert (status, result) == ("409 Conflict", {"error": "approval_conflict"})
    assert _effect_count(path) == 0
    candidate.close()


def test_conflicting_committed_slot_is_rejected_before_provider_effect(tmp_path):
    path = tmp_path / "conflict.sqlite"
    conflict = _event(
        event_id="other-interview",
        start_at=datetime(2026, 12, 2, 9, 0, tzinfo=timezone.utc),
    )
    _seed(path, _event(), conflict)
    candidate = Candidate(path)
    status, result = candidate.reschedule()
    assert (status, result) == ("409 Conflict", {"error": "schedule_conflict"})
    assert _effect_count(path) == 0
    candidate.close()


def test_timeout_after_effect_requires_restart_and_evidence_before_completion(tmp_path):
    path = tmp_path / "timeout.sqlite"
    _seed(path, _event())
    first = Candidate(path, lambda _key, _attempt: SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT)
    status, result = first.reschedule()
    assert status == "202 Accepted"
    assert result["outcome"] == "reconciliation_required"
    assert _effect_count(path) == 1
    first.close()

    restarted = Candidate(path)
    status, result = restarted.reconcile(False)
    assert (status, result) == ("409 Conflict", {"error": "provider_evidence_conflict"})
    status, result = restarted.reconcile(True)
    assert status == "200 OK" and result["outcome"] == "completed"
    assert result["event"]["status"] == "reschedule_requested"
    assert result["event"]["state_version"] == 2
    assert _effect_count(path) == 1
    restarted.close()


def test_interruption_before_effect_requires_no_effect_evidence_then_same_key_retry(tmp_path):
    path = tmp_path / "interrupted.sqlite"
    _seed(path, _event())
    first = Candidate(path, lambda _key, _attempt: SandboxCalendarOutcome.INTERRUPTED_BEFORE_EFFECT)
    status, result = first.reschedule()
    assert status == "202 Accepted"
    assert result["outcome"] == "reconciliation_required"
    assert _effect_count(path) == 0
    first.close()

    restarted = Candidate(path)
    status, result = restarted.reconcile(True)
    assert (status, result) == ("409 Conflict", {"error": "provider_evidence_conflict"})
    status, result = restarted.reconcile(False)
    assert status == "200 OK" and result["outcome"] == "no_effect"
    assert result["event"]["state_version"] == 1
    status, result = restarted.reschedule()
    assert status == "200 OK" and result["outcome"] == "completed"
    assert result["event"]["state_version"] == 2
    assert _effect_count(path) == 1
    restarted.close()


def test_definite_no_effect_can_retry_the_same_durable_intent(tmp_path):
    path = tmp_path / "no-effect.sqlite"
    _seed(path, _event())
    attempts = {"count": 0}

    def plan(_key, _attempt):
        attempts["count"] += 1
        return SandboxCalendarOutcome.NO_EFFECT if attempts["count"] == 1 else SandboxCalendarOutcome.SUCCESS

    candidate = Candidate(path, plan)
    status, result = candidate.reschedule()
    assert status == "200 OK" and result["outcome"] == "no_effect"
    assert result["event"]["status"] == "accepted"
    assert _effect_count(path) == 0
    status, result = candidate.reschedule()
    assert status == "200 OK" and result["outcome"] == "completed"
    assert _effect_count(path) == 1
    candidate.close()


@pytest.mark.parametrize("body", [
    _body(decision="decline"),
    _body(user_id=OWNER),
    _body(operation_id="caller-controlled"),
    _body(state_version=True),
    _body(state_version=0),
    _body(start_at="2026-12-02T10:00:00"),
    _body(end_at="2026-12-02T09:00:00+01:00"),
    _body(timezone_name="Europe/Berlin "),
])
def test_reschedule_rejects_unapproved_or_malformed_request_before_effect(tmp_path, body):
    path = tmp_path / "invalid.sqlite"
    _seed(path, _event())
    candidate = Candidate(path)
    status, result = candidate.reschedule(body)
    assert status == "400 Bad Request"
    assert result == {"error": "invalid_request"}
    assert _effect_count(path) == 0
    candidate.close()


def test_reschedule_rejects_duplicate_json_members(tmp_path):
    path = tmp_path / "duplicate.sqlite"
    _seed(path, _event())
    candidate = Candidate(path)
    raw = (
        '{"schema_version":1,"schema_version":1,"event_id":"interview-reschedule-1",'
        '"state_version":1,"start_at":"2026-12-02T10:00:00+01:00",'
        '"end_at":"2026-12-02T11:00:00+01:00","timezone_name":"Europe/Berlin",'
        '"decision":"approve"}'
    )
    status, result = candidate.call(CandidateScheduleActionWSGIApp.RESCHEDULE_PATH, raw=raw.encode())
    assert (status, result) == ("400 Bad Request", {"error": "invalid_request"})
    assert _effect_count(path) == 0
    candidate.close()


def test_reconciliation_requires_explicit_version_bound_attestation(tmp_path):
    path = tmp_path / "attestation.sqlite"
    _seed(path, _event())
    first = Candidate(path, lambda _key, _attempt: SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT)
    assert first.reschedule()[0] == "202 Accepted"
    status, result = first.reconcile(True, verified=False)
    assert (status, result) == ("400 Bad Request", {"error": "invalid_request"})
    assert _effect_count(path) == 1
    first.close()


def test_authentication_precedes_missing_storage_access(tmp_path):
    path = tmp_path / "missing.sqlite"
    app = CandidateScheduleActionWSGIApp(
        database_path=str(path), resolve_bearer=lambda _token: None
    )
    body = json.dumps(_body()).encode()
    statuses = []
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": CandidateScheduleActionWSGIApp.RESCHEDULE_PATH,
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(body)),
        "HTTP_AUTHORIZATION": "Bearer invalid",
        "wsgi.input": io.BytesIO(body),
    }
    payload = b"".join(app(environ, lambda status, headers: statuses.append(status)))
    assert statuses == ["401 Unauthorized"]
    assert json.loads(payload) == {"error": "unauthorized"}
    assert not path.exists()


def test_methods_content_type_and_no_store_contract(tmp_path):
    path = tmp_path / "method.sqlite"
    _seed(path, _event())
    candidate = Candidate(path)
    status, result = candidate.call(
        CandidateScheduleActionWSGIApp.RESCHEDULE_PATH, method="GET", body={}
    )
    assert (status, result) == ("405 Method Not Allowed", {"error": "method_not_allowed"})
    status, result = candidate.call(
        CandidateScheduleActionWSGIApp.RESCHEDULE_PATH, body=_body(),
        content_type="text/plain",
    )
    assert (status, result) == ("415 Unsupported Media Type", {"error": "unsupported_media_type"})
    statuses = []
    body = json.dumps(_body()).encode()
    response = b"".join(candidate.app({
        "REQUEST_METHOD": "POST",
        "PATH_INFO": CandidateScheduleActionWSGIApp.RESCHEDULE_PATH,
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(body)),
        "HTTP_AUTHORIZATION": "Bearer owner-token",
        "wsgi.input": io.BytesIO(body),
    }, lambda status, headers: statuses.append((status, dict(headers)))))
    assert statuses[0][0] == "200 OK"
    assert statuses[0][1]["Cache-Control"] == "no-store"
    assert statuses[0][1]["Pragma"] == "no-cache"
    candidate.close()


def test_openapi_and_dashboard_publish_the_approved_sandbox_flow():
    document = candidate_approval_openapi_document()
    route = document["paths"][CandidateScheduleActionWSGIApp.RESCHEDULE_PATH]["post"]
    schema = route["requestBody"]["content"]["application/json"]["schema"]
    assert route["security"] == [{"bearerAuth": []}]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["decision"]["const"] == "approve"
    reconciliation = document["paths"][CandidateScheduleActionWSGIApp.RECONCILIATION_PATH]["post"]
    reconciliation_schema = reconciliation["requestBody"]["content"]["application/json"]["schema"]
    assert reconciliation_schema["properties"]["independently_verified"]["const"] is True

    html = candidate_sandbox_html().decode("utf-8")
    for identifier in (
        "reschedule-start", "reschedule-end", "reschedule-timezone",
        "request-reschedule", "confirm-reschedule-completed",
        "confirm-reschedule-no-effect", "/v1/candidate/schedule-event/reschedule",
        "/v1/candidate/schedule-event/reschedule-reconciliation",
    ):
        assert identifier in html
    assert "No real calendar is contacted" in html


def test_candidate_sandbox_browser_submits_and_reconciles_owned_interview_reschedule(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from threading import Thread
    from wsgiref.simple_server import WSGIRequestHandler, make_server

    path = tmp_path / "candidate-browser-reschedule.sqlite"
    _seed(path, _event())
    candidate = Candidate(
        path, lambda _key, _attempt: SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT
    )

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, format, *args):
            pass

    server = make_server("127.0.0.1", 0, candidate.app, handler_class=QuietHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/sandbox/candidate")
            page.locator("#token").fill("owner-token")
            page.locator("#list-events").click()
            page.get_by_role("button", name="View event 1: interview-reschedule-1").wait_for()
            page.get_by_role("button", name="View event 1: interview-reschedule-1").click()
            page.locator("#event-detail").get_by_text('"state_version": 1').wait_for()
            assert not page.locator("#reschedule-panel").is_disabled()

            page.locator("#reschedule-start").fill(START)
            page.locator("#reschedule-end").fill(END)
            page.locator("#reschedule-timezone").fill(ZONE)
            page.locator("#request-reschedule").click()
            page.locator("#reschedule-reconciliation-state").get_by_text(
                "Provider outcome is uncertain"
            ).wait_for()
            assert page.locator("#reschedule-panel").is_disabled()
            assert page.locator("#confirm-reschedule-completed").is_disabled()
            assert page.locator("#confirm-reschedule-no-effect").is_disabled()

            page.locator("#reschedule-reconciliation-verified").check()
            page.locator("#confirm-reschedule-no-effect").click()
            page.locator("#reschedule-reconciliation-state").get_by_text(
                "Provider evidence did not confirm"
            ).wait_for()
            assert not page.locator("#reschedule-reconciliation-panel").is_disabled()

            page.locator("#confirm-reschedule-completed").click()
            page.locator("#reschedule-state").get_by_text(
                "Provider evidence confirmed the sandbox reschedule"
            ).wait_for()
            assert '"status": "reschedule_requested"' in page.locator(
                "#event-detail"
            ).text_content()
            assert '"state_version": 2' in page.locator("#event-detail").text_content()
            assert page.locator("#confirm-reschedule-completed").is_disabled()
            assert page.locator("#confirm-reschedule-no-effect").is_disabled()
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
        candidate.close()
