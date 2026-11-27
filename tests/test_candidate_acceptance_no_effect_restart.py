from __future__ import annotations

import json
from datetime import datetime, timezone
from threading import Thread
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest


def test_candidate_acceptance_retries_confirmed_no_effect_after_runtime_restart(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from career_agent_ai.application.career.autonomous_career_loop import (
        _candidate_schedule_accept_operation_id,
    )
    from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
    from career_agent_ai.application.scheduling.models import (
        ScheduleEvent,
        ScheduleEventType,
        ScheduleStatus,
    )
    from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
    from career_agent_ai.application.storage.sqlite_sandbox_calendar_provider import (
        SandboxCalendarOutcome,
    )
    from career_agent_ai.application.storage.sqlite_scheduling_repository import (
        SQLiteSchedulingRepository,
    )

    path = tmp_path / "candidate-acceptance-no-effect-restart.sqlite"
    database = SQLiteDatabase(str(path))
    SQLiteSchedulingRepository(database).create(ScheduleEvent(
        event_id="event-no-effect-1",
        candidate_id="candidate-1",
        employer_name="Sandbox GmbH",
        event_type=ScheduleEventType.INTERVIEW,
        location="Berlin",
        start_at=datetime(2026, 11, 24, 10, tzinfo=timezone.utc),
        end_at=datetime(2026, 11, 24, 11, tzinfo=timezone.utc),
        timezone_name="UTC",
        status=ScheduleStatus.PROPOSED,
    ))
    database.close()

    def first_attempt_no_effect(_operation_id, attempt):
        assert attempt == 1
        return SandboxCalendarOutcome.NO_EFFECT

    def second_attempt_succeeds(_operation_id, attempt):
        assert attempt == 2
        return SandboxCalendarOutcome.SUCCESS

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, format, *args):
            pass

    resolve_bearer = lambda token: "candidate-1" if token == "valid" else None
    runtime1 = RuntimeCandidateApp(
        database_path=str(path),
        jobs=(),
        resolve_bearer=resolve_bearer,
        sandbox_calendar_outcome_plan=first_attempt_no_effect,
    )
    server1 = make_server("127.0.0.1", 0, runtime1, handler_class=QuietHandler)
    thread1 = Thread(target=server1.serve_forever, daemon=True)
    thread1.start()

    runtime2 = None
    server2 = None
    thread2 = None
    server1_stopped = False
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server1.server_port}/sandbox/candidate")
            page.locator("#token").fill("valid")
            page.locator("#list-events").click()
            page.locator("#owned-events button").click()
            page.wait_for_function(
                "() => !document.querySelector('#accept-event').disabled"
            )

            # The sandbox provider durably records NO_EFFECT and the operation
            # becomes safely retryable without an ambiguous reconciliation claim.
            page.locator("#accept-event").click()
            page.locator("#event-acceptance-state").get_by_text(
                "The provider confirmed no effect", exact=False
            ).wait_for()
            assert not page.locator("#accept-event").is_disabled()

            # Simulate process loss after the provider's definite no-effect result.
            server1.shutdown()
            thread1.join(timeout=5)
            server1.server_close()
            runtime1.close()
            server1_stopped = True

            runtime2 = RuntimeCandidateApp(
                database_path=str(path),
                jobs=(),
                resolve_bearer=resolve_bearer,
                sandbox_calendar_outcome_plan=second_attempt_succeeds,
            )
            server2 = make_server("127.0.0.1", 0, runtime2, handler_class=QuietHandler)
            thread2 = Thread(target=server2.serve_forever, daemon=True)
            thread2.start()

            page.goto(f"http://127.0.0.1:{server2.server_port}/sandbox/candidate")
            page.locator("#token").fill("valid")
            page.locator("#list-events").click()
            page.locator("#owned-events button").click()
            page.locator("#event-acceptance-state").get_by_text(
                "The provider confirmed no effect", exact=False
            ).wait_for()
            assert not page.locator("#accept-event").is_disabled()

            # A fresh explicit candidate click reuses the same event-version
            # operation; the restarted runtime must observe attempt number two.
            page.locator("#accept-event").click()
            page.locator("#event-acceptance-state").get_by_text(
                "Interview acceptance is confirmed", exact=False
            ).wait_for()
            assert page.locator("#accept-event").is_disabled()

            # Refresh the event projection and prove the durable version advanced once.
            page.locator("#owned-events button").click()
            page.locator("#event-detail").get_by_text(
                '"state_version": 2', exact=False
            ).wait_for()
            event = json.loads(page.locator("#event-detail").text_content())
            assert event["status"] == "accepted"
            assert event["state_version"] == 2
            browser.close()
    finally:
        if server2 is not None:
            server2.shutdown()
            if thread2 is not None:
                thread2.join(timeout=5)
            server2.server_close()
        if runtime2 is not None:
            runtime2.close()
        if not server1_stopped:
            server1.shutdown()
            thread1.join(timeout=5)
            server1.server_close()
            runtime1.close()

    operation_id = _candidate_schedule_accept_operation_id(
        "candidate-1", "event-no-effect-1", 1
    )
    database = SQLiteDatabase(str(path))
    operation = database.connection.execute(
        "SELECT action_type, status, payload_json, result_json "
        "FROM external_action_operations WHERE operation_id = ?",
        (operation_id,),
    ).fetchone()
    assert operation is not None
    assert operation[0] == "calendar.accept"
    assert operation[1] == "succeeded"
    payload = json.loads(operation[2])
    assert payload["event_id"] == "event-no-effect-1"
    assert payload["event_version"] == 1
    assert operation[3] is not None

    attempts = database.connection.execute(
        "SELECT attempt_no, outcome FROM sandbox_calendar_attempts "
        "WHERE operation_id = ? ORDER BY attempt_no",
        (operation_id,),
    ).fetchall()
    assert attempts == [(1, "no_effect"), (2, "success")]

    effect = database.connection.execute(
        "SELECT event_json, replays FROM sandbox_calendar_effects WHERE operation_id = ?",
        (operation_id,),
    ).fetchone()
    assert effect is not None
    assert effect[1] == 0
    assert json.loads(effect[0])["status"] == "accepted"
    assert database.connection.execute(
        "SELECT COUNT(*) FROM sandbox_calendar_effects"
    ).fetchone()[0] == 1
    assert database.connection.execute(
        "SELECT status, version, active_action_operation_id "
        "FROM scheduling_events WHERE event_id = ?",
        ("event-no-effect-1",),
    ).fetchone() == ("accepted", 2, None)
    database.close()
