from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from career_agent_ai.application.external_actions import (
    ExternalActionOperation,
    ExternalActionService,
    ExternalActionStatus,
)
from career_agent_ai.application.scheduling import (
    FakeCalendarAdapter,
    ScheduleEvent,
    ScheduleEventType,
    ScheduleStatus,
    SchedulingConflictError,
    SchedulingService,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import (
    SQLiteExternalActionOperationRepository,
)
from career_agent_ai.application.storage.sqlite_scheduling_repository import (
    SQLiteSchedulingRepository,
)


BASE_CREATED = datetime(2026, 1, 1, tzinfo=timezone.utc)
BASE_START = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)


def event(
    event_id: str = "event-1",
    *,
    start_at: datetime = BASE_START,
    end_at: datetime | None = BASE_START + timedelta(hours=1),
    timezone_name: str = "Europe/Berlin",
    status: ScheduleStatus = ScheduleStatus.PROPOSED,
    provider_event_id: str | None = None,
    employer_name: str = "Acme GmbH",
    location: str = "Hauptstrasse 1, Karlsruhe",
) -> ScheduleEvent:
    return ScheduleEvent(
        event_id=event_id,
        candidate_id="candidate-1",
        employer_name=employer_name,
        event_type=ScheduleEventType.INTERVIEW,
        location=location,
        start_at=start_at,
        end_at=end_at,
        timezone_name=timezone_name,
        status=status,
        provider_event_id=provider_event_id,
        application_id="application-1",
        created_at=BASE_CREATED,
        updated_at=BASE_CREATED,
    )


def stack(database: SQLiteDatabase, provider: FakeCalendarAdapter | None = None):
    provider = provider or FakeCalendarAdapter()
    schedules = SQLiteSchedulingRepository(database)
    operations = SQLiteExternalActionOperationRepository(database)
    external = ExternalActionService(
        operations, SchedulingService.action_adapter(provider, schedules)
    )
    return SchedulingService(schedules, provider, external), schedules, operations, provider


@pytest.mark.parametrize(
    "changes,error_type",
    [
        ({"event_id": "../bad"}, ValueError),
        ({"candidate_id": 42}, TypeError),
        ({"timezone_name": "Mars/Olympus"}, ValueError),
        ({"start_at": datetime(2026, 1, 1)}, ValueError),
        ({"event_type": "interview"}, TypeError),
        ({"status": "proposed"}, TypeError),
        ({"location": "bad\x00location"}, ValueError),
        ({"employer_name": "bad\ud800name"}, ValueError),
    ],
)
def test_schedule_event_strictly_validates_untrusted_input(changes, error_type):
    values = {
        "event_id": "event-1",
        "candidate_id": "candidate-1",
        "employer_name": "Acme GmbH",
        "event_type": ScheduleEventType.INTERVIEW,
        "location": "Office",
        "start_at": BASE_START,
        "end_at": BASE_START + timedelta(hours=1),
        "timezone_name": "Europe/Berlin",
        "status": ScheduleStatus.PROPOSED,
        "created_at": BASE_CREATED,
        "updated_at": BASE_CREATED,
    }
    values.update(changes)
    with pytest.raises(error_type):
        ScheduleEvent(**values)


def test_schedule_event_rejects_invalid_time_ranges():
    with pytest.raises(ValueError, match="later than"):
        event(end_at=BASE_START)


def test_human_view_exposes_exact_local_details_and_dst_offset():
    database = SQLiteDatabase()
    service, _, _, _ = stack(database)
    spring = event(
        start_at=datetime(2026, 3, 29, 7, 30, tzinfo=timezone.utc),
        end_at=datetime(2026, 3, 29, 8, 30, tzinfo=timezone.utc),
        location="https://meet.example.test/interview",
    )
    service.add_event(spring)

    view = service.human_view("event-1")

    assert view.employer_name == "Acme GmbH"
    assert view.event_type == ScheduleEventType.INTERVIEW
    assert view.location == "https://meet.example.test/interview"
    assert view.local_date == "2026-03-29"
    assert view.local_start_time == "09:30:00"
    assert view.local_end_time == "10:30:00"
    assert view.local_end_date == "2026-03-29"
    assert view.timezone_name == "Europe/Berlin"
    assert view.utc_offset == "+02:00"
    assert view.end_utc_offset == "+02:00"
    assert view.status == ScheduleStatus.PROPOSED


