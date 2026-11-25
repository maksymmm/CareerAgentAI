"""Durable no-network calendar provider with authoritative reconciliation evidence."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime
from enum import Enum
from hashlib import sha256
from typing import Callable

from career_agent_ai.application.scheduling.calendar_adapter import (
    CalendarEvidence,
    CalendarEvidenceState,
    PreCalendarActionError,
)
from career_agent_ai.application.scheduling.fake_calendar_adapter import FakeCalendarAdapter
from career_agent_ai.application.scheduling.models import (
    ScheduleEvent,
    ScheduleEventType,
    ScheduleStatus,
    validate_schedule_identifier,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


class SandboxCalendarOutcome(str, Enum):
    SUCCESS = "success"
    NO_EFFECT = "no_effect"
    INTERRUPTED_BEFORE_EFFECT = "interrupted_before_effect"
    TIMEOUT_AFTER_EFFECT = "timeout_after_effect"


SandboxCalendarOutcomePlan = Callable[[str, int], SandboxCalendarOutcome]


class SQLiteSandboxCalendarProvider(FakeCalendarAdapter):
    """SQLite-backed sandbox calendar with durable idempotency and evidence."""

    _NO_EFFECT_OUTCOMES = frozenset(
        {
            SandboxCalendarOutcome.NO_EFFECT,
            SandboxCalendarOutcome.INTERRUPTED_BEFORE_EFFECT,
        }
    )
    _EFFECT_OUTCOMES = frozenset(
        {
            SandboxCalendarOutcome.SUCCESS,
            SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT,
        }
    )

    def __init__(
        self,
        database: SQLiteDatabase,
        plan: SandboxCalendarOutcomePlan | None = None,
    ) -> None:
        super().__init__()
        self._database = database
        self._plan = plan or (lambda _key, _attempt: SandboxCalendarOutcome.SUCCESS)
        connection = database.connection
        connection.execute(
            """CREATE TABLE IF NOT EXISTS sandbox_calendar_intents (
                operation_id TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                intent_json TEXT
            )"""
        )
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(sandbox_calendar_intents)"
            ).fetchall()
        }
        if "intent_json" not in columns:
            # Legacy rows cannot be upgraded from a digest alone. They remain
            # readable as UNKNOWN until an operator can establish their provenance.
            connection.execute(
                "ALTER TABLE sandbox_calendar_intents ADD COLUMN intent_json TEXT"
            )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS sandbox_calendar_attempts (
                operation_id TEXT NOT NULL, attempt_no INTEGER NOT NULL,
                outcome TEXT NOT NULL, PRIMARY KEY(operation_id, attempt_no)
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS sandbox_calendar_effects (
                operation_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                action TEXT NOT NULL, event_json TEXT NOT NULL,
                replays INTEGER NOT NULL DEFAULT 0
            )"""
        )
        connection.commit()

    def _deliver(
        self,
        action: str,
        operation_id: str,
        delivered: ScheduleEvent,
        *,
        source_event: ScheduleEvent | None = None,
        intent_arguments: tuple[object, ...] = (),
    ) -> ScheduleEvent:
        operation_id = validate_schedule_identifier(operation_id, "operation_id")
        if source_event is None:
            raise PreCalendarActionError("Sandbox calendar source intent is missing.")

        event_json = self._encode_event(delivered)
        intent_document = [
            action,
            json.loads(self._encode_event(source_event)),
            list(intent_arguments),
            json.loads(event_json),
        ]
        intent_json = self._canonical_json(intent_document)
        fingerprint = sha256(intent_json.encode("utf-8")).hexdigest()

        connection = self._database.connection
        self._begin()
        try:
            intent = connection.execute(
                "SELECT fingerprint, intent_json FROM sandbox_calendar_intents "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            attempt_rows = connection.execute(
                "SELECT attempt_no, outcome FROM sandbox_calendar_attempts "
                "WHERE operation_id = ? ORDER BY attempt_no",
                (operation_id,),
            ).fetchall()
            attempts = self._parse_attempts(attempt_rows)
            effect = connection.execute(
                "SELECT fingerprint, action, event_json, replays FROM sandbox_calendar_effects "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()

            if attempts is None:
                raise PreCalendarActionError(
                    "Sandbox calendar attempt history is corrupt; reconciliation is required."
                )

            if intent is None:
                if attempts or effect is not None:
                    raise PreCalendarActionError(
                        "Sandbox calendar evidence exists without its durable intent."
                    )
                connection.execute(
                    "INSERT INTO sandbox_calendar_intents "
                    "(operation_id, fingerprint, intent_json) VALUES (?, ?, ?)",
                    (operation_id, fingerprint, intent_json),
                )
            else:
                decoded_intent = self._decode_intent(intent[0], intent[1])
                if decoded_intent is None:
                    raise PreCalendarActionError(
                        "Sandbox calendar intent evidence is incomplete or corrupt; "
                        "reconciliation is required."
                    )
                if (
                    intent[1] != intent_json
                    or decoded_intent[0] != action
                    or decoded_intent[1] != delivered
                ):
                    raise PreCalendarActionError(
                        "Sandbox idempotency key was reused with a different calendar intent."
                    )
                if not attempts:
                    raise PreCalendarActionError(
                        "Sandbox calendar intent has no attempt history; reconciliation is required."
                    )

            if effect is not None:
                replay_count = effect[3]
                if (
                    not self._attempts_allow_effect(attempts)
                    or effect[0] != fingerprint
                    or effect[1] != action
                    or effect[2] != event_json
                ):
                    raise PreCalendarActionError(
                        "Sandbox calendar effect conflicts with its canonical intent evidence."
                    )
                if (
                    type(replay_count) is not int
                    or replay_count < 0
                    or replay_count >= (1 << 63) - 1
                ):
                    raise PreCalendarActionError(
                        "Sandbox calendar replay counter is malformed or exhausted."
                    )
                cursor = connection.execute(
                    "UPDATE sandbox_calendar_effects SET replays = replays + 1 "
                    "WHERE operation_id = ? AND replays = ?",
                    (operation_id, replay_count),
                )
                if cursor.rowcount != 1:
                    raise PreCalendarActionError(
                        "Sandbox calendar replay evidence changed before replay accounting."
                    )
                connection.commit()
                return self._decode_event(effect[2])

            if attempts and not self._attempts_allow_absence(attempts):
                raise PreCalendarActionError(
                    "Sandbox calendar attempts do not prove a retry is safe."
                )

            attempt = len(attempts) + 1
            outcome = self._plan(operation_id, attempt)
            if not isinstance(outcome, SandboxCalendarOutcome):
                raise PreCalendarActionError(
                    "Sandbox calendar outcome plan returned an invalid outcome."
                )
            connection.execute(
                "INSERT INTO sandbox_calendar_attempts VALUES (?, ?, ?)",
                (operation_id, attempt, outcome.value),
            )
            self.calls.append((action, operation_id))
            if outcome in self._NO_EFFECT_OUTCOMES:
                connection.commit()
                if outcome == SandboxCalendarOutcome.INTERRUPTED_BEFORE_EFFECT:
                    raise ConnectionError("Sandbox calendar connection was interrupted.")
                raise PreCalendarActionError("Sandbox provider recorded no calendar effect.")

            connection.execute(
                "INSERT INTO sandbox_calendar_effects "
                "(operation_id, fingerprint, action, event_json) VALUES (?, ?, ?, ?)",
                (operation_id, fingerprint, action, event_json),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise

        if outcome == SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT:
            raise TimeoutError("Sandbox calendar timed out after recording the effect.")
        return delivered

    def verify_calendar_action(self, operation_id: str) -> CalendarEvidence:
        """Verify canonical intent, complete attempts, and the exact delivered effect."""
        operation_id = validate_schedule_identifier(operation_id, "operation_id")
        connection = self._database.connection
        if connection.in_transaction:
            raise PreCalendarActionError(
                "Sandbox calendar evidence cannot be verified inside a transaction."
            )
        self._begin()
        try:
            intent = connection.execute(
                "SELECT fingerprint, intent_json FROM sandbox_calendar_intents "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            attempt_rows = connection.execute(
                "SELECT attempt_no, outcome FROM sandbox_calendar_attempts "
                "WHERE operation_id = ? ORDER BY attempt_no",
                (operation_id,),
            ).fetchall()
            effect = connection.execute(
                "SELECT fingerprint, action, event_json FROM sandbox_calendar_effects "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        finally:
            connection.rollback()

        attempts = self._parse_attempts(attempt_rows)
        if intent is None or attempts is None or not attempts:
            return CalendarEvidence(CalendarEvidenceState.UNKNOWN)
        decoded_intent = self._decode_intent(intent[0], intent[1])
        if decoded_intent is None:
            return CalendarEvidence(CalendarEvidenceState.UNKNOWN)

        if effect is None:
            if self._attempts_allow_absence(attempts):
                return CalendarEvidence(CalendarEvidenceState.ABSENT_CONFIRMED)
            return CalendarEvidence(CalendarEvidenceState.UNKNOWN)

        action, expected_event, expected_event_json = decoded_intent
        effect_fingerprint, effect_action, event_json = effect
        if (
            not self._attempts_allow_effect(attempts)
            or effect_fingerprint != intent[0]
            or effect_action != action
            or event_json != expected_event_json
            or not isinstance(event_json, str)
        ):
            return CalendarEvidence(CalendarEvidenceState.UNKNOWN)
        try:
            event = self._decode_event(event_json)
        except (KeyError, TypeError, ValueError, OverflowError):
            return CalendarEvidence(CalendarEvidenceState.UNKNOWN)
        if event != expected_event:
            return CalendarEvidence(CalendarEvidenceState.UNKNOWN)
        return CalendarEvidence(CalendarEvidenceState.PRESENT, event)

    def effect_count(self, operation_id: str) -> int:
        return int(
            self._database.connection.execute(
                "SELECT COUNT(*) FROM sandbox_calendar_effects WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()[0]
        )

    def _begin(self) -> None:
        try:
            self._database.connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            raise PreCalendarActionError(
                "Sandbox calendar provider is busy; nothing was attempted."
            ) from exc

    @staticmethod
    def _canonical_json(value: object) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def _decode_intent(
        cls, fingerprint: object, encoded: object
    ) -> tuple[str, ScheduleEvent, str] | None:
        if (
            not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
            or not isinstance(encoded, str)
        ):
            return None
        try:
            if sha256(encoded.encode("utf-8")).hexdigest() != fingerprint:
                return None
            payload = json.loads(encoded, parse_constant=_reject_json_constant)
            if (
                not isinstance(payload, list)
                or len(payload) != 4
                or cls._canonical_json(payload) != encoded
            ):
                return None
            action, source_data, arguments, delivered_data = payload
            if (
                action not in {"accept", "decline", "reschedule"}
                or not isinstance(source_data, dict)
                or not isinstance(arguments, list)
                or not isinstance(delivered_data, dict)
            ):
                return None

            source = cls._decode_event(cls._canonical_json(source_data))
            delivered = cls._decode_event(cls._canonical_json(delivered_data))
            provider_event_id = source.provider_event_id or f"fake-{source.event_id}"
            if action == "accept":
                if arguments:
                    return None
                expected = replace(
                    source,
                    status=ScheduleStatus.ACCEPTED,
                    provider_event_id=provider_event_id,
                )
            elif action == "decline":
                if arguments:
                    return None
                expected = replace(
                    source,
                    status=ScheduleStatus.DECLINED,
                    provider_event_id=provider_event_id,
                )
            else:
                if (
                    len(arguments) != 3
                    or not isinstance(arguments[0], str)
                    or (arguments[1] is not None and not isinstance(arguments[1], str))
                    or not isinstance(arguments[2], str)
                ):
                    return None
                expected = replace(
                    source,
                    status=ScheduleStatus.RESCHEDULE_REQUESTED,
                    start_at=datetime.fromisoformat(arguments[0]),
                    end_at=(
                        None
                        if arguments[1] is None
                        else datetime.fromisoformat(arguments[1])
                    ),
                    timezone_name=arguments[2],
                    provider_event_id=provider_event_id,
                )
            if delivered != expected:
                return None
            return action, expected, cls._canonical_json(delivered_data)
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _parse_attempts(
        rows: list[tuple[object, object]] | tuple[tuple[object, object], ...]
    ) -> tuple[SandboxCalendarOutcome, ...] | None:
        numbers: list[int] = []
        outcomes: list[SandboxCalendarOutcome] = []
        for row in rows:
            if type(row[0]) is not int:
                return None
            try:
                outcome = SandboxCalendarOutcome(row[1])
            except (TypeError, ValueError):
                return None
            numbers.append(row[0])
            outcomes.append(outcome)
        if numbers != list(range(1, len(numbers) + 1)):
            return None
        return tuple(outcomes)

    @classmethod
    def _attempts_allow_absence(
        cls, attempts: tuple[SandboxCalendarOutcome, ...]
    ) -> bool:
        return bool(attempts) and all(
            outcome in cls._NO_EFFECT_OUTCOMES for outcome in attempts
        )

    @classmethod
    def _attempts_allow_effect(
        cls, attempts: tuple[SandboxCalendarOutcome, ...]
    ) -> bool:
        return (
            bool(attempts)
            and attempts[-1] in cls._EFFECT_OUTCOMES
            and all(outcome in cls._NO_EFFECT_OUTCOMES for outcome in attempts[:-1])
        )

    @staticmethod
    def _encode_event(event: ScheduleEvent) -> str:
        return json.dumps(
            {
                "event_id": event.event_id,
                "candidate_id": event.candidate_id,
                "employer_name": event.employer_name,
                "event_type": event.event_type.value,
                "location": event.location,
                "start_at": event.start_at.isoformat(),
                "timezone_name": event.timezone_name,
                "status": event.status.value,
                "end_at": None if event.end_at is None else event.end_at.isoformat(),
                "provider_event_id": event.provider_event_id,
                "application_id": event.application_id,
                "version": event.version,
                "created_at": event.created_at.isoformat(),
                "updated_at": event.updated_at.isoformat(),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _decode_event(encoded: str) -> ScheduleEvent:
        value = json.loads(encoded, parse_constant=_reject_json_constant)
        expected_keys = {
            "event_id",
            "candidate_id",
            "employer_name",
            "event_type",
            "location",
            "start_at",
            "timezone_name",
            "status",
            "end_at",
            "provider_event_id",
            "application_id",
            "version",
            "created_at",
            "updated_at",
        }
        if not isinstance(value, dict) or set(value) != expected_keys:
            raise ValueError("Sandbox calendar evidence is malformed.")
        return ScheduleEvent(
            event_id=value["event_id"],
            candidate_id=value["candidate_id"],
            employer_name=value["employer_name"],
            event_type=ScheduleEventType(value["event_type"]),
            location=value["location"],
            start_at=datetime.fromisoformat(value["start_at"]),
            timezone_name=value["timezone_name"],
            status=ScheduleStatus(value["status"]),
            end_at=(
                None
                if value["end_at"] is None
                else datetime.fromisoformat(value["end_at"])
            ),
            provider_event_id=value["provider_event_id"],
            application_id=value["application_id"],
            version=value["version"],
            created_at=datetime.fromisoformat(value["created_at"]),
            updated_at=datetime.fromisoformat(value["updated_at"]),
        )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON constant {value!r} is not supported.")
