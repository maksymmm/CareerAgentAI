from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from threading import Barrier, Lock

import pytest

from career_agent_ai.application.external_actions import ExternalActionService, ExternalActionStatus
from career_agent_ai.application.scheduling import (
    CalendarEvidenceState,
    CalendarReconciliationEvidenceError,
    PreCalendarActionError,
    ScheduleEvent,
    ScheduleEventType,
    ScheduleStatus,
    SchedulingService,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import (
    SQLiteExternalActionOperationRepository,
)
from career_agent_ai.application.storage.sqlite_sandbox_calendar_provider import (
    SandboxCalendarOutcome,
    SQLiteSandboxCalendarProvider,
)
from career_agent_ai.application.storage.sqlite_scheduling_repository import (
    SQLiteSchedulingRepository,
)


def _event() -> ScheduleEvent:
    start = datetime(2026, 11, 20, 17, 0, tzinfo=timezone.utc)
    return ScheduleEvent(
        event_id="interview-evidence",
        candidate_id="candidate-1",
        employer_name="Sandbox GmbH",
        event_type=ScheduleEventType.INTERVIEW,
        location="Sandbox video room",
        start_at=start,
        end_at=start + timedelta(hours=1),
        timezone_name="Europe/Berlin",
        application_id="application-1",
        created_at=datetime(2026, 11, 19, tzinfo=timezone.utc),
        updated_at=datetime(2026, 11, 19, tzinfo=timezone.utc),
    )


def _stack(database, plan=None):
    provider = SQLiteSandboxCalendarProvider(database, plan)
    events = SQLiteSchedulingRepository(database)
    operations = SQLiteExternalActionOperationRepository(database)
    external = ExternalActionService(
        operations, SchedulingService.action_adapter(provider, events)
    )
    service = SchedulingService(
        events, provider, external, require_provider_evidence=True
    )
    return service, provider, operations


def test_timeout_after_calendar_effect_requires_matching_evidence(tmp_path):
    path = str(tmp_path / "calendar.db")
    database = SQLiteDatabase(path)
    service, provider, operations = _stack(
        database,
        lambda _key, _attempt: SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT,
    )
    service.add_event(_event())
    assert service.accept(
        "accept:evidence", "interview-evidence", human_approved=True
    ) is None
    assert operations.get("accept:evidence").status == ExternalActionStatus.RECONCILIATION_REQUIRED
    assert provider.effect_count("accept:evidence") == 1
    database.close()

    database = SQLiteDatabase(path)
    service, provider, operations = _stack(database)
    evidence = provider.verify_calendar_action("accept:evidence")
    assert evidence.state == CalendarEvidenceState.PRESENT
    with pytest.raises(CalendarReconciliationEvidenceError, match="no calendar effect"):
        service.resolve_reconciliation("accept:evidence", completed=False)
    resolved = service.resolve_reconciliation("accept:evidence", completed=True)
    assert resolved.status == ExternalActionStatus.SUCCEEDED
    assert resolved.result["provider_event_id"] == "fake-interview-evidence"
    assert provider.effect_count("accept:evidence") == 1


def test_interruption_before_calendar_effect_can_reopen_and_retry(tmp_path):
    database = SQLiteDatabase(str(tmp_path / "calendar.db"))
    service, provider, operations = _stack(
        database,
        lambda _key, attempt: (
            SandboxCalendarOutcome.INTERRUPTED_BEFORE_EFFECT
            if attempt == 1
            else SandboxCalendarOutcome.SUCCESS
        ),
    )
    service.add_event(_event())
    assert service.accept(
        "accept:evidence", "interview-evidence", human_approved=True
    ) is None
    assert provider.verify_calendar_action(
        "accept:evidence"
    ).state == CalendarEvidenceState.ABSENT_CONFIRMED
    with pytest.raises(CalendarReconciliationEvidenceError, match="no evidence"):
        service.resolve_reconciliation("accept:evidence", completed=True)
    assert service.resolve_reconciliation(
        "accept:evidence", completed=False
    ).status == ExternalActionStatus.PREPARED
    accepted = service.accept(
        "accept:evidence", "interview-evidence", human_approved=True
    )
    assert accepted is not None and accepted.status == ScheduleStatus.ACCEPTED
    assert provider.effect_count("accept:evidence") == 1


def test_calendar_provider_replays_and_rejects_changed_intent(tmp_path):
    database = SQLiteDatabase(str(tmp_path / "calendar.db"))
    provider = SQLiteSandboxCalendarProvider(database)
    source = _event()
    delivered = provider.accept("accept:evidence", source)
    assert provider.accept("accept:evidence", source) == delivered
    with pytest.raises(PreCalendarActionError, match="different calendar intent"):
        provider.accept(
            "accept:evidence", replace(source, location="Changed location")
        )
    assert provider.effect_count("accept:evidence") == 1


def test_calendar_provider_confirms_absence_and_rejects_nested_lookup(tmp_path):
    database = SQLiteDatabase(str(tmp_path / "calendar.db"))
    provider = SQLiteSandboxCalendarProvider(
        database,
        lambda _key, _attempt: SandboxCalendarOutcome.NO_EFFECT,
    )
    with pytest.raises(PreCalendarActionError, match="no calendar effect"):
        provider.decline("decline:evidence", _event())
    assert provider.verify_calendar_action(
        "decline:evidence"
    ).state == CalendarEvidenceState.ABSENT_CONFIRMED
    database.connection.execute("BEGIN")
    with pytest.raises(PreCalendarActionError, match="inside a transaction"):
        provider.verify_calendar_action("decline:evidence")
    database.connection.rollback()


def test_no_effect_binds_reschedule_source_intent(tmp_path):
    database = SQLiteDatabase(str(tmp_path / "calendar.db"))
    provider = SQLiteSandboxCalendarProvider(
        database,
        lambda _key, attempt: (
            SandboxCalendarOutcome.NO_EFFECT
            if attempt == 1
            else SandboxCalendarOutcome.SUCCESS
        ),
    )
    source = _event()
    target = source.start_at + timedelta(days=1)
    with pytest.raises(PreCalendarActionError, match="no calendar effect"):
        provider.reschedule(
            "reschedule:evidence", source, target, None, "Europe/Berlin"
        )
    changed_source = replace(
        source,
        start_at=source.start_at + timedelta(hours=2),
        end_at=source.end_at + timedelta(hours=2),
    )
    with pytest.raises(PreCalendarActionError, match="different calendar intent"):
        provider.reschedule(
            "reschedule:evidence", changed_source, target, None, "Europe/Berlin"
        )
    assert provider.effect_count("reschedule:evidence") == 0


def test_evidence_cannot_replace_existing_provider_event_id(tmp_path):
    database = SQLiteDatabase(str(tmp_path / "calendar.db"))
    service, provider, operations = _stack(
        database,
        lambda _key, _attempt: SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT,
    )
    source = replace(_event(), provider_event_id="provider-existing")
    service.add_event(source)
    assert service.accept(
        "accept:evidence", source.event_id, human_approved=True
    ) is None
    row = database.connection.execute(
        "SELECT event_json FROM sandbox_calendar_effects WHERE operation_id = ?",
        ("accept:evidence",),
    ).fetchone()
    corrupted = json.loads(row[0])
    corrupted["provider_event_id"] = "provider-other"
    database.connection.execute(
        "UPDATE sandbox_calendar_effects SET event_json = ? WHERE operation_id = ?",
        (json.dumps(corrupted), "accept:evidence"),
    )
    database.connection.commit()

    with pytest.raises(CalendarReconciliationEvidenceError, match="existing provider"):
        service.resolve_reconciliation("accept:evidence", completed=True)
    assert operations.get("accept:evidence").status == ExternalActionStatus.RECONCILIATION_REQUIRED



def test_busy_calendar_writer_lock_records_no_attempt_and_allows_same_intent_retry(tmp_path):
    path = str(tmp_path / "calendar-busy-writer.db")
    database = SQLiteDatabase(path)
    provider = SQLiteSandboxCalendarProvider(database)
    database.connection.execute("PRAGMA busy_timeout = 0")
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(PreCalendarActionError, match="busy; nothing was attempted"):
            provider.accept("accept:busy-writer", _event())
    finally:
        holder.rollback()
        holder.close()

    assert database.connection.execute(
        "SELECT COUNT(*) FROM sandbox_calendar_intents WHERE operation_id = ?",
        ("accept:busy-writer",),
    ).fetchone()[0] == 0
    assert database.connection.execute(
        "SELECT COUNT(*) FROM sandbox_calendar_attempts WHERE operation_id = ?",
        ("accept:busy-writer",),
    ).fetchone()[0] == 0
    assert provider.effect_count("accept:busy-writer") == 0
    assert provider.verify_calendar_action(
        "accept:busy-writer"
    ).state == CalendarEvidenceState.ABSENT_CONFIRMED

    accepted = provider.accept("accept:busy-writer", _event())
    assert accepted.status == ScheduleStatus.ACCEPTED
    assert provider.effect_count("accept:busy-writer") == 1
    attempt_rows = database.connection.execute(
        "SELECT attempt_no, outcome FROM sandbox_calendar_attempts "
        "WHERE operation_id = ?",
        ("accept:busy-writer",),
    ).fetchall()
    assert attempt_rows == [(1, SandboxCalendarOutcome.SUCCESS.value)]
    database.close()


def test_calendar_evidence_lookup_fails_closed_while_another_connection_holds_writer_lock(
    tmp_path,
):
    path = str(tmp_path / "calendar-evidence-busy.db")
    database = SQLiteDatabase(path)
    provider = SQLiteSandboxCalendarProvider(database)
    event = provider.accept("accept:evidence-busy", _event())
    database.connection.execute("PRAGMA busy_timeout = 0")
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(PreCalendarActionError, match="busy; nothing was attempted"):
            provider.verify_calendar_action("accept:evidence-busy")
    finally:
        holder.rollback()
        holder.close()

    evidence = provider.verify_calendar_action("accept:evidence-busy")
    assert evidence.state == CalendarEvidenceState.PRESENT
    assert evidence.event == event
    assert provider.effect_count("accept:evidence-busy") == 1
    database.close()


def test_concurrent_same_key_calendar_calls_observe_one_committed_effect(tmp_path):
    path = str(tmp_path / "calendar-concurrent-same-key.db")
    initialized = SQLiteDatabase(path)
    SQLiteSandboxCalendarProvider(initialized)
    initialized.close()

    barrier = Barrier(2)
    setup_lock = Lock()
    plan_lock = Lock()
    plan_calls = []

    def plan(operation_id, attempt):
        with plan_lock:
            plan_calls.append((operation_id, attempt))
        return SandboxCalendarOutcome.SUCCESS

    def deliver():
        database = SQLiteDatabase(path)
        try:
            with setup_lock:
                provider = SQLiteSandboxCalendarProvider(database, plan)
            barrier.wait(timeout=10)
            return provider.accept("accept:concurrent", _event())
        finally:
            database.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(deliver) for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]

    assert results[0] == results[1]
    assert plan_calls == [("accept:concurrent", 1)]

    verifier = SQLiteDatabase(path)
    provider = SQLiteSandboxCalendarProvider(verifier)
    attempts = verifier.connection.execute(
        "SELECT attempt_no, outcome FROM sandbox_calendar_attempts "
        "WHERE operation_id = ? ORDER BY attempt_no",
        ("accept:concurrent",),
    ).fetchall()
    effects = verifier.connection.execute(
        "SELECT action, replays FROM sandbox_calendar_effects WHERE operation_id = ?",
        ("accept:concurrent",),
    ).fetchall()
    assert attempts == [(1, SandboxCalendarOutcome.SUCCESS.value)]
    assert effects == [("accept", 1)]
    assert provider.effect_count("accept:concurrent") == 1
    verifier.close()


