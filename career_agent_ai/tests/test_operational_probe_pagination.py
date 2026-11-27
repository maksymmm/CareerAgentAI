from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from career_agent_ai.application.api.operational_api import (
    OperationalApiService,
    OperationalWSGIApp,
    openapi_document,
)
from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopPhase,
    CareerLoopRequest,
    CareerLoopState,
)
from career_agent_ai.application.external_actions import (
    ExternalActionOperation,
    ExternalActionStatus,
)
from career_agent_ai.application.observability import OperationalSeverity
from career_agent_ai.application.storage.sqlite_career_loop_repository import (
    SQLiteCareerLoopRepository,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import (
    SQLiteExternalActionOperationRepository,
)
from career_agent_ai.application.storage.sqlite_operational_probe import (
    SQLiteOperationalProbe,
)

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)


def _failed_action(database: SQLiteDatabase, operation_id: str, updated_at: datetime) -> None:
    repository = SQLiteExternalActionOperationRepository(database)
    repository.create(
        ExternalActionOperation(
            operation_id=operation_id,
            action_type="application.submit",
            payload={"application_id": operation_id},
            created_at=updated_at,
            updated_at=updated_at,
        )
    )
    repository.transition(
        operation_id, ExternalActionStatus.PREPARED, ExternalActionStatus.IN_PROGRESS
    )
    repository.transition(
        operation_id,
        ExternalActionStatus.IN_PROGRESS,
        ExternalActionStatus.FAILED,
        error="sandbox provider failed",
    )
    database.connection.execute(
        "UPDATE external_action_operations SET updated_at = ? WHERE operation_id = ?",
        (updated_at.isoformat(), operation_id),
    )
    database.connection.commit()


def _request() -> CareerLoopRequest:
    return CareerLoopRequest(
        user_id="candidate-1",
        keyword="logistics",
        candidate_profile="Experienced logistics professional.",
    )


def _wsgi_call(app, *, query: str, token: str):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(
        app(
            {
                "REQUEST_METHOD": "GET",
                "PATH_INFO": "/v1/operational/issues",
                "QUERY_STRING": query,
                "HTTP_AUTHORIZATION": f"Bearer {token}",
            },
            start_response,
        )
    )
    return captured, json.loads(body.decode("utf-8"))


def test_operational_probe_returns_a_bounded_sorted_page_and_truncation_signal(tmp_path):
    database = SQLiteDatabase(str(tmp_path / "operations.sqlite"))
    old = NOW - timedelta(hours=2)
    for index in range(4):
        _failed_action(database, f"failed-{index}", old + timedelta(seconds=index))

    probe = SQLiteOperationalProbe(database)
    page = probe.inspect_page(now=NOW, stale_after_seconds=3600, limit=2)

    assert [issue.entity_id for issue in page.issues] == ["failed-0", "failed-1"]
    assert all(issue.issue_type == "external_action_failed" for issue in page.issues)
    assert all(issue.severity == OperationalSeverity.ERROR for issue in page.issues)
    assert page.truncated is True

    complete = probe.inspect_page(now=NOW, stale_after_seconds=3600, limit=10)
    assert [issue.entity_id for issue in complete.issues] == [
        "failed-0", "failed-1", "failed-2", "failed-3"
    ]
    assert complete.truncated is False
    database.close()


def test_operational_wsgi_exposes_bounded_limit_and_openapi_contract(tmp_path):
    database = SQLiteDatabase(str(tmp_path / "api.sqlite"))
    old = NOW - timedelta(hours=2)
    for index in range(3):
        _failed_action(database, f"api-failed-{index}", old + timedelta(seconds=index))

    token = "o" * 40
    service = OperationalApiService(
        SQLiteOperationalProbe(database),
        clock=lambda: NOW,
    )
    app = OperationalWSGIApp(service, bearer_token=token)
    meta, payload = _wsgi_call(app, query="stale_after_seconds=60&limit=2", token=token)

    assert meta["status"] == "200 OK"
    assert meta["headers"]["Cache-Control"] == "no-store"
    assert len(payload["issues"]) == 2
    assert payload["truncated"] is True
    assert payload["generated_at"] == NOW.isoformat()

    spec = openapi_document()
    parameters = spec["paths"]["/v1/operational/issues"]["get"]["parameters"]
    limit = next(parameter for parameter in parameters if parameter["name"] == "limit")
    assert limit["schema"] == {
        "type": "integer", "minimum": 1, "maximum": 500, "default": 100
    }
    response_schema = spec["paths"]["/v1/operational/issues"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert "truncated" in response_schema["required"]
    database.close()


@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=501", "limit=not-an-integer", "limit=1&limit=2"],
)
def test_operational_wsgi_rejects_invalid_page_limits(tmp_path, query):
    database = SQLiteDatabase(str(tmp_path / "invalid-limit.sqlite"))
    app = OperationalWSGIApp(
        OperationalApiService(SQLiteOperationalProbe(database), clock=lambda: NOW),
        bearer_token="v" * 40,
    )
    meta, body = _wsgi_call(app, query=query, token="v" * 40)
    assert meta["status"] == "400 Bad Request"
    assert body["error"] == "invalid_request"
    database.close()


