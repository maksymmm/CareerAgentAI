from __future__ import annotations

import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

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
from career_agent_ai.application.runtime import (
    RuntimeConfig,
    RuntimeEnvironment,
    build_external_action_service,
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
    assert guard_signal_provider(config, signals).collect() == ()
    assert result.status == ExternalActionStatus.SUCCEEDED
    assert adapter.calls == 1
    assert signals.calls == 1

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