def test_human_view_disambiguates_dst_fall_back_with_offset():
    database = SQLiteDatabase()
    service, _, _, _ = stack(database)
    service.add_event(
        event(
            start_at=datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc),
            end_at=datetime(2026, 10, 25, 2, 30, tzinfo=timezone.utc),
        )
    )

    view = service.human_view("event-1")

    assert view.local_start_time == "02:30:00"
    assert view.utc_offset == "+01:00"



def test_human_view_exposes_distinct_offsets_across_dst_fall_back():
    service, _, _, _ = stack(SQLiteDatabase())
    service.add_event(
        event(
            start_at=datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc),
            end_at=datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc),
        )
    )

    view = service.human_view("event-1")

    assert view.local_start_time == "02:30:00"
    assert view.local_end_time == "02:30:00"
    assert view.utc_offset == "+02:00"
    assert view.end_utc_offset == "+01:00"

def test_sqlite_restart_preserves_event_and_deterministic_order(tmp_path):
    path = str(tmp_path / "schedule.sqlite")
    first_db = SQLiteDatabase(path)
    first_repo = SQLiteSchedulingRepository(first_db)
    first_repo.create(event("event-b", start_at=BASE_START + timedelta(microseconds=1)))
    first_repo.create(event("event-a", start_at=BASE_START))
    first_db.close()

    restarted = SQLiteSchedulingRepository(SQLiteDatabase(path))

    assert restarted.get("event-a") == event("event-a", start_at=BASE_START)
    assert tuple(item.event_id for item in restarted.list_candidate("candidate-1")) == (
        "event-a",
        "event-b",
    )


def test_duplicate_create_is_idempotent_but_provider_identity_collision_is_rejected():
    repository = SQLiteSchedulingRepository(SQLiteDatabase())
    original = event(provider_event_id="provider-1")

    assert repository.create(original) == original
    assert repository.create(original) == original

    with pytest.raises(SchedulingConflictError):
        repository.create(
            event(
                "event-2",
                start_at=BASE_START + timedelta(hours=3),
                end_at=BASE_START + timedelta(hours=4),
                provider_event_id="provider-1",
            )
        )



def test_opaque_provider_event_identifier_is_accepted_and_persisted():
    repository = SQLiteSchedulingRepository(SQLiteDatabase())
    opaque_id = "calendar/events/abc==/instance:2026-10-20T08:00:00Z"
    value = event(provider_event_id=opaque_id)

    persisted = repository.create(value)

    assert persisted.provider_event_id == opaque_id
    assert repository.get("event-1").provider_event_id == opaque_id


@pytest.mark.parametrize(
    "provider_event_id",
    [
        "",
        "   ",
        "bad\x00provider",
        "bad\ud800provider",
        "x" * 1001,
    ],
)
def test_provider_event_identifier_rejects_only_unsafe_or_unbounded_values(
    provider_event_id,
):
    with pytest.raises((TypeError, ValueError)):
        event(provider_event_id=provider_event_id)

def test_conflict_detection_ignores_terminal_events():
    repository = SQLiteSchedulingRepository(SQLiteDatabase())
    repository.create(event("active"))
    repository.create(
        event(
            "declined",
            start_at=BASE_START + timedelta(minutes=15),
            end_at=BASE_START + timedelta(minutes=45),
            status=ScheduleStatus.DECLINED,
        )
    )

    conflicts = repository.find_conflicts(
        "candidate-1",
        BASE_START + timedelta(minutes=30),
        BASE_START + timedelta(minutes=50),
        exclude_event_id="other",
    )

    assert tuple(item.event_id for item in conflicts) == ("active",)


