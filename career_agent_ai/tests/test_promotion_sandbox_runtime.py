"""Sandbox composition proves durable operator HTTP behavior without live I/O."""

from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from career_agent_ai.application.api import ReleaseOperator
from career_agent_ai.application.runtime.promotion_sandbox import build_promotion_sandbox_app_from_env
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import SQLiteExternalActionOperationRepository
from career_agent_ai.tests.test_promotion_approval_api import operation, request


def environment(tmp_path):
    return {"CAREER_AGENT_PROMOTION_SANDBOX": "true", "CAREER_AGENT_DB_PATH": str(tmp_path / "promotion.sqlite")}


def resolve(token):
    return ReleaseOperator("owner", frozenset({"release:promote:dry-run"})) if token == "valid" else None


@pytest.mark.parametrize("override", [
    {"CAREER_AGENT_PROMOTION_SANDBOX": "false"},
    {"CAREER_AGENT_ALLOW_NETWORK_PROVIDERS": "true"},
    {"CAREER_AGENT_ALLOW_CONSEQUENTIAL_ACTIONS": "true"},
    {"CAREER_AGENT_DB_PATH": ":memory:"},
    {"CAREER_AGENT_DB_PATH": "file:memory?mode=memory&cache=shared"},
])
def test_unsafe_policy_rejected_before_storage_creation(tmp_path, override):
    with pytest.raises((ValueError, PermissionError)):
        build_promotion_sandbox_app_from_env(resolve_bearer=resolve, environ={**environment(tmp_path), **override})
    assert not (tmp_path / "promotion.sqlite").exists()


def test_http_approval_execute_restart_and_replay(tmp_path):
    env = environment(tmp_path)
    database = SQLiteDatabase(env["CAREER_AGENT_DB_PATH"])
    SQLiteExternalActionOperationRepository(database).create(operation())
    database.close()
    with build_promotion_sandbox_app_from_env(resolve_bearer=resolve, environ=env) as runtime:
        _, prompt = request(runtime)
        body = {"schema_version": 1, "action_fingerprint": prompt["action_fingerprint"], "decision": "approve"}
        assert request(runtime, method="POST", body=body)[0][0] == "200 OK"
    runtime = build_promotion_sandbox_app_from_env(resolve_bearer=resolve, environ=env)
    path = "/v1/release/promotions/promote:release-1/execute-dry-run"
    assert request(runtime, method="POST", path=path, body={"schema_version": 1})[0][0] == "200 OK"
    runtime.close()
    with build_promotion_sandbox_app_from_env(resolve_bearer=resolve, environ=env) as restarted:
        assert request(restarted, method="POST", path=path, body={"schema_version": 1})[0][0] == "409 Conflict"
        status, result = request(restarted, method="POST", path=path.replace("execute", "recover"), body={"schema_version": 1})
        assert status[0] == "200 OK"
        assert result["receipt_status"] == "succeeded"
    with pytest.raises(RuntimeError):
        request(runtime)
    with pytest.raises(RuntimeError):
        runtime.__enter__()


def test_auth_and_routing_precede_storage_and_connections_are_closed(tmp_path, monkeypatch):
    from career_agent_ai.application.runtime import promotion_sandbox
    runtime = build_promotion_sandbox_app_from_env(resolve_bearer=resolve, environ=environment(tmp_path))
    opened = []
    original = promotion_sandbox.SQLiteDatabase

    def track(path):
        database = original(path)
        opened.append(database)
        return database

    monkeypatch.setattr(promotion_sandbox, "SQLiteDatabase", track)
    assert request(runtime, token="invalid")[0][0] == "401 Unauthorized"
    assert request(runtime, path="/missing")[0][0] == "404 Not Found"
    assert opened == []
    # Concurrent workers must open/close their own connections in their own thread.
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: request(runtime)[0][0], range(2)))
    assert responses == ["404 Not Found", "404 Not Found"]
    assert len(opened) == 2
    for database in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            database.connection.execute("SELECT 1")


def test_response_failure_closes_connection(tmp_path, monkeypatch):
    from career_agent_ai.application.runtime import promotion_sandbox
    runtime = build_promotion_sandbox_app_from_env(resolve_bearer=resolve, environ=environment(tmp_path))
    closed = []
    original = SQLiteDatabase.close

    def close(database):
        closed.append(database)
        original(database)

    monkeypatch.setattr(SQLiteDatabase, "close", close)
    def fail(*args):
        raise RuntimeError("response failed")
    with pytest.raises(RuntimeError):
        runtime({"PATH_INFO": "/v1/release/promotions/missing/approval", "HTTP_AUTHORIZATION": "Bearer valid"}, fail)
    assert len(closed) == 1


def test_invalid_resolver_is_rejected_before_storage(tmp_path):
    with pytest.raises(TypeError):
        build_promotion_sandbox_app_from_env(resolve_bearer=None, environ=environment(tmp_path))
    assert not (tmp_path / "promotion.sqlite").exists()
