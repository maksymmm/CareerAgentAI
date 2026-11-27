"""Browser/runtime restart regression for pointerless candidate reschedule recovery."""

from __future__ import annotations

import sqlite3
from threading import Thread
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

from career_agent_ai.application.api.candidate_schedule_actions import (
    CandidateScheduleActionWSGIApp,
)
from career_agent_ai.tests.test_candidate_schedule_actions import (
    Candidate,
    END,
    EVENT_ID,
    OWNER,
    PENDING_END,
    PENDING_START,
    START,
    TOKENS,
    ZONE,
    _event,
    _seed,
)


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


def _serve(candidate):
    server = make_server(
        "127.0.0.1", 0, candidate.app, handler_class=QuietHandler
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop(server, thread):
    server.shutdown()
    thread.join(timeout=10)
    assert not thread.is_alive()
    server.server_close()


def _durable_snapshot(path, operation_id):
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


def test_candidate_browser_recovers_pointerless_provider_applied_reschedule_after_restart(
    tmp_path,
):
    """Restore a provider-applied reschedule after terminal persistence fails, without replay."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from playwright.sync_api import expect

    path = tmp_path / "candidate-pointerless-reschedule-browser.sqlite"
    _seed(path, _event())

    # Initialize the durable operation and sandbox-provider schemas before
    # injecting a failure that affects only the terminal success write.
    bootstrap = CandidateScheduleActionWSGIApp(
        database_path=str(path),
        resolve_bearer=TOKENS.get,
    )
    database, _, _, _ = bootstrap._open_stack()
    database.close()

    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_reschedule_success_persistence
            BEFORE UPDATE OF status ON external_action_operations
            WHEN NEW.action_type = 'calendar.reschedule'
              AND NEW.status = 'succeeded'
            BEGIN
                SELECT RAISE(ABORT, 'simulated terminal persistence failure');
            END
            """
        )
        connection.commit()

    from datetime import datetime, timezone

    target_start = datetime.fromisoformat(START).astimezone(timezone.utc)
    target_end = datetime.fromisoformat(END).astimezone(timezone.utc)
    operation_id = CandidateScheduleActionWSGIApp._operation_id(
        OWNER, EVENT_ID, 1, target_start, target_end, ZONE
    )

    first = Candidate(path)
    second = None
    server = None
    thread = None
    second_server = None
    second_thread = None

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            server, thread = _serve(first)
            first_page = browser.new_page()
            first_page.goto(f"http://127.0.0.1:{server.server_port}/sandbox/candidate")
            first_page.locator("#token").fill("owner-token")
            first_page.locator("#list-events").click()
            first_page.get_by_role(
                "button", name=f"View event 1: {EVENT_ID}"
            ).wait_for()
            first_page.get_by_role(
                "button", name=f"View event 1: {EVENT_ID}"
            ).click()
            first_page.locator("#event-detail").get_by_text(
                '"state_version": 1'
            ).wait_for()

            first_page.locator("#reschedule-start").fill(START)
            first_page.locator("#reschedule-end").fill(END)
            first_page.locator("#reschedule-timezone").fill(ZONE)
            first_page.locator("#request-reschedule").click()
            expect(
                first_page.locator("#reschedule-reconciliation-state")
            ).to_contain_text("Provider outcome is uncertain")

            after_provider_commit = _durable_snapshot(path, operation_id)
            assert after_provider_commit == {
                "event": ("reschedule_requested", 2, None),
                "operation": ("reconciliation_required",),
                "attempts": [(1, "success")],
                "effect": ("reschedule", 0),
            }

            # The provider effect and event transition are durable. Remove only
            # the injected terminal-write fault before evidence-only recovery.
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "DROP TRIGGER fail_reschedule_success_persistence"
                )
                connection.commit()

            first_page.close()
            _stop(server, thread)
            server = None
            thread = None
            first.close()
            first = None

            second = Candidate(path)
            second_server, second_thread = _serve(second)
            page = browser.new_page()
            page.goto(
                f"http://127.0.0.1:{second_server.server_port}/sandbox/candidate"
            )
            page.locator("#token").fill("owner-token")
            page.locator("#list-pending-reschedules").click()

            review = page.get_by_role(
                "button",
                name=f"Review pending reschedule for event {EVENT_ID}",
            )
            review.wait_for()
            pending_items = page.locator("#pending-reschedule-items")
            assert pending_items.get_by_role("button").count() == 1
            assert page.locator("#next-pending-reschedules").is_disabled()
            review.click()

            expect(
                page.locator("#reschedule-reconciliation-state")
            ).to_contain_text(
                "An ambiguous reschedule was restored from durable storage"
            )
            assert page.locator("#reschedule-start").input_value() == PENDING_START
            assert page.locator("#reschedule-end").input_value() == PENDING_END
            assert page.locator("#reschedule-timezone").input_value() == ZONE
            assert not page.locator(
                "#reschedule-reconciliation-verified"
            ).is_checked()
            assert page.locator(
                "#confirm-reschedule-completed"
            ).is_disabled()
            assert page.locator(
                "#confirm-reschedule-no-effect"
            ).is_disabled()

            # Discovery, event detail, and pending restoration are read-only.
            assert _durable_snapshot(path, operation_id) == after_provider_commit

            page.locator("#reschedule-reconciliation-verified").check()
            page.locator("#confirm-reschedule-completed").click()
            expect(page.locator("#reschedule-state")).to_contain_text(
                "Provider evidence confirmed the sandbox reschedule"
            )
            expect(page.locator("#event-detail")).to_contain_text(
                '"status": "reschedule_requested"'
            )
            expect(page.locator("#event-detail")).to_contain_text(
                '"state_version": 2'
            )

            assert _durable_snapshot(path, operation_id) == {
                "event": ("reschedule_requested", 2, None),
                "operation": ("succeeded",),
                "attempts": [(1, "success")],
                "effect": ("reschedule", 0),
            }
        finally:
            if server is not None:
                _stop(server, thread)
            if second_server is not None:
                _stop(second_server, second_thread)
            if first is not None:
                first.close()
            if second is not None:
                second.close()
            browser.close()

