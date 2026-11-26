from __future__ import annotations

from hashlib import sha256
from threading import Barrier, Thread

import pytest

from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_sandbox_submission_provider import (
    SQLiteSandboxSubmissionProvider,
)
from career_agent_ai.application.storage.sqlite_sandbox_withdrawal_provider import (
    PreApplicationWithdrawalError,
    SandboxApplicationWithdrawalOutcome,
    SandboxApplicationWithdrawalEvidence,
    SQLiteSandboxApplicationWithdrawalProvider,
    WithdrawalEvidenceState,
    scripted_withdrawal_outcomes,
)


def _seed_submission(database, application_id):
    provider = SQLiteSandboxSubmissionProvider(database)
    content = f"Approved synthetic application artifact for {application_id}."
    artifact_digest = sha256(content.encode("utf-8")).hexdigest()
    snapshot_digest = sha256(f"snapshot:{application_id}".encode("utf-8")).hexdigest()
    result = provider.submit(
        f"submit:{application_id}",
        f"job:{application_id}",
        application_id,
        content,
        artifact_digest,
        snapshot_digest,
    )
    return result["provider_submission_id"]


def _provider(path, application_id="application-1", plan=None, *, seed=True):
    database = SQLiteDatabase(str(path))
    if seed:
        submission_id = _seed_submission(database, application_id)
    else:
        row = database.connection.execute(
            "SELECT provider_submission_id FROM sandbox_submission_ledger "
            "WHERE application_id = ?",
            (application_id,),
        ).fetchone()
        submission_id = None if row is None else row[0]
    provider = SQLiteSandboxApplicationWithdrawalProvider(database, plan)
    return database, provider, submission_id


def test_successful_withdrawal_is_durable_and_same_key_replay_is_idempotent(tmp_path):
    path = tmp_path / "withdrawal.sqlite"
    database, provider, submission_id = _provider(path, "application-1")
    assert provider.is_dry_run is True

    first = provider.withdraw("withdraw:op-1", "application-1", submission_id)
    assert first.provider_withdrawal_id == "sandbox-withdrawal:application-1"
    assert first.replays == 0
    assert provider.attempts("withdraw:op-1") == (
        SandboxApplicationWithdrawalOutcome.SUCCESS,
    )
    database.close()

    reopened, recovered, persisted_submission_id = _provider(
        path, "application-1", seed=False
    )
    assert persisted_submission_id == submission_id
    evidence = recovered.verify_withdrawal("withdraw:op-1")
    assert evidence == SandboxApplicationWithdrawalEvidence(
        WithdrawalEvidenceState.PRESENT,
        application_id="application-1",
        provider_submission_id=submission_id,
        provider_withdrawal_id="sandbox-withdrawal:application-1",
    )

    replay = recovered.withdraw("withdraw:op-1", "application-1", submission_id)
    assert replay == type(first)(
        operation_id="withdraw:op-1",
        application_id="application-1",
        provider_submission_id=submission_id,
        provider_withdrawal_id="sandbox-withdrawal:application-1",
        replays=1,
    )
    assert recovered.effect_count("withdraw:op-1") == 1
    assert recovered.attempts("withdraw:op-1") == (
        SandboxApplicationWithdrawalOutcome.SUCCESS,
    )
    assert recovered.ledger_entries() == (replay,)
    assert not reopened.connection.in_transaction
    reopened.close()


def test_confirmed_no_effect_can_retry_same_intent_after_restart(tmp_path):
    path = tmp_path / "no-effect.sqlite"
    plan = scripted_withdrawal_outcomes(
        {
            "withdraw:op-2": (
                SandboxApplicationWithdrawalOutcome.NO_EFFECT,
                SandboxApplicationWithdrawalOutcome.SUCCESS,
            )
        }
    )
    database, provider, submission_id = _provider(path, "application-2", plan)
    with pytest.raises(PreApplicationWithdrawalError, match="recorded no withdrawal effect"):
        provider.withdraw("withdraw:op-2", "application-2", submission_id)
    assert provider.attempts("withdraw:op-2") == (
        SandboxApplicationWithdrawalOutcome.NO_EFFECT,
    )
    assert provider.effect_count("withdraw:op-2") == 0
    assert provider.verify_withdrawal("withdraw:op-2").state is WithdrawalEvidenceState.ABSENT_CONFIRMED
    database.close()

    reopened, recovered, persisted_submission_id = _provider(
        path, "application-2", plan, seed=False
    )
    assert persisted_submission_id == submission_id
    receipt = recovered.withdraw("withdraw:op-2", "application-2", submission_id)
    assert receipt.replays == 0
    assert recovered.attempts("withdraw:op-2") == (
        SandboxApplicationWithdrawalOutcome.NO_EFFECT,
        SandboxApplicationWithdrawalOutcome.SUCCESS,
    )
    assert recovered.effect_count("withdraw:op-2") == 1
    assert recovered.verify_withdrawal("withdraw:op-2").state is WithdrawalEvidenceState.PRESENT
    reopened.close()


