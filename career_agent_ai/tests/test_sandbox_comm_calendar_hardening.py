"""Durability, concurrency and evidence hardening for message and calendar providers."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Barrier, Thread

import pytest

from career_agent_ai.application.communication import (
    CommunicationMessage,
    CommunicationReconciliationEvidenceError,
    CommunicationService,
    DeliveryEvidence,
    DeliveryEvidenceState,
    MessageDirection,
    PreDeliveryCommunicationError,
)
from career_agent_ai.application.external_actions import (
    ExternalActionService,
    ExternalActionStatus,
)
from career_agent_ai.application.scheduling import (
    CalendarEvidence,
    CalendarEvidenceState,
    CalendarReconciliationEvidenceError,
    PreCalendarActionError,
    ScheduleEvent,
    ScheduleEventType,
    ScheduleStatus,
    SchedulingService,
)
from career_agent_ai.application.storage.sqlite_communication_repository import (
    SQLiteCommunicationRepository,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import (
    SQLiteExternalActionOperationRepository,
)
from career_agent_ai.application.storage.sqlite_sandbox_calendar_provider import (
    SandboxCalendarOutcome,
    SQLiteSandboxCalendarProvider,
)
from career_agent_ai.application.storage.sqlite_sandbox_communication_provider import (
    SandboxCommunicationOutcome,
    SQLiteSandboxCommunicationProvider,
)
from career_agent_ai.application.storage.sqlite_scheduling_repository import (
    SQLiteSchedulingRepository,
)

REQUIRED = ExternalActionStatus.RECONCILIATION_REQUIRED
MSG_OP = "send:hardening"
CAL_OP = "accept:hardening"
CM = SandboxCommunicationOutcome
CO = SandboxCalendarOutcome


class Crash(BaseException):
    """Simulated process death: not an Exception, so nothing is persisted or rolled back."""


def _plan(first, then=None):
    """Return a plan yielding ``first`` on attempt 1 and ``then`` afterwards."""
    return lambda _key, attempt: first if attempt == 1 else (then or first.__class__("success"))


def _message(**overrides) -> CommunicationMessage:
    values = dict(
        message_id="message-hardening",
        thread_id="thread-hardening",
        sender="candidate@example.test",
        recipient="recruiter@example.test",
        subject="Sandbox opportunity",
        body="This stays inside the sandbox.",
        direction=MessageDirection.DRAFT,
        created_at=datetime(2026, 11, 19, tzinfo=timezone.utc),
    )
    values.update(overrides)
    return CommunicationMessage(**values)


def _event(**overrides) -> ScheduleEvent:
    start = datetime(2026, 11, 20, 17, 0, tzinfo=timezone.utc)
    values = dict(
        event_id="interview-hardening",
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
    values.update(overrides)
    return ScheduleEvent(**values)


class Comm:
    """One simulated process for the communication workflow over a shared SQLite file."""

    def __init__(self, path, plan=None, provider_cls=SQLiteSandboxCommunicationProvider):
        self.database = SQLiteDatabase(str(path))
        self.provider = provider_cls(self.database, plan)
        self.messages = SQLiteCommunicationRepository(self.database)
        self.operations = SQLiteExternalActionOperationRepository(self.database)
        self.service = CommunicationService(
            self.messages,
            self.provider,
            ExternalActionService(
                self.operations,
                CommunicationService.action_adapter(self.provider, self.messages),
            ),
            require_provider_evidence=True,
        )

    def status(self, op=MSG_OP):
        return self.operations.get(op).status

    def send(self, op=MSG_OP, message_id="message-hardening"):
        return self.service.send(op, message_id, human_approved=True)

    def close(self):
        self.database.close()


class Cal:
    """One simulated process for the calendar workflow over a shared SQLite file."""

    def __init__(self, path, plan=None, provider_cls=SQLiteSandboxCalendarProvider):
        self.database = SQLiteDatabase(str(path))
        self.provider = provider_cls(self.database, plan)
        self.events = SQLiteSchedulingRepository(self.database)
        self.operations = SQLiteExternalActionOperationRepository(self.database)
        self.service = SchedulingService(
            self.events,
            self.provider,
            ExternalActionService(
                self.operations,
                SchedulingService.action_adapter(self.provider, self.events),
            ),
            require_provider_evidence=True,
        )

    def status(self, op=CAL_OP):
        return self.operations.get(op).status

    def accept(self, op=CAL_OP, event_id="interview-hardening"):
        return self.service.accept(op, event_id, human_approved=True)

    def close(self):
        self.database.close()


@pytest.fixture
def path(tmp_path):
    return tmp_path / "hardening.sqlite"


def _raw(path, sql, *args):
    connection = sqlite3.connect(path)
    connection.execute(sql, args)
    connection.commit()
    connection.close()


# ============================== communication ==============================


def _ambiguous_comm(path, outcome=CM.TIMEOUT_AFTER_EFFECT):
    comm = Comm(path, lambda _k, _a: outcome)
    comm.service.create_draft(_message())
    assert comm.send() is None and comm.status() == REQUIRED
    return comm


def test_comm_success_is_one_effect_and_replay_adds_none(path):
    comm = Comm(path)
    comm.service.create_draft(_message())
    first = comm.send()
    assert first is not None and first.direction == MessageDirection.OUTBOUND
    assert comm.send() == first
    assert comm.provider.delivery_count(MSG_OP) == 1
    assert comm.provider.verify_delivery(MSG_OP).state == DeliveryEvidenceState.PRESENT
    comm.close()


def test_comm_timeout_then_restart_reconciles_with_exactly_one_delivery(path):
    _ambiguous_comm(path).close()
    restarted = Comm(path, lambda _k, _a: CM.NO_EFFECT)
    with pytest.raises(CommunicationReconciliationEvidenceError):
        restarted.service.resolve_reconciliation(MSG_OP, delivered=False)
    restarted.service.resolve_reconciliation(MSG_OP, delivered=True)
    assert restarted.status() == ExternalActionStatus.SUCCEEDED
    assert restarted.provider.delivery_count(MSG_OP) == 1
    restarted.close()


def test_comm_crash_before_effect_leaves_no_trace_and_retries_once(path):
    def crashing_plan(_key, _attempt):
        raise Crash()

    crashing = Comm(path, crashing_plan)
    crashing.service.create_draft(_message())
    with pytest.raises(Crash):
        crashing.send()
    assert crashing.status() == ExternalActionStatus.IN_PROGRESS
    crashing.database.connection.close()  # process death: uncommitted work is lost

    recovered = Comm(path)
    assert recovered.provider.delivery_count(MSG_OP) == 0
    assert recovered.provider.verify_delivery(MSG_OP).state == (
        DeliveryEvidenceState.ABSENT_CONFIRMED
    )
    # Recovery never re-invokes the provider: it turns the in-flight action into a
    # reconciliation that must be settled by evidence.
    assert recovered.send() is None and recovered.status() == REQUIRED
    assert sqlite3.connect(path).execute(
        "SELECT COUNT(*) FROM sandbox_communication_attempts"
    ).fetchone()[0] == 0
    with pytest.raises(CommunicationReconciliationEvidenceError):
        recovered.service.resolve_reconciliation(MSG_OP, delivered=True)
    recovered.service.resolve_reconciliation(MSG_OP, delivered=False)
    assert recovered.send() is not None
    assert recovered.provider.delivery_count(MSG_OP) == 1
    recovered.close()


class CommCrashAfterEffect(SQLiteSandboxCommunicationProvider):
    def _deliver(self, *args, **kwargs):
        super()._deliver(*args, **kwargs)
        raise Crash()


def test_comm_crash_after_committed_effect_recovers_without_second_delivery(path):
    crashing = Comm(path, provider_cls=CommCrashAfterEffect)
    crashing.service.create_draft(_message())
    with pytest.raises(Crash):
        crashing.send()
    assert crashing.status() == ExternalActionStatus.IN_PROGRESS
    crashing.close()

    recovered = Comm(path, lambda _k, _a: CM.NO_EFFECT)
    assert recovered.send() is None and recovered.status() == REQUIRED
    with pytest.raises(CommunicationReconciliationEvidenceError):
        recovered.service.resolve_reconciliation(MSG_OP, delivered=False)
    recovered.service.resolve_reconciliation(MSG_OP, delivered=True)
    assert recovered.status() == ExternalActionStatus.SUCCEEDED
    assert recovered.provider.delivery_count(MSG_OP) == 1
    recovered.close()


def test_comm_conflicting_key_and_conflicting_message_are_definitive_no_effect(path):
    comm = Comm(path)
    comm.service.create_draft(_message())
    comm.send()
    # same key, changed intent
    with pytest.raises(PreDeliveryCommunicationError, match="different message intent"):
        comm.provider.send(MSG_OP, replace(_message(), body="altered"))
    # a second key for an already-delivered message must be a definitive no-effect
    with pytest.raises(PreDeliveryCommunicationError):
        comm.provider.send("send:second-key", _message())
    assert comm.provider.delivery_count("send:second-key") == 0
    assert comm.provider.verify_delivery("send:second-key").state == (
        DeliveryEvidenceState.ABSENT_CONFIRMED
    )
    comm.close()


def test_comm_second_key_for_a_delivered_message_is_rejected_by_the_service(path):
    comm = Comm(path)
    comm.service.create_draft(_message())
    comm.send()
    with pytest.raises(ValueError, match="draft"):
        comm.send("send:second-key")
    assert comm.provider.delivery_count("send:second-key") == 0
    assert comm.provider.delivery_count(MSG_OP) == 1
    comm.close()


def test_comm_message_id_cannot_be_rebound_to_different_content_by_another_key(path):
    comm = Comm(path)
    comm.provider.draft(_message())
    with pytest.raises(PreDeliveryCommunicationError):
        comm.provider.send("send:rebind", _message(body="rebound content"))
    assert comm.provider.delivery_count("send:rebind") == 0
    comm.close()


@pytest.mark.parametrize(
    "column, value",
    [
        ("recipient", "attacker@example.test"),
        ("sender", "other@example.test"),
        ("subject", "Forged subject"),
        ("body", "Forged body"),
        ("thread_id", "thread-forged"),
    ],
)
def test_comm_tampered_delivery_evidence_is_rejected(path, column, value):
    comm = _ambiguous_comm(path)
    _raw(path, f"UPDATE sandbox_communication_messages SET {column} = ?", value)
    with pytest.raises(CommunicationReconciliationEvidenceError):
        comm.service.resolve_reconciliation(MSG_OP, delivered=True)
    assert comm.status() == REQUIRED
    comm.close()


class CommUnknown(SQLiteSandboxCommunicationProvider):
    def verify_delivery(self, operation_id):
        return DeliveryEvidence(DeliveryEvidenceState.UNKNOWN)


class CommBroken(SQLiteSandboxCommunicationProvider):
    def verify_delivery(self, operation_id):
        raise sqlite3.OperationalError("lookup unavailable")


class CommMalformed(SQLiteSandboxCommunicationProvider):
    def verify_delivery(self, operation_id):
        return {"state": "present"}


@pytest.mark.parametrize("provider_cls", [CommUnknown, CommBroken, CommMalformed])
@pytest.mark.parametrize("delivered", [True, False])
def test_comm_unknown_failed_or_malformed_evidence_fails_closed(path, provider_cls, delivered):
    comm = Comm(path, lambda _k, _a: CM.TIMEOUT_AFTER_EFFECT, provider_cls)
    comm.service.create_draft(_message())
    assert comm.send() is None
    with pytest.raises(CommunicationReconciliationEvidenceError):
        comm.service.resolve_reconciliation(MSG_OP, delivered=delivered)
    assert comm.status() == REQUIRED
    comm.close()


def test_comm_concurrent_identical_sends_produce_one_delivery(path):
    Comm(path).close()
    seed = Comm(path)
    seed.service.create_draft(_message())
    seed.close()
    _parallel(lambda: Comm(path), lambda c: c.send())
    # Either caller may win; a caller that sees the action in flight refuses to repeat it
    # and leaves it for evidence-based reconciliation. Never a second effect, never lost.
    verifier = Comm(path)
    assert verifier.provider.delivery_count(MSG_OP) == 1
    assert verifier.status() in {ExternalActionStatus.SUCCEEDED, REQUIRED}
    if verifier.status() == REQUIRED:
        verifier.service.resolve_reconciliation(MSG_OP, delivered=True)
    assert verifier.status() == ExternalActionStatus.SUCCEEDED
    assert verifier.provider.delivery_count(MSG_OP) == 1
    verifier.close()


def test_comm_concurrent_different_keys_for_one_message_produce_one_delivery(path):
    seed = Comm(path)
    seed.service.create_draft(_message())
    seed.close()
    keys = iter(["send:key-a", "send:key-b"])
    ops = [next(keys), next(keys)]
    barrier = Barrier(2)
    results: list[object] = []

    def worker(op):
        comm = Comm(path)
        try:
            barrier.wait(timeout=10)
            results.append(comm.send(op))
        except Exception as exc:
            results.append(exc)
        finally:
            comm.close()

    threads = [Thread(target=worker, args=(op,)) for op in ops]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    count = sqlite3.connect(path).execute(
        "SELECT COUNT(*) FROM sandbox_communication_deliveries"
    ).fetchone()[0]
    assert count == 1
    assert sum(1 for r in results if isinstance(r, CommunicationMessage)) == 1


class FaultyConnection:
    """Delegating connection injecting one scripted SQLite failure on provider statements."""

    def __init__(self, real, fail_on, marker, *, durable=False):
        self._real, self._fail_on, self._marker, self._durable = real, fail_on, marker, durable
        self.tripped = False
        self._pending = False

    def __getattr__(self, name):
        return getattr(self._real, name)

    def execute(self, sql, *args):
        if self._fail_on == "begin" and not self.tripped and sql == "BEGIN IMMEDIATE":
            self.tripped = True
            raise sqlite3.OperationalError("database is locked")
        if self._marker in sql:
            if self._fail_on == "insert" and not self.tripped:
                self.tripped = True
                raise sqlite3.OperationalError("disk I/O error")
            self._pending = True
        return self._real.execute(sql, *args)

    def commit(self):
        if self._fail_on == "commit" and not self.tripped and self._pending:
            self.tripped = True
            if self._durable:
                self._real.commit()
            else:
                self._real.rollback()
            raise sqlite3.OperationalError("commit failed")
        self._pending = False
        return self._real.commit()


def _inject(stack, fail_on, marker, durable=False):
    faulty = FaultyConnection(stack.database.connection, fail_on, marker, durable=durable)
    stack.database._connection = faulty
    return faulty


_DELIVERY_INSERT = "INSERT INTO sandbox_communication_deliveries"
_EFFECT_INSERT = "INSERT INTO sandbox_calendar_effects"


def test_comm_lock_contention_is_a_definitive_no_effect_and_retry_succeeds(path):
    comm = Comm(path)
    comm.service.create_draft(_message())
    faulty = _inject(comm, "begin", _DELIVERY_INSERT)
    assert comm.send() is None
    assert faulty.tripped and comm.status() == ExternalActionStatus.FAILED
    assert comm.provider.delivery_count(MSG_OP) == 0
    assert comm.send() is None  # a failed operation id is terminal; no provider call
    assert comm.provider.delivery_count(MSG_OP) == 0
    assert comm.send("send:retry") is not None  # a legitimate new attempt, new key
    assert comm.provider.delivery_count("send:retry") == 1
    comm.close()


def test_comm_evidence_lookup_under_lock_contention_fails_closed(path):
    comm = _ambiguous_comm(path)
    faulty = _inject(comm, "begin", _DELIVERY_INSERT)
    with pytest.raises(CommunicationReconciliationEvidenceError):
        comm.service.resolve_reconciliation(MSG_OP, delivered=True)
    assert faulty.tripped
    comm.service.resolve_reconciliation(MSG_OP, delivered=True)
    assert comm.status() == ExternalActionStatus.SUCCEEDED
    comm.close()


@pytest.mark.parametrize("fail_on", ["insert", "commit"])
def test_comm_sqlite_failure_without_commit_rolls_back_and_retry_is_safe(path, fail_on):
    comm = Comm(path)
    comm.service.create_draft(_message())
    faulty = _inject(comm, fail_on, _DELIVERY_INSERT)
    assert comm.send() is None
    assert faulty.tripped and comm.status() == REQUIRED
    assert comm.provider.delivery_count(MSG_OP) == 0
    with pytest.raises(CommunicationReconciliationEvidenceError):
        comm.service.resolve_reconciliation(MSG_OP, delivered=True)
    comm.service.resolve_reconciliation(MSG_OP, delivered=False)
    assert comm.send() is not None
    assert comm.provider.delivery_count(MSG_OP) == 1
    comm.close()


def test_comm_commit_error_after_durable_effect_is_resolved_by_evidence(path):
    comm = Comm(path)
    comm.service.create_draft(_message())
    _inject(comm, "commit", _DELIVERY_INSERT, durable=True)
    assert comm.send() is None and comm.status() == REQUIRED
    assert comm.provider.delivery_count(MSG_OP) == 1
    with pytest.raises(CommunicationReconciliationEvidenceError):
        comm.service.resolve_reconciliation(MSG_OP, delivered=False)
    comm.service.resolve_reconciliation(MSG_OP, delivered=True)
    assert comm.provider.delivery_count(MSG_OP) == 1
    comm.close()


# ================================ calendar ================================


def _ambiguous_cal(path, outcome=CO.TIMEOUT_AFTER_EFFECT):
    cal = Cal(path, lambda _k, _a: outcome)
    cal.service.add_event(_event())
    assert cal.accept() is None and cal.status() == REQUIRED
    return cal


def test_cal_success_is_one_effect_and_replay_adds_none(path):
    cal = Cal(path)
    cal.service.add_event(_event())
    first = cal.accept()
    assert first.status == ScheduleStatus.ACCEPTED
    assert cal.accept() == first
    assert cal.provider.effect_count(CAL_OP) == 1
    assert cal.provider.verify_calendar_action(CAL_OP).state == CalendarEvidenceState.PRESENT
    cal.close()


def test_cal_timeout_then_restart_reconciles_with_exactly_one_effect(path):
    _ambiguous_cal(path).close()
    restarted = Cal(path, lambda _k, _a: CO.NO_EFFECT)
    with pytest.raises(CalendarReconciliationEvidenceError):
        restarted.service.resolve_reconciliation(CAL_OP, completed=False)
    resolved = restarted.service.resolve_reconciliation(CAL_OP, completed=True)
    assert resolved.status == ExternalActionStatus.SUCCEEDED
    assert restarted.provider.effect_count(CAL_OP) == 1
    restarted.close()


@pytest.mark.parametrize("forged", ["forged-provider-id", "fake-other-event"])
def test_cal_forged_provider_event_id_is_rejected(path, forged):
    cal = _ambiguous_cal(path)
    with pytest.raises(CalendarReconciliationEvidenceError):
        cal.service.resolve_reconciliation(CAL_OP, completed=True, provider_event_id=forged)
    assert cal.status() == REQUIRED
    cal.service.resolve_reconciliation(
        CAL_OP, completed=True, provider_event_id="fake-interview-hardening"
    )
    assert cal.status() == ExternalActionStatus.SUCCEEDED
    cal.close()


def test_cal_empty_provider_event_id_claim_is_invalid_input(path):
    cal = _ambiguous_cal(path)
    with pytest.raises(ValueError, match="provider_event_id"):
        cal.service.resolve_reconciliation(CAL_OP, completed=True, provider_event_id="")
    assert cal.status() == REQUIRED
    cal.close()


def test_cal_crash_before_effect_leaves_no_trace_and_retries_once(path):
    def crashing_plan(_key, _attempt):
        raise Crash()

    crashing = Cal(path, crashing_plan)
    crashing.service.add_event(_event())
    with pytest.raises(Crash):
        crashing.accept()
    assert crashing.status() == ExternalActionStatus.IN_PROGRESS
    crashing.database.connection.close()

    recovered = Cal(path)
    assert recovered.provider.effect_count(CAL_OP) == 0
    assert recovered.accept() is None and recovered.status() == REQUIRED
    with pytest.raises(CalendarReconciliationEvidenceError):
        recovered.service.resolve_reconciliation(CAL_OP, completed=True)
    recovered.service.resolve_reconciliation(CAL_OP, completed=False)
    assert recovered.accept().status == ScheduleStatus.ACCEPTED
    assert recovered.provider.effect_count(CAL_OP) == 1
    recovered.close()


class CalCrashAfterEffect(SQLiteSandboxCalendarProvider):
    def _deliver(self, *args, **kwargs):
        super()._deliver(*args, **kwargs)
        raise Crash()


def test_cal_crash_after_committed_effect_recovers_without_second_effect(path):
    crashing = Cal(path, provider_cls=CalCrashAfterEffect)
    crashing.service.add_event(_event())
    with pytest.raises(Crash):
        crashing.accept()
    assert crashing.status() == ExternalActionStatus.IN_PROGRESS
    crashing.close()

    recovered = Cal(path, lambda _k, _a: CO.NO_EFFECT)
    assert recovered.accept() is None and recovered.status() == REQUIRED
    with pytest.raises(CalendarReconciliationEvidenceError):
        recovered.service.resolve_reconciliation(CAL_OP, completed=False)
    recovered.service.resolve_reconciliation(CAL_OP, completed=True)
    assert recovered.status() == ExternalActionStatus.SUCCEEDED
    assert recovered.provider.effect_count(CAL_OP) == 1
    recovered.close()


def test_cal_second_key_cannot_accept_an_already_accepted_event(path):
    cal = Cal(path)
    source = _event()
    cal.provider.accept(CAL_OP, source)
    with pytest.raises(PreCalendarActionError):
        cal.provider.accept("accept:second-key", source)
    assert cal.provider.effect_count("accept:second-key") == 0
    cal.close()


def test_cal_accept_after_reschedule_cycle_remains_allowed(path):
    cal = Cal(path)
    source = _event()
    cal.provider.accept(CAL_OP, source)
    moved_start = source.start_at + timedelta(days=1)
    accepted = replace(source, status=ScheduleStatus.ACCEPTED, provider_event_id="fake-interview-hardening")
    moved_end = moved_start + timedelta(hours=1)
    cal.provider.reschedule("reschedule:1", accepted, moved_start, moved_end, "Europe/Berlin")
    pending = replace(
        accepted,
        status=ScheduleStatus.RESCHEDULE_REQUESTED,
        start_at=moved_start,
        end_at=moved_end,
    )
    assert cal.provider.accept("accept:after-reschedule", pending).status == ScheduleStatus.ACCEPTED
    cal.close()


@pytest.mark.parametrize(
    "overrides",
    [
        {"candidate_id": "candidate-2"},
        {"application_id": "application-2"},
    ],
)
def test_cal_event_id_cannot_be_taken_over_by_another_candidate_or_application(path, overrides):
    cal = Cal(path)
    cal.provider.accept(CAL_OP, _event())
    with pytest.raises(PreCalendarActionError):
        cal.provider.decline("decline:takeover", _event(**overrides))
    assert cal.provider.effect_count("decline:takeover") == 0
    cal.close()


@pytest.mark.parametrize(
    "mutation",
    [
        ("candidate_id", "candidate-2"),
        ("application_id", "application-2"),
        ("event_id", "interview-other"),
        ("location", "Forged room"),
        ("start_at", "2030-01-01T00:00:00+00:00"),
    ],
)
def test_cal_tampered_evidence_is_rejected(path, mutation):
    import json

    cal = _ambiguous_cal(path)
    connection = sqlite3.connect(path)
    raw = connection.execute("SELECT event_json FROM sandbox_calendar_effects").fetchone()[0]
    document = json.loads(raw)
    document[mutation[0]] = mutation[1]
    connection.execute("UPDATE sandbox_calendar_effects SET event_json = ?", (json.dumps(document),))
    connection.commit()
    connection.close()
    with pytest.raises(CalendarReconciliationEvidenceError):
        cal.service.resolve_reconciliation(CAL_OP, completed=True)
    assert cal.status() == REQUIRED
    cal.close()


class CalUnknown(SQLiteSandboxCalendarProvider):
    def verify_calendar_action(self, operation_id):
        return CalendarEvidence(CalendarEvidenceState.UNKNOWN)


class CalBroken(SQLiteSandboxCalendarProvider):
    def verify_calendar_action(self, operation_id):
        raise sqlite3.OperationalError("lookup unavailable")


class CalMalformed(SQLiteSandboxCalendarProvider):
    def verify_calendar_action(self, operation_id):
        return {"state": "present"}


@pytest.mark.parametrize("provider_cls", [CalUnknown, CalBroken, CalMalformed])
@pytest.mark.parametrize("completed", [True, False])
def test_cal_unknown_failed_or_malformed_evidence_fails_closed(path, provider_cls, completed):
    cal = Cal(path, lambda _k, _a: CO.TIMEOUT_AFTER_EFFECT, provider_cls)
    cal.service.add_event(_event())
    assert cal.accept() is None
    with pytest.raises(CalendarReconciliationEvidenceError):
        cal.service.resolve_reconciliation(CAL_OP, completed=completed)
    assert cal.status() == REQUIRED
    cal.close()


def test_cal_concurrent_identical_accepts_produce_one_effect(path):
    seed = Cal(path)
    seed.service.add_event(_event())
    seed.close()
    _parallel(lambda: Cal(path), lambda c: c.accept())
    verifier = Cal(path)
    assert verifier.provider.effect_count(CAL_OP) == 1
    assert verifier.status() in {ExternalActionStatus.SUCCEEDED, REQUIRED}
    if verifier.status() == REQUIRED:
        verifier.service.resolve_reconciliation(CAL_OP, completed=True)
    assert verifier.status() == ExternalActionStatus.SUCCEEDED
    assert verifier.provider.effect_count(CAL_OP) == 1
    verifier.close()


def test_cal_concurrent_different_keys_for_one_event_produce_one_effect(path):
    seed = Cal(path)
    seed.service.add_event(_event())
    seed.close()
    barrier = Barrier(2)
    results: list[object] = []

    def worker(op):
        cal = Cal(path)
        try:
            barrier.wait(timeout=10)
            results.append(cal.accept(op))
        except Exception as exc:
            results.append(exc)
        finally:
            cal.close()

    threads = [Thread(target=worker, args=(op,)) for op in ("accept:key-a", "accept:key-b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    count = sqlite3.connect(path).execute(
        "SELECT COUNT(*) FROM sandbox_calendar_effects WHERE action = 'accept'"
    ).fetchone()[0]
    assert count == 1


def test_cal_lock_contention_is_a_definitive_no_effect_and_retry_succeeds(path):
    cal = Cal(path)
    cal.service.add_event(_event())
    faulty = _inject(cal, "begin", _EFFECT_INSERT)
    assert cal.accept() is None
    assert faulty.tripped and cal.status() == ExternalActionStatus.FAILED
    assert cal.provider.effect_count(CAL_OP) == 0
    assert cal.accept("accept:retry").status == ScheduleStatus.ACCEPTED
    assert cal.provider.effect_count("accept:retry") == 1
    cal.close()


def test_cal_evidence_lookup_under_lock_contention_fails_closed(path):
    cal = _ambiguous_cal(path)
    faulty = _inject(cal, "begin", _EFFECT_INSERT)
    with pytest.raises(CalendarReconciliationEvidenceError):
        cal.service.resolve_reconciliation(CAL_OP, completed=True)
    assert faulty.tripped
    cal.service.resolve_reconciliation(CAL_OP, completed=True)
    assert cal.status() == ExternalActionStatus.SUCCEEDED
    cal.close()


@pytest.mark.parametrize("fail_on", ["insert", "commit"])
def test_cal_sqlite_failure_without_commit_rolls_back_and_retry_is_safe(path, fail_on):
    cal = Cal(path)
    cal.service.add_event(_event())
    faulty = _inject(cal, fail_on, _EFFECT_INSERT)
    assert cal.accept() is None
    assert faulty.tripped and cal.status() == REQUIRED
    assert cal.provider.effect_count(CAL_OP) == 0
    with pytest.raises(CalendarReconciliationEvidenceError):
        cal.service.resolve_reconciliation(CAL_OP, completed=True)
    cal.service.resolve_reconciliation(CAL_OP, completed=False)
    assert cal.accept().status == ScheduleStatus.ACCEPTED
    assert cal.provider.effect_count(CAL_OP) == 1
    cal.close()


def test_cal_commit_error_after_durable_effect_is_resolved_by_evidence(path):
    cal = Cal(path)
    cal.service.add_event(_event())
    _inject(cal, "commit", _EFFECT_INSERT, durable=True)
    assert cal.accept() is None and cal.status() == REQUIRED
    assert cal.provider.effect_count(CAL_OP) == 1
    with pytest.raises(CalendarReconciliationEvidenceError):
        cal.service.resolve_reconciliation(CAL_OP, completed=False)
    cal.service.resolve_reconciliation(CAL_OP, completed=True)
    assert cal.provider.effect_count(CAL_OP) == 1
    cal.close()


# ================================= helpers =================================


def _parallel(make_stack, work):
    barrier = Barrier(2)
    outcomes: list[object] = []

    def worker():
        stack = make_stack()
        try:
            barrier.wait(timeout=10)
            outcomes.append(work(stack))
        except Exception as exc:
            outcomes.append(exc)
        finally:
            stack.close()

    threads = [Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert len(outcomes) == 2
    return outcomes
