from __future__ import annotations

import json
import sqlite3

import pytest

from career_agent_ai.application.external_actions import (
    ExternalActionOperation,
    ExternalActionService,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import (
    SQLiteExternalActionOperationRepository,
)


class NoopAdapter:
    def execute(self, operation_id, action_type, payload):
        return {"provider_reference": "sandbox:" + operation_id}


def test_service_persists_correlation_outside_provider_payload_and_keeps_first_binding(tmp_path):
    database = SQLiteDatabase(str(tmp_path / "correlation.sqlite"))
    repository = SQLiteExternalActionOperationRepository(database)
    service = ExternalActionService(repository, NoopAdapter())

    first = service.prepare(
        "application-submit:one",
        "application.submit",
        {"application_id": "application-one"},
        correlation_id="candidate-run-one",
    )
    replay = service.prepare(
        "application-submit:one",
        "application.submit",
        {"application_id": "application-one"},
        correlation_id="candidate-run-two",
    )

    row = database.connection.execute(
        "SELECT correlation_id, payload_json FROM external_action_operations "
        "WHERE operation_id = ?",
        ("application-submit:one",),
    ).fetchone()
    assert first.correlation_id == "candidate-run-one"
    assert replay.correlation_id == "candidate-run-one"
    assert row[0] == "candidate-run-one"
    assert json.loads(row[1]) == {"application_id": "application-one"}
    assert "candidate-run-one" not in repr(first)
    database.close()


def test_correlation_id_is_optional_for_legacy_callers():
    operation = ExternalActionOperation("operation-legacy", "application.submit", {})
    assert operation.correlation_id is None


@pytest.mark.parametrize(
    "value",
    ["", " ", " leading", "trailing ", "x" * 201, "line\nbreak", "delete\x7f", "\ud800"],
)
def test_malformed_correlation_id_is_rejected(value):
    with pytest.raises(ValueError, match="correlation_id"):
        ExternalActionOperation(
            "operation-invalid-correlation",
            "application.submit",
            {},
            correlation_id=value,
        )


def test_non_text_correlation_id_is_rejected():
    with pytest.raises(TypeError, match="correlation_id"):
        ExternalActionOperation(
            "operation-invalid-correlation",
            "application.submit",
            {},
            correlation_id=123,  # type: ignore[arg-type]
        )


def test_legacy_schema_migrates_additively_and_preserves_existing_rows(tmp_path):
    path = str(tmp_path / "legacy-correlation.sqlite")
    legacy = SQLiteDatabase(path)
    legacy.connection.execute(
        """
        CREATE TABLE external_action_operations (
            operation_id TEXT PRIMARY KEY,
            action_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    legacy.connection.execute(
        """
        INSERT INTO external_action_operations (
            operation_id, action_type, payload_json, status,
            result_json, error, created_at, updated_at
        ) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)
        """,
        (
            "legacy-operation",
            "application.submit",
            '{"application_id":"legacy"}',
            "prepared",
            "2026-11-24T12:00:00+00:00",
            "2026-11-24T12:00:00+00:00",
        ),
    )
    legacy.connection.commit()
    legacy.close()

    database = SQLiteDatabase(path)
    repository = SQLiteExternalActionOperationRepository(database)
    legacy_operation = repository.get("legacy-operation")
    assert legacy_operation is not None
    assert legacy_operation.correlation_id is None

    columns = {
        row[1]
        for row in database.connection.execute(
            "PRAGMA table_info(external_action_operations)"
        )
    }
    indexes = {
        row[1]
        for row in database.connection.execute(
            "PRAGMA index_list(external_action_operations)"
        )
    }
    assert "correlation_id" in columns
    assert "idx_external_actions_correlation" in indexes

    service = ExternalActionService(repository, NoopAdapter())
    created = service.prepare(
        "new-operation",
        "application.submit",
        {"application_id": "new"},
        correlation_id="candidate-run-new",
    )
    assert repository.get("new-operation") == created
    database.close()


def test_migration_is_idempotent():
    database = SQLiteDatabase()
    SQLiteExternalActionOperationRepository(database)
    SQLiteExternalActionOperationRepository(database)
    columns = [
        row[1]
        for row in database.connection.execute(
            "PRAGMA table_info(external_action_operations)"
        )
    ]
    assert columns.count("correlation_id") == 1
    database.close()


def test_malformed_persisted_correlation_fails_closed():
    database = SQLiteDatabase()
    repository = SQLiteExternalActionOperationRepository(database)
    repository.create(
        ExternalActionOperation(
            "operation-corrupt-correlation",
            "application.submit",
            {},
            correlation_id="candidate-run-valid",
        )
    )
    database.connection.execute(
        "UPDATE external_action_operations SET correlation_id = ? "
        "WHERE operation_id = ?",
        ("bad\nreference", "operation-corrupt-correlation"),
    )
    database.connection.commit()

    with pytest.raises(ValueError, match="Persisted external-action operation is malformed"):
        repository.get("operation-corrupt-correlation")
    database.close()
