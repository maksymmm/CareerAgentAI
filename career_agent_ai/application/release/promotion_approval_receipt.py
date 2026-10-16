"""Durable single-use operator decisions for the no-I/O promotion executor."""

from hashlib import sha256
from dataclasses import replace

from career_agent_ai.application.external_actions import ExternalActionService, ExternalActionStatus
from career_agent_ai.application.external_actions.external_action_repository import (
    ExternalActionOperationRepository,
)
from career_agent_ai.application.release.promotion_approval import (
    PromotionApprovalDecision,
    PromotionApprovalSubmission,
    validate_promotion_approval_submission,
)
from career_agent_ai.application.release.promotion_execution import (
    DryRunPromotionAdapter,
    PromotionExecutionService,
    validate_promotion_operation_id,
    validate_promotion_plan_payload,
)
from career_agent_ai.application.external_actions import ExternalActionOperation


class PromotionApprovalReceiptService:
    """Persist one immutable decision and consume approval once, without live I/O.

    Consumption is claimed durably before execution. A crash after the claim must
    be inspected, never retried automatically with the same approval.
    """

    def __init__(self, repository: ExternalActionOperationRepository) -> None:
        self._repository = repository
        self._executor = PromotionExecutionService(ExternalActionService(
            repository,
            PromotionExecutionService.action_adapter(DryRunPromotionAdapter()),
        ))

    def record(
        self,
        submission: PromotionApprovalSubmission,
        *,
        authenticated_operator_id: str,
        granted_scopes: tuple[str, ...],
    ) -> ExternalActionOperation:
        """Validate current intent and persist an immutable approve/decline receipt."""
        operation = self._repository.get(submission.operation_id)
        if operation is None:
            raise KeyError("Unknown promotion operation")
        decision = validate_promotion_approval_submission(
            operation, authenticated_operator_id=authenticated_operator_id,
            granted_scopes=granted_scopes, submission=submission,
        )
        return self._repository.create(ExternalActionOperation(
            operation_id=self._receipt_id(submission.operation_id),
            action_type="release.approval.receipt.dry-run",
            payload={
                "schema_version": 1,
                "operation_id": submission.operation_id,
                "operator_id": submission.operator_id,
                "action_fingerprint": submission.action_fingerprint,
                "decision": decision.value,
            },
        ))

    def consume(
        self,
        operation_id: str,
        *,
        authenticated_operator_id: str,
        granted_scopes: tuple[str, ...],
    ) -> ExternalActionOperation:
        """Claim a matching approval once, then invoke only the fixed dry-run adapter."""
        receipt_id = self._receipt_id(operation_id)
        receipt = self._repository.get(receipt_id)
        if receipt is None or receipt.action_type != "release.approval.receipt.dry-run":
            raise PermissionError("A durable promotion approval is required")
        submission = PromotionApprovalSubmission.from_mapping(receipt.payload)
        operation = self._repository.get(operation_id)
        if operation is None or submission.operation_id != operation_id:
            raise ValueError("Approval does not match the promotion operation")
        decision = validate_promotion_approval_submission(
            operation, authenticated_operator_id=authenticated_operator_id,
            granted_scopes=granted_scopes, submission=submission,
        )
        if decision != PromotionApprovalDecision.APPROVE:
            raise PermissionError("Promotion was declined")
        plan = validate_promotion_plan_payload(operation.payload)
        self._repository.transition(
            receipt_id, ExternalActionStatus.PREPARED, ExternalActionStatus.IN_PROGRESS,
        )
        result = self._executor.execute(operation_id, plan=plan, human_approved=True)
        if result is None:
            raise RuntimeError("Dry-run outcome requires inspection; approval is consumed")
        return self._repository.transition(
            receipt_id, ExternalActionStatus.IN_PROGRESS, ExternalActionStatus.SUCCEEDED,
            result=result.as_dict(),
        )

    @staticmethod
    def _receipt_id(operation_id: str) -> str:
        operation_id = validate_promotion_operation_id(operation_id)
        return "promotion-approval:" + sha256(operation_id.encode("ascii")).hexdigest()

    def recover_claim(
        self,
        operation_id: str,
        *,
        authenticated_operator_id: str,
        granted_scopes: tuple[str, ...],
    ) -> ExternalActionOperation:
        """Classify an interrupted execution for reconciliation without adapter I/O.

        This is an explicit operator recovery action, not automatic consumption or
        permission to reuse the approval. Repeated recovery remains idempotent.
        """
        receipt_id = self._receipt_id(operation_id)
        receipt = self._repository.get(receipt_id)
        operation = self._repository.get(operation_id)
        if receipt is None or receipt.action_type != "release.approval.receipt.dry-run":
            raise PermissionError("A durable promotion approval is required")
        submission = PromotionApprovalSubmission.from_mapping(receipt.payload)
        if operation is None or submission.operation_id != operation_id:
            raise ValueError("Approval does not match the promotion operation")
        decision = validate_promotion_approval_submission(
            replace(operation, status=ExternalActionStatus.PREPARED),
            authenticated_operator_id=authenticated_operator_id,
            granted_scopes=granted_scopes, submission=submission,
        )
        if decision != PromotionApprovalDecision.APPROVE:
            raise PermissionError("Promotion was declined")
        recoverable = {ExternalActionStatus.IN_PROGRESS, ExternalActionStatus.RECONCILIATION_REQUIRED}
        if receipt.status not in recoverable or operation.status not in recoverable:
            raise ValueError("Only an interrupted promotion execution can be recovered")
        if operation.status == ExternalActionStatus.IN_PROGRESS:
            self._repository.transition(
                operation_id, ExternalActionStatus.IN_PROGRESS,
                ExternalActionStatus.RECONCILIATION_REQUIRED,
                error="Interrupted promotion execution requires independent reconciliation",
            )
        if receipt.status == ExternalActionStatus.RECONCILIATION_REQUIRED:
            return receipt
        return self._repository.transition(
            receipt_id, ExternalActionStatus.IN_PROGRESS,
            ExternalActionStatus.RECONCILIATION_REQUIRED,
            error="Approval consumed; interrupted execution must not be retried",
        )
