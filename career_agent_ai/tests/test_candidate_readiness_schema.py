"""Candidate readiness and bootstrap require complete durable provider ledgers."""

import sqlite3

import pytest

from career_agent_ai.application.runtime.candidate_schema import (
    _validate_candidate_schema,
    _validate_loop_schema,
)
from career_agent_ai.application.runtime.candidate_service import CandidateServiceWSGIApp
from career_agent_ai.application.runtime.composition import _build_candidate_loop
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


REQUIRED_LOOP = (
    "run_id",
    "owner_user_id",
    "phase",
    "payload_json",
    "updated_at",
    "version",
    "execution_claim_owner",
    "execution_claim_expires_at",
)

PROVIDER_TABLES = (
    "external_action_operations",
    "sandbox_submission_intents",
    "sandbox_submission_attempts",
    "sandbox_submission_ledger",
    "sandbox_communication_messages",
    "sandbox_communication_intents",
    "sandbox_communication_attempts",
    "sandbox_communication_deliveries",
    "sandbox_calendar_intents",
    "sandbox_calendar_attempts",
    "sandbox_calendar_effects",
)


class _DelegatedApp:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, environ, start_response):
        self.calls += 1
        start_response("200 OK", [("Content-Length", "9")])
        return [b"delegated"]

    def close(self) -> None:
        pass


def _initialize_candidate_database(path) -> None:
    database = SQLiteDatabase(str(path))
    try:
        _build_candidate_loop(database, (), None)
    finally:
        database.close()


def _request(app, path: str, *, method: str = "GET", authorization: str | None = None):
    statuses: list[str] = []
    environ = {"PATH_INFO": path, "REQUEST_METHOD": method}
    if authorization is not None:
        environ["HTTP_AUTHORIZATION"] = authorization
    body = b"".join(
        app(environ, lambda status, _headers: statuses.append(status))
    )
    assert len(statuses) == 1
    return statuses[0], body


@pytest.mark.parametrize("missing", REQUIRED_LOOP)
def test_incomplete_candidate_loop_schema_is_rejected(missing: str) -> None:
    connection = sqlite3.connect(":memory:")
    try:
        columns = ", ".join(f"{name} TEXT" for name in REQUIRED_LOOP if name != missing)
        connection.execute(f"CREATE TABLE autonomous_career_loops ({columns})")
        with pytest.raises(RuntimeError, match="candidate schema is unavailable"):
            _validate_loop_schema(connection)
    finally:
        connection.close()


def test_complete_candidate_loop_schema_is_accepted() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        columns = ", ".join(f"{name} TEXT" for name in REQUIRED_LOOP)
        connection.execute(f"CREATE TABLE autonomous_career_loops ({columns})")
        _validate_loop_schema(connection)
    finally:
        connection.close()


def test_fresh_candidate_database_bootstraps_a_complete_schema_manifest(tmp_path) -> None:
    path = tmp_path / "candidate.sqlite"
    _initialize_candidate_database(path)

    database = SQLiteDatabase.open_read_only(str(path))
    try:
        _validate_candidate_schema(database.connection)
        assert database.connection.execute(
            "SELECT singleton_id, schema_version FROM candidate_schema_manifest"
        ).fetchall() == [(1, 1)]
    finally:
        database.close()


@pytest.mark.parametrize("table", PROVIDER_TABLES)
def test_bootstrap_refuses_to_recreate_a_missing_provider_ledger(
    tmp_path, table: str
) -> None:
    path = tmp_path / f"missing-{table}.sqlite"
    _initialize_candidate_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(f'DROP TABLE "{table}"')

    database = SQLiteDatabase(str(path))
    try:
        with pytest.raises(RuntimeError, match="candidate schema is unavailable"):
            _build_candidate_loop(database, (), None)
        assert database.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone() is None
    finally:
        database.close()


def test_legacy_database_with_history_cannot_adopt_manifest_after_ledger_loss(tmp_path) -> None:
    path = tmp_path / "legacy-ledger-loss.sqlite"
    _initialize_candidate_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE candidate_schema_manifest")
        connection.execute(
            """
            INSERT INTO external_action_operations(
                operation_id, action_type, payload_json, status, created_at, updated_at
            ) VALUES ('existing-operation', 'application.submit', '{}', 'prepared', 'now', 'now')
            """
        )
        connection.execute("DROP TABLE sandbox_submission_ledger")

    database = SQLiteDatabase(str(path))
    try:
        with pytest.raises(RuntimeError, match="candidate schema is unavailable"):
            _build_candidate_loop(database, (), None)
        assert database.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sandbox_submission_ledger'"
        ).fetchone() is None
    finally:
        database.close()


