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
from career_agent_ai.application.external_actions.external_action_operation import (
    ExternalActionOperation,
    ExternalActionStatus,
)
from career_agent_ai.application.api.candidate_sandbox_ui import candidate_sandbox_html
from career_agent_ai.application.runtime.composition import build_candidate_sandbox_app_from_env
from career_agent_ai.application.scheduling import ScheduleEvent, ScheduleEventType, ScheduleStatus
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
EVENT_ID = "interview-reschedule-1"
START = "2026-12-02T10:00:00+01:00"
END = "2026-12-02T11:00:00+01:00"
ZONE = "Europe/Berlin"
PENDING_START = datetime.fromisoformat(START).astimezone(timezone.utc).isoformat()
PENDING_END = datetime.fromisoformat(END).astimezone(timezone.utc).isoformat()


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


def _raw_state_version(body, literal):
    encoded = json.dumps(body, separators=(",", ":"))
    return encoded.replace('"state_version":1', f'"state_version":{literal}', 1).encode("utf-8")


class Candidate:
    def __init__(self, path, plan=None):
        self.path = str(path)
        self.app = build_candidate_sandbox_app_from_env(
            resolve_bearer=TOKENS.get,
            environ={"CAREER_AGENT_DB_PATH": self.path, **ENV},
            sandbox_calendar_outcome_plan=plan,
        )

    def call(self, route, *, token="owner-token", method="POST", body=None, raw=None,
             content_type="application/json", query=""):
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
        payload = b"".join(self.app(environ, lambda status, headers: statuses.append(status)))
        return statuses[0], json.loads(payload)

    def reschedule(self, body=None):
        return self.call(
            CandidateScheduleActionWSGIApp.RESCHEDULE_PATH,
            body=_body() if body is None else body,
        )

    def pending(self, event_id=EVENT_ID, *, token="owner-token", query=None):
        encoded_query = query if query is not None else "event_id=" + event_id
        return self.call(
            CandidateScheduleActionWSGIApp.PENDING_PATH,
            token=token, method="GET", body={}, query=encoded_query,
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


def _seed_pending_operation(path, *, start_at=START, end_at=END, final_status=None):
    event = _event()
    target_start = datetime.fromisoformat(start_at).astimezone(timezone.utc)
    target_end = (
        None if end_at is None
        else datetime.fromisoformat(end_at).astimezone(timezone.utc)
    )
    operation_id = CandidateScheduleActionWSGIApp._operation_id(
        OWNER, EVENT_ID, 1, target_start, target_end, ZONE
    )
    payload = {
        "event_id": EVENT_ID,
        "event_version": 1,
        "event_start_at": event.start_at.isoformat(),
        "event_end_at": event.end_at.isoformat(),
        "event_timezone_name": event.timezone_name,
        "event_status": event.status.value,
        "start_at": target_start.isoformat(),
        "end_at": None if target_end is None else target_end.isoformat(),
        "timezone_name": ZONE,
    }
    database = SQLiteDatabase(str(path))
    operations = SQLiteExternalActionOperationRepository(database)
    operations.create(ExternalActionOperation(
        operation_id=operation_id,
        action_type="calendar.reschedule",
        payload=payload,
    ))
    operations.transition(
        operation_id, ExternalActionStatus.PREPARED, ExternalActionStatus.IN_PROGRESS
    )
    if final_status == ExternalActionStatus.RECONCILIATION_REQUIRED:
        operations.transition(
            operation_id,
            ExternalActionStatus.IN_PROGRESS,
            ExternalActionStatus.RECONCILIATION_REQUIRED,
            error="Simulated ambiguous provider result.",
        )
    database.close()
    return operation_id


def _mark_operation_stale(path, operation_id, *, age=timedelta(minutes=10)):
    connection = sqlite3.connect(path)
    try:
        stale_at = (datetime.now(timezone.utc) - age).isoformat()
        connection.execute(
            "UPDATE external_action_operations SET updated_at = ? WHERE operation_id = ?",
            (stale_at, operation_id),
        )
        connection.commit()
    finally:
        connection.close()


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


def test_duplicate_reschedule_post_preserves_fresh_in_progress_operation(tmp_path):
    path = tmp_path / "duplicate-live-reschedule.sqlite"
    _seed(path, _event())
    operation_id = _seed_pending_operation(path)
    candidate = Candidate(path)

    status, result = candidate.reschedule()

    assert status == "202 Accepted"
    assert result == {
        "schema_version": 1,
        "outcome": "in_progress",
        "event_id": EVENT_ID,
    }
    database = SQLiteDatabase(str(path))
    try:
        operation = SQLiteExternalActionOperationRepository(database).get(operation_id)
        assert operation.status == ExternalActionStatus.IN_PROGRESS
    finally:
        database.close()
    assert _effect_count(path) == 0
    candidate.close()


@pytest.mark.parametrize(
    "final_status,expected_status",
    [
        (None, ExternalActionStatus.IN_PROGRESS),
        (ExternalActionStatus.RECONCILIATION_REQUIRED, ExternalActionStatus.RECONCILIATION_REQUIRED),
    ],
)
def test_distinct_target_post_is_blocked_by_existing_ambiguous_event_operation(
    tmp_path, final_status, expected_status
):
    path = tmp_path / "distinct-target-blocked.sqlite"
    _seed(path, _event())
    operation_id = _seed_pending_operation(path, final_status=final_status)
    candidate = Candidate(path)
    different_target = _body(
        start_at="2026-12-02T12:00:00+01:00",
        end_at="2026-12-02T13:00:00+01:00",
    )

    status, result = candidate.reschedule(different_target)

    assert status == "202 Accepted"
    assert result == {
        "schema_version": 1,
        "outcome": "blocked",
        "event_id": EVENT_ID,
    }
    database = SQLiteDatabase(str(path))
    try:
        operation = SQLiteExternalActionOperationRepository(database).get(operation_id)
        assert operation.status == expected_status
    finally:
        database.close()
    assert _effect_count(path) == 0
    if expected_status == ExternalActionStatus.RECONCILIATION_REQUIRED:
        pending_status, pending = candidate.pending()
        assert pending_status == "200 OK"
        assert pending["outcome"] == "pending"
        assert pending["start_at"] == PENDING_START
        assert pending["end_at"] == PENDING_END
    candidate.close()


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


@pytest.mark.parametrize("literal", ["1e1000000000", "1" + "0" * 5000])
def test_reschedule_rejects_extreme_state_version_without_integer_expansion(tmp_path, literal):
    path = tmp_path / "extreme-state-version.sqlite"
    _seed(path, _event())
    candidate = Candidate(path)
    status, result = candidate.call(
        CandidateScheduleActionWSGIApp.RESCHEDULE_PATH,
        raw=_raw_state_version(_body(), literal),
    )
    assert (status, result) == ("400 Bad Request", {"error": "invalid_request"})
    assert _effect_count(path) == 0
    candidate.close()


def test_reconciliation_rejects_extreme_state_version_before_provider_resolution(tmp_path):
    path = tmp_path / "extreme-reconciliation-version.sqlite"
    _seed(path, _event())
    candidate = Candidate(
        path, lambda _key, _attempt: SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT
    )
    assert candidate.reschedule()[0] == "202 Accepted"
    request = {
        key: _body()[key]
        for key in ("schema_version", "event_id", "state_version", "start_at", "end_at", "timezone_name")
    }
    request.update(completed=True, independently_verified=True)
    status, result = candidate.call(
        CandidateScheduleActionWSGIApp.RECONCILIATION_PATH,
        raw=_raw_state_version(request, "1e1000000000"),
    )
    assert (status, result) == ("400 Bad Request", {"error": "invalid_request"})
    assert _effect_count(path) == 1
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



def test_pending_reschedule_intent_is_restored_after_restart_without_provider_retry(tmp_path):
    path = tmp_path / "pending-recovery.sqlite"
    _seed(path, _event())
    first = Candidate(
        path, lambda _key, _attempt: SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT
    )
    status, result = first.reschedule()
    assert status == "202 Accepted"
    assert result["outcome"] == "reconciliation_required"
    assert _effect_count(path) == 1
    first.close()

    restarted = Candidate(path)
    status, pending = restarted.pending()
    assert status == "200 OK"
    assert pending == {
        "schema_version": 1,
        "outcome": "pending",
        "event_id": EVENT_ID,
        "state_version": 1,
        "start_at": PENDING_START,
        "end_at": PENDING_END,
        "timezone_name": ZONE,
    }
    assert _effect_count(path) == 1
    recovered_request = _body(
        state_version=pending["state_version"],
        start_at=pending["start_at"],
        end_at=pending["end_at"],
        timezone_name=pending["timezone_name"],
    )
    status, reconciled = restarted.reconcile(True, body=recovered_request)
    assert status == "200 OK" and reconciled["outcome"] == "completed"
    assert reconciled["event"]["state_version"] == 2
    assert _effect_count(path) == 1
    restarted.close()


def test_pending_lookup_keeps_fresh_in_progress_operation_active_without_reconciliation(tmp_path):
    path = tmp_path / "pending-live-in-progress.sqlite"
    _seed(path, _event())
    operation_id = _seed_pending_operation(path)
    candidate = Candidate(path)

    status, pending = candidate.pending()
    assert status == "202 Accepted"
    assert pending == {
        "schema_version": 1,
        "outcome": "in_progress",
        "event_id": EVENT_ID,
    }
    assert _effect_count(path) == 0

    database = SQLiteDatabase(str(path))
    try:
        operation = SQLiteExternalActionOperationRepository(database).get(operation_id)
        assert operation.status == ExternalActionStatus.IN_PROGRESS
    finally:
        database.close()
    candidate.close()


def test_pending_lookup_recovers_stranded_in_progress_operation_without_provider_retry(tmp_path):
    path = tmp_path / "pending-in-progress.sqlite"
    _seed(path, _event())
    operation_id = _seed_pending_operation(path)
    _mark_operation_stale(path, operation_id)
    candidate = Candidate(path)

    status, pending = candidate.pending()
    assert status == "200 OK"
    assert pending["outcome"] == "pending"
    assert pending["state_version"] == 1
    assert pending["start_at"] == PENDING_START
    assert _effect_count(path) == 0

    database = SQLiteDatabase(str(path))
    try:
        operation = SQLiteExternalActionOperationRepository(database).get(operation_id)
        assert operation.status == ExternalActionStatus.RECONCILIATION_REQUIRED
    finally:
        database.close()
    candidate.close()


def test_pending_lookup_recovers_operation_after_event_commit_before_operation_commit(tmp_path):
    path = tmp_path / "pending-event-committed.sqlite"
    target_event = _event().transition(
        ScheduleStatus.RESCHEDULE_REQUESTED,
        start_at=datetime.fromisoformat(START),
        end_at=datetime.fromisoformat(END),
        timezone_name=ZONE,
    )
    _seed(path, target_event)
    operation_id = _seed_pending_operation(path)
    _mark_operation_stale(path, operation_id)
    candidate = Candidate(path)

    status, pending = candidate.pending()
    assert status == "200 OK"
    assert pending["outcome"] == "pending"
    assert pending["state_version"] == 1
    assert pending["start_at"] == PENDING_START
    assert _stored_event(path).version == 2
    assert _effect_count(path) == 0

    database = SQLiteDatabase(str(path))
    try:
        operation = SQLiteExternalActionOperationRepository(database).get(operation_id)
        assert operation.status == ExternalActionStatus.RECONCILIATION_REQUIRED
    finally:
        database.close()
    candidate.close()


def test_pending_lookup_fails_closed_when_multiple_ambiguous_intents_exist(tmp_path):
    path = tmp_path / "pending-ambiguous.sqlite"
    _seed(path, _event())
    _seed_pending_operation(
        path,
        start_at="2026-12-02T10:00:00+01:00",
        end_at="2026-12-02T11:00:00+01:00",
        final_status=ExternalActionStatus.RECONCILIATION_REQUIRED,
    )
    _seed_pending_operation(
        path,
        start_at="2026-12-02T12:00:00+01:00",
        end_at="2026-12-02T13:00:00+01:00",
        final_status=ExternalActionStatus.RECONCILIATION_REQUIRED,
    )
    candidate = Candidate(path)
    assert candidate.pending() == (
        "409 Conflict", {"error": "approval_conflict"}
    )
    assert _effect_count(path) == 0
    candidate.close()


def test_pending_reschedule_lookup_is_owner_scoped_and_returns_none_when_absent(tmp_path):
    path = tmp_path / "pending-owner.sqlite"
    _seed(path, _event(), _event("foreign-interview", candidate_id="candidate-2"))
    candidate = Candidate(path)
    assert candidate.pending() == (
        "200 OK",
        {"schema_version": 1, "outcome": "none", "event_id": EVENT_ID},
    )
    assert candidate.pending("foreign-interview") == (
        "404 Not Found", {"error": "not_found"}
    )
    assert candidate.pending(token="other-token") == (
        "401 Unauthorized", {"error": "unauthorized"}
    )
    candidate.close()


def test_pending_reschedule_query_is_strict_and_authentication_precedes_storage(tmp_path):
    missing = tmp_path / "pending-missing.sqlite"
    app = CandidateScheduleActionWSGIApp(
        database_path=str(missing), resolve_bearer=lambda _token: None
    )
    statuses = []
    payload = b"".join(app({
        "REQUEST_METHOD": "GET",
        "PATH_INFO": CandidateScheduleActionWSGIApp.PENDING_PATH,
        "QUERY_STRING": "event_id=" + EVENT_ID,
        "CONTENT_LENGTH": "0",
        "wsgi.input": io.BytesIO(b""),
    }, lambda status, headers: statuses.append(status)))
    assert statuses == ["401 Unauthorized"]
    assert json.loads(payload) == {"error": "unauthorized"}
    assert not missing.exists()

    path = tmp_path / "pending-query.sqlite"
    _seed(path, _event())
    candidate = Candidate(path)
    assert candidate.pending(query="event_id=" + EVENT_ID + "&event_id=" + EVENT_ID) == (
        "400 Bad Request", {"error": "invalid_request"}
    )
    assert candidate.pending(query="event_id=%ZZ") == (
        "400 Bad Request", {"error": "invalid_request"}
    )
    candidate.close()


def test_extreme_decimal_exponent_parse_failure_is_a_sanitized_bad_request(tmp_path):
    path = tmp_path / "extreme-decimal-parse.sqlite"
    _seed(path, _event())
    candidate = Candidate(path)
    raw = (
        '{"schema_version":1,"event_id":"' + EVENT_ID + '",'
        '"state_version":1e' + "9" * 60 + ','
        '"start_at":"' + START + '","end_at":"' + END + '",'
        '"timezone_name":"' + ZONE + '","decision":"approve"}'
    ).encode("utf-8")
    status, result = candidate.call(
        CandidateScheduleActionWSGIApp.RESCHEDULE_PATH, raw=raw
    )
    assert (status, result) == ("400 Bad Request", {"error": "invalid_request"})
    assert _effect_count(path) == 0
    candidate.close()


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
    pending_get = document["paths"][CandidateScheduleActionWSGIApp.PENDING_PATH]["get"]
    assert "202" in pending_get["responses"]
    active_schema = pending_get["responses"]["202"]["content"]["application/json"]["schema"]
    assert active_schema["properties"]["outcome"]["const"] == "in_progress"
    reschedule_post = document["paths"][CandidateScheduleActionWSGIApp.RESCHEDULE_PATH]["post"]
    post_active_schemas = reschedule_post["responses"]["202"]["content"]["application/json"]["schema"]["oneOf"]
    assert {
        schema["properties"]["outcome"]["const"] for schema in post_active_schemas
    } == {"in_progress", "reconciliation_required", "blocked"}

    html = candidate_sandbox_html().decode("utf-8")
    for identifier in (
        "reschedule-start", "reschedule-end", "reschedule-timezone",
        "request-reschedule", "confirm-reschedule-completed",
        "confirm-reschedule-no-effect", "/v1/candidate/schedule-event/reschedule",
        "/v1/candidate/schedule-event/reschedule-reconciliation",
    ):
        assert identifier in html
    assert "['none', 'pending', 'in_progress'].includes(pending.outcome)" in html
    assert "A sandbox reschedule is still in progress." in html
    assert "A previous sandbox reschedule is unresolved." in html
    assert "result.outcome === 'blocked' || result.outcome === 'in_progress'" in html
    assert "No real calendar is contacted" in html


def test_candidate_sandbox_browser_disables_actions_while_reschedule_is_live(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from threading import Thread
    from wsgiref.simple_server import WSGIRequestHandler, make_server

    path = tmp_path / "candidate-browser-live-reschedule.sqlite"
    _seed(path, _event())
    _seed_pending_operation(path)
    candidate = Candidate(path)

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
            page.locator("#reschedule-state").get_by_text(
                "The current sandbox request is active", exact=False
            ).wait_for()

            assert page.locator("#reschedule-panel").get_attribute("disabled") is not None
            assert page.locator("#reschedule-start").is_disabled()
            assert page.locator("#reschedule-end").is_disabled()
            assert page.locator("#reschedule-timezone").is_disabled()
            assert page.locator("#request-reschedule").is_disabled()
            assert page.locator("#reschedule-reconciliation-panel").get_attribute("disabled") is not None
            assert page.locator("#reschedule-reconciliation-verified").is_disabled()
            assert page.locator("#confirm-reschedule-completed").is_disabled()
            assert page.locator("#confirm-reschedule-no-effect").is_disabled()
            assert _effect_count(path) == 0
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        candidate.close()


def test_candidate_sandbox_browser_disables_duplicate_post_while_reschedule_is_live(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from threading import Thread
    from wsgiref.simple_server import WSGIRequestHandler, make_server

    path = tmp_path / "candidate-browser-duplicate-live-reschedule.sqlite"
    _seed(path, _event())
    candidate = Candidate(path)

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

            page.locator("#reschedule-start").fill("2026-12-02T12:00:00+01:00")
            page.locator("#reschedule-end").fill("2026-12-02T13:00:00+01:00")
            page.locator("#reschedule-timezone").fill(ZONE)
            operation_id = _seed_pending_operation(path)
            page.locator("#request-reschedule").click()
            page.locator("#reschedule-state").get_by_text(
                "Another target is blocked", exact=False
            ).wait_for()

            assert page.locator("#reschedule-panel").get_attribute("disabled") is not None
            assert page.locator("#reschedule-start").is_disabled()
            assert page.locator("#reschedule-end").is_disabled()
            assert page.locator("#reschedule-timezone").is_disabled()
            assert page.locator("#request-reschedule").is_disabled()
            assert page.locator("#reschedule-reconciliation-panel").get_attribute("disabled") is not None
            assert page.locator("#reschedule-reconciliation-verified").is_disabled()
            assert page.locator("#confirm-reschedule-completed").is_disabled()
            assert page.locator("#confirm-reschedule-no-effect").is_disabled()
            assert _effect_count(path) == 0

            database = SQLiteDatabase(str(path))
            try:
                operation = SQLiteExternalActionOperationRepository(database).get(operation_id)
                assert operation.status == ExternalActionStatus.IN_PROGRESS
            finally:
                database.close()
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        candidate.close()


def test_candidate_sandbox_browser_submits_and_reconciles_owned_interview_reschedule(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from threading import Thread
    from wsgiref.simple_server import WSGIRequestHandler, make_server

    path = tmp_path / "candidate-browser-reschedule.sqlite"
    _seed(
        path,
        _event(),
        _event("interview-reschedule-2", start_at=datetime(2026, 12, 3, 9, 0, tzinfo=timezone.utc)),
    )
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
            assert not page.locator("#reschedule-start").is_disabled()

            page.locator("#reschedule-start").fill(START)
            page.locator("#reschedule-end").fill(END)
            page.locator("#reschedule-timezone").fill(ZONE)
            page.locator("#request-reschedule").click()
            page.locator("#reschedule-reconciliation-state").get_by_text(
                "Provider outcome is uncertain"
            ).wait_for()
            assert page.locator("#reschedule-start").is_disabled()
            assert page.locator("#request-reschedule").is_disabled()
            assert page.locator("#confirm-reschedule-completed").is_disabled()
            assert page.locator("#confirm-reschedule-no-effect").is_disabled()
            assert not page.locator("#reschedule-reconciliation-verified").is_disabled()

            # Switching events clears memory state; selecting the original event
            # restores its exact pending slot from the durable operation ledger.
            page.get_by_role("button", name="View event 2: interview-reschedule-2").click()
            page.locator("#event-detail").get_by_text('"event_id": "interview-reschedule-2"').wait_for()
            page.get_by_role("button", name="View event 1: interview-reschedule-1").click()
            page.locator("#reschedule-reconciliation-state").get_by_text(
                "An ambiguous reschedule was restored from durable storage"
            ).wait_for()
            assert page.locator("#reschedule-start").input_value() == PENDING_START
            assert page.locator("#reschedule-end").input_value() == PENDING_END
            assert page.locator("#reschedule-timezone").input_value() == ZONE
            assert not page.locator("#reschedule-reconciliation-verified").is_checked()
            assert page.locator("#confirm-reschedule-completed").is_disabled()
            assert page.locator("#confirm-reschedule-no-effect").is_disabled()

            # Reload restores the target again, but never the previous attestation.
            page.reload()
            page.locator("#token").fill("owner-token")
            page.locator("#list-events").click()
            page.get_by_role("button", name="View event 1: interview-reschedule-1").wait_for()
            page.get_by_role("button", name="View event 1: interview-reschedule-1").click()
            page.locator("#reschedule-reconciliation-state").get_by_text(
                "An ambiguous reschedule was restored from durable storage"
            ).wait_for()
            assert page.locator("#reschedule-start").input_value() == PENDING_START
            assert not page.locator("#reschedule-reconciliation-verified").is_checked()

            page.locator("#reschedule-reconciliation-verified").check()
            page.locator("#confirm-reschedule-no-effect").click()
            page.locator("#reschedule-reconciliation-state").get_by_text(
                "Provider evidence did not confirm"
            ).wait_for()
            assert page.locator("#reschedule-reconciliation-verified").is_disabled()
            assert not page.locator("#reschedule-reconciliation-verified").is_checked()

            # A rejected evidence claim consumes the attestation. Re-selecting the
            # event restores the pending slot and requires a fresh independent check.
            page.get_by_role("button", name="View event 1: interview-reschedule-1").click()
            page.locator("#reschedule-reconciliation-state").get_by_text(
                "An ambiguous reschedule was restored from durable storage"
            ).wait_for()
            page.locator("#reschedule-reconciliation-verified").check()
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


def test_candidate_sandbox_browser_retries_only_same_slot_after_verified_no_effect(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from threading import Thread
    from wsgiref.simple_server import WSGIRequestHandler, make_server

    path = tmp_path / "candidate-browser-no-effect.sqlite"
    _seed(path, _event())
    candidate = Candidate(
        path,
        lambda _key, attempt: (
            SandboxCalendarOutcome.INTERRUPTED_BEFORE_EFFECT
            if attempt == 1 else SandboxCalendarOutcome.SUCCESS
        ),
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
            page.get_by_role("button", name="View event 1: interview-reschedule-1").click()
            page.locator("#event-detail").get_by_text('"state_version": 1').wait_for()
            page.locator("#reschedule-start").fill(START)
            page.locator("#reschedule-end").fill(END)
            page.locator("#reschedule-timezone").fill(ZONE)
            page.locator("#request-reschedule").click()
            page.locator("#reschedule-reconciliation-state").get_by_text(
                "Provider outcome is uncertain"
            ).wait_for()

            page.locator("#reschedule-reconciliation-verified").check()
            page.locator("#confirm-reschedule-completed").click()
            page.locator("#reschedule-reconciliation-state").get_by_text(
                "Provider evidence did not confirm"
            ).wait_for()
            assert page.locator("#reschedule-reconciliation-verified").is_disabled()
            page.get_by_role("button", name="View event 1: interview-reschedule-1").click()
            page.locator("#reschedule-reconciliation-state").get_by_text(
                "An ambiguous reschedule was restored from durable storage"
            ).wait_for()
            page.locator("#reschedule-reconciliation-verified").check()
            page.locator("#confirm-reschedule-no-effect").click()
            page.locator("#reschedule-state").get_by_text(
                "Provider evidence confirmed no calendar change"
            ).wait_for()
            assert page.locator("#reschedule-start").is_disabled()
            assert page.locator("#reschedule-end").is_disabled()
            assert page.locator("#reschedule-timezone").is_disabled()
            assert page.locator("#request-reschedule").text_content() == "Retry same approved slot"

            page.locator("#request-reschedule").click()
            page.locator("#reschedule-state").get_by_text(
                "Sandbox reschedule completed"
            ).wait_for()
            assert '"status": "reschedule_requested"' in page.locator(
                "#event-detail"
            ).text_content()
            assert _effect_count(path) == 1
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
        candidate.close()


@pytest.mark.parametrize("create_operations_table", [False, True])
def test_empty_pending_lookup_does_not_mutate_candidate_database(
    tmp_path, create_operations_table
):
    path = tmp_path / ("pending-read-only-" + str(create_operations_table) + ".sqlite")
    _seed(path, _event())
    if create_operations_table:
        database = SQLiteDatabase(str(path))
        SQLiteExternalActionOperationRepository(database)
        database.close()
    candidate = Candidate(path)
    baseline = path.read_bytes()
    try:
        status, result = candidate.pending()
        assert status == "200 OK"
        assert result == {
            "schema_version": 1,
            "outcome": "none",
            "event_id": EVENT_ID,
        }
    finally:
        candidate.close()
    assert path.read_bytes() == baseline


@pytest.mark.parametrize(
    "malformed_payload",
    [
        "[]",
        '{"unexpected":"shape"}',
        '{"event_id":null}',
        '{"event_id":"bad id"}',
        '{"event_id":"interview-reschedule-1","event_id":"other"}',
    ],
)
def test_pending_lookup_fails_closed_on_unmatchable_ambiguous_operation_payload(
    tmp_path, malformed_payload
):
    path = tmp_path / "malformed-pending-payload.sqlite"
    _seed(path, _event())
    operation_id = _seed_pending_operation(path)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "UPDATE external_action_operations SET payload_json = ? WHERE operation_id = ?",
            (malformed_payload, operation_id),
        )
        connection.commit()
    finally:
        connection.close()
    candidate = Candidate(path)
    status, result = candidate.pending()
    assert status == "409 Conflict"
    assert result == {"error": "approval_conflict"}
    assert _effect_count(path) == 0


def test_pending_reschedule_scan_uses_composite_index_without_temporary_sort(tmp_path):
    path = tmp_path / "pending-reschedule-query-plan.sqlite"
    _seed(path, _event())
    _seed_pending_operation(path)

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
                "calendar.reschedule",
                ExternalActionStatus.IN_PROGRESS.value,
                ExternalActionStatus.RECONCILIATION_REQUIRED.value,
            ),
        ).fetchall()
    finally:
        connection.close()

    details = tuple(str(row[3]) for row in plan)
    assert any("idx_external_actions_type_status" in detail for detail in details)
    assert not any("TEMP B-TREE" in detail.upper() for detail in details)
