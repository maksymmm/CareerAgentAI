from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import base64
import json
import sqlite3

import pytest

from career_agent_ai.application.api import (
    CandidateApprovalWSGIApp,
    candidate_approval_openapi_document,
)
from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopRequest,
    CareerLoopState,
)
from career_agent_ai.application.external_actions import (
    ExternalActionOperation,
    ExternalActionStatus,
)
from career_agent_ai.application.observability.action_cursor import decode_action_cursor
from career_agent_ai.application.observability.references import stable_reference
from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
from career_agent_ai.application.storage.sqlite_candidate_action_projection import (
    SQLiteCandidateActionProjection,
)
from career_agent_ai.application.storage.sqlite_career_loop_repository import (
    SQLiteCareerLoopRepository,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import (
    SQLiteExternalActionOperationRepository,
)


NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


def _request(
    app,
    *,
    path: str,
    query: str = "",
    method: str = "GET",
    token: str | None = "valid",
):
    statuses: list[tuple[str, list[tuple[str, str]]]] = []
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "HTTP_AUTHORIZATION": "" if token is None else f"Bearer {token}",
        "CONTENT_LENGTH": "0",
        "wsgi.input": BytesIO(b""),
    }
    body = b"".join(
        app(environ, lambda status, headers: statuses.append((status, headers)))
    )
    status, headers = statuses[0]
    return status, dict(headers), json.loads(body.decode("utf-8"))


def _add_action(repository, operation_id: str, action_type: str, run_id: str, status):
    operation = ExternalActionOperation(
        operation_id=operation_id,
        action_type=action_type,
        payload={"private_payload": "payload-secret"},
        created_at=NOW,
        updated_at=NOW,
        correlation_id=run_id,
    )
    repository.create(operation)
    if status == ExternalActionStatus.PREPARED:
        return
    repository.transition(
        operation_id,
        ExternalActionStatus.PREPARED,
        ExternalActionStatus.IN_PROGRESS,
    )
    if status == ExternalActionStatus.IN_PROGRESS:
        return
    if status == ExternalActionStatus.SUCCEEDED:
        repository.transition(
            operation_id,
            ExternalActionStatus.IN_PROGRESS,
            ExternalActionStatus.SUCCEEDED,
            result={"private_result": "result-secret"},
        )
    elif status == ExternalActionStatus.FAILED:
        repository.transition(
            operation_id,
            ExternalActionStatus.IN_PROGRESS,
            ExternalActionStatus.FAILED,
            error="provider-error-secret",
        )
    elif status == ExternalActionStatus.RECONCILIATION_REQUIRED:
        repository.transition(
            operation_id,
            ExternalActionStatus.IN_PROGRESS,
            ExternalActionStatus.RECONCILIATION_REQUIRED,
            error="ambiguous-provider-secret",
        )
    else:
        raise AssertionError(f"Unsupported test status: {status}")


def _seed(path):
    database = SQLiteDatabase(str(path))
    runs = SQLiteCareerLoopRepository(database)
    for run_id, owner in (
        ("candidate-run-a", "candidate-1"),
        ("candidate-run-b", "candidate-1"),
        ("foreign-run", "candidate-2"),
    ):
        runs.save(CareerLoopState(
            run_id=run_id,
            request=CareerLoopRequest(
                user_id=owner,
                keyword="logistics",
                candidate_profile="Experienced logistics professional.",
            ),
        ))
    actions = SQLiteExternalActionOperationRepository(database)
    _add_action(actions, "action-01", "application.submit", "candidate-run-a",
                ExternalActionStatus.PREPARED)
    _add_action(actions, "action-02", "communication.send", "candidate-run-a",
                ExternalActionStatus.IN_PROGRESS)
    _add_action(actions, "action-03", "calendar.accept", "candidate-run-a",
                ExternalActionStatus.FAILED)
    _add_action(actions, "action-04", "calendar.reschedule", "candidate-run-a",
                ExternalActionStatus.RECONCILIATION_REQUIRED)
    _add_action(actions, "action-05", "application.submit", "candidate-run-a",
                ExternalActionStatus.SUCCEEDED)
    _add_action(actions, "action-06", "custom.workflow", "candidate-run-a",
                ExternalActionStatus.PREPARED)
    _add_action(actions, "other-run-action", "communication.send", "candidate-run-b",
                ExternalActionStatus.FAILED)
    _add_action(actions, "foreign-action", "calendar.accept", "foreign-run",
                ExternalActionStatus.SUCCEEDED)
    database.close()


