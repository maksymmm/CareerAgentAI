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
    with pytest.raises(ValueError):
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
    with pytest.raises(ValueError):
        restarted.consume("promotion:one", authenticated_operator_id="operator", granted_scopes=SCOPES)
    assert restarted._executor._external_actions._adapter._adapter._results == {}


def test_unclaimed_approval_cannot_be_recovered():
    repository, service, submission = prepare(SQLiteDatabase())
    service.record(submission, authenticated_operator_id="operator", granted_scopes=SCOPES)
    with pytest.raises(ValueError):
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
