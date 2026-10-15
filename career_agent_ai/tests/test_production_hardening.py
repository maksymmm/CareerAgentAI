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


def request() -> CareerLoopRequest:
    return CareerLoopRequest(
        user_id="user-1",
        keyword="logistics",
        candidate_profile=PROFILE,
    )


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
            page.locator("#token").fill("sandbox-token")
            page.locator("#run-id").fill(durable_run_id)
            page.locator("#status").click()
            page.locator("#result").get_by_text('"phase": "complete"').wait_for()
            assert page.locator("#run-id").input_value() == durable_run_id
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



@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER", "bEaReR"])
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
