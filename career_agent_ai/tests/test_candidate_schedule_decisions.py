"""HTTP, SQLite restart, and browser regressions for candidate interview decline."""

from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from threading import Thread
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

from career_agent_ai.application.api import (
    CandidateScheduleDeclineWSGIApp,
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
EVENT_ID = "candidate-decline-interview-1"


def _event(
    event_id=EVENT_ID,
    candidate_id=OWNER,
    *,
    status=ScheduleStatus.PROPOSED,
    version=1,
):
    start = datetime(2026, 12, 8, 9, 0, tzinfo=timezone.utc)
    return ScheduleEvent(
        event_id=event_id,
        candidate_id=candidate_id,
        employer_name="Sandbox GmbH",
        event_type=ScheduleEventType.INTERVIEW,
        location="Sandbox video room",
        start_at=start,
        end_at=start + timedelta(hours=1),
        timezone_name="Europe/Berlin",
        status=status,
        provider_event_id="fake-" + event_id,
        application_id="application-1",
        version=version,
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
        "decision": "decline",
        **overrides,
    }


class Candidate:
    def __init__(self, path, plan=None):
        self.path = str(path)
        self.app = CandidateScheduleDeclineWSGIApp(
            database_path=self.path,
            resolve_bearer=TOKENS.get,
            outcome_plan=plan,
        )

    def call(
        self,
        route,
        *,
        token="owner-token",
        method="POST",
        body=None,
        raw=None,
        query="",
        content_type="application/json",
    ):
        encoded = raw if raw is not None else json.dumps(body).encode("utf-8")
        statuses = []
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": route,
            "QUERY_STRING": query,
            "CONTENT_TYPE": content_type,
            "CONTENT_LENGTH": str(len(encoded)),
            "wsgi.input": io.BytesIO(encoded),
        }
        if token is not None:
            environ["HTTP_AUTHORIZATION"] = "Bearer " + token
        payload = b"".join(
            self.app(environ, lambda status, headers: statuses.append(status))
        )
        return statuses[0], json.loads(payload)

    def decline(self, body=None, *, raw=None, token="owner-token"):
        return self.call(
            CandidateScheduleDeclineWSGIApp.DECLINE_PATH,
            token=token,
            body=_body() if body is None and raw is None else body,
            raw=raw,
        )

    def pending(self, *, event_id=EVENT_ID, token="owner-token", query=None):
        return self.call(
            CandidateScheduleDeclineWSGIApp.PENDING_PATH,
            token=token,
            method="GET",
            body={},
            query=query if query is not None else "event_id=" + event_id,
        )

    def reconcile(self, completed, *, state_version=1, token="owner-token", verified=True):
        return self.call(
            CandidateScheduleDeclineWSGIApp.RECONCILIATION_PATH,
            token=token,
            body={
                "schema_version": 1,
                "event_id": EVENT_ID,
                "state_version": state_version,
                "completed": completed,
                "independently_verified": verified,
            },
        )


def _effect_count(path):
    connection = sqlite3.connect(path)
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sandbox_calendar_effects'"
        ).fetchone()
        if exists is None:
            return 0
        return connection.execute(
            "SELECT COUNT(*) FROM sandbox_calendar_effects"
        ).fetchone()[0]
    finally:
        connection.close()


def _stored_event(path, event_id=EVENT_ID):
    database = SQLiteDatabase(str(path))
    try:
        return SQLiteSchedulingRepository(database).get(event_id)
    finally:
        database.close()


def test_candidate_declines_owned_interview_and_replays_after_restart(tmp_path):
    path = tmp_path / "candidate-decline.sqlite"
    _seed(path, _event())
    first = Candidate(path)
    status, result = first.decline()
    assert status == "200 OK"
    assert result["outcome"] == "completed"
    assert result["event"]["event_id"] == EVENT_ID
    assert result["event"]["status"] == "declined"
    assert result["event"]["state_version"] == 2
    assert _effect_count(path) == 1

    restarted = Candidate(path)
    replay_status, replay = restarted.decline()
    assert replay_status == "200 OK"
    assert replay == result
    assert _effect_count(path) == 1
    assert _stored_event(path).status == ScheduleStatus.DECLINED


