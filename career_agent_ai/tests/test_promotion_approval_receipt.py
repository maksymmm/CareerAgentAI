import pytest

from career_agent_ai.application.external_actions import ExternalActionOperation, ExternalActionStatus
from career_agent_ai.application.release.promotion_approval import (
    PromotionApprovalSubmission, build_promotion_approval_prompt,
)
from career_agent_ai.application.release import PromotionApprovalReceiptService
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import SQLiteExternalActionOperationRepository
from career_agent_ai.tests.test_promotion_execution import _plan, _service, DryRunPromotionAdapter

SCOPES = ("release:promote:dry-run",)


@pytest.mark.parametrize("boundary", ["receipt_claim", "adapter_entered", "outcome_saved"])
def test_abrupt_process_loss_preserves_single_use_approval(tmp_path, boundary):
    """Kill a real worker without cleanup at each durable execution boundary."""
    import subprocess
    import sys
    import textwrap
    from pathlib import Path
    from career_agent_ai.application.external_actions.external_action_repository import OperationConflictError

    database_path = tmp_path / "crash.sqlite"
    marker = tmp_path / "adapter-called"
    script = textwrap.dedent('''
        import os
        import sys
        from pathlib import Path
        from career_agent_ai.tests.test_promotion_approval_receipt import prepare, SCOPES
        from career_agent_ai.tests.test_promotion_execution import _service, DryRunPromotionAdapter
        from career_agent_ai.application.external_actions import ExternalActionStatus
        from career_agent_ai.application.release import PromotionApprovalReceiptService
        from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
        from career_agent_ai.application.storage.sqlite_external_action_repository import SQLiteExternalActionOperationRepository

        path, boundary, marker = sys.argv[1:]
        database = SQLiteDatabase(path)
        _, service, submission = prepare(database)
        receipt = service.record(submission, authenticated_operator_id="operator", granted_scopes=SCOPES)

        class CrashRepository(SQLiteExternalActionOperationRepository):
            def transition(self, operation_id, expected, status, **kwargs):
                result = super().transition(operation_id, expected, status, **kwargs)
                if (boundary == "receipt_claim" and operation_id == receipt.operation_id
                        and status == ExternalActionStatus.IN_PROGRESS):
                    os._exit(73)
                if (boundary == "outcome_saved" and operation_id == "promotion:one"
                        and status == ExternalActionStatus.SUCCEEDED):
                    os._exit(73)
                return result

        class MarkedAdapter(DryRunPromotionAdapter):
            def promote(self, *args, **kwargs):
                Path(marker).write_text("called", encoding="ascii")
                if boundary == "adapter_entered":
                    os._exit(73)
                return super().promote(*args, **kwargs)

        repository = CrashRepository(database)
        service = PromotionApprovalReceiptService(repository)
        from career_agent_ai.application.external_actions import ExternalActionService
        from career_agent_ai.application.release.promotion_execution import PromotionExecutionService
        service._executor = PromotionExecutionService(ExternalActionService(
            repository, PromotionExecutionService.action_adapter(MarkedAdapter())))
        service.consume("promotion:one", authenticated_operator_id="operator", granted_scopes=SCOPES)
        raise AssertionError("crash boundary was not reached")
    ''')
    worker = subprocess.run([sys.executable, "-c", script, str(database_path), boundary, str(marker)],
                            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=15)
    assert worker.returncode == 73, worker.stderr
    assert marker.exists() == (boundary != "receipt_claim")
    database = SQLiteDatabase(str(database_path))
    try:
        repository = SQLiteExternalActionOperationRepository(database)
        service = PromotionApprovalReceiptService(repository)
        with pytest.raises(OperationConflictError):
            service.consume("promotion:one", authenticated_operator_id="operator", granted_scopes=SCOPES)
        recovered = service.recover_claim("promotion:one", authenticated_operator_id="operator", granted_scopes=SCOPES)
        expected = (ExternalActionStatus.SUCCEEDED if boundary == "outcome_saved"
                    else ExternalActionStatus.RECONCILIATION_REQUIRED)
        assert recovered.status == expected
        assert service.recover_claim("promotion:one", authenticated_operator_id="operator", granted_scopes=SCOPES) == recovered
        with pytest.raises(OperationConflictError):
            service.consume("promotion:one", authenticated_operator_id="operator", granted_scopes=SCOPES)
        assert service._executor._external_actions._adapter._adapter._results == {}
        assert repository.get("promotion:one").status == {
            "receipt_claim": ExternalActionStatus.PREPARED,
            "adapter_entered": ExternalActionStatus.RECONCILIATION_REQUIRED,
            "outcome_saved": ExternalActionStatus.SUCCEEDED,
        }[boundary]
    finally:
        database.close()


