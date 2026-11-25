"""Regression tests for application sandbox idempotent replay integrity."""

from __future__ import annotations

import pytest

from career_agent_ai.application.career.application_submission import PreSubmissionError
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_sandbox_submission_provider import (
    SandboxOutcome,
    SQLiteSandboxSubmissionProvider,
)

OPERATION_ID = "application-submit:replay-integrity"
JOB_ID = "job-replay-integrity"
APPLICATION_ID = "application-replay-integrity"
ARTIFACT_SHA256 = "a" * 64
SNAPSHOT_SHA256 = "b" * 64


def _stack(path, plan=None):
    database = SQLiteDatabase(str(path))
    provider = SQLiteSandboxSubmissionProvider(database, plan)
    return database, provider


def _submit(provider):
    return provider.submit(
        OPERATION_ID,
        JOB_ID,
        APPLICATION_ID,
        "synthetic approved application",
        ARTIFACT_SHA256,
        SNAPSHOT_SHA256,
    )


@pytest.mark.parametrize(
    ("statement", "value"),
    [
        (
            "UPDATE sandbox_submission_ledger SET fingerprint = ? WHERE idempotency_key = ?",
            "0" * 64,
        ),
        (
            "UPDATE sandbox_submission_ledger SET job_id = ? WHERE idempotency_key = ?",
            "tampered-job",
        ),
        (
            "UPDATE sandbox_submission_ledger SET application_id = ? WHERE idempotency_key = ?",
            "tampered-application",
        ),
        (
            "UPDATE sandbox_submission_ledger SET artifact_sha256 = ? WHERE idempotency_key = ?",
            "not-a-digest",
        ),
        (
            "UPDATE sandbox_submission_ledger SET candidate_snapshot_sha256 = ? WHERE idempotency_key = ?",
            "c" * 64,
        ),
        (
            "UPDATE sandbox_submission_ledger SET provider_submission_id = ? WHERE idempotency_key = ?",
            "fake:tampered",
        ),
        (
            "UPDATE sandbox_submission_ledger SET replays = ? WHERE idempotency_key = ?",
            1.5,
        ),
        (
            "UPDATE sandbox_submission_ledger SET replays = ? WHERE idempotency_key = ?",
            (1 << 63) - 1,
        ),
    ],
)
def test_replay_fails_closed_on_corrupt_effect_metadata_without_mutation(
    tmp_path, statement, value
):
    path = tmp_path / "application-replay.sqlite"
    database, provider = _stack(path)
    _submit(provider)
    database.connection.execute(statement, (value, OPERATION_ID))
    database.connection.commit()
    before_ledger = database.connection.execute(
        "SELECT idempotency_key, fingerprint, job_id, application_id, artifact_sha256, "
        "candidate_snapshot_sha256, provider_submission_id, replays "
        "FROM sandbox_submission_ledger WHERE idempotency_key = ?",
        (OPERATION_ID,),
    ).fetchone()
    before_attempts = database.connection.execute(
        "SELECT attempt_no, outcome FROM sandbox_submission_attempts "
        "WHERE idempotency_key = ? ORDER BY attempt_no",
        (OPERATION_ID,),
    ).fetchall()
    database.close()

    restarted, provider = _stack(path)
    with pytest.raises(PreSubmissionError):
        _submit(provider)

    after_ledger = restarted.connection.execute(
        "SELECT idempotency_key, fingerprint, job_id, application_id, artifact_sha256, "
        "candidate_snapshot_sha256, provider_submission_id, replays "
        "FROM sandbox_submission_ledger WHERE idempotency_key = ?",
        (OPERATION_ID,),
    ).fetchone()
    after_attempts = restarted.connection.execute(
        "SELECT attempt_no, outcome FROM sandbox_submission_attempts "
        "WHERE idempotency_key = ? ORDER BY attempt_no",
        (OPERATION_ID,),
    ).fetchall()
    assert after_ledger == before_ledger
    assert after_attempts == before_attempts
    restarted.close()


