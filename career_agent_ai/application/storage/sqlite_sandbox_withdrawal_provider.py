"""Durable, deterministic, no-network sandbox provider for application withdrawal.

This adapter models an employer-side withdrawal boundary entirely inside a SQLite
sandbox ledger. It never contacts an employer or any network service. The ledger is
authoritative for reconciliation: an absent effect row under the writer lock proves
that no sandbox withdrawal effect committed for the operation.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from typing import Callable, Iterable, Mapping

from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


class PreApplicationWithdrawalError(RuntimeError):
    """The withdrawal was rejected before a sandbox effect could occur."""


class SandboxApplicationWithdrawalOutcome(str, Enum):
    """Deterministic result of one sandbox provider attempt."""

    SUCCESS = "success"
    NO_EFFECT = "no_effect"
    INTERRUPTED_BEFORE_EFFECT = "interrupted_before_effect"
    TIMEOUT_AFTER_EFFECT = "timeout_after_effect"


SandboxApplicationWithdrawalOutcomePlan = Callable[
    [str, int], SandboxApplicationWithdrawalOutcome
]
"""Pure function of operation ID and one-based attempt number."""


class WithdrawalEvidenceState(str, Enum):
    PRESENT = "present"
    ABSENT_CONFIRMED = "absent_confirmed"


@dataclass(frozen=True)
class SandboxApplicationWithdrawalEvidence:
    """Authoritative evidence returned by the sandbox withdrawal ledger."""

    state: WithdrawalEvidenceState
    application_id: str | None = None
    provider_submission_id: str | None = None
    provider_withdrawal_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, WithdrawalEvidenceState):
            raise TypeError("state must be a WithdrawalEvidenceState.")
        values = (
            self.application_id,
            self.provider_submission_id,
            self.provider_withdrawal_id,
        )
        if self.state is WithdrawalEvidenceState.PRESENT:
            if any(not isinstance(value, str) or not value for value in values):
                raise ValueError("Present withdrawal evidence requires every identity field.")
        elif any(value is not None for value in values):
            raise ValueError("Absent withdrawal evidence must not contain identity fields.")


@dataclass(frozen=True)
class SandboxApplicationWithdrawalReceipt:
    """One durable synthetic withdrawal effect."""

    operation_id: str
    application_id: str
    provider_submission_id: str
    provider_withdrawal_id: str
    replays: int = 0


_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS sandbox_application_withdrawal_intents (
        operation_id TEXT PRIMARY KEY,
        fingerprint TEXT NOT NULL,
        application_id TEXT NOT NULL UNIQUE,
        provider_submission_id TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sandbox_application_withdrawal_attempts (
        operation_id TEXT NOT NULL,
        attempt_no INTEGER NOT NULL CHECK(attempt_no >= 1),
        outcome TEXT NOT NULL,
        PRIMARY KEY(operation_id, attempt_no)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sandbox_application_withdrawal_effects (
        operation_id TEXT PRIMARY KEY,
        fingerprint TEXT NOT NULL,
        application_id TEXT NOT NULL UNIQUE,
        provider_submission_id TEXT NOT NULL,
        provider_withdrawal_id TEXT NOT NULL,
        replays INTEGER NOT NULL DEFAULT 0 CHECK(replays >= 0)
    )
    """,
)


def always_withdrawal_outcome(
    outcome: SandboxApplicationWithdrawalOutcome,
) -> SandboxApplicationWithdrawalOutcomePlan:
    """Return a plan that yields one outcome for every attempt."""
    if not isinstance(outcome, SandboxApplicationWithdrawalOutcome):
        raise TypeError("outcome must be a SandboxApplicationWithdrawalOutcome.")
    return lambda _operation_id, _attempt: outcome


def scripted_withdrawal_outcomes(
    script: Mapping[str, Iterable[SandboxApplicationWithdrawalOutcome]],
    *,
    default: SandboxApplicationWithdrawalOutcome = SandboxApplicationWithdrawalOutcome.SUCCESS,
) -> SandboxApplicationWithdrawalOutcomePlan:
    """Return deterministic per-operation outcomes, then the supplied default."""
    if not isinstance(default, SandboxApplicationWithdrawalOutcome):
        raise TypeError("default must be a SandboxApplicationWithdrawalOutcome.")
    frozen: dict[str, tuple[SandboxApplicationWithdrawalOutcome, ...]] = {}
    for operation_id, outcomes in script.items():
        key = _identifier(operation_id, "operation_id")
        sequence = tuple(outcomes)
        if not all(isinstance(item, SandboxApplicationWithdrawalOutcome) for item in sequence):
            raise TypeError("script must contain only sandbox withdrawal outcomes.")
        frozen[key] = sequence

    def plan(operation_id: str, attempt: int) -> SandboxApplicationWithdrawalOutcome:
        sequence = frozen.get(operation_id, ())
        return sequence[attempt - 1] if attempt <= len(sequence) else default

    return plan