def prepare(database, decision="approve"):
    executor, repository = _service(database, DryRunPromotionAdapter())
    with pytest.raises(PermissionError):
        executor.execute("promotion:one", plan=_plan(), human_approved=False)
    operation = repository.get("promotion:one")
    prompt = build_promotion_approval_prompt(operation, operator_id="operator", granted_scopes=SCOPES)
    submission = PromotionApprovalSubmission("promotion:one", "operator", prompt.action_fingerprint, decision)
    service = PromotionApprovalReceiptService(repository)
    return repository, service, submission


def test_approval_survives_restart_and_is_consumed_once(tmp_path):
    path = str(tmp_path / "receipts.sqlite")
    database = SQLiteDatabase(path)
    repository, service, submission = prepare(database)
    receipt = service.record(submission, authenticated_operator_id="operator", granted_scopes=SCOPES)
    assert service.record(submission, authenticated_operator_id="operator", granted_scopes=SCOPES) == receipt
    database.close()
    repository = SQLiteExternalActionOperationRepository(SQLiteDatabase(path))
    service = PromotionApprovalReceiptService(repository)
    result = service.consume("promotion:one", authenticated_operator_id="operator", granted_scopes=SCOPES)
    assert result.status == ExternalActionStatus.SUCCEEDED
    assert result.result["execution_mode"] == "dry-run"
    from career_agent_ai.application.external_actions.external_action_repository import OperationConflictError
    with pytest.raises(OperationConflictError):
        service.consume("promotion:one", authenticated_operator_id="operator", granted_scopes=SCOPES)


@pytest.mark.parametrize("decision,operator,scopes", [
    ("decline", "operator", SCOPES),
    ("approve", "foreign", SCOPES),
    ("approve", "operator", ()),
])
def test_invalid_approval_does_not_execute(decision, operator, scopes):
    repository, service, submission = prepare(SQLiteDatabase(), decision)
    receipt = service.record(submission, authenticated_operator_id="operator", granted_scopes=SCOPES)
    with pytest.raises(PermissionError):
        service.consume("promotion:one", authenticated_operator_id=operator, granted_scopes=scopes)
    assert repository.get("promotion:one").status == ExternalActionStatus.PREPARED
    assert repository.get(receipt.operation_id).status == ExternalActionStatus.PREPARED


def test_crashed_claim_cannot_reuse_approval():
    repository, service, submission = prepare(SQLiteDatabase())
    receipt = service.record(submission, authenticated_operator_id="operator", granted_scopes=SCOPES)
    repository.transition(receipt.operation_id, ExternalActionStatus.PREPARED, ExternalActionStatus.IN_PROGRESS)
    from career_agent_ai.application.external_actions.external_action_repository import OperationConflictError
    with pytest.raises(OperationConflictError):
        service.consume("promotion:one", authenticated_operator_id="operator", granted_scopes=SCOPES)
    assert repository.get("promotion:one").status == ExternalActionStatus.PREPARED


def test_missing_receipt_and_substituted_intent_fail_closed():
    repository, service, submission = prepare(SQLiteDatabase())
    with pytest.raises(PermissionError):
        service.consume("promotion:one", authenticated_operator_id="operator", granted_scopes=SCOPES)
    service.record(submission, authenticated_operator_id="operator", granted_scopes=SCOPES)
    # A stale/substituted operator decision cannot authorize another operation.
    with pytest.raises(ValueError):
        service.record(PromotionApprovalSubmission(
            submission.operation_id, submission.operator_id, "0" * 64, "approve",
        ), authenticated_operator_id="operator", granted_scopes=SCOPES)
    assert repository.get("promotion:one").status == ExternalActionStatus.PREPARED


def test_decline_cannot_be_replaced_by_approval():
    from career_agent_ai.application.external_actions.external_action_repository import OperationConflictError
    repository, service, submission = prepare(SQLiteDatabase(), "decline")
    service.record(submission, authenticated_operator_id="operator", granted_scopes=SCOPES)
    with pytest.raises(OperationConflictError):
        service.record(PromotionApprovalSubmission(
            submission.operation_id, submission.operator_id, submission.action_fingerprint, "approve",
        ), authenticated_operator_id="operator", granted_scopes=SCOPES)


