from __future__ import annotations

import json
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


def test_candidate_browser_requires_separate_application_and_message_approval_before_delivery(
    tmp_path,
):
    """Exercise the real candidate browser and SQLite providers through both approval gates."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from playwright.sync_api import expect

    database_path = tmp_path / "candidate-browser-message-approval.sqlite"
    message = {
        "sender": "candidate@example.test",
        "recipient": "recruiter@example.test",
        "subject": "Logistics coordinator application",
        "body": "I would welcome a conversation about the logistics coordinator role.",
    }
    job = Job.create(
        job_id="browser-message-job",
        title="Logistics Coordinator",
        company=Company("browser-message-company", "Sandbox Logistics"),
        location=Location("Germany", "Karlsruhe"),
        description="Warehouse logistics coordination and inventory control.",
        created_at=NOW,
    )
    runtime = RuntimeCandidateApp(
        database_path=str(database_path),
        jobs=(job,),
        resolve_bearer=lambda token: {
            "owner": "candidate-1",
            "foreign": "candidate-2",
        }.get(token),
    )
    started = runtime.start(
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
        run_id="candidate-browser-message-approval",
    )
    assert started.phase is CareerLoopPhase.APPLICATION_APPROVAL
    run_id = started.run_id

    server = make_server("127.0.0.1", 0, runtime, handler_class=QuietHandler)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    browser = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/sandbox/candidate")

            page.locator("#token").fill("owner")
            page.locator("#run-id").fill(run_id)
            page.locator("#status").click()
            expect(page.locator("#run-overview")).to_contain_text(
                "application approval"
            )
            page.locator("#prompt").click()
            expect(page.locator("#approve")).to_be_enabled()
            expect(page.locator("#action-details")).to_contain_text(
                "Logistics Coordinator"
            )

            before_application_approval = _communication_snapshot(database_path)
            assert before_application_approval["attempts"] == []
            assert before_application_approval["intents"] == []
            assert before_application_approval["deliveries"] == []
            assert before_application_approval["messages"] == []
            assert before_application_approval["provider_messages"] == []

            # Application approval creates only the immutable message draft.
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

            before_message_approval = _communication_snapshot(database_path)
            assert before_message_approval["attempts"] == []
            assert before_message_approval["intents"] == []
            assert before_message_approval["deliveries"] == []
            assert before_message_approval["messages"] == [
                (
                    f"{run_id}:message",
                    message["sender"],
                    message["recipient"],
                    message["subject"],
                    message["body"],
                    "draft",
                )
            ]
            assert before_message_approval["provider_messages"] == [
                (f"{run_id}:message", "draft")
            ]

            # Only the second, exact message approval may invoke the sandbox provider.
            page.locator("#approve").click()
            expect(page.locator("#result")).to_contain_text('"phase": "complete"')
            page.locator("#status").click()
            expect(page.locator("#run-overview")).to_contain_text("Finished")
            expect(page.locator("#run-overview")).to_contain_text("complete")

            page.locator("#clear-session").click()
            expect(page.locator("#token")).to_have_value("")
            expect(page.locator("#run-id")).to_have_value("")
    finally:
        if browser is not None:
            browser.close()
        server.shutdown()
        server_thread.join(timeout=5)
        assert not server_thread.is_alive()
        server.server_close()
        runtime.close()

    completed = _communication_snapshot(database_path)
    operation_id = f"{run_id}:message-send"
    message_id = f"{run_id}:message"
    assert completed["attempts"] == [(operation_id, 1, "success")]
    assert len(completed["intents"]) == 1
    assert completed["intents"][0][0] == operation_id
    assert len(completed["deliveries"]) == 1
    assert completed["deliveries"][0][0] == operation_id
    assert completed["deliveries"][0][1] == completed["intents"][0][1]
    assert completed["deliveries"][0][2:] == (message_id, "send", 0)
    assert completed["messages"] == [
        (
            message_id,
            message["sender"],
            message["recipient"],
            message["subject"],
            message["body"],
            "outbound",
        )
    ]
    assert completed["provider_messages"] == [(message_id, "outbound")]
