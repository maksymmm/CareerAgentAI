"""Chromium proof that a stale candidate decline cannot affect a newer event version."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Thread
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
from career_agent_ai.application.scheduling.models import (
    ScheduleEvent,
    ScheduleEventType,
    ScheduleStatus,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_scheduling_repository import (
    SQLiteSchedulingRepository,
)

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
EVENT_ID = "candidate-decline-version-race-event"


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


def _runtime(database_path, job):
    return RuntimeCandidateApp(
        database_path=str(database_path),
        jobs=(job,),
        resolve_bearer=lambda token: {"owner": "candidate-1"}.get(token),
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


def _seed_event(database_path):
    database = SQLiteDatabase(str(database_path))
    try:
        SQLiteSchedulingRepository(database).create(
            ScheduleEvent(
                event_id=EVENT_ID,
                candidate_id="candidate-1",
                employer_name="Sandbox Logistics",
                event_type=ScheduleEventType.INTERVIEW,
                location="Karlsruhe",
                start_at=NOW + timedelta(days=2),
                end_at=NOW + timedelta(days=2, hours=1),
                timezone_name="Europe/Berlin",
                status=ScheduleStatus.PROPOSED,
                application_id="application-1",
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    finally:
        database.close()


def _advance_event(database_path):
    connection = sqlite3.connect(database_path)
    try:
        cursor = connection.execute(
            """
            UPDATE scheduling_events
            SET status = ?, version = version + 1, updated_at = ?
            WHERE event_id = ? AND version = ?
            """,
            (
                ScheduleStatus.RESCHEDULE_REQUESTED.value,
                (NOW + timedelta(minutes=1)).isoformat(),
                EVENT_ID,
                1,
            ),
        )
        assert cursor.rowcount == 1
        connection.commit()
    finally:
        connection.close()


def _calendar_snapshot(database_path):
    connection = sqlite3.connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

        def count(table):
            if table not in tables:
                return 0
            return connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]

        decline_operations = 0
        if "external_action_operations" in tables:
            decline_operations = connection.execute(
                "SELECT COUNT(*) FROM external_action_operations "
                "WHERE action_type = ?",
                ("calendar.decline",),
            ).fetchone()[0]
        event = connection.execute(
            "SELECT status, version, active_action_operation_id "
            "FROM scheduling_events WHERE event_id = ?",
            (EVENT_ID,),
        ).fetchone()
        return {
            "decline_operations": decline_operations,
            "intents": count("sandbox_calendar_intents"),
            "attempts": count("sandbox_calendar_attempts"),
            "effects": count("sandbox_calendar_effects"),
            "event": event,
        }
    finally:
        connection.close()


def test_browser_stale_decline_conflict_requires_fresh_event_confirmation(tmp_path):
    """A concurrent event advance invalidates the displayed approval before provider I/O."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from playwright.sync_api import expect

    database_path = tmp_path / "candidate-decline-version-race.sqlite"
    _seed_event(database_path)
    job = Job.create(
        job_id="candidate-decline-version-race-job",
        title="Logistics Coordinator",
        company=Company("candidate-decline-version-race-company", "Sandbox Logistics"),
        location=Location("Germany", "Karlsruhe"),
        description="Warehouse logistics coordination and inventory control.",
        created_at=NOW,
    )
    runtime = _runtime(database_path, job)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            server, thread = _serve(runtime)
            try:
                page = browser.new_page()
                page.goto(
                    f"http://127.0.0.1:{server.server_port}/sandbox/candidate"
                )
                page.locator("#token").fill("owner")
                page.locator("#list-events").click()
                expect(page.locator("#owned-events")).to_contain_text(
                    "Sandbox Logistics — interview — proposed"
                )

                page.locator("#owned-events button").first.click()
                expect(page.locator("#event-detail")).to_contain_text(
                    '"state_version": 1'
                )
                expect(page.locator("#event-detail")).to_contain_text(
                    '"status": "proposed"'
                )
                expect(page.locator("#decline-confirmed")).to_be_enabled()
                expect(page.locator("#decline-event")).to_be_disabled()

                # The candidate approves the displayed version; another scheduler
                # advances the event before the browser submits that approval.
                page.locator("#decline-confirmed").check()
                expect(page.locator("#decline-event")).to_be_enabled()
                _advance_event(database_path)
                page.locator("#decline-event").click()

                expect(page.locator("#decline-state")).to_contain_text(
                    "No decline was submitted."
                )
                expect(page.locator("#event-detail-state")).to_contain_text(
                    "The event changed after your confirmation."
                )
                expect(page.locator("#event-detail")).to_be_empty()
                expect(page.locator("#decline-confirmed")).to_be_disabled()
                expect(page.locator("#decline-event")).to_be_disabled()

                # A fresh authoritative read shows the newer version. A new
                # explicit confirmation is required before the control is enabled.
                page.locator("#owned-events button").first.click()
                expect(page.locator("#event-detail")).to_contain_text(
                    '"state_version": 2'
                )
                expect(page.locator("#event-detail")).to_contain_text(
                    '"status": "reschedule_requested"'
                )
                expect(page.locator("#decline-confirmed")).to_be_enabled()
                expect(page.locator("#decline-confirmed")).not_to_be_checked()
                expect(page.locator("#decline-event")).to_be_disabled()
                page.locator("#decline-confirmed").check()
                expect(page.locator("#decline-event")).to_be_enabled()
                page.locator("#clear-session").click()
            finally:
                _stop(server, thread)
                runtime.close()
        finally:
            browser.close()

    assert _calendar_snapshot(database_path) == {
        "decline_operations": 0,
        "intents": 0,
        "attempts": 0,
        "effects": 0,
        "event": ("reschedule_requested", 2, None),
    }


def test_candidate_ci_enforces_independent_coverage_gate_after_pytest():
    """The independent coverage gate runs even when pytest fails."""
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")
    test_job = workflow.split("  release-evidence:", 1)[0]
    pytest_command = "python -m pytest"
    coverage_command = "python -m coverage report --fail-under=90"
    always_condition = "if: " + "$" + "{{ always() && !cancelled() && steps.pytest.outcome != 'skipped' }}"

    assert pytest_command in test_job
    assert "--cov=career_agent_ai" in test_job
    assert "--cov-report=term-missing" in test_job
    assert "--cov-fail-under=90" in test_job
    assert "id: pytest" in test_job
    assert coverage_command in test_job
    assert always_condition in test_job
    assert test_job.index(pytest_command) < test_job.index(coverage_command)
