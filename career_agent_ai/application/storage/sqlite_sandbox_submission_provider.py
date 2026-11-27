"""Durable, deterministic, no-network sandbox provider for application submissions.

The provider models an external job-application system whose side effects live in
a SQLite *sandbox ledger*. Unlike the in-memory ``FakeApplicationSubmissionAdapter``
its state survives process restarts, so ambiguous outcomes can be reconciled and
"exactly one provider-side effect" can be asserted against durable evidence.

Provider-side invariants (enforced by SQL constraints, not only by Python):

* one ledger row per idempotency key (the external-action ``operation_id``);
* at most one ledger row per ``application_id``;
* a replay of the same key and intent returns the stored result and never adds an
  effect; a replay with a different intent is rejected without any effect.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from typing import Any, Callable, Iterable, Mapping

from career_agent_ai.application.career.application_submission import (
    EvidenceState,
    PreSubmissionError,
    SubmissionEvidence,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


class SandboxOutcome(str, Enum):
    """Configurable result of one provider attempt."""

    SUCCESS = "success"
    NO_EFFECT = "no_effect"
    RETRYABLE_NO_EFFECT = "retryable_no_effect"
    INTERRUPTED_BEFORE_EFFECT = "interrupted_before_effect"
    TIMEOUT_AFTER_EFFECT = "timeout_after_effect"
    EVIDENCE_MISMATCH = "evidence_mismatch"


SandboxOutcomePlan = Callable[[str, int], SandboxOutcome]
"""Pure function of ``(idempotency_key, attempt_number)`` returning the outcome."""

_NO_EFFECT_OUTCOMES = frozenset(
    {SandboxOutcome.NO_EFFECT, SandboxOutcome.RETRYABLE_NO_EFFECT}
)
_EFFECT_OUTCOMES = frozenset(
    {
        SandboxOutcome.SUCCESS,
        SandboxOutcome.TIMEOUT_AFTER_EFFECT,
        SandboxOutcome.EVIDENCE_MISMATCH,
    }
)
_MAX_VERIFIED_ATTEMPTS = 1_000
_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS sandbox_submission_intents (
        idempotency_key TEXT PRIMARY KEY,
        fingerprint TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sandbox_submission_attempts (
        idempotency_key TEXT NOT NULL,
        attempt_no INTEGER NOT NULL CHECK(attempt_no >= 1),
        outcome TEXT NOT NULL,
        PRIMARY KEY (idempotency_key, attempt_no)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sandbox_submission_ledger (
        idempotency_key TEXT PRIMARY KEY,
        fingerprint TEXT NOT NULL,
        job_id TEXT NOT NULL,
        application_id TEXT NOT NULL UNIQUE,
        artifact_sha256 TEXT NOT NULL,
        candidate_snapshot_sha256 TEXT NOT NULL,
        provider_submission_id TEXT NOT NULL,
        replays INTEGER NOT NULL DEFAULT 0 CHECK(replays >= 0)
    )
    """,
)
_LEDGER_COLUMNS = (
    "idempotency_key, fingerprint, job_id, application_id, artifact_sha256, "
    "candidate_snapshot_sha256, provider_submission_id, replays"
)


def always(outcome: SandboxOutcome) -> SandboxOutcomePlan:
    """Return a plan that yields the same outcome for every attempt."""
    if not isinstance(outcome, SandboxOutcome):
        raise TypeError("outcome must be a SandboxOutcome.")
    return lambda _key, _attempt: outcome


def scripted_outcomes(
    script: Mapping[str, Iterable[SandboxOutcome]],
    *,
    default: SandboxOutcome = SandboxOutcome.SUCCESS,
) -> SandboxOutcomePlan:
    """Return a plan that replays per-key outcome sequences, then ``default``."""
    frozen = {key: tuple(outcomes) for key, outcomes in script.items()}
    for outcomes in frozen.values():
        if not all(isinstance(item, SandboxOutcome) for item in outcomes):
            raise TypeError("script must contain only SandboxOutcome values.")

    def plan(key: str, attempt: int) -> SandboxOutcome:
        sequence = frozen.get(key, ())
        return sequence[attempt - 1] if attempt <= len(sequence) else default

    return plan


