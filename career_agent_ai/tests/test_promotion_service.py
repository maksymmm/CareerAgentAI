"""Local HTTP smoke and startup policy tests for the operator service."""

import json
from http.client import HTTPConnection
from threading import Thread

import pytest

from career_agent_ai.application.runtime.promotion_service import PromotionServiceConfig, promotion_service_from_env
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import SQLiteExternalActionOperationRepository
from career_agent_ai.tests.test_promotion_approval_api import operation


def environment(tmp_path):
    return {"CAREER_AGENT_PROMOTION_SANDBOX": "true", "CAREER_AGENT_PROMOTION_PORT": "0",
            "CAREER_AGENT_PROMOTION_OPERATOR_ID": "operator", "CAREER_AGENT_PROMOTION_BEARER_TOKEN": "s" * 40,
            "CAREER_AGENT_DB_PATH": str(tmp_path / "operator.sqlite")}


@pytest.mark.parametrize("override", [
    {"CAREER_AGENT_PROMOTION_HOST": "0.0.0.0"}, {"CAREER_AGENT_PROMOTION_HOST": "::1"},
    {"CAREER_AGENT_PROMOTION_PORT": "65536"}, {"CAREER_AGENT_PROMOTION_PORT": "-1"},
    {"CAREER_AGENT_PROMOTION_BEARER_TOKEN": "short"}, {"CAREER_AGENT_PROMOTION_OPERATOR_ID": ""},
    {"CAREER_AGENT_PROMOTION_BEARER_TOKEN": " " + "s" * 40},
])
def test_invalid_settings_fail_before_storage(tmp_path, override):
    with pytest.raises((ValueError, TypeError)):
        with promotion_service_from_env({**environment(tmp_path), **override}):
            pytest.fail("must not start")
    assert not (tmp_path / "operator.sqlite").exists()


def test_auth_is_constant_identity_and_token_not_in_repr(tmp_path):
    config = PromotionServiceConfig.from_env(environment(tmp_path))
    assert config.bearer_token not in repr(config)
    for token in ("invalid", "ß", None):
        assert config.resolve_bearer(token) is None
    operator = config.resolve_bearer(config.bearer_token)
    assert operator.operator_id == "operator"
    assert operator.scopes == frozenset({"release:promote:dry-run"})


