"""Full candidate workflow recovery over the production composition and HTTP boundary.

The run is started through the production composition function (the HTTP start contract
deliberately cannot carry a recipient or calendar event), then approvals, status and
reconciliation go through the authenticated HTTP application.
"""

from __future__ import annotations

import io
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from career_agent_ai.application.api import CandidateApprovalWSGIApp
from career_agent_ai.application.career.autonomous_loop_models import CareerLoopRequest
from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.runtime import composition
from career_agent_ai.application.runtime.composition import (
    SandboxProviderPlans,
    build_candidate_sandbox_app_from_env,
)
from career_agent_ai.application.scheduling import ScheduleEvent, ScheduleEventType, ScheduleStatus
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.tests.test_production_hardening import PROFILE
from career_agent_ai.tests.test_sandbox_comm_calendar_hardening import (
    Cal,
    CM,
    CO,
)

USER = "candidate-1"
TOKENS = {"owner": USER, "foreign": "candidate-2"}
ENV = {
    "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
    "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
}
JOBS = (
    Job.create(
        job_id="job-e2e",
        title="Logistics Coordinator",
        company=Company("company-e2e", "Acme"),
        location=Location("Germany", "Karlsruhe"),
        description="logistics coordination",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    ),
)
CLIENT_RUN = "workflow-e2e"
RUN_ID = CandidateApprovalWSGIApp._owner_scoped_run_id(USER, CLIENT_RUN)
EVENT_ID = "interview-e2e"


def _request() -> CareerLoopRequest:
    return CareerLoopRequest(
        user_id=USER,
        keyword="logistics",
        candidate_profile=PROFILE,
        location="Karlsruhe",
        sender="candidate@example.test",
        recipient="recruiter@example.test",
        message_subject="Application follow-up",
        message_body="Thank you for considering my application.",
        schedule_event_id=EVENT_ID,
    )


def _event() -> ScheduleEvent:
    start = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)
    now = datetime(2026, 11, 19, tzinfo=timezone.utc)
    return ScheduleEvent(
        event_id=EVENT_ID,
        candidate_id=USER,
        employer_name="Acme",
        event_type=ScheduleEventType.INTERVIEW,
        location="Sandbox video room",
        start_at=start,
        end_at=start + timedelta(hours=1),
        timezone_name="Europe/Berlin",
        application_id=f"{RUN_ID}:application",
        created_at=now,
        updated_at=now,
    )