def test_concurrent_conflicting_calendar_intents_cannot_rebind_one_operation_id(tmp_path):
    path = str(tmp_path / "calendar-concurrent-conflict.db")
    initialized = SQLiteDatabase(path)
    SQLiteSandboxCalendarProvider(initialized)
    initialized.close()

    barrier = Barrier(2)
    setup_lock = Lock()
    plan_lock = Lock()
    plan_calls = []

    def plan(operation_id, attempt):
        with plan_lock:
            plan_calls.append((operation_id, attempt))
        return SandboxCalendarOutcome.SUCCESS

    def deliver(source):
        database = SQLiteDatabase(path)
        try:
            with setup_lock:
                provider = SQLiteSandboxCalendarProvider(database, plan)
            barrier.wait(timeout=10)
            try:
                return ("success", provider.accept("accept:conflict", source))
            except PreCalendarActionError:
                return ("conflict", None)
        finally:
            database.close()

    sources = (_event(), replace(_event(), location="Different approved location"))
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(deliver, source) for source in sources]
        outcomes = [future.result(timeout=30) for future in futures]

    assert sorted(kind for kind, _value in outcomes) == ["conflict", "success"]
    assert plan_calls == [("accept:conflict", 1)]

    verifier = SQLiteDatabase(path)
    provider = SQLiteSandboxCalendarProvider(verifier)
    assert provider.effect_count("accept:conflict") == 1
    attempts = verifier.connection.execute(
        "SELECT attempt_no, outcome FROM sandbox_calendar_attempts "
        "WHERE operation_id = ?",
        ("accept:conflict",),
    ).fetchall()
    effect = verifier.verify_calendar_action("accept:conflict")
    assert attempts == [(1, SandboxCalendarOutcome.SUCCESS.value)]
    assert effect.state == CalendarEvidenceState.PRESENT
    assert effect.event in (sources[0], sources[1])
    verifier.close()
