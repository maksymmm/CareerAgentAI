from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Thread
from wsgiref.simple_server import WSGIRequestHandler, make_server

import career_agent_ai.application.runtime.composition as runtime_composition
import pytest

from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopPhase,
    CareerLoopRequest,
)
from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
from career_agent_ai.application.scheduling.models import ScheduleEvent, ScheduleEventType
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_sandbox_communication_provider import (
    SandboxCommunicationOutcome,
)
from career_agent_ai.application.storage.sqlite_sandbox_submission_provider import (
    SandboxOutcome,
)
from career_agent_ai.application.storage.sqlite_scheduling_repository import (
    SQLiteSchedulingRepository,
)


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


def _submission_snapshot(path):
    database = SQLiteDatabase.open_read_only(str(path))
    try:
        connection = database.connection
        return {
            "attempts": connection.execute(
                "SELECT idempotency_key, attempt_no, outcome "
                "FROM sandbox_submission_attempts ORDER BY idempotency_key, attempt_no"
            ).fetchall(),
            "ledger": connection.execute(
                "SELECT COUNT(*), MIN(replays), MAX(replays) "
                "FROM sandbox_submission_ledger"
            ).fetchone(),
        }
    finally:
        database.close()


def _start_server(runtime):
    server = make_server("127.0.0.1", 0, runtime, handler_class=QuietHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop_server(server, thread, runtime):
    server.shutdown()
    thread.join(timeout=5)
    assert not thread.is_alive()
    server.server_close()
    runtime.close()


def test_candidate_browser_recovers_confirmed_message_no_effect_after_runtime_restart(
    tmp_path, monkeypatch
):
    """The browser resumes one approved message intent after durable provider absence."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from playwright.sync_api import expect

    database_path = tmp_path / "candidate-browser-message-no-effect-restart.sqlite"
    message = {
        "sender": "candidate@example.test",
        "recipient": "recruiter@example.test",
        "subject": "Logistics coordinator application",
        "body": "I would welcome a conversation about the logistics coordinator role.",
    }
    run_id = "candidate-browser-message-no-effect-restart"
    event_id = "candidate-browser-message-no-effect-event"
    job = Job.create(
        job_id="browser-message-no-effect-job",
        title="Logistics Coordinator",
        company=Company("browser-message-no-effect-company", "Sandbox Logistics"),
        location=Location("Germany", "Karlsruhe"),
        description="Warehouse logistics coordination and inventory control.",
        created_at=NOW,
    )

    application_attempts = []
    communication_attempts = []

    def application_plan(operation_id, attempt_no):
        application_attempts.append((operation_id, attempt_no))
        return SandboxOutcome.SUCCESS

    def communication_plan(operation_id, attempt_no):
        communication_attempts.append((operation_id, attempt_no))
        if attempt_no == 1:
            return SandboxCommunicationOutcome.NO_EFFECT
        if attempt_no == 2:
            return SandboxCommunicationOutcome.SUCCESS
        raise AssertionError("The approved message operation must never exceed two attempts.")

    original_communication_init = (
        runtime_composition.SQLiteSandboxCommunicationProvider.__init__
    )

    def planned_communication_init(self, database, plan=None):
        original_communication_init(
            self, database, plan if plan is not None else communication_plan
        )

    monkeypatch.setattr(
        runtime_composition.SQLiteSandboxCommunicationProvider,
        "__init__",
        planned_communication_init,
    )

    resolve_bearer = lambda token: "candidate-1" if token == "owner" else None

    def build_runtime():
        return RuntimeCandidateApp(
            database_path=str(database_path),
            jobs=(job,),
            resolve_bearer=resolve_bearer,
            sandbox_outcome_plan=application_plan,
        )

    runtime1 = build_runtime()
    started = runtime1.start(
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
        run_id=run_id,
    )
    assert started.phase is CareerLoopPhase.APPLICATION_APPROVAL

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

    server1, thread1 = _start_server(runtime1)
    runtime2 = None
    server2 = None
    thread2 = None
    server1_stopped = False
    try:
        with sync_playwright() as playwright:
            browser1 = playwright.chromium.launch(headless=True)
            try:
                page = browser1.new_page()
                page.goto(f"http://127.0.0.1:{server1.server_port}/sandbox/candidate")
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
                expect(page.locator("#result")).to_contain_text('"phase": "message_send"')
                page.locator("#status").click()
                expect(page.locator("#run-overview")).to_contain_text("message send")
            finally:
                browser1.close()

        operation_id = f"{run_id}:message-send"
        message_id = f"{run_id}:message"
        before_restart = _communication_snapshot(database_path)
        assert before_restart["attempts"] == [
            (operation_id, 1, SandboxCommunicationOutcome.NO_EFFECT.value)
        ]
        assert len(before_restart["intents"]) == 1
        assert before_restart["intents"][0][0] == operation_id
        assert before_restart["deliveries"] == []
        assert before_restart["messages"] == [
            (
                message_id,
                message["sender"],
                message["recipient"],
                message["subject"],
                message["body"],
                "draft",
            )
        ]
        assert before_restart["provider_messages"] == [(message_id, "draft")]
        assert _submission_snapshot(database_path)["ledger"] == (1, 0, 0)

        _stop_server(server1, thread1, runtime1)
        server1_stopped = True
        runtime2 = build_runtime()
        server2, thread2 = _start_server(runtime2)

        browser2 = playwright.chromium.launch(headless=True)
        try:
            page = browser2.new_page()
            page.goto(f"http://127.0.0.1:{server2.server_port}/sandbox/candidate")
            page.locator("#token").fill("owner")
            page.locator("#run-id").fill(run_id)

            page.locator("#status").click()
            expect(page.locator("#run-overview")).to_contain_text("message send")
            page.locator("#continue").click()
            expect(page.locator("#result")).to_contain_text(
                '"phase": "interview_approval"'
            )

            page.locator("#status").click()
            expect(page.locator("#run-overview")).to_contain_text(
                "interview approval"
            )
            page.locator("#prompt").click()
            expect(page.locator("#approve")).to_be_enabled()
            expect(page.locator("#action-details")).to_contain_text("Sandbox Logistics")
        finally:
            browser2.close()
    finally:
        if server2 is not None and runtime2 is not None:
            _stop_server(server2, thread2, runtime2)
        if not server1_stopped:
            _stop_server(server1, thread1, runtime1)

    operation_id = f"{run_id}:message-send"
    message_id = f"{run_id}:message"
    assert len(application_attempts) == 1
    assert application_attempts[0][1] == 1
    assert communication_attempts == [(operation_id, 1), (operation_id, 2)]

    database = SQLiteDatabase.open_read_only(str(database_path))
    try:
        connection = database.connection
        operations = connection.execute(
            "SELECT action_type, status FROM external_action_operations ORDER BY action_type"
        ).fetchall()
        application_attempts_final = connection.execute(
            "SELECT idempotency_key, attempt_no, outcome "
            "FROM sandbox_submission_attempts ORDER BY idempotency_key, attempt_no"
        ).fetchall()
        application_ledger = connection.execute(
            "SELECT COUNT(*), MIN(replays), MAX(replays) FROM sandbox_submission_ledger"
        ).fetchone()
        communication_attempts_final = connection.execute(
            "SELECT operation_id, attempt_no, outcome "
            "FROM sandbox_communication_attempts ORDER BY operation_id, attempt_no"
        ).fetchall()
        communication_intents = connection.execute(
            "SELECT operation_id, fingerprint FROM sandbox_communication_intents"
        ).fetchall()
        deliveries = connection.execute(
            "SELECT operation_id, fingerprint, message_id, action, replays "
            "FROM sandbox_communication_deliveries"
        ).fetchall()
        outbound_messages = connection.execute(
            "SELECT message_id, sender, recipient, subject, body, direction "
            "FROM communication_messages WHERE direction = 'outbound'"
        ).fetchall()
        provider_messages = connection.execute(
            "SELECT message_id, direction FROM sandbox_communication_messages"
        ).fetchall()
        event = connection.execute(
            "SELECT status, version FROM scheduling_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
    finally:
        database.close()

    assert operations == [
        ("application.submit", "succeeded"),
        ("communication.send", "succeeded"),
    ]
    assert len(application_attempts_final) == 1
    assert application_attempts_final[0][1:] == (1, SandboxOutcome.SUCCESS.value)
    assert application_ledger == (1, 0, 0)
    assert communication_attempts_final == [
        (operation_id, 1, SandboxCommunicationOutcome.NO_EFFECT.value),
        (operation_id, 2, SandboxCommunicationOutcome.SUCCESS.value),
    ]
    assert len(communication_intents) == 1
    assert communication_intents[0][0] == operation_id
    assert deliveries == [
        (
            operation_id,
            communication_intents[0][1],
            message_id,
            "send",
            0,
        )
    ]
    assert outbound_messages == [
        (
            message_id,
            message["sender"],
            message["recipient"],
            message["subject"],
            message["body"],
            "outbound",
        )
    ]
    assert provider_messages == [(message_id, "outbound")]
    assert event == ("proposed", 1)


def test_ci_enforces_independent_coverage_gate_after_pytest():
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
    assert "if: " + chr(36) + "{{ always() && !cancelled() && steps.pytest.outcome != 'skipped' }}" in test_job
    assert test_job.index(pytest_command) < test_job.index(coverage_command)
