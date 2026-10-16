import pytest

from career_agent_ai.application.external_actions import ExternalActionOperation, ExternalActionStatus
from career_agent_ai.application.release.promotion_approval import (
    PromotionApprovalSubmission, build_promotion_approval_prompt,
)
from career_agent_ai.application.release.promotion_approval_receipt import PromotionApprovalReceiptService
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
