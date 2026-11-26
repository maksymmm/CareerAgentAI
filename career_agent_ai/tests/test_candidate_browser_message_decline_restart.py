from __future__ import annotations

from datetime import datetime, timezone
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
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


def _communication_snapshot(path):
    database = SQLiteDatabase.open_read_only(str(path))
    try:
        connection = database.connection
        return {
            "attempts": connection.execute(
                "SELECT operation_id, attempt_no, outcome "
                "FROM sandbox_communication_attempts ORDER BY operation_id, attempt_no"
            ).fetchall(),
            "intents": connection.execute(
                "SELECT operation_id, fingerprint "
                "FROM sandbox_communication_intents ORDER BY operation_id"
            ).fetchall(),
            "deliveries": connection.execute(
                "SELECT operation_id, fingerprint, message_id, action, replays "
                "FROM sandbox_communication_deliveries ORDER BY operation_id"
            ).fetchall(),
            "messages": connection.execute(
                "SELECT message_id, sender, recipient, subject, body, direction "
                "FROM communication_messages ORDER BY message_id"
            ).fetchall(),
            "provider_messages": connection.execute(
                "SELECT message_id, direction "
                "FROM sandbox_communication_messages ORDER BY message_id"
            ).fetchall(),
        }
    finally:
        database.close()


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


def test_candidate_browser_message_decline_stays_terminal_after_runtime_restart(
    tmp_path,
):
    """Declining the message gate never sends it and remains terminal after restart."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from playwright.sync_api import expect

    database_path = tmp_path / "candidate-browser-message-decline.sqlite"
    message = {
        "sender": "candidate@example.test",
        "recipient": "recruiter@example.test",
        "subject": "Logistics coordinator application",
        "body": "I would welcome a conversation about the logistics coordinator role.",
    }
    job = Job.create(
        job_id="browser-message-decline-job",
        title="Logistics Coordinator",
        company=Company("browser-message-decline-company", "Sandbox Logistics"),
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
        ),
        run_id="candidate-browser-message-decline",
    )
    assert started.phase is CareerLoopPhase.APPLICATION_APPROVAL
    run_id = started.run_id
    message_id = f"{run_id}:message"

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
                expect(page.locator("#decline")).to_be_enabled()
                for expected in message.values():
                    expect(page.locator("#action-details")).to_contain_text(expected)

                before_decline = _communication_snapshot(database_path)
                assert before_decline["attempts"] == []
                assert before_decline["intents"] == []
                assert before_decline["deliveries"] == []
                assert before_decline["messages"] == [
                    (
                        message_id,
                        message["sender"],
                        message["recipient"],
                        message["subject"],
                        message["body"],
                        "draft",
                    )
                ]
                assert before_decline["provider_messages"] == [
                    (message_id, "draft")
                ]

                # Declining the exact message approval must not invoke its provider.
                page.locator("#decline").click()
                expect(page.locator("#result")).to_contain_text('"phase": "failed"')
                page.locator("#status").click()
                expect(page.locator("#run-overview")).to_contain_text("Finished")
                expect(page.locator("#run-overview")).to_contain_text("failed")
                assert _communication_snapshot(database_path) == before_decline
                page.locator("#clear-session").click()
            finally:
                _stop(first_server, first_thread)
                first_runtime.close()

            # A fresh runtime and browser session must preserve the terminal decline.
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
                restored_page.locator("#clear-session").click()
            finally:
                _stop(second_server, second_thread)
                second_runtime.close()
        finally:
            browser.close()

    completed = _communication_snapshot(database_path)
    assert completed == before_decline
    assert completed["attempts"] == []
    assert completed["intents"] == []
    assert completed["deliveries"] == []
    assert completed["messages"] == [
        (
            message_id,
            message["sender"],
            message["recipient"],
            message["subject"],
            message["body"],
            "draft",
        )
    ]
    assert completed["provider_messages"] == [(message_id, "draft")]


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
        "if: ${{ always() && !cancelled() && steps.pytest.outcome != 'skipped' }}"
        in test_job
    )
    assert test_job.index(pytest_command) < test_job.index(coverage_command)