def test_accept_requires_human_approval_and_replays_after_restart(tmp_path):
    path = str(tmp_path / "accept.sqlite")
    database = SQLiteDatabase(path)
    service, _, operations, provider = stack(database)
    service.add_event(event())

    with pytest.raises(PermissionError, match="human approval"):
        service.accept("accept:1", "event-1", human_approved=False)
    assert provider.calls == []
    assert operations.get("accept:1").status == ExternalActionStatus.PREPARED

    accepted = service.accept("accept:1", "event-1", human_approved=True)
    assert accepted is not None and accepted.status == ScheduleStatus.ACCEPTED
    assert provider.calls == [("accept", "accept:1")]
    database.close()

    restarted_provider = FakeCalendarAdapter()
    restarted_service, _, _, _ = stack(SQLiteDatabase(path), restarted_provider)
    replay = restarted_service.accept("accept:1", "event-1", human_approved=True)

    assert replay == accepted
    assert restarted_provider.calls == []



def test_successful_accept_replay_returns_original_outcome_after_later_reschedule():
    service, repository, _, provider = stack(SQLiteDatabase())
    service.add_event(event())
    accepted = service.accept("accept:original", "event-1", human_approved=True)
    assert accepted is not None

    new_start = BASE_START + timedelta(days=3)
    rescheduled = service.reschedule(
        "reschedule:later",
        "event-1",
        start_at=new_start,
        end_at=new_start + timedelta(hours=1),
        timezone_name="Europe/Berlin",
        human_approved=True,
    )
    assert rescheduled is not None
    assert rescheduled.status == ScheduleStatus.RESCHEDULE_REQUESTED

    replay = service.accept(
        "accept:original", "event-1", human_approved=True
    )

    assert replay == accepted
    assert replay.status == ScheduleStatus.ACCEPTED
    assert replay.start_at == BASE_START
    assert repository.get("event-1") == rescheduled
    assert provider.calls == [
        ("accept", "accept:original"),
        ("reschedule", "reschedule:later"),
    ]


def test_accept_replay_normalizes_event_identifier():
    service, _, _, provider = stack(SQLiteDatabase())
    service.add_event(event())

    accepted = service.accept(
        "accept:normalized",
        " event-1 ",
        human_approved=True,
    )
    replay = service.accept(
        "accept:normalized",
        " event-1 ",
        human_approved=True,
    )

    assert accepted is not None
    assert replay == accepted
    assert provider.calls == [("accept", "accept:normalized")]


def test_invalid_operation_identifier_is_rejected_before_prepare():
    service, _, operations, provider = stack(SQLiteDatabase())
    service.add_event(event())

    with pytest.raises(ValueError, match="operation_id"):
        service.accept(
            "accept request 1",
            "event-1",
            human_approved=True,
        )

    assert operations.get("accept request 1") is None
    assert provider.calls == []


def test_decline_is_human_gated_and_persisted():
    service, repository, _, provider = stack(SQLiteDatabase())
    service.add_event(event())

    declined = service.decline("decline:1", "event-1", human_approved=True)

    assert declined is not None
    assert declined.status == ScheduleStatus.DECLINED
    assert declined.provider_event_id == "fake-event-1"
    assert repository.get("event-1") == declined
    assert provider.calls == [("decline", "decline:1")]


def test_reschedule_updates_slot_timezone_and_can_clear_end_time():
    service, _, _, provider = stack(SQLiteDatabase())
    service.add_event(event())
    new_start = datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc)

    updated = service.reschedule(
        "reschedule:1",
        "event-1",
        start_at=new_start,
        end_at=None,
        timezone_name="Europe/Berlin",
        human_approved=True,
    )

    assert updated is not None
    assert updated.status == ScheduleStatus.RESCHEDULE_REQUESTED
    assert updated.start_at == new_start
    assert updated.end_at is None
    assert updated.version == 2
    assert provider.calls == [("reschedule", "reschedule:1")]


