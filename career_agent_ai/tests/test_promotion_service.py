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
