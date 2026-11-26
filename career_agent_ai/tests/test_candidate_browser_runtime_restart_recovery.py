from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from threading import Thread
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

import career_agent_ai.application.runtime.composition as runtime_composition
from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopPhase,
    CareerLoopRequest,
)
from career_agent_ai.application.external_actions import ExternalActionStatus
from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
from career_agent_ai.application.scheduling.models import (
    ScheduleEvent,
    ScheduleEventType,
    ScheduleStatus,
)
from career_agent_ai.application.storage.sqlite_career_loop_repository import (
    SQLiteCareerLoopRepository,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_sandbox_calendar_provider import (
    SandboxCalendarOutcome,
)
from career_agent_ai.application.storage.sqlite_sandbox_submission_provider import (
    SandboxOutcome,
)
from career_agent_ai.application.storage.sqlite_scheduling_repository import (
    SQLiteSchedulingRepository,
)


NOW = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


def _request(runtime, path: str, *, method: str = "GET", body=None):
    encoded = b"" if body is None else json.dumps(body).encode("utf-8")
    responses = []
    result = runtime(
        {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "HTTP_AUTHORIZATION": "Bearer owner",
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": str(len(encoded)),
            "wsgi.input": io.BytesIO(encoded),
        },
        lambda status, headers: responses.append((status, headers)),
    )
    assert len(responses) == 1
    return responses[0][0], json.loads(b"".join(result))


def _approve(runtime, run_id: str):
    path = f"/v1/candidate/runs/{run_id}/approval"
    status, prompt = _request(runtime, path)
    assert status == "200 OK"
    assert prompt["run_id"] == run_id
    status, result = _request(
        runtime,
        path,
        method="POST",
        body={
            "schema_version": 1,
            "run_id": run_id,
            "state_version": prompt["state_version"],
            "action_fingerprint": prompt["action_fingerprint"],
            "decision": "approve",
        },
    )
    assert status == "200 OK"
    return result


def _stop_server(server, thread):
    if server is None:
        return
    server.shutdown()
    if thread is not None:
        thread.join(timeout=5)
        assert not thread.is_alive()
    server.server_close()


def test_candidate_browser_recovers_calendar_timeout_after_runtime_restart_without_replay(
    tmp_path, monkeypatch
):
    """A browser reconnects to a new runtime and reconciles the one durable calendar effect."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from playwright.sync_api import expect

    database_path = str(tmp_path / "candidate-browser-runtime-restart.sqlite")
    jobs = (
        Job.create(
            job_id="browser-restart-job",
            title="Logistics Coordinator",
            company=Company("browser-restart-company", "Sandbox Logistics"),
            location=Location("Germany", "Karlsruhe"),
            description="logistics coordination",
            created_at=NOW,
        ),
    )
    event_id = "browser-restart-interview"
    application_attempts = []
    calendar_attempts = []

    def application_plan(operation_id, attempt_no):
        application_attempts.append((operation_id, attempt_no))
        return SandboxOutcome.SUCCESS

    original_calendar_init = runtime_composition.SQLiteSandboxCalendarProvider.__init__

    def planned_calendar_init(self, database, plan=None):
        def outcome_plan(operation_id, attempt_no):
            calendar_attempts.append((operation_id, attempt_no))
            return SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT

        original_calendar_init(self, database, plan or outcome_plan)

    monkeypatch.setattr(
        runtime_composition.SQLiteSandboxCalendarProvider,
        "__init__",
        planned_calendar_init,
    )

    def build_runtime():
        return RuntimeCandidateApp(
            database_path=database_path,
            jobs=jobs,
            resolve_bearer=lambda token: "candidate-1" if token == "owner" else None,
            sandbox_outcome_plan=application_plan,
        )

    first = build_runtime()
    second = None
    server = None
    server_thread = None
    try:
        started = first.start(
            CareerLoopRequest(
                user_id="candidate-1",
                keyword="logistics",
                candidate_profile="Experienced logistics professional.",
                location="Karlsruhe",
                sender="candidate@example.test",
                recipient="recruiter@example.test",
                schedule_event_id=event_id,
            ),
            run_id="candidate-browser-runtime-restart",
        )
        assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL
        run_id = started.run_id
        assert isinstance(run_id, str) and run_id

        database = SQLiteDatabase(database_path)
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

        assert _approve(first, run_id)["phase"] == CareerLoopPhase.MESSAGE_APPROVAL.value
        assert _approve(first, run_id)["phase"] == CareerLoopPhase.INTERVIEW_APPROVAL.value
        assert _approve(first, run_id)["phase"] == CareerLoopPhase.INTERVIEW_RECONCILIATION.value
        assert application_attempts and len(application_attempts) == 1

        database = SQLiteDatabase(database_path)
        try:
            calendar_operation = database.connection.execute(
                "SELECT operation_id, status FROM external_action_operations "
                "WHERE action_type = ?",
                ("calendar.accept",),
            ).fetchone()
            effect_row = database.connection.execute(
                "SELECT event_json, replays FROM sandbox_calendar_effects"
            ).fetchone()
            calendar_rows = database.connection.execute(
                "SELECT operation_id, attempt_no, outcome FROM sandbox_calendar_attempts "
                "ORDER BY attempt_no"
            ).fetchall()
            assert calendar_operation is not None
            assert calendar_operation[1] == ExternalActionStatus.RECONCILIATION_REQUIRED.value
            assert effect_row is not None and effect_row[1] == 0
            assert len(calendar_rows) == 1
            provider_event_id = json.loads(effect_row[0])["provider_event_id"]
            assert provider_event_id
        finally:
            database.close()

        server = make_server("127.0.0.1", 0, first, handler_class=QuietHandler)
        port = server.server_port
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                url = f"http://127.0.0.1:{port}/sandbox/candidate"
                page.goto(url)
                page.locator("#token").fill("owner")
                page.locator("#run-id").fill(run_id)
                page.locator("#status").click()
                expect(page.locator("#run-overview")).to_contain_text("interview reconciliation")
                expect(page.locator("#provider-event-id")).to_be_enabled()
                expect(page.locator("#confirm-calendar-completed")).to_be_disabled()
    
                # The dashboard intentionally keeps credentials and selected state in tab memory.
                page.locator("#clear-session").click()
                expect(page.locator("#token")).to_have_value("")
                expect(page.locator("#run-id")).to_have_value("")
    
                _stop_server(server, server_thread)
                server = None
                server_thread = None
                first.close()
    
                # Rebind the same local origin to a new RuntimeCandidateApp over the same SQLite file.
                second = build_runtime()
                server = make_server("127.0.0.1", port, second, handler_class=QuietHandler)
                server_thread = Thread(target=server.serve_forever, daemon=True)
                server_thread.start()
                page.reload(wait_until="domcontentloaded")
                expect(page.locator("#token")).to_have_value("")
                expect(page.locator("#run-id")).to_have_value("")
    
                page.locator("#token").fill("owner")
                page.locator("#run-id").fill(run_id)
                page.locator("#status").click()
                expect(page.locator("#run-overview")).to_contain_text("interview reconciliation")
                expect(page.locator("#provider-event-id")).to_be_enabled()
                page.locator("#provider-event-id").fill(provider_event_id)
                page.locator("#interview-reconciliation-verified").check()
                expect(page.locator("#confirm-calendar-completed")).to_be_enabled()
                page.locator("#confirm-calendar-completed").click()
                expect(page.locator("#interview-reconciliation-state")).to_contain_text(
                    "Reconciliation finished"
                )
                expect(page.locator("#confirm-calendar-completed")).to_be_disabled()
    
                page.locator("#status").click()
                expect(page.locator("#run-overview")).to_contain_text("Finished")
                expect(page.locator("#run-overview")).to_contain_text("complete")
                expect(page.locator("#confirm-calendar-completed")).to_be_disabled()
                expect(page.locator("#confirm-calendar-not-completed")).to_be_disabled()
    
            finally:
                browser.close()
    finally:
        _stop_server(server, server_thread)
        first.close()
        if second is not None:
            second.close()

    assert len(calendar_attempts) == 1
    assert calendar_attempts[0][1] == 1

    database = SQLiteDatabase(database_path)
    try:
        state = SQLiteCareerLoopRepository(database).get(run_id)
        assert state is not None
        assert state.phase is CareerLoopPhase.COMPLETE

        event = SQLiteSchedulingRepository(database).get(event_id)
        assert event is not None
        assert event.status is ScheduleStatus.ACCEPTED
        assert event.provider_event_id == provider_event_id

        operations = database.connection.execute(
            "SELECT action_type, status FROM external_action_operations ORDER BY action_type"
        ).fetchall()
        submission = database.connection.execute(
            "SELECT COUNT(*), MIN(replays), MAX(replays) FROM sandbox_submission_ledger"
        ).fetchone()
        deliveries = database.connection.execute(
            "SELECT COUNT(*), MIN(replays), MAX(replays) FROM sandbox_communication_deliveries"
        ).fetchone()
        outbound_messages = database.connection.execute(
            "SELECT COUNT(*) FROM sandbox_communication_messages WHERE direction = 'outbound'"
        ).fetchone()[0]
        calendar_effects = database.connection.execute(
            "SELECT COUNT(*), MIN(replays), MAX(replays) FROM sandbox_calendar_effects"
        ).fetchone()
        calendar_rows = database.connection.execute(
            "SELECT operation_id, attempt_no, outcome FROM sandbox_calendar_attempts "
            "ORDER BY attempt_no"
        ).fetchall()
    finally:
        database.close()

    assert operations == [
        ("application.submit", ExternalActionStatus.SUCCEEDED.value),
        ("calendar.accept", ExternalActionStatus.SUCCEEDED.value),
        ("communication.send", ExternalActionStatus.SUCCEEDED.value),
    ]
    assert submission == (1, 0, 0)
    assert deliveries == (1, 0, 0)
    assert outbound_messages == 1
    assert calendar_effects == (1, 0, 0)
    assert calendar_rows == [
        (calendar_operation[0], 1, SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT.value)
    ]


def test_ci_runs_independent_coverage_gate_after_full_pytest():
    """The workflow must retain a separate CLI coverage gate after the test suite."""
    from pathlib import Path

    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")
    assert "name: Run test suite" in workflow
    assert "name: Enforce independent coverage threshold" in workflow
    assert "python -m pytest" in workflow
    assert "python -m coverage report --fail-under=90" in workflow
    assert workflow.index("python -m pytest") < workflow.index(
        "python -m coverage report --fail-under=90"
    )
