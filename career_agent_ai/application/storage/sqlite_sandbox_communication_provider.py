"""Durable no-network communication provider with reconciliation evidence."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from enum import Enum
from hashlib import sha256
from typing import Callable

from career_agent_ai.application.communication.communication_adapter import (
    DeliveryEvidence,
    DeliveryEvidenceState,
    PreDeliveryCommunicationError,
)
from career_agent_ai.application.communication.fake_communication_adapter import (
    FakeCommunicationAdapter,
)
from career_agent_ai.application.communication.models import (
    CommunicationMessage,
    MessageDirection,
    validate_identifier,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


class SandboxCommunicationOutcome(str, Enum):
    SUCCESS = "success"
    NO_EFFECT = "no_effect"
    INTERRUPTED_BEFORE_EFFECT = "interrupted_before_effect"
    TIMEOUT_AFTER_EFFECT = "timeout_after_effect"


SandboxCommunicationOutcomePlan = Callable[[str, int], SandboxCommunicationOutcome]


class SQLiteSandboxCommunicationProvider(FakeCommunicationAdapter):
    """SQLite-backed sandbox mailbox with durable idempotency and evidence."""

    def __init__(
        self,
        database: SQLiteDatabase,
        plan: SandboxCommunicationOutcomePlan | None = None,
    ) -> None:
        super().__init__()
        self._database = database
        self._plan = plan or (lambda _key, _attempt: SandboxCommunicationOutcome.SUCCESS)
        connection = database.connection
        connection.execute(
            """CREATE TABLE IF NOT EXISTS sandbox_communication_messages (
                message_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
                sender TEXT NOT NULL, recipient TEXT NOT NULL, subject TEXT NOT NULL,
                body TEXT NOT NULL, direction TEXT NOT NULL,
                created_at TEXT NOT NULL, in_reply_to TEXT
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS sandbox_communication_intents (
                operation_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS sandbox_communication_attempts (
                operation_id TEXT NOT NULL, attempt_no INTEGER NOT NULL,
                outcome TEXT NOT NULL,
                PRIMARY KEY(operation_id, attempt_no)
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS sandbox_communication_deliveries (
                operation_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                message_id TEXT NOT NULL UNIQUE, action TEXT NOT NULL,
                replays INTEGER NOT NULL DEFAULT 0
            )"""
        )
        connection.commit()

    def draft(self, message: CommunicationMessage) -> CommunicationMessage:
        if message.direction != MessageDirection.DRAFT:
            raise ValueError("Sandbox communication drafts must have draft direction.")
        self.calls.append(("draft", message.message_id))
        self._save_message(message)
        return message

    def read(self, message_id: str) -> CommunicationMessage:
        normalized = validate_identifier(message_id, "message_id")
        self.calls.append(("read", normalized))
        message = self._load_message(normalized)
        if message is None:
            raise KeyError(f"Unknown sandbox-provider message: {normalized!r}")
        return message

    def _deliver(
        self, action: str, operation_id: str, message: CommunicationMessage
    ) -> CommunicationMessage:
        operation_id = validate_identifier(operation_id, "operation_id")
        fingerprint = self._fingerprint(action, message)
        connection = self._database.connection
        self._begin()
        try:
            row = connection.execute(
                "SELECT fingerprint FROM sandbox_communication_intents WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO sandbox_communication_intents VALUES (?, ?)",
                    (operation_id, fingerprint),
                )
            elif row[0] != fingerprint:
                raise PreDeliveryCommunicationError(
                    "Sandbox idempotency key was reused with a different message intent."
                )
            delivered_row = connection.execute(
                "SELECT fingerprint, message_id FROM sandbox_communication_deliveries "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if delivered_row is not None:
                if delivered_row[0] != fingerprint:
                    raise PreDeliveryCommunicationError(
                        "Sandbox delivery evidence conflicts with the message intent."
                    )
                connection.execute(
                    "UPDATE sandbox_communication_deliveries SET replays = replays + 1 "
                    "WHERE operation_id = ?",
                    (operation_id,),
                )
                delivered = self._load_message(delivered_row[1])
                if delivered is None:
                    raise ValueError("Sandbox delivery ledger references a missing message.")
                connection.commit()
                return delivered
            attempt = connection.execute(
                "SELECT COUNT(*) FROM sandbox_communication_attempts WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()[0] + 1
            outcome = self._plan(operation_id, attempt)
            if not isinstance(outcome, SandboxCommunicationOutcome):
                raise PreDeliveryCommunicationError(
                    "Sandbox communication outcome plan returned an invalid outcome."
                )
            connection.execute(
                "INSERT INTO sandbox_communication_attempts VALUES (?, ?, ?)",
                (operation_id, attempt, outcome.value),
            )
            self.calls.append((action, operation_id))
            if outcome in {
                SandboxCommunicationOutcome.NO_EFFECT,
                SandboxCommunicationOutcome.INTERRUPTED_BEFORE_EFFECT,
            }:
                connection.commit()
                if outcome == SandboxCommunicationOutcome.INTERRUPTED_BEFORE_EFFECT:
                    raise ConnectionError("Sandbox provider connection was interrupted.")
                raise PreDeliveryCommunicationError("Sandbox provider recorded no delivery.")
            delivered = replace(message, direction=MessageDirection.OUTBOUND)
            self._save_message(delivered, commit=False)
            connection.execute(
                "INSERT INTO sandbox_communication_deliveries "
                "(operation_id, fingerprint, message_id, action) VALUES (?, ?, ?, ?)",
                (operation_id, fingerprint, delivered.message_id, action),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        if outcome == SandboxCommunicationOutcome.TIMEOUT_AFTER_EFFECT:
            raise TimeoutError("Sandbox provider timed out after recording delivery.")
        return delivered

    def verify_delivery(self, operation_id: str) -> DeliveryEvidence:
        """Return evidence only when the durable intent and attempt ledger agree."""
        operation_id = validate_identifier(operation_id, "operation_id")
        connection = self._database.connection
        if connection.in_transaction:
            raise PreDeliveryCommunicationError(
                "Sandbox delivery evidence cannot be verified inside a transaction."
            )
        self._begin()
        try:
            intent = connection.execute(
                "SELECT fingerprint FROM sandbox_communication_intents "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            attempts = connection.execute(
                "SELECT attempt_no, outcome FROM sandbox_communication_attempts "
                "WHERE operation_id = ? ORDER BY attempt_no",
                (operation_id,),
            ).fetchall()
            delivery = connection.execute(
                "SELECT fingerprint, message_id, action "
                "FROM sandbox_communication_deliveries WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()

            unknown = DeliveryEvidence(DeliveryEvidenceState.UNKNOWN)
            if intent is None or not self._valid_fingerprint(intent[0]) or not attempts:
                return unknown

            parsed_attempts: list[SandboxCommunicationOutcome] = []
            for expected_no, (attempt_no, raw_outcome) in enumerate(attempts, start=1):
                if type(attempt_no) is not int or attempt_no != expected_no:
                    return unknown
                try:
                    parsed_attempts.append(SandboxCommunicationOutcome(raw_outcome))
                except (TypeError, ValueError):
                    return unknown

            no_effect_outcomes = {
                SandboxCommunicationOutcome.NO_EFFECT,
                SandboxCommunicationOutcome.INTERRUPTED_BEFORE_EFFECT,
            }
            effect_outcomes = {
                SandboxCommunicationOutcome.SUCCESS,
                SandboxCommunicationOutcome.TIMEOUT_AFTER_EFFECT,
            }
            if delivery is None:
                if all(outcome in no_effect_outcomes for outcome in parsed_attempts):
                    return DeliveryEvidence(DeliveryEvidenceState.ABSENT_CONFIRMED)
                return unknown

            # A delivery must be the terminal provider attempt. Earlier attempts may
            # prove no effect, but a second attempt after an effect is never safe.
            if (
                parsed_attempts[-1] not in effect_outcomes
                or any(outcome in effect_outcomes for outcome in parsed_attempts[:-1])
            ):
                return unknown

            delivery_fingerprint, message_id, action = delivery
            if (
                not self._valid_fingerprint(delivery_fingerprint)
                or delivery_fingerprint != intent[0]
                or not isinstance(action, str)
                or action not in {"send", "reply"}
            ):
                return unknown
            try:
                normalized_message_id = validate_identifier(message_id, "message_id")
                message = self._load_message(normalized_message_id)
                if (
                    message is None
                    or message.direction != MessageDirection.OUTBOUND
                    or self._fingerprint(action, message) != intent[0]
                ):
                    return unknown
            except (AttributeError, KeyError, TypeError, UnicodeError, ValueError):
                return unknown
            return DeliveryEvidence(DeliveryEvidenceState.PRESENT, message)
        finally:
            connection.rollback()

    @staticmethod
    def _valid_fingerprint(value: object) -> bool:
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        )

    def delivery_count(self, operation_id: str) -> int:
        return int(
            self._database.connection.execute(
                "SELECT COUNT(*) FROM sandbox_communication_deliveries WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()[0]
        )

    def _begin(self) -> None:
        try:
            self._database.connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            raise PreDeliveryCommunicationError(
                "Sandbox communication provider is busy; nothing was attempted."
            ) from exc

    def _save_message(self, message: CommunicationMessage, *, commit: bool = True) -> None:
        existing = self._load_message(message.message_id)
        if existing is not None:
            if replace(existing, direction=message.direction) != message:
                raise ValueError("Sandbox message_id is bound to different content.")
            if existing.direction == message.direction:
                return
            if not (
                existing.direction == MessageDirection.DRAFT
                and message.direction == MessageDirection.OUTBOUND
            ):
                raise ValueError("Sandbox message direction cannot be replaced.")
            self._database.connection.execute(
                "UPDATE sandbox_communication_messages SET direction = ? "
                "WHERE message_id = ?",
                (message.direction.value, message.message_id),
            )
        else:
            self._database.connection.execute(
                "INSERT INTO sandbox_communication_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message.message_id, message.thread_id, message.sender,
                    message.recipient, message.subject, message.body,
                    message.direction.value, message.created_at.isoformat(),
                    message.in_reply_to,
                ),
            )
        if commit:
            self._database.connection.commit()

    def _load_message(self, message_id: str) -> CommunicationMessage | None:
        row = self._database.connection.execute(
            "SELECT * FROM sandbox_communication_messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if row is None:
            return None
        from datetime import datetime
        return CommunicationMessage(
            message_id=row[0], thread_id=row[1], sender=row[2], recipient=row[3],
            subject=row[4], body=row[5], direction=MessageDirection(row[6]),
            created_at=datetime.fromisoformat(row[7]), in_reply_to=row[8],
        )

    @staticmethod
    def _fingerprint(action: str, message: CommunicationMessage) -> str:
        encoded = json.dumps(
            [
                action, message.message_id, message.thread_id, message.sender,
                message.recipient, message.subject, message.body,
                message.created_at.isoformat(), message.in_reply_to,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(encoded).hexdigest()
