from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from threading import Thread
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

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


_CREATED_AT = datetime(2026, 11, 28, 10, 0, tzinfo=timezone.utc)
_START_AT = datetime(2026, 11, 30, 8, 0, tzinfo=timezone.utc)


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


def _event(
    event_id: str,
    candidate_id: str,
    *,
    status: ScheduleStatus,
    start_at: datetime,
    provider_event_id: str | None,
) -> ScheduleEvent:
    event = ScheduleEvent(
        event_id=event_id,
        candidate_id=candidate_id,
        employer_name=f"Employer {event_id}",
        event_type=ScheduleEventType.INTERVIEW,
        location="Karlsruhe, Germany",
        start_at=start_at,
        end_at=start_at + timedelta(hours=1),
        timezone_name="Europe/Berlin",
        application_id=f"private-application-{event_id}",
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )
    if status is ScheduleStatus.PROPOSED:
        return event
    return event.transition(
        status,
        provider_event_id=provider_event_id,
        occurred_at=_CREATED_AT + timedelta(seconds=1),
    )


def _seed_schedule(path):
    database = SQLiteDatabase(str(path))
    repository = SQLiteSchedulingRepository(database)

    for index in range(26):
        identifier = f"event-{index:02d}"
        repository.create(
            _event(
                identifier,
                "candidate-1",
                status=ScheduleStatus.ACCEPTED,
                start_at=_START_AT + timedelta(days=index),
                provider_event_id=(
                    None if index == 0 else f"private-provider-{identifier}"
                ),
            )
        )

    repository.create(
        _event(
            "event-proposed",
            "candidate-1",
            status=ScheduleStatus.PROPOSED,
            start_at=_START_AT + timedelta(days=40),
            provider_event_id=None,
        )
    )
    repository.create(
        _event(
            "event-foreign",
            "candidate-2",
            status=ScheduleStatus.ACCEPTED,
            start_at=_START_AT + timedelta(days=50),
            provider_event_id="private-provider-event-foreign",
        )
    )

    database.connection.execute(
        "UPDATE scheduling_events "
        "SET active_action_operation_id = 'private-operation-' || event_id "
        "WHERE event_id <> 'event-00'"
    )
    database.connection.execute(
        "UPDATE scheduling_events SET timezone_name = ? WHERE event_id = ?",
        ("private-corrupt-timezone", "event-foreign"),
    )
    database.connection.commit()
    tables = {
        row[0]
        for row in database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert "scheduling_events" in tables
    assert "external_action_operations" not in tables
    assert not any(name.startswith("sandbox_") for name in tables)
    database.close()
    return tables


def _advance_event_version(path):
    database = SQLiteDatabase(str(path))
    try:
        updated = SQLiteSchedulingRepository(database).attach_provider_event_id(
            "event-00",
            "private-provider-event-00",
            expected_version=2,
        )
        assert updated.version == 3
    finally:
        database.close()


def _start_server(database_path):
    runtime = RuntimeCandidateApp(
        database_path=str(database_path),
        jobs=(),
        resolve_bearer=lambda token: "candidate-1" if token == "valid" else None,
    )
    server = make_server(
        "127.0.0.1", 0, runtime, handler_class=_QuietHandler
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return (
        runtime,
        server,
        thread,
        f"http://127.0.0.1:{server.server_port}/sandbox/candidate",
    )


def _stop_server(runtime, server, thread):
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()
    runtime.close()


def _load_accepted_events(page):
    page.locator("#event-status").select_option("accepted")
    with page.expect_response(
        lambda response: "/v1/candidate/schedule-events?" in response.url
    ) as response_info:
        page.locator("#list-events").click()
    response = response_info.value
    assert response.status == 200
    page.get_by_text(
        "25 events on this page. More available. Read-only snapshots.",
        exact=True,
    ).wait_for()
    return response.json()


def test_candidate_schedule_browser_filters_paginates_and_recovers_read_only_after_restart(
    tmp_path,
):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    database_path = tmp_path / "candidate-schedule-browser.sqlite"
    initial_tables = _seed_schedule(database_path)

    runtime = server = thread = None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            runtime, server, thread, url = _start_server(database_path)
            page = browser.new_page()
            page.goto(url)
            page.locator("#token").fill("valid")

            first_page = _load_accepted_events(page)
            assert [item["event_id"] for item in first_page["items"]] == [
                f"event-{index:02d}" for index in range(25)
            ]
            assert first_page["items"][0]["state_version"] == 2
            assert first_page["next_after"] == "event-24"
            assert page.locator("#owned-events li").count() == 25
            assert "event-foreign" not in page.locator("body").text_content()
            assert "Private foreign employer" not in page.locator("body").text_content()

            page.locator("#next-events").click()
            page.get_by_text(
                "1 event on this page. End of list. Read-only snapshots.",
                exact=True,
            ).wait_for()
            assert page.locator("#owned-events li").count() == 1
            assert "event-25" in page.locator("#owned-events").text_content()
            assert page.locator("#next-events").is_disabled()

            page.locator("#event-status").select_option("proposed")
            page.locator("#list-events").click()
            page.get_by_text(
                "1 event on this page. End of list. Read-only snapshots.",
                exact=True,
            ).wait_for()
            assert page.locator("#owned-events li").count() == 1
            assert "event-proposed" in page.locator("#owned-events").text_content()

            first_page = _load_accepted_events(page)
            assert first_page["items"][0]["state_version"] == 2

            # Simulate a concurrent, legitimate event advance after the list snapshot.
            _advance_event_version(database_path)
            baseline = database_path.read_bytes()

            page.get_by_role(
                "button", name="View event 1: event-00"
            ).click()
            page.get_by_text("Read-only event detail.", exact=True).wait_for()
            detail = json.loads(page.locator("#event-detail").text_content())
            assert detail["event_id"] == "event-00"
            assert detail["status"] == "accepted"
            assert detail["state_version"] == 3
            assert "provider_event_id" not in detail
            assert "application_id" not in detail
            assert "active_action_operation_id" not in detail
            assert "private" not in json.dumps(detail)

            # A later response must not roll the selected event back below version 3.
            stale_detail = dict(detail)
            stale_detail["state_version"] = 2

            def fulfill_stale_detail(route):
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"schema_version": 1, "event": stale_detail}),
                )

            page.route(
                "**/v1/candidate/schedule-event?*",
                fulfill_stale_detail,
            )
            page.get_by_role(
                "button", name="View event 1: event-00"
            ).click()
            page.get_by_text(
                "Select the event again to retry.", exact=True
            ).wait_for()
            assert page.locator("#event-detail").text_content() == ""
            page.unroute("**/v1/candidate/schedule-event?*")

            _stop_server(runtime, server, thread)
            runtime = server = thread = None

            # Reopen the same durable file through a new runtime and WSGI server.
            runtime, server, thread, restarted_url = _start_server(database_path)
            page.goto(restarted_url)
            page.locator("#token").fill("valid")
            restarted_page = _load_accepted_events(page)
            assert restarted_page["items"][0]["state_version"] == 3
            page.get_by_role(
                "button", name="View event 1: event-00"
            ).click()
            page.get_by_text("Read-only event detail.", exact=True).wait_for()
            restarted_detail = json.loads(
                page.locator("#event-detail").text_content()
            )
            assert restarted_detail["state_version"] == 3
            assert restarted_detail["event_id"] == "event-00"

            foreign = page.evaluate(
                """async () => {
                  const response = await fetch(
                    '/v1/candidate/schedule-event?event_id=event-foreign',
                    {headers: {Authorization: 'Bearer ' +
                      document.getElementById('token').value}}
                  );
                  return {
                    status: response.status,
                    cacheControl: response.headers.get('Cache-Control')
                  };
                }"""
            )
            assert foreign == {"status": 404, "cacheControl": "no-store"}

            schedule = page.locator("section[aria-labelledby='schedule-title']")
            button_labels = schedule.locator("button").all_text_contents()
            assert button_labels
            assert all(
                label in {"Load my events", "Next event page", "View event"}
                for label in button_labels
            )
            body = page.locator("body").text_content()
            assert "No accept, decline, reschedule or calendar actions." in body
            assert "Private foreign employer" not in body
            assert "private-provider-" not in body
            assert "private-application-" not in body
            assert "private-operation-" not in body

            _stop_server(runtime, server, thread)
            runtime = server = thread = None
            assert database_path.read_bytes() == baseline
        finally:
            browser.close()
            if runtime is not None:
                _stop_server(runtime, server, thread)

    connection = sqlite3.connect(
        f"file:{database_path.as_posix()}?mode=ro",
        uri=True,
    )
    try:
        final_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        connection.close()
    assert final_tables == initial_tables
    assert database_path.read_bytes() == baseline
