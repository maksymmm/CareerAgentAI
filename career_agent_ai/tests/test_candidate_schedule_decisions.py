"""HTTP, SQLite restart, and browser regressions for candidate interview decline."""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
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
from career_agent_ai.application.scheduling import (
    ScheduleEvent,
    ScheduleEventType,
    ScheduleStatus,
    SchedulingService,
)
from career_agent_ai.application.external_actions.external_action_operation import (
    ExternalActionStatus,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import (
    SQLiteExternalActionOperationRepository,
)
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
    open_ended=False,
):
    start = datetime(2026, 12, 8, 9, 0, tzinfo=timezone.utc)
    return ScheduleEvent(
        event_id=event_id,
        candidate_id=candidate_id,
        employer_name="Sandbox GmbH",
        event_type=ScheduleEventType.INTERVIEW,
        location="Sandbox video room",
        start_at=start,
        end_at=None if open_ended else start + timedelta(hours=1),
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

    def pending_page(self, *, limit=25, after=None, token="owner-token", query=None):
        encoded_query = query
        if encoded_query is None:
            encoded_query = "limit=" + str(limit)
            if after is not None:
                encoded_query += "&after=" + after
        return self.call(
            CandidateScheduleDeclineWSGIApp.PENDING_COLLECTION_PATH,
            token=token,
            method="GET",
            body={},
            query=encoded_query,
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
    assert restarted.pending() == (
        "200 OK",
        {"schema_version": 1, "outcome": "none", "event_id": EVENT_ID},
    )

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
    assert CandidateScheduleDeclineWSGIApp.PENDING_COLLECTION_PATH in document["paths"]
    html = candidate_sandbox_html().decode("utf-8")
    for identifier in (
        "load-decline-intents",
        "pending-decline-intents",
        "decline-intents-state",
        "next-decline-intents",
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


@pytest.mark.parametrize(
    "malformed_payload",
    [
        "[]",
        '{"unexpected":"shape"}',
        '{"event_id":null}',
        '{"event_id":"bad id"}',
        '{"event_id":"candidate-decline-interview-1","event_id":"other"}',
    ],
)
def test_pending_decline_fails_closed_on_unmatchable_ambiguous_payload(
    tmp_path, malformed_payload
):
    path = tmp_path / "decline-malformed-pending.sqlite"
    _seed(path, _event())
    first = Candidate(
        path,
        lambda _key, _attempt: SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT,
    )
    assert first.decline()[0] == "202 Accepted"
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "UPDATE external_action_operations SET payload_json = ?",
            (malformed_payload,),
        )
        connection.commit()
    finally:
        connection.close()
    restarted = Candidate(path)
    status, result = restarted.pending()
    assert status == "409 Conflict"
    assert result == {"error": "approval_conflict"}
    assert _effect_count(path) == 1


def test_decline_rejects_concurrent_version_advance_before_intent_or_provider(
    tmp_path, monkeypatch
):
    path = tmp_path / "decline-concurrent-version.sqlite"
    _seed(path, _event())
    original_get_event = SchedulingService.get_event
    advanced = False

    def concurrent_writer(service, event_id):
        nonlocal advanced
        if not advanced:
            advanced = True
            connection = sqlite3.connect(path)
            try:
                cursor = connection.execute(
                    """
                    UPDATE scheduling_events
                    SET status = ?, version = version + 1, updated_at = ?
                    WHERE event_id = ? AND version = ?
                    """,
                    (
                        ScheduleStatus.RESCHEDULE_REQUESTED.value,
                        datetime.now(timezone.utc).isoformat(),
                        event_id,
                        1,
                    ),
                )
                assert cursor.rowcount == 1
                connection.commit()
            finally:
                connection.close()
        return original_get_event(service, event_id)

    monkeypatch.setattr(SchedulingService, "get_event", concurrent_writer)
    candidate = Candidate(path)
    status, result = candidate.decline()

    assert advanced
    assert status == "409 Conflict"
    assert result == {"error": "approval_conflict"}
    assert _effect_count(path) == 0
    connection = sqlite3.connect(path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM external_action_operations"
        ).fetchone()[0] == 0
    finally:
        connection.close()
    event = _stored_event(path)
    assert event.version == 2
    assert event.status == ScheduleStatus.RESCHEDULE_REQUESTED


@pytest.mark.parametrize(
    ("field", "tampered_value"),
    [
        ("event_id", "another-valid-event"),
        ("candidate_id", "candidate-2"),
        ("event_version", 2),
    ],
)
def test_pending_decline_rejects_operation_identity_tampering_before_filtering(
    tmp_path, field, tampered_value
):
    path = tmp_path / "decline-operation-identity-tamper.sqlite"
    _seed(path, _event())
    first = Candidate(
        path,
        lambda _key, _attempt: SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT,
    )
    assert first.decline()[0] == "202 Accepted"

    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            """
            SELECT operation_id, payload_json
            FROM external_action_operations
            WHERE action_type = 'calendar.decline'
            """
        ).fetchone()
        assert row is not None
        operation_id, payload_json = row
        payload = json.loads(payload_json)
        payload[field] = tampered_value
        connection.execute(
            "UPDATE external_action_operations SET payload_json = ? WHERE operation_id = ?",
            (json.dumps(payload, separators=(",", ":")), operation_id),
        )
        connection.commit()
    finally:
        connection.close()

    restarted = Candidate(path)
    assert restarted.pending() == (
        "409 Conflict",
        {"error": "approval_conflict"},
    )
    assert _effect_count(path) == 1



def test_pending_decline_rejects_coherently_rewritten_row_with_stale_active_pointer(
    tmp_path,
):
    path = tmp_path / "decline-active-pointer-rewrite.sqlite"
    _seed(path, _event())
    first = Candidate(
        path,
        lambda _key, _attempt: SandboxCalendarOutcome.INTERRUPTED_BEFORE_EFFECT,
    )
    assert first.decline()[0] == "202 Accepted"
    assert _effect_count(path) == 0

    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            """
            SELECT operation_id, payload_json
            FROM external_action_operations
            WHERE action_type = 'calendar.decline'
            """
        ).fetchone()
        assert row is not None
        original_operation_id, payload_json = row
        payload = json.loads(payload_json)
        payload["candidate_id"] = "candidate-2"
        payload["event_id"] = "another-valid-event"
        rewritten_operation_id = CandidateScheduleDeclineWSGIApp._operation_id(
            payload["candidate_id"], payload["event_id"], payload["event_version"]
        )
        connection.execute(
            """
            UPDATE external_action_operations
            SET operation_id = ?, payload_json = ?
            WHERE operation_id = ?
            """,
            (
                rewritten_operation_id,
                json.dumps(payload, separators=(",", ":")),
                original_operation_id,
            ),
        )
        active_operation_id = connection.execute(
            "SELECT active_action_operation_id FROM scheduling_events WHERE event_id = ?",
            (EVENT_ID,),
        ).fetchone()[0]
        assert active_operation_id == original_operation_id
        connection.commit()
    finally:
        connection.close()

    restarted = Candidate(path)
    assert restarted.pending() == (
        "409 Conflict",
        {"error": "approval_conflict"},
    )
    assert restarted.decline() == (
        "409 Conflict",
        {"error": "approval_conflict"},
    )
    assert _effect_count(path) == 0