@dataclass(frozen=True)
class SandboxLedgerEntry:
    """One durable provider-side submission effect."""

    idempotency_key: str
    job_id: str
    application_id: str
    artifact_sha256: str
    candidate_snapshot_sha256: str
    provider_submission_id: str
    replays: int


class SQLiteSandboxSubmissionProvider:
    """``ApplicationSubmissionAdapter`` backed by a durable SQLite sandbox ledger."""

    def __init__(
        self,
        database: SQLiteDatabase,
        plan: SandboxOutcomePlan | None = None,
    ) -> None:
        self._database = database
        self._plan: SandboxOutcomePlan = plan or always(SandboxOutcome.SUCCESS)
        for statement in _SCHEMA:
            self._database.connection.execute(statement)
        self._database.connection.commit()

    @property
    def is_dry_run(self) -> bool:
        """Return True because this provider never performs network I/O."""
        return True

    def submit(
        self,
        operation_id: str,
        job_id: str,
        application_id: str,
        artifact_content: str,
        artifact_sha256: str,
        candidate_snapshot_sha256: str,
    ) -> Mapping[str, Any]:
        """Apply one idempotent simulated submission and its scripted outcome."""
        del artifact_content  # bound to the effect through artifact_sha256
        fingerprint = _fingerprint(
            job_id, application_id, artifact_sha256, candidate_snapshot_sha256
        )
        connection = self._database.connection
        self._begin(connection)
        try:
            self._submission_evidence_in_transaction(operation_id)
            self._bind_intent(operation_id, fingerprint)
            existing = self._load(operation_id)
            if existing is not None:
                result = self._replay(existing, fingerprint)
                connection.commit()
                return result
            outcome = self._next_outcome(operation_id)
            if outcome in _NO_EFFECT_OUTCOMES or (
                outcome == SandboxOutcome.INTERRUPTED_BEFORE_EFFECT
            ):
                connection.commit()
                return self._raise_without_effect(outcome)
            self._insert_effect(
                operation_id, fingerprint, job_id, application_id,
                artifact_sha256, candidate_snapshot_sha256,
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise PreSubmissionError(
                "Sandbox provider already holds a submission for this application."
            ) from exc
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        return self._effect_result(
            outcome, job_id, application_id, artifact_sha256, candidate_snapshot_sha256
        )

    def observe(self, operation_id: str) -> SandboxLedgerEntry | None:
        """Return the durable provider-side evidence for one idempotency key."""
        row = self._load(operation_id)
        return None if row is None else _entry(row)

    def ledger_entries(self) -> tuple[SandboxLedgerEntry, ...]:
        """Return every provider-side effect in deterministic key order."""
        rows = self._database.connection.execute(
            f"SELECT {_LEDGER_COLUMNS} FROM sandbox_submission_ledger "
            "ORDER BY idempotency_key"
        ).fetchall()
        return tuple(_entry(row) for row in rows)

    def attempts(self, operation_id: str) -> tuple[SandboxOutcome, ...]:
        """Return the recorded outcomes of every attempt for one key, in order."""
        rows = self._database.connection.execute(
            "SELECT outcome FROM sandbox_submission_attempts "
            "WHERE idempotency_key = ? ORDER BY attempt_no",
            (operation_id,),
        ).fetchall()
        return tuple(SandboxOutcome(row[0]) for row in rows)

    def verify_submission(self, operation_id: str) -> SubmissionEvidence:
        """Return authoritative evidence after validating the complete durable ledger."""
        connection = self._database.connection
        if connection.in_transaction:
            raise PreSubmissionError(
                "Sandbox evidence cannot be verified inside an existing transaction."
            )
        self._begin(connection)
        try:
            return self._submission_evidence_in_transaction(operation_id)
        finally:
            connection.rollback()

    def _submission_evidence_in_transaction(
        self, operation_id: str
    ) -> SubmissionEvidence:
        """Validate intent, attempts, and effect while the caller holds the writer lock."""
        connection = self._database.connection
        try:
            intent = connection.execute(
                "SELECT fingerprint FROM sandbox_submission_intents "
                "WHERE idempotency_key = ?",
                (operation_id,),
            ).fetchone()
            attempts = connection.execute(
                "SELECT attempt_no, outcome FROM sandbox_submission_attempts "
                "WHERE idempotency_key = ? ORDER BY attempt_no LIMIT ?",
                (operation_id, _MAX_VERIFIED_ATTEMPTS + 1),
            ).fetchall()
            row = self._load(operation_id)
            if len(attempts) > _MAX_VERIFIED_ATTEMPTS:
                raise PreSubmissionError(
                    "Sandbox submission attempt history exceeds the verification limit."
                )
            if intent is None:
                if attempts or row is not None:
                    raise PreSubmissionError(
                        "Sandbox submission evidence contains rows without a durable intent."
                    )
                return SubmissionEvidence(EvidenceState.ABSENT_CONFIRMED)

            fingerprint = intent[0]
            if not _is_sha256(fingerprint):
                raise PreSubmissionError(
                    "Sandbox submission intent fingerprint is malformed."
                )
            if not attempts:
                raise PreSubmissionError(
                    "Sandbox submission intent has no durable attempt history."
                )

            effect_attempts: list[int] = []
            for expected_attempt, attempt_row in enumerate(attempts, start=1):
                attempt_no, outcome_value = attempt_row
                if type(attempt_no) is not int or attempt_no != expected_attempt:
                    raise PreSubmissionError(
                        "Sandbox submission attempt history is not contiguous."
                    )
                try:
                    outcome = SandboxOutcome(outcome_value)
                except (TypeError, ValueError) as exc:
                    raise PreSubmissionError(
                        "Sandbox submission attempt has an unknown outcome."
                    ) from exc
                if outcome in _EFFECT_OUTCOMES:
                    effect_attempts.append(expected_attempt)

            if row is None:
                if effect_attempts:
                    raise PreSubmissionError(
                        "Sandbox submission effect is missing after an effect-producing attempt."
                    )
                return SubmissionEvidence(EvidenceState.ABSENT_CONFIRMED)

            if len(effect_attempts) != 1 or effect_attempts[0] != len(attempts):
                raise PreSubmissionError(
                    "Sandbox submission effect is not bound to one final provider attempt."
                )
            if len(row) != 8 or row[0] != operation_id or row[1] != fingerprint:
                raise PreSubmissionError(
                    "Sandbox submission effect identity conflicts with its intent."
                )

            (
                job_id,
                application_id,
                artifact_sha256,
                candidate_snapshot_sha256,
                provider_id,
                replays,
            ) = row[2:]
            identity_fields = (job_id, application_id, provider_id)
            if any(
                not isinstance(value, str) or not value or value.strip() != value
                for value in identity_fields
            ):
                raise PreSubmissionError(
                    "Sandbox submission effect contains a malformed identifier."
                )
            if not _is_sha256(artifact_sha256) or not _is_sha256(candidate_snapshot_sha256):
                raise PreSubmissionError(
                    "Sandbox submission effect contains a malformed digest."
                )
            if _fingerprint(
                job_id, application_id, artifact_sha256, candidate_snapshot_sha256
            ) != fingerprint:
                raise PreSubmissionError(
                    "Sandbox submission effect fields do not match the intent fingerprint."
                )
            if provider_id != f"fake:{application_id}":
                raise PreSubmissionError(
                    "Sandbox provider submission ID does not match the application identity."
                )
            if type(replays) is not int or replays < 0:
                raise PreSubmissionError(
                    "Sandbox submission replay counter is malformed."
                )

            return SubmissionEvidence(
                EvidenceState.PRESENT,
                job_id=job_id,
                application_id=application_id,
                provider_submission_id=provider_id,
                artifact_sha256=artifact_sha256,
                candidate_snapshot_sha256=candidate_snapshot_sha256,
            )
        except PreSubmissionError:
            raise
        except Exception as exc:
            raise PreSubmissionError(
                "Sandbox submission evidence is malformed or inconsistent."
            ) from exc

    @staticmethod
    def _begin(connection: sqlite3.Connection) -> None:
        if connection.in_transaction:
            return
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            raise PreSubmissionError(
                "Sandbox provider is busy; nothing was attempted."
            ) from exc

    def _load(self, operation_id: str) -> tuple[Any, ...] | None:
        return self._database.connection.execute(
            f"SELECT {_LEDGER_COLUMNS} FROM sandbox_submission_ledger "
            "WHERE idempotency_key = ?",
            (operation_id,),
        ).fetchone()

    def _bind_intent(self, operation_id: str, fingerprint: str) -> None:
        row = self._database.connection.execute(
            "SELECT fingerprint FROM sandbox_submission_intents "
            "WHERE idempotency_key = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            self._database.connection.execute(
                "INSERT INTO sandbox_submission_intents(idempotency_key, fingerprint) "
                "VALUES (?, ?)",
                (operation_id, fingerprint),
            )
            return
        if row[0] != fingerprint:
            raise PreSubmissionError(
                "Sandbox idempotency key was reused with a different submission intent."
            )

    def _replay(
        self, row: tuple[Any, ...], fingerprint: str
    ) -> Mapping[str, Any]:
        if row[1] != fingerprint:
            raise PreSubmissionError(
                "Sandbox idempotency key was reused with a different submission intent."
            )
        self._database.connection.execute(
            "UPDATE sandbox_submission_ledger SET replays = replays + 1 "
            "WHERE idempotency_key = ?",
            (row[0],),
        )
        return {
            "job_id": row[2],
            "application_id": row[3],
            "provider_submission_id": row[6],
            "artifact_sha256": row[4],
            "candidate_snapshot_sha256": row[5],
        }

    def _next_outcome(self, operation_id: str) -> SandboxOutcome:
        connection = self._database.connection
        attempt = connection.execute(
            "SELECT COUNT(*) FROM sandbox_submission_attempts WHERE idempotency_key = ?",
            (operation_id,),
        ).fetchone()[0] + 1
        outcome = self._plan(operation_id, attempt)
        if not isinstance(outcome, SandboxOutcome):
            raise PreSubmissionError("Sandbox outcome plan returned an invalid outcome.")
        connection.execute(
            "INSERT INTO sandbox_submission_attempts(idempotency_key, attempt_no, outcome) "
            "VALUES (?, ?, ?)",
            (operation_id, attempt, outcome.value),
        )
        return outcome

    def _insert_effect(
        self,
        operation_id: str,
        fingerprint: str,
        job_id: str,
        application_id: str,
        artifact_sha256: str,
        candidate_snapshot_sha256: str,
    ) -> None:
        self._database.connection.execute(
            f"INSERT INTO sandbox_submission_ledger({_LEDGER_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            (
                operation_id, fingerprint, job_id, application_id,
                artifact_sha256, candidate_snapshot_sha256,
                f"fake:{application_id}",
            ),
        )

    @staticmethod
    def _raise_without_effect(outcome: SandboxOutcome) -> Mapping[str, Any]:
        if outcome == SandboxOutcome.INTERRUPTED_BEFORE_EFFECT:
            raise ConnectionError("Sandbox provider connection was interrupted.")
        if outcome == SandboxOutcome.RETRYABLE_NO_EFFECT:
            raise PreSubmissionError("Sandbox provider is temporarily unavailable.")
        raise PreSubmissionError("Sandbox provider rejected the submission.")

    @staticmethod
    def _effect_result(
        outcome: SandboxOutcome,
        job_id: str,
        application_id: str,
        artifact_sha256: str,
        candidate_snapshot_sha256: str,
    ) -> Mapping[str, Any]:
        if outcome == SandboxOutcome.TIMEOUT_AFTER_EFFECT:
            raise TimeoutError("Sandbox provider timed out after recording the effect.")
        returned_digest = artifact_sha256
        if outcome == SandboxOutcome.EVIDENCE_MISMATCH:
            returned_digest = "0" * 64 if artifact_sha256 != "0" * 64 else "f" * 64
        return {
            "job_id": job_id,
            "application_id": application_id,
            "provider_submission_id": f"fake:{application_id}",
            "artifact_sha256": returned_digest,
            "candidate_snapshot_sha256": candidate_snapshot_sha256,
        }


def _is_sha256(value: Any) -> bool:
    """Return whether value is a canonical lowercase SHA-256 digest."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _fingerprint(*parts: str) -> str:
    if not all(isinstance(part, str) for part in parts):
        raise TypeError("Sandbox intent fingerprint fields must be strings.")
    encoded = json.dumps(
        parts,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _entry(row: tuple[Any, ...]) -> SandboxLedgerEntry:
    return SandboxLedgerEntry(
        idempotency_key=row[0],
        job_id=row[2],
        application_id=row[3],
        artifact_sha256=row[4],
        candidate_snapshot_sha256=row[5],
        provider_submission_id=row[6],
        replays=int(row[7]),
    )
