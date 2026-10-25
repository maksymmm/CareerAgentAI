from __future__ import annotations

import io
import json
import logging
import socket
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Thread
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest
import career_agent_ai.application.runtime.candidate_service as candidate_service
import career_agent_ai.application.runtime.composition as runtime_composition
from career_agent_ai.application.api.candidate_sandbox_ui import candidate_sandbox_html
from career_agent_ai.application.api import (
    OperationalApiService,
    OperationalWSGIApp,
    openapi_document,
)
from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopPhase,
    CareerLoopRequest,
    CareerLoopState,
    HumanActionEvent,
    HumanActionKind,
)
from career_agent_ai.application.external_actions import (
    ExternalActionOperation,
    ExternalActionStatus,
)
from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.observability import (
    JsonLogFormatter,
    OperationalSeverity,
    correlation_scope,
    current_correlation_id,
    log_event,
    redact_mapping,
)
from career_agent_ai.application.runtime import (
    CandidateServiceConfig,
    CandidateServiceRuntime,
    CandidateServiceWSGIApp,
    RuntimeConfig,
    RuntimeEnvironment,
    build_candidate_sandbox_app_from_env,
    build_candidate_service_from_env,
    build_operational_app_from_env,
    build_external_action_service,
    guard_job_provider,
    guard_signal_provider,
)
from career_agent_ai.application.storage.sqlite_career_loop_repository import (
    SQLiteCareerLoopRepository,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import (
    SQLiteExternalActionOperationRepository,
)
from career_agent_ai.application.storage.sqlite_migrations import (
    SQLiteMigration,
    SQLiteMigrationRunner,
)
from career_agent_ai.application.storage.sqlite_operational_probe import (
    SQLiteOperationalProbe,
)


NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
PROFILE = "Experienced logistics professional."


def test_candidate_dashboard_narrow_layout_preserves_long_text():
    """Long snapshots and IDs remain readable without horizontal page scrolling."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    long_text = json.dumps({"snapshot": "x" * 2000}, indent=2)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(candidate_sandbox_html().decode("utf-8"))
        page.evaluate("""text => {
            document.querySelectorAll('pre').forEach(node => node.textContent = text);
            const row = document.createElement('li');
            const button = document.createElement('button');
            button.textContent = 'r'.repeat(200);
            row.append(button); document.getElementById('owned-runs').append(row);
        }""", long_text)
        for width in (320, 390, 1280):
            page.set_viewport_size({"width": width, "height": 800})
            assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
            for pane in page.locator("pre").all():
                assert pane.text_content() == long_text
                assert pane.evaluate("node => node.scrollWidth <= node.clientWidth")
            for button in page.locator("button").all():
                bounds = button.bounding_box()
                assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
            columns = page.locator(".actions").first.evaluate(
                "node => getComputedStyle(node).gridTemplateColumns.split(' ').length")
            assert columns == (1 if width < 512 else 2)
        browser.close()


def test_candidate_dashboard_read_only_output_regions_are_named():
    """Every read-only detail pane exposes its surrounding context to AT."""
    html = candidate_sandbox_html().decode("utf-8")

    for region_id, title_id, title in (
        ("application-detail", "application-detail-title", "Application detail"),
        ("application-history", "application-history-title", "Application history"),
        ("event-detail", "event-detail-title", "Event detail"),
        ("result", "result-title", "Result"),
    ):
        assert f'id="{title_id}"' in html
        assert f'>{title}</' in html
        assert (
            f'id="{region_id}" role="region" aria-labelledby="{title_id}"'
            in html
        )
        if region_id != "result":
            assert f'aria-labelledby="{title_id}" aria-busy="false"' in html


def test_candidate_dashboard_collection_regions_expose_initial_loading_state():
    """Owned collections are named and start outside an async update."""
    html = candidate_sandbox_html().decode("utf-8")

    for region_id, label in (
        ("owned-runs", "My runs"),
        ("owned-applications", "My applications"),
        ("owned-events", "My events"),
    ):
        assert (
            f'id="{region_id}" aria-label="{label}" aria-busy="false"'
            in html
        )


def request() -> CareerLoopRequest:
    return CareerLoopRequest(
        user_id="user-1",
        keyword="logistics",
        candidate_profile=PROFILE,
    )


def test_candidate_owned_run_pages_are_read_only_and_restart_safe(tmp_path):
    from dataclasses import replace
    from career_agent_ai.application.career.autonomous_loop_models import CareerLoopState
    from career_agent_ai.application.storage.sqlite_career_loop_repository import SQLiteCareerLoopRepository
    from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
    path = tmp_path / "pages.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteCareerLoopRepository(database)
    for identifier, owner, phase in (
        ("a", "user-1", CareerLoopPhase.SEARCH),
        ("b", "foreign", CareerLoopPhase.SEARCH),
        ("c", "user-1", CareerLoopPhase.COMPLETE),
        ("d", "user-1", CareerLoopPhase.SEARCH),
        ("e", "user-1", CareerLoopPhase.SEARCH),
    ):
        repository.save(CareerLoopState(
            run_id=identifier, request=replace(request(), user_id=owner), phase=phase))
    database.connection.execute("UPDATE autonomous_career_loops SET payload_json = 'not-json' WHERE run_id = 'b'")
    database.connection.commit()
    database.close()
    before = path.read_bytes()
    with RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: "user-1") as runtime:
        from career_agent_ai.tests.test_candidate_approval_api import request as api_request
        (status, _), collection = api_request(runtime, path="/v1/candidate/run-statuses", query="limit=2")
        assert status == "200 OK"
        assert [item["run_id"] for item in collection["items"]] == ["a", "c"]
        first = runtime.list_run_statuses(user_id="user-1", limit=2)
        assert [item["run_id"] for item in first["items"]] == ["a", "c"]
        assert first["next_after"] == "c"
        assert set(first["items"][0]) == {"schema_version", "run_id", "state_version", "phase", "waiting_for_human", "terminal"}
        assert runtime.list_run_statuses(user_id="nobody")["items"] == []
        filtered = runtime.list_run_statuses(
            user_id="user-1", limit=2, phase=CareerLoopPhase.SEARCH)
        assert [item["run_id"] for item in filtered["items"]] == ["a", "d"]
        assert filtered["next_after"] == "d"
        assert runtime.list_run_statuses(
            user_id="user-1", phase=CareerLoopPhase.COMPLETE)["items"][0]["run_id"] == "c"
        with pytest.raises(TypeError):
            runtime.list_run_statuses(user_id="user-1", phase="search")
        for invalid in (True, 0, 101, "2"):
            with pytest.raises(ValueError):
                runtime.list_run_statuses(user_id="user-1", limit=invalid)
    with pytest.raises(RuntimeError):
        runtime.list_run_statuses(user_id="user-1")
    with RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: None) as restarted:
        page = restarted.list_run_statuses(user_id="user-1", limit=2, after=first["next_after"])
        assert [item["run_id"] for item in page["items"]] == ["d", "e"]
        assert page["next_after"] is None
        assert restarted.list_run_statuses(user_id="user-1", after="b")["items"][0]["run_id"] == "c"
        filtered_page = restarted.list_run_statuses(
            user_id="user-1", limit=2, after=filtered["next_after"], phase=CareerLoopPhase.SEARCH)
        assert [item["run_id"] for item in filtered_page["items"]] == ["e"]
        with pytest.raises(ValueError):
            restarted.list_run_statuses(user_id="foreign")
    assert path.read_bytes() == before


def test_candidate_run_phase_projection_is_backfilled_and_indexed(tmp_path):
    from career_agent_ai.application.storage.sqlite_career_loop_repository import SQLiteCareerLoopRepository
    path = tmp_path / "run-phase-index.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteCareerLoopRepository(database)
    repository.save(CareerLoopState(run_id="legacy", request=request()))
    database.connection.execute("DROP INDEX idx_autonomous_loop_owner_phase")
    database.connection.execute("ALTER TABLE autonomous_career_loops RENAME COLUMN phase TO legacy_phase")
    database.connection.commit()
    database.close()

    database = SQLiteDatabase(str(path))
    SQLiteCareerLoopRepository(database)
    assert database.connection.execute(
        "SELECT phase FROM autonomous_career_loops WHERE run_id = 'legacy'"
    ).fetchone()[0] == CareerLoopPhase.SEARCH.value
    # Simulate interruption after ALTER TABLE committed but before backfill.
    database.connection.execute(
        "UPDATE autonomous_career_loops SET phase = NULL WHERE run_id = 'legacy'"
    )
    database.connection.commit()
    database.close()
    database = SQLiteDatabase(str(path))
    SQLiteCareerLoopRepository(database)
    assert database.connection.execute(
        "SELECT phase FROM autonomous_career_loops WHERE run_id = 'legacy'"
    ).fetchone()[0] == CareerLoopPhase.SEARCH.value
    columns = database.connection.execute(
        "PRAGMA index_info(idx_autonomous_loop_owner_phase)"
    ).fetchall()
    assert [row[2] for row in columns] == ["owner_user_id", "phase", "run_id"]
    plan = database.connection.execute(
        "EXPLAIN QUERY PLAN SELECT run_id FROM autonomous_career_loops "
        "WHERE owner_user_id = ? AND phase = ? AND run_id > ? ORDER BY run_id LIMIT ?",
        ("owner", "search", "cursor", 26),
    ).fetchall()
    details = " ".join(row[3] for row in plan)
    assert "idx_autonomous_loop_owner_phase" in details
    assert "TEMP B-TREE" not in details
    database.close()


def test_candidate_run_owner_projection_resumes_after_interrupted_backfill(tmp_path):
    """Startup repairs a durable owner column left NULL by an interrupted migration."""
    from career_agent_ai.application.storage.sqlite_career_loop_repository import (
        SQLiteCandidateRunStatusProjection,
    )

    path = tmp_path / "run-owner-restart.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteCareerLoopRepository(database)
    state = CareerLoopState(run_id="legacy", request=request())
    repository.save(state)

    # Reproduce a process exit after ALTER TABLE committed but before its
    # payload-derived owner projection was persisted.
    database.connection.execute(
        "UPDATE autonomous_career_loops SET owner_user_id = NULL WHERE run_id = ?",
        (state.run_id,),
    )
    database.connection.commit()
    database.close()

    database = SQLiteDatabase(str(path))
    repository = SQLiteCareerLoopRepository(database)
    assert database.connection.execute(
        "SELECT owner_user_id FROM autonomous_career_loops WHERE run_id = ?",
        (state.run_id,),
    ).fetchone()[0] == state.request.user_id
    page = SQLiteCandidateRunStatusProjection(database).list_page(
        user_id=state.request.user_id
    )
    assert [item["run_id"] for item in page["items"]] == [state.run_id]

    loaded = repository.get(state.run_id)
    assert loaded is not None
    previous_version = loaded.version
    repository.save(loaded)
    assert repository.get(state.run_id).version == previous_version + 1
    database.close()


def test_candidate_filtered_run_page_reads_each_snapshot_in_one_query(tmp_path):
    from career_agent_ai.application.career.autonomous_loop_models import CareerLoopState
    from career_agent_ai.application.storage.sqlite_career_loop_repository import (
        SQLiteCandidateRunStatusProjection,
        SQLiteCareerLoopRepository,
    )
    path = tmp_path / "run-phase-snapshot.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteCareerLoopRepository(database)
    repository.save(CareerLoopState(run_id="run-1", request=request()))
    database.close()

    database = SQLiteDatabase.open_read_only(str(path))
    statements = []
    database.connection.set_trace_callback(statements.append)
    page = SQLiteCandidateRunStatusProjection(database).list_page(
        user_id="user-1", phase=CareerLoopPhase.SEARCH)
    database.connection.set_trace_callback(None)
    database.close()
    assert [item["run_id"] for item in page["items"]] == ["run-1"]
    reads = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
    assert len(reads) == 1
    assert "payload_json" in reads[0]


def test_candidate_run_listing_does_not_create_missing_storage(tmp_path):
    from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
    path = tmp_path / "missing.sqlite"
    with RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: None) as runtime:
        with pytest.raises(Exception):
            runtime.list_run_statuses(user_id="user-1")
    assert not path.exists()


def test_runtime_config_defaults_are_safe():
    config = RuntimeConfig.from_env({})

    assert config.environment == RuntimeEnvironment.DEVELOPMENT
    assert config.database_path == ":memory:"
    assert config.log_level == logging.INFO
    assert config.allow_network_providers is False
    assert config.allow_consequential_actions is False


def test_candidate_sandbox_requires_explicit_safe_runtime_flags(tmp_path):
    base = {
        "CAREER_AGENT_DB_PATH": str(tmp_path / "candidate.sqlite"),
        "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
        "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
    }
    with pytest.raises(PermissionError, match="explicitly enabled"):
        build_candidate_sandbox_app_from_env(
            resolve_bearer=lambda token: token, environ={}
        )
    with pytest.raises(PermissionError, match="cutover gate"):
        build_candidate_sandbox_app_from_env(
            resolve_bearer=lambda token: token,
            environ={**base, "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-v1"},
        )
    with pytest.raises(PermissionError, match="cutover gate"):
        build_candidate_sandbox_app_from_env(
            resolve_bearer=lambda token: token,
            environ={
                "CAREER_AGENT_DB_PATH": str(tmp_path / "candidate.sqlite"),
                "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
            },
        )
    for field in (
        "CAREER_AGENT_ALLOW_NETWORK_PROVIDERS",
        "CAREER_AGENT_ALLOW_CONSEQUENTIAL_ACTIONS",
    ):
        with pytest.raises(ValueError, match="forbids"):
            build_candidate_sandbox_app_from_env(
                resolve_bearer=lambda token: token,
                environ={**base, field: "true"},
            )
    with pytest.raises(TypeError, match="resolve_bearer"):
        build_candidate_sandbox_app_from_env(
            resolve_bearer=None,  # type: ignore[arg-type]
            environ=base,
        )
    with pytest.raises(ValueError, match="durable SQLite"):
        build_candidate_sandbox_app_from_env(
            resolve_bearer=lambda token: token,
            environ={
                "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
                "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
            },
        )


def test_candidate_sandbox_composes_durable_loop_and_no_io_adapters(tmp_path):
    app = build_candidate_sandbox_app_from_env(
        resolve_bearer=lambda token: "user-1" if token == "sandbox-token" else None,
        jobs=(
            Job.create(
                job_id="job-1",
                title="Logistics Coordinator",
                company=Company("company-1", "Acme"),
                location=Location("Germany", "Karlsruhe"),
                description="logistics coordination",
                created_at=NOW,
            ),
        ),
        environ={
            "CAREER_AGENT_DB_PATH": str(tmp_path / "candidate.sqlite"),
            "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
            "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
        },
    )
    start_body = json.dumps({
        "schema_version": 1,
        "run_id": "candidate-run",
        "keyword": "logistics",
        "candidate_profile": PROFILE,
    }).encode()
    start_statuses = []
    start_payload = json.loads(b"".join(app(
        {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/v1/candidate/runs",
            "HTTP_AUTHORIZATION": "Bearer sandbox-token",
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": str(len(start_body)),
            "wsgi.input": io.BytesIO(start_body),
        },
        lambda status, headers: start_statuses.append(status),
    )))
    assert start_statuses == ["201 Created"]
    durable_run_id = start_payload["run_id"]
    assert durable_run_id != "candidate-run"
    assert durable_run_id.startswith("scoped-v2:")
    assert len(durable_run_id) == 138
    assert start_payload["phase"] == CareerLoopPhase.APPLICATION_APPROVAL.value

    programmatic_replay = app.start(request(), run_id="candidate-run")
    assert programmatic_replay.run_id == durable_run_id
    assert programmatic_replay.phase == CareerLoopPhase.APPLICATION_APPROVAL

    statuses = []
    payload = json.loads(
        b"".join(
            app(
                {
                    "REQUEST_METHOD": "GET",
                    "PATH_INFO": f"/v1/candidate/runs/{durable_run_id}/approval",
                    "HTTP_AUTHORIZATION": "Bearer sandbox-token",
                },
                lambda status, headers: statuses.append(status),
            )
        )
    )
    assert statuses == ["200 OK"]
    assert payload["run_id"] == durable_run_id
    assert payload["action_kind"] == "approve_application"

    def threaded_get() -> str:
        threaded_statuses = []
        b"".join(
            app(
                {
                    "REQUEST_METHOD": "GET",
                    "PATH_INFO": f"/v1/candidate/runs/{durable_run_id}/approval",
                    "HTTP_AUTHORIZATION": "Bearer sandbox-token",
                },
                lambda status, headers: threaded_statuses.append(status),
            )
        )
        return threaded_statuses[0]

    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(threaded_get).result() == "200 OK"

    app.close()
    app.close()
    with pytest.raises(RuntimeError, match="closed"):
        app({}, lambda status, headers: None)
    with pytest.raises(RuntimeError, match="closed"):
        app.__enter__()
    with pytest.raises(RuntimeError, match="closed"):
        app.start(request())


def test_candidate_schedule_browser_read_only_restart_and_privacy(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from career_agent_ai.application.scheduling.models import ScheduleEvent, ScheduleEventType
    from career_agent_ai.application.storage.sqlite_scheduling_repository import SQLiteSchedulingRepository
    from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
    path = tmp_path / "browser-events.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteSchedulingRepository(database)
    for index in range(28):
        repository.create(ScheduleEvent(event_id=f"event-{index:03}", candidate_id="owner" if index < 27 else "other",
            employer_name="<b>Employer</b>", event_type=ScheduleEventType.INTERVIEW, location="Karlsruhe",
            start_at=datetime(2026, 10, 25, 0, 30, 0, 123456, tzinfo=timezone.utc),
            end_at=datetime(2026, 10, 25, 2, 30, 0, 123456, tzinfo=timezone.utc), timezone_name="Europe/Berlin"))
    database.connection.execute("UPDATE scheduling_events SET provider_event_id='private-provider-'||event_id, application_id='private-app'")
    database.connection.commit()
    database.close()
    baseline = path.read_bytes()
    app = runtime_composition.RuntimeCandidateApp(database_path=str(path), jobs=(),
        resolve_bearer=lambda token: "owner" if token == "sandbox-token" else None)
    app.close()
    app = runtime_composition.RuntimeCandidateApp(database_path=str(path), jobs=(),
        resolve_bearer=lambda token: "owner" if token == "sandbox-token" else None)
    class QuietHandler(WSGIRequestHandler):
        def log_message(self, format, *args):
            pass
    server = make_server("127.0.0.1", 0, app, handler_class=QuietHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/sandbox/candidate")
            page.locator("#token").fill("sandbox-token")
            page.locator("#list-events").click()
            page.locator("#events-state").get_by_text(
                "25 events on this page. More available. Read-only snapshots.", exact=True
            ).wait_for()
            assert page.locator("#owned-events button").count() == 25
            assert page.get_by_role(
                "button", name="View event 1: event-000", exact=True
            ).count() == 1
            assert page.get_by_role(
                "button", name="View event 25: event-024", exact=True
            ).count() == 1
            assert page.locator("#owned-events b").count() == 0
            page.route("**/v1/candidate/schedule-events?*after=*", lambda route: route.fulfill(status=503, json={"message": "private-page-secret"}))
            page.locator("#next-events").click()
            page.locator("#events-state").get_by_text("Load or retry the event page.").wait_for()
            page.locator("#result").get_by_text(
                "Schedule events request failed.", exact=False
            ).wait_for()
            assert page.locator("#next-events").is_enabled()
            assert "private-page-secret" not in page.locator("body").text_content()
            page.unroute("**/v1/candidate/schedule-events?*after=*")
            page.locator("#next-events").click()
            page.locator("#events-state").get_by_text(
                "2 events on this page. End of list. Read-only snapshots.", exact=True
            ).wait_for()
            assert page.locator("#owned-events button").count() == 2
            assert page.get_by_role(
                "button", name="View event 1: event-025", exact=True
            ).count() == 1
            assert page.get_by_role(
                "button", name="View event 2: event-026", exact=True
            ).count() == 1
            assert page.locator("#next-events").is_disabled()
            page.locator("#owned-events button").first.click()
            page.locator("#event-detail-state").get_by_text("Read-only event detail.").wait_for()
            detail = json.loads(page.locator("#event-detail").text_content())
            assert detail["event_id"] == "event-025" and detail["local_start_time"] == "02:30:00.123456"
            assert detail["utc_offset"] == "+02:00" and detail["end_utc_offset"] == "+01:00"
            assert "private" not in page.locator("body").text_content()
            def expanded_detail(route):
                payload = route.fetch().json()
                payload["private"] = "private-top-secret"
                payload["event"]["private"] = "private-nested-secret"
                route.fulfill(status=200, json=payload)
            page.route("**/v1/candidate/schedule-event?*", expanded_detail)
            page.locator("#owned-events button").first.click()
            page.locator("#event-detail-state").get_by_text("Read-only event detail.").wait_for()
            assert "private" not in page.locator("body").text_content()
            page.unroute("**/v1/candidate/schedule-event?*")
            for replacement in ({"event_id": "wrong"}, {"state_version": True}, {"status": "invalid"}, {"utc_offset": "bad"},
                                {"local_date": "2026-02-30"}, {"local_date": "1900-02-29"}, {"local_date": "0000-01-01"},
                                {"local_end_date": "2026-13-01"}, {"local_start_time": "25:00:00"},
                                {"local_end_time": "12:60:00"}, {"utc_offset": "+99:99:99"},
                                {"end_utc_offset": "-00:00:60"}, {"start_at": "2026-10-25"},
                                {"start_at": "2026-02-30T00:00:00+00:00"}, {"end_at": "2026-10-25T12:00:00"},
                                {"start_at": "2026-10-25T00:00:00.123+00:00"},
                                {"local_start_time": "03:30:00.123456"}, {"utc_offset": "+01:00"},
                                {"timezone_name": "UTC"}, {"timezone_name": "Unknown/Zone"},
                                {"timezone_name": "+02:00"},
                                {"local_date": "2026-10-26"}, {"local_end_time": "04:30:00.123457"},
                                {"end_at": "2026-10-25T00:30:00.123456+00:00", "local_end_date": "2026-10-25",
                                 "local_end_time": "02:30:00.123456", "end_utc_offset": "+02:00"},
                                {"end_at": "2026-10-25T00:30:00.123455+00:00", "local_end_date": "2026-10-25",
                                 "local_end_time": "02:30:00.123455", "end_utc_offset": "+02:00"}):
                def malformed_detail(route):
                    payload = route.fetch().json()
                    payload["event"].update(replacement)
                    route.fulfill(status=200, json=payload)
                page.route("**/v1/candidate/schedule-event?*", malformed_detail)
                page.locator("#owned-events button").first.click()
                page.locator("#event-detail-state").get_by_text("Select the event again to retry.").wait_for()
                assert page.locator("#event-detail").text_content() == ""
                page.unroute("**/v1/candidate/schedule-event?*")
            for date, zone, offset, local_day, local_time in [
                ("2000-02-29", "UTC", "+00:00", "2000-02-29", "00:30:00.123456"),
                ("1900-01-01", "Africa/Monrovia", "-00:43:08", "1899-12-31", "23:46:52.123456")]:
                def valid_precision_detail(route):
                    payload = route.fetch().json()
                    payload["event"].update(start_at=date + "T00:30:00.123456+00:00", local_date=local_day,
                        local_start_time=local_time, timezone_name=zone, utc_offset=offset,
                        end_at=None, local_end_date=None, local_end_time=None, end_utc_offset=None)
                    if date == "2000-02-29":
                        payload["event"].update(end_at=date + "T00:30:00.123457+00:00", local_end_date=date,
                            local_end_time="00:30:00.123457", end_utc_offset="+00:00")
                    route.fulfill(status=200, json=payload)
                page.route("**/v1/candidate/schedule-event?*", valid_precision_detail)
                page.locator("#owned-events button").first.click()
                page.locator("#event-detail-state").get_by_text("Read-only event detail.").wait_for()
                assert json.loads(page.locator("#event-detail").text_content())["utc_offset"] == offset
                page.unroute("**/v1/candidate/schedule-event?*")
            page.route("**/v1/candidate/schedule-event?*", lambda route: route.fulfill(status=200, content_type="application/json", body="private-malformed-secret"))
            page.locator("#owned-events button").first.click()
            page.locator("#event-detail-state").get_by_text("Select the event again to retry.").wait_for()
            page.locator("#result").get_by_text("Event detail request failed.", exact=False).wait_for()
            assert "private-malformed-secret" not in page.locator("body").text_content()
            page.unroute("**/v1/candidate/schedule-event?*")
            page.evaluate("""() => {
              window.originalEventFetch = window.fetch;
              window.fetch = async (...args) => {
                const response = await window.originalEventFetch(...args);
                if (String(args[0]).includes('/schedule-event?')) {
                  document.body.dataset.eventDetailRequests = String(
                    Number(document.body.dataset.eventDetailRequests || 0) + 1);
                  document.body.dataset.eventWaiting = 'yes';
                  await new Promise(resolve => { window.releaseEvent = resolve; });
                  document.body.dataset.eventReleased = 'yes';
                }
                return response;
              };
            }""")
            page.locator("#owned-events button").first.click()
            page.locator('body[data-event-waiting="yes"]').wait_for()
            assert page.locator("#owned-events button").first.is_disabled()
            assert page.locator("#event-detail").get_attribute("aria-busy") == "true"
            page.evaluate("document.querySelector('#owned-events button').click()")
            assert page.locator("body").get_attribute("data-event-detail-requests") == "1"
            page.locator("#profile").fill("private-profile-secret")
            page.locator("#keyword").fill("private-keyword")
            page.locator("#location").fill("private-location")
            page.locator("#start-run-id").fill("private-start")
            requests = []
            page.on("request", lambda request: requests.append(request.url))
            page.locator("#clear-session").click()
            page.locator("#result").get_by_text("Session cleared from this tab.", exact=False).wait_for()
            assert requests == []
            for field in ("token", "profile", "keyword", "location", "run-id", "start-run-id"):
                assert page.locator("#" + field).input_value() == ""
            assert page.locator("#approve").is_disabled() and page.locator("#decline").is_disabled()
            assert page.locator("#owned-runs li").count() == 0
            assert page.locator("#owned-applications li").count() == 0
            assert page.locator("#application-detail").text_content() == ""
            assert page.locator("#application-history").text_content() == ""
            page.evaluate("window.releaseEvent(); window.fetch = window.originalEventFetch")
            page.locator('body[data-event-released="yes"]').wait_for()
            assert page.locator("#event-detail").text_content() == ""
            assert page.locator("#event-detail").get_attribute("aria-busy") == "false"
            assert page.locator("#owned-events li").count() == 0
            assert page.locator("#next-events").is_disabled()
            assert "private-profile-secret" not in page.locator("body").text_content()
            page.locator("#token").fill("sandbox-token")
            page.route("**/v1/candidate/schedule-events?*", lambda route: route.fulfill(status=200, json={"schema_version": 99, "items": [], "next_after": None, "private": "private-page-payload"}))
            page.locator("#list-events").click()
            page.locator("#events-state").get_by_text("Load or retry the event page.").wait_for()
            assert "private-page-payload" not in page.locator("body").text_content()
            page.unroute("**/v1/candidate/schedule-events?*")
            page.locator("#list-events").click()
            page.locator("#events-state").get_by_text(
                "25 events on this page. More available. Read-only snapshots.", exact=True
            ).wait_for()
            assert page.locator("#approve").is_disabled()
            browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2); app.close()
    assert path.read_bytes() == baseline


def test_candidate_sandbox_browser_uses_durable_safe_composition(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    database_path = str(tmp_path / "browser-candidate.sqlite")
    environ = {
        "CAREER_AGENT_DB_PATH": database_path,
        "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
        "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
    }
    jobs = (
        Job.create(
            job_id="browser-job",
            title="Logistics Coordinator",
            company=Company("browser-company", "Sandbox Logistics"),
            location=Location("Germany", "Karlsruhe"),
            description="logistics coordination",
            created_at=NOW,
        ),
    )
    app = build_candidate_sandbox_app_from_env(
        resolve_bearer=lambda token: "browser-user" if token == "sandbox-token" else None,
        jobs=jobs,
        environ=environ,
    )

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, format, *args):
            pass

    server = make_server("127.0.0.1", 0, app, handler_class=QuietHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    durable_run_id = ""
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/sandbox/candidate")
            page.locator("#token").fill("sandbox-token")
            page.locator("#start-run-id").fill("browser-composed-run")
            page.locator("#keyword").fill("logistics")
            page.locator("#location").fill("Karlsruhe")
            page.locator("#profile").fill(PROFILE)
            page.locator("#start").click()
            page.locator("#result").get_by_text('"phase": "application_approval"').wait_for()
            durable_run_id = page.locator("#run-id").input_value()
            assert durable_run_id.startswith("scoped-v2:")
            page.locator("#status").click()
            page.locator("#run-overview").get_by_text("Your decision is needed", exact=False).wait_for()

            for replacement in ({"run_id": "other-owned-run"}, {"schema_version": 9},
                                {"state_version": True}, {"state_version": -1},
                                {"action_kind": "reconcile_application_submission"}, {"title": ""},
                                {"details": []}, {"action_fingerprint": "invalid"},
                                {"allowed_decisions": ["approve", "approve"]}, {"extra": "private-prompt-secret"}):
                def malformed_prompt(route):
                    payload = route.fetch().json()
                    payload.update(replacement)
                    route.fulfill(status=200, json=payload)
                page.route("**/v1/candidate/runs/*/approval", malformed_prompt)
                page.locator("#prompt").click()
                page.locator("#action-title").get_by_text("Approval prompt unavailable. Load it again.").wait_for()
                assert page.locator("#approve").is_disabled() and page.locator("#decline").is_disabled()
                assert page.locator("#action-details").text_content() == ""
                assert "other-owned-run" not in page.locator("body").text_content()
                assert "private-prompt-secret" not in page.locator("body").text_content()
                assert page.locator("#run-id").input_value() == durable_run_id
                page.unroute("**/v1/candidate/runs/*/approval")
            page.route("**/v1/candidate/runs/*/approval", lambda route: route.fulfill(
                status=200, content_type="application/json", body="private-prompt-json-secret"))
            page.locator("#prompt").click()
            page.locator("#action-title").get_by_text("Approval prompt unavailable. Load it again.").wait_for()
            page.locator("#result").get_by_text("Approval prompt request failed.", exact=False).wait_for()
            assert "private-prompt-json-secret" not in page.locator("body").text_content()
            page.unroute("**/v1/candidate/runs/*/approval")
            page.locator("#prompt").click()
            page.locator("#action-title").get_by_text("Approve job application").wait_for()
            page.locator("#approve").click()
            page.locator("#result").get_by_text('"phase": "complete"').wait_for()
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        app.close()

    operations_database = SQLiteDatabase(database_path)
    try:
        persisted_operations = operations_database.connection.execute(
            """
            SELECT action_type, status, result_json
            FROM external_action_operations
            ORDER BY operation_id
            """
        ).fetchall()
    finally:
        operations_database.close()

    assert len(persisted_operations) == 1
    action_type, status, result_json = persisted_operations[0]
    assert action_type == "application.submit"
    assert status == ExternalActionStatus.SUCCEEDED.value
    assert json.loads(result_json)["provider_submission_id"].startswith("fake:")

    from dataclasses import replace
    seed_database = SQLiteDatabase(database_path)
    seed_repository = SQLiteCareerLoopRepository(seed_database)
    for index in range(26):
        seed_repository.save(CareerLoopState(run_id=f"a{index:02}",
            request=replace(request(), user_id="browser-user")))
    seed_database.close()
    restarted = build_candidate_sandbox_app_from_env(
        resolve_bearer=lambda token: "browser-user" if token == "sandbox-token" else None,
        jobs=jobs,
        environ=environ,
    )
    restarted_server = make_server(
        "127.0.0.1", 0, restarted, handler_class=QuietHandler
    )
    restarted_thread = Thread(target=restarted_server.serve_forever, daemon=True)
    restarted_thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(
                f"http://127.0.0.1:{restarted_server.server_port}/sandbox/candidate"
            )
            navigation_requests = []
            record_navigation_request = lambda request: navigation_requests.append(request.url)
            page.on("request", record_navigation_request)
            page.keyboard.press("Tab")
            skip_link = page.get_by_role("link", name="Skip to dashboard content", exact=True)
            assert page.evaluate("document.activeElement === document.querySelector('.skip-link')")
            assert skip_link.bounding_box()["y"] >= 0
            page.keyboard.press("Enter")
            assert page.evaluate("document.activeElement.id") == "dashboard-content"
            assert page.url.endswith("#dashboard-content")
            page.locator("#token").fill("sandbox-token")
            navigation = page.get_by_role("navigation", name="Candidate dashboard")
            targets = ("start-panel", "inspect-panel", "decision-panel", "overview-title",
                       "applications-title", "schedule-title")
            assert navigation.get_by_role("link").count() == len(targets)
            for target in targets:
                navigation.locator(f'a[href="#{target}"]').click()
                assert page.evaluate("document.activeElement.id") == target
                assert page.url.endswith(f"#{target}")
            first_link = navigation.get_by_role("link", name="Start run", exact=True)
            first_link.focus()
            first_link.press("Enter")
            assert page.evaluate("document.activeElement.id") == "start-panel"
            page.get_by_role("link", name="Back to dashboard navigation").click()
            assert page.evaluate("document.activeElement.id") == "candidate-top"
            assert navigation_requests == []
            page.remove_listener("request", record_navigation_request)
            assert page.locator("#token").input_value() == "sandbox-token"
            assert page.locator("#approve").is_disabled()
            assert page.locator("#decline").is_disabled()
            page.locator("#start-run-id").fill("deduplicated-start")
            page.locator("#keyword").fill("warehouse")
            page.locator("#profile").fill("Warehouse candidate")
            page.evaluate("""() => {
              window.startOriginalFetch = window.fetch;
              window.fetch = async (...args) => {
                if (String(args[0]) === '/v1/candidate/runs') {
                  document.body.dataset.startRequests = String(
                    Number(document.body.dataset.startRequests || 0) + 1
                  );
                  document.body.dataset.startWaiting = 'yes';
                  await new Promise(resolve => { window.startRelease = resolve; });
                }
                return window.startOriginalFetch(...args);
              };
            }""")
            page.locator("#start").click()
            page.locator('body[data-start-waiting="yes"]').wait_for()
            assert page.locator("#start").is_disabled()
            assert page.locator("#start-panel").get_attribute("aria-busy") == "true"
            page.evaluate("document.getElementById('start').click()")
            assert page.locator("body").get_attribute("data-start-requests") == "1"
            page.locator("#token").fill("changed-token")
            page.evaluate("window.startRelease(); window.fetch = window.startOriginalFetch")
            page.locator("#start:not([disabled])").wait_for()
            assert page.locator("#start-panel").get_attribute("aria-busy") == "false"
            assert page.locator("#run-id").input_value() == ""
            page.locator("#token").fill("sandbox-token")
            for index, (input_id, changed_value) in enumerate((
                ("start-run-id", "superseded-start"),
                ("keyword", "superseded-keyword"),
                ("location", "superseded-location"),
                ("profile", "Superseded profile"),
            )):
                page.locator("#start-run-id").fill(f"stale-input-{index}")
                page.locator("#keyword").fill("warehouse")
                page.locator("#location").fill("Remote")
                page.locator("#profile").fill("Warehouse candidate")
                page.evaluate("""() => {
                  window.startOriginalFetch = window.fetch;
                  window.fetch = async (...args) => {
                    if (String(args[0]) === '/v1/candidate/runs') {
                      document.body.dataset.startInputWaiting = 'yes';
                      await new Promise(resolve => { window.startInputRelease = resolve; });
                    }
                    return window.startOriginalFetch(...args);
                  };
                }""")
                page.locator("#start").click()
                page.locator('body[data-start-input-waiting="yes"]').wait_for()
                page.locator(f"#{input_id}").fill(changed_value)
                page.evaluate("""() => {
                  window.startInputRelease();
                  window.fetch = window.startOriginalFetch;
                  delete document.body.dataset.startInputWaiting;
                }""")
                page.locator("#start:not([disabled])").wait_for()
                assert page.locator("#run-id").input_value() == ""
            for resource, button, region, marker in (
                ("run-statuses", "list-runs", "owned-runs", "runs"),
                ("applications", "list-applications", "owned-applications", "applications"),
                ("schedule-events", "list-events", "owned-events", "events"),
            ):
                page.evaluate("""({resource, marker}) => {
                  window.collectionOriginalFetch = window.fetch;
                  window.fetch = async (...args) => {
                    const response = await window.collectionOriginalFetch(...args);
                    if (String(args[0]).includes('/' + resource + '?')) {
                      document.body.dataset[marker + 'Requests'] = String(
                        Number(document.body.dataset[marker + 'Requests'] || 0) + 1
                      );
                      document.body.dataset[marker + 'Waiting'] = 'yes';
                      await new Promise(resolve => { window.collectionRelease = resolve; });
                      // The timer runs only after fetch, response parsing and the
                      // collection loader's promise continuations have drained.
                      setTimeout(() => { document.body.dataset[marker + 'Completed'] = 'yes'; }, 0);
                    }
                    return response;
                  };
                }""", {"resource": resource, "marker": marker})
                page.locator(f"#{button}").click()
                page.locator(f'body[data-{marker}-waiting="yes"]').wait_for()
                assert page.locator(f"#{button}").is_disabled()
                assert page.locator(f"#{region}").get_attribute("aria-busy") == "true"
                page.evaluate("button => document.getElementById(button).click()", button)
                assert page.locator("body").get_attribute(
                    f"data-{marker}-requests"
                ) == "1"
                page.locator("#token").fill("changed-token")
                page.evaluate("window.collectionRelease(); window.fetch = window.collectionOriginalFetch")
                page.locator(f'body[data-{marker}-completed="yes"]').wait_for()
                assert page.locator(f"#{button}").is_enabled()
                assert page.locator(f"#{region}").get_attribute("aria-busy") == "false"
                assert page.locator(f"#{region} li").count() == 0
                page.locator("#token").fill("sandbox-token")
            page.locator("#list-runs").click()
            page.locator("#list-state").get_by_text(
                "25 runs on this page. More available. Select a run to inspect.",
                exact=True,
            ).wait_for()
            assert page.locator("#owned-runs button").count() == 25
            phase_requests = []
            page.on("request", lambda request: phase_requests.append(request.url))
            page.locator("#run-phase").select_option("complete")
            assert page.locator("#owned-runs li").count() == 0
            assert page.locator("#next-runs").is_disabled()
            page.locator("#list-runs").click()
            page.locator("#owned-runs button").filter(has_text=durable_run_id).wait_for()
            assert page.locator("#owned-runs button").count() == 1
            assert "complete" in page.locator("#owned-runs").text_content()
            assert any("phase=complete" in url for url in phase_requests)
            page.locator("#run-phase").select_option("")
            page.locator("#list-runs").click()
            page.locator("#list-state").get_by_text(
                "25 runs on this page. More available. Select a run to inspect.",
                exact=True,
            ).wait_for()
            # Collections must never render raw payloads, even before validation.
            for resource, button, state, rows, next_button, ready, empty, retry, error in (
                ("run-statuses", "list-runs", "list-state", "owned-runs", "next-runs",
                 "25 runs on this page. More available. Select a run to inspect.",
                 "0 runs on this page. End of list.", "Load my runs to retry.",
                 "Run statuses request failed."),
                ("applications", "list-applications", "applications-state", "owned-applications",
                 "next-applications",
                 "1 application on this page. End of list. Read-only snapshots.",
                 "0 applications on this page. End of list. Read-only snapshots.",
                 "Load or retry the application page.", "Applications request failed."),
            ):
                pattern = f"**/v1/candidate/{resource}?*"

                def private_collection(route):
                    payload = route.fetch().json()
                    payload["private_extra"] = "private-collection-top-secret"
                    payload["items"][0]["private_extra"] = "private-collection-item-secret"
                    route.fulfill(status=200, json=payload)

                page.route(pattern, private_collection)
                page.locator(f"#{button}").click()
                page.locator(f"#{state}").get_by_text(ready, exact=True).wait_for()
                assert "private-collection-" not in page.locator("body").text_content()
                page.unroute(pattern)
                page.route(pattern, lambda route: route.fulfill(
                    status=200, content_type="application/json", body="private-invalid-json-secret"))
                page.locator(f"#{button}").click()
                page.locator(f"#{state}").get_by_text(retry, exact=True).wait_for()
                page.locator("#result").get_by_text(error, exact=False).wait_for()
                assert page.locator(f"#{rows} li").count() == 0
                assert "private-invalid-json-secret" not in page.locator("body").text_content()
                page.unroute(pattern)
                page.route(pattern, lambda route: route.fulfill(
                    status=200, json={"schema_version": 1, "items": [], "next_after": None}))
                page.locator(f"#{button}").click()
                page.locator(f"#{state}").get_by_text(empty, exact=True).wait_for()
                assert page.locator(f"#{rows} li").count() == 0
                assert page.locator(f"#{next_button}").is_disabled()
                page.unroute(pattern)
                page.locator(f"#{button}").click()
                page.locator(f"#{state}").get_by_text(ready, exact=True).wait_for()
                assert page.locator(f"#{rows} li").count() > 0
            first_run_page = page.request.get(
                f"http://127.0.0.1:{restarted_server.server_port}/v1/candidate/run-statuses?limit=25",
                headers={"Authorization": "Bearer sandbox-token"},
            ).json()
            for corruption in ("duplicate", "reverse", "wrong-cursor", "short", "empty"):
                malformed = json.loads(json.dumps(first_run_page))
                if corruption == "duplicate":
                    malformed["items"][1]["run_id"] = malformed["items"][0]["run_id"]
                elif corruption == "reverse":
                    malformed["items"].reverse()
                elif corruption == "wrong-cursor":
                    malformed["next_after"] = malformed["items"][0]["run_id"]
                else:
                    malformed["items"] = malformed["items"][:1] if corruption == "short" else []

                def malformed_run_page(route):
                    route.fulfill(status=200, json=malformed)

                page.route("**/v1/candidate/run-statuses?*", malformed_run_page)
                page.locator("#list-runs").click()
                page.locator("#list-state").get_by_text("Load my runs to retry.").wait_for()
                assert page.locator("#owned-runs li").count() == 0
                assert page.locator("#next-runs").is_disabled()
                page.unroute("**/v1/candidate/run-statuses?*")
            page.locator("#list-runs").click()
            page.locator("#list-state").get_by_text(
                "25 runs on this page. More available. Select a run to inspect.",
                exact=True,
            ).wait_for()
            # A replayed first page must fail after the cursor, retaining page-two retry.
            page.route("**/v1/candidate/run-statuses?limit=25&after=*",
                       lambda route: route.fulfill(status=200, json=first_run_page))
            page.locator("#next-runs").click()
            page.locator("#list-state").get_by_text("Next page failed. Retry Next page.").wait_for()
            assert page.locator("#owned-runs li").count() == 0
            assert page.locator("#next-runs").is_enabled()
            page.unroute("**/v1/candidate/run-statuses?limit=25&after=*")
            page.route("**/v1/candidate/run-statuses?limit=25&after=*",
                       lambda route: route.fulfill(status=503, json={"error": "unavailable"}))
            page.locator("#next-runs").click()
            page.locator("#list-state").get_by_text("Next page failed. Retry Next page.").wait_for()
            assert page.locator("#next-runs").is_enabled()
            page.unroute("**/v1/candidate/run-statuses?limit=25&after=*")
            page.locator("#next-runs").click()
            page.locator("#owned-runs button").filter(has_text=durable_run_id).click()
            assert page.locator("#approve").is_disabled()
            page.locator("#status").click()
            page.locator("#result").get_by_text('"phase": "complete"').wait_for()
            assert page.locator("#run-id").input_value() == durable_run_id
            page.locator("#run-overview").get_by_text("Finished", exact=False).wait_for()
            page.locator("#list-applications").click()
            page.locator("#applications-state").get_by_text(
                "1 application on this page. End of list. Read-only snapshots.",
                exact=True,
            ).wait_for()
            assert page.locator("#owned-applications li").count() == 1
            assert "applied" in page.locator("#owned-applications").text_content()
            assert "version 2" in page.locator("#owned-applications").text_content()
            assert page.locator("#owned-applications button").count() == 1
            application_button = page.locator("#owned-applications button")
            assert application_button.text_content() == "View application"
            assert application_button.get_attribute("aria-label").startswith(
                "View application "
            )
            page.locator("#owned-applications button").click()
            page.locator("#application-detail-state").get_by_text("Read-only application detail.").wait_for()
            detail = json.loads(page.locator("#application-detail").text_content())
            assert application_button.get_attribute("aria-label") == (
                f"View application 1: {detail['application_id']}"
            )
            page.locator("#application-status").select_option("interview")
            page.locator("#list-applications").click()
            page.locator("#applications-state").get_by_text(
                "0 applications on this page. End of list. Read-only snapshots.",
                exact=True,
            ).wait_for()
            assert page.locator("#owned-applications li").count() == 0
            page.locator("#application-status").select_option("applied")
            page.locator("#list-applications").click()
            page.locator("#applications-state").get_by_text(
                "1 application on this page. End of list. Read-only snapshots.",
                exact=True,
            ).wait_for()
            page.locator("#application-status").select_option("")
            page.route(
                "**/v1/candidate/applications?*",
                lambda route: route.fulfill(status=200, json={
                    "schema_version": 1,
                    "items": [
                        {"application_id": "candidate  one", "job_id": "job-1",
                         "job_title": "First", "company_id": "company",
                         "status": "saved", "state_version": 1},
                        {"application_id": "candidate one", "job_id": "job-2",
                         "job_title": "Second", "company_id": "company",
                         "status": "saved", "state_version": 1},
                    ],
                    "next_after": None,
                }),
            )
            page.locator("#list-applications").click()
            page.locator("#applications-state").get_by_text(
                "2 applications on this page. End of list. Read-only snapshots.", exact=True
            ).wait_for()
            assert page.get_by_role(
                "button", name="View application 1: candidate one", exact=True
            ).count() == 1
            assert page.get_by_role(
                "button", name="View application 2: candidate one", exact=True
            ).count() == 1
            page.unroute("**/v1/candidate/applications?*")
            page.locator("#list-applications").click()
            page.locator("#applications-state").get_by_text(
                "1 application on this page. End of list. Read-only snapshots.", exact=True
            ).wait_for()
            page.locator("#owned-applications button").click()
            page.locator("#application-detail-state").get_by_text(
                "Read-only application detail."
            ).wait_for()
            assert detail["status"] == "applied" and detail["state_version"] == 2
            assert set(detail) == {"application_id", "job_id", "job_title", "company_id",
                                   "status", "state_version", "created_at", "updated_at"}
            page.locator("#load-application-history").click()
            page.locator("#application-history-state").get_by_text("Read-only application history.").wait_for()
            history = json.loads(page.locator("#application-history").text_content())
            assert history["application_id"] == detail["application_id"] and history["state_version"] == 2
            assert len(history["items"]) == 1 and history["items"][0]["to_status"] == "applied"
            assert set(history["items"][0]) == {"sequence", "from_status", "to_status", "occurred_at"}
            def expanded_history(route):
                payload = route.fetch().json()
                payload["private"] = "private-history-secret"
                payload["items"][0]["note"] = "private-history-secret"
                route.fulfill(status=200, json=payload)
            page.route("**/v1/candidate/application/history?*", expanded_history)
            page.locator("#load-application-history").click()
            page.locator("#application-history-state").get_by_text("Read-only application history.").wait_for()
            assert "private-history-secret" not in page.locator("body").text_content()
            page.unroute("**/v1/candidate/application/history?*")
            for payload in ({**history, "application_id": "foreign"},
                            {**history, "items": []}, {**history, "schema_version": 99},
                            *({**history, "items": [{**history["items"][0], "from_status": source, "to_status": target}]}
                              for source, target in [("saved", "offer"), ("applied", "applied"),
                                                     ("rejected", "interview"), ("withdrawn", "saved")])):
                def malformed_history(route):
                    route.fulfill(status=200, json=payload)
                page.route("**/v1/candidate/application/history?*", malformed_history)
                page.locator("#load-application-history").click()
                page.locator("#application-history-state").get_by_text("Load history again to retry.").wait_for()
                assert page.locator("#application-history").text_content() == ""
                page.unroute("**/v1/candidate/application/history?*")
            page.route("**/v1/candidate/application/history?*", lambda route: route.fulfill(status=200, content_type="application/json", body="private-history-json-secret"))
            page.locator("#load-application-history").click()
            page.locator("#application-history-state").get_by_text("Load history again to retry.").wait_for()
            page.locator("#result").get_by_text("Application history request failed.", exact=False).wait_for()
            assert "private-history-json-secret" not in page.locator("body").text_content()
            page.unroute("**/v1/candidate/application/history?*")
            page.locator("#load-application-history").click()
            page.locator("#application-history-state").get_by_text("Read-only application history.").wait_for()
            page.evaluate("""() => {
                window.originalHistoryFetch = window.fetch;
                window.fetch = async (...args) => {
                    const response = await window.originalHistoryFetch(...args);
                    if (String(args[0]).includes('/application/history?')) {
                        document.body.dataset.historyRequests = String(
                            Number(document.body.dataset.historyRequests || 0) + 1);
                        document.body.dataset.historyWaiting = 'yes';
                        await new Promise(resolve => { window.releaseHistory = resolve; });
                        document.body.dataset.historyReleased = 'yes';
                    }
                    return response;
                };
            }""")
            page.locator("#load-application-history").click()
            page.locator('body[data-history-waiting="yes"]').wait_for()
            assert page.locator("#load-application-history").is_disabled()
            assert page.locator("#application-history").get_attribute("aria-busy") == "true"
            page.evaluate("document.getElementById('load-application-history').click()")
            assert page.locator("body").get_attribute("data-history-requests") == "1"
            page.locator("#owned-applications button").click()
            page.locator("#application-detail-state").get_by_text("Read-only application detail.").wait_for()
            page.evaluate("window.releaseHistory(); window.fetch = window.originalHistoryFetch")
            page.locator('body[data-history-released="yes"]').wait_for()
            assert page.locator("#application-history").text_content() == ""
            assert page.locator("#application-history-state").text_content() == ""
            assert page.locator("#application-history").get_attribute("aria-busy") == "false"
            def expanded_detail(route):
                payload = route.fetch().json()
                payload["private_top_level"] = "private-top-secret"
                payload["application"]["private_metadata"] = "private-nested-secret"
                route.fulfill(status=200, json=payload)
            page.route("**/v1/candidate/application?*", expanded_detail)
            page.locator("#owned-applications button").click()
            page.locator("#application-detail-state").get_by_text("Read-only application detail.").wait_for()
            assert "private-top-secret" not in page.locator("body").text_content()
            assert "private-nested-secret" not in page.locator("body").text_content()
            page.unroute("**/v1/candidate/application?*")
            page.route("**/v1/candidate/application?*", lambda route: route.fulfill(status=503, json={"error": "internal_error", "message": "private-error-secret"}))
            page.locator("#owned-applications button").click()
            page.locator("#application-detail-state").get_by_text("Select the application again to retry.").wait_for()
            assert page.locator("#application-detail").text_content() == ""
            assert "private-error-secret" not in page.locator("body").text_content()
            page.unroute("**/v1/candidate/application?*")
            page.route("**/v1/candidate/application?*", lambda route: route.fulfill(
                status=200, content_type="application/json", body="private-malformed-json-secret"))
            page.locator("#owned-applications button").click()
            page.locator("#application-detail-state").get_by_text("Select the application again to retry.").wait_for()
            page.locator("#result").get_by_text("Application detail request failed.", exact=False).wait_for()
            assert "private-malformed-json-secret" not in page.locator("body").text_content()
            assert page.locator("#application-detail").text_content() == ""
            page.unroute("**/v1/candidate/application?*")
            page.locator("#owned-applications button").click()
            page.locator("#application-detail-state").get_by_text("Read-only application detail.").wait_for()
            def malformed_detail(route):
                payload = route.fetch().json()
                payload["application"]["application_id"] = "wrong-identity"
                route.fulfill(status=200, json=payload)
            page.route("**/v1/candidate/application?*", malformed_detail)
            page.locator("#owned-applications button").click()
            page.locator("#application-detail-state").get_by_text("Select the application again to retry.").wait_for()
            assert page.locator("#application-detail").text_content() == ""
            page.unroute("**/v1/candidate/application?*")
            # Delay detail in the browser while changing authentication context.
            page.evaluate("""() => {
                const original = window.fetch;
                window.fetch = async (...args) => {
                    const response = await original(...args);
                    if (String(args[0]).includes('/v1/candidate/application?')) {
                        document.body.dataset.detailRequests = String(
                            Number(document.body.dataset.detailRequests || 0) + 1);
                        document.body.dataset.detailWaiting = 'yes';
                        await new Promise(resolve => { window.releaseDetail = resolve; });
                        document.body.dataset.detailReleased = 'yes';
                    }
                    return response;
                };
            }""")
            page.locator("#owned-applications button").click()
            page.locator('body[data-detail-waiting="yes"]').wait_for()
            assert page.locator("#owned-applications button").is_disabled()
            assert page.locator("#application-detail").get_attribute("aria-busy") == "true"
            page.evaluate("document.querySelector('#owned-applications button').click()")
            assert page.locator("body").get_attribute("data-detail-requests") == "1"
            page.locator("#token").fill("changed")
            page.evaluate("window.releaseDetail()")
            page.locator('body[data-detail-released="yes"]').wait_for()
            page.locator("#token").fill("sandbox-token")
            assert page.locator("#application-detail").text_content() == ""
            assert page.locator("#application-detail-state").text_content() == ""
            assert page.locator("#application-detail").get_attribute("aria-busy") == "false"
            assert page.locator("#application-history").text_content() == ""
            assert page.locator("#load-application-history").is_disabled()
            from urllib.parse import parse_qs, urlsplit
            opaque_cursor = "\ufeff" + "😀" * 198 + "\ufeff"
            seen_pages = []
            def application_page(route):
                payload = route.fetch(url=route.request.url.split("?")[0] + "?limit=25").json()
                query = parse_qs(urlsplit(route.request.url).query)
                seen_pages.append(query.get("after"))
                payload["items"][0]["application_id"] = opaque_cursor
                payload["next_after"] = opaque_cursor if len(seen_pages) == 1 else None
                route.fulfill(status=200, json=payload)
            page.route("**/v1/candidate/applications?*", application_page)
            page.locator("#list-applications").click()
            page.locator("#applications-state").get_by_text(
                "1 application on this page. More available. Read-only snapshots.",
                exact=True,
            ).wait_for()
            assert page.locator("#next-applications").is_enabled()
            page.locator("#next-applications").click()
            page.locator("#applications-state").get_by_text(
                "1 application on this page. End of list. Read-only snapshots.",
                exact=True,
            ).wait_for()
            assert seen_pages == [None, [opaque_cursor]]
            assert page.locator("#next-applications").is_disabled()
            page.locator("#token").fill("changed")
            assert page.locator("#owned-applications li").count() == 0
            assert page.locator("#next-applications").is_disabled()
            assert page.locator("#owned-runs button").count() == 0
            assert page.locator("#next-runs").is_disabled()
            assert page.locator("#run-overview").text_content() == "Refresh status to load a snapshot."
            browser.close()
    finally:
        restarted_server.shutdown()
        restarted_thread.join(timeout=5)
        restarted_server.server_close()
        restarted.close()


def test_candidate_service_validates_auth_before_opening_storage(tmp_path):
    database_path = tmp_path / "must-not-exist.sqlite"
    environ = {
        "CAREER_AGENT_ENV": "production",
        "CAREER_AGENT_DB_PATH": str(database_path),
        "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
        "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
        "CAREER_AGENT_CANDIDATE_BEARER_TOKEN": "short",
        "CAREER_AGENT_CANDIDATE_USER_ID": "candidate-1",
    }

    with pytest.raises(ValueError, match="32 to 4096"):
        build_candidate_service_from_env(environ=environ)

    assert not database_path.exists()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("CAREER_AGENT_CANDIDATE_HOST", "localhost", "IPv4 or IPv6"),
        ("CAREER_AGENT_CANDIDATE_PORT", "8O80", "must be an integer"),
        ("CAREER_AGENT_CANDIDATE_PORT", "65536", "between 0 and 65535"),
        ("CAREER_AGENT_CANDIDATE_USER_ID", "bad user", "malformed"),
    ],
)
def test_candidate_service_rejects_invalid_listener_and_identity(field, value, message):
    environ = {
        "CAREER_AGENT_CANDIDATE_BEARER_TOKEN": "s" * 32,
        "CAREER_AGENT_CANDIDATE_USER_ID": "candidate-1",
        field: value,
    }

    with pytest.raises(ValueError, match=message):
        CandidateServiceConfig.from_env(environ)


@pytest.mark.parametrize("token", (" " + "s" * 32, "s" * 32 + " "))
def test_candidate_service_rejects_bearer_whitespace_changed_by_header_parser(token):
    with pytest.raises(ValueError, match="start or end with whitespace"):
        CandidateServiceConfig.from_env(
            {
                "CAREER_AGENT_CANDIDATE_BEARER_TOKEN": token,
                "CAREER_AGENT_CANDIDATE_USER_ID": "candidate-1",
            }
        )


def test_candidate_service_ipv6_listener_is_explicitly_ipv6_only(monkeypatch):
    socket_calls = []
    parent_bind_calls = []

    class FakeSocket:
        def setsockopt(self, level, option, value):
            socket_calls.append((level, option, value))

    fake_server = object.__new__(candidate_service._IPv6WSGIServer)
    fake_server.socket = FakeSocket()
    monkeypatch.setattr(
        candidate_service.WSGIServer,
        "server_bind",
        lambda self: parent_bind_calls.append(self),
    )

    candidate_service._IPv6WSGIServer.server_bind(fake_server)

    assert socket_calls == [(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)]
    assert parent_bind_calls == [fake_server]


def test_candidate_service_binds_authenticates_and_closes(tmp_path):
    token = "s" * 32
    environ = {
        "CAREER_AGENT_ENV": "production",
        "CAREER_AGENT_DB_PATH": str(tmp_path / "candidate-service.sqlite"),
        "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
        "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
        "CAREER_AGENT_CANDIDATE_HOST": "127.0.0.1",
        "CAREER_AGENT_CANDIDATE_PORT": "0",
        "CAREER_AGENT_CANDIDATE_BEARER_TOKEN": token,
        "CAREER_AGENT_CANDIDATE_USER_ID": "candidate-1",
    }
    config = CandidateServiceConfig.from_env(environ)

    assert config.resolve_bearer(token) == "candidate-1"
    assert config.resolve_bearer(token + "x") is None
    assert config.resolve_bearer("é" * 32) is None

    runtime = build_candidate_service_from_env(environ=environ)
    assert runtime.server_port > 0
    runtime.close()
    runtime.close()
    with pytest.raises(RuntimeError, match="closed"):
        runtime.__enter__()


def test_candidate_service_probe_routes_are_public_safe_and_read_only(tmp_path):
    class DelegatedApp:
        def __init__(self):
            self.calls = []
            self.close_calls = 0

        def __call__(self, environ, start_response):
            self.calls.append(environ["PATH_INFO"])
            start_response("418 I'm a teapot", [("Content-Length", "2")])
            return [b"{}"]

        def close(self):
            self.close_calls += 1

    database_path = tmp_path / "candidate.sqlite"
    database = SQLiteDatabase(str(database_path))
    SQLiteCareerLoopRepository(database)
    database.close()
    delegated = DelegatedApp()
    app = CandidateServiceWSGIApp(
        delegated, database_path=str(database_path)  # type: ignore[arg-type]
    )

    health_meta, health = _wsgi_call(app, "/healthz")
    ready_meta, ready = _wsgi_call(app, "/readyz")
    delegated_meta, _ = _wsgi_call(app, "/delegated")

    assert health_meta["status"] == "200 OK"
    assert health == {"status": "ok"}
    assert ready_meta["status"] == "200 OK"
    assert ready == {"status": "ok"}
    assert ready_meta["headers"]["Cache-Control"] == "no-store"
    assert delegated_meta["status"] == "418 I'm a teapot"
    assert delegated.calls == ["/delegated"]
    app.close()
    assert delegated.close_calls == 1


def test_candidate_service_readiness_does_not_create_or_disclose_missing_database(
    tmp_path,
):
    class DelegatedApp:
        def __call__(self, environ, start_response):
            raise AssertionError("probe must not reach the authenticated app")

        def close(self):
            pass

    database_path = tmp_path / "missing.sqlite"
    app = CandidateServiceWSGIApp(
        DelegatedApp(), database_path=str(database_path)  # type: ignore[arg-type]
    )

    meta, body = _wsgi_call(app, "/readyz")

    assert meta["status"] == "503 Service Unavailable"
    assert body == {"status": "unavailable"}
    assert str(database_path) not in json.dumps(body)
    assert not database_path.exists()


def test_candidate_service_readiness_rejects_corrupt_database(tmp_path):
    class DelegatedApp:
        def __call__(self, environ, start_response):
            raise AssertionError("probe must not reach the authenticated app")

        def close(self):
            pass

    database_path = tmp_path / "corrupt.sqlite"
    database_path.write_bytes(b"not a sqlite database")
    app = CandidateServiceWSGIApp(
        DelegatedApp(), database_path=str(database_path)  # type: ignore[arg-type]
    )

    meta, body = _wsgi_call(app, "/readyz")

    assert meta["status"] == "503 Service Unavailable"
    assert body == {"status": "unavailable"}


def test_candidate_service_startup_rejects_corrupt_data_page(tmp_path):

    database_path = tmp_path / "corrupt-page.sqlite"
    database = SQLiteDatabase(str(database_path))
    database.connection.execute(
        "CREATE TABLE autonomous_career_loops (payload BLOB NOT NULL)"
    )
    for _ in range(8):
        database.connection.execute(
            "INSERT INTO autonomous_career_loops VALUES (zeroblob(3000))"
        )
    database.connection.commit()
    page_size = database.connection.execute("PRAGMA page_size").fetchone()[0]
    database.close()
    contents = bytearray(database_path.read_bytes())
    last_page_offset = len(contents) - page_size
    contents[last_page_offset] = 0xFF
    database_path.write_bytes(contents)
    with pytest.raises(RuntimeError, match="failed startup validation"):
        candidate_service._validate_candidate_storage(str(database_path))


@pytest.mark.parametrize("path", ("/healthz", "/readyz"))
def test_candidate_service_probe_routes_reject_non_get_methods(path, tmp_path):
    class DelegatedApp:
        def __call__(self, environ, start_response):
            raise AssertionError("probe must not reach the authenticated app")

        def close(self):
            pass

    app = CandidateServiceWSGIApp(
        DelegatedApp(),  # type: ignore[arg-type]
        database_path=str(tmp_path / "missing.sqlite"),
    )
    captured = {}

    chunks = app(
        {"REQUEST_METHOD": "POST", "PATH_INFO": path},
        lambda status, headers: captured.update(status=status, headers=dict(headers)),
    )

    assert captured["status"] == "405 Method Not Allowed"
    assert captured["headers"]["Allow"] == "GET"
    assert b"".join(chunks) == b""
    assert not (tmp_path / "missing.sqlite").exists()


def test_candidate_service_interrupt_closes_listener_and_app():
    class FakeApp:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    class InterruptedServer:
        server_port = 8080

        def __init__(self):
            self.close_calls = 0

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            self.close_calls += 1

    app = FakeApp()
    server = InterruptedServer()
    runtime = CandidateServiceRuntime(app, server)  # type: ignore[arg-type]

    runtime.serve_forever()
    runtime.close()

    assert app.close_calls == 1
    assert server.close_calls == 1


def test_candidate_service_bind_interrupt_closes_composed_app(monkeypatch):
    class FakeApp:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    app = FakeApp()
    monkeypatch.setattr(
        candidate_service,
        "build_candidate_sandbox_app_from_env",
        lambda **kwargs: app,
    )
    monkeypatch.setattr(
        candidate_service,
        "make_server",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    monkeypatch.setattr(candidate_service, "_validate_candidate_storage", lambda path: None)

    with pytest.raises(KeyboardInterrupt):
        build_candidate_service_from_env(
            environ={
                "CAREER_AGENT_CANDIDATE_BEARER_TOKEN": "s" * 32,
                "CAREER_AGENT_CANDIDATE_USER_ID": "candidate-1",
            }
        )

    assert app.close_calls == 1


def test_candidate_service_storage_validation_failure_closes_composed_app(monkeypatch):
    class FakeApp:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    app = FakeApp()
    monkeypatch.setattr(
        candidate_service,
        "build_candidate_sandbox_app_from_env",
        lambda **kwargs: app,
    )
    monkeypatch.setattr(
        candidate_service,
        "_validate_candidate_storage",
        lambda path: (_ for _ in ()).throw(RuntimeError("invalid storage")),
    )

    with pytest.raises(RuntimeError, match="invalid storage"):
        build_candidate_service_from_env(
            environ={
                "CAREER_AGENT_DB_PATH": "/tmp/candidate-invalid.sqlite",
                "CAREER_AGENT_CANDIDATE_BEARER_TOKEN": "s" * 32,
                "CAREER_AGENT_CANDIDATE_USER_ID": "candidate-1",
            }
        )

    assert app.close_calls == 1


def test_candidate_sandbox_rejects_non_job_seed_and_closes_database(tmp_path):
    with pytest.raises(TypeError, match="Job instances"):
        build_candidate_sandbox_app_from_env(
            resolve_bearer=lambda token: token,
            jobs=(object(),),  # type: ignore[arg-type]
            environ={
                "CAREER_AGENT_DB_PATH": str(tmp_path / "candidate.sqlite"),
                "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
                "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
            },
        )


def test_candidate_sandbox_authenticates_before_opening_storage(tmp_path, monkeypatch):
    app = build_candidate_sandbox_app_from_env(
        resolve_bearer=lambda token: None,
        environ={
            "CAREER_AGENT_DB_PATH": str(tmp_path / "candidate.sqlite"),
            "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
            "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
        },
    )

    class ForbiddenDatabase:
        def __init__(self, path):
            raise AssertionError("storage must not be opened before authentication")

    monkeypatch.setattr(runtime_composition, "SQLiteDatabase", ForbiddenDatabase)
    statuses = []
    payload = json.loads(
        b"".join(
            app(
                {
                    "REQUEST_METHOD": "GET",
                    "PATH_INFO": "/v1/candidate/runs/private/approval",
                    "HTTP_AUTHORIZATION": "Bearer invalid",
                },
                lambda status, headers: statuses.append(status),
            )
        )
    )
    assert statuses == ["401 Unauthorized"]
    assert payload == {"error": "unauthorized"}



def test_runtime_config_expands_home_relative_database_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    config = RuntimeConfig.from_env({"CAREER_AGENT_DB_PATH": "~/career.sqlite"})

    assert config.database_path == str(tmp_path / "career.sqlite")

def test_runtime_config_production_requires_durable_database():
    with pytest.raises(ValueError, match="durable"):
        RuntimeConfig.from_env({"CAREER_AGENT_ENV": "production"})


@pytest.mark.parametrize(
    "field",
    ["CAREER_AGENT_ALLOW_NETWORK_PROVIDERS", "CAREER_AGENT_ALLOW_CONSEQUENTIAL_ACTIONS"],
)
def test_runtime_config_test_environment_rejects_external_effects(field):
    with pytest.raises(ValueError, match="Test environment"):
        RuntimeConfig.from_env(
            {
                "CAREER_AGENT_ENV": "test",
                field: "true",
            }
        )


@pytest.mark.parametrize(
    "values",
    [
        {"CAREER_AGENT_ENV": "unknown"},
        {"CAREER_AGENT_LOG_LEVEL": "LOUD"},
        {"CAREER_AGENT_ALLOW_NETWORK_PROVIDERS": "sometimes"},
        {"CAREER_AGENT_DB_PATH": ""},
        {"CAREER_AGENT_DB_PATH": "bad\x00path"},
    ],
)
def test_runtime_config_rejects_malformed_environment(values):
    with pytest.raises(ValueError):
        RuntimeConfig.from_env(values)


def test_operational_runtime_composes_from_environment_and_closes(tmp_path):
    database_path = str(tmp_path / "runtime.sqlite")
    token = "a" * 32
    SQLiteDatabase(database_path).close()

    with build_operational_app_from_env(
        {
            "CAREER_AGENT_ENV": "production",
            "CAREER_AGENT_DB_PATH": database_path,
            "CAREER_AGENT_OPERATIONAL_BEARER_TOKEN": token,
        }
    ) as app:
        health_meta, health = _wsgi_call(app, "/healthz")
        issues_meta, issues = _wsgi_call(
            app,
            "/v1/operational/issues",
            token=token,
        )

        assert health_meta["status"] == "200 OK"
        assert health == {"status": "ok"}
        assert issues_meta["status"] == "200 OK"
        assert issues["issues"] == []

    with pytest.raises(RuntimeError, match="closed"):
        _wsgi_call(app, "/healthz")


def test_operational_runtime_uses_request_thread_sqlite_connections(tmp_path):
    token = "a" * 32
    database_path = str(tmp_path / "threaded.sqlite")
    SQLiteDatabase(database_path).close()
    app = build_operational_app_from_env(
        {
            "CAREER_AGENT_ENV": "production",
            "CAREER_AGENT_DB_PATH": database_path,
            "CAREER_AGENT_OPERATIONAL_BEARER_TOKEN": token,
        }
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(
                lambda _: _wsgi_call(
                    app,
                    "/v1/operational/issues",
                    token=token,
                ),
                range(2),
            )
        )

    assert [metadata["status"] for metadata, _ in results] == ["200 OK", "200 OK"]
    assert [payload["issues"] for _, payload in results] == [[], []]
    app.close()


def test_read_only_sqlite_connection_rejects_writes(tmp_path):
    database_path = str(tmp_path / "read-only.sqlite")
    writable = SQLiteDatabase(database_path)
    writable.connection.execute("CREATE TABLE sample(value TEXT)")
    writable.connection.commit()
    writable.close()

    read_only = SQLiteDatabase.open_read_only(database_path)
    try:
        assert read_only.connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'sample'"
        ).fetchone() == ("sample",)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            read_only.connection.execute("INSERT INTO sample VALUES ('blocked')")
    finally:
        read_only.close()


def test_operational_runtime_keeps_liveness_and_sanitizes_database_failure(tmp_path):
    token = "a" * 32
    database_directory = tmp_path / "database-directory"
    database_directory.mkdir()
    app = build_operational_app_from_env(
        {
            "CAREER_AGENT_ENV": "production",
            "CAREER_AGENT_DB_PATH": str(database_directory / "runtime.sqlite"),
            "CAREER_AGENT_OPERATIONAL_BEARER_TOKEN": token,
        }
    )
    database_directory.rmdir()

    health_meta, health = _wsgi_call(app, "/healthz")
    issues_meta, issues = _wsgi_call(
        app,
        "/v1/operational/issues",
        token=token,
    )

    assert health_meta["status"] == "200 OK"
    assert health == {"status": "ok"}
    assert issues_meta["status"] == "500 Internal Server Error"
    assert issues == {"error": "internal_error"}
    app.close()


def test_operational_runtime_never_creates_a_missing_database(tmp_path):
    token = "a" * 32
    database_path = tmp_path / "missing.sqlite"
    app = build_operational_app_from_env(
        {
            "CAREER_AGENT_ENV": "production",
            "CAREER_AGENT_DB_PATH": str(database_path),
            "CAREER_AGENT_OPERATIONAL_BEARER_TOKEN": token,
        }
    )

    issues_meta, issues = _wsgi_call(
        app,
        "/v1/operational/issues",
        token=token,
    )

    assert issues_meta["status"] == "500 Internal Server Error"
    assert issues == {"error": "internal_error"}
    assert not database_path.exists()
    app.close()


@pytest.mark.parametrize(
    "token",
    ["", "too-short", "x" * 31, "non-ascii-é" + "x" * 32],
)
def test_operational_runtime_rejects_invalid_token_before_creating_database(
    tmp_path, token
):
    database_path = tmp_path / "must-not-exist.sqlite"

    with pytest.raises(ValueError, match="bearer_token"):
        build_operational_app_from_env(
            {
                "CAREER_AGENT_ENV": "production",
                "CAREER_AGENT_DB_PATH": str(database_path),
                "CAREER_AGENT_OPERATIONAL_BEARER_TOKEN": token,
            }
        )

    assert not database_path.exists()


def test_operational_runtime_rejects_invalid_token_before_probing_sqlite_uri(tmp_path):
    database_path = tmp_path / "must-not-exist-uri.sqlite"

    with pytest.raises(ValueError, match="bearer_token"):
        build_operational_app_from_env(
            {
                "CAREER_AGENT_ENV": "production",
                "CAREER_AGENT_DB_PATH": f"file:{database_path}?mode=rwc",
            }
        )

    assert not database_path.exists()


def test_operational_runtime_valid_token_does_not_create_missing_sqlite_uri(tmp_path):
    database_path = tmp_path / "must-not-exist-valid-uri.sqlite"
    token = "a" * 32
    app = build_operational_app_from_env(
        {
            "CAREER_AGENT_ENV": "production",
            "CAREER_AGENT_DB_PATH": f"file:{database_path}?mode=rwc",
            "CAREER_AGENT_OPERATIONAL_BEARER_TOKEN": token,
        }
    )

    assert not database_path.exists()
    issues_meta, issues = _wsgi_call(
        app,
        "/v1/operational/issues",
        token=token,
    )
    assert issues_meta["status"] == "500 Internal Server Error"
    assert issues == {"error": "internal_error"}
    assert not database_path.exists()
    app.close()



def test_runtime_composition_blocks_consequential_actions_by_default():
    class RecordingAdapter:
        def __init__(self):
            self.calls = []

        def execute(self, operation_id, action_type, payload):
            self.calls.append((operation_id, action_type, dict(payload)))
            return {"ok": True}

    database = SQLiteDatabase()
    repository = SQLiteExternalActionOperationRepository(database)
    adapter = RecordingAdapter()
    service = build_external_action_service(
        RuntimeConfig.from_env({}),
        repository,
        adapter,
    )
    service.prepare("runtime-guard-action", "test.action", {"value": 1})

    with pytest.raises(PermissionError, match="disabled by runtime policy"):
        service.execute("runtime-guard-action", human_approved=True)

    assert repository.get("runtime-guard-action").status == ExternalActionStatus.PREPARED
    assert adapter.calls == []


def test_runtime_composition_blocks_network_signal_provider_by_default():
    class RecordingSignalProvider:
        def __init__(self):
            self.calls = 0

        def collect(self):
            self.calls += 1
            return ()

    provider = RecordingSignalProvider()
    guarded = guard_signal_provider(RuntimeConfig.from_env({}), provider)

    with pytest.raises(PermissionError, match="Network providers are disabled"):
        guarded.collect()

    assert provider.calls == 0


def test_runtime_composition_blocks_network_job_provider_by_default():
    class RecordingJobProvider:
        def __init__(self):
            self.calls = 0

        def search(self, query):
            self.calls += 1
            return ()

    provider = RecordingJobProvider()
    guarded = guard_job_provider(RuntimeConfig.from_env({}), provider)

    with pytest.raises(PermissionError, match="Network providers are disabled"):
        guarded.search("python")

    assert provider.calls == 0


def test_runtime_composition_allows_explicitly_enabled_external_effects():
    class RecordingAdapter:
        def __init__(self):
            self.calls = 0

        def execute(self, operation_id, action_type, payload):
            self.calls += 1
            return {"ok": True}

    class RecordingSignalProvider:
        def __init__(self):
            self.calls = 0

        def collect(self):
            self.calls += 1
            return ()

    class RecordingJobProvider:
        def __init__(self):
            self.calls = 0

        def search(self, query):
            self.calls += 1
            return ()

    config = RuntimeConfig.from_env(
        {
            "CAREER_AGENT_ALLOW_NETWORK_PROVIDERS": "true",
            "CAREER_AGENT_ALLOW_CONSEQUENTIAL_ACTIONS": "true",
        }
    )
    database = SQLiteDatabase()
    repository = SQLiteExternalActionOperationRepository(database)
    adapter = RecordingAdapter()
    actions = build_external_action_service(config, repository, adapter)
    actions.prepare("runtime-enabled-action", "test.action", {})
    result = actions.execute("runtime-enabled-action", human_approved=True)

    signals = RecordingSignalProvider()
    jobs = RecordingJobProvider()
    assert guard_signal_provider(config, signals).collect() == ()
    assert guard_job_provider(config, jobs).search("python") == ()
    assert result.status == ExternalActionStatus.SUCCEEDED
    assert adapter.calls == 1
    assert signals.calls == 1
    assert jobs.calls == 1

def test_structured_logging_includes_correlation_id_and_redacts_secrets():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    logger = logging.getLogger("career-agent-production-test")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    with correlation_scope("corr-123") as correlation_id:
        assert correlation_id == "corr-123"
        assert current_correlation_id() == "corr-123"
        log_event(
            logger,
            logging.INFO,
            "application.prepared",
            "Application prepared",
            application_id="app-1",
            api_token="do-not-log",
            nested={"password": "secret", "safe": "value"},
        )

    assert current_correlation_id() is None
    payload = json.loads(stream.getvalue())
    assert payload["correlation_id"] == "corr-123"
    assert payload["event"] == "application.prepared"
    assert payload["fields"]["application_id"] == "app-1"
    assert payload["fields"]["api_token"] == "[REDACTED]"
    assert payload["fields"]["nested"]["password"] == "[REDACTED]"
    assert payload["fields"]["nested"]["safe"] == "value"


def test_redaction_bounds_untrusted_log_values():
    result = redact_mapping(
        {
            "body": "x" * 5000,
            "authorization_header": "Bearer top-secret",
            "diagnostic": (
                '{"api_key":"very-secret-value"} '
                "client_secret=client-value access_token=access-value"
            ),
            "items": list(range(150)),
        }
    )

    assert len(result["body"]) == 2000
    assert result["authorization_header"] == "[REDACTED]"
    assert "very-secret-value" not in result["diagnostic"]
    assert "client-value" not in result["diagnostic"]
    assert "access-value" not in result["diagnostic"]
    assert result["diagnostic"].count("[REDACTED]") == 3
    assert len(result["items"]) == 100



def test_redaction_sanitizes_fallback_object_representations():
    result = redact_mapping(
        {
            "provider_error": RuntimeError(
                "api_key=super-secret Bearer provider-token"
            )
        }
    )

    rendered = result["provider_error"]
    assert "super-secret" not in rendered
    assert "provider-token" not in rendered
    assert rendered.count("[REDACTED]") == 2

def test_redaction_covers_basic_authorization_and_quoted_secret_whitespace():
    result = redact_mapping(
        {
            "diagnostic": (
                'Authorization: Basic dXNlcjpwYXNz\n'
                '{"api_key":"abc def"}'
            )
        }
    )

    rendered = result["diagnostic"]
    assert "dXNlcjpwYXNz" not in rendered
    assert "abc def" not in rendered
    assert rendered.count("[REDACTED]") == 2


@pytest.mark.parametrize("diagnostic", [
    json.dumps({"password": 'abc" secret tail', "safe": "visible"}),
    json.dumps({"api_key": 'abc\\" secret tail', "safe": "visible"}),
    "password='abc\\' secret tail' safe=visible",
])
def test_redaction_masks_complete_escape_aware_quoted_values(diagnostic):
    rendered = redact_mapping({"diagnostic": diagnostic})["diagnostic"]
    assert "secret tail" not in rendered
    assert "abc" not in rendered
    assert "visible" in rendered
    assert "[REDACTED]" in rendered


def test_structured_logging_normalizes_non_finite_numbers_to_valid_json():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    logger = logging.getLogger("career-agent-non-finite-log-test")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    log_event(
        logger,
        logging.INFO,
        "numeric.diagnostic",
        "Numeric diagnostic",
        nan=float("nan"),
        positive_infinity=float("inf"),
        negative_infinity=float("-inf"),
    )

    rendered = stream.getvalue()
    payload = json.loads(
        rendered,
        parse_constant=lambda token: (_ for _ in ()).throw(
            ValueError(f"non-standard JSON constant: {token}")
        ),
    )
    assert payload["fields"]["nan"] == "[NON_FINITE]"
    assert payload["fields"]["positive_infinity"] == "[NON_FINITE]"
    assert payload["fields"]["negative_infinity"] == "[NON_FINITE]"
    assert "NaN" not in rendered
    assert "Infinity" not in rendered


@pytest.mark.parametrize(
    "correlation_id",
    ["", "x" * 201, "bad\nvalue", "bad\ud800value"],
)
def test_correlation_scope_rejects_malformed_ids(correlation_id):
    with pytest.raises(ValueError):
        with correlation_scope(correlation_id):
            pass


def _create_operation(
    repository: SQLiteExternalActionOperationRepository,
    operation_id: str,
    status: ExternalActionStatus,
    *,
    updated_at: datetime,
) -> None:
    operation = ExternalActionOperation(
        operation_id=operation_id,
        action_type="test.action",
        payload={"id": operation_id},
        created_at=updated_at,
        updated_at=updated_at,
    )
    repository.create(operation)
    if status == ExternalActionStatus.PREPARED:
        repository._database.connection.execute(
            "UPDATE external_action_operations SET updated_at = ? WHERE operation_id = ?",
            (updated_at.isoformat(), operation_id),
        )
        repository._database.connection.commit()
        return
    repository.transition(
        operation_id,
        ExternalActionStatus.PREPARED,
        ExternalActionStatus.IN_PROGRESS,
    )
    if status == ExternalActionStatus.IN_PROGRESS:
        repository._database.connection.execute(
            "UPDATE external_action_operations SET updated_at = ? WHERE operation_id = ?",
            (updated_at.isoformat(), operation_id),
        )
        repository._database.connection.commit()
        return
    repository.transition(
        operation_id,
        ExternalActionStatus.IN_PROGRESS,
        status,
        result={"ok": True} if status == ExternalActionStatus.SUCCEEDED else None,
        error=(
            "definite failure"
            if status == ExternalActionStatus.FAILED
            else "ambiguous outcome"
        ),
    )
    repository._database.connection.execute(
        "UPDATE external_action_operations SET updated_at = ? WHERE operation_id = ?",
        (updated_at.isoformat(), operation_id),
    )
    repository._database.connection.commit()


def test_operational_probe_surfaces_failed_ambiguous_and_stuck_actions():
    database = SQLiteDatabase()
    actions = SQLiteExternalActionOperationRepository(database)
    old = NOW - timedelta(hours=2)

    _create_operation(actions, "failed", ExternalActionStatus.FAILED, updated_at=old)
    _create_operation(
        actions,
        "reconcile",
        ExternalActionStatus.RECONCILIATION_REQUIRED,
        updated_at=old,
    )
    _create_operation(
        actions,
        "in-progress",
        ExternalActionStatus.IN_PROGRESS,
        updated_at=old,
    )
    _create_operation(
        actions,
        "prepared",
        ExternalActionStatus.PREPARED,
        updated_at=old,
    )
    _create_operation(
        actions,
        "recent-prepared",
        ExternalActionStatus.PREPARED,
        updated_at=NOW - timedelta(seconds=10),
    )

    issues = SQLiteOperationalProbe(database).inspect(
        now=NOW,
        stale_after_seconds=3600,
    )

    by_id = {issue.entity_id: issue for issue in issues}
    assert by_id["failed"].issue_type == "external_action_failed"
    assert by_id["failed"].severity == OperationalSeverity.ERROR
    assert by_id["reconcile"].issue_type == "external_action_reconciliation_required"
    assert by_id["in-progress"].issue_type == "external_action_stuck_in_progress"
    assert by_id["prepared"].issue_type == "external_action_stale_prepared"
    assert "recent-prepared" not in by_id


def test_operational_probe_surfaces_failed_and_stuck_loops_but_not_human_waits():
    database = SQLiteDatabase()
    loops = SQLiteCareerLoopRepository(database)
    old = NOW - timedelta(hours=2)

    failed = CareerLoopState(
        run_id="failed-loop",
        request=request(),
        phase=CareerLoopPhase.FAILED,
        last_error="provider failure",
        created_at=old,
        updated_at=old,
    )
    loops.save(failed)

    stuck = CareerLoopState(
        run_id="stuck-loop",
        request=request(),
        phase=CareerLoopPhase.APPLICATION_SUBMIT,
        created_at=old,
        updated_at=old,
    )
    loops.save(stuck)

    waiting = CareerLoopState(
        run_id="waiting-loop",
        request=request(),
        phase=CareerLoopPhase.APPLICATION_APPROVAL,
        pending_human_action=HumanActionEvent(
            kind=HumanActionKind.APPROVE_APPLICATION,
            title="Approve",
            details={"application_id": "app-1"},
        ),
        created_at=old,
        updated_at=old,
    )
    loops.save(waiting)

    issues = SQLiteOperationalProbe(database).inspect(
        now=NOW,
        stale_after_seconds=3600,
    )

    by_id = {issue.entity_id: issue for issue in issues}
    assert by_id["failed-loop"].issue_type == "career_loop_failed"
    assert by_id["failed-loop"].details["error"] == "provider failure"
    assert by_id["stuck-loop"].issue_type == "career_loop_stuck"
    assert by_id["stuck-loop"].severity == OperationalSeverity.WARNING
    assert "waiting-loop" not in by_id



def test_operational_probe_redacts_persisted_errors_before_exposure():
    database = SQLiteDatabase()
    actions = SQLiteExternalActionOperationRepository(database)
    old = NOW - timedelta(hours=2)
    _create_operation(actions, "failed-secret", ExternalActionStatus.FAILED, updated_at=old)
    database.connection.execute(
        "UPDATE external_action_operations SET error = ? WHERE operation_id = ?",
        ("Authorization: Bearer super-secret-token", "failed-secret"),
    )

    loops = SQLiteCareerLoopRepository(database)
    failed = CareerLoopState(
        run_id="failed-secret-loop",
        request=request(),
        phase=CareerLoopPhase.FAILED,
        last_error="api_key=very-secret-value",
        created_at=old,
        updated_at=old,
    )
    loops.save(failed)
    database.connection.commit()

    issues = SQLiteOperationalProbe(database).inspect(
        now=NOW,
        stale_after_seconds=3600,
    )
    by_id = {issue.entity_id: issue for issue in issues}

    assert "super-secret-token" not in str(by_id["failed-secret"].details)
    assert "[REDACTED]" in str(by_id["failed-secret"].details)
    assert "very-secret-value" not in str(by_id["failed-secret-loop"].details)
    assert "[REDACTED]" in str(by_id["failed-secret-loop"].details)


def test_operational_probe_does_not_flag_loop_with_live_execution_lease():
    database = SQLiteDatabase()
    loops = SQLiteCareerLoopRepository(database)
    old = NOW - timedelta(hours=2)
    state = CareerLoopState(
        run_id="leased-loop",
        request=request(),
        phase=CareerLoopPhase.APPLICATION_SUBMIT,
        created_at=old,
        updated_at=old,
    )
    loops.save(state)
    database.connection.execute(
        """
        UPDATE autonomous_career_loops
        SET execution_claim_owner = ?, execution_claim_expires_at = ?
        WHERE run_id = ?
        """,
        ("worker-1", (NOW + timedelta(minutes=30)).isoformat(), "leased-loop"),
    )
    database.connection.commit()

    issues = SQLiteOperationalProbe(database).inspect(
        now=NOW,
        stale_after_seconds=3600,
    )

    assert "leased-loop" not in {issue.entity_id for issue in issues}


def test_operational_probe_reports_loop_after_execution_lease_expires():
    database = SQLiteDatabase()
    loops = SQLiteCareerLoopRepository(database)
    old = NOW - timedelta(hours=2)
    state = CareerLoopState(
        run_id="expired-lease-loop",
        request=request(),
        phase=CareerLoopPhase.APPLICATION_SUBMIT,
        created_at=old,
        updated_at=old,
    )
    loops.save(state)
    database.connection.execute(
        """
        UPDATE autonomous_career_loops
        SET execution_claim_owner = ?, execution_claim_expires_at = ?
        WHERE run_id = ?
        """,
        ("worker-1", (NOW - timedelta(minutes=1)).isoformat(), "expired-lease-loop"),
    )
    database.connection.commit()

    issues = SQLiteOperationalProbe(database).inspect(
        now=NOW,
        stale_after_seconds=3600,
    )

    assert "expired-lease-loop" in {issue.entity_id for issue in issues}

def test_operational_probe_is_safe_before_optional_tables_exist():
    issues = SQLiteOperationalProbe(SQLiteDatabase()).inspect(
        now=NOW,
        stale_after_seconds=60,
    )
    assert issues == ()


@pytest.mark.parametrize(
    "now,stale",
    [
        (datetime(2026, 1, 1), 60),
        (NOW, 0),
        ("not-a-date", 60),
    ],
)
def test_operational_probe_validates_inputs(now, stale):
    with pytest.raises((TypeError, ValueError)):
        SQLiteOperationalProbe(SQLiteDatabase()).inspect(
            now=now,
            stale_after_seconds=stale,
        )


def test_migration_runner_applies_once_and_verifies_checksum():
    database = SQLiteDatabase()
    runner = SQLiteMigrationRunner(database)
    migration = SQLiteMigration(
        version=1,
        name="create_example",
        statements=(
            "CREATE TABLE migration_example(id TEXT PRIMARY KEY, value TEXT NOT NULL)",
            "CREATE INDEX idx_migration_example_value ON migration_example(value)",
        ),
    )

    assert runner.apply((migration,)) == (1,)
    assert runner.apply((migration,)) == ()
    assert runner.applied_versions() == (1,)

    changed = SQLiteMigration(
        version=1,
        name="create_example",
        statements=("CREATE TABLE migration_example(id TEXT PRIMARY KEY)",),
    )
    with pytest.raises(ValueError, match="differs"):
        runner.apply((changed,))


def test_migration_checksum_includes_destructive_policy():
    database = SQLiteDatabase()
    runner = SQLiteMigrationRunner(database)
    destructive = SQLiteMigration(
        version=1,
        name="policy-bound",
        statements=("CREATE TABLE policy_bound(id TEXT PRIMARY KEY)",),
        destructive=True,
    )
    non_destructive = SQLiteMigration(
        version=1,
        name="policy-bound",
        statements=("CREATE TABLE policy_bound(id TEXT PRIMARY KEY)",),
        destructive=False,
    )

    assert destructive.checksum != non_destructive.checksum
    assert runner.apply((destructive,), allow_destructive=True) == (1,)
    with pytest.raises(ValueError, match="differs"):
        runner.apply((non_destructive,))


def test_migration_rejects_non_boolean_destructive_metadata():
    with pytest.raises(TypeError, match="destructive flag"):
        SQLiteMigration(
            version=1,
            name="bad-policy",
            statements=("SELECT 1",),
            destructive="false",
        )


def test_migration_runner_rolls_back_failure_and_requires_destructive_approval():
    database = SQLiteDatabase()
    runner = SQLiteMigrationRunner(database)
    bad = SQLiteMigration(
        version=1,
        name="broken",
        statements=(
            "CREATE TABLE should_rollback(id TEXT PRIMARY KEY)",
            "INSERT INTO missing_table(value) VALUES ('x')",
        ),
    )

    with pytest.raises(Exception):
        runner.apply((bad,))

    assert database.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='should_rollback'"
    ).fetchone() is None
    assert runner.applied_versions() == ()

    destructive = SQLiteMigration(
        version=2,
        name="drop_old",
        statements=("DROP TABLE IF EXISTS old_table",),
        destructive=True,
    )
    with pytest.raises(PermissionError, match="explicit approval"):
        runner.apply((destructive,))
    assert runner.apply((destructive,), allow_destructive=True) == (2,)




def test_migration_runner_rejects_truthy_non_boolean_destructive_approval():
    database = SQLiteDatabase()
    runner = SQLiteMigrationRunner(database)
    destructive = SQLiteMigration(
        version=1,
        name="destructive_guard",
        statements=("CREATE TABLE destructive_guard(id TEXT PRIMARY KEY)",),
        destructive=True,
    )

    with pytest.raises(TypeError, match="allow_destructive must be a boolean"):
        runner.apply((destructive,), allow_destructive="false")

    assert runner.applied_versions() == ()
    assert database.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='destructive_guard'"
    ).fetchone() is None

def test_migration_runner_converges_under_concurrent_deployers(tmp_path):
    path = str(tmp_path / "migration-race.sqlite")
    setup = SQLiteDatabase(path)
    SQLiteMigrationRunner(setup)
    setup.close()
    barrier = Barrier(2)
    migration = SQLiteMigration(
        version=1,
        name="create_race_table",
        statements=("CREATE TABLE race_table(id TEXT PRIMARY KEY)",),
    )

    def apply_once():
        database = SQLiteDatabase(path)
        runner = SQLiteMigrationRunner(database)
        try:
            barrier.wait(timeout=5)
            return runner.apply((migration,))
        finally:
            database.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: apply_once(), range(2)))

    assert sorted(results, key=len) == [(), (1,)]
    verify = SQLiteDatabase(path)
    runner = SQLiteMigrationRunner(verify)
    assert runner.applied_versions() == (1,)
    assert verify.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='race_table'"
    ).fetchone() is not None
    verify.close()

@pytest.mark.parametrize(
    "migration",
    [
        SQLiteMigration(version=2, name="second", statements=("SELECT 1",)),
        SQLiteMigration(version=1, name="first", statements=("SELECT 1",)),
    ],
)
def test_migration_runner_rejects_unsorted_input(migration):
    runner = SQLiteMigrationRunner(SQLiteDatabase())
    other = SQLiteMigration(
        version=3 if migration.version == 1 else 1,
        name="other",
        statements=("SELECT 1",),
    )
    values = (migration, other)
    if [item.version for item in values] == sorted(item.version for item in values):
        values = tuple(reversed(values))
    with pytest.raises(ValueError, match="ascending"):
        runner.apply(values)


def _wsgi_call(app, path, *, query="", token=None, method="GET", scheme="Bearer"):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
    }
    if token is not None:
        environ["HTTP_AUTHORIZATION"] = f"{scheme} {token}"
    chunks = app(environ, start_response)
    body = json.loads(b"".join(chunks).decode("utf-8"))
    return captured, body


def test_operational_api_is_authenticated_read_only_and_openapi_matches():
    database = SQLiteDatabase()
    probe = SQLiteOperationalProbe(database)
    service = OperationalApiService(probe, clock=lambda: NOW)
    token = "t" * 40
    app = OperationalWSGIApp(service, bearer_token=token)

    health_meta, health = _wsgi_call(app, "/healthz")
    assert health_meta["status"] == "200 OK"
    assert health == {"status": "ok"}

    denied_meta, denied = _wsgi_call(app, "/v1/operational/issues")
    assert denied_meta["status"] == "401 Unauthorized"
    assert denied["error"] == "unauthorized"

    ok_meta, payload = _wsgi_call(
        app,
        "/v1/operational/issues",
        query="stale_after_seconds=60",
        token=token,
    )
    assert ok_meta["status"] == "200 OK"
    assert payload["generated_at"] == NOW.isoformat()
    assert payload["issues"] == []
    assert ok_meta["headers"]["Cache-Control"] == "no-store"

    spec = openapi_document()
    assert spec["openapi"] == "3.1.0"
    assert "bearerAuth" in spec["components"]["securitySchemes"]
    assert "/healthz" in spec["paths"]
    assert "/v1/operational/issues" in spec["paths"]



@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER", "bEaReR", "bearer ", "BEARER   "])
def test_operational_wsgi_accepts_case_insensitive_scheme_but_not_wrong_token(scheme):
    database = SQLiteDatabase()
    service = OperationalApiService(SQLiteOperationalProbe(database), clock=lambda: NOW)
    token = "CaseSensitiveToken" * 3
    app = OperationalWSGIApp(service, bearer_token=token)
    accepted, _ = _wsgi_call(app, "/v1/operational/issues", token=token, scheme=scheme)
    denied, body = _wsgi_call(app, "/v1/operational/issues", token=token.lower(), scheme=scheme)
    assert accepted["status"] == "200 OK"
    assert denied["status"] == "401 Unauthorized"
    assert body == {"error": "unauthorized"}
    database.close()


@pytest.mark.parametrize("token", ["x" * 31 + " ", " " + "x" * 31, " " + "x" * 30 + " "])
@pytest.mark.parametrize("scheme", ["Bearer", "bearer ", "BEARER   "])
def test_operational_wsgi_preserves_configured_credential_whitespace(token, scheme):
    database = SQLiteDatabase()
    app = OperationalWSGIApp(
        OperationalApiService(SQLiteOperationalProbe(database), clock=lambda: NOW),
        bearer_token=token,
    )
    accepted, _ = _wsgi_call(app, "/v1/operational/issues", token=token, scheme=scheme)
    assert accepted["status"] == "200 OK"
    denied, _ = _wsgi_call(app, "/v1/operational/issues", token=token + " ", scheme=scheme)
    assert denied["status"] == "401 Unauthorized"
    foreign_prefix, _ = _wsgi_call(app, "/v1/operational/issues", token="z" + token, scheme=scheme)
    assert foreign_prefix["status"] == "401 Unauthorized"
    database.close()


def test_operational_wsgi_non_ascii_request_token_is_unauthorized():
    service = OperationalApiService(
        SQLiteOperationalProbe(SQLiteDatabase()),
        clock=lambda: NOW,
    )
    app = OperationalWSGIApp(service, bearer_token="a" * 40)

    meta, body = _wsgi_call(
        app,
        "/v1/operational/issues",
        token="ä" * 40,
    )

    assert meta["status"] == "401 Unauthorized"
    assert body["error"] == "unauthorized"


def test_operational_wsgi_rejects_non_ascii_configured_token():
    service = OperationalApiService(SQLiteOperationalProbe(SQLiteDatabase()))

    with pytest.raises(ValueError, match="ASCII"):
        OperationalWSGIApp(service, bearer_token="ä" * 40)


def test_operational_wsgi_returns_sanitized_500_for_probe_failure():
    class BrokenProbe:
        def inspect(self, *, now, stale_after_seconds):
            raise ValueError("corrupt persisted secret=do-not-expose")

    service = OperationalApiService(BrokenProbe(), clock=lambda: NOW)
    token = "p" * 40
    app = OperationalWSGIApp(service, bearer_token=token)

    meta, body = _wsgi_call(
        app,
        "/v1/operational/issues",
        query="stale_after_seconds=60",
        token=token,
    )

    assert meta["status"] == "500 Internal Server Error"
    assert body == {"error": "internal_error"}
    assert "do-not-expose" not in str(body)


def test_operational_wsgi_serialization_failure_is_sanitized_500():
    class NonJsonOperationalService:
        def health(self):
            return {"status": "ok"}

        def issues(self, *, stale_after_seconds):
            return {
                "generated_at": NOW.isoformat(),
                "issues": [
                    {
                        "issue_type": "example",
                        "entity_id": "entity-1",
                        "severity": "warning",
                        "updated_at": NOW.isoformat(),
                        "details": {"non_finite": float("nan")},
                    }
                ],
            }

    token = "s" * 40
    app = OperationalWSGIApp(NonJsonOperationalService(), bearer_token=token)

    meta, body = _wsgi_call(
        app,
        "/v1/operational/issues",
        query="stale_after_seconds=60",
        token=token,
    )

    assert meta["status"] == "500 Internal Server Error"
    assert body == {"error": "internal_error"}


@pytest.mark.parametrize(
    "path,query,method,expected",
    [
        ("/missing", "", "GET", "404 Not Found"),
        ("/healthz", "", "POST", "405 Method Not Allowed"),
        ("/v1/operational/issues", "stale_after_seconds=0", "GET", "400 Bad Request"),
        ("/v1/operational/issues", "stale_after_seconds=nope", "GET", "400 Bad Request"),
    ],
)
def test_operational_wsgi_rejects_invalid_requests(path, query, method, expected):
    service = OperationalApiService(
        SQLiteOperationalProbe(SQLiteDatabase()),
        clock=lambda: NOW,
    )
    token = "z" * 40
    app = OperationalWSGIApp(service, bearer_token=token)
    meta, _ = _wsgi_call(app, path, query=query, method=method, token=token)
    assert meta["status"] == expected


@pytest.mark.parametrize("token", ["short", "x" * 4097, "bad\n" + "x" * 40])
def test_operational_wsgi_rejects_unsafe_auth_tokens(token):
    service = OperationalApiService(SQLiteOperationalProbe(SQLiteDatabase()))
    with pytest.raises((TypeError, ValueError)):
        OperationalWSGIApp(service, bearer_token=token)


@pytest.mark.parametrize('phase,kind', [
    (CareerLoopPhase.SEARCH, None),
    (CareerLoopPhase.APPLICATION_APPROVAL, HumanActionKind.APPROVE_APPLICATION),
    (CareerLoopPhase.APPLICATION_RECONCILIATION, HumanActionKind.RECONCILE_APPLICATION_SUBMISSION),
    (CareerLoopPhase.COMPLETE, None),
    (CareerLoopPhase.FAILED, None),
])
def test_candidate_status_is_restart_safe_private_and_read_only(tmp_path, monkeypatch, phase, kind):
    from career_agent_ai.application.api import candidate_approval_openapi_document
    from career_agent_ai.application.career.autonomous_career_loop import AutonomousCareerLoop

    database_path = str(tmp_path / 'status.sqlite')
    database = SQLiteDatabase(database_path)
    repository = SQLiteCareerLoopRepository(database)
    state = CareerLoopState(
        run_id='scoped-v2:' + 'a' * 128, request=request(), phase=phase,
        last_error='secret provider credentials',
        pending_human_action=None if kind is None else HumanActionEvent(
            kind=kind, title='private title', details={'secret': 'private content'},
        ),
    )
    repository.save(state)
    before = repository.get(state.run_id)
    database.close()

    def forbidden(*args, **kwargs):
        pytest.fail('GET status must never execute or persist a run')

    monkeypatch.setattr(AutonomousCareerLoop, '_continue', forbidden)
    monkeypatch.setattr(SQLiteCareerLoopRepository, 'save', forbidden)
    monkeypatch.setattr(SQLiteCareerLoopRepository, 'claim_execution', forbidden)
    with build_candidate_sandbox_app_from_env(
        resolve_bearer=lambda token: {'owner': 'user-1', 'foreign': 'user-2'}.get(token),
        environ={
            'CAREER_AGENT_DB_PATH': database_path,
            'CAREER_AGENT_CANDIDATE_SANDBOX': 'true',
            'CAREER_AGENT_CANDIDATE_ID_CUTOVER': 'drain-and-replace-scoped-v2',
        },
    ) as runtime:
        path = f'/v1/candidate/runs/{state.run_id}/status'
        with ThreadPoolExecutor(max_workers=1) as executor:
            meta, payload = executor.submit(_wsgi_call, runtime, path, token='owner').result()
        assert meta['status'] == '200 OK'
        assert payload == {
            'schema_version': 1, 'run_id': state.run_id, 'state_version': before.version,
            'phase': phase.value, 'waiting_for_human': kind is not None,
            'terminal': phase in {CareerLoopPhase.COMPLETE, CareerLoopPhase.FAILED},
        }
        assert meta['headers']['Cache-Control'] == 'no-store'
        foreign = _wsgi_call(runtime, path, token='foreign')
        missing = _wsgi_call(runtime, '/v1/candidate/runs/missing/status', token='owner')
        assert foreign == missing
        assert foreign[0]['status'] == '404 Not Found'
        schema = candidate_approval_openapi_document()['paths']['/v1/candidate/runs/{run_id}/status']['get']
        assert schema['security'] == [{'bearerAuth': []}]
        properties = schema['responses']['200']['content']['application/json']['schema']
        assert set(properties['required']) == set(payload)
        assert properties['additionalProperties'] is False
    reopened = SQLiteDatabase(database_path)
    try:
        assert SQLiteCareerLoopRepository(reopened).get(state.run_id) == before
    finally:
        reopened.close()


def test_candidate_status_does_not_recreate_missing_database(tmp_path):
    database_path = tmp_path / "missing-after-start.sqlite"
    runtime = build_candidate_sandbox_app_from_env(
        resolve_bearer=lambda token: "user-1" if token == "owner" else None,
        environ={
            "CAREER_AGENT_DB_PATH": str(database_path),
            "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
            "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
        },
    )
    database_path.unlink()
    try:
        meta, payload = _wsgi_call(
            runtime, "/v1/candidate/runs/missing/status", token="owner"
        )
        assert meta["status"] == "500 Internal Server Error"
        assert payload == {"error": "internal_error"}
        assert not database_path.exists()
    finally:
        runtime.close()


def test_candidate_status_checks_owner_before_decoding_malformed_payload(tmp_path):
    database_path = str(tmp_path / "malformed-foreign.sqlite")
    database = SQLiteDatabase(database_path)
    repository = SQLiteCareerLoopRepository(database)
    foreign = CareerLoopState(run_id="foreign-run", request=CareerLoopRequest(
        user_id="user-2", keyword="logistics", candidate_profile=PROFILE,
    ))
    repository.save(foreign)
    database.connection.execute(
        "UPDATE autonomous_career_loops SET payload_json = ? WHERE run_id = ?",
        ("{malformed private state", foreign.run_id),
    )
    database.connection.commit()
    database.close()

    with build_candidate_sandbox_app_from_env(
        resolve_bearer=lambda token: {"owner": "user-1", "foreign": "user-2"}.get(token),
        environ={
            "CAREER_AGENT_DB_PATH": database_path,
            "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
            "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
        },
    ) as runtime:
        hidden = _wsgi_call(runtime, "/v1/candidate/runs/foreign-run/status", token="owner")
        missing = _wsgi_call(runtime, "/v1/candidate/runs/missing/status", token="owner")
        assert hidden == missing
        assert hidden[0]["status"] == "404 Not Found"
        owned_meta, owned_payload = _wsgi_call(
            runtime, "/v1/candidate/runs/foreign-run/status", token="foreign"
        )
        assert owned_meta["status"] == "500 Internal Server Error"
        assert owned_payload == {"error": "internal_error"}