def test_pending_decline_rejects_dangling_active_pointer_without_recreating_operation(
    tmp_path,
):
    path = tmp_path / "decline-active-pointer-dangling.sqlite"
    _seed(path, _event())
    first = Candidate(
        path,
        lambda _key, _attempt: SandboxCalendarOutcome.INTERRUPTED_BEFORE_EFFECT,
    )
    assert first.decline()[0] == "202 Accepted"
    assert _effect_count(path) == 0

    connection = sqlite3.connect(path)
    try:
        operation_id = connection.execute(
            "SELECT active_action_operation_id FROM scheduling_events WHERE event_id = ?",
            (EVENT_ID,),
        ).fetchone()[0]
        assert operation_id
        connection.execute(
            "DELETE FROM external_action_operations WHERE operation_id = ?",
            (operation_id,),
        )
        connection.commit()
    finally:
        connection.close()

    restarted = Candidate(path)
    assert restarted.pending() == (
        "409 Conflict",
        {"error": "approval_conflict"},
    )
    assert restarted.decline() == (
        "409 Conflict",
        {"error": "approval_conflict"},
    )
    assert _effect_count(path) == 0


def test_pending_decline_rejects_ambiguous_row_without_active_pointer(tmp_path):
    path = tmp_path / "decline-active-pointer-missing.sqlite"
    _seed(path, _event())
    first = Candidate(
        path,
        lambda _key, _attempt: SandboxCalendarOutcome.INTERRUPTED_BEFORE_EFFECT,
    )
    assert first.decline()[0] == "202 Accepted"
    assert _effect_count(path) == 0

    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            UPDATE scheduling_events
            SET active_action_operation_id = NULL
            WHERE event_id = ?
            """,
            (EVENT_ID,),
        )
        connection.commit()
    finally:
        connection.close()

    restarted = Candidate(path)
    assert restarted.pending() == (
        "409 Conflict",
        {"error": "approval_conflict"},
    )
    assert restarted.decline() == (
        "409 Conflict",
        {"error": "approval_conflict"},
    )
    assert _effect_count(path) == 0


def test_pending_decline_scan_uses_composite_index_without_temporary_sort(tmp_path):
    path = tmp_path / "decline-pending-query-plan.sqlite"
    _seed(path, _event())
    candidate = Candidate(
        path,
        lambda _key, _attempt: SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT,
    )
    assert candidate.decline()[0] == "202 Accepted"

    connection = sqlite3.connect(path)
    try:
        plan = connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT operation_id, payload_json
            FROM external_action_operations
            WHERE action_type = ?
              AND status IN (?, ?)
            LIMIT 1001
            """,
            (
                "calendar.decline",
                "in_progress",
                "reconciliation_required",
            ),
        ).fetchall()
    finally:
        connection.close()

    details = tuple(str(row[3]) for row in plan)
    assert any("idx_external_actions_type_status" in detail for detail in details)
    assert not any("TEMP B-TREE" in detail.upper() for detail in details)

