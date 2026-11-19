"""Durable no-network calendar provider with authoritative reconciliation evidence."""

from __future__ import annotations

import json
import sqlite3
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
                operation_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL
            )"""
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
        self, action: str, operation_id: str, delivered: ScheduleEvent
    ) -> ScheduleEvent:
        operation_id = validate_schedule_identifier(operation_id, "operation_id")
        event_json = self._encode_event(delivered)
        fingerprint = sha256(
            json.dumps(
                [action, json.loads(event_json)],
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        connection = self._database.connection
        self._begin()
        try:
            intent = connection.execute(
                "SELECT fingerprint FROM sandbox_calendar_intents WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if intent is None:
                connection.execute(
                    "INSERT INTO sandbox_calendar_intents VALUES (?, ?)",
                    (operation_id, fingerprint),
                )
            elif intent[0] != fingerprint:
                raise PreCalendarActionError(
                    "Sandbox idempotency key was reused with a different calendar intent."
                )
            effect = connection.execute(
                "SELECT fingerprint, event_json FROM sandbox_calendar_effects "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if effect is not None:
                if effect[0] != fingerprint:
                    raise PreCalendarActionError(
                        "Sandbox calendar evidence conflicts with the action intent."
                    )
                connection.execute(
                    "UPDATE sandbox_calendar_effects SET replays = replays + 1 "
                    "WHERE operation_id = ?",
                    (operation_id,),
                )
                connection.commit()
                return self._decode_event(effect[1])
            attempt = connection.execute(
                "SELECT COUNT(*) FROM sandbox_calendar_attempts WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()[0] + 1
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
            if outcome in {
                SandboxCalendarOutcome.NO_EFFECT,
                SandboxCalendarOutcome.INTERRUPTED_BEFORE_EFFECT,
            }:
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
        operation_id = validate_schedule_identifier(operation_id, "operation_id")
        if self._database.connection.in_transaction:
            raise PreCalendarActionError(
                "Sandbox calendar evidence cannot be verified inside a transaction."
            )
        self._begin()
        try:
            row = self._database.connection.execute(
                "SELECT event_json FROM sandbox_calendar_effects WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        finally:
            self._database.connection.rollback()
        if row is None:
            return CalendarEvidence(CalendarEvidenceState.ABSENT_CONFIRMED)
        return CalendarEvidence(CalendarEvidenceState.PRESENT, self._decode_event(row[0]))

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
        value = json.loads(encoded)
        if not isinstance(value, dict):
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
            end_at=None if value["end_at"] is None else datetime.fromisoformat(value["end_at"]),
            provider_event_id=value["provider_event_id"],
            application_id=value["application_id"],
            version=value["version"],
            created_at=datetime.fromisoformat(value["created_at"]),
            updated_at=datetime.fromisoformat(value["updated_at"]),
        )
