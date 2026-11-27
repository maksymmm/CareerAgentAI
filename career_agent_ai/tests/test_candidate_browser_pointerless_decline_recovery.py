"""Chromium proof for pointerless candidate-decline recovery after a committed provider effect."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from threading import Thread
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

from career_agent_ai.application.api.candidate_schedule_decisions import (
    CandidateScheduleDeclineWSGIApp,
)
from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
from career_agent_ai.application.scheduling.models import (
    ScheduleEvent,
    ScheduleEventType,
    ScheduleStatus,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_scheduling_repository import (
    SQLiteSchedulingRepository,
)

OWNER = "candidate-1"
EVENT_ID = "browser-pointerless-decline-event"
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


def _seed_event(path):
    database = SQLiteDatabase(str(path))
    try:
        SQLiteSchedulingRepository(database).create(
            ScheduleEvent(
                event_id=EVENT_ID,
                candidate_id=OWNER,
                employer_name="Sandbox GmbH",
                event_type=ScheduleEventType.INTERVIEW,
                location="Sandbox video room",
                start_at=NOW + timedelta(days=11),
                end_at=NOW + timedelta(days=11, hours=1),
                timezone_name="Europe/Berlin",
                status=ScheduleStatus.PROPOSED,
                provider_event_id="fake-" + EVENT_ID,
                application_id="browser-pointerless-decline-application",
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    finally:
        database.close()


def _runtime(path):
    return RuntimeCandidateApp(
        database_path=str(path),
        jobs=(),
        resolve_bearer=lambda token: {
            "owner-token": OWNER,
            "foreign-token": "candidate-2",
        }.get(token),
    )


def _serve(runtime):
    server = make_server("127.0.0.1", 0, runtime, handler_class=QuietHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop(server, thread):
    server.shutdown()
    thread.join(timeout=5)
    assert not thread.is_alive()
    server.server_close()


def _durable_snapshot(path):
    operation_id = CandidateScheduleDeclineWSGIApp._operation_id(OWNER, EVENT_ID, 1)
    connection = sqlite3.connect(path)
    try:
        event = connection.execute(
            "SELECT status, version, active_action_operation_id "
            "FROM scheduling_events WHERE event_id = ?",
            (EVENT_ID,),
        ).fetchone()
        operation = connection.execute(
            "SELECT status FROM external_action_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        attempts = connection.execute(
            "SELECT attempt_no, outcome FROM sandbox_calendar_attempts "
            "WHERE operation_id = ? ORDER BY attempt_no",
            (operation_id,),
        ).fetchall()
        effect = connection.execute(
            "SELECT action, replays FROM sandbox_calendar_effects "
            "WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        return {
            "event": event,
            "operation": operation,
            "attempts": attempts,
            "effect": effect,
        }
    finally:
        connection.close()


def _open_event_in_browser(page, base_url):
    page.goto(base_url + "/sandbox/candidate")
    page.locator("#token").fill("owner-token")
    page.locator("#list-events").click()
    expect = pytest.importorskip("playwright.sync_api").expect
    expect(page.locator("#owned-events")).to_contain_text("Sandbox GmbH")
    page.locator("#owned-events button").click()
    expect(page.locator("#event-detail")).to_contain_text(EVENT_ID)


def test_browser_recovers_pointerless_provider_applied_decline_after_restart(
    tmp_path,
):
    """Fresh candidate UI restores and reconciles one committed decline without replay."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from playwright.sync_api import expect

    path = tmp_path / "candidate-pointerless-decline-browser.sqlite"
    _seed_event(path)
    resolve_bearer = lambda token: {
        "owner-token": OWNER,
        "foreign-token": "candidate-2",
    }.get(token)

    # Initialize the durable provider/operation schemas before injecting the
    # post-effect failure. The trigger rejects only the terminal success write.
    bootstrap = CandidateScheduleDeclineWSGIApp(
        database_path=str(path),
        resolve_bearer=resolve_bearer,
    )
    database, _, _, _ = bootstrap._open_stack()
    try:
        database.connection.execute(
            """
            CREATE TRIGGER fail_decline_success_persistence
            BEFORE UPDATE OF status ON external_action_operations
            WHEN NEW.action_type = 'calendar.decline'
              AND NEW.status = 'succeeded'
            BEGIN
                SELECT RAISE(ABORT, 'simulated terminal persistence failure');
            END
            """
        )
        database.connection.commit()
    finally:
        database.close()

    first_runtime = _runtime(path)
    second_runtime = None
    first_server = None
    second_server = None
    first_thread = None
    second_thread = None

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            first_server, first_thread = _serve(first_runtime)
            first_page = browser.new_page()
            _open_event_in_browser(
                first_page, f"http://127.0.0.1:{first_server.server_port}"
            )
            expect(first_page.locator("#decline-confirmed")).to_be_enabled()
            first_page.locator("#decline-confirmed").check()
            first_page.locator("#decline-event").click()
            expect(first_page.locator("#decline-reconciliation-panel")).to_be_enabled()
            expect(first_page.locator("#decline-reconciliation-state")).to_contain_text(
                "Provider outcome is uncertain."
            )

            after_provider_commit = _durable_snapshot(str(path))
            assert after_provider_commit == {
                "event": ("declined", 2, None),
                "operation": ("reconciliation_required",),
                "attempts": [(1, "success")],
                "effect": ("decline", 0),
            }

            # Remove the injected persistence fault before the evidence-only recovery.
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "DROP TRIGGER fail_decline_success_persistence"
                )
                connection.commit()
            finally:
                connection.close()

            first_page.close()
            _stop(first_server, first_thread)
            first_server = None
            first_thread = None
            first_runtime.close()

            second_runtime = _runtime(path)
            second_server, second_thread = _serve(second_runtime)
            second_page = browser.new_page()
            _open_event_in_browser(
                second_page, f"http://127.0.0.1:{second_server.server_port}"
            )

            expect(second_page.locator("#decline-reconciliation-panel")).to_be_enabled()
            expect(second_page.locator("#decline-reconciliation-state")).to_contain_text(
                "An ambiguous interview decline was restored from durable storage."
            )
            expect(second_page.locator("#confirm-decline-completed")).to_be_disabled()
            expect(second_page.locator("#confirm-decline-no-effect")).to_be_disabled()

            # Loading the event and pending intent is read-only: the same single
            # provider attempt/effect remains durable until fresh candidate attestation.
            assert _durable_snapshot(str(path)) == after_provider_commit

            second_page.locator("#decline-reconciliation-verified").check()
            expect(second_page.locator("#confirm-decline-completed")).to_be_enabled()
            second_page.locator("#confirm-decline-completed").click()
            expect(second_page.locator("#decline-reconciliation-state")).to_contain_text(
                "Provider evidence confirmed the interview decline."
            )
            expect(second_page.locator("#event-detail")).to_contain_text('"status": "declined"')
            expect(second_page.locator("#event-detail")).to_contain_text('"state_version": 2')

            assert _durable_snapshot(str(path)) == {
                "event": ("declined", 2, None),
                "operation": ("succeeded",),
                "attempts": [(1, "success")],
                "effect": ("decline", 0),
            }
        finally:
            if first_server is not None:
                _stop(first_server, first_thread)
            if second_server is not None:
                _stop(second_server, second_thread)
            first_runtime.close()
            if second_runtime is not None:
                second_runtime.close()
            browser.close()
