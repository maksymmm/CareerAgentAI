from __future__ import annotations

from typing import Any, Mapping

import pytest

from career_agent_ai.application.external_actions import (
    ExternalActionService,
    ExternalActionStatus,
)
from career_agent_ai.application.release import (
    DryRunPromotionAdapter,
    DryRunPromotionPlan,
    PromotionExecutionResult,
    PromotionExecutionService,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import (
    SQLiteExternalActionOperationRepository,
)

SOURCE = "a" * 40
REPOSITORY = "ghcr.io/maksymmm/careeragentai-candidate"
TARGET = REPOSITORY + "@sha256:" + "b" * 64
ROLLBACK = REPOSITORY + "@sha256:" + "c" * 64
RECEIPT = "sha256:" + "d" * 64


def _plan() -> DryRunPromotionPlan:
    return DryRunPromotionPlan(
        source_commit=SOURCE,
        target_reference=TARGET,
        rollback_reference=ROLLBACK,
        publication_receipt_sha256=RECEIPT,
    )


def _service(
    database: SQLiteDatabase,
    adapter: object,
) -> tuple[PromotionExecutionService, SQLiteExternalActionOperationRepository]:
    repository = SQLiteExternalActionOperationRepository(database)
    action_adapter = PromotionExecutionService.action_adapter(adapter)  # type: ignore[arg-type]
    external_actions = ExternalActionService(repository, action_adapter)
    return PromotionExecutionService(external_actions), repository


def test_dry_run_promotion_requires_approval_and_persists_exact_intent():
    adapter = DryRunPromotionAdapter()
    service, repository = _service(SQLiteDatabase(), adapter)

    with pytest.raises(PermissionError, match="human approval"):
        service.execute("promote:release-1", plan=_plan(), human_approved=False)

    prepared = repository.get("promote:release-1")
    assert prepared is not None
    assert prepared.status == ExternalActionStatus.PREPARED
    assert prepared.action_type == "release.promote.dry-run"
    assert prepared.payload["target_reference"] == TARGET
    assert prepared.payload["rollback_reference"] == ROLLBACK
    assert prepared.payload["execution_mode"] == "dry-run"


def test_approved_dry_run_is_idempotent_and_has_no_provider_side_effect():
    adapter = DryRunPromotionAdapter()
    service, repository = _service(SQLiteDatabase(), adapter)

    first = service.execute("promote:release-1", plan=_plan(), human_approved=True)
    replay = service.execute("promote:release-1", plan=_plan(), human_approved=True)

    assert first == replay
    assert first is not None
    assert first.execution_id.startswith("dry-run:")
    assert first.target_reference == TARGET
    assert first.rollback_reference == ROLLBACK
    assert repository.get("promote:release-1").status == ExternalActionStatus.SUCCEEDED


def test_operation_id_cannot_be_rebound_to_another_plan():
    service, _ = _service(SQLiteDatabase(), DryRunPromotionAdapter())
    service.execute("promote:release-1", plan=_plan(), human_approved=True)
    changed = DryRunPromotionPlan(
        source_commit=SOURCE,
        target_reference=REPOSITORY + "@sha256:" + "e" * 64,
        rollback_reference=ROLLBACK,
        publication_receipt_sha256=RECEIPT,
    )

    with pytest.raises(ValueError, match="another promotion intent"):
        service.execute("promote:release-1", plan=changed, human_approved=True)


class MismatchedAdapter:
    def promote(
        self,
        operation_id: str,
        *,
        target_reference: str,
        rollback_reference: str,
    ) -> PromotionExecutionResult:
        del operation_id, target_reference
        return PromotionExecutionResult(
            execution_id="provider:wrong",
            target_reference=REPOSITORY + "@sha256:" + "e" * 64,
            rollback_reference=rollback_reference,
        )


class MalformedAdapter:
    def promote(
        self,
        operation_id: str,
        *,
        target_reference: str,
        rollback_reference: str,
    ) -> object:
        del operation_id, target_reference, rollback_reference
        return {"execution_id": "not-a-typed-result"}


def test_mismatched_provider_result_requires_reconciliation_and_is_not_retried():
    service, repository = _service(SQLiteDatabase(), MismatchedAdapter())

    assert service.execute("promote:mismatch", plan=_plan(), human_approved=True) is None
    assert service.execute("promote:mismatch", plan=_plan(), human_approved=True) is None

    operation = repository.get("promote:mismatch")
    assert operation.status == ExternalActionStatus.RECONCILIATION_REQUIRED
    assert "does not match" in operation.error


def test_malformed_provider_result_requires_reconciliation():
    service, repository = _service(SQLiteDatabase(), MalformedAdapter())

    assert service.execute(
        "promote:malformed", plan=_plan(), human_approved=True
    ) is None

    operation = repository.get("promote:malformed")
    assert operation.status == ExternalActionStatus.RECONCILIATION_REQUIRED
    assert "malformed result" in operation.error


def test_restart_converts_in_progress_transition_to_reconciliation(tmp_path):
    path = str(tmp_path / "promotion.sqlite")
    first_database = SQLiteDatabase(path)
    first_service, repository = _service(first_database, DryRunPromotionAdapter())
    with pytest.raises(PermissionError):
        first_service.execute(
            "promote:restart", plan=_plan(), human_approved=False
        )
    repository.transition(
        "promote:restart",
        ExternalActionStatus.PREPARED,
        ExternalActionStatus.IN_PROGRESS,
    )
    first_database.close()

    second_database = SQLiteDatabase(path)
    restarted, restarted_repository = _service(
        second_database, DryRunPromotionAdapter()
    )
    assert restarted.execute(
        "promote:restart", plan=_plan(), human_approved=True
    ) is None
    assert (
        restarted_repository.get("promote:restart").status
        == ExternalActionStatus.RECONCILIATION_REQUIRED
    )


def test_reconciliation_can_confirm_exact_success():
    service, repository = _service(SQLiteDatabase(), MismatchedAdapter())
    service.execute("promote:reconcile", plan=_plan(), human_approved=True)

    resolved = service.resolve_reconciliation(
        "promote:reconcile",
        promoted=True,
        execution_id="provider:verified-1",
        observed_target_reference=TARGET,
        observed_rollback_reference=ROLLBACK,
    )

    assert resolved.status == ExternalActionStatus.SUCCEEDED
    assert resolved.result == {
        "execution_id": "provider:verified-1",
        "execution_mode": "dry-run",
        "rollback_reference": ROLLBACK,
        "target_reference": TARGET,
    }
    assert repository.get("promote:reconcile") == resolved


def test_reconciliation_no_effect_returns_to_prepared_without_execution_id():
    service, _ = _service(SQLiteDatabase(), MismatchedAdapter())
    service.execute("promote:no-effect", plan=_plan(), human_approved=True)

    reopened = service.resolve_reconciliation(
        "promote:no-effect", promoted=False
    )

    assert reopened.status == ExternalActionStatus.PREPARED


def test_reconciliation_rejects_tampered_durable_plan_checksum():
    database = SQLiteDatabase()
    service, repository = _service(database, MismatchedAdapter())
    service.execute("promote:tampered", plan=_plan(), human_approved=True)
    database.connection.execute(
        """
        UPDATE external_action_operations
        SET payload_json = replace(payload_json, ?, ?)
        WHERE operation_id = ?
        """,
        (TARGET, REPOSITORY + "@sha256:" + "e" * 64, "promote:tampered"),
    )
    database.connection.commit()

    with pytest.raises(ValueError, match="checksum"):
        service.resolve_reconciliation(
            "promote:tampered",
            promoted=True,
            execution_id="provider:verified",
            observed_target_reference=TARGET,
            observed_rollback_reference=ROLLBACK,
        )

    assert (
        repository.get("promote:tampered").status
        == ExternalActionStatus.RECONCILIATION_REQUIRED
    )


@pytest.mark.parametrize("approval", [None, 1, "true"])
def test_promotion_rejects_non_boolean_approval(approval):
    service, repository = _service(SQLiteDatabase(), DryRunPromotionAdapter())

    with pytest.raises(TypeError, match="boolean"):
        service.execute("promote:invalid", plan=_plan(), human_approved=approval)

    assert repository.get("promote:invalid") is None


@pytest.mark.parametrize("operation_id", ["", " has-space", "x" * 201, 1])
def test_promotion_rejects_invalid_operation_id(operation_id):
    service, _ = _service(SQLiteDatabase(), DryRunPromotionAdapter())

    with pytest.raises(ValueError, match="operation_id"):
        service.execute(operation_id, plan=_plan(), human_approved=True)


@pytest.mark.parametrize(
    "promoted,execution_id,target,rollback,error",
    [
        (True, None, TARGET, ROLLBACK, "requires execution_id"),
        (True, "provider:1", None, ROLLBACK, "observed transition"),
        (True, "provider:1", TARGET, None, "observed transition"),
        (
            True,
            "provider:1",
            REPOSITORY + "@sha256:" + "e" * 64,
            ROLLBACK,
            "exactly match",
        ),
        (True, "provider:1", TARGET, TARGET, "exactly match"),
        (False, "provider:unexpected", None, None, "must be omitted"),
        (False, None, TARGET, None, "must be omitted"),
        ("true", None, None, None, "boolean"),
    ],
)
def test_reconciliation_rejects_ambiguous_claims(
    promoted, execution_id, target, rollback, error
):
    service, _ = _service(SQLiteDatabase(), MismatchedAdapter())
    service.execute("promote:claims", plan=_plan(), human_approved=True)

    with pytest.raises((TypeError, ValueError), match=error):
        service.resolve_reconciliation(
            "promote:claims",
            promoted=promoted,
            execution_id=execution_id,
            observed_target_reference=target,
            observed_rollback_reference=rollback,
        )


class RecordingRawAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Mapping[str, Any]]] = []

    def execute(
        self,
        operation_id: str,
        action_type: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        self.calls.append((operation_id, action_type, payload))
        return {}


def test_operation_lookup_validates_identifier_without_executing():
    raw = RecordingRawAdapter()
    external = ExternalActionService(
        SQLiteExternalActionOperationRepository(SQLiteDatabase()), raw
    )
    service = PromotionExecutionService(external)

    assert service.get_operation("promote:missing") is None
    assert raw.calls == []
    with pytest.raises(ValueError, match="operation_id"):
        service.get_operation("bad id")
