from __future__ import annotations

import json
from datetime import datetime, timezone
from threading import Thread
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest


NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


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


def _provider_counts(database_path: str) -> dict[str, int]:
    from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase

    tables = (
        "external_action_operations",
        "sandbox_submission_attempts",
        "sandbox_submission_ledger",
        "sandbox_communication_attempts",
        "sandbox_communication_deliveries",
        "sandbox_calendar_attempts",
        "sandbox_calendar_effects",
    )
    database = SQLiteDatabase.open_read_only(database_path)
    try:
        existing = {
            row[0]
            for row in database.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        return {
            table: (
                database.connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                if table in existing
                else 0
            )
            for table in tables
        }
    finally:
        database.close()


def test_candidate_cancellation_is_browser_confirmed_and_terminal_after_runtime_restart(
    tmp_path,
):
    """The actual dashboard cancels a fresh approval gate and cannot resume it after restart."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

    from career_agent_ai.application.career.autonomous_loop_models import (
        CareerLoopPhase,
        CareerLoopRequest,
    )
    from career_agent_ai.application.jobs.company import Company
    from career_agent_ai.application.jobs.job import Job
    from career_agent_ai.application.jobs.location import Location
    from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
    from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase

    database_path = str(tmp_path / "candidate-cancellation-browser-restart.sqlite")
    job = Job.create(
        job_id="candidate-cancel-browser-job",
        title="Logistics Coordinator",
        company=Company("candidate-cancel-browser-company", "Sandbox Logistics"),
        location=Location("Germany", "Karlsruhe"),
        description="Warehouse logistics coordination",
        created_at=NOW,
    )
    request = CareerLoopRequest(
        user_id="candidate-1",
        keyword="logistics",
        candidate_profile="Experienced logistics professional.",
        location="Karlsruhe",
        sender="candidate@example.test",
        recipient="recruiter@example.test",
        message_subject="Application follow-up",
        message_body="Thank you for considering my application.",
    )

    def build_runtime():
        return RuntimeCandidateApp(
            database_path=database_path,
            jobs=(job,),
            resolve_bearer=lambda token: "candidate-1" if token == "valid" else None,
        )

    runtime1 = build_runtime()
    started = runtime1.start(request, run_id="candidate-cancel-browser")
    assert started.phase is CareerLoopPhase.APPLICATION_APPROVAL
    run_id = started.run_id

    server1 = make_server("127.0.0.1", 0, runtime1, handler_class=QuietHandler)
    thread1 = Thread(target=server1.serve_forever, daemon=True)
    thread1.start()
    runtime2 = None
    server2 = None
    thread2 = None
    server1_stopped = False

    try:
        with sync_playwright() as playwright:
            browser1 = playwright.chromium.launch(headless=True)
            try:
                page1 = browser1.new_page()
                page1.goto(
                    f"http://127.0.0.1:{server1.server_port}/sandbox/candidate"
                )
                page1.locator("#token").fill("valid")
                page1.locator("#list-runs").click()
                page1.locator("#owned-runs button").first.wait_for()
                page1.locator("#owned-runs button").first.click()
                page1.locator("#status").click()
                page1.wait_for_function(
                    "() => document.querySelector('#run-overview').textContent.includes('Your decision is needed')"
                )
                assert page1.locator("#cancel-run").is_enabled()

                # The candidate's actual dashboard control submits the exact
                # version observed by the preceding authenticated status read.
                page1.locator("#cancel-run").click()
                page1.wait_for_function(
                    "() => document.querySelector('#run-overview').textContent.includes('Cancelled — refresh status')"
                )
                assert page1.locator("#cancel-run").is_disabled()

                page1.locator("#status").click()
                page1.wait_for_function(
                    "() => document.querySelector('#run-overview').textContent.includes('Cancelled — cancelled')"
                )
                first_snapshot = json.loads(page1.locator("#result").text_content())[
                    "payload"
                ]
                assert first_snapshot["phase"] == "cancelled"
                assert first_snapshot["terminal"] is True
                assert first_snapshot["waiting_for_human"] is False
            finally:
                browser1.close()

        _stop_server(server1, thread1)
        server1_stopped = True
        runtime1.close()

        # Reconstruct the production composition over the same durable database.
        runtime2 = build_runtime()
        server2 = make_server("127.0.0.1", 0, runtime2, handler_class=QuietHandler)
        thread2 = Thread(target=server2.serve_forever, daemon=True)
        thread2.start()

        with sync_playwright() as playwright:
            browser2 = playwright.chromium.launch(headless=True)
            try:
                page2 = browser2.new_page()
                page2.goto(
                    f"http://127.0.0.1:{server2.server_port}/sandbox/candidate"
                )
                page2.locator("#token").fill("valid")
                page2.locator("#list-runs").click()
                page2.locator("#owned-runs button").first.wait_for()
                page2.locator("#owned-runs button").first.click()
                page2.locator("#status").click()
                page2.wait_for_function(
                    "() => document.querySelector('#run-overview').textContent.includes('Cancelled — cancelled')"
                )
                assert page2.locator("#cancel-run").is_disabled()

                # Even a direct authenticated continuation request after restart
                # returns the same terminal cancellation and cannot resume work.
                continued = page2.evaluate(
                    """async (runId) => {
                      const response = await fetch(
                        '/v1/candidate/runs/' + encodeURIComponent(runId) + '/continue',
                        {method: 'POST', headers: {Authorization: 'Bearer valid'}}
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
                page2.wait_for_function(
                    "() => document.querySelector('#run-overview').textContent.includes('Cancelled — cancelled')"
                )
                restored_snapshot = json.loads(
                    page2.locator("#result").text_content()
                )["payload"]
                assert restored_snapshot == first_snapshot
            finally:
                browser2.close()
    finally:
        if not server1_stopped:
            _stop_server(server1, thread1)
            runtime1.close()
        if server2 is not None:
            _stop_server(server2, thread2)
        if runtime2 is not None:
            runtime2.close()

    counts = _provider_counts(database_path)
    assert all(value == 0 for value in counts.values()), counts

    database = SQLiteDatabase.open_read_only(database_path)
    try:
        projection = database.connection.execute(
            "SELECT phase, version, payload_json FROM autonomous_career_loops WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert projection is not None
        assert projection[0] == "cancelled"
        assert projection[1] == first_snapshot["state_version"]
    finally:
        database.close()


def test_candidate_ci_enforces_precise_independent_coverage_after_pytest():
    from pathlib import Path

    workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    pytest_command = "python -m pytest"
    coverage_command = "python -m coverage report --precision=2 --fail-under=90"
    assert pytest_command in workflow
    assert coverage_command in workflow
    assert workflow.index("Run test suite") < workflow.index(
        "Enforce independent coverage threshold"
    )
    assert "steps.pytest.outcome != 'skipped'" in workflow