def test_interrupted_before_effect_can_retry_after_restart_without_replay(tmp_path):
    path = tmp_path / "interrupted.sqlite"
    plan = scripted_withdrawal_outcomes(
        {
            "withdraw:op-interrupted": (
                SandboxApplicationWithdrawalOutcome.INTERRUPTED_BEFORE_EFFECT,
                SandboxApplicationWithdrawalOutcome.SUCCESS,
            )
        }
    )
    database, provider, submission_id = _provider(
        path, "application-interrupted", plan
    )
    with pytest.raises(ConnectionError, match="connection was interrupted"):
        provider.withdraw(
            "withdraw:op-interrupted", "application-interrupted", submission_id
        )
    assert provider.attempts("withdraw:op-interrupted") == (
        SandboxApplicationWithdrawalOutcome.INTERRUPTED_BEFORE_EFFECT,
    )
    assert provider.effect_count("withdraw:op-interrupted") == 0
    assert provider.verify_withdrawal("withdraw:op-interrupted").state is (
        WithdrawalEvidenceState.ABSENT_CONFIRMED
    )
    database.close()

    reopened, recovered, persisted_submission_id = _provider(
        path, "application-interrupted", plan, seed=False
    )
    assert persisted_submission_id == submission_id
    receipt = recovered.withdraw(
        "withdraw:op-interrupted", "application-interrupted", submission_id
    )
    assert receipt.replays == 0
    assert recovered.attempts("withdraw:op-interrupted") == (
        SandboxApplicationWithdrawalOutcome.INTERRUPTED_BEFORE_EFFECT,
        SandboxApplicationWithdrawalOutcome.SUCCESS,
    )
    assert recovered.effect_count("withdraw:op-interrupted") == 1
    reopened.close()


def test_timeout_after_effect_is_reconciled_without_a_second_attempt(tmp_path):
    path = tmp_path / "timeout.sqlite"
    plan = scripted_withdrawal_outcomes(
        {"withdraw:op-3": (SandboxApplicationWithdrawalOutcome.TIMEOUT_AFTER_EFFECT,)}
    )
    database, provider, submission_id = _provider(path, "application-3", plan)
    with pytest.raises(TimeoutError, match="after recording withdrawal"):
        provider.withdraw("withdraw:op-3", "application-3", submission_id)
    assert provider.effect_count("withdraw:op-3") == 1
    assert provider.attempts("withdraw:op-3") == (
        SandboxApplicationWithdrawalOutcome.TIMEOUT_AFTER_EFFECT,
    )
    database.close()

    reopened, recovered, persisted_submission_id = _provider(
        path, "application-3", seed=False
    )
    assert persisted_submission_id == submission_id
    evidence = recovered.verify_withdrawal("withdraw:op-3")
    assert evidence.state is WithdrawalEvidenceState.PRESENT
    receipt = recovered.withdraw("withdraw:op-3", "application-3", submission_id)
    assert receipt.replays == 1
    assert recovered.effect_count("withdraw:op-3") == 1
    assert recovered.attempts("withdraw:op-3") == (
        SandboxApplicationWithdrawalOutcome.TIMEOUT_AFTER_EFFECT,
    )
    reopened.close()


def test_operation_and_application_intents_cannot_be_rebound(tmp_path):
    database, provider, submission_id = _provider(
        tmp_path / "conflict.sqlite", "application-4"
    )
    provider.withdraw("withdraw:op-4", "application-4", submission_id)

    with pytest.raises(PreApplicationWithdrawalError, match="different withdrawal intent"):
        provider.withdraw("withdraw:op-4", "application-4", "different-submission-id")
    with pytest.raises(PreApplicationWithdrawalError, match="different withdrawal intent"):
        provider.withdraw("withdraw:op-other", "application-4", submission_id)

    assert provider.intent_count() == 1
    assert provider.effect_count("withdraw:op-4") == 1
    assert len(provider.ledger_entries()) == 1
    database.close()


def test_withdrawal_requires_a_verified_prior_sandbox_submission(tmp_path):
    database = SQLiteDatabase(str(tmp_path / "missing-submission.sqlite"))
    calls = []

    def plan(operation_id, attempt):
        calls.append((operation_id, attempt))
        return SandboxApplicationWithdrawalOutcome.SUCCESS

    provider = SQLiteSandboxApplicationWithdrawalProvider(database, plan)
    with pytest.raises(
        PreApplicationWithdrawalError,
        match="verified sandbox application submission is required",
    ):
        provider.withdraw("withdraw:missing", "application-missing", "fake:application-missing")

    assert calls == []
    assert provider.intent_count() == 0
    assert provider.attempts("withdraw:missing") == ()
    assert provider.effect_count("withdraw:missing") == 0
    database.close()


