from __future__ import annotations

import pytest

from career_agent_ai.application.career import (
    ApprovalDecision,
    CandidateApprovalSubmission,
    CareerLoopRequest,
    HumanActionEvent,
    HumanActionKind,
    build_candidate_approval_prompt,
    validate_candidate_approval_submission,
)
from career_agent_ai.application.career.autonomous_loop_models import CareerLoopState


def _state() -> CareerLoopState:
    return CareerLoopState(
        run_id="run-1",
        request=CareerLoopRequest(
            user_id="candidate-1",
            keyword="engineer",
            candidate_profile="profile that must not leak",
        ),
        version=7,
        pending_human_action=HumanActionEvent(
            kind=HumanActionKind.APPROVE_APPLICATION,
            title="Approve application",
            details={"company": "Example GmbH", "job_title": "Engineer"},
        ),
    )


def _submission(prompt, decision="approve") -> CandidateApprovalSubmission:
    return CandidateApprovalSubmission.from_mapping(
        {
            "schema_version": 1,
            "run_id": prompt.run_id,
            "state_version": prompt.state_version,
            "action_fingerprint": prompt.action_fingerprint,
            "decision": decision,
        }
    )


def test_prompt_is_owner_scoped_minimal_and_json_safe():
    prompt = build_candidate_approval_prompt(_state(), user_id="candidate-1")

    payload = prompt.to_dict()

    assert payload == {
        "schema_version": 1,
        "run_id": "run-1",
        "state_version": 7,
        "action_kind": "approve_application",
        "title": "Approve application",
        "details": {"company": "Example GmbH", "job_title": "Engineer"},
        "action_fingerprint": prompt.action_fingerprint,
        "allowed_decisions": ["approve", "decline"],
    }
    assert "profile that must not leak" not in str(payload)


def test_prompt_rejects_foreign_owner_and_missing_action():
    state = _state()
    with pytest.raises(PermissionError, match="belong"):
        build_candidate_approval_prompt(state, user_id="candidate-2")

    state.pending_human_action = None
    with pytest.raises(ValueError, match="no pending"):
        build_candidate_approval_prompt(state, user_id="candidate-1")


@pytest.mark.parametrize("decision, expected", [("approve", True), ("decline", False)])
def test_submission_preserves_the_explicit_current_decision(decision, expected):
    state = _state()
    prompt = build_candidate_approval_prompt(state, user_id="candidate-1")

    assert validate_candidate_approval_submission(
            state,
            user_id="candidate-1",
            submission=_submission(prompt, decision),
        ) is (ApprovalDecision.APPROVE if expected else ApprovalDecision.DECLINE)


def test_submission_rejects_stale_state_and_changed_displayed_action():
    state = _state()
    prompt = build_candidate_approval_prompt(state, user_id="candidate-1")
    submission = _submission(prompt)

    state.version += 1
    with pytest.raises(ValueError, match="stale"):
        validate_candidate_approval_submission(
            state, user_id="candidate-1", submission=submission
        )

    state.version = prompt.state_version
    state.pending_human_action = HumanActionEvent(
        kind=HumanActionKind.APPROVE_APPLICATION,
        title="Approve application",
        details={"company": "Different GmbH", "job_title": "Engineer"},
    )
    with pytest.raises(ValueError, match="pending action"):
        validate_candidate_approval_submission(
            state, user_id="candidate-1", submission=submission
        )


