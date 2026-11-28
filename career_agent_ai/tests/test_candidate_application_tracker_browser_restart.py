from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from threading import Thread
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

from career_agent_ai.application.jobs.job_application import JobApplication
from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus
from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_job_application_repository import (
    SQLiteJobApplicationRepository,
)


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


def _seed_tracker(path):
    database = SQLiteDatabase(str(path))
    repository = SQLiteJobApplicationRepository(database)
    created_at = datetime(2026, 11, 28, 10, 0, tzinfo=timezone.utc)

    for index in range(26):
        application = JobApplication(
            application_id=f"application-{index:02d}",
            user_id="candidate-1",
            job_id=f"job-{index:02d}",
            company_id=f"company-{index:02d}",
            job_title=f"Role {index:02d}",
            status=JobApplicationStatus.SAVED,
            created_at=created_at,
            updated_at=created_at,
        )
        repository.add(application)
        if index == 0:
            applied = application.transition(
                JobApplicationStatus.APPLIED,
                note="private tracker note that must never reach the candidate",
            )
            repository.update(applied, expected_version=application.version)
            interviewed = applied.transition(
                JobApplicationStatus.INTERVIEW,
                note="private second tracker note",
            )
            repository.update(interviewed, expected_version=applied.version)

    repository.add(
        JobApplication(
            application_id="application-foreign",
            user_id="candidate-2",
            job_id="foreign-job",
            company_id="foreign-company",
            job_title="Private foreign role",
            status=JobApplicationStatus.APPLIED,
            created_at=created_at,
            updated_at=created_at,
        )
    )
    database.close()


def _start_server(database_path):
    runtime = RuntimeCandidateApp(
        database_path=str(database_path),
        jobs=(),
        resolve_bearer=lambda token: "candidate-1" if token == "valid" else None,
    )
    server = make_server("127.0.0.1", 0, runtime, handler_class=_QuietHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return runtime, server, thread, f"http://127.0.0.1:{server.server_port}/sandbox/candidate"


def _stop_server(runtime, server, thread):
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()
    runtime.close()


def _assert_tracker_view(page):
    page.locator("#application-status").select_option("interview")
    page.get_by_role("button", name="Load my applications").click()
    page.get_by_text(
        "1 application on this page. End of list. Read-only snapshots."
    ).wait_for()
    assert page.locator("#owned-applications li").count() == 1
    assert "application-00" in page.locator("#owned-applications").text_content()
    assert "application-foreign" not in page.locator("body").text_content()
    assert "Private foreign role" not in page.locator("body").text_content()

    page.get_by_role(
        "button", name="View application 1: application-00"
    ).click()
    page.get_by_text("Read-only application detail.").wait_for()
    detail = json.loads(page.locator("#application-detail").text_content())
    assert detail == {
        "application_id": "application-00",
        "job_id": "job-00",
        "job_title": "Role 00",
        "company_id": "company-00",
        "status": "interview",
        "state_version": 3,
        "created_at": detail["created_at"],
        "updated_at": detail["updated_at"],
    }
    assert detail["created_at"].startswith("2026-11-28T10:00:00")
    assert detail["updated_at"]
    assert "private tracker note" not in page.locator("body").text_content()

    page.get_by_role("button", name="Load application history").click()
    page.get_by_text("Read-only application history.").wait_for()
    history = json.loads(page.locator("#application-history").text_content())
    assert history["schema_version"] == 1
    assert history["application_id"] == "application-00"
    assert history["state_version"] == 3
    assert [
        (item["sequence"], item["from_status"], item["to_status"])
        for item in history["items"]
    ] == [
        (0, "saved", "applied"),
        (1, "applied", "interview"),
    ]
    assert "private tracker note" not in page.locator("body").text_content()


def test_candidate_application_tracker_browser_paginates_and_recovers_after_runtime_restart(
    tmp_path,
):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    database_path = tmp_path / "candidate-application-tracker.sqlite"
    _seed_tracker(database_path)
    baseline = database_path.read_bytes()

    connection = sqlite3.connect(database_path)
    try:
        initial_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "job_applications" in initial_tables
        assert "application_timeline" in initial_tables
        assert not any(name.startswith("sandbox_") for name in initial_tables)
        assert "external_action_operations" not in initial_tables
    finally:
        connection.close()

    runtime, server, thread, url = _start_server(database_path)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url)
            page.locator("#token").fill("valid")
            page.get_by_role("button", name="Load my applications").click()
            page.get_by_text(
                "25 applications on this page. More available. Read-only snapshots."
            ).wait_for()
            assert page.locator("#owned-applications li").count() == 25
            assert not page.locator("#next-applications").is_disabled()

            page.get_by_role("button", name="Next application page").click()
            page.get_by_text(
                "1 application on this page. End of list. Read-only snapshots."
            ).wait_for()
            assert page.locator("#owned-applications li").count() == 1
            assert "application-25" in page.locator("#owned-applications").text_content()

            _stop_server(runtime, server, thread)
            runtime = server = thread = None

            runtime, server, thread, restarted_url = _start_server(database_path)
            page.goto(restarted_url)
            page.locator("#token").fill("valid")
            _assert_tracker_view(page)
            assert database_path.read_bytes() == baseline
        finally:
            browser.close()
            if runtime is not None:
                _stop_server(runtime, server, thread)

    connection = sqlite3.connect(database_path)
    try:
        final_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert final_tables == initial_tables
    finally:
        connection.close()
    assert database_path.read_bytes() == baseline
