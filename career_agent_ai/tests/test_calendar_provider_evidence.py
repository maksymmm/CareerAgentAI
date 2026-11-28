from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

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


def test_stale_decline_reconciliation_rejects_later_same_slot_version(tmp_path):
    class FailFirstSuccessRepository(SQLiteExternalActionOperationRepository):
        def __init__(self, database):
            self.fail_success_once = True
            super().__init__(database)

        def transition(
            self,
            operation_id,
            expected_status,
            status,
            *,
            result=None,
            error=None,
        ):
            if status == ExternalActionStatus.SUCCEEDED and self.fail_success_once:
                self.fail_success_once = False
                raise RuntimeError("simulated terminal success persistence failure")
            return super().transition(
                operation_id,
                expected_status,
                status,
                result=result,
                error=error,
            )

    database = SQLiteDatabase(str(tmp_path / "stale-decline.sqlite"))
    provider = SQLiteSandboxCalendarProvider(database)
    events = SQLiteSchedulingRepository(database)
    operations = FailFirstSuccessRepository(database)
    external = ExternalActionService(
        operations, SchedulingService.action_adapter(provider, events)
    )
    service = SchedulingService(
        events, provider, external, require_provider_evidence=True
    )
    source = _event()
    service.add_event(source)

    # The provider effect and event transition commit, but the external-action
    # terminal write fails. This leaves an ambiguous operation over a declined
    # version-2 event, matching the real post-provider crash window.
    assert service.decline(
        "decline:stale-version",
        source.event_id,
        human_approved=True,
        expected_version=source.version,
    ) is None
    operation = operations.get("decline:stale-version")
    assert operation is not None
    assert operation.status == ExternalActionStatus.RECONCILIATION_REQUIRED
    declined = service.get_event(source.event_id)
    assert declined.status == ScheduleStatus.DECLINED
    assert declined.version == source.version + 1

    # Model a later durable event-version advance that preserves the visible
    # declined status and slot. The old provider evidence must not authorize it.
    database.connection.execute(
        """
        UPDATE scheduling_events
        SET version = version + 1, updated_at = ?
        WHERE event_id = ?
        """,
        (datetime.now(timezone.utc).isoformat(), source.event_id),
    )
    database.connection.commit()
    current = service.get_event(source.event_id)
    assert current.status == ScheduleStatus.DECLINED
    assert current.version == source.version + 2
    assert provider.verify_calendar_action(
        "decline:stale-version"
    ).state == CalendarEvidenceState.PRESENT

    with pytest.raises(
        ValueError, match="stale for the current event version"
    ):
        service.resolve_reconciliation(
            "decline:stale-version",
            completed=True,
        )

    operation = operations.get("decline:stale-version")
    assert operation is not None
    assert operation.status == ExternalActionStatus.RECONCILIATION_REQUIRED
    assert provider.effect_count("decline:stale-version") == 1
    assert database.connection.execute(
        """
        SELECT COUNT(*), MAX(replays)
        FROM sandbox_calendar_attempts
        JOIN sandbox_calendar_effects USING (operation_id)
        WHERE operation_id = ?
        """,
        ("decline:stale-version",),
    ).fetchone() == (1, 0)
    database.close()
