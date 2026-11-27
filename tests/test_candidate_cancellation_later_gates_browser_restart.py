from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from threading import Thread
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


def _serve(runtime):
    server = make_server("127.0.0.1", 0, runtime, handler_class=QuietHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop(server, thread):
    if server is None:
        return
    server.shutdown()
    if thread is not None:
        thread.join(timeout=5)
        assert not thread.is_alive()
    server.server_close()


def _workflow_effect_snapshot(database_path, run_id, event_id=None):
    from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase

    tables = (
        "external_action_operations",
        "sandbox_submission_intents",
        "sandbox_submission_attempts",
        "sandbox_submission_ledger",
        "sandbox_communication_intents",
        "sandbox_communication_attempts",
        "sandbox_communication_deliveries",
        "sandbox_communication_messages",
        "sandbox_calendar_intents",
        "sandbox_calendar_attempts",
        "sandbox_calendar_effects",
    )
    database = SQLiteDatabase.open_read_only(str(database_path))
    try:
        connection = database.connection
        existing = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        counts = {
            table: (
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if table in existing
                else 0
            )
            for table in tables
        }
        messages = (
            tuple(
                connection.execute(
                    "SELECT message_id, direction FROM communication_messages "
                    "WHERE message_id = ?",
                    (f"{run_id}:message",),
                ).fetchall()
            )
            if "communication_messages" in existing
            else ()
        )
        event = None
        if event_id is not None and "scheduling_events" in existing:
            event = connection.execute(
                "SELECT event_id, candidate_id, status, provider_event_id, version, "
                "active_action_operation_id, reservation_start_epoch_us, "
                "reservation_end_epoch_us FROM scheduling_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return {"counts": counts, "messages": messages, "event": event}
    finally:
        database.close()


@pytest.mark.parametrize(
    ("gate", "expected_message_direction", "expected_communication_effects"),
    [
        ("message", "draft", 0),
        ("interview", "outbound", 1),
    ],
)
def test_candidate_cancellation_at_later_approval_gates_preserves_prior_effects_after_restart(
    tmp_path, gate, expected_message_direction, expected_communication_effects
):
    """The browser cancels later gates without compensating earlier sandbox effects."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from playwright.sync_api import expect

    from career_agent_ai.application.career.autonomous_loop_models import (
        CareerLoopPhase,
        CareerLoopRequest,
    )
    from career_agent_ai.application.jobs.company import Company
    from career_agent_ai.application.jobs.job import Job
    from career_agent_ai.application.jobs.location import Location
    from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
    from career_agent_ai.application.scheduling.models import (
        ScheduleEvent,
        ScheduleEventType,
    )
    from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
    from career_agent_ai.application.storage.sqlite_scheduling_repository import (
        SQLiteSchedulingRepository,
    )

    database_path = tmp_path / f"candidate-cancel-{gate}-browser-restart.sqlite"
    run_id = f"candidate-cancel-{gate}-browser"
    event_id = f"{run_id}:interview" if gate == "interview" else None
    job = Job.create(
        job_id=f"{run_id}-job",
        title="Logistics Coordinator",
        company=Company(f"{run_id}-company", "Sandbox Logistics"),
        location=Location("Germany", "Karlsruhe"),
        description="Warehouse logistics coordination and inventory control.",
        created_at=NOW,
    )
    request = CareerLoopRequest(
        user_id="candidate-1",
        keyword="logistics",
        candidate_profile=(
            "Experienced logistics coordinator with warehouse, inventory, "
            "and daily operations experience."
        ),
        location="Karlsruhe",
        sender="candidate@example.test",
        recipient="recruiter@example.test",
        message_subject="Logistics coordinator application",
        message_body="I would welcome a conversation about the logistics coordinator role.",
        schedule_event_id=event_id,
    )

    def build_runtime():
        return RuntimeCandidateApp(
            database_path=str(database_path),
            jobs=(job,),
            resolve_bearer=lambda token: {
                "owner": "candidate-1",
                "foreign": "candidate-2",
            }.get(token),
        )

    runtime1 = build_runtime()
    started = runtime1.start(request, run_id=run_id)
    assert started.phase is CareerLoopPhase.APPLICATION_APPROVAL
    run_id = started.run_id

    if event_id is not None:
        database = SQLiteDatabase(str(database_path))
        try:
            SQLiteSchedulingRepository(database).create(
                ScheduleEvent(
                    event_id=event_id,
                    candidate_id="candidate-1",
                    employer_name="Sandbox Logistics",
                    event_type=ScheduleEventType.INTERVIEW,
                    location="Karlsruhe",
                    start_at=NOW + timedelta(days=2),
                    end_at=NOW + timedelta(days=2, hours=1),
                    timezone_name="Europe/Berlin",
                    application_id=f"{run_id}:application",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        finally:
            database.close()

    server1, thread1 = _serve(runtime1)
    runtime2 = None
    server2 = None
    thread2 = None
    server1_stopped = False
    first_snapshot = None
    effects_before_cancel = None

    try:
        with sync_playwright() as playwright:
            browser1 = playwright.chromium.launch(headless=True)
            try:
                page1 = browser1.new_page()
                page1.goto(
                    f"http://127.0.0.1:{server1.server_port}/sandbox/candidate"
                )
                page1.locator("#token").fill("owner")
                page1.locator("#list-runs").click()
                page1.locator("#owned-runs button").first.wait_for()
                page1.locator("#owned-runs button").first.click()
                assert page1.locator("#run-id").input_value() == run_id

                page1.locator("#status").click()
                expect(page1.locator("#run-overview")).to_contain_text(
                    "application approval"
                )
                page1.locator("#prompt").click()
                expect(page1.locator("#approve")).to_be_enabled()
                page1.locator("#approve").click()
                expect(page1.locator("#result")).to_contain_text("message_approval")

                # A decision mutation invalidates the old status snapshot. The
                # cancellation control must stay disabled until a fresh status read.
                expect(page1.locator("#cancel-run")).to_be_disabled()

                if gate == "interview":
                    page1.locator("#status").click()
                    expect(page1.locator("#run-overview")).to_contain_text(
                        "message approval"
                    )
                    page1.locator("#prompt").click()
                    expect(page1.locator("#approve")).to_be_enabled()
                    page1.locator("#approve").click()
                    expect(page1.locator("#result")).to_contain_text(
                        "interview_approval"
                    )
                    expect(page1.locator("#cancel-run")).to_be_disabled()

                page1.locator("#status").click()
                expect(page1.locator("#run-overview")).to_contain_text(
                    f"{gate} approval"
                )
                expect(page1.locator("#cancel-run")).to_be_enabled()

                effects_before_cancel = _workflow_effect_snapshot(
                    database_path, run_id, event_id
                )
                counts = effects_before_cancel["counts"]
                assert counts["sandbox_submission_attempts"] == 1
                assert counts["sandbox_submission_ledger"] == 1
                assert counts["sandbox_communication_attempts"] == (
                    expected_communication_effects
                )
                assert counts["sandbox_communication_deliveries"] == (
                    expected_communication_effects
                )
                assert counts["sandbox_calendar_attempts"] == 0
                assert counts["sandbox_calendar_effects"] == 0
                assert counts["external_action_operations"] == (
                    1 + expected_communication_effects
                )
                assert effects_before_cancel["messages"] == (
                    (f"{run_id}:message", expected_message_direction),
                )
                if event_id is not None:
                    assert effects_before_cancel["event"] is not None
                    assert effects_before_cancel["event"][2:6] == (
                        "proposed",
                        None,
                        1,
                        None,
                    )

                # Cancel from the fresh message/interview approval snapshot.
                page1.locator("#cancel-run").click()
                expect(page1.locator("#run-overview")).to_contain_text(
                    "Cancelled — refresh status"
                )
                expect(page1.locator("#cancel-run")).to_be_disabled()
                page1.locator("#status").click()
                expect(page1.locator("#run-overview")).to_contain_text(
                    "Cancelled — cancelled"
                )
                first_snapshot = json.loads(
                    page1.locator("#result").text_content()
                )["payload"]
                assert first_snapshot["phase"] == "cancelled"
                assert first_snapshot["terminal"] is True
                assert first_snapshot["waiting_for_human"] is False
                assert _workflow_effect_snapshot(
                    database_path, run_id, event_id
                ) == effects_before_cancel
            finally:
                browser1.close()

        _stop(server1, thread1)
        server1_stopped = True
        runtime1.close()

        # Reconstruct the complete sandbox composition over the same durable DB.
        runtime2 = build_runtime()
        server2, thread2 = _serve(runtime2)
        with sync_playwright() as playwright:
            browser2 = playwright.chromium.launch(headless=True)
            try:
                page2 = browser2.new_page()
                page2.goto(
                    f"http://127.0.0.1:{server2.server_port}/sandbox/candidate"
                )
                page2.locator("#token").fill("owner")
                page2.locator("#list-runs").click()
                page2.locator("#owned-runs button").first.wait_for()
                page2.locator("#owned-runs button").first.click()
                assert page2.locator("#run-id").input_value() == run_id
                page2.locator("#status").click()
                expect(page2.locator("#run-overview")).to_contain_text(
                    "Cancelled — cancelled"
                )
                expect(page2.locator("#cancel-run")).to_be_disabled()

                continued = page2.evaluate(
                    """async (runId) => {
                      const response = await fetch(
                        '/v1/candidate/runs/' + encodeURIComponent(runId) + '/continue',
                        {method: 'POST', headers: {Authorization: 'Bearer owner'}}
                      );
                      return {status: response.status, body: await response.json()};
                    }""",
                    run_id,
                )
                assert continued == {
                    "status": 200,
                    "body": {"run_id": run_id, "phase": "cancelled"},
                }

                page2.locator("#status").click()
                expect(page2.locator("#run-overview")).to_contain_text(
                    "Cancelled — cancelled"
                )
                restored_snapshot = json.loads(
                    page2.locator("#result").text_content()
                )["payload"]
                assert restored_snapshot == first_snapshot
                assert _workflow_effect_snapshot(
                    database_path, run_id, event_id
                ) == effects_before_cancel
            finally:
                browser2.close()
    finally:
        if not server1_stopped:
            _stop(server1, thread1)
            runtime1.close()
        if server2 is not None:
            _stop(server2, thread2)
        if runtime2 is not None:
            runtime2.close()