def test_stale_pointerless_reschedule_attestation_is_rejected_after_same_target_version_advance(
    tmp_path,
):
    """A newer same-slot approval must fence an older recovered attestation."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from playwright.sync_api import expect
    from datetime import datetime, timezone

    path = tmp_path / "candidate-stale-reschedule-attestation.sqlite"
    _seed(path, _event())

    bootstrap = CandidateScheduleActionWSGIApp(
        database_path=str(path),
        resolve_bearer=TOKENS.get,
    )
    database, _, _, _ = bootstrap._open_stack()
    database.close()

    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_reschedule_success_persistence
            BEFORE UPDATE OF status ON external_action_operations
            WHEN NEW.action_type = 'calendar.reschedule'
              AND NEW.status = 'succeeded'
            BEGIN
                SELECT RAISE(ABORT, 'simulated terminal persistence failure');
            END
            """
        )
        connection.commit()

    target_start = datetime.fromisoformat(START).astimezone(timezone.utc)
    target_end = datetime.fromisoformat(END).astimezone(timezone.utc)
    original_operation_id = CandidateScheduleActionWSGIApp._operation_id(
        OWNER, EVENT_ID, 1, target_start, target_end, ZONE
    )
    competing_operation_id = CandidateScheduleActionWSGIApp._operation_id(
        OWNER, EVENT_ID, 2, target_start, target_end, ZONE
    )

    first = Candidate(path)
    second = None
    competitor = None
    server = thread = second_server = second_thread = None

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            server, thread = _serve(first)
            first_page = browser.new_page()
            first_page.goto(f"http://127.0.0.1:{server.server_port}/sandbox/candidate")
            first_page.locator("#token").fill("owner-token")
            first_page.locator("#list-events").click()
            first_page.get_by_role(
                "button", name=f"View event 1: {EVENT_ID}"
            ).wait_for()
            first_page.get_by_role(
                "button", name=f"View event 1: {EVENT_ID}"
            ).click()
            first_page.locator("#event-detail").get_by_text(
                '"state_version": 1'
            ).wait_for()
            first_page.locator("#reschedule-start").fill(START)
            first_page.locator("#reschedule-end").fill(END)
            first_page.locator("#reschedule-timezone").fill(ZONE)
            first_page.locator("#request-reschedule").click()
            expect(
                first_page.locator("#reschedule-reconciliation-state")
            ).to_contain_text("Provider outcome is uncertain")

            assert _durable_snapshot(path, original_operation_id) == {
                "event": ("reschedule_requested", 2, None),
                "operation": ("reconciliation_required",),
                "attempts": [(1, "success")],
                "effect": ("reschedule", 0),
            }

            with sqlite3.connect(path) as connection:
                connection.execute(
                    "DROP TRIGGER fail_reschedule_success_persistence"
                )
                connection.commit()

            first_page.close()
            _stop(server, thread)
            server = thread = None
            first.close()
            first = None

            second = Candidate(path)
            second_server, second_thread = _serve(second)
            page = browser.new_page()
            page.goto(
                f"http://127.0.0.1:{second_server.server_port}/sandbox/candidate"
            )
            page.locator("#token").fill("owner-token")
            page.locator("#list-pending-reschedules").click()
            review = page.get_by_role(
                "button",
                name=f"Review pending reschedule for event {EVENT_ID}",
            )
            review.wait_for()
            review.click()
            expect(
                page.locator("#reschedule-reconciliation-state")
            ).to_contain_text(
                "An ambiguous reschedule was restored from durable storage"
            )
            assert not page.locator(
                "#reschedule-reconciliation-verified"
            ).is_checked()

            # A second, explicitly approved reschedule reuses the same target
            # but advances the event to version 3. The first operation's
            # browser snapshot and attestation remain bound to source version 1.
            competitor = Candidate(path)
            status, result = competitor.reschedule(
                {
                    "schema_version": 1,
                    "event_id": EVENT_ID,
                    "state_version": 2,
                    "start_at": START,
                    "end_at": END,
                    "timezone_name": ZONE,
                    "decision": "approve",
                }
            )
            assert status == "200 OK"
            assert result["outcome"] == "completed"
            assert result["event"]["state_version"] == 3
            competitor.close()
            competitor = None

            original_before_stale_submit = _durable_snapshot(
                path, original_operation_id
            )
            competing_before_stale_submit = _durable_snapshot(
                path, competing_operation_id
            )
            assert original_before_stale_submit == {
                "event": ("reschedule_requested", 3, None),
                "operation": ("reconciliation_required",),
                "attempts": [(1, "success")],
                "effect": ("reschedule", 0),
            }
            assert competing_before_stale_submit == {
                "event": ("reschedule_requested", 3, None),
                "operation": ("succeeded",),
                "attempts": [(1, "success")],
                "effect": ("reschedule", 0),
            }

            def ledger_totals():
                connection = sqlite3.connect(path)
                try:
                    attempts = connection.execute(
                        "SELECT COUNT(*) FROM sandbox_calendar_attempts"
                    ).fetchone()[0]
                    effects = connection.execute(
                        "SELECT COUNT(*) FROM sandbox_calendar_effects"
                    ).fetchone()[0]
                    replays = connection.execute(
                        "SELECT COALESCE(SUM(replays), 0) FROM sandbox_calendar_effects"
                    ).fetchone()[0]
                    return attempts, effects, replays
                finally:
                    connection.close()

            before_stale_submit = ledger_totals()
            assert before_stale_submit == (2, 2, 0)

            page.locator("#reschedule-reconciliation-verified").check()
            page.locator("#confirm-reschedule-completed").click()
            expect(
                page.locator("#reschedule-reconciliation-state")
            ).to_contain_text(
                "Provider evidence did not confirm the reported outcome"
            )
            assert not page.locator(
                "#reschedule-reconciliation-verified"
            ).is_checked()
            assert page.locator("#confirm-reschedule-completed").is_disabled()
            assert page.locator("#confirm-reschedule-no-effect").is_disabled()

            # The stale attestation cannot resolve the old operation or trigger
            # another provider call; both durable operations and effects persist.
            assert _durable_snapshot(path, original_operation_id) == (
                original_before_stale_submit
            )
            assert _durable_snapshot(path, competing_operation_id) == (
                competing_before_stale_submit
            )
            assert ledger_totals() == before_stale_submit
        finally:
            if server is not None:
                _stop(server, thread)
            if second_server is not None:
                _stop(second_server, second_thread)
            if first is not None:
                first.close()
            if second is not None:
                second.close()
            if competitor is not None:
                competitor.close()
            browser.close()