@pytest.mark.parametrize("limit", [True, 0, 501, "2"])
def test_operational_probe_rejects_invalid_limits(tmp_path, limit):
    database = SQLiteDatabase(str(tmp_path / "invalid-probe-limit.sqlite"))
    with pytest.raises((TypeError, ValueError)):
        SQLiteOperationalProbe(database).inspect_page(
            now=NOW, stale_after_seconds=60, limit=limit
        )
    database.close()


def test_operational_probe_uses_recovery_indexes_without_temporary_sorts():
    database = SQLiteDatabase()
    SQLiteExternalActionOperationRepository(database)
    SQLiteCareerLoopRepository(database)

    action_plan = database.connection.execute(
        """
        EXPLAIN QUERY PLAN
        SELECT operation_id FROM external_action_operations
        WHERE status = 'failed'
        ORDER BY julianday(updated_at), operation_id LIMIT 2
        """
    ).fetchall()
    action_details = " ".join(row[3] for row in action_plan)
    assert "idx_external_actions_recovery_failed" in action_details
    assert "TEMP B-TREE" not in action_details

    loop_plan = database.connection.execute(
        """
        EXPLAIN QUERY PLAN
        SELECT run_id FROM autonomous_career_loops
        WHERE phase NOT IN ('complete', 'failed')
          AND julianday(updated_at) < julianday(?)
        ORDER BY julianday(updated_at), run_id LIMIT 2
        """,
        (NOW.isoformat(),),
    ).fetchall()
    loop_details = " ".join(row[3] for row in loop_plan)
    assert "idx_autonomous_loop_stale_updated" in loop_details
    assert "TEMP B-TREE" not in loop_details
    database.close()


def test_operational_probe_surfaces_malformed_loop_payload_without_echoing_it(tmp_path):
    database = SQLiteDatabase(str(tmp_path / "malformed-loop.sqlite"))
    loops = SQLiteCareerLoopRepository(database)
    old = NOW - timedelta(hours=2)
    state = CareerLoopState(
        run_id="malformed-loop",
        request=_request(),
        phase=CareerLoopPhase.SEARCH,
        created_at=old,
        updated_at=old,
    )
    loops.save(state)
    database.connection.execute(
        "UPDATE autonomous_career_loops SET payload_json = ? WHERE run_id = ?",
        ("private-corrupt-payload", state.run_id),
    )
    database.connection.commit()

    page = SQLiteOperationalProbe(database).inspect_page(
        now=NOW, stale_after_seconds=3600, limit=10
    )
    issue = next(item for item in page.issues if item.entity_id == state.run_id)
    assert issue.issue_type == "career_loop_malformed"
    assert issue.severity == OperationalSeverity.ERROR
    assert issue.details == {"reason": "invalid_payload_json"}
    assert "private-corrupt-payload" not in str(issue.details)
    database.close()


def test_operational_probe_is_read_only_and_bounded_after_restart(tmp_path):
    path = tmp_path / "readonly.sqlite"
    database = SQLiteDatabase(str(path))
    _failed_action(database, "readonly-failed", NOW - timedelta(hours=2))
    database.close()
    before = path.read_bytes()

    readonly = SQLiteDatabase.open_read_only(str(path))
    page = SQLiteOperationalProbe(readonly).inspect_page(
        now=NOW, stale_after_seconds=3600, limit=1
    )
    assert [issue.entity_id for issue in page.issues] == ["readonly-failed"]
    with pytest.raises(sqlite3.OperationalError):
        readonly.connection.execute(
            "DELETE FROM external_action_operations WHERE operation_id = ?",
            ("readonly-failed",),
        )
    readonly.close()
    assert path.read_bytes() == before


def test_candidate_ci_runs_independent_coverage_gate_after_pytest():
    workflow = (
        Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")
    assert "id: pytest" in workflow
    assert "Enforce independent coverage threshold" in workflow
    assert "steps.pytest.outcome != 'skipped'" in workflow
    assert "python -m coverage report --precision=2 --fail-under=90" in workflow
    assert workflow.index("Run test suite") < workflow.index(
        "Enforce independent coverage threshold"
    )