def test_mid_execution_restart_is_reconciled_without_adapter_retry(tmp_path):
    class CrashingAdapter:
        def promote(self, *args, **kwargs):
            raise SystemExit("simulated process loss")

    path = str(tmp_path / "interrupted.sqlite")
    database = SQLiteDatabase(path)
    repository, service, submission = prepare(database)
    receipt = service.record(submission, authenticated_operator_id="operator", granted_scopes=SCOPES)
    service._executor, _ = _service(database, CrashingAdapter())
    with pytest.raises(SystemExit):
        service.consume("promotion:one", authenticated_operator_id="operator", granted_scopes=SCOPES)
    database.close()
    repository = SQLiteExternalActionOperationRepository(SQLiteDatabase(path))
    restarted = PromotionApprovalReceiptService(repository)
    for operator, scopes in (("foreign", SCOPES), ("operator", ())):
        with pytest.raises(PermissionError):
            restarted.recover_claim("promotion:one", authenticated_operator_id=operator, granted_scopes=scopes)
        assert repository.get("promotion:one").status == ExternalActionStatus.IN_PROGRESS
        assert repository.get(receipt.operation_id).status == ExternalActionStatus.IN_PROGRESS
    recovered = restarted.recover_claim("promotion:one", authenticated_operator_id="operator", granted_scopes=SCOPES)
    assert recovered.status == ExternalActionStatus.RECONCILIATION_REQUIRED
    assert repository.get("promotion:one").status == ExternalActionStatus.RECONCILIATION_REQUIRED
    assert restarted.recover_claim("promotion:one", authenticated_operator_id="operator", granted_scopes=SCOPES) == recovered
    from career_agent_ai.application.external_actions.external_action_repository import OperationConflictError
    with pytest.raises(OperationConflictError):
        restarted.consume("promotion:one", authenticated_operator_id="operator", granted_scopes=SCOPES)
    assert restarted._executor._external_actions._adapter._adapter._results == {}


def test_unclaimed_approval_cannot_be_recovered():
    repository, service, submission = prepare(SQLiteDatabase())
    service.record(submission, authenticated_operator_id="operator", granted_scopes=SCOPES)
    from career_agent_ai.application.external_actions.external_action_repository import OperationConflictError
    with pytest.raises(OperationConflictError):
        service.recover_claim("promotion:one", authenticated_operator_id="operator", granted_scopes=SCOPES)


@pytest.mark.parametrize("executed", [False, True])
def test_recovery_at_receipt_execution_boundaries(tmp_path, executed):
    path = str(tmp_path / "boundary.sqlite")
    database = SQLiteDatabase(path)
    repository, service, submission = prepare(database)
    receipt = service.record(submission, authenticated_operator_id="operator", granted_scopes=SCOPES)
    repository.transition(receipt.operation_id, ExternalActionStatus.PREPARED, ExternalActionStatus.IN_PROGRESS)
    if executed:
        service._executor.execute("promotion:one", plan=_plan(), human_approved=True)
    database.close()
    repository = SQLiteExternalActionOperationRepository(SQLiteDatabase(path))
    restarted = PromotionApprovalReceiptService(repository)
    recovered = restarted.recover_claim("promotion:one", authenticated_operator_id="operator", granted_scopes=SCOPES)
    expected = ExternalActionStatus.SUCCEEDED if executed else ExternalActionStatus.RECONCILIATION_REQUIRED
    assert recovered.status == expected
    if executed:
        assert dict(recovered.result) == dict(repository.get("promotion:one").result)
    else:
        assert repository.get("promotion:one").status == ExternalActionStatus.PREPARED
    assert restarted.recover_claim("promotion:one", authenticated_operator_id="operator", granted_scopes=SCOPES) == recovered
    assert restarted._executor._external_actions._adapter._adapter._results == {}


def test_recovery_rejects_substituted_durable_success_result():
    repository, service, submission = prepare(SQLiteDatabase())
    receipt = service.record(submission, authenticated_operator_id="operator", granted_scopes=SCOPES)
    repository.transition(receipt.operation_id, ExternalActionStatus.PREPARED, ExternalActionStatus.IN_PROGRESS)
    service._executor.execute("promotion:one", plan=_plan(), human_approved=True)
    operation = repository.get("promotion:one")
    repository.replace_succeeded_result("promotion:one", {**operation.result, "target_reference": _plan().rollback_reference})
    with pytest.raises(ValueError):
        service.recover_claim("promotion:one", authenticated_operator_id="operator", granted_scopes=SCOPES)
    assert repository.get(receipt.operation_id).status == ExternalActionStatus.IN_PROGRESS


@pytest.mark.parametrize("field", [
    "execution_mode", "execution_id", "target_reference", "rollback_reference", "extra",
])
def test_recovery_rejects_inexact_durable_result_fields(field):
    repository, service, submission = prepare(SQLiteDatabase())
    receipt = service.record(submission, authenticated_operator_id="operator", granted_scopes=SCOPES)
    repository.transition(receipt.operation_id, ExternalActionStatus.PREPARED, ExternalActionStatus.IN_PROGRESS)
    service._executor.execute("promotion:one", plan=_plan(), human_approved=True)
    result = dict(repository.get("promotion:one").result)
    if field == "extra":
        result[field] = "unexpected"
    else:
        result.pop(field)
    repository.replace_succeeded_result("promotion:one", result)
    with pytest.raises(ValueError, match="fields do not match"):
        service.recover_claim("promotion:one", authenticated_operator_id="operator", granted_scopes=SCOPES)
    assert repository.get(receipt.operation_id).status == ExternalActionStatus.IN_PROGRESS
    assert dict(repository.get("promotion:one").result) == result