def test_foreign_event_is_indistinguishable_from_missing_and_never_reaches_provider(tmp_path):
    path = tmp_path / "foreign-decline.sqlite"
    _seed(path, _event(candidate_id="candidate-2"))
    candidate = Candidate(path)
    assert candidate.decline() == ("404 Not Found", {"error": "not_found"})
    assert _effect_count(path) == 0


def test_stale_or_ineligible_event_is_rejected_before_provider_effect(tmp_path):
    path = tmp_path / "stale-decline.sqlite"
    _seed(path, _event(version=2))
    candidate = Candidate(path)
    assert candidate.decline() == ("409 Conflict", {"error": "approval_conflict"})
    assert _effect_count(path) == 0


def test_timeout_after_decline_effect_requires_restart_and_provider_evidence(tmp_path):
    path = tmp_path / "decline-timeout.sqlite"
    _seed(path, _event())
    first = Candidate(
        path,
        lambda _key, _attempt: SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT,
    )
    status, result = first.decline()
    assert status == "202 Accepted"
    assert result["outcome"] == "reconciliation_required"
    assert _effect_count(path) == 1

    restarted = Candidate(path)
    pending_status, pending = restarted.pending()
    assert pending_status == "200 OK"
    assert pending == {
        "schema_version": 1,
        "outcome": "pending",
        "event_id": EVENT_ID,
        "state_version": 1,
    }
    status, result = restarted.reconcile(True)
    assert status == "200 OK"
    assert result["outcome"] == "completed"
    assert result["event"]["status"] == "declined"
    assert result["event"]["state_version"] == 2
    assert _effect_count(path) == 1


def test_interruption_before_effect_requires_no_effect_evidence_before_same_key_retry(tmp_path):
    path = tmp_path / "decline-interrupted.sqlite"
    _seed(path, _event())
    first = Candidate(
        path,
        lambda _key, _attempt: SandboxCalendarOutcome.INTERRUPTED_BEFORE_EFFECT,
    )
    status, result = first.decline()
    assert status == "202 Accepted"
    assert result["outcome"] == "reconciliation_required"
    assert _effect_count(path) == 0

    restarted = Candidate(path)
    assert restarted.pending()[1]["outcome"] == "pending"
    status, result = restarted.reconcile(False)
    assert status == "200 OK"
    assert result["outcome"] == "no_effect"
    assert result["event"]["status"] == "proposed"
    assert _effect_count(path) == 0

    retry_status, retry = restarted.decline()
    assert retry_status == "200 OK"
    assert retry["outcome"] == "completed"
    assert retry["event"]["status"] == "declined"
    assert _effect_count(path) == 1


def test_definite_no_effect_retries_only_the_same_durable_decline_intent(tmp_path):
    path = tmp_path / "decline-no-effect.sqlite"
    _seed(path, _event())
    candidate = Candidate(
        path,
        lambda _key, attempt: (
            SandboxCalendarOutcome.NO_EFFECT
            if attempt == 1 else SandboxCalendarOutcome.SUCCESS
        ),
    )
    status, result = candidate.decline()
    assert status == "200 OK"
    assert result["outcome"] == "no_effect"
    assert result["event"]["status"] == "proposed"
    assert _effect_count(path) == 0

    retry_status, retry = candidate.decline()
    assert retry_status == "200 OK"
    assert retry["outcome"] == "completed"
    assert retry["event"]["status"] == "declined"
    assert _effect_count(path) == 1


@pytest.mark.parametrize(
    "body",
    [
        _body(decision="approve"),
        _body(extra="unexpected"),
        _body(state_version=True),
        _body(state_version=0),
        _body(state_version=1.5),
    ],
)
def test_decline_rejects_unapproved_or_malformed_body_before_provider_effect(tmp_path, body):
    path = tmp_path / "decline-invalid.sqlite"
    _seed(path, _event())
    candidate = Candidate(path)
    status, result = candidate.decline(body)
    assert status == "400 Bad Request"
    assert result == {"error": "invalid_request"}
    assert _effect_count(path) == 0


def test_decline_rejects_extreme_decimal_version_and_duplicate_json_members(tmp_path):
    path = tmp_path / "decline-json.sqlite"
    _seed(path, _event())
    candidate = Candidate(path)
    raw = (
        b'{"schema_version":1,"event_id":"' + EVENT_ID.encode() +
        b'","state_version":1e1000000000,"decision":"decline"}'
    )
    assert candidate.decline(raw=raw) == (
        "400 Bad Request", {"error": "invalid_request"}
    )
    duplicate = (
        b'{"schema_version":1,"event_id":"' + EVENT_ID.encode() +
        b'","state_version":1,"state_version":1,"decision":"decline"}'
    )
    assert candidate.decline(raw=duplicate) == (
        "400 Bad Request", {"error": "invalid_request"}
    )
    assert _effect_count(path) == 0


