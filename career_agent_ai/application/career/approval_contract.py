"""Candidate-facing contract for explicit autonomous-loop decisions."""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from typing import Any, Mapping

from .autonomous_loop_models import (
    CareerLoopState,
    HumanActionEvent,
    validate_loop_identifier,
)


class ApprovalDecision(str, Enum):
    """Decisions a candidate may submit for one displayed human action."""

    APPROVE = "approve"
    DECLINE = "decline"


def _action_fingerprint(action: HumanActionEvent) -> str:
    """Bind approval to the exact action payload shown to the candidate."""
    try:
        encoded = json.dumps(
            {
                "kind": action.kind.value,
                "title": action.title,
                "details": dict(action.details),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("pending human action must be JSON-safe.") from exc
    return sha256(encoded).hexdigest()


@dataclass(frozen=True)
class CandidateApprovalPrompt:
    """Minimal JSON-safe action data that an approval UI may display."""

    run_id: str
    state_version: int
    action_kind: str
    title: str
    details: Mapping[str, Any]
    action_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        """Return the stable wire representation for a candidate UI."""
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "state_version": self.state_version,
            "action_kind": self.action_kind,
            "title": self.title,
            "details": dict(self.details),
            "action_fingerprint": self.action_fingerprint,
            "allowed_decisions": [decision.value for decision in ApprovalDecision],
        }


@dataclass(frozen=True)
class CandidateApprovalSubmission:
    """Strict candidate response bound to one prompt snapshot."""

    run_id: str
    state_version: int
    action_fingerprint: str
    decision: ApprovalDecision

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "CandidateApprovalSubmission":
        """Parse a strict versioned approval response from untrusted JSON data."""
        if not isinstance(payload, Mapping):
            raise TypeError("approval payload must be an object.")
        required = {
            "schema_version",
            "run_id",
            "state_version",
            "action_fingerprint",
            "decision",
        }
        if set(payload) != required:
            raise ValueError("approval payload fields do not match schema version 1.")
        if payload["schema_version"] != 1 or isinstance(
            payload["schema_version"], bool
        ):
            raise ValueError("approval schema_version must be 1.")
        run_id = validate_loop_identifier(payload["run_id"], "run_id")
        state_version = payload["state_version"]
        if (
            not isinstance(state_version, int)
            or isinstance(state_version, bool)
            or state_version < 0
        ):
            raise ValueError("approval state_version must be a non-negative integer.")
        fingerprint = payload["action_fingerprint"]
        if not isinstance(fingerprint, str) or len(fingerprint) != 64:
            raise ValueError("approval action_fingerprint must be a SHA-256 digest.")
        try:
            bytes.fromhex(fingerprint)
        except ValueError as exc:
            raise ValueError(
                "approval action_fingerprint must be a SHA-256 digest."
            ) from exc
        try:
            decision = ApprovalDecision(payload["decision"])
        except (TypeError, ValueError) as exc:
            raise ValueError("approval decision must be approve or decline.") from exc
        return cls(
            run_id=run_id,
            state_version=state_version,
            action_fingerprint=fingerprint,
            decision=decision,
        )


def build_candidate_approval_prompt(
    state: CareerLoopState, *, user_id: str
) -> CandidateApprovalPrompt:
    """Build an owner-scoped prompt without exposing the full loop request."""
    owner_id = validate_loop_identifier(user_id, "user_id")
    if not hmac.compare_digest(state.request.user_id, owner_id):
        raise PermissionError("approval request does not belong to this user.")
    action = state.pending_human_action
    if action is None:
        raise ValueError("career loop has no pending human action.")
    return CandidateApprovalPrompt(
        run_id=state.run_id,
        state_version=state.version,
        action_kind=action.kind.value,
        title=action.title,
        details=dict(action.details),
        action_fingerprint=_action_fingerprint(action),
    )


def validate_candidate_approval_submission(
    state: CareerLoopState,
    *,
    user_id: str,
    submission: CandidateApprovalSubmission,
) -> bool:
    """Validate ownership and freshness, then return the explicit boolean decision."""
    prompt = build_candidate_approval_prompt(state, user_id=user_id)
    if not hmac.compare_digest(prompt.run_id, submission.run_id):
        raise ValueError("approval response is for a different career loop.")
    if prompt.state_version != submission.state_version:
        raise ValueError("approval response is stale.")
    if not hmac.compare_digest(
        prompt.action_fingerprint, submission.action_fingerprint
    ):
        raise ValueError("approval response does not match the pending action.")
    return submission.decision is ApprovalDecision.APPROVE