def test_submission_rejects_different_run_and_foreign_owner():
    state = _state()
    prompt = build_candidate_approval_prompt(state, user_id="candidate-1")
    submission = _submission(prompt)

    state.run_id = "run-2"
    with pytest.raises(ValueError, match="different career loop"):
        validate_candidate_approval_submission(
            state, user_id="candidate-1", submission=submission
        )
    state.run_id = "run-1"
    with pytest.raises(PermissionError, match="belong"):
        validate_candidate_approval_submission(
            state, user_id="candidate-2", submission=submission
        )


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("schema_version", 2, "schema_version"),
        ("state_version", True, "state_version"),
        ("state_version", -1, "state_version"),
        ("action_fingerprint", "not-a-digest", "SHA-256"),
        ("action_fingerprint", "0" * 62 + "  ", "SHA-256"),
        ("decision", True, "decision"),
        ("decision", "later", "decision"),
    ],
)
def test_submission_parser_rejects_invalid_wire_values(field, value, message):
    prompt = build_candidate_approval_prompt(_state(), user_id="candidate-1")
    payload = {
        "schema_version": 1,
        "run_id": prompt.run_id,
        "state_version": prompt.state_version,
        "action_fingerprint": prompt.action_fingerprint,
        "decision": ApprovalDecision.APPROVE.value,
    }
    payload[field] = value

    with pytest.raises(ValueError, match=message):
        CandidateApprovalSubmission.from_mapping(payload)


def test_submission_parser_rejects_unknown_fields():
    prompt = build_candidate_approval_prompt(_state(), user_id="candidate-1")
    payload = {
        "schema_version": 1,
        "run_id": prompt.run_id,
        "state_version": prompt.state_version,
        "action_fingerprint": prompt.action_fingerprint,
        "decision": "approve",
        "human_approved": True,
    }

    with pytest.raises(ValueError, match="fields"):
        CandidateApprovalSubmission.from_mapping(payload)


def test_direct_submission_construction_normalizes_decision_before_validation():
    state = _state()
    prompt = build_candidate_approval_prompt(state, user_id="candidate-1")
    submission = CandidateApprovalSubmission(
        run_id=prompt.run_id,
        state_version=prompt.state_version,
        action_fingerprint=prompt.action_fingerprint,
        decision="approve",
    )

    assert submission.decision is ApprovalDecision.APPROVE
    assert (
        validate_candidate_approval_submission(
            state,
            user_id="candidate-1",
            submission=submission,
        )
        is ApprovalDecision.APPROVE
    )


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("run_id", "not valid!", "run_id"),
        ("state_version", True, "state_version"),
        ("state_version", -1, "state_version"),
        ("action_fingerprint", "not-a-digest", "SHA-256"),
        ("action_fingerprint", "0" * 62 + "  ", "SHA-256"),
        ("decision", True, "decision"),
        ("decision", "later", "decision"),
    ],
)
def test_direct_submission_construction_rejects_invalid_values(field, value, message):
    prompt = build_candidate_approval_prompt(_state(), user_id="candidate-1")
    values = {
        "run_id": prompt.run_id,
        "state_version": prompt.state_version,
        "action_fingerprint": prompt.action_fingerprint,
        "decision": ApprovalDecision.APPROVE,
    }
    values[field] = value

    with pytest.raises((TypeError, ValueError), match=message):
        CandidateApprovalSubmission(**values)


def test_prompt_details_are_deeply_immutable_and_wire_output_is_detached():
    state = _state()
    state.pending_human_action = HumanActionEvent(
        kind=HumanActionKind.APPROVE_APPLICATION,
        title="Approve application",
        details={"document": {"sections": ["summary"]}},
    )
    prompt = build_candidate_approval_prompt(state, user_id="candidate-1")

    with pytest.raises(TypeError):
        prompt.details["document"] = {}
    with pytest.raises(TypeError):
        prompt.details["document"]["sections"][0] = "changed"
    wire = prompt.to_dict()
    wire["details"]["document"]["sections"][0] = "changed"

    assert prompt.to_dict()["details"]["document"]["sections"] == ["summary"]


@pytest.mark.parametrize(
    "kind",
    [
        HumanActionKind.RECONCILE_APPLICATION_SUBMISSION,
        HumanActionKind.RECONCILE_MESSAGE_DELIVERY,
        HumanActionKind.RECONCILE_INTERVIEW_RESPONSE,
    ],
)
def test_prompt_rejects_reconciliation_actions(kind):
    state = _state()
    state.pending_human_action = HumanActionEvent(
        kind=kind,
        title="Reconcile outcome",
        details={"operation_id": "operation-1"},
    )

    with pytest.raises(ValueError, match="reconciliation contract"):
        build_candidate_approval_prompt(state, user_id="candidate-1")
