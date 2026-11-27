from __future__ import annotations

import json
from datetime import datetime, timezone
from socketserver import ThreadingMixIn
from threading import Barrier, Event, Thread, local
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server

import pytest


NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def _serve(app):
    server = make_server(
        "127.0.0.1",
        0,
        app,
        server_class=ThreadingWSGIServer,
        handler_class=QuietHandler,
    )
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


def _sandbox_counts(connection):
    tables = (
        "external_action_operations",
        "sandbox_submission_attempts",
        "sandbox_submission_ledger",
        "sandbox_communication_attempts",
        "sandbox_communication_deliveries",
        "sandbox_calendar_attempts",
        "sandbox_calendar_effects",
    )
    existing = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    return {
        table: (
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if table in existing
            else 0
        )
        for table in tables
    }


@pytest.mark.parametrize("winner", ["cancel", "approve"])
def test_candidate_cancel_and_approval_race_through_authenticated_browser_wsgi(
    tmp_path, monkeypatch, winner
):
    """The authenticated HTTP routes serialize competing decisions on one snapshot."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from playwright.sync_api import expect

    from career_agent_ai.application.career.autonomous_loop_models import (
        CareerLoopPhase,
        CareerLoopRequest,
    )
    from career_agent_ai.application.career.autonomous_loop_repository import (
        CareerLoopConflictError,
    )
    from career_agent_ai.application.jobs.company import Company
    from career_agent_ai.application.jobs.job import Job
    from career_agent_ai.application.jobs.job_application_status import (
        JobApplicationStatus,
    )
    from career_agent_ai.application.jobs.location import Location
    from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
    from career_agent_ai.application.storage.sqlite_career_loop_repository import (
        SQLiteCareerLoopRepository,
    )
    from career_agent_ai.application.storage.sqlite_communication_repository import (
        SQLiteCommunicationRepository,
    )
    from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
    from career_agent_ai.application.storage.sqlite_job_application_repository import (
        SQLiteJobApplicationRepository,
    )

    database_path = str(tmp_path / f"candidate-cancel-approval-race-{winner}.sqlite")
    client_run_id = f"candidate-cancel-approval-race-{winner}"
    job = Job.create(
        job_id=f"{client_run_id}-job",
        title="Logistics Coordinator",
        company=Company(f"{client_run_id}-company", "Sandbox Logistics"),
        location=Location("Germany", "Karlsruhe"),
        description="Warehouse logistics coordination and inventory control.",
        created_at=NOW,
    )
    request = CareerLoopRequest(
        user_id="candidate-1",
        keyword="logistics",
        candidate_profile="Experienced logistics professional.",
        location="Karlsruhe",
        sender="candidate@example.test",
        recipient="recruiter@example.test",
        message_subject="Logistics coordinator application",
        message_body="I would welcome a conversation about this role.",
    )

    runtime = RuntimeCandidateApp(
        database_path=database_path,
        jobs=(job,),
        resolve_bearer=lambda token: "candidate-1" if token == "owner" else None,
    )
    started = runtime.start(request, run_id=client_run_id)
    assert started.phase is CareerLoopPhase.APPLICATION_APPROVAL
    run_id = started.run_id

    request_barrier = Barrier(2)
    winner_claimed = Event()
    loser_conflicted = Event()
    release_winner = Event()
    controller_timed_out = Event()
    request_context = local()
    original_claim = SQLiteCareerLoopRepository.claim_execution

    def controlled_claim(
        repository,
        candidate_run_id,
        owner_id,
        *,
        expected_version,
        lease_seconds,
    ):
        role = getattr(request_context, "role", None)
        if (
            candidate_run_id != run_id
            or role not in {"cancel", "approve"}
            or getattr(request_context, "claim_seen", False)
        ):
            return original_claim(
                repository,
                candidate_run_id,
                owner_id,
                expected_version=expected_version,
                lease_seconds=lease_seconds,
            )

        request_context.claim_seen = True
        if role == winner:
            result = original_claim(
                repository,
                candidate_run_id,
                owner_id,
                expected_version=expected_version,
                lease_seconds=lease_seconds,
            )
            winner_claimed.set()
            if not release_winner.wait(timeout=15):
                raise TimeoutError("The competing request did not reach its claim.")
            return result

        if not winner_claimed.wait(timeout=15):
            raise TimeoutError("The winning request did not acquire its lease.")
        try:
            return original_claim(
                repository,
                candidate_run_id,
                owner_id,
                expected_version=expected_version,
                lease_seconds=lease_seconds,
            )
        except CareerLoopConflictError:
            loser_conflicted.set()
            raise

    monkeypatch.setattr(
        SQLiteCareerLoopRepository, "claim_execution", controlled_claim
    )

    class RaceGate:
        def __call__(self, environ, start_response):
            role = None
            if environ.get("REQUEST_METHOD") == "POST":
                path = str(environ.get("PATH_INFO", ""))
                if path.endswith("/cancel"):
                    role = "cancel"
                elif path.endswith("/approval"):
                    role = "approve"
            if role is not None:
                request_context.role = role
                request_context.claim_seen = False
                request_barrier.wait(timeout=10)
            try:
                return runtime(environ, start_response)
            finally:
                if role is not None:
                    request_context.role = None
                    request_context.claim_seen = False

    server, server_thread = _serve(RaceGate())
    release_controller = None

    def release_after_loser_conflicts():
        if not loser_conflicted.wait(timeout=15):
            controller_timed_out.set()
        release_winner.set()

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{server.server_port}/sandbox/candidate")
                page.locator("#token").fill("owner")
                page.locator("#list-runs").click()
                page.locator("#owned-runs button").first.wait_for()
                page.locator("#owned-runs button").first.click()
                assert page.locator("#run-id").input_value() == run_id

                page.locator("#status").click()
                expect(page.locator("#run-overview")).to_contain_text(
                    "application approval"
                )
                page.locator("#prompt").click()
                expect(page.locator("#approve")).to_be_enabled()
                expect(page.locator("#cancel-run")).to_be_enabled()

                release_controller = Thread(
                    target=release_after_loser_conflicts, daemon=True
                )
                release_controller.start()
                outcomes = page.evaluate(
                    """async (runId) => {
                      const headers = {
                        Authorization: 'Bearer owner',
                        'Content-Type': 'application/json'
                      };
                      const base = '/v1/candidate/runs/' + encodeURIComponent(runId);
                      const promptResponse = await fetch(base + '/approval', {
                        headers: {Authorization: headers.Authorization}
                      });
                      const prompt = await promptResponse.json();
                      if (!promptResponse.ok) {
                        throw new Error('Fresh approval prompt could not be loaded.');
                      }
                      const cancel = fetch(base + '/cancel', {
                        method: 'POST',
                        headers,
                        body: JSON.stringify({
                          schema_version: 1,
                          state_version: prompt.state_version
                        })
                      }).then(async response => ({
                        status: response.status,
                        body: await response.json()
                      }));
                      const approve = fetch(base + '/approval', {
                        method: 'POST',
                        headers,
                        body: JSON.stringify({
                          schema_version: 1,
                          run_id: prompt.run_id,
                          state_version: prompt.state_version,
                          action_fingerprint: prompt.action_fingerprint,
                          decision: 'approve'
                        })
                      }).then(async response => ({
                        status: response.status,
                        body: await response.json()
                      }));
                      const [cancelResult, approveResult] = await Promise.all([
                        cancel, approve
                      ]);
                      return {
                        prompt_version: prompt.state_version,
                        cancel: cancelResult,
                        approve: approveResult
                      };
                    }""",
                    run_id,
                )
                release_controller.join(timeout=5)
                assert not release_controller.is_alive()
                assert not controller_timed_out.is_set()
                assert loser_conflicted.is_set()

                if winner == "cancel":
                    assert outcomes["cancel"] == {
                        "status": 200,
                        "body": {"run_id": run_id, "phase": "cancelled"},
                    }
                    assert outcomes["approve"]["status"] == 409
                    assert outcomes["approve"]["body"] == {
                        "error": "approval_conflict"
                    }
                else:
                    assert outcomes["approve"] == {
                        "status": 200,
                        "body": {"run_id": run_id, "phase": "message_approval"},
                    }
                    assert outcomes["cancel"]["status"] == 409
                    assert outcomes["cancel"]["body"] == {
                        "error": "approval_conflict"
                    }

                page.locator("#status").click()
                if winner == "cancel":
                    expect(page.locator("#run-overview")).to_contain_text(
                        "Cancelled — cancelled"
                    )
                    expect(page.locator("#cancel-run")).to_be_disabled()
                else:
                    expect(page.locator("#run-overview")).to_contain_text(
                        "message approval"
                    )
                    expect(page.locator("#cancel-run")).to_be_enabled()
                final_status = json.loads(page.locator("#result").text_content())[
                    "payload"
                ]
                assert final_status["state_version"] > outcomes["prompt_version"]
                if winner == "cancel":
                    assert final_status["phase"] == "cancelled"
                    assert final_status["terminal"] is True
                    assert final_status["waiting_for_human"] is False
                else:
                    assert final_status["phase"] == "message_approval"
                    assert final_status["terminal"] is False
                    assert final_status["waiting_for_human"] is True
            finally:
                browser.close()
    finally:
        release_winner.set()
        if release_controller is not None:
            release_controller.join(timeout=5)
        _stop(server, server_thread)
        runtime.close()

    database = SQLiteDatabase(database_path)
    try:
        state = SQLiteCareerLoopRepository(database).get(run_id)
        assert state is not None
        applications = SQLiteJobApplicationRepository(database)
        application = applications.get(state.application_id)
        assert application is not None
        counts = _sandbox_counts(database.connection)
        assert counts["sandbox_communication_attempts"] == 0
        assert counts["sandbox_communication_deliveries"] == 0
        assert counts["sandbox_calendar_attempts"] == 0
        assert counts["sandbox_calendar_effects"] == 0

        if winner == "cancel":
            assert state.phase is CareerLoopPhase.CANCELLED
            assert application.status is JobApplicationStatus.SAVED
            assert counts["external_action_operations"] == 0
            assert counts["sandbox_submission_attempts"] == 0
            assert counts["sandbox_submission_ledger"] == 0
        else:
            assert state.phase is CareerLoopPhase.MESSAGE_APPROVAL
            assert application.status is JobApplicationStatus.APPLIED
            assert counts["external_action_operations"] == 1
            assert counts["sandbox_submission_attempts"] == 1
            assert counts["sandbox_submission_ledger"] == 1
            messages = SQLiteCommunicationRepository(database)
            message = messages.get(state.message_id)
            assert message is not None
            assert message.direction.value == "draft"
    finally:
        database.close()
