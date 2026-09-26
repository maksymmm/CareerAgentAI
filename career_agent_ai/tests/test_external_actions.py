from __future__ import annotations

from typing import Any, Mapping

import pytest

from career_agent_ai.application.external_actions.external_action_operation import (
    ExternalActionOperation,
    ExternalActionStatus,
)
from career_agent_ai.application.external_actions.external_action_repository import (
    OperationConflictError,
)
from career_agent_ai.application.external_actions.external_action_service import (
    ExternalActionService,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import (
    SQLiteExternalActionOperationRepository,
)


class RecordingAdapter:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, Mapping[str, Any]]] = []
        self.error = error

    def execute(
        self,
        operation_id: str,
        action_type: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        self.calls.append((operation_id, action_type, payload))
        if self.error is not None:
            raise self.error
        return {"provider_reference": "ref-123"}


def make_service(
    database: SQLiteDatabase,
    adapter: RecordingAdapter,
) -> tuple[ExternalActionService, SQLiteExternalActionOperationRepository]:
    repository = SQLiteExternalActionOperationRepository(database)
    return ExternalActionService(repository, adapter), repository


def test_approved_operation_persists_intent_and_success(tmp_path):
    database = SQLiteDatabase(str(tmp_path / "actions.sqlite"))
    adapter = RecordingAdapter()
    service, repository = make_service(database, adapter)

    prepared = service.prepare("apply:job-42:user-7", "job_application", {"job_id": "42"})
    succeeded = service.execute(prepared.operation_id, human_approved=True)

    assert prepared.status == ExternalActionStatus.PREPARED
    assert succeeded.status == ExternalActionStatus.SUCCEEDED
    assert succeeded.result == {"provider_reference": "ref-123"}
    assert repository.get(prepared.operation_id) == succeeded
    assert len(adapter.calls) == 1


def test_duplicate_invocation_returns_terminal_result_without_second_call():
    database = SQLiteDatabase()
    adapter = RecordingAdapter()
    service, _ = make_service(database, adapter)
    service.prepare("message:1", "recruiter_message", {"body": "Hello"})

    first = service.execute("message:1", human_approved=True)
    duplicate = service.execute("message:1", human_approved=True)

    assert duplicate == first
    assert len(adapter.calls) == 1


def test_prepare_is_idempotent_but_rejects_identifier_reuse():
    service, _ = make_service(SQLiteDatabase(), RecordingAdapter())

    first = service.prepare("operation-1", "application", {"job": "1"})
    duplicate = service.prepare("operation-1", "application", {"job": "1"})

    assert duplicate == first
    with pytest.raises(OperationConflictError):
        service.prepare("operation-1", "application", {"job": "2"})


def test_execution_requires_explicit_human_approval():
    adapter = RecordingAdapter()
    service, repository = make_service(SQLiteDatabase(), adapter)
    service.prepare("operation-1", "application", {})

    with pytest.raises(PermissionError, match="human approval"):
        service.execute("operation-1", human_approved=False)

    assert repository.get("operation-1").status == ExternalActionStatus.PREPARED
    assert adapter.calls == []


def test_adapter_failure_is_persisted_and_not_retried():
    adapter = RecordingAdapter(RuntimeError("provider unavailable"))
    service, _ = make_service(SQLiteDatabase(), adapter)
    service.prepare("operation-1", "application", {})

    failed = service.execute("operation-1", human_approved=True)
    duplicate = service.execute("operation-1", human_approved=True)

    assert failed.status == ExternalActionStatus.FAILED
    assert failed.error == "provider unavailable"
    assert duplicate == failed
    assert len(adapter.calls) == 1


def test_restart_marks_in_progress_operation_for_reconciliation(tmp_path):
    path = str(tmp_path / "actions.sqlite")
    first_database = SQLiteDatabase(path)
    _, first_repository = make_service(first_database, RecordingAdapter())
    first_repository.create(ExternalActionOperation("operation-1", "application", {}))
    first_repository.transition(
        "operation-1",
        ExternalActionStatus.PREPARED,
        ExternalActionStatus.IN_PROGRESS,
    )
    first_database.close()

    adapter = RecordingAdapter()
    second_database = SQLiteDatabase(path)
    restarted_service, _ = make_service(second_database, adapter)
    recovered = restarted_service.execute("operation-1", human_approved=True)

    assert recovered.status == ExternalActionStatus.RECONCILIATION_REQUIRED
    assert "ambiguous outcome" in recovered.error
    assert adapter.calls == []


@pytest.mark.parametrize(
    "column,value",
    [
        ("payload_json", "not-json"),
        ("payload_json", "[]"),
        ("status", "failed"),
        ("created_at", "not-a-date"),
    ],
)
def test_malformed_persisted_state_is_rejected(column, value):
    database = SQLiteDatabase()
    _, repository = make_service(database, RecordingAdapter())
    repository.create(ExternalActionOperation("operation-1", "application", {}))
    database.connection.execute(
        f"UPDATE external_action_operations SET {column} = ? WHERE operation_id = ?",
        (value, "operation-1"),
    )
    database.connection.commit()

    with pytest.raises(ValueError, match="malformed"):
        repository.get("operation-1")


def test_repository_validates_json_and_transitions():
    _, repository = make_service(SQLiteDatabase(), RecordingAdapter())

    with pytest.raises(ValueError, match="JSON-serializable"):
        repository.create(ExternalActionOperation("bad-json", "application", {"bad": object()}))
    with pytest.raises(ValueError, match="must be prepared"):
        repository.create(
            ExternalActionOperation(
                "already-running",
                "application",
                {},
                status=ExternalActionStatus.IN_PROGRESS,
            )
        )
    with pytest.raises(ValueError, match="Invalid external-action transition"):
        repository.transition(
            "missing",
            ExternalActionStatus.PREPARED,
            ExternalActionStatus.SUCCEEDED,
            result={},
        )
    with pytest.raises(OperationConflictError):
        repository.transition(
            "missing",
            ExternalActionStatus.PREPARED,
            ExternalActionStatus.IN_PROGRESS,
        )


def test_unknown_operation_and_invalid_identifiers_are_rejected():
    service, repository = make_service(SQLiteDatabase(), RecordingAdapter())

    with pytest.raises(KeyError, match="Unknown"):
        service.execute("missing", human_approved=True)
    with pytest.raises(ValueError):
        repository.get("  ")
    with pytest.raises(ValueError):
        service.prepare("", "application", {})
    with pytest.raises(ValueError):
        service.prepare("operation-1", "", {})
