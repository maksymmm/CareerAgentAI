"""Regression tests for the durable sandbox application-submission provider."""

from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime, timezone
from hashlib import sha256
from threading import Barrier, Thread

import pytest

from career_agent_ai.application.api import candidate_approval_openapi_document
from career_agent_ai.application.career import ApplicationSubmissionService
from career_agent_ai.application.career.application_submission import PreSubmissionError
from career_agent_ai.application.external_actions import (
    ExternalActionService,
    ExternalActionStatus,
)
from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.runtime.composition import (
    build_candidate_sandbox_app_from_env,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import (
    SQLiteExternalActionOperationRepository,
)
from career_agent_ai.application.storage.sqlite_sandbox_submission_provider import (
    SandboxOutcome,
    SQLiteSandboxSubmissionProvider,
    always,
    scripted_outcomes,
)
from career_agent_ai.tests.test_production_hardening import PROFILE

CONTENT = "Approved application artifact"
DIGEST = sha256(CONTENT.encode()).hexdigest()
SNAPSHOT = "b" * 64
OP = "application-submit:test-op"
OUTCOME = SandboxOutcome


def _submit_kwargs(**overrides):
    values = {
        "job_id": "job-1",
        "application_id": "application-1",
        "artifact_content": CONTENT,
        "artifact_sha256": DIGEST,
        "candidate_snapshot_sha256": SNAPSHOT,
        "human_approved": True,
    }
    values.update(overrides)
    return values


class Stack:
    """One 'process': fresh connection, provider, and service over a shared file."""

    def __init__(self, path, plan=None, *, evidence=False, provider_cls=None):
        self.database = SQLiteDatabase(str(path))
        self.provider = (provider_cls or SQLiteSandboxSubmissionProvider)(
            self.database, plan
        )
        self.service = ApplicationSubmissionService(
            self.provider,
            ExternalActionService(
                SQLiteExternalActionOperationRepository(self.database),
                ApplicationSubmissionService.action_adapter(self.provider),
            ),
            require_provider_evidence=evidence,
        )

    def status(self, operation_id=OP):
        return self.service.get_operation(operation_id).status

    def close(self):
        self.database.close()


@pytest.fixture
def path(tmp_path):
    return tmp_path / "sandbox.sqlite"


def test_happy_path_creates_exactly_one_provider_effect_and_replays_idempotently(path):
    stack = Stack(path)
    result = stack.service.submit(OP, **_submit_kwargs())
    assert result["provider_submission_id"] == "fake:application-1"
    assert stack.status() == ExternalActionStatus.SUCCEEDED
    assert stack.service.submit(OP, **_submit_kwargs()) == result
    entries = stack.provider.ledger_entries()
    assert [e.idempotency_key for e in entries] == [OP]
    assert stack.provider.attempts(OP) == (OUTCOME.SUCCESS,)
    stack.close()


def test_provider_replay_with_same_key_never_adds_effect_but_is_counted(path):
    stack = Stack(path)
    args = (OP, "job-1", "application-1", CONTENT, DIGEST, SNAPSHOT)
    first = stack.provider.submit(*args)
    assert stack.provider.submit(*args) == first
    entry = stack.provider.observe(OP)
    assert entry.replays == 1 and len(stack.provider.ledger_entries()) == 1
    assert stack.provider.observe("missing") is None
    assert stack.provider.is_dry_run is True
    stack.close()


@pytest.mark.parametrize(
    "outcome", [OUTCOME.TIMEOUT_AFTER_EFFECT, OUTCOME.EVIDENCE_MISMATCH]
)
def test_ambiguous_effect_survives_restart_and_reconciles_to_exactly_one_effect(
    path, outcome
):
    first = Stack(path, always(outcome))
    assert first.service.submit(OP, **_submit_kwargs()) is None
    assert first.status() == ExternalActionStatus.RECONCILIATION_REQUIRED
    evidence = first.provider.observe(OP)
    assert evidence is not None
    first.close()

    restarted = Stack(path, always(OUTCOME.NO_EFFECT))  # plan must not matter now
    assert restarted.status() == ExternalActionStatus.RECONCILIATION_REQUIRED
    assert restarted.provider.observe(OP) == evidence
    restarted.service.resolve_reconciliation(
        OP, submitted=True, provider_submission_id=evidence.provider_submission_id
    )
    assert restarted.status() == ExternalActionStatus.SUCCEEDED
    result = restarted.service.submit(OP, **_submit_kwargs())
    assert result["provider_submission_id"] == evidence.provider_submission_id
    assert restarted.provider.attempts(OP) == (outcome,)
    assert len(restarted.provider.ledger_entries()) == 1
    restarted.close()


def test_connection_interrupted_before_effect_reconciles_as_not_submitted_then_retries(path):
    first = Stack(path, scripted_outcomes({OP: [OUTCOME.INTERRUPTED_BEFORE_EFFECT]}))
    assert first.service.submit(OP, **_submit_kwargs()) is None
    assert first.status() == ExternalActionStatus.RECONCILIATION_REQUIRED
    assert first.provider.ledger_entries() == ()
    first.close()

    restarted = Stack(path, scripted_outcomes({OP: [OUTCOME.INTERRUPTED_BEFORE_EFFECT]}))
    restarted.service.resolve_reconciliation(OP, submitted=False)
    result = restarted.service.submit(OP, **_submit_kwargs())
    assert result["provider_submission_id"] == "fake:application-1"
    # The plan is consumed durably: attempt 2 is SUCCESS even after restart.
    assert restarted.provider.attempts(OP) == (
        OUTCOME.INTERRUPTED_BEFORE_EFFECT,
        OUTCOME.SUCCESS,
    )
    assert len(restarted.provider.ledger_entries()) == 1
    restarted.close()


@pytest.mark.parametrize("outcome", [OUTCOME.NO_EFFECT, OUTCOME.RETRYABLE_NO_EFFECT])
def test_definitive_no_effect_allows_one_new_approved_attempt(path, outcome):
    stack = Stack(path, scripted_outcomes({OP: [outcome]}))
    assert stack.service.submit(OP, **_submit_kwargs()) is None
    assert stack.status() == ExternalActionStatus.FAILED
    assert stack.provider.ledger_entries() == ()
    # Without fresh human approval the failed operation is never re-executed.
    assert stack.service.submit(OP, **_submit_kwargs(human_approved=False)) is None
    assert stack.status() == ExternalActionStatus.FAILED
    assert stack.provider.attempts(OP) == (outcome,)
    result = stack.service.submit(OP, **_submit_kwargs())
    assert result["application_id"] == "application-1"
    assert stack.provider.attempts(OP) == (outcome, OUTCOME.SUCCESS)
    assert len(stack.provider.ledger_entries()) == 1
    stack.close()


def test_stale_not_submitted_claim_cannot_duplicate_an_existing_provider_effect(path):
    stack = Stack(path, scripted_outcomes({OP: [OUTCOME.TIMEOUT_AFTER_EFFECT]}))
    assert stack.service.submit(OP, **_submit_kwargs()) is None
    stack.service.resolve_reconciliation(OP, submitted=False)  # operator was wrong
    result = stack.service.submit(OP, **_submit_kwargs())
    assert result["provider_submission_id"] == "fake:application-1"
    entries = stack.provider.ledger_entries()
    assert len(entries) == 1 and entries[0].replays == 1
    stack.close()


def test_same_key_with_different_intent_is_rejected_without_effect(path):
    stack = Stack(path)
    args = (OP, "job-1", "application-1", CONTENT, DIGEST, SNAPSHOT)
    stack.provider.submit(*args)
    other = (OP, "job-2", "application-1", CONTENT, DIGEST, SNAPSHOT)
    with pytest.raises(PreSubmissionError, match="different submission intent"):
        stack.provider.submit(*other)
    assert stack.provider.observe(OP).job_id == "job-1"
    assert stack.provider.observe(OP).replays == 0
    stack.close()


@pytest.mark.parametrize(
    "outcome",
    [OUTCOME.NO_EFFECT, OUTCOME.RETRYABLE_NO_EFFECT, OUTCOME.INTERRUPTED_BEFORE_EFFECT],
)
@pytest.mark.parametrize(
    "changed",
    [
        ("job-2", "application-1", DIGEST, SNAPSHOT),
        ("job-1", "application-2", DIGEST, SNAPSHOT),
        ("job-1", "application-1", "c" * 64, SNAPSHOT),
        ("job-1", "application-1", DIGEST, "d" * 64),
    ],
)
def test_no_effect_attempt_binds_key_to_intent_across_restart(path, outcome, changed):
    first = Stack(path, scripted_outcomes({OP: [outcome]}))
    with pytest.raises((PreSubmissionError, ConnectionError)):
        first.provider.submit(OP, "job-1", "application-1", CONTENT, DIGEST, SNAPSHOT)
    assert first.provider.attempts(OP) == (outcome,)
    assert first.provider.ledger_entries() == ()
    first.close()

    restarted = Stack(path)
    with pytest.raises(PreSubmissionError, match="different submission intent"):
        restarted.provider.submit(OP, *changed[:2], CONTENT, *changed[2:])
    assert restarted.provider.attempts(OP) == (outcome,)
    assert restarted.provider.ledger_entries() == ()
    result = restarted.provider.submit(
        OP, "job-1", "application-1", CONTENT, DIGEST, SNAPSHOT
    )
    assert result["provider_submission_id"] == "fake:application-1"
    assert restarted.provider.attempts(OP) == (outcome, OUTCOME.SUCCESS)
    restarted.close()


def test_second_key_for_same_application_cannot_create_a_second_effect(path):
    stack = Stack(path)
    stack.provider.submit(OP, "job-1", "application-1", CONTENT, DIGEST, SNAPSHOT)
    with pytest.raises(PreSubmissionError, match="already holds"):
        stack.provider.submit(
            "application-submit:other", "job-1", "application-1", CONTENT, DIGEST, SNAPSHOT
        )
    assert len(stack.provider.ledger_entries()) == 1
    assert stack.provider.attempts("application-submit:other") == ()
    stack.close()


def test_invalid_plan_outcome_fails_closed_before_any_effect(path):
    stack = Stack(path, lambda key, attempt: "success")  # type: ignore[arg-type,return-value]
    with pytest.raises(PreSubmissionError, match="invalid outcome"):
        stack.provider.submit(OP, "job-1", "application-1", CONTENT, DIGEST, SNAPSHOT)
    assert stack.provider.ledger_entries() == ()
    stack.close()


def test_plan_helpers_validate_their_inputs():
    with pytest.raises(TypeError):
        always("success")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        scripted_outcomes({"k": ["success"]})  # type: ignore[list-item]
    plan = scripted_outcomes({"k": [OUTCOME.NO_EFFECT]}, default=OUTCOME.TIMEOUT_AFTER_EFFECT)
    assert plan("k", 1) == OUTCOME.NO_EFFECT
    assert plan("k", 2) == OUTCOME.TIMEOUT_AFTER_EFFECT
    assert plan("other", 1) == OUTCOME.TIMEOUT_AFTER_EFFECT


def test_concurrent_submissions_with_one_key_produce_one_effect(path):
    barrier = Barrier(2)
    results: list[object] = []
    errors: list[BaseException] = []

    def worker() -> None:
        stack = Stack(path)
        try:
            barrier.wait(timeout=10)
            results.append(
                stack.provider.submit(OP, "job-1", "application-1", CONTENT, DIGEST, SNAPSHOT)
            )
        except BaseException as exc:  # pragma: no cover - surfaced by assertion
            errors.append(exc)
        finally:
            stack.close()

    threads = [Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    assert len(results) == 2 and results[0] == results[1]
    verifier = Stack(path)
    entries = verifier.provider.ledger_entries()
    assert len(entries) == 1 and entries[0].replays == 1
    assert verifier.provider.attempts(OP) == (OUTCOME.SUCCESS,)
    verifier.close()


def test_ledger_schema_is_additive_and_idempotent(path):
    first = Stack(path)
    first.close()
    second = Stack(path)
    second.close()
    names = {
        row[0]
        for row in sqlite3.connect(path).execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {
        "sandbox_submission_intents",
        "sandbox_submission_ledger",
        "sandbox_submission_attempts",
    } <= names
    assert "external_action_operations" in names


# --------------------------- production-composition HTTP ---------------------------

_ENV_KEYS = {
    "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
    "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
}
_JOBS = (
    Job.create(
        job_id="job-http",
        title="Logistics Coordinator",
        company=Company("company-http", "Acme"),
        location=Location("Germany", "Karlsruhe"),
        description="logistics coordination",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    ),
)
_TOKENS = {"owner": "candidate-1", "foreign": "candidate-2"}


class Http:
    """Rebuild the production-composed app per 'process' over one durable file."""

    def __init__(self, path, plan=None):
        self.path = str(path)
        self.app = build_candidate_sandbox_app_from_env(
            resolve_bearer=_TOKENS.get,
            jobs=_JOBS,
            environ={"CAREER_AGENT_DB_PATH": self.path, **_ENV_KEYS},
            sandbox_outcome_plan=plan,
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

    def start_and_approve(self):
        status, started = self.call(
            "/v1/candidate/runs",
            method="POST",
            body={
                "schema_version": 1,
                "run_id": "sandbox-e2e",
                "keyword": "logistics",
                "candidate_profile": PROFILE,
                "location": "Karlsruhe",
            },
        )
        assert status == "201 Created"
        run_id = started["run_id"]
        _, prompt = self.call(f"/v1/candidate/runs/{run_id}/approval")
        decision = {
            "schema_version": 1,
            "run_id": run_id,
            "state_version": prompt["state_version"],
            "action_fingerprint": prompt["action_fingerprint"],
            "decision": "approve",
        }
        status, body = self.call(
            f"/v1/candidate/runs/{run_id}/approval", method="POST", body=decision
        )
        assert status == "200 OK"
        return run_id, body["phase"]

    def ledger(self):
        database = SQLiteDatabase(self.path)
        try:
            return SQLiteSandboxSubmissionProvider(database).ledger_entries()
        finally:
            database.close()


def _first_attempt(outcome):
    return lambda key, attempt: outcome if attempt == 1 else OUTCOME.SUCCESS


def test_http_happy_path_completes_with_one_provider_effect(path):
    http = Http(path)
    run_id, phase = http.start_and_approve()
    assert phase == "complete"
    assert len(http.ledger()) == 1
    http.app.close()


def test_http_ambiguous_timeout_reconciles_after_restart_with_authorization_and_versioning(
    path,
):
    first = Http(path, _first_attempt(OUTCOME.TIMEOUT_AFTER_EFFECT))
    run_id, phase = first.start_and_approve()
    assert phase == "application_reconciliation"
    first.app.close()

    second = Http(path)  # restart; default plan would succeed
    route = f"/v1/candidate/runs/{run_id}/application-reconciliation"
    _, snapshot = second.call(f"/v1/candidate/runs/{run_id}/status")
    assert snapshot["phase"] == "application_reconciliation"
    evidence = second.ledger()[0]
    body = {
        "schema_version": 1,
        "state_version": snapshot["state_version"],
        "submitted": True,
        "provider_submission_id": evidence.provider_submission_id,
    }
    assert second.call(route, method="POST", token=None, body=body)[0] == "401 Unauthorized"
    assert second.call(route, method="POST", token="bad", body=body)[0] == "401 Unauthorized"
    assert second.call(route, method="POST", token="foreign", body=body) == (
        "404 Not Found",
        {"error": "not_found"},
    )
    stale = {**body, "state_version": snapshot["state_version"] - 1}
    assert second.call(route, method="POST", body=stale)[0] == "409 Conflict"
    assert len(second.ledger()) == 1  # rejected calls changed nothing

    status, resolved = second.call(route, method="POST", body=body)
    assert (status, resolved["phase"]) == ("200 OK", "complete")
    assert len(second.ledger()) == 1 and second.ledger()[0].replays == 0
    second.app.close()

    third = Http(path)
    _, final = third.call(f"/v1/candidate/runs/{run_id}/status")
    assert final["terminal"] is True and len(third.ledger()) == 1
    third.app.close()


def test_http_interrupted_before_effect_reconciles_not_submitted_and_retries_once(path):
    first = Http(path, _first_attempt(OUTCOME.INTERRUPTED_BEFORE_EFFECT))
    run_id, phase = first.start_and_approve()
    assert phase == "application_reconciliation" and first.ledger() == ()
    first.app.close()

    second = Http(path, _first_attempt(OUTCOME.INTERRUPTED_BEFORE_EFFECT))
    _, snapshot = second.call(f"/v1/candidate/runs/{run_id}/status")
    status, resolved = second.call(
        f"/v1/candidate/runs/{run_id}/application-reconciliation",
        method="POST",
        body={
            "schema_version": 1,
            "state_version": snapshot["state_version"],
            "submitted": False,
        },
    )
    assert (status, resolved["phase"]) == ("200 OK", "complete")
    assert len(second.ledger()) == 1
    second.app.close()


def _reconcile(http, run_id, snapshot, **claim):
    return http.call(
        f"/v1/candidate/runs/{run_id}/application-reconciliation",
        method="POST",
        body={"schema_version": 1, "state_version": snapshot["state_version"], **claim},
    )


def test_http_wrong_not_submitted_claim_is_rejected_by_provider_evidence(path):
    http = Http(path, _first_attempt(OUTCOME.TIMEOUT_AFTER_EFFECT))
    run_id, _ = http.start_and_approve()
    _, before = http.call(f"/v1/candidate/runs/{run_id}/status")
    assert _reconcile(http, run_id, before, submitted=False) == (
        "409 Conflict",
        {"error": "approval_conflict"},
    )
    _, after = http.call(f"/v1/candidate/runs/{run_id}/status")
    assert after == before  # state, version and phase are untouched
    assert len(http.ledger()) == 1 and http.ledger()[0].replays == 0
    # The execution lease was released: the correct claim still succeeds.
    evidence = http.ledger()[0]
    status, resolved = _reconcile(
        http, run_id, after, submitted=True,
        provider_submission_id=evidence.provider_submission_id,
    )
    assert (status, resolved["phase"]) == ("200 OK", "complete")
    assert len(http.ledger()) == 1 and http.ledger()[0].replays == 0
    http.app.close()


def test_http_wrong_or_foreign_provider_id_is_rejected_without_state_change(path):
    http = Http(path, _first_attempt(OUTCOME.TIMEOUT_AFTER_EFFECT))
    run_id, _ = http.start_and_approve()
    _, before = http.call(f"/v1/candidate/runs/{run_id}/status")
    for claimed in ("fake:other-application", "provider-guess", "x" * 1000):
        assert _reconcile(
            http, run_id, before, submitted=True, provider_submission_id=claimed
        ) == ("409 Conflict", {"error": "approval_conflict"})
    assert http.call(f"/v1/candidate/runs/{run_id}/status")[1] == before
    assert len(http.ledger()) == 1
    http.app.close()


def test_http_submitted_claim_without_provider_record_is_rejected_then_no_effect_retry_works(
    path,
):
    http = Http(path, _first_attempt(OUTCOME.INTERRUPTED_BEFORE_EFFECT))
    run_id, _ = http.start_and_approve()
    _, before = http.call(f"/v1/candidate/runs/{run_id}/status")
    assert _reconcile(
        http, run_id, before, submitted=True, provider_submission_id="fake:invented"
    ) == ("409 Conflict", {"error": "approval_conflict"})
    assert http.ledger() == ()
    status, resolved = _reconcile(http, run_id, before, submitted=False)
    assert (status, resolved["phase"]) == ("200 OK", "complete")
    assert len(http.ledger()) == 1
    http.app.close()


def test_http_foreign_owner_cannot_reconcile_even_with_correct_evidence(path):
    http = Http(path, _first_attempt(OUTCOME.TIMEOUT_AFTER_EFFECT))
    run_id, _ = http.start_and_approve()
    _, before = http.call(f"/v1/candidate/runs/{run_id}/status")
    evidence = http.ledger()[0]
    route = f"/v1/candidate/runs/{run_id}/application-reconciliation"
    body = {
        "schema_version": 1,
        "state_version": before["state_version"],
        "submitted": True,
        "provider_submission_id": evidence.provider_submission_id,
    }
    assert http.call(route, method="POST", token="foreign", body=body)[0] == "404 Not Found"
    assert http.call(route, method="POST", token=None, body=body)[0] == "401 Unauthorized"
    assert http.call(f"/v1/candidate/runs/{run_id}/status")[1] == before
    http.app.close()


def test_http_evidence_mismatch_is_not_accepted_as_success(path):
    http = Http(path, _first_attempt(OUTCOME.EVIDENCE_MISMATCH))
    run_id, phase = http.start_and_approve()
    assert phase == "application_reconciliation"
    _, snapshot = http.call(f"/v1/candidate/runs/{run_id}/status")
    assert snapshot["terminal"] is False and snapshot["waiting_for_human"] is True
    assert len(http.ledger()) == 1
    http.app.close()


def test_openapi_contract_exposes_no_new_sandbox_provider_surface():
    document = candidate_approval_openapi_document()
    paths = document["paths"]
    route = "/v1/candidate/runs/{run_id}/application-reconciliation"
    methods = set(paths[route]) - {"parameters"}
    assert methods == {"post"}
    assert "409" in paths[route]["post"]["responses"]
    assert not [p for p in paths if "ledger" in p or "provider" in p or "sandbox-" in p]
