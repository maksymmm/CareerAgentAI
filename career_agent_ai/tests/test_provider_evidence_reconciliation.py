"""Evidence-verified reconciliation for durable sandbox application submissions."""

from __future__ import annotations

import sqlite3
from threading import Barrier, Thread

import pytest

from career_agent_ai.application.career import (
    ApplicationSubmissionService,
    EvidenceState,
    FakeApplicationSubmissionAdapter,
    ReconciliationEvidenceError,
    SubmissionEvidence,
)
from career_agent_ai.application.career.application_submission import PreSubmissionError
from career_agent_ai.application.external_actions import ExternalActionStatus
from career_agent_ai.application.storage.sqlite_sandbox_submission_provider import (
    SandboxOutcome,
    SQLiteSandboxSubmissionProvider,
    always,
    scripted_outcomes,
)
from career_agent_ai.tests.test_sandbox_submission_provider import (
    CONTENT,
    DIGEST,
    OP,
    OUTCOME,
    SNAPSHOT,
    Stack,
    _submit_kwargs,
)

REQUIRED = ExternalActionStatus.RECONCILIATION_REQUIRED
PID = "fake:application-1"


@pytest.fixture
def path(tmp_path):
    return tmp_path / "evidence.sqlite"


def _ambiguous(path, outcome=OUTCOME.TIMEOUT_AFTER_EFFECT, **kwargs):
    stack = Stack(path, always(outcome), evidence=True, **kwargs)
    assert stack.service.submit(OP, **_submit_kwargs()) is None
    assert stack.status() == REQUIRED
    return stack


def _expect_rejected(stack, **claim):
    with pytest.raises(ReconciliationEvidenceError):
        stack.service.resolve_reconciliation(OP, **claim)
    assert stack.status() == REQUIRED  # nothing was resolved


# ------------------------------- reconciliation rules -------------------------------


def test_submitted_claim_matching_ledger_evidence_resolves_to_one_effect(path):
    stack = _ambiguous(path)
    stack.service.resolve_reconciliation(OP, submitted=True, provider_submission_id=PID)
    assert stack.status() == ExternalActionStatus.SUCCEEDED
    assert stack.service.submit(OP, **_submit_kwargs())["provider_submission_id"] == PID
    assert len(stack.provider.ledger_entries()) == 1
    stack.close()


@pytest.mark.parametrize("claimed", ["fake:other", "fake:application-1 ", "", "FAKE:application-1"])
def test_submitted_claim_with_wrong_provider_id_is_rejected(path, claimed):
    stack = _ambiguous(path)
    _expect_rejected(stack, submitted=True, provider_submission_id=claimed)
    stack.close()


def test_submitted_claim_without_any_provider_id_is_rejected(path):
    stack = _ambiguous(path)
    _expect_rejected(stack, submitted=True, provider_submission_id=None)
    stack.close()


def test_submitted_claim_is_rejected_when_provider_has_no_record(path):
    stack = _ambiguous(path, OUTCOME.INTERRUPTED_BEFORE_EFFECT)
    _expect_rejected(stack, submitted=True, provider_submission_id=PID)
    assert stack.provider.ledger_entries() == ()
    stack.close()


def test_not_submitted_claim_is_rejected_when_provider_holds_the_effect(path):
    stack = _ambiguous(path)
    _expect_rejected(stack, submitted=False)
    # The rejected claim changed nothing; the correct claim still works afterwards.
    stack.service.resolve_reconciliation(OP, submitted=True, provider_submission_id=PID)
    assert stack.status() == ExternalActionStatus.SUCCEEDED
    assert len(stack.provider.ledger_entries()) == 1
    stack.close()