def exchange(server, method, suffix, token, payload=None):
    worker = Thread(target=server.handle_request)
    worker.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        body = None if payload is None else json.dumps(payload)
        connection.request(method, "/v1/release/promotions/promote:release-1/" + suffix,
            body=body, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()
        worker.join(timeout=5)
        assert not worker.is_alive()


def test_http_approval_restart_execute_and_recovery(tmp_path):
    env = environment(tmp_path)
    database = SQLiteDatabase(env["CAREER_AGENT_DB_PATH"])
    SQLiteExternalActionOperationRepository(database).create(operation())
    database.close()
    token = env["CAREER_AGENT_PROMOTION_BEARER_TOKEN"]
    with promotion_service_from_env(env) as server:
        assert exchange(server, "GET", "approval", "wrong")[0] == 401
        status, prompt = exchange(server, "GET", "approval", token)
        assert status == 200
        assert exchange(server, "POST", "approval", token, {"schema_version": 1,
            "action_fingerprint": prompt["action_fingerprint"], "decision": "approve"})[0] == 200
    assert server.socket.fileno() == -1
    with promotion_service_from_env(env) as restarted:
        assert exchange(restarted, "POST", "execute-dry-run", token, {"schema_version": 1})[0] == 200
        assert exchange(restarted, "POST", "execute-dry-run", token, {"schema_version": 1})[0] == 409
        status, result = exchange(restarted, "POST", "recover-dry-run", token, {"schema_version": 1})
        assert status == 200
        assert result["execution_mode"] == "dry-run"


def test_bind_failure_closes_sandbox(tmp_path, monkeypatch):
    from career_agent_ai.application.runtime import promotion_service
    from career_agent_ai.application.runtime.promotion_sandbox import RuntimePromotionSandboxApp
    closed = []
    original = RuntimePromotionSandboxApp.close
    def close(app):
        closed.append(app)
        original(app)
    def fail(*args):
        raise OSError("listener unavailable")
    monkeypatch.setattr(RuntimePromotionSandboxApp, "close", close)
    monkeypatch.setattr(promotion_service, "make_server", fail)
    with pytest.raises(OSError):
        with promotion_service_from_env(environment(tmp_path)):
            pytest.fail("must not start")
    assert len(closed) == 1


@pytest.mark.parametrize("state", ["missing", "empty", "corrupt", "ready"])
def test_probes_are_read_only_sanitized_and_do_not_require_auth(tmp_path, state):
    from career_agent_ai.application.runtime.promotion_service import PromotionServiceWSGIApp
    from career_agent_ai.tests.test_promotion_approval_api import request
    path = tmp_path / "probe.sqlite"
    if state == "empty":
        path.touch()
    elif state == "corrupt":
        path.write_bytes(b"private corrupt storage detail")
    elif state == "ready":
        database = SQLiteDatabase(str(path))
        SQLiteExternalActionOperationRepository(database).create(operation())
        database.close()
    before = path.read_bytes() if path.exists() else None
    def forbidden(*args):
        raise AssertionError("probe must not reach workflow")
    application = PromotionServiceWSGIApp(forbidden, database_path=str(path))
    assert request(application, path="/healthz", token="")[0][0] == "200 OK"
    (status, headers), payload = request(application, path="/readyz", token="")
    assert status == ("200 OK" if state == "ready" else "503 Service Unavailable")
    assert payload == {"status": "ok" if state == "ready" else "unavailable"}
    assert ("Cache-Control", "no-store") in headers
    assert request(application, method="POST", path="/readyz", body={})[0][0] == "405 Method Not Allowed"
    assert (path.read_bytes() if path.exists() else None) == before


def test_http_probe_routes_and_openapi(tmp_path):
    from career_agent_ai.application.runtime.promotion_service import promotion_service_openapi_document
    with promotion_service_from_env(environment(tmp_path)) as server:
        for path in ("/healthz", "/readyz"):
            worker = Thread(target=server.handle_request)
            worker.start()
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.request("GET", path)
                response = connection.getresponse()
                assert response.status == 200
                assert json.loads(response.read()) == {"status": "ok"}
            finally:
                connection.close()
                worker.join(timeout=5)
    document = promotion_service_openapi_document()
    assert document["paths"]["/readyz"]["get"]["security"] == []
    assert "503" in document["paths"]["/readyz"]["get"]["responses"]


@pytest.mark.parametrize("missing", ["result_json", "error", "created_at", "updated_at", "all_four"])
def test_readiness_rejects_partial_workflow_schema_without_mutation(tmp_path, missing):
    import sqlite3
    from career_agent_ai.application.runtime.promotion_service import PromotionServiceWSGIApp
    from career_agent_ai.tests.test_promotion_approval_api import request
    path = tmp_path / "partial.sqlite"
    columns = ("operation_id", "action_type", "payload_json", "status", "result_json", "error", "created_at", "updated_at")
    retained = columns[:4] if missing == "all_four" else tuple(column for column in columns if column != missing)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE external_action_operations (" + ", ".join(column + " TEXT" for column in retained) + ")")
    before = path.read_bytes()
    app = PromotionServiceWSGIApp(lambda *args: pytest.fail("probe reached workflow"), database_path=str(path))
    (status, _), payload = request(app, path="/readyz", token="")
    assert status == "503 Service Unavailable"
    assert payload == {"status": "unavailable"}
    assert path.read_bytes() == before


@pytest.mark.parametrize("key", ["none", "partial", "composite", "unique", "primary"])
def test_readiness_requires_single_column_nonpartial_unique_operation_key(tmp_path, key):
    import sqlite3
    from career_agent_ai.application.runtime.promotion_service import PromotionServiceWSGIApp
    from career_agent_ai.tests.test_promotion_approval_api import request
    path = tmp_path / "keys.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE external_action_operations (operation_id TEXT" +
            (" PRIMARY KEY" if key == "primary" else "") +
            ", action_type TEXT, payload_json TEXT, status TEXT, result_json TEXT, error TEXT, created_at TEXT, updated_at TEXT)")
        if key in {"partial", "composite", "unique"}:
            connection.execute("CREATE UNIQUE INDEX operation_key ON external_action_operations (" +
                ("operation_id, action_type" if key == "composite" else "operation_id") + ")" +
                (" WHERE status='prepared'" if key == "partial" else ""))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    app = PromotionServiceWSGIApp(lambda *args: None, database_path=str(path))
    assert request(app, path="/readyz")[0][0] == ("200 OK" if key in {"primary", "unique"} else "503 Service Unavailable")
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_wal_readiness_fails_closed_without_creating_sidecars(tmp_path):
    from career_agent_ai.application.runtime.promotion_service import PromotionServiceWSGIApp
    from career_agent_ai.tests.test_promotion_approval_api import request
    path = tmp_path / "wal.sqlite"
    database = SQLiteDatabase(str(path))
    SQLiteExternalActionOperationRepository(database)
    database.connection.execute("PRAGMA journal_mode=WAL")
    database.close()
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    app = PromotionServiceWSGIApp(lambda *args: None, database_path=str(path))
    assert request(app, path="/readyz")[0][0] == "503 Service Unavailable"
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
