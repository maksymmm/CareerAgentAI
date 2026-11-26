from __future__ import annotations

from threading import Barrier, Thread

import pytest

from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_sandbox_withdrawal_provider import (
    PreApplicationWithdrawalError,
    SandboxApplicationWithdrawalOutcome,
    SandboxApplicationWithdrawalEvidence,
    SQLiteSandboxApplicationWithdrawalProvider,
    WithdrawalEvidenceState,
    scripted_withdrawal_outcomes,
)


def _provider(path, plan=None):
    database = SQLiteDatabase(str(path))
    provider = SQLiteSandboxApplicationWithdrawalProvider(database, plan)
    return database, provider


def test_successful_withdrawal_is_durable_and_same_key_replay_is_idempotent(tmp_path):
    path = tmp_path / "withdrawal.sqlite"
    database, provider = _provider(path)
    assert provider.is_dry_run is True

    first = provider.withdraw("withdraw:op-1", "application-1", "submission-1")
    assert first.provider_withdrawal_id == "sandbox-withdrawal:application-1"
    assert first.replays == 0
    assert provider.attempts("withdraw:op-1") == (
        SandboxApplicationWithdrawalOutcome.SUCCESS,
    )
    database.close()

    reopened, recovered = _provider(path)
    evidence = recovered.verify_withdrawal("withdraw:op-1")
    assert evidence == SandboxApplicationWithdrawalEvidence(
        WithdrawalEvidenceState.PRESENT,
        application_id="application-1",
        provider_submission_id="submission-1",
        provider_withdrawal_id="sandbox-withdrawal:application-1",
    )

    replay = recovered.withdraw("withdraw:op-1", "application-1", "submission-1")
    assert replay == type(first)(
        operation_id="withdraw:op-1",
        application_id="application-1",
        provider_submission_id="submission-1",
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
    database, provider = _provider(path, plan)
    with pytest.raises(PreApplicationWithdrawalError, match="recorded no withdrawal effect"):
        provider.withdraw("withdraw:op-2", "application-2", "submission-2")
    assert provider.attempts("withdraw:op-2") == (
        SandboxApplicationWithdrawalOutcome.NO_EFFECT,
    )
    assert provider.effect_count("withdraw:op-2") == 0
    assert provider.verify_withdrawal("withdraw:op-2").state is WithdrawalEvidenceState.ABSENT_CONFIRMED
    database.close()

    reopened, recovered = _provider(path, plan)
    receipt = recovered.withdraw("withdraw:op-2", "application-2", "submission-2")
    assert receipt.replays == 0
    assert recovered.attempts("withdraw:op-2") == (
        SandboxApplicationWithdrawalOutcome.NO_EFFECT,
        SandboxApplicationWithdrawalOutcome.SUCCESS,
    )
    assert recovered.effect_count("withdraw:op-2") == 1
    assert recovered.verify_withdrawal("withdraw:op-2").state is WithdrawalEvidenceState.PRESENT
    reopened.close()


def test_timeout_after_effect_is_reconciled_without_a_second_attempt(tmp_path):
    path = tmp_path / "timeout.sqlite"
    plan = scripted_withdrawal_outcomes(
        {"withdraw:op-3": (SandboxApplicationWithdrawalOutcome.TIMEOUT_AFTER_EFFECT,)}
    )
    database, provider = _provider(path, plan)
    with pytest.raises(TimeoutError, match="after recording withdrawal"):
        provider.withdraw("withdraw:op-3", "application-3", "submission-3")
    assert provider.effect_count("withdraw:op-3") == 1
    assert provider.attempts("withdraw:op-3") == (
        SandboxApplicationWithdrawalOutcome.TIMEOUT_AFTER_EFFECT,
    )
    database.close()

    reopened, recovered = _provider(path)
    evidence = recovered.verify_withdrawal("withdraw:op-3")
    assert evidence.state is WithdrawalEvidenceState.PRESENT
    receipt = recovered.withdraw("withdraw:op-3", "application-3", "submission-3")
    assert receipt.replays == 1
    assert recovered.effect_count("withdraw:op-3") == 1
    assert recovered.attempts("withdraw:op-3") == (
        SandboxApplicationWithdrawalOutcome.TIMEOUT_AFTER_EFFECT,
    )
    reopened.close()


def test_operation_and_application_intents_cannot_be_rebound(tmp_path):
    database, provider = _provider(tmp_path / "conflict.sqlite")
    provider.withdraw("withdraw:op-4", "application-4", "submission-4")

    with pytest.raises(PreApplicationWithdrawalError, match="different withdrawal intent"):
        provider.withdraw("withdraw:op-4", "application-4", "submission-other")
    with pytest.raises(PreApplicationWithdrawalError, match="different withdrawal intent"):
        provider.withdraw("withdraw:op-other", "application-4", "submission-4")

    assert provider.intent_count() == 1
    assert provider.effect_count("withdraw:op-4") == 1
    assert len(provider.ledger_entries()) == 1
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

    database, provider = _provider(path, plan)
    blocker = SQLiteDatabase(str(path))
    database.connection.execute("PRAGMA busy_timeout = 0")
    blocker.connection.execute("PRAGMA busy_timeout = 0")
    blocker.connection.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(PreApplicationWithdrawalError, match="busy; nothing was attempted"):
            provider.withdraw("withdraw:busy", "application-busy", "submission-busy")
        assert calls == []
        assert provider.intent_count("withdraw:busy") == 0
        assert provider.attempts("withdraw:busy") == ()
        assert provider.effect_count("withdraw:busy") == 0
    finally:
        blocker.connection.rollback()

    receipt = provider.withdraw("withdraw:busy", "application-busy", "submission-busy")
    assert receipt.application_id == "application-busy"
    assert calls == [("withdraw:busy", 1)]
    database.close()
    blocker.close()


def test_busy_evidence_lookup_never_becomes_confirmed_absence(tmp_path):
    path = tmp_path / "busy-evidence.sqlite"
    database, provider = _provider(path)
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
    barrier = Barrier(2)
    results = []
    errors = []

    def worker():
        database = SQLiteDatabase(str(path))
        try:
            provider = SQLiteSandboxApplicationWithdrawalProvider(database)
            barrier.wait(timeout=10)
            results.append(
                provider.withdraw("withdraw:shared", "application-shared", "submission-shared")
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

    database, verifier = _provider(path)
    assert verifier.effect_count("withdraw:shared") == 1
    assert verifier.attempts("withdraw:shared") == (
        SandboxApplicationWithdrawalOutcome.SUCCESS,
    )
    assert verifier.ledger_entries()[0].replays == 1
    database.close()

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
    database, provider = _provider(path, plan)
    with pytest.raises(ConnectionError, match="connection was interrupted"):
        provider.withdraw(
            "withdraw:op-interrupted", "application-interrupted", "submission-interrupted"
        )
    assert provider.attempts("withdraw:op-interrupted") == (
        SandboxApplicationWithdrawalOutcome.INTERRUPTED_BEFORE_EFFECT,
    )
    assert provider.effect_count("withdraw:op-interrupted") == 0
    assert provider.verify_withdrawal("withdraw:op-interrupted").state is (
        WithdrawalEvidenceState.ABSENT_CONFIRMED
    )
    database.close()

    reopened, recovered = _provider(path, plan)
    receipt = recovered.withdraw(
        "withdraw:op-interrupted", "application-interrupted", "submission-interrupted"
    )
    assert receipt.replays == 0
    assert recovered.attempts("withdraw:op-interrupted") == (
        SandboxApplicationWithdrawalOutcome.INTERRUPTED_BEFORE_EFFECT,
        SandboxApplicationWithdrawalOutcome.SUCCESS,
    )
    assert recovered.effect_count("withdraw:op-interrupted") == 1
    reopened.close()