def test_accept_rejects_conflict_before_external_action_is_prepared():
    service, _, operations, provider = stack(SQLiteDatabase())
    service.add_event(event("event-1"))
    service.add_event(
        event(
            "event-2",
            start_at=BASE_START + timedelta(minutes=30),
            end_at=BASE_START + timedelta(hours=2),
            status=ScheduleStatus.ACCEPTED,
        )
    )

    with pytest.raises(SchedulingConflictError):
        service.accept("accept:1", "event-1", human_approved=True)

    assert operations.get("accept:1") is None
    assert provider.calls == []


def test_reschedule_rejects_conflicting_target_before_provider_call():
    service, _, operations, provider = stack(SQLiteDatabase())
    service.add_event(event("event-1"))
    service.add_event(
        event(
            "event-2",
            start_at=BASE_START + timedelta(hours=3),
            end_at=BASE_START + timedelta(hours=4),
            status=ScheduleStatus.ACCEPTED,
        )
    )

    with pytest.raises(SchedulingConflictError):
        service.reschedule(
            "reschedule:1",
            "event-1",
            start_at=BASE_START + timedelta(hours=3, minutes=30),
            end_at=BASE_START + timedelta(hours=4, minutes=30),
            timezone_name="Europe/Berlin",
            human_approved=True,
        )

    assert operations.get("reschedule:1") is None
    assert provider.calls == []



def test_overlapping_uncommitted_proposals_do_not_block_acceptance():
    service, _, _, provider = stack(SQLiteDatabase())
    service.add_event(event("event-1"))
    service.add_event(
        event(
            "event-2",
            start_at=BASE_START + timedelta(minutes=15),
            end_at=BASE_START + timedelta(minutes=45),
        )
    )

    accepted = service.accept("accept:1", "event-1", human_approved=True)

    assert accepted is not None and accepted.status == ScheduleStatus.ACCEPTED
    assert provider.calls == [("accept", "accept:1")]


def test_reschedule_requested_event_can_request_a_new_slot_again():
    service, _, _, provider = stack(SQLiteDatabase())
    service.add_event(event(status=ScheduleStatus.RESCHEDULE_REQUESTED))
    next_start = BASE_START + timedelta(days=1)

    updated = service.reschedule(
        "reschedule:again",
        "event-1",
        start_at=next_start,
        end_at=next_start + timedelta(hours=1),
        timezone_name="Europe/Berlin",
        human_approved=True,
    )

    assert updated is not None
    assert updated.status == ScheduleStatus.RESCHEDULE_REQUESTED
    assert updated.start_at == next_start
    assert provider.calls == [("reschedule", "reschedule:again")]


def test_pre_provider_snapshot_validation_failure_releases_action_claim():
    service, repository, operations, provider = stack(SQLiteDatabase())
    service.add_event(event())
    operations.create(
        ExternalActionOperation(
            operation_id="accept:malformed-snapshot",
            action_type="calendar.accept",
            payload={
                "event_id": "event-1",
                "event_version": 1,
                "event_start_at": (BASE_START + timedelta(hours=5)).isoformat(),
                "event_end_at": (BASE_START + timedelta(hours=6)).isoformat(),
                "event_timezone_name": "Europe/Berlin",
                "event_status": ScheduleStatus.PROPOSED.value,
            },
        )
    )

    assert service.accept(
        "accept:malformed-snapshot", "event-1", human_approved=True
    ) is None
    assert (
        operations.get("accept:malformed-snapshot").status
        == ExternalActionStatus.FAILED
    )
    assert provider.calls == []

    retry = service.accept("accept:clean-retry", "event-1", human_approved=True)

    assert retry is not None
    assert retry.status == ScheduleStatus.ACCEPTED
    assert repository.get("event-1").status == ScheduleStatus.ACCEPTED
    assert provider.calls == [("accept", "accept:clean-retry")]