def test_not_submitted_claim_is_accepted_only_with_confirmed_absence_then_retries_once(path):
    stack = Stack(
        path, scripted_outcomes({OP: [OUTCOME.INTERRUPTED_BEFORE_EFFECT]}), evidence=True
    )
    assert stack.service.submit(OP, **_submit_kwargs()) is None
    assert stack.status() == REQUIRED
    stack.service.resolve_reconciliation(OP, submitted=False)
    assert stack.service.submit(OP, **_submit_kwargs())["provider_submission_id"] == PID
    assert stack.provider.attempts(OP) == (OUTCOME.INTERRUPTED_BEFORE_EFFECT, OUTCOME.SUCCESS)
    assert len(stack.provider.ledger_entries()) == 1
    stack.close()


@pytest.mark.parametrize(
    "column, value",
    [
        ("application_id", "application-other"),
        ("job_id", "job-other"),
        ("artifact_sha256", "c" * 64),
        ("candidate_snapshot_sha256", "d" * 64),
    ],
)
def test_conflicting_ledger_identity_is_rejected_even_with_matching_provider_id(
    path, column, value
):
    stack = _ambiguous(path)
    evidence_id = stack.provider.observe(OP).provider_submission_id
    raw = sqlite3.connect(path)
    raw.execute(f"UPDATE sandbox_submission_ledger SET {column} = ?", (value,))
    raw.commit()
    raw.close()
    _expect_rejected(stack, submitted=True, provider_submission_id=evidence_id)
    stack.close()


# ---------------- absence of evidence is not evidence of absence ----------------


class NonAuthoritativeProvider(SQLiteSandboxSubmissionProvider):
    """Models a real provider whose lookup cannot prove that nothing happened."""

    def verify_submission(self, operation_id):
        return SubmissionEvidence(EvidenceState.UNKNOWN)


@pytest.mark.parametrize("claim", [{"submitted": False}, {"submitted": True, "provider_submission_id": PID}])
def test_unknown_evidence_rejects_both_claims_fail_closed(path, claim):
    stack = _ambiguous(path, provider_cls=NonAuthoritativeProvider)
    _expect_rejected(stack, **claim)
    stack.close()


class BrokenLookupProvider(SQLiteSandboxSubmissionProvider):
    def verify_submission(self, operation_id):
        raise sqlite3.OperationalError("lookup unavailable")


class MalformedLookupProvider(SQLiteSandboxSubmissionProvider):
    def verify_submission(self, operation_id):
        return {"state": "present"}


@pytest.mark.parametrize("provider_cls", [BrokenLookupProvider, MalformedLookupProvider])
@pytest.mark.parametrize("claim", [{"submitted": False}, {"submitted": True, "provider_submission_id": PID}])
def test_failed_or_malformed_lookup_rejects_claims(path, provider_cls, claim):
    stack = _ambiguous(path, provider_cls=provider_cls)
    _expect_rejected(stack, **claim)
    stack.close()


def test_evidence_model_and_service_flags_are_validated(path):
    with pytest.raises(ValueError):
        SubmissionEvidence(EvidenceState.PRESENT, job_id="j")
    with pytest.raises(ValueError):
        SubmissionEvidence(EvidenceState.ABSENT_CONFIRMED, job_id="j")
    stack = Stack(path)
    external = stack.service._external_actions
    with pytest.raises(TypeError, match="verify_submission"):
        ApplicationSubmissionService(
            FakeApplicationSubmissionAdapter(), external, require_provider_evidence=True
        )
    with pytest.raises(TypeError, match="boolean"):
        ApplicationSubmissionService(stack.provider, external, require_provider_evidence=1)
    stack.close()


# ----------------------------- restart and crash recovery -----------------------------


def test_evidence_verified_reconciliation_survives_restart(path):
    first = _ambiguous(path)
    first.close()
    restarted = Stack(path, always(OUTCOME.NO_EFFECT), evidence=True)
    _expect_rejected(restarted, submitted=False)
    restarted.service.resolve_reconciliation(OP, submitted=True, provider_submission_id=PID)
    assert restarted.status() == ExternalActionStatus.SUCCEEDED
    assert len(restarted.provider.ledger_entries()) == 1
    restarted.close()


class Crash(BaseException):
    """Simulates process death: not an Exception, so no outcome is persisted."""


