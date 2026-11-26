from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from threading import Thread
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

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


NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


def _runtime(database_path, job):
    return RuntimeCandidateApp(
        database_path=str(database_path),
        jobs=(job,),
        resolve_bearer=lambda token: {
            "owner": "candidate-1",
            "foreign": "candidate-2",
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


def _calendar_snapshot(path, event_id):
    database = SQLiteDatabase.open_read_only(str(path))
    try:
        connection = database.connection
        return {
            "attempts": connection.execute(
                "SELECT operation_id, attempt_no, outcome "
                "FROM sandbox_calendar_attempts ORDER BY operation_id, attempt_no"
            ).fetchall(),
            "intents": connection.execute(
                "SELECT operation_id, fingerprint "
                "FROM sandbox_calendar_intents ORDER BY operation_id"
            ).fetchall(),
            "effects": connection.execute(
                "SELECT operation_id, fingerprint, action, event_json, replays "
                "FROM sandbox_calendar_effects ORDER BY operation_id"
            ).fetchall(),
            "event": connection.execute(
                "SELECT event_id, candidate_id, status, provider_event_id, version, "
                "active_action_operation_id, reservation_start_epoch_us, "
                "reservation_end_epoch_us FROM scheduling_events WHERE event_id = ?",
                (event_id,),
            ).fetchone(),
            "application_effects": connection.execute(
                "SELECT COUNT(*) FROM sandbox_submission_ledger"
            ).fetchone()[0],
            "message_deliveries": connection.execute(
                "SELECT COUNT(*) FROM sandbox_communication_deliveries"
            ).fetchone()[0],
        }
    finally:
        database.close()


def _owner_status(runtime, run_id, token):
    statuses = []
    response = runtime(
        {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": f"/v1/candidate/runs/{run_id}/status",
            "HTTP_AUTHORIZATION": f"Bearer {token}",
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": "0",
            "wsgi.input": BytesIO(b""),
        },
        lambda status, headers: statuses.append(status),
    )
    assert len(statuses) == 1
    return statuses[0], b"".join(response)


def test_candidate_browser_interview_decline_stays_terminal_after_runtime_restart(
    tmp_path,
):
    """A browser interview decline survives restart without any calendar provider effect."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from playwright.sync_api import expect

    database_path = tmp_path / "candidate-browser-interview-decline.sqlite"
    event_id = "browser-interview-decline-event"
    message = {
        "sender": "candidate@example.test",
        "recipient": "recruiter@example.test",
        "subject": "Logistics coordinator application",
        "body": "I would welcome a conversation about the logistics coordinator role.",
    }
    job = Job.create(
        job_id="browser-interview-decline-job",
        title="Logistics Coordinator",
        company=Company("browser-interview-decline-company", "Sandbox Logistics"),
        location=Location("Germany", "Karlsruhe"),
        description="Warehouse logistics coordination and inventory control.",
        created_at=NOW,
    )
    first_runtime = _runtime(database_path, job)
    started = first_runtime.start(
        CareerLoopRequest(
            user_id="candidate-1",
            keyword="logistics",
            candidate_profile=(
                "Experienced logistics coordinator with warehouse, inventory, "
                "and daily operations experience."
            ),
            location="Karlsruhe",
            sender=message["sender"],
            recipient=message["recipient"],
            message_subject=message["subject"],
            message_body=message["body"],
            schedule_event_id=event_id,
        ),
        run_id="candidate-browser-interview-decline",
    )
    assert started.phase is CareerLoopPhase.APPLICATION_APPROVAL
    run_id = started.run_id

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

    before_decline = None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            first_server, first_thread = _serve(first_runtime)
            try:
                page = browser.new_page()
                page.goto(
                    f"http://127.0.0.1:{first_server.server_port}/sandbox/candidate"
                )
                page.locator("#token").fill("owner")
                page.locator("#run-id").fill(run_id)

                page.locator("#status").click()
                expect(page.locator("#run-overview")).to_contain_text(
                    "application approval"
                )
                page.locator("#prompt").click()
                expect(page.locator("#approve")).to_be_enabled()
                page.locator("#approve").click()
                expect(page.locator("#result")).to_contain_text("message_approval")

                page.locator("#status").click()
                expect(page.locator("#run-overview")).to_contain_text(
                    "message approval"
                )
                page.locator("#prompt").click()
                expect(page.locator("#approve")).to_be_enabled()
                for expected in message.values():
                    expect(page.locator("#action-details")).to_contain_text(expected)
                page.locator("#approve").click()
                expect(page.locator("#result")).to_contain_text("interview_approval")

                page.locator("#status").click()
                expect(page.locator("#run-overview")).to_contain_text(
                    "interview approval"
                )
                page.locator("#prompt").click()
                expect(page.locator("#decline")).to_be_enabled()
                expect(page.locator("#action-details")).to_contain_text(event_id)
                expect(page.locator("#action-details")).to_contain_text(
                    "Sandbox Logistics"
                )

                before_decline = _calendar_snapshot(database_path, event_id)
                assert before_decline["attempts"] == []
                assert before_decline["intents"] == []
                assert before_decline["effects"] == []
                assert before_decline["event"] == (
                    event_id,
                    "candidate-1",
                    "proposed",
                    None,
                    1,
                    None,
                    None,
                    None,
                )
                assert before_decline["application_effects"] == 1
                assert before_decline["message_deliveries"] == 1

                # Declining the exact interview prompt must not call the calendar provider.
                page.locator("#decline").click()
                expect(page.locator("#result")).to_contain_text('"phase": "failed"')
                page.locator("#status").click()
                expect(page.locator("#run-overview")).to_contain_text("Finished")
                expect(page.locator("#run-overview")).to_contain_text("failed")
                assert _calendar_snapshot(database_path, event_id) == before_decline
                assert _owner_status(first_runtime, run_id, "foreign")[0] == "404 Not Found"
                page.locator("#clear-session").click()
            finally:
                _stop(first_server, first_thread)
                first_runtime.close()

            # A fresh runtime and browser session must preserve the terminal interview decline.
            second_runtime = _runtime(database_path, job)
            second_server, second_thread = _serve(second_runtime)
            try:
                restored_page = browser.new_page()
                restored_page.goto(
                    f"http://127.0.0.1:{second_server.server_port}/sandbox/candidate"
                )
                restored_page.locator("#token").fill("owner")
                restored_page.locator("#run-id").fill(run_id)
                restored_page.locator("#status").click()
                expect(restored_page.locator("#run-overview")).to_contain_text(
                    "Finished"
                )
                expect(restored_page.locator("#run-overview")).to_contain_text(
                    "failed"
                )

                restored_page.locator("#continue").click()
                expect(restored_page.locator("#result")).to_contain_text(
                    '"phase": "failed"'
                )
                restored_page.locator("#status").click()
                expect(restored_page.locator("#run-overview")).to_contain_text(
                    "Finished"
                )
                expect(restored_page.locator("#run-overview")).to_contain_text(
                    "failed"
                )

                restored_page.locator("#list-events").click()
                expect(restored_page.locator("#owned-events")).to_contain_text(
                    "Sandbox Logistics — interview — proposed"
                )
                restored_page.locator("#owned-events button").first.click()
                expect(restored_page.locator("#event-detail")).to_contain_text(
                    '"status": "proposed"'
                )
                expect(restored_page.locator("#event-detail")).to_contain_text(
                    event_id
                )
                assert _calendar_snapshot(database_path, event_id) == before_decline
                assert _owner_status(second_runtime, run_id, "foreign")[0] == "404 Not Found"
                restored_page.locator("#clear-session").click()
            finally:
                _stop(second_server, second_thread)
                second_runtime.close()
        finally:
            browser.close()

    completed = _calendar_snapshot(database_path, event_id)
    assert completed == before_decline
    assert completed["attempts"] == []
    assert completed["intents"] == []
    assert completed["effects"] == []
    assert completed["event"][2:5] == ("proposed", None, 1)
    assert completed["application_effects"] == 1
    assert completed["message_deliveries"] == 1


def test_candidate_ci_enforces_independent_coverage_gate_after_pytest():
    from pathlib import Path

    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")
    test_job = workflow.split("  release-evidence:", 1)[0]
    pytest_command = "python -m pytest"
    coverage_command = "python -m coverage report --fail-under=90"

    assert pytest_command in test_job
    assert coverage_command in test_job
    assert "id: pytest" in test_job
    assert (
        "if: $" + "{{ always() && !cancelled() && steps.pytest.outcome != 'skipped' }}"
        in test_job
    )
    assert test_job.index(pytest_command) < test_job.index(coverage_command)


def test_candidate_ci_preserves_the_full_pytest_coverage_threshold():
    from pathlib import Path

    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")
    assert "--cov=career_agent_ai" in workflow
    assert "--cov-report=term-missing" in workflow
    assert "--cov-fail-under=90" in workflow