def test_definite_pre_provider_failure_releases_claim_for_new_attempt():
    provider = FakeCalendarAdapter()
    service, _, operations, _ = stack(SQLiteDatabase(), provider)
    service.add_event(event())
    provider.failure = RuntimeError("provider unavailable before delivery")

    assert service.accept("accept:1", "event-1", human_approved=True) is None
    assert operations.get("accept:1").status == ExternalActionStatus.FAILED

    provider.failure = None
    retried = service.accept("accept:2", "event-1", human_approved=True)

    assert retried is not None and retried.status == ScheduleStatus.ACCEPTED
    assert provider.calls == [("accept", "accept:1"), ("accept", "accept:2")]



def test_claim_release_failure_requires_reconciliation_and_blocks_blind_retry():
    class FailingReleaseRepository(SQLiteSchedulingRepository):
        def release_action(self, event_id, operation_id):
            raise RuntimeError("database unavailable while releasing claim")

    database = SQLiteDatabase()
    repository = FailingReleaseRepository(database)
    provider = FakeCalendarAdapter()
    provider.failure = RuntimeError("provider unavailable before delivery")
    operations = SQLiteExternalActionOperationRepository(database)
    external = ExternalActionService(
        operations, SchedulingService.action_adapter(provider, repository)
    )
    service = SchedulingService(repository, provider, external)
    service.add_event(event())

    assert service.accept("accept:cleanup", "event-1", human_approved=True) is None
    assert (
        operations.get("accept:cleanup").status
        == ExternalActionStatus.RECONCILIATION_REQUIRED
    )

    provider.failure = None
    assert service.accept("accept:retry", "event-1", human_approved=True) is None
    assert operations.get("accept:retry").status == ExternalActionStatus.FAILED
    assert provider.calls == [("accept", "accept:cleanup")]

def test_ambiguous_provider_exception_requires_reconciliation_and_keeps_claim():
    class AmbiguousProvider(FakeCalendarAdapter):
        def accept(self, operation_id, value):
            self.calls.append(("accept", operation_id))
            raise RuntimeError("connection reset after request write")

    provider = AmbiguousProvider()
    service, _, operations, _ = stack(SQLiteDatabase(), provider)
    service.add_event(event())

    assert service.accept("accept:1", "event-1", human_approved=True) is None
    assert (
        operations.get("accept:1").status
        == ExternalActionStatus.RECONCILIATION_REQUIRED
    )

    assert service.accept("accept:2", "event-1", human_approved=True) is None
    assert operations.get("accept:2").status == ExternalActionStatus.FAILED
    assert provider.calls == [("accept", "accept:1")]


def test_provider_success_followed_by_repository_failure_requires_reconciliation():
    class FailingRepository(SQLiteSchedulingRepository):
        def complete_action(self, value, operation_id, *, expected_version):
            raise RuntimeError("database unavailable after provider success")

    database = SQLiteDatabase()
    repository = FailingRepository(database)
    provider = FakeCalendarAdapter()
    operations = SQLiteExternalActionOperationRepository(database)
    external = ExternalActionService(
        operations, SchedulingService.action_adapter(provider, repository)
    )
    service = SchedulingService(repository, provider, external)
    service.add_event(event())

    assert service.accept("accept:1", "event-1", human_approved=True) is None
    assert (
        operations.get("accept:1").status
        == ExternalActionStatus.RECONCILIATION_REQUIRED
    )
    assert provider.calls == [("accept", "accept:1")]