class CrashAfterEffectProvider(SQLiteSandboxSubmissionProvider):
    def submit(self, *args, **kwargs):
        super().submit(*args, **kwargs)
        raise Crash()


def test_crash_after_committed_effect_recovers_without_second_effect(path):
    crashing = Stack(path, evidence=True, provider_cls=CrashAfterEffectProvider)
    with pytest.raises(Crash):
        crashing.service.submit(OP, **_submit_kwargs())
    assert crashing.status() == ExternalActionStatus.IN_PROGRESS
    crashing.close()

    recovered = Stack(path, always(OUTCOME.NO_EFFECT), evidence=True)
    recovered.service.resolve_reconciliation(OP, submitted=True, provider_submission_id=PID)
    assert recovered.status() == ExternalActionStatus.SUCCEEDED
    entries = recovered.provider.ledger_entries()
    # Recovery reconciles the in-flight action; it never re-invokes the provider.
    assert len(entries) == 1 and entries[0].replays == 0
    assert recovered.provider.attempts(OP) == (OUTCOME.SUCCESS,)
    recovered.close()


# ------------------------------- SQLite fault injection -------------------------------


class FaultyConnection:
    """Delegating connection that injects one scripted SQLite failure.

    Faults are bound to the provider's own statements so that unrelated repositories
    sharing the connection are unaffected.
    """

    def __init__(self, real, fail_on, *, durable):
        self._real, self._fail_on, self._durable = real, fail_on, durable
        self.tripped = False
        self._ledger_write_pending = False

    def __getattr__(self, name):
        return getattr(self._real, name)

    def execute(self, sql, *args):
        if self._fail_on == "begin" and not self.tripped and sql == "BEGIN IMMEDIATE":
            self.tripped = True
            raise sqlite3.OperationalError("database is locked")
        if "INSERT INTO sandbox_submission_ledger" in sql:
            if self._fail_on == "insert" and not self.tripped:
                self.tripped = True
                raise sqlite3.OperationalError("disk I/O error")
            self._ledger_write_pending = True
        return self._real.execute(sql, *args)

    def commit(self):
        if self._fail_on == "commit" and not self.tripped and self._ledger_write_pending:
            self.tripped = True
            if self._durable:
                self._real.commit()
            else:
                self._real.rollback()
            raise sqlite3.OperationalError("commit failed")
        self._ledger_write_pending = False
        return self._real.commit()


def _inject(stack, fail_on, durable=False):
    faulty = FaultyConnection(stack.database.connection, fail_on, durable=durable)
    stack.database._connection = faulty
    return faulty


@pytest.mark.parametrize("fail_on, durable", [("insert", False), ("commit", False)])
def test_sqlite_failure_without_commit_rolls_back_and_retry_is_safe(path, fail_on, durable):
    stack = Stack(path, evidence=True)
    faulty = _inject(stack, fail_on, durable)
    assert stack.service.submit(OP, **_submit_kwargs()) is None
    assert faulty.tripped and stack.status() == REQUIRED
    assert stack.provider.ledger_entries() == () and stack.provider.attempts(OP) == ()
    _expect_rejected(stack, submitted=True, provider_submission_id=PID)
    stack.service.resolve_reconciliation(OP, submitted=False)
    assert stack.service.submit(OP, **_submit_kwargs())["provider_submission_id"] == PID
    assert len(stack.provider.ledger_entries()) == 1
    stack.close()


def test_commit_error_after_durable_effect_is_resolved_by_evidence_not_retry(path):
    stack = Stack(path, evidence=True)
    _inject(stack, "commit", durable=True)
    assert stack.service.submit(OP, **_submit_kwargs()) is None
    assert stack.status() == REQUIRED
    assert len(stack.provider.ledger_entries()) == 1  # the effect really is durable
    _expect_rejected(stack, submitted=False)
    stack.service.resolve_reconciliation(OP, submitted=True, provider_submission_id=PID)
    assert stack.status() == ExternalActionStatus.SUCCEEDED
    assert len(stack.provider.ledger_entries()) == 1
    stack.close()