def test_legacy_database_with_complete_ledgers_can_adopt_manifest(tmp_path) -> None:
    path = tmp_path / "legacy-complete.sqlite"
    _initialize_candidate_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE candidate_schema_manifest")
        connection.execute(
            """
            INSERT INTO external_action_operations(
                operation_id, action_type, payload_json, status, created_at, updated_at
            ) VALUES ('existing-operation', 'application.submit', '{}', 'prepared', 'now', 'now')
            """
        )

    database = SQLiteDatabase(str(path))
    try:
        _build_candidate_loop(database, (), None)
        _validate_candidate_schema(database.connection)
    finally:
        database.close()


def test_missing_provider_column_is_rejected_before_provider_initialization(tmp_path) -> None:
    path = tmp_path / "missing-column.sqlite"
    _initialize_candidate_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "ALTER TABLE sandbox_calendar_effects RENAME TO sandbox_calendar_effects_old"
        )
        connection.execute(
            """
            CREATE TABLE sandbox_calendar_effects (
                operation_id TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                action TEXT NOT NULL,
                event_json TEXT NOT NULL
            )
            """
        )
        connection.execute("DROP TABLE sandbox_calendar_effects_old")

    database = SQLiteDatabase(str(path))
    try:
        with pytest.raises(RuntimeError, match="candidate schema is unavailable"):
            _build_candidate_loop(database, (), None)
    finally:
        database.close()


def test_readiness_and_authenticated_api_fail_closed_without_recreating_provider_ledger(
    tmp_path,
) -> None:
    path = tmp_path / "service-ledger-loss.sqlite"
    _initialize_candidate_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE sandbox_submission_ledger")

    delegated = _DelegatedApp()
    app = CandidateServiceWSGIApp(
        delegated,
        database_path=str(path),
        resolve_bearer=lambda token: "candidate" if token == "secret" else None,
    )

    assert _request(app, "/healthz") == ("200 OK", b'{"status":"ok"}')
    assert _request(app, "/readyz") == (
        "503 Service Unavailable",
        b'{"status":"unavailable"}',
    )
    assert _request(
        app,
        "/v1/candidate/runs",
        authorization="Bearer secret",
    ) == ("503 Service Unavailable", b'{"status":"unavailable"}')
    assert delegated.calls == 0

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sandbox_submission_ledger'"
        ).fetchone() is None


def test_unauthenticated_candidate_request_does_not_open_storage(tmp_path) -> None:
    delegated = _DelegatedApp()
    app = CandidateServiceWSGIApp(
        delegated,
        database_path=str(tmp_path / "missing.sqlite"),
        resolve_bearer=lambda _token: None,
    )

    assert _request(
        app,
        "/v1/candidate/runs",
        authorization="Bearer invalid",
    ) == ("200 OK", b"delegated")
    assert delegated.calls == 1


def test_candidate_schema_manifest_is_reused_on_rebuild(tmp_path) -> None:
    path = tmp_path / "manifest-reuse.sqlite"
    _initialize_candidate_database(path)

    database = SQLiteDatabase(str(path))
    try:
        _build_candidate_loop(database, (), None)
        assert database.connection.execute(
            "SELECT singleton_id, schema_version FROM candidate_schema_manifest"
        ).fetchall() == [(1, 1)]
    finally:
        database.close()


def test_candidate_schema_rejects_an_unsupported_manifest_version(tmp_path) -> None:
    path = tmp_path / "unsupported-manifest.sqlite"
    _initialize_candidate_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE candidate_schema_manifest SET schema_version = 2 WHERE singleton_id = 1"
        )

    database = SQLiteDatabase.open_read_only(str(path))
    try:
        with pytest.raises(RuntimeError, match="candidate schema is unavailable"):
            _validate_candidate_schema(database.connection)
    finally:
        database.close()


def test_healthy_readiness_and_authenticated_candidate_route_use_complete_schema(
    tmp_path,
) -> None:
    path = tmp_path / "healthy-service.sqlite"
    _initialize_candidate_database(path)
    delegated = _DelegatedApp()
    app = CandidateServiceWSGIApp(
        delegated,
        database_path=str(path),
        resolve_bearer=lambda token: "candidate" if token == "secret" else None,
    )

    assert _request(app, "/readyz") == ("200 OK", b'{"status":"ok"}')
    assert _request(
        app,
        "/v1/candidate/runs",
        authorization="Bearer secret",
    ) == ("200 OK", b"delegated")
    assert delegated.calls == 1