def test_provider_cannot_change_immutable_event_intent():
    class MutatingProvider(FakeCalendarAdapter):
        def accept(self, operation_id, value):
            delivered = super().accept(operation_id, value)
            return replace(delivered, employer_name="Attacker Corp")

    provider = MutatingProvider()
    service, repository, operations, _ = stack(SQLiteDatabase(), provider)
    service.add_event(event())

    assert service.accept("accept:1", "event-1", human_approved=True) is None
    assert (
        operations.get("accept:1").status
        == ExternalActionStatus.RECONCILIATION_REQUIRED
    )
    assert repository.get("event-1").status == ScheduleStatus.PROPOSED


def test_concurrent_competing_actions_only_one_reaches_provider(tmp_path):
    path = str(tmp_path / "concurrent-actions.sqlite")
    entered = Event()
    release = Event()

    class BlockingProvider(FakeCalendarAdapter):
        def accept(self, operation_id, value):
            entered.set()
            assert release.wait(timeout=5)
            return super().accept(operation_id, value)

    provider = BlockingProvider()
    setup_db = SQLiteDatabase(path)
    setup, _, _, _ = stack(setup_db, provider)
    setup.add_event(event())
    with pytest.raises(PermissionError):
        setup.accept("accept:first", "event-1", human_approved=False)
    with pytest.raises(PermissionError):
        setup.accept("accept:second", "event-1", human_approved=False)
    setup_db.close()

    def execute(operation_id: str):
        worker_db = SQLiteDatabase(path)
        worker, _, _, _ = stack(worker_db, provider)
        try:
            return worker.accept(operation_id, "event-1", human_approved=True)
        finally:
            worker_db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        winner = executor.submit(execute, "accept:first")
        assert entered.wait(timeout=5)
        loser = executor.submit(execute, "accept:second")
        assert loser.result(timeout=5) is None
        release.set()
        assert winner.result(timeout=5) is not None

    verification_db = SQLiteDatabase(path)
    _, _, operations, _ = stack(verification_db, provider)
    assert operations.get("accept:second").status == ExternalActionStatus.FAILED
    verification_db.close()
    assert [call for call in provider.calls if call[0] == "accept"] == [
        ("accept", "accept:first")
    ]





def test_reschedule_claim_reserves_current_and_target_slots():
    repository = SQLiteSchedulingRepository(SQLiteDatabase())
    repository.create(event("event-1", status=ScheduleStatus.ACCEPTED))
    repository.create(
        event(
            "event-2",
            start_at=BASE_START + timedelta(minutes=15),
            end_at=BASE_START + timedelta(minutes=45),
        )
    )
    target_start = BASE_START + timedelta(hours=3)
    repository.claim_action(
        "event-1",
        "reschedule:1",
        (ScheduleStatus.ACCEPTED,),
        expected_version=1,
        reservation_start=target_start,
        reservation_end=target_start + timedelta(hours=1),
        enforce_conflicts=True,
    )

    with pytest.raises(SchedulingConflictError):
        repository.claim_action(
            "event-2",
            "accept:2",
            (ScheduleStatus.PROPOSED,),
            expected_version=1,
            reservation_start=BASE_START + timedelta(minutes=15),
            reservation_end=BASE_START + timedelta(minutes=45),
            enforce_conflicts=True,
        )


def test_prepared_acceptance_is_bound_to_original_event_version_and_slot():
    service, repository, operations, provider = stack(SQLiteDatabase())
    service.add_event(event())

    with pytest.raises(PermissionError):
        service.accept("accept:stale-slot", "event-1", human_approved=False)

    new_start = BASE_START + timedelta(days=2)
    changed = service.reschedule(
        "reschedule:winner",
        "event-1",
        start_at=new_start,
        end_at=new_start + timedelta(hours=1),
        timezone_name="Europe/Berlin",
        human_approved=True,
    )
    assert changed is not None
    assert changed.start_at == new_start

    assert service.accept(
        "accept:stale-slot", "event-1", human_approved=True
    ) is None
    assert (
        operations.get("accept:stale-slot").status
        == ExternalActionStatus.FAILED
    )
    assert repository.get("event-1").start_at == new_start
    assert provider.calls == [("reschedule", "reschedule:winner")]

