"""Chromium race regressions for candidate interview-decline discovery."""

from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

from career_agent_ai.application.api import CandidateScheduleDeclineWSGIApp
from career_agent_ai.application.runtime.composition import build_candidate_sandbox_app_from_env
from career_agent_ai.application.scheduling import ScheduleEvent, ScheduleEventType, ScheduleStatus
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_sandbox_calendar_provider import SandboxCalendarOutcome
from career_agent_ai.application.storage.sqlite_scheduling_repository import SQLiteSchedulingRepository


OWNER = "candidate-1"
TOKENS = {"owner-token": OWNER}
ENV = {
    "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
    "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
}


def _event(event_id: str, index: int) -> ScheduleEvent:
    start = datetime(2026, 12, 8, 9, 0, tzinfo=timezone.utc) + timedelta(days=index)
    return ScheduleEvent(
        event_id=event_id,
        candidate_id=OWNER,
        employer_name="Sandbox GmbH",
        event_type=ScheduleEventType.INTERVIEW,
        location="Sandbox video room",
        start_at=start,
        end_at=start + timedelta(hours=1),
        timezone_name="Europe/Berlin",
        status=ScheduleStatus.PROPOSED,
        provider_event_id="fake-" + event_id,
        application_id="application-1",
        version=1,
        created_at=datetime(2026, 11, 20, tzinfo=timezone.utc),
        updated_at=datetime(2026, 11, 20, tzinfo=timezone.utc),
    )


def _seed(path, *events: ScheduleEvent) -> None:
    database = SQLiteDatabase(str(path))
    try:
        repository = SQLiteSchedulingRepository(database)
        for event in events:
            repository.create(event)
    finally:
        database.close()


def _call(app, route: str, *, method: str, query: str = "", body=None):
    encoded = json.dumps({} if body is None else body).encode("utf-8")
    statuses = []
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": route,
        "QUERY_STRING": query,
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(encoded)),
        "HTTP_AUTHORIZATION": "Bearer owner-token",
        "wsgi.input": io.BytesIO(encoded),
    }
    payload = b"".join(
        app(environ, lambda status, headers: statuses.append(status))
    )
    return statuses[0], json.loads(payload)


def _provider_counts(path):
    with sqlite3.connect(path) as connection:
        attempts = connection.execute(
            "SELECT COUNT(*) FROM sandbox_calendar_attempts"
        ).fetchone()[0]
        effects = connection.execute(
            "SELECT COUNT(*) FROM sandbox_calendar_effects"
        ).fetchone()[0]
    return attempts, effects


def test_candidate_decline_discovery_ignores_delayed_second_page_after_selection(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    path = tmp_path / "candidate-decline-delayed-page.sqlite"
    events = tuple(
        _event("decline-delay-" + f"{index:03d}", index)
        for index in range(26)
    )
    _seed(path, *events)

    def timeout_after_effect(_key, _attempt):
        return SandboxCalendarOutcome.TIMEOUT_AFTER_EFFECT

    first = CandidateScheduleDeclineWSGIApp(
        database_path=str(path), resolve_bearer=TOKENS.get, outcome_plan=timeout_after_effect
    )
    for event in events:
        status, result = _call(
            first,
            CandidateScheduleDeclineWSGIApp.DECLINE_PATH,
            method="POST",
            body={
                "schema_version": 1,
                "event_id": event.event_id,
                "state_version": 1,
                "decision": "decline",
            },
        )
        assert status == "202 Accepted"
        assert result["outcome"] == "reconciliation_required"

    baseline_counts = _provider_counts(path)
    assert baseline_counts == (len(events), len(events))

    def unexpected_retry(*_args):
        raise AssertionError("discovery and event selection must not replay the calendar provider")

    runtime = build_candidate_sandbox_app_from_env(
        resolve_bearer=TOKENS.get,
        environ={"CAREER_AGENT_DB_PATH": str(path), **ENV},
    )
    decline = CandidateScheduleDeclineWSGIApp(
        database_path=str(path), resolve_bearer=TOKENS.get, outcome_plan=unexpected_retry
    )

    page_two_started = Event()
    release_page_two = Event()

    def dispatch(environ, start_response):
        if environ.get("PATH_INFO") in CandidateScheduleDeclineWSGIApp.ROUTES:
            return decline(environ, start_response)
        return runtime(environ, start_response)

    def delayed_dispatch(environ, start_response):
        is_page_two = (
            environ.get("PATH_INFO") == CandidateScheduleDeclineWSGIApp.PENDING_COLLECTION_PATH
            and "after=" in environ.get("QUERY_STRING", "")
        )
        if is_page_two:
            page_two_started.set()
            if not release_page_two.wait(timeout=10):
                raise RuntimeError("Timed out waiting to release delayed decline discovery page.")
        return dispatch(environ, start_response)

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, format, *args):
            pass

    server = make_server(
        "127.0.0.1", 0, delayed_dispatch, handler_class=QuietHandler
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{server.server_port}/sandbox/candidate")
                page.evaluate(
                    """() => {
                      const originalFetch = window.fetch.bind(window);
                      window.__delayedDeclinePageConsumed = false;
                      window.fetch = async (...args) => {
                        const response = await originalFetch(...args);
                        const input = args[0];
                        const url = typeof input === 'string' ? input : input.url;
                        if (url.includes('/v1/candidate/schedule-event/decline-intents') &&
                            url.includes('after=')) {
                          await response.clone().arrayBuffer();
                          window.__delayedDeclinePageConsumed = true;
                        }
                        return response;
                      };
                    }"""
                )
                page.locator("#token").fill("owner-token")
                page.locator("#load-decline-intents").click()

                first_button = "Recover pending decline 1: " + events[0].event_id
                last_button = "Recover pending decline 25: " + events[24].event_id
                page.get_by_role("button", name=last_button).wait_for()
                buttons = page.locator("#pending-decline-intents button")
                assert buttons.count() == 25

                next_page = page.locator("#next-decline-intents")
                assert not next_page.is_disabled()
                next_page.click()
                assert page_two_started.wait(timeout=5)

                # Selection must invalidate the whole collection before page two returns.
                page.get_by_role("button", name=first_button).click()
                assert buttons.count() == 0
                assert next_page.is_disabled()
                assert page.locator("#load-decline-intents").is_enabled()

                release_page_two.set()
                page.wait_for_function(
                    "() => window.__delayedDeclinePageConsumed === true"
                )
                page.locator("#decline-reconciliation-state").get_by_text(
                    "An ambiguous interview decline was restored from durable storage",
                    exact=False,
                ).wait_for()

                # The late page cannot restore rows/cursor or poison a fresh discovery.
                assert buttons.count() == 0
                assert next_page.is_disabled()
                page.locator("#load-decline-intents").click()
                page.get_by_role("button", name=last_button).wait_for()
                assert buttons.count() == 25
                assert not next_page.is_disabled()
            finally:
                browser.close()
    finally:
        release_page_two.set()
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
        runtime.close()

    assert _provider_counts(path) == baseline_counts