def test_constructor_rejects_ambient_transaction_without_committing_caller_work(tmp_path):
    database = SQLiteDatabase(str(tmp_path / "ambient.sqlite"))
    connection = database.connection
    connection.execute("CREATE TABLE caller_owned (value TEXT NOT NULL)")
    connection.commit()
    connection.execute("INSERT INTO caller_owned(value) VALUES ('pending')")
    assert connection.in_transaction

    with pytest.raises(
        PreApplicationWithdrawalError,
        match="initialize inside an existing transaction",
    ):
        SQLiteSandboxApplicationWithdrawalProvider(database)

    assert connection.in_transaction
    existing = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {
        "sandbox_application_withdrawal_intents",
        "sandbox_application_withdrawal_attempts",
        "sandbox_application_withdrawal_effects",
    }.isdisjoint(existing)
    connection.rollback()
    assert connection.execute("SELECT COUNT(*) FROM caller_owned").fetchone()[0] == 0

    provider = SQLiteSandboxApplicationWithdrawalProvider(database)
    assert provider.intent_count() == 0
    database.close()


def test_busy_writer_rejects_before_intent_attempt_or_outcome_plan(tmp_path):
    path = tmp_path / "busy.sqlite"
    calls = []

    def plan(operation_id, attempt):
        calls.append((operation_id, attempt))
        return SandboxApplicationWithdrawalOutcome.SUCCESS

    database, provider, submission_id = _provider(path, "application-busy", plan)
    blocker = SQLiteDatabase(str(path))
    database.connection.execute("PRAGMA busy_timeout = 0")
    blocker.connection.execute("PRAGMA busy_timeout = 0")
    blocker.connection.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(PreApplicationWithdrawalError, match="busy; nothing was attempted"):
            provider.withdraw("withdraw:busy", "application-busy", submission_id)
        assert calls == []
        assert provider.intent_count("withdraw:busy") == 0
        assert provider.attempts("withdraw:busy") == ()
        assert provider.effect_count("withdraw:busy") == 0
    finally:
        blocker.connection.rollback()

    receipt = provider.withdraw("withdraw:busy", "application-busy", submission_id)
    assert receipt.application_id == "application-busy"
    assert calls == [("withdraw:busy", 1)]
    database.close()
    blocker.close()


def test_busy_evidence_lookup_never_becomes_confirmed_absence(tmp_path):
    path = tmp_path / "busy-evidence.sqlite"
    database = SQLiteDatabase(str(path))
    provider = SQLiteSandboxApplicationWithdrawalProvider(database)
    blocker = SQLiteDatabase(str(path))
    database.connection.execute("PRAGMA busy_timeout = 0")
    blocker.connection.execute("PRAGMA busy_timeout = 0")
    blocker.connection.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(PreApplicationWithdrawalError, match="busy; nothing was attempted"):
            provider.verify_withdrawal("withdraw:unknown")
    finally:
        blocker.connection.rollback()

    evidence = provider.verify_withdrawal("withdraw:unknown")
    assert evidence.state is WithdrawalEvidenceState.ABSENT_CONFIRMED
    database.close()
    blocker.close()


def test_concurrent_same_intent_produces_one_effect_and_one_attempt(tmp_path):
    path = tmp_path / "concurrent.sqlite"
    seed_database = SQLiteDatabase(str(path))
    submission_id = _seed_submission(seed_database, "application-shared")
    seed_database.close()

    barrier = Barrier(2)
    results = []
    errors = []

    def worker():
        database = SQLiteDatabase(str(path))
        try:
            provider = SQLiteSandboxApplicationWithdrawalProvider(database)
            barrier.wait(timeout=10)
            results.append(
                provider.withdraw("withdraw:shared", "application-shared", submission_id)
            )
        except BaseException as exc:
            errors.append(exc)
        finally:
            database.close()

    threads = [Thread(target=worker), Thread(target=worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 2
    assert {item.provider_withdrawal_id for item in results} == {
        "sandbox-withdrawal:application-shared"
    }

    database = SQLiteDatabase(str(path))
    verifier = SQLiteSandboxApplicationWithdrawalProvider(database)
    assert verifier.effect_count("withdraw:shared") == 1
    assert verifier.attempts("withdraw:shared") == (
        SandboxApplicationWithdrawalOutcome.SUCCESS,
    )
    assert verifier.ledger_entries()[0].replays == 1
    database.close()