def test_prepared_acceptance_rechecks_conflict_at_execution_time():
    service, _, operations, provider = stack(SQLiteDatabase())
    service.add_event(event("event-1"))
    service.add_event(
        event(
            "event-2",
            start_at=BASE_START + timedelta(minutes=15),
            end_at=BASE_START + timedelta(minutes=45),
        )
    )

    with pytest.raises(PermissionError):
        service.accept("accept:stale", "event-1", human_approved=False)

    accepted = service.accept("accept:winner", "event-2", human_approved=True)
    assert accepted is not None and accepted.status == ScheduleStatus.ACCEPTED

    assert service.accept("accept:stale", "event-1", human_approved=True) is None
    assert operations.get("accept:stale").status == ExternalActionStatus.FAILED
    assert provider.calls == [("accept", "accept:winner")]

def test_concurrent_overlapping_events_cannot_both_reach_provider(tmp_path):
    path = str(tmp_path / "concurrent-conflict.sqlite")
    entered = Event()
    release = Event()

    class BlockingProvider(FakeCalendarAdapter):
        def accept(self, operation_id, value):
            if operation_id == "accept:first":
                entered.set()
                assert release.wait(timeout=5)
            return super().accept(operation_id, value)

    provider = BlockingProvider()
    setup_db = SQLiteDatabase(path)
    setup, _, _, _ = stack(setup_db, provider)
    setup.add_event(event("event-1"))
    setup.add_event(
        event(
            "event-2",
            start_at=BASE_START + timedelta(minutes=15),
            end_at=BASE_START + timedelta(minutes=45),
        )
    )
    with pytest.raises(PermissionError):
        setup.accept("accept:first", "event-1", human_approved=False)
    with pytest.raises(PermissionError):
        setup.accept("accept:second", "event-2", human_approved=False)
    setup_db.close()

    def execute(operation_id: str, event_id: str):
        worker_db = SQLiteDatabase(path)
        worker, _, _, _ = stack(worker_db, provider)
        try:
            return worker.accept(operation_id, event_id, human_approved=True)
        finally:
            worker_db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(execute, "accept:first", "event-1")
        assert entered.wait(timeout=5)
        second = executor.submit(execute, "accept:second", "event-2")
        assert second.result(timeout=5) is None
        release.set()
        assert first.result(timeout=5) is not None

    verify_db = SQLiteDatabase(path)
    verify, _, operations, _ = stack(verify_db, provider)
    assert verify.get_event("event-1").status == ScheduleStatus.ACCEPTED
    assert verify.get_event("event-2").status == ScheduleStatus.PROPOSED
    assert operations.get("accept:second").status == ExternalActionStatus.FAILED
    verify_db.close()
    assert [call for call in provider.calls if call[0] == "accept"] == [
        ("accept", "accept:first")
    ]

def test_malformed_persisted_timezone_is_rejected():
    database = SQLiteDatabase()
    repository = SQLiteSchedulingRepository(database)
    repository.create(event())
    database.connection.execute(
        "UPDATE scheduling_events SET timezone_name = ? WHERE event_id = ?",
        ("Not/AZone", "event-1"),
    )
    database.connection.commit()

    with pytest.raises(ValueError, match="malformed"):
        repository.get("event-1")


def test_malformed_persisted_epoch_index_is_rejected():
    database = SQLiteDatabase()
    repository = SQLiteSchedulingRepository(database)
    repository.create(event())
    database.connection.execute(
        "UPDATE scheduling_events SET start_epoch_us = ? WHERE event_id = ?",
        (1, "event-1"),
    )
    database.connection.commit()

    with pytest.raises(ValueError, match="malformed"):
        repository.get("event-1")