def test_candidate_action_projection_is_owner_scoped_bounded_and_restart_safe(tmp_path):
    path = tmp_path / "candidate-actions.sqlite"
    _seed(path)
    before = path.read_bytes()
    run_ref = stable_reference("candidate-run-a")

    database = SQLiteDatabase.open_read_only(str(path))
    try:
        projection = SQLiteCandidateActionProjection(database)
        first = projection.list_page("candidate-run-a", user_id="candidate-1", limit=2)
        second = projection.list_page(
            "candidate-run-a", user_id="candidate-1", limit=2,
            after=first["next_after"],
        )
        third = projection.list_page(
            "candidate-run-a", user_id="candidate-1", limit=2,
            after=second["next_after"],
        )
    finally:
        database.close()

    assert first["schema_version"] == 1
    assert first["run_ref"] == run_ref
    expected = {
        "action-01": ("application_submission", "prepared", False),
        "action-02": ("communication", "in_progress", False),
        "action-03": ("calendar", "failed", True),
        "action-04": ("calendar", "reconciliation_required", True),
        "action-05": ("application_submission", "succeeded", False),
        "action-06": ("other", "prepared", False),
    }
    expected_ids = sorted(expected)
    all_items = [item for page in (first, second, third) for item in page["items"]]
    assert [item["operation_ref"] for item in all_items] == [
        stable_reference(operation_id) for operation_id in expected_ids
    ]
    assert [
        (item["action_family"], item["status"], item["error_present"])
        for item in all_items
    ] == [expected[operation_id] for operation_id in expected_ids]
    assert decode_action_cursor(
        first["next_after"], candidate_run_ref=run_ref
    ) == stable_reference(expected_ids[1])
    decoded_cursor = base64.urlsafe_b64decode(
        first["next_after"] + "=" * (-len(first["next_after"]) % 4)
    ).decode("ascii")
    assert all(operation_id not in decoded_cursor for operation_id in expected_ids)
    assert stable_reference(expected_ids[1]) in decoded_cursor
    assert third["next_after"] is None
    assert all(
        set(item) == {
            "operation_ref", "action_family", "status",
            "created_at", "updated_at", "error_present",
        }
        for page in (first, second, third)
        for item in page["items"]
    )
    assert "action-01" not in str(first)
    assert "candidate-run-a" not in str(first)
    assert "payload-secret" not in str((first, second, third))
    assert "result-secret" not in str((first, second, third))
    assert "provider-error-secret" not in str((first, second, third))
    assert path.read_bytes() == before


def test_candidate_action_projection_hides_foreign_and_missing_runs_identically(tmp_path):
    path = tmp_path / "candidate-action-owners.sqlite"
    _seed(path)
    database = SQLiteDatabase.open_read_only(str(path))
    try:
        projection = SQLiteCandidateActionProjection(database)
        with pytest.raises(KeyError):
            projection.list_page("foreign-run", user_id="candidate-1")
        with pytest.raises(KeyError):
            projection.list_page("missing-run", user_id="candidate-1")
    finally:
        database.close()