class Process:
    """One simulated server process over a shared SQLite file."""

    def __init__(self, path, plans=None):
        self.path = str(path)
        self.app = build_candidate_sandbox_app_from_env(
            resolve_bearer=TOKENS.get,
            jobs=JOBS,
            environ={"CAREER_AGENT_DB_PATH": self.path, **ENV},
            sandbox_outcome_plan=plans,
        )

    def call(self, route, *, method="GET", token="owner", body=None):
        encoded = b"" if body is None else json.dumps(body).encode()
        statuses: list[str] = []
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": route,
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": str(len(encoded)),
            "wsgi.input": io.BytesIO(encoded),
        }
        if token is not None:
            environ["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        payload = b"".join(self.app(environ, lambda s, h: statuses.append(s)))
        return statuses[0], json.loads(payload)

    def status(self):
        return self.call(f"/v1/candidate/runs/{RUN_ID}/status")[1]

    def approve(self):
        prompt = self.call(f"/v1/candidate/runs/{RUN_ID}/approval")[1]
        decision = {
            "schema_version": 1,
            "run_id": RUN_ID,
            "state_version": prompt["state_version"],
            "action_fingerprint": prompt["action_fingerprint"],
            "decision": "approve",
        }
        status, body = self.call(
            f"/v1/candidate/runs/{RUN_ID}/approval", method="POST", body=decision
        )
        assert status == "200 OK", body
        return body["phase"]

    def reconcile(self, kind, token="owner", **claim):
        state = claim.pop("state_version", None)
        if state is None:
            state = self.status()["state_version"]
        return self.call(
            f"/v1/candidate/runs/{RUN_ID}/{kind}-reconciliation",
            method="POST",
            token=token,
            body={"schema_version": 1, "state_version": state, **claim},
        )

    def close(self):
        self.app.close()


def _start(path, plans=None):
    """Seed the interview, then start the run through the composed loop."""
    seed = Cal(path)
    seed.service.add_event(_event())
    seed.close()
    database = SQLiteDatabase(str(path))
    try:
        loop = composition._build_candidate_loop(database, JOBS, plans)
        loop.start(_request(), run_id=RUN_ID)
    finally:
        database.close()
    return Process(path, plans)


def _rows(path, table, where="1=1"):
    connection = sqlite3.connect(path)
    try:
        return connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
    finally:
        connection.close()


@pytest.fixture
def path(tmp_path):
    return tmp_path / "workflow.sqlite"


def _plans(message=None, calendar=None):
    return SandboxProviderPlans(
        communication=(lambda _k, a: message if a == 1 else CM.SUCCESS) if message else None,
        calendar=(lambda _k, a: calendar if a == 1 else CO.SUCCESS) if calendar else None,
    )


def test_happy_workflow_reaches_completion_with_one_effect_per_action(path):
    process = _start(path)
    assert process.status()["phase"] == "application_approval"
    assert process.approve() == "message_approval"
    assert process.approve() == "interview_approval"
    assert process.approve() == "complete"
    assert _rows(path, "sandbox_submission_ledger") == 1
    assert _rows(path, "sandbox_communication_deliveries") == 1
    assert _rows(path, "sandbox_calendar_effects") == 1
    process.close()


# --------------------------------- message path ---------------------------------


def _to_message_reconciliation(path, outcome):
    process = _start(path, _plans(message=outcome))
    assert process.approve() == "message_approval"
    assert process.approve() == "message_reconciliation"
    return process


def test_message_timeout_recovers_after_restart_with_verified_delivery(path):
    first = _to_message_reconciliation(path, CM.TIMEOUT_AFTER_EFFECT)
    assert _rows(path, "sandbox_communication_deliveries") == 1
    first.close()

    restarted = Process(path)  # restart: default plans would succeed
    snapshot = restarted.status()
    assert snapshot["phase"] == "message_reconciliation"
    # A forged "not delivered" claim contradicts the provider's durable evidence.
    status, body = restarted.reconcile("message", delivered=False)
    assert (status, body) == ("409 Conflict", {"error": "approval_conflict"})
    assert restarted.status() == snapshot
    status, body = restarted.reconcile("message", delivered=True)
    assert (status, body["phase"]) == ("200 OK", "interview_approval")
    assert restarted.approve() == "complete"
    assert _rows(path, "sandbox_communication_deliveries") == 1
    restarted.close()


def test_message_interruption_before_effect_rejects_forged_delivery_then_retries_once(path):
    process = _to_message_reconciliation(path, CM.INTERRUPTED_BEFORE_EFFECT)
    assert _rows(path, "sandbox_communication_deliveries") == 0
    before = process.status()
    assert process.reconcile("message", delivered=True)[0] == "409 Conflict"
    assert process.status() == before
    status, body = process.reconcile("message", delivered=False)
    assert status == "200 OK"
    assert _rows(path, "sandbox_communication_deliveries") <= 1
    while process.status()["phase"] not in {"complete", "failed"}:
        process.approve()
    assert process.status()["phase"] == "complete"
    assert _rows(path, "sandbox_communication_deliveries") == 1
    process.close()


def test_message_reconciliation_rejects_unauthorized_foreign_and_stale_requests(path):
    process = _to_message_reconciliation(path, CM.TIMEOUT_AFTER_EFFECT)
    before = process.status()
    assert process.reconcile("message", token=None, delivered=True)[0] == "401 Unauthorized"
    assert process.reconcile("message", token="bad", delivered=True)[0] == "401 Unauthorized"
    assert process.reconcile("message", token="foreign", delivered=True) == (
        "404 Not Found",
        {"error": "not_found"},
    )
    stale = process.reconcile(
        "message", delivered=True, state_version=before["state_version"] - 1
    )
    assert stale[0] == "409 Conflict"
    assert process.call(f"/v1/candidate/runs/{RUN_ID}/status", token="foreign")[0] == (
        "404 Not Found"
    )
    assert process.status() == before
    assert _rows(path, "sandbox_communication_deliveries") == 1
    process.close()


def test_message_tampered_provider_evidence_blocks_a_delivered_claim(path):
    process = _to_message_reconciliation(path, CM.TIMEOUT_AFTER_EFFECT)
    connection = sqlite3.connect(path)
    connection.execute("UPDATE sandbox_communication_messages SET recipient = 'attacker@example.test'")
    connection.commit()
    connection.close()
    before = process.status()
    assert process.reconcile("message", delivered=True)[0] == "409 Conflict"
    assert process.status() == before
    process.close()


# ------------------------------- interview path -------------------------------


def _to_interview_reconciliation(path, outcome):
    process = _start(path, _plans(calendar=outcome))
    assert process.approve() == "message_approval"
    assert process.approve() == "interview_approval"
    assert process.approve() == "interview_reconciliation"
    return process


def test_interview_timeout_recovers_after_restart_with_verified_event(path):
    first = _to_interview_reconciliation(path, CO.TIMEOUT_AFTER_EFFECT)
    assert _rows(path, "sandbox_calendar_effects") == 1
    first.close()

    restarted = Process(path)
    snapshot = restarted.status()
    assert snapshot["phase"] == "interview_reconciliation"
    forged = restarted.reconcile("interview", completed=True, provider_event_id="forged-id")
    assert forged == ("409 Conflict", {"error": "approval_conflict"})
    not_done = restarted.reconcile("interview", completed=False)
    assert not_done == ("409 Conflict", {"error": "approval_conflict"})
    assert restarted.status() == snapshot
    status, body = restarted.reconcile(
        "interview", completed=True, provider_event_id=f"fake-{EVENT_ID}"
    )
    assert (status, body["phase"]) == ("200 OK", "complete")
    assert _rows(path, "sandbox_calendar_effects") == 1
    restarted.close()


def test_interview_interruption_before_effect_rejects_claim_then_retries_once(path):
    process = _to_interview_reconciliation(path, CO.INTERRUPTED_BEFORE_EFFECT)
    assert _rows(path, "sandbox_calendar_effects") == 0
    before = process.status()
    claim = process.reconcile("interview", completed=True, provider_event_id=f"fake-{EVENT_ID}")
    assert claim[0] == "409 Conflict" and process.status() == before
    assert process.reconcile("interview", completed=False)[0] == "200 OK"
    while process.status()["phase"] not in {"complete", "failed"}:
        process.approve()
    assert process.status()["phase"] == "complete"
    assert _rows(path, "sandbox_calendar_effects") == 1
    process.close()


def test_interview_reconciliation_rejects_unauthorized_foreign_and_stale_requests(path):
    process = _to_interview_reconciliation(path, CO.TIMEOUT_AFTER_EFFECT)
    before = process.status()
    claim = {"completed": True, "provider_event_id": f"fake-{EVENT_ID}"}
    assert process.reconcile("interview", token=None, **claim)[0] == "401 Unauthorized"
    assert process.reconcile("interview", token="foreign", **claim)[0] == "404 Not Found"
    stale = process.reconcile(
        "interview", state_version=before["state_version"] - 1, **claim
    )
    assert stale[0] == "409 Conflict"
    assert process.status() == before
    assert _rows(path, "sandbox_calendar_effects") == 1
    process.close()


def test_interview_event_taken_over_by_another_candidate_blocks_the_claim(path):
    process = _to_interview_reconciliation(path, CO.TIMEOUT_AFTER_EFFECT)
    document = sqlite3.connect(path).execute(
        "SELECT event_json FROM sandbox_calendar_effects"
    ).fetchone()[0]
    forged = json.loads(document)
    forged["candidate_id"] = "candidate-2"
    connection = sqlite3.connect(path)
    connection.execute("UPDATE sandbox_calendar_effects SET event_json = ?", (json.dumps(forged),))
    connection.commit()
    connection.close()
    before = process.status()
    claim = process.reconcile("interview", completed=True, provider_event_id=f"fake-{EVENT_ID}")
    assert claim[0] == "409 Conflict" and process.status() == before
    process.close()


def test_openapi_contract_is_unchanged_for_reconciliation_routes():
    from career_agent_ai.application.api import candidate_approval_openapi_document

    paths = candidate_approval_openapi_document()["paths"]
    for route in ("message-reconciliation", "interview-reconciliation"):
        entry = paths[f"/v1/candidate/runs/{{run_id}}/{route}"]
        assert set(entry) - {"parameters"} == {"post"}
        assert {"200", "401", "404", "409"} <= set(entry["post"]["responses"])
    assert not [p for p in paths if "ledger" in p or "provider" in p]
