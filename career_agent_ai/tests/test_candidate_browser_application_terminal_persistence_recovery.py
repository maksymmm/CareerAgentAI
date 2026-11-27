from __future__ import annotations

from datetime import datetime, timezone
from threading import Thread
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopPhase,
    CareerLoopRequest,
)
from career_agent_ai.application.external_actions import ExternalActionStatus
from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
from career_agent_ai.application.storage.sqlite_candidate_applications import (
    SQLiteCandidateApplications,
)
from career_agent_ai.application.storage.sqlite_career_loop_repository import (
    SQLiteCareerLoopRepository,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_sandbox_submission_provider import (
    SandboxOutcome,
)

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


def _stop_server(server, thread):
    if server is None:
        return
    server.shutdown()
    if thread is not None:
        thread.join(timeout=5)
        assert not thread.is_alive()
    server.server_close()


def _submission_evidence(path):
    database = SQLiteDatabase.open_read_only(str(path))
    try:
        intents = database.connection.execute(
            "SELECT idempotency_key, fingerprint FROM sandbox_submission_intents "
            "ORDER BY idempotency_key"
        ).fetchall()
        attempts = database.connection.execute(
            "SELECT idempotency_key, attempt_no, outcome "
            "FROM sandbox_submission_attempts ORDER BY idempotency_key, attempt_no"
        ).fetchall()
        submissions = database.connection.execute(
            "SELECT idempotency_key, application_id, provider_submission_id, replays "
            "FROM sandbox_submission_ledger ORDER BY idempotency_key"
        ).fetchall()
        operations = database.connection.execute(
            "SELECT action_type, status FROM external_action_operations ORDER BY action_type"
        ).fetchall()
        return intents, attempts, submissions, operations
    finally:
        database.close()


def test_candidate_browser_recovers_provider_applied_submission_after_terminal_write_failure(
    tmp_path,
):
    """Recover one committed application effect after its terminal status write fails."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from playwright.sync_api import expect

    database_path = str(tmp_path / "candidate-application-terminal-write-recovery.sqlite")
    jobs = (
        Job.create(
            job_id="terminal-write-recovery-job",
            title="Logistics Coordinator",
            company=Company("terminal-write-recovery-company", "Sandbox Logistics"),
            location=Location("Germany", "Karlsruhe"),
            description="logistics coordination",
            created_at=NOW,
        ),
    )
    plan_calls = []

    def outcome_plan(operation_id, attempt_no):
        plan_calls.append((operation_id, attempt_no))
        return SandboxOutcome.SUCCESS

    def build_runtime():
        return RuntimeCandidateApp(
            database_path=database_path,
            jobs=jobs,
            resolve_bearer=lambda token: "candidate-1" if token == "owner" else None,
            sandbox_outcome_plan=outcome_plan,
        )

    first = build_runtime()
    second = None
    server = None
    server_thread = None
    browser = None
    try:
        started = first.start(
            CareerLoopRequest(
                user_id="candidate-1",
                keyword="logistics",
                candidate_profile="Experienced logistics professional.",
                location="Karlsruhe",
            ),
            run_id="candidate-application-terminal-write-recovery",
        )
        assert started.phase is CareerLoopPhase.APPLICATION_APPROVAL
        run_id = started.run_id
        assert isinstance(run_id, str) and run_id

        database = SQLiteDatabase(database_path)
        try:
            database.connection.execute(
                """
                CREATE TRIGGER reject_application_terminal_success
                BEFORE UPDATE OF status ON external_action_operations
                WHEN OLD.action_type = 'application.submit'
                 AND OLD.status = 'in_progress'
                 AND NEW.status = 'succeeded'
                BEGIN
                    SELECT RAISE(ABORT, 'injected terminal success persistence failure');
                END
                """
            )
            database.connection.commit()
        finally:
            database.close()

        server = make_server("127.0.0.1", 0, first, handler_class=QuietHandler)
        port = server.server_port
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/sandbox/candidate")
            page.locator("#token").fill("owner")
            page.locator("#run-id").fill(run_id)
            page.locator("#status").click()
            expect(page.locator("#run-overview")).to_contain_text("application approval")
            page.locator("#prompt").click()
            expect(page.locator("#approve")).to_be_enabled()
            page.locator("#approve").click()
            page.locator("#status").click()
            expect(page.locator("#run-overview")).to_contain_text("application reconciliation")
            expect(page.locator("#provider-submission-id")).to_be_enabled()
            expect(page.locator("#application-reconciliation-verified")).to_be_enabled()
            expect(page.locator("#confirm-submitted")).to_be_disabled()

            before_restart = _submission_evidence(database_path)
            intents, attempts, submissions, operations = before_restart
            assert len(intents) == 1
            assert len(attempts) == 1
            assert attempts[0][1:] == (1, SandboxOutcome.SUCCESS.value)
            assert len(submissions) == 1
            operation_id, application_id, provider_submission_id, replays = submissions[0]
            assert provider_submission_id == f"fake:{application_id}"
            assert replays == 0
            assert operations == [
                ("application.submit", ExternalActionStatus.RECONCILIATION_REQUIRED.value)
            ]
            assert plan_calls == [(operation_id, 1)]

            page.locator("#clear-session").click()
            _stop_server(server, server_thread)
            server = None
            server_thread = None
            first.close()
            first = None

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
            expect(page.locator("#run-overview")).to_contain_text("application reconciliation")
            expect(page.locator("#provider-submission-id")).to_be_enabled()
            expect(page.locator("#application-reconciliation-verified")).to_be_enabled()
            expect(page.locator("#confirm-submitted")).to_be_disabled()
            assert _submission_evidence(database_path) == before_restart
            assert plan_calls == [(operation_id, 1)]

            page.locator("#provider-submission-id").fill(provider_submission_id)
            page.locator("#application-reconciliation-verified").check()
            expect(page.locator("#confirm-submitted")).to_be_enabled()
            page.locator("#confirm-submitted").click()
            expect(page.locator("#reconciliation-state")).to_contain_text(
                "Reconciliation finished"
            )
            page.locator("#status").click()
            expect(page.locator("#run-overview")).to_contain_text("complete")
            assert plan_calls == [(operation_id, 1)]
            page.locator("#clear-session").click()
            browser.close()
            browser = None
    finally:
        if browser is not None:
            browser.close()
        _stop_server(server, server_thread)
        if first is not None:
            first.close()
        if second is not None:
            second.close()

    database = SQLiteDatabase(database_path)
    try:
        state = SQLiteCareerLoopRepository(database).get(run_id)
        assert state is not None and state.phase is CareerLoopPhase.COMPLETE
        operations = database.connection.execute(
            "SELECT action_type, status FROM external_action_operations ORDER BY action_type"
        ).fetchall()
        attempts = database.connection.execute(
            "SELECT idempotency_key, attempt_no, outcome "
            "FROM sandbox_submission_attempts ORDER BY idempotency_key, attempt_no"
        ).fetchall()
        submissions = database.connection.execute(
            "SELECT idempotency_key, application_id, provider_submission_id, replays "
            "FROM sandbox_submission_ledger ORDER BY idempotency_key"
        ).fetchall()
    finally:
        database.close()

    assert operations == [("application.submit", ExternalActionStatus.SUCCEEDED.value)]
    assert attempts == [(operation_id, 1, SandboxOutcome.SUCCESS.value)]
    assert submissions == [(operation_id, application_id, provider_submission_id, 0)]
    application = SQLiteCandidateApplications(database_path).get_snapshot(
        application_id, user_id="candidate-1"
    )
    assert application["application"]["status"] == "applied"
    assert plan_calls == [(operation_id, 1)]