def test_candidate_action_cursor_cannot_be_replayed_for_another_owned_run(tmp_path):
    path = tmp_path / "candidate-action-cursor.sqlite"
    _seed(path)
    database = SQLiteDatabase.open_read_only(str(path))
    try:
        projection = SQLiteCandidateActionProjection(database)
        first = projection.list_page("candidate-run-a", user_id="candidate-1", limit=1)
        with pytest.raises(ValueError, match="candidate-run filter"):
            projection.list_page(
                "candidate-run-b", user_id="candidate-1", limit=1,
                after=first["next_after"],
            )
    finally:
        database.close()


@pytest.mark.parametrize("limit", [True, 0, -1, 26, "2", 1.0])
def test_candidate_action_projection_rejects_invalid_limits(tmp_path, limit):
    path = tmp_path / "candidate-action-limit.sqlite"
    _seed(path)
    database = SQLiteDatabase.open_read_only(str(path))
    try:
        with pytest.raises(ValueError, match="limit"):
            SQLiteCandidateActionProjection(database).list_page(
                "candidate-run-a", user_id="candidate-1", limit=limit
            )
    finally:
        database.close()


def test_candidate_action_projection_fails_closed_on_corrupt_correlation(tmp_path):
    path = tmp_path / "candidate-action-corrupt-reference.sqlite"
    _seed(path)
    database = SQLiteDatabase(str(path))
    database.connection.execute(
        "UPDATE external_action_operations SET correlation_ref = ? WHERE operation_id = ?",
        ("f" * 24, "action-03"),
    )
    database.connection.commit()
    database.close()

    database = SQLiteDatabase.open_read_only(str(path))
    try:
        with pytest.raises(RuntimeError, match="correlation is inconsistent"):
            SQLiteCandidateActionProjection(database).list_page(
                "candidate-run-a", user_id="candidate-1", limit=25
            )
    finally:
        database.close()



def test_candidate_action_projection_fails_closed_on_corrupt_operation_reference(tmp_path):
    path = tmp_path / "candidate-action-corrupt-operation-reference.sqlite"
    _seed(path)
    database = SQLiteDatabase(str(path))
    database.connection.execute(
        "UPDATE external_action_operations SET operation_ref = ? WHERE operation_id = ?",
        ("f" * 24, "action-03"),
    )
    database.connection.commit()
    database.close()

    database = SQLiteDatabase.open_read_only(str(path))
    try:
        with pytest.raises(RuntimeError, match="operation reference is inconsistent"):
            SQLiteCandidateActionProjection(database).list_page(
                "candidate-run-a", user_id="candidate-1", limit=25
            )
    finally:
        database.close()