def test_replay_does_not_recreate_a_missing_durable_intent(tmp_path):
    path = tmp_path / "application-missing-intent.sqlite"
    database, provider = _stack(path)
    _submit(provider)
    database.connection.execute(
        "DELETE FROM sandbox_submission_intents WHERE idempotency_key = ?",
        (OPERATION_ID,),
    )
    database.connection.commit()
    database.close()

    restarted, provider = _stack(path)
    with pytest.raises(PreSubmissionError, match="durable intent"):
        _submit(provider)
    assert restarted.connection.execute(
        "SELECT COUNT(*) FROM sandbox_submission_intents WHERE idempotency_key = ?",
        (OPERATION_ID,),
    ).fetchone()[0] == 0
    assert restarted.connection.execute(
        "SELECT replays FROM sandbox_submission_ledger WHERE idempotency_key = ?",
        (OPERATION_ID,),
    ).fetchone()[0] == 0
    assert provider.attempts(OPERATION_ID) == (SandboxOutcome.SUCCESS,)
    restarted.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "DELETE FROM sandbox_submission_attempts WHERE idempotency_key = ?",
        "UPDATE sandbox_submission_attempts SET attempt_no = 2 WHERE idempotency_key = ?",
        "UPDATE sandbox_submission_attempts SET outcome = 'unknown' WHERE idempotency_key = ?",
        "UPDATE sandbox_submission_attempts SET outcome = 'no_effect' WHERE idempotency_key = ?",
        (
            "INSERT INTO sandbox_submission_attempts(idempotency_key, attempt_no, outcome) "
            "VALUES (?, 2, 'no_effect')"
        ),
    ],
)
def test_replay_fails_closed_on_incomplete_or_contradictory_attempt_history(
    tmp_path, mutation
):
    path = tmp_path / "application-attempt-history.sqlite"
    database, provider = _stack(path)
    _submit(provider)
    database.connection.execute(mutation, (OPERATION_ID,))
    database.connection.commit()
    before_attempts = database.connection.execute(
        "SELECT attempt_no, outcome FROM sandbox_submission_attempts "
        "WHERE idempotency_key = ? ORDER BY attempt_no",
        (OPERATION_ID,),
    ).fetchall()
    database.close()

    restarted, provider = _stack(path)
    with pytest.raises(PreSubmissionError, match="attempt history"):
        _submit(provider)
    after_attempts = restarted.connection.execute(
        "SELECT attempt_no, outcome FROM sandbox_submission_attempts "
        "WHERE idempotency_key = ? ORDER BY attempt_no",
        (OPERATION_ID,),
    ).fetchall()
    assert after_attempts == before_attempts
    assert restarted.connection.execute(
        "SELECT replays FROM sandbox_submission_ledger WHERE idempotency_key = ?",
        (OPERATION_ID,),
    ).fetchone()[0] == 0
    restarted.close()


def test_replay_accepts_only_explicit_no_effect_attempts_before_terminal_effect(tmp_path):
    path = tmp_path / "application-valid-retry-replay.sqlite"

    def plan(_operation_id, attempt_no):
        return (
            SandboxOutcome.NO_EFFECT
            if attempt_no == 1
            else SandboxOutcome.SUCCESS
        )

    database, provider = _stack(path, plan)
    with pytest.raises(PreSubmissionError, match="rejected the submission"):
        _submit(provider)
    result = _submit(provider)
    assert provider.attempts(OPERATION_ID) == (
        SandboxOutcome.NO_EFFECT,
        SandboxOutcome.SUCCESS,
    )
    database.close()

    restarted, provider = _stack(path)
    assert _submit(provider) == result
    assert provider.attempts(OPERATION_ID) == (
        SandboxOutcome.NO_EFFECT,
        SandboxOutcome.SUCCESS,
    )
    assert provider.observe(OPERATION_ID).replays == 1
    restarted.close()


def test_replay_does_not_accept_a_rewritten_durable_intent(tmp_path):
    path = tmp_path / "application-corrupt-intent.sqlite"
    database, provider = _stack(path)
    _submit(provider)
    database.connection.execute(
        "UPDATE sandbox_submission_intents SET fingerprint = ? WHERE idempotency_key = ?",
        ("0" * 64, OPERATION_ID),
    )
    database.connection.commit()
    database.close()

    restarted, provider = _stack(path)
    with pytest.raises(PreSubmissionError, match="durable intent"):
        _submit(provider)
    assert restarted.connection.execute(
        "SELECT fingerprint FROM sandbox_submission_intents WHERE idempotency_key = ?",
        (OPERATION_ID,),
    ).fetchone() == ("0" * 64,)
    assert restarted.connection.execute(
        "SELECT replays FROM sandbox_submission_ledger WHERE idempotency_key = ?",
        (OPERATION_ID,),
    ).fetchone() == (0,)
    assert provider.attempts(OPERATION_ID) == (SandboxOutcome.SUCCESS,)
    restarted.close()
