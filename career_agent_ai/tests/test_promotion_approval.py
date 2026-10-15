from dataclasses import replace

import pytest

from career_agent_ai.application.external_actions import ExternalActionOperation, ExternalActionStatus
from career_agent_ai.application.release import (
    PromotionApprovalDecision,
    PromotionApprovalSubmission,
    build_promotion_approval_prompt,
    validate_promotion_approval_submission,
)


def _operation(**payload_changes):
    payload = {
        "environment": "sandbox",
        "execution_mode": "dry-run",
        "plan_sha256": "sha256:" + "a" * 64,
        "publication_receipt_sha256": "sha256:" + "b" * 64,
        "rollback_reference": "registry.example/app@sha256:" + "c" * 64,
        "schema_version": 1,
        "source_commit": "d" * 40,
        "target_reference": "registry.example/app@sha256:" + "e" * 64,
    }
    payload.update(payload_changes)
    return ExternalActionOperation(
        operation_id="promote:release-1",
        action_type="release.promote.dry-run",
        payload=payload,
    )


def _submission(prompt, **changes):
    values = {
        "operation_id": prompt.operation_id,
        "operator_id": prompt.operator_id,
        "action_fingerprint": prompt.action_fingerprint,
        "decision": "approve",
    }
    values.update(changes)
    return PromotionApprovalSubmission(**values)


def test_prompt_is_minimal_scoped_and_does_not_expose_receipt_checksum():
    prompt = build_promotion_approval_prompt(
        _operation(), operator_id="operator:release", granted_scopes={"release:promote:dry-run"}
    )
    wire = prompt.to_dict()
    assert wire["required_scope"] == "release:promote:dry-run"
    assert wire["allowed_decisions"] == ["approve", "decline"]
    assert "publication_receipt_sha256" not in wire["details"]
    assert "operator_id" not in wire


def test_prompt_requires_exact_scope_and_prepared_release_operation():
    with pytest.raises(PermissionError, match="scope"):
        build_promotion_approval_prompt(_operation(), operator_id="operator:release", granted_scopes=set())
    with pytest.raises(ValueError, match="not awaiting approval"):
        build_promotion_approval_prompt(
            replace(_operation(), status=ExternalActionStatus.IN_PROGRESS),
            operator_id="operator:release",
            granted_scopes={"release:promote:dry-run"},
        )


def test_submission_revalidates_operator_scope_and_exact_current_intent():
    operation = _operation()
    prompt = build_promotion_approval_prompt(
        operation, operator_id="operator:release", granted_scopes={"release:promote:dry-run"}
    )
    assert validate_promotion_approval_submission(
        operation,
        authenticated_operator_id="operator:release",
        granted_scopes={"release:promote:dry-run"},
        submission=_submission(prompt),
    ) is PromotionApprovalDecision.APPROVE
    with pytest.raises(PermissionError, match="another operator"):
        validate_promotion_approval_submission(
            operation,
            authenticated_operator_id="operator:other",
            granted_scopes={"release:promote:dry-run"},
            submission=_submission(prompt),
        )
    with pytest.raises(ValueError, match="stale"):
        validate_promotion_approval_submission(
            _operation(target_reference="registry.example/app@sha256:" + "f" * 64),
            authenticated_operator_id="operator:release",
            granted_scopes={"release:promote:dry-run"},
            submission=_submission(prompt),
        )


def test_submission_parser_rejects_ambiguous_or_extra_fields():
    prompt = build_promotion_approval_prompt(
        _operation(), operator_id="operator:release", granted_scopes={"release:promote:dry-run"}
    )
    payload = {
        "schema_version": 1,
        "operation_id": prompt.operation_id,
        "operator_id": prompt.operator_id,
        "action_fingerprint": prompt.action_fingerprint,
        "decision": "approve",
    }
    assert PromotionApprovalSubmission.from_mapping(payload).decision is PromotionApprovalDecision.APPROVE
    with pytest.raises(ValueError, match="fields"):
        PromotionApprovalSubmission.from_mapping({**payload, "human_approved": True})
    with pytest.raises(ValueError, match="approve or decline"):
        PromotionApprovalSubmission.from_mapping({**payload, "decision": True})