def test_reconciliation_requires_explicit_version_bound_attestation(tmp_path):
    path = tmp_path / "decline-attestation.sqlite"
    _seed(path, _event())
    candidate = Candidate(
        path,
        lambda _key, _attempt: SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT,
    )
    assert candidate.decline()[0] == "202 Accepted"
    assert candidate.reconcile(True, verified=False) == (
        "400 Bad Request", {"error": "invalid_request"}
    )
    assert _stored_event(path).status == ScheduleStatus.PROPOSED
    assert _effect_count(path) == 1


def test_empty_pending_lookup_is_read_only_and_owner_scoped(tmp_path):
    path = tmp_path / "decline-pending-read-only.sqlite"
    _seed(path, _event())
    baseline = path.read_bytes()
    candidate = Candidate(path)
    assert candidate.pending() == (
        "200 OK",
        {"schema_version": 1, "outcome": "none", "event_id": EVENT_ID},
    )
    assert candidate.pending(token="foreign-token") == (
        "404 Not Found", {"error": "not_found"}
    )
    assert path.read_bytes() == baseline


def test_pending_lookup_rejects_duplicate_or_malformed_query_parameters(tmp_path):
    path = tmp_path / "decline-pending-query.sqlite"
    _seed(path, _event())
    candidate = Candidate(path)
    assert candidate.pending(query="event_id=" + EVENT_ID + "&event_id=" + EVENT_ID)[0] == "400 Bad Request"
    assert candidate.pending(query="event_id=%ZZ")[0] == "400 Bad Request"
    assert candidate.pending(query="other=" + EVENT_ID)[0] == "400 Bad Request"


def test_authentication_precedes_missing_database_access(tmp_path):
    path = tmp_path / "does-not-exist.sqlite"
    candidate = Candidate(path)
    status, result = candidate.decline(token=None)
    assert status == "401 Unauthorized"
    assert result == {"error": "unauthorized"}
    assert not path.exists()


def test_openapi_and_dashboard_publish_candidate_decline_workflow():
    document = candidate_approval_openapi_document()
    assert CandidateScheduleDeclineWSGIApp.DECLINE_PATH in document["paths"]
    assert CandidateScheduleDeclineWSGIApp.RECONCILIATION_PATH in document["paths"]
    assert CandidateScheduleDeclineWSGIApp.PENDING_PATH in document["paths"]
    html = candidate_sandbox_html().decode("utf-8")
    for identifier in (
        "decline-confirmed",
        "decline-event",
        "decline-reconciliation-verified",
        "confirm-decline-completed",
        "confirm-decline-no-effect",
    ):
        assert 'id="' + identifier + '"' in html


def test_candidate_sandbox_browser_can_decline_an_owned_interview(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    path = tmp_path / "decline-browser.sqlite"
    _seed(path, _event())
    app = build_candidate_sandbox_app_from_env(
        resolve_bearer=lambda token: OWNER if token == "owner-token" else None,
        environ={"CAREER_AGENT_DB_PATH": str(path), **ENV},
    )

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, format, *args):
            pass

    server = make_server("127.0.0.1", 0, app, handler_class=QuietHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/sandbox/candidate")
            page.locator("#token").fill("owner-token")
            page.locator("#list-events").click()
            page.get_by_role("button", name="View event 1: " + EVENT_ID).wait_for()
            page.get_by_role("button", name="View event 1: " + EVENT_ID).click()
            page.locator("#event-detail").get_by_text('"state_version": 1').wait_for()
            assert not page.locator("#decline-confirmed").is_disabled()
            assert page.locator("#decline-event").is_disabled()
            page.locator("#decline-confirmed").check()
            page.locator("#decline-event").click()
            page.locator("#decline-state").get_by_text(
                "Interview declined in the sandbox."
            ).wait_for()
            assert '"status": "declined"' in page.locator("#event-detail").text_content()
            assert '"state_version": 2' in page.locator("#event-detail").text_content()
            assert page.locator("#decline-event").is_disabled()
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
        app.close()