class SQLiteSandboxApplicationWithdrawalProvider:
    """SQLite-backed, idempotent employer-withdrawal simulator with no network I/O."""

    def __init__(
        self,
        database: SQLiteDatabase,
        plan: SandboxApplicationWithdrawalOutcomePlan | None = None,
    ) -> None:
        self._database = database
        self._plan = plan or always_withdrawal_outcome(
            SandboxApplicationWithdrawalOutcome.SUCCESS
        )
        connection = database.connection
        if connection.in_transaction:
            raise PreApplicationWithdrawalError(
                "Sandbox withdrawal provider cannot initialize inside an existing transaction."
            )
        try:
            for statement in _SCHEMA:
                connection.execute(statement)
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise

    @property
    def is_dry_run(self) -> bool:
        """The provider is permanently local and performs no network I/O."""
        return True

    def withdraw(
        self,
        operation_id: str,
        application_id: str,
        provider_submission_id: str,
    ) -> SandboxApplicationWithdrawalReceipt:
        """Record one idempotent synthetic withdrawal of a submitted application."""
        operation_id = _identifier(operation_id, "operation_id")
        application_id = _identifier(application_id, "application_id")
        provider_submission_id = _identifier(
            provider_submission_id, "provider_submission_id", maximum=1_000
        )
        fingerprint = _fingerprint(
            operation_id, application_id, provider_submission_id
        )
        connection = self._database.connection
        self._begin(connection)
        try:
            self._require_submission(application_id, provider_submission_id)
            self._bind_intent(
                operation_id, fingerprint, application_id, provider_submission_id
            )
            existing = self._load(operation_id)
            if existing is not None:
                if existing[1] != fingerprint:
                    raise PreApplicationWithdrawalError(
                        "Sandbox idempotency key was reused with a different withdrawal intent."
                    )
                connection.execute(
                    "UPDATE sandbox_application_withdrawal_effects "
                    "SET replays = replays + 1 WHERE operation_id = ?",
                    (operation_id,),
                )
                receipt = self._receipt(existing, replays=existing[5] + 1)
                connection.commit()
                return receipt

            attempt = connection.execute(
                "SELECT COUNT(*) FROM sandbox_application_withdrawal_attempts "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()[0] + 1
            outcome = self._plan(operation_id, attempt)
            if not isinstance(outcome, SandboxApplicationWithdrawalOutcome):
                raise PreApplicationWithdrawalError(
                    "Sandbox withdrawal outcome plan returned an invalid outcome."
                )
            connection.execute(
                "INSERT INTO sandbox_application_withdrawal_attempts "
                "(operation_id, attempt_no, outcome) VALUES (?, ?, ?)",
                (operation_id, attempt, outcome.value),
            )
            if outcome in {
                SandboxApplicationWithdrawalOutcome.NO_EFFECT,
                SandboxApplicationWithdrawalOutcome.INTERRUPTED_BEFORE_EFFECT,
            }:
                connection.commit()
                if outcome is SandboxApplicationWithdrawalOutcome.INTERRUPTED_BEFORE_EFFECT:
                    raise ConnectionError("Sandbox withdrawal connection was interrupted.")
                raise PreApplicationWithdrawalError(
                    "Sandbox provider recorded no withdrawal effect."
                )

            provider_withdrawal_id = f"sandbox-withdrawal:{application_id}"
            connection.execute(
                "INSERT INTO sandbox_application_withdrawal_effects "
                "(operation_id, fingerprint, application_id, provider_submission_id, "
                "provider_withdrawal_id) VALUES (?, ?, ?, ?, ?)",
                (
                    operation_id,
                    fingerprint,
                    application_id,
                    provider_submission_id,
                    provider_withdrawal_id,
                ),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            if connection.in_transaction:
                connection.rollback()
            raise PreApplicationWithdrawalError(
                "Sandbox application already has a different withdrawal intent or effect."
            ) from exc
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise

        receipt = SandboxApplicationWithdrawalReceipt(
            operation_id=operation_id,
            application_id=application_id,
            provider_submission_id=provider_submission_id,
            provider_withdrawal_id=provider_withdrawal_id,
        )
        if outcome is SandboxApplicationWithdrawalOutcome.TIMEOUT_AFTER_EFFECT:
            raise TimeoutError("Sandbox provider timed out after recording withdrawal.")
        return receipt

    def verify_withdrawal(
        self, operation_id: str
    ) -> SandboxApplicationWithdrawalEvidence:
        """Read authoritative evidence while holding the SQLite writer lock.

        A busy or caller-owned transaction raises instead of being interpreted as
        confirmed absence. The read transaction is always rolled back after inspection.
        """
        operation_id = _identifier(operation_id, "operation_id")
        connection = self._database.connection
        self._begin(connection)
        try:
            row = connection.execute(
                "SELECT application_id, provider_submission_id, provider_withdrawal_id "
                "FROM sandbox_application_withdrawal_effects WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        finally:
            connection.rollback()
        if row is None:
            return SandboxApplicationWithdrawalEvidence(
                WithdrawalEvidenceState.ABSENT_CONFIRMED
            )
        return SandboxApplicationWithdrawalEvidence(
            WithdrawalEvidenceState.PRESENT,
            application_id=row[0],
            provider_submission_id=row[1],
            provider_withdrawal_id=row[2],
        )

    def attempts(
        self, operation_id: str
    ) -> tuple[SandboxApplicationWithdrawalOutcome, ...]:
        operation_id = _identifier(operation_id, "operation_id")
        rows = self._database.connection.execute(
            "SELECT outcome FROM sandbox_application_withdrawal_attempts "
            "WHERE operation_id = ? ORDER BY attempt_no",
            (operation_id,),
        ).fetchall()
        return tuple(SandboxApplicationWithdrawalOutcome(row[0]) for row in rows)

    def ledger_entries(self) -> tuple[SandboxApplicationWithdrawalReceipt, ...]:
        rows = self._database.connection.execute(
            "SELECT operation_id, fingerprint, application_id, provider_submission_id, "
            "provider_withdrawal_id, replays "
            "FROM sandbox_application_withdrawal_effects ORDER BY operation_id"
        ).fetchall()
        return tuple(self._receipt(row, replays=row[5]) for row in rows)

    def effect_count(self, operation_id: str) -> int:
        operation_id = _identifier(operation_id, "operation_id")
        return int(
            self._database.connection.execute(
                "SELECT COUNT(*) FROM sandbox_application_withdrawal_effects "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()[0]
        )

    def intent_count(self, operation_id: str | None = None) -> int:
        if operation_id is None:
            row = self._database.connection.execute(
                "SELECT COUNT(*) FROM sandbox_application_withdrawal_intents"
            ).fetchone()
        else:
            operation_id = _identifier(operation_id, "operation_id")
            row = self._database.connection.execute(
                "SELECT COUNT(*) FROM sandbox_application_withdrawal_intents "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return int(row[0])

    @staticmethod
    def _begin(connection: sqlite3.Connection) -> None:
        if connection.in_transaction:
            raise PreApplicationWithdrawalError(
                "Sandbox withdrawal provider cannot run inside an existing transaction."
            )
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            raise PreApplicationWithdrawalError(
                "Sandbox withdrawal provider is busy; nothing was attempted."
            ) from exc

    def _require_submission(
        self, application_id: str, provider_submission_id: str
    ) -> None:
        """Require an exact prior sandbox submission before withdrawal is attempted."""
        try:
            row = self._database.connection.execute(
                "SELECT 1 FROM sandbox_submission_ledger "
                "WHERE application_id = ? AND provider_submission_id = ?",
                (application_id, provider_submission_id),
            ).fetchone()
        except sqlite3.OperationalError as exc:
            raise PreApplicationWithdrawalError(
                "A verified sandbox application submission is required before withdrawal."
            ) from exc
        if row is None:
            raise PreApplicationWithdrawalError(
                "A verified sandbox application submission is required before withdrawal."
            )

    def _bind_intent(
        self,
        operation_id: str,
        fingerprint: str,
        application_id: str,
        provider_submission_id: str,
    ) -> None:
        connection = self._database.connection
        row = connection.execute(
            "SELECT fingerprint, application_id, provider_submission_id "
            "FROM sandbox_application_withdrawal_intents WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO sandbox_application_withdrawal_intents "
                "(operation_id, fingerprint, application_id, provider_submission_id) "
                "VALUES (?, ?, ?, ?)",
                (operation_id, fingerprint, application_id, provider_submission_id),
            )
            return
        if row != (fingerprint, application_id, provider_submission_id):
            raise PreApplicationWithdrawalError(
                "Sandbox idempotency key was reused with a different withdrawal intent."
            )

    def _load(self, operation_id: str) -> tuple[object, ...] | None:
        return self._database.connection.execute(
            "SELECT operation_id, fingerprint, application_id, provider_submission_id, "
            "provider_withdrawal_id, replays "
            "FROM sandbox_application_withdrawal_effects WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()

    @staticmethod
    def _receipt(
        row: tuple[object, ...], *, replays: int
    ) -> SandboxApplicationWithdrawalReceipt:
        return SandboxApplicationWithdrawalReceipt(
            operation_id=str(row[0]),
            application_id=str(row[2]),
            provider_submission_id=str(row[3]),
            provider_withdrawal_id=str(row[4]),
            replays=replays,
        )


def _identifier(value: object, name: str, *, maximum: int = 200) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    ):
        raise ValueError(f"{name} must be a canonical non-empty identifier.")
    value.encode("utf-8", errors="strict")
    return value


def _fingerprint(*parts: str) -> str:
    encoded = json.dumps(
        parts, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()