def _hold_writer_lock(path):
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    return holder


def test_busy_writer_lock_is_a_definitive_no_effect_then_retry_succeeds(path):
    stack = Stack(path, evidence=True)
    faulty = _inject(stack, "begin")
    assert stack.service.submit(OP, **_submit_kwargs()) is None
    assert faulty.tripped and stack.status() == ExternalActionStatus.FAILED
    assert stack.provider.attempts(OP) == () and stack.provider.ledger_entries() == ()
    assert stack.service.submit(OP, **_submit_kwargs())["provider_submission_id"] == PID
    assert len(stack.provider.ledger_entries()) == 1
    stack.close()


def test_evidence_lookup_under_busy_lock_fails_closed(path):
    stack = _ambiguous(path)
    faulty = _inject(stack, "begin")
    _expect_rejected(stack, submitted=True, provider_submission_id=PID)
    assert faulty.tripped
    stack.service.resolve_reconciliation(OP, submitted=True, provider_submission_id=PID)
    assert stack.status() == ExternalActionStatus.SUCCEEDED
    stack.close()


def test_provider_busy_error_is_reported_as_pre_submission_error(path):
    stack = Stack(path)
    stack.database.connection.execute("PRAGMA busy_timeout = 0")
    holder = _hold_writer_lock(path)
    with pytest.raises(PreSubmissionError, match="busy"):
        stack.provider.submit(OP, "job-1", "application-1", CONTENT, DIGEST, SNAPSHOT)
    holder.rollback()
    holder.close()
    stack.close()


# ------------------------------------ concurrency ------------------------------------


def _run_parallel(path, work):
    barrier = Barrier(2)
    outcomes: list[object] = []

    def worker():
        stack = Stack(path, evidence=True)
        try:
            barrier.wait(timeout=10)
            outcomes.append(work(stack))
        except Exception as exc:
            outcomes.append(exc)
        finally:
            stack.close()

    threads = [Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert len(outcomes) == 2
    return outcomes


def test_concurrent_submissions_through_the_service_yield_one_provider_effect(path):
    Stack(path).close()
    outcomes = _run_parallel(path, lambda s: s.service.submit(OP, **_submit_kwargs()))
    assert any(isinstance(o, dict) for o in outcomes)
    verifier = Stack(path)
    assert len(verifier.provider.ledger_entries()) == 1
    assert verifier.provider.attempts(OP) == (OUTCOME.SUCCESS,)
    verifier.close()


def test_concurrent_reconciliations_resolve_once_with_one_provider_effect(path):
    _ambiguous(path).close()
    _run_parallel(
        path,
        lambda s: s.service.resolve_reconciliation(
            OP, submitted=True, provider_submission_id=PID
        ),
    )
    verifier = Stack(path, evidence=True)
    assert verifier.status() == ExternalActionStatus.SUCCEEDED
    assert len(verifier.provider.ledger_entries()) == 1
    verifier.close()


def test_concurrent_conflicting_claims_never_resolve_against_the_evidence(path):
    _ambiguous(path).close()
    claims = [
        {"submitted": False},
        {"submitted": True, "provider_submission_id": "fake:wrong"},
    ]
    barrier = Barrier(2)
    errors: list[BaseException] = []

    def worker(claim):
        stack = Stack(path, evidence=True)
        try:
            barrier.wait(timeout=10)
            stack.service.resolve_reconciliation(OP, **claim)
        except Exception as exc:
            errors.append(exc)
        finally:
            stack.close()

    threads = [Thread(target=worker, args=(claim,)) for claim in claims]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert len(errors) == 2 and all(isinstance(e, ReconciliationEvidenceError) for e in errors)
    verifier = Stack(path, evidence=True)
    assert verifier.status() == REQUIRED and len(verifier.provider.ledger_entries()) == 1
    verifier.close()