@pytest.mark.parametrize(
    ("field", "replacement", "remove_field"),
    [
        ("event_start_at", None, True),
        ("event_end_at", None, True),
        ("event_timezone_name", "Invalid/Timezone", False),
        ("event_status", "accepted", False),
    ],
)
def test_pending_prepared_decline_rejects_malformed_source_snapshot(
    tmp_path, field, replacement, remove_field
):
    path = tmp_path / "decline-prepared-source-corrupt.sqlite"
    _seed(path, _event())
    candidate = Candidate(
        path,
        lambda _key, _attempt: SandboxCalendarOutcome.INTERRUPTED_BEFORE_EFFECT,
    )
    assert candidate.decline()[0] == "202 Accepted"
    assert candidate.pending()[1]["outcome"] == "pending"
    assert candidate.reconcile(False)[1]["outcome"] == "no_effect"

    connection = sqlite3.connect(path)
    try:
        operation_id = connection.execute(
            "SELECT active_action_operation_id FROM scheduling_events WHERE event_id = ?",
            (EVENT_ID,),
        ).fetchone()[0]
        assert operation_id
        row = connection.execute(
            "SELECT payload_json, status FROM external_action_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        assert row is not None
        payload_json, status = row
        assert status == "prepared"
        payload = json.loads(payload_json)
        if remove_field:
            payload.pop(field, None)
        else:
            payload[field] = replacement
        connection.execute(
            "UPDATE external_action_operations SET payload_json = ? WHERE operation_id = ?",
            (json.dumps(payload, separators=(",", ":")), operation_id),
        )
        connection.commit()
    finally:
        connection.close()

    restarted = Candidate(path)
    assert restarted.pending() == (
        "409 Conflict",
        {"error": "approval_conflict"},
    )
    assert restarted.decline() == (
        "409 Conflict",
        {"error": "approval_conflict"},
    )
    assert _effect_count(path) == 0

def test_decline_retry_rejects_missing_nullable_end_snapshot_for_open_ended_event(tmp_path):
    path = tmp_path / "decline-open-ended-source-corrupt.sqlite"
    _seed(path, _event(open_ended=True))
    candidate = Candidate(
        path,
        lambda _key, _attempt: SandboxCalendarOutcome.INTERRUPTED_BEFORE_EFFECT,
    )
    assert candidate.decline()[0] == "202 Accepted"
    assert candidate.reconcile(False)[1]["outcome"] == "no_effect"

    connection = sqlite3.connect(path)
    try:
        operation_id = connection.execute(
            "SELECT active_action_operation_id FROM scheduling_events WHERE event_id = ?",
            (EVENT_ID,),
        ).fetchone()[0]
        assert operation_id
        row = connection.execute(
            "SELECT payload_json, status FROM external_action_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        assert row is not None
        payload_json, status = row
        assert status == "prepared"
        payload = json.loads(payload_json)
        assert payload["event_end_at"] is None
        payload.pop("event_end_at")
        connection.execute(
            "UPDATE external_action_operations SET payload_json = ? WHERE operation_id = ?",
            (json.dumps(payload, separators=(",", ":")), operation_id),
        )
        connection.commit()
    finally:
        connection.close()

    restarted = Candidate(path)
    assert restarted.decline() == (
        "409 Conflict",
        {"error": "approval_conflict"},
    )
    assert _effect_count(path) == 0

def test_open_ended_decline_retry_accepts_explicit_null_end_snapshot(tmp_path):
    path = tmp_path / "decline-open-ended-source-valid.sqlite"
    _seed(path, _event(open_ended=True))
    candidate = Candidate(
        path,
        lambda _key, attempt: (
            SandboxCalendarOutcome.INTERRUPTED_BEFORE_EFFECT
            if attempt == 1 else SandboxCalendarOutcome.SUCCESS
        ),
    )
    assert candidate.decline()[0] == "202 Accepted"
    assert candidate.reconcile(False)[1]["outcome"] == "no_effect"

    connection = sqlite3.connect(path)
    try:
        operation_id = connection.execute(
            "SELECT active_action_operation_id FROM scheduling_events WHERE event_id = ?",
            (EVENT_ID,),
        ).fetchone()[0]
        payload_json = connection.execute(
            "SELECT payload_json FROM external_action_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()[0]
        assert json.loads(payload_json)["event_end_at"] is None
    finally:
        connection.close()

    status, result = candidate.decline()
    assert status == "200 OK"
    assert result["outcome"] == "completed"
    assert result["event"]["status"] == "declined"
    assert _effect_count(path) == 1


@pytest.mark.parametrize("leave_in_progress", [False, True])
def test_pending_recovers_decline_after_event_commit_before_operation_completion(
    tmp_path, monkeypatch, leave_in_progress
):
    path = tmp_path / "decline-event-committed-operation-pending.sqlite"
    _seed(path, _event())
    original_transition = SQLiteExternalActionOperationRepository.transition

    def interrupt_operation_completion(
        repository, operation_id, expected_status, status, **kwargs
    ):
        if (
            expected_status == ExternalActionStatus.IN_PROGRESS
            and status == ExternalActionStatus.SUCCEEDED
        ):
            raise sqlite3.OperationalError("simulated terminal status write failure")
        if (
            leave_in_progress
            and expected_status == ExternalActionStatus.IN_PROGRESS
            and status == ExternalActionStatus.RECONCILIATION_REQUIRED
        ):
            raise sqlite3.OperationalError("simulated process loss before classification")
        return original_transition(
            repository, operation_id, expected_status, status, **kwargs
        )

    with monkeypatch.context() as patch:
        patch.setattr(
            SQLiteExternalActionOperationRepository,
            "transition",
            interrupt_operation_completion,
        )
        first_status, first_result = Candidate(path).decline()

    assert first_status == (
        "503 Service Unavailable" if leave_in_progress else "202 Accepted"
    )
    if not leave_in_progress:
        assert first_result["outcome"] == "reconciliation_required"
    committed_event = _stored_event(path)
    assert committed_event.status == ScheduleStatus.DECLINED
    assert committed_event.version == 2

    connection = sqlite3.connect(path)
    try:
        active_operation_id = connection.execute(
            "SELECT active_action_operation_id FROM scheduling_events WHERE event_id = ?",
            (EVENT_ID,),
        ).fetchone()[0]
        operation_id, operation_status = connection.execute(
            "SELECT operation_id, status FROM external_action_operations"
        ).fetchone()
        assert active_operation_id is None
        assert operation_id == CandidateScheduleDeclineWSGIApp._operation_id(
            OWNER, EVENT_ID, 1
        )
        assert operation_status == (
            ExternalActionStatus.IN_PROGRESS.value
            if leave_in_progress
            else ExternalActionStatus.RECONCILIATION_REQUIRED.value
        )
    finally:
        connection.close()
    assert _effect_count(path) == 1

    restarted = Candidate(path)
    page_status, page = restarted.pending_page()
    assert page_status == "200 OK"
    assert page["schema_version"] == 1
    assert page["next_after"] is None
    assert len(page["items"]) == 1
    assert page["items"][0]["event"]["event_id"] == EVENT_ID
    assert page["items"][0]["state_version"] == 1
    assert page["items"][0]["operation_status"] == operation_status
    pending_status, pending = restarted.pending()
    assert pending_status == "200 OK"
    assert pending == {
        "schema_version": 1,
        "outcome": "pending",
        "event_id": EVENT_ID,
        "state_version": 1,
    }

    connection = sqlite3.connect(path)
    try:
        recovered_status = connection.execute(
            "SELECT status FROM external_action_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()[0]
        assert recovered_status == ExternalActionStatus.RECONCILIATION_REQUIRED.value
    finally:
        connection.close()

    reconciled_status, reconciled = restarted.reconcile(True)
    assert reconciled_status == "200 OK"
    assert reconciled["outcome"] == "completed"
    assert reconciled["event"]["status"] == "declined"
    assert reconciled["event"]["state_version"] == 2
    assert _effect_count(path) == 1


def test_pending_rejects_detached_ambiguous_decline_before_event_transition(tmp_path):
    path = tmp_path / "decline-detached-before-event-commit.sqlite"
    _seed(path, _event())
    candidate = Candidate(
        path,
        lambda _key, _attempt: SandboxCalendarOutcome.INTERRUPTED_BEFORE_EFFECT,
    )
    assert candidate.decline()[0] == "202 Accepted"

    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "UPDATE scheduling_events SET active_action_operation_id = NULL "
            "WHERE event_id = ?",
            (EVENT_ID,),
        )
        connection.commit()
    finally:
        connection.close()

    assert Candidate(path).pending() == (
        "409 Conflict",
        {"error": "approval_conflict"},
    )
    assert _stored_event(path).status == ScheduleStatus.PROPOSED
    assert _effect_count(path) == 0


def test_pending_decline_collection_is_owner_scoped_read_only_and_hides_private_ids(tmp_path):
    path = tmp_path / "decline-intent-owner-scope.sqlite"
    owner_event = _event(event_id="decline-owner-event")
    foreign_event = _event(event_id="decline-foreign-event", candidate_id="candidate-2")
    _seed(path, owner_event, foreign_event)

    def timeout_after_effect(_key, _attempt):
        return SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT

    candidate = Candidate(path, timeout_after_effect)
    assert candidate.decline(_body(event_id=owner_event.event_id))[0] == "202 Accepted"
    assert candidate.decline(
        _body(event_id=foreign_event.event_id), token="foreign-token"
    )[0] == "202 Accepted"

    baseline = path.read_bytes()
    owner_status, owner_page = candidate.pending_page()
    assert owner_status == "200 OK"
    assert owner_page["schema_version"] == 1
    assert owner_page["next_after"] is None
    assert len(owner_page["items"]) == 1
    owner_item = owner_page["items"][0]
    assert owner_item["event"]["event_id"] == owner_event.event_id
    assert owner_item["state_version"] == 1
    assert owner_item["operation_status"] == "reconciliation_required"
    serialized = json.dumps(owner_item)
    for private_field in (
        "candidate_id", "application_id", "provider_event_id", "operation_id",
        "active_action_operation_id",
    ):
        assert private_field not in serialized
    assert path.read_bytes() == baseline

    foreign_status, foreign_page = candidate.pending_page(token="foreign-token")
    assert foreign_status == "200 OK"
    assert [item["event"]["event_id"] for item in foreign_page["items"]] == [
        foreign_event.event_id
    ]
    assert path.read_bytes() == baseline
    assert _effect_count(path) == 2


def test_pending_decline_collection_uses_stable_keyset_pagination(tmp_path):
    path = tmp_path / "decline-intent-pagination.sqlite"
    first_event = _event(event_id="decline-page-a")
    second_event = _event(event_id="decline-page-b")
    _seed(path, first_event, second_event)
    candidate = Candidate(
        path,
        lambda _key, _attempt: SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT,
    )
    assert candidate.decline(_body(event_id=first_event.event_id))[0] == "202 Accepted"
    assert candidate.decline(_body(event_id=second_event.event_id))[0] == "202 Accepted"

    first_status, first_page = candidate.pending_page(limit=1)
    assert first_status == "200 OK"
    assert [item["event"]["event_id"] for item in first_page["items"]] == [
        first_event.event_id
    ]
    assert first_page["next_after"] == first_event.event_id

    second_status, second_page = candidate.pending_page(
        limit=1, after=first_page["next_after"]
    )
    assert second_status == "200 OK"
    assert [item["event"]["event_id"] for item in second_page["items"]] == [
        second_event.event_id
    ]
    assert second_page["next_after"] is None
    assert _effect_count(path) == 2


def test_pending_decline_collection_rejects_duplicate_and_unbounded_queries(tmp_path):
    path = tmp_path / "decline-intent-query.sqlite"
    _seed(path, _event())
    candidate = Candidate(path)
    assert candidate.pending_page(query="limit=1&limit=2")[0] == "400 Bad Request"
    assert candidate.pending_page(query="limit=101")[0] == "400 Bad Request"
    assert candidate.pending_page(query="after=%ZZ")[0] == "400 Bad Request"


def test_pending_decline_collection_authenticates_before_opening_missing_storage(tmp_path):
    path = tmp_path / "missing-decline-intents.sqlite"
    candidate = Candidate(path)
    assert candidate.pending_page(token=None) == (
        "401 Unauthorized", {"error": "unauthorized"}
    )
    assert not path.exists()


def test_candidate_browser_discovers_and_reconciles_pending_decline_after_restart(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    path = tmp_path / "decline-intent-browser-restart.sqlite"
    _seed(path, _event())
    provider_calls = []

    def first_plan(key, attempt):
        provider_calls.append((key, attempt))
        return SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT

    def unexpected_retry(*_args):
        raise AssertionError("provider execution must not replay during recovery")

    def runtime_dispatch(plan):
        runtime = build_candidate_sandbox_app_from_env(
            resolve_bearer=TOKENS.get,
            environ={"CAREER_AGENT_DB_PATH": str(path), **ENV},
        )
        decline = CandidateScheduleDeclineWSGIApp(
            database_path=str(path), resolve_bearer=TOKENS.get, outcome_plan=plan
        )

        def dispatch(environ, start_response):
            if environ.get("PATH_INFO") in CandidateScheduleDeclineWSGIApp.ROUTES:
                return decline(environ, start_response)
            return runtime(environ, start_response)

        return runtime, dispatch

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, format, *args):
            pass

    runtime1, app1 = runtime_dispatch(first_plan)
    server1 = make_server("127.0.0.1", 0, app1, handler_class=QuietHandler)
    thread1 = Thread(target=server1.serve_forever, daemon=True)
    thread1.start()
    first_url = f"http://127.0.0.1:{server1.server_port}/sandbox/candidate"

    with sync_playwright() as playwright:
        browser1 = playwright.chromium.launch(headless=True)
        try:
            page1 = browser1.new_page()
            page1.goto(first_url)
            page1.locator("#token").fill("owner-token")
            page1.locator("#list-events").click()
            view_button = page1.get_by_role("button", name="View event 1: " + EVENT_ID)
            view_button.wait_for()
            view_button.click()
            page1.locator("#decline-confirmed").check()
            page1.locator("#decline-event").click()
            page1.get_by_text("Decline is awaiting provider reconciliation.").wait_for()
        finally:
            browser1.close()
            server1.shutdown()
            thread1.join(timeout=10)
            server1.server_close()
            runtime1.close()

        runtime2, app2 = runtime_dispatch(unexpected_retry)
        server2 = make_server("127.0.0.1", 0, app2, handler_class=QuietHandler)
        thread2 = Thread(target=server2.serve_forever, daemon=True)
        thread2.start()
        browser2 = playwright.chromium.launch(headless=True)
        try:
            page2 = browser2.new_page()
            page2.goto(f"http://127.0.0.1:{server2.server_port}/sandbox/candidate")
            page2.locator("#token").fill("owner-token")
            page2.locator("#load-decline-intents").click()
            recover_button = page2.get_by_role(
                "button", name="Recover pending decline 1: " + EVENT_ID
            )
            recover_button.wait_for()
            recover_button.click()
            page2.get_by_text(
                "An ambiguous interview decline was restored from durable storage."
            ).wait_for()
            assert page2.locator("#decline-reconciliation-panel").is_enabled()
            page2.locator("#decline-reconciliation-verified").check()
            page2.locator("#confirm-decline-completed").click()
            page2.get_by_text(
                "Provider evidence confirmed the interview decline."
            ).wait_for()
            assert len(provider_calls) == 1
            assert _effect_count(path) == 1
            assert _stored_event(path).status == ScheduleStatus.DECLINED
            assert _stored_event(path).version == 2
        finally:
            browser2.close()
            server2.shutdown()
            thread2.join(timeout=10)
            server2.server_close()
            runtime2.close()


def test_ci_enforces_independent_coverage_gate_after_pytest():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    pytest_step = workflow.index("python -m pytest")
    coverage_gate = workflow.index("python -m coverage report --fail-under=90")
    assert coverage_gate > pytest_step