def test_candidate_action_projection_uses_composite_keyset_index(tmp_path):
    path = tmp_path / "candidate-action-query-plan.sqlite"
    _seed(path)
    database = SQLiteDatabase.open_read_only(str(path))
    try:
        plan = database.connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT operation_id, operation_ref
            FROM external_action_operations
            WHERE correlation_ref = ?
            ORDER BY operation_id ASC
            LIMIT ?
            """,
            (stable_reference("candidate-run-a"), 26),
        ).fetchall()
        details = " ".join(str(row[3]) for row in plan)
        assert "idx_external_actions_correlation_ref_operation_id" in details
    finally:
        database.close()

def test_candidate_action_projection_fails_closed_on_missing_lookup_index(tmp_path):
    path = tmp_path / "candidate-action-index.sqlite"
    _seed(path)
    database = SQLiteDatabase(str(path))
    database.connection.execute("DROP INDEX idx_external_actions_correlation_ref")
    database.connection.commit()
    database.close()

    database = SQLiteDatabase.open_read_only(str(path))
    try:
        with pytest.raises(RuntimeError, match="reference index"):
            SQLiteCandidateActionProjection(database).list_page(
                "candidate-run-a", user_id="candidate-1"
            )
    finally:
        database.close()


def test_candidate_action_projection_fails_closed_on_corrupt_timestamps(tmp_path):
    path = tmp_path / "candidate-action-timestamp.sqlite"
    _seed(path)
    database = SQLiteDatabase(str(path))
    database.connection.execute(
        "UPDATE external_action_operations SET updated_at = ? WHERE operation_id = ?",
        ("not-a-timestamp", "action-01"),
    )
    database.connection.commit()
    database.close()

    database = SQLiteDatabase.open_read_only(str(path))
    try:
        with pytest.raises(RuntimeError, match="timestamp"):
            SQLiteCandidateActionProjection(database).list_page(
                "candidate-run-a", user_id="candidate-1"
            )
    finally:
        database.close()


def test_candidate_action_projection_reads_legacy_correlation_schema(tmp_path):
    path = tmp_path / "candidate-action-legacy.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE autonomous_career_loops (
            run_id TEXT PRIMARY KEY,
            owner_user_id TEXT,
            payload_json TEXT NOT NULL,
            version INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO autonomous_career_loops VALUES (?, ?, ?, ?)",
        ("legacy-run", "candidate-1", json.dumps({"phase": "search", "version": 1}), 1),
    )
    connection.execute(
        """
        CREATE TABLE external_action_operations (
            operation_id TEXT PRIMARY KEY,
            action_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            correlation_id TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX idx_external_actions_correlation ON external_action_operations(correlation_id)"
    )
    connection.execute(
        """
        INSERT INTO external_action_operations (
            operation_id, action_type, payload_json, status, error,
            created_at, updated_at, correlation_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "legacy-action", "communication.send", "{}", "failed",
            "private-error", NOW.isoformat(), NOW.isoformat(), "legacy-run",
        ),
    )
    connection.commit()
    connection.close()

    # The writable runtime performs the additive migration before read-only
    # candidate projections are opened. The projection itself never migrates.
    migration = SQLiteDatabase(str(path))
    SQLiteExternalActionOperationRepository(migration)
    migration.close()

    database = SQLiteDatabase.open_read_only(str(path))
    try:
        page = SQLiteCandidateActionProjection(database).list_page(
            "legacy-run", user_id="candidate-1"
        )
    finally:
        database.close()
    assert page["run_ref"] == stable_reference("legacy-run")
    assert page["items"][0]["action_family"] == "communication"
    assert page["items"][0]["status"] == "failed"
    assert page["items"][0]["error_present"] is True
    assert "legacy-run" not in str(page)
    assert "private-error" not in str(page)


def test_candidate_action_http_endpoint_is_authenticated_read_only_and_strict():
    class Loop:
        def __init__(self):
            self.calls = []

        def list_candidate_action_statuses(self, run_id, **kwargs):
            self.calls.append((run_id, kwargs))
            return {
                "schema_version": 1,
                "run_ref": stable_reference(run_id),
                "items": [],
                "next_after": None,
            }

    loop = Loop()
    app = CandidateApprovalWSGIApp(
        loop,
        resolve_bearer=lambda token: "candidate-1" if token == "valid" else None,
    )
    route = "/v1/candidate/runs/candidate-run-a/actions"
    status, headers, body = _request(app, path=route, query="limit=2")
    assert status == "200 OK"
    assert headers["Cache-Control"] == "no-store"
    assert body == {
        "schema_version": 1,
        "run_ref": stable_reference("candidate-run-a"),
        "items": [],
        "next_after": None,
    }
    assert loop.calls == [("candidate-run-a", {"user_id": "candidate-1", "limit": 2, "after": None})]

    for query in (
        "limit=0",
        "limit=26",
        "limit=2&limit=3",
        "after=",
        "unknown=value",
        "after=%GG",
        "after=" + "x" * 1201,
    ):
        assert _request(app, path=route, query=query)[0] == "400 Bad Request"
    assert len(loop.calls) == 1
    assert _request(app, path=route, method="POST")[0] == "405 Method Not Allowed"
    assert _request(app, path=route, token=None)[0] == "401 Unauthorized"
    assert len(loop.calls) == 1


def test_candidate_action_http_runtime_enforces_owner_and_survives_restart(tmp_path):
    path = tmp_path / "candidate-action-http.sqlite"
    _seed(path)
    before = path.read_bytes()
    resolve = lambda token: {
        "owner": "candidate-1",
        "foreign": "candidate-2",
    }.get(token)
    route = "/v1/candidate/runs/candidate-run-a/actions"

    with RuntimeCandidateApp(
        database_path=str(path), jobs=(), resolve_bearer=resolve
    ) as runtime:
        status, headers, page = _request(runtime, path=route, token="owner")
        assert status == "200 OK"
        assert headers["Cache-Control"] == "no-store"
        expected = {
            "action-01": ("prepared", "application_submission"),
            "action-02": ("in_progress", "communication"),
            "action-03": ("failed", "calendar"),
            "action-04": ("reconciliation_required", "calendar"),
            "action-05": ("succeeded", "application_submission"),
            "action-06": ("prepared", "other"),
        }
        expected_ids = sorted(expected)
        assert [
            (item["status"], item["action_family"]) for item in page["items"]
        ] == [expected[operation_id] for operation_id in expected_ids]
        assert _request(
            runtime,
            path="/v1/candidate/runs/foreign-run/actions",
            token="owner",
        )[0] == "404 Not Found"
        assert _request(runtime, path=route, token="foreign")[0] == "404 Not Found"
    assert path.read_bytes() == before


def test_candidate_action_openapi_is_bounded_and_minimal():
    route = "/v1/candidate/runs/{run_id}/actions"
    operation = candidate_approval_openapi_document()["paths"][route]["get"]
    assert operation["security"] == [{"bearerAuth": []}]
    parameters = {item["name"]: item for item in operation["parameters"]}
    assert parameters["limit"]["schema"]["maximum"] == 25
    assert parameters["after"]["schema"]["maxLength"] == 1200
    response = operation["responses"]["200"]
    assert response["headers"]["Cache-Control"]["schema"]["const"] == "no-store"
    schema = response["content"]["application/json"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"schema_version", "run_ref", "items", "next_after"}
    item = schema["properties"]["items"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {
        "operation_ref", "action_family", "status",
        "created_at", "updated_at", "error_present",
    }
    assert "operation_id" not in item["properties"]
    assert "payload" not in item["properties"]
    assert "result" not in item["properties"]
    assert "error" not in item["properties"]

def test_candidate_action_projection_rejects_memory_storage():
    database = SQLiteDatabase()
    try:
        with pytest.raises(ValueError, match="durable storage"):
            SQLiteCandidateActionProjection(database)
    finally:
        database.close()


def test_candidate_action_projection_fails_closed_when_action_table_is_missing(tmp_path):
    path = tmp_path / "candidate-action-missing-table.sqlite"
    _seed(path)
    database = SQLiteDatabase(str(path))
    database.connection.execute("DROP TABLE external_action_operations")
    database.connection.commit()
    database.close()

    database = SQLiteDatabase.open_read_only(str(path))
    try:
        with pytest.raises(RuntimeError, match="storage is unavailable"):
            SQLiteCandidateActionProjection(database).list_page(
                "candidate-run-a", user_id="candidate-1"
            )
    finally:
        database.close()


def test_candidate_action_projection_fails_closed_when_required_schema_is_missing(tmp_path):
    path = tmp_path / "candidate-action-missing-column.sqlite"
    _seed(path)
    database = SQLiteDatabase(str(path))
    database.connection.execute("DROP INDEX idx_external_actions_correlation")
    database.connection.execute("DROP INDEX idx_external_actions_correlation_operation_id")
    database.connection.execute(
        "ALTER TABLE external_action_operations DROP COLUMN correlation_id"
    )
    database.connection.commit()
    database.close()

    database = SQLiteDatabase.open_read_only(str(path))
    try:
        with pytest.raises(RuntimeError, match="schema is unavailable"):
            SQLiteCandidateActionProjection(database).list_page(
                "candidate-run-a", user_id="candidate-1"
            )
    finally:
        database.close()


def test_candidate_action_projection_rejects_malformed_persisted_operation_id(tmp_path):
    path = tmp_path / "candidate-action-operation-id.sqlite"
    _seed(path)
    database = SQLiteDatabase(str(path))
    database.connection.execute(
        "UPDATE external_action_operations SET operation_id = ? WHERE operation_id = ?",
        (" action-01", "action-01"),
    )
    database.connection.commit()
    database.close()

    database = SQLiteDatabase.open_read_only(str(path))
    try:
        with pytest.raises(RuntimeError, match="identifier is malformed"):
            SQLiteCandidateActionProjection(database).list_page(
                "candidate-run-a", user_id="candidate-1"
            )
    finally:
        database.close()


def test_candidate_action_projection_rejects_reference_collision_or_tampering(tmp_path):
    path = tmp_path / "candidate-action-correlation-mismatch.sqlite"
    _seed(path)
    database = SQLiteDatabase(str(path))
    database.connection.execute(
        "UPDATE external_action_operations SET correlation_id = ? WHERE operation_id = ?",
        ("candidate-run-b", "action-01"),
    )
    database.connection.commit()
    database.close()

    database = SQLiteDatabase.open_read_only(str(path))
    try:
        with pytest.raises(RuntimeError, match="correlation is inconsistent"):
            SQLiteCandidateActionProjection(database).list_page(
                "candidate-run-a", user_id="candidate-1"
            )
    finally:
        database.close()


def test_candidate_action_projection_rejects_malformed_action_type(tmp_path):
    path = tmp_path / "candidate-action-type.sqlite"
    _seed(path)
    database = SQLiteDatabase(str(path))
    database.connection.execute(
        "UPDATE external_action_operations SET action_type = ? WHERE operation_id = ?",
        ("", "action-01"),
    )
    database.connection.commit()
    database.close()

    database = SQLiteDatabase.open_read_only(str(path))
    try:
        with pytest.raises(RuntimeError, match="action type is malformed"):
            SQLiteCandidateActionProjection(database).list_page(
                "candidate-run-a", user_id="candidate-1"
            )
    finally:
        database.close()


def test_candidate_action_projection_rejects_unknown_persisted_status(tmp_path):
    path = tmp_path / "candidate-action-status.sqlite"
    _seed(path)
    database = SQLiteDatabase(str(path))
    database.connection.execute("PRAGMA ignore_check_constraints = ON")
    database.connection.execute(
        "UPDATE external_action_operations SET status = ? WHERE operation_id = ?",
        ("unknown", "action-01"),
    )
    database.connection.commit()
    database.close()

    database = SQLiteDatabase.open_read_only(str(path))
    try:
        with pytest.raises(RuntimeError, match="action status is malformed"):
            SQLiteCandidateActionProjection(database).list_page(
                "candidate-run-a", user_id="candidate-1"
            )
    finally:
        database.close()


@pytest.mark.parametrize("timestamp", ["", "2026-11-24T12:00:00"])
def test_candidate_action_projection_rejects_empty_or_naive_timestamps(tmp_path, timestamp):
    path = tmp_path / "candidate-action-naive-timestamp.sqlite"
    _seed(path)
    database = SQLiteDatabase(str(path))
    database.connection.execute(
        "UPDATE external_action_operations SET created_at = ? WHERE operation_id = ?",
        (timestamp, "action-01"),
    )
    database.connection.commit()
    database.close()

    database = SQLiteDatabase.open_read_only(str(path))
    try:
        with pytest.raises(RuntimeError, match="timestamp"):
            SQLiteCandidateActionProjection(database).list_page(
                "candidate-run-a", user_id="candidate-1"
            )
    finally:
        database.close()
