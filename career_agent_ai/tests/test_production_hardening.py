from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

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
from career_agent_ai.application.observability import (
    JsonLogFormatter,
    OperationalSeverity,
    correlation_scope,
    current_correlation_id,
    log_event,
    redact_mapping,
)
from career_agent_ai.application.runtime import RuntimeConfig, RuntimeEnvironment
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
            "items": list(range(150)),
        }
    )

    assert len(result["body"]) == 2000
    assert result["authorization_header"] == "[REDACTED]"
    assert len(result["items"]) == 100


@pytest.mark.parametrize("correlation_id", ["", "x" * 201, "bad\nvalue"])
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


def _wsgi_call(app, path, *, query="", token=None, method="GET"):
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
        environ["HTTP_AUTHORIZATION"] = f"Bearer {token}"
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
