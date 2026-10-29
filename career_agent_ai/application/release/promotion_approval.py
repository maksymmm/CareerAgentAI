"""Authenticated operator approval contract for one prepared promotion."""

from __future__ import annotations

import hmac
import json
import re
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from career_agent_ai.application.external_actions import (
    ExternalActionOperation,
    ExternalActionStatus,
)
from career_agent_ai.application.release.promotion_execution import (
    validate_promotion_operation_id,
    validate_promotion_plan_payload,
)

_ACTION_TYPE = "release.promote.dry-run"
_REQUIRED_SCOPE = "release:promote:dry-run"
_OPERATOR_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,199}\Z")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")
_VISIBLE_FIELDS = (
    "environment",
    "execution_mode",
    "plan_sha256",
    "rollback_reference",
    "source_commit",
    "target_reference",
)


class PromotionApprovalDecision(str, Enum):
    """Explicit operator decisions accepted by the release boundary."""

    APPROVE = "approve"
    DECLINE = "decline"


@dataclass(frozen=True, slots=True)
class PromotionApprovalPrompt:
    """Minimal immutable projection shown to one authorized operator."""

    operation_id: str
    operator_id: str
    required_scope: str
    details: Mapping[str, Any]
    action_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        """Return the stable JSON-safe wire representation."""

        return {
            "schema_version": 1,
            "operation_id": self.operation_id,
            "action_type": _ACTION_TYPE,
            "required_scope": self.required_scope,
            "details": dict(self.details),
            "action_fingerprint": self.action_fingerprint,
            "allowed_decisions": [item.value for item in PromotionApprovalDecision],
        }


@dataclass(frozen=True, slots=True)
class PromotionApprovalSubmission:
    """Strict response bound to one operator and displayed promotion intent."""

    operation_id: str
    operator_id: str
    action_fingerprint: str
    decision: PromotionApprovalDecision

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "operation_id", validate_promotion_operation_id(self.operation_id)
        )
        object.__setattr__(self, "operator_id", _operator_identifier(self.operator_id))
        if not isinstance(self.action_fingerprint, str) or not _FINGERPRINT.fullmatch(
            self.action_fingerprint
        ):
            raise ValueError("promotion action_fingerprint must be a lowercase SHA-256 digest")
        try:
            decision = PromotionApprovalDecision(self.decision)
        except (TypeError, ValueError) as exc:
            raise ValueError("promotion decision must be approve or decline") from exc
        object.__setattr__(self, "decision", decision)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "PromotionApprovalSubmission":
        """Parse an untrusted strict versioned approval payload."""

        required = {
            "schema_version",
            "operation_id",
            "operator_id",
            "action_fingerprint",
            "decision",
        }
        if not isinstance(payload, Mapping) or set(payload) != required:
            raise ValueError("promotion approval fields do not match schema version 1")
        if payload["schema_version"] != 1 or isinstance(payload["schema_version"], bool):
            raise ValueError("promotion approval schema_version must be 1")
        return cls(
            operation_id=payload["operation_id"],
            operator_id=payload["operator_id"],
            action_fingerprint=payload["action_fingerprint"],
            decision=payload["decision"],
        )


def build_promotion_approval_prompt(
    operation: ExternalActionOperation,
    *,
    operator_id: str,
    granted_scopes: Iterable[str],
) -> PromotionApprovalPrompt:
    """Project a prepared dry-run promotion after authenticated scope validation."""

    operator_id = _operator_identifier(operator_id)
    operation_id = validate_promotion_operation_id(operation.operation_id)
    scopes = _validated_scopes(granted_scopes)
    if _REQUIRED_SCOPE not in scopes:
        raise PermissionError("operator lacks the required promotion approval scope")
    if operation.action_type != _ACTION_TYPE:
        raise ValueError("operation is not a dry-run promotion")
    if operation.status != ExternalActionStatus.PREPARED:
        raise ValueError("promotion operation is not awaiting approval")
    plan = validate_promotion_plan_payload(operation.payload)
    details = {
        "environment": plan.environment,
        "execution_mode": plan.execution_mode,
        "plan_sha256": operation.payload["plan_sha256"],
        "rollback_reference": plan.rollback_reference,
        "source_commit": plan.source_commit,
        "target_reference": plan.target_reference,
    }
    encoded = json.dumps(
        {
            "action_type": _ACTION_TYPE,
            "operation_id": operation_id,
            "operator_id": operator_id,
            "required_scope": _REQUIRED_SCOPE,
            "details": details,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return PromotionApprovalPrompt(
        operation_id=operation_id,
        operator_id=operator_id,
        required_scope=_REQUIRED_SCOPE,
        details=MappingProxyType(dict(details)),
        action_fingerprint=sha256(encoded).hexdigest(),
    )


def validate_promotion_approval_submission(
    operation: ExternalActionOperation,
    *,
    authenticated_operator_id: str,
    granted_scopes: Iterable[str],
    submission: PromotionApprovalSubmission,
) -> PromotionApprovalDecision:
    """Rebuild the current prompt and reject foreign, stale, or under-scoped decisions."""

    prompt = build_promotion_approval_prompt(
        operation,
        operator_id=authenticated_operator_id,
        granted_scopes=granted_scopes,
    )
    if not hmac.compare_digest(prompt.operator_id, submission.operator_id):
        raise PermissionError("promotion approval belongs to another operator")
    if not hmac.compare_digest(prompt.operation_id, submission.operation_id):
        raise ValueError("promotion approval belongs to another operation")
    if not hmac.compare_digest(prompt.action_fingerprint, submission.action_fingerprint):
        raise ValueError("promotion approval is stale or does not match the displayed intent")
    return submission.decision


def _operator_identifier(value: Any) -> str:
    if not isinstance(value, str) or not _OPERATOR_IDENTIFIER.fullmatch(value):
        raise ValueError("operator_id is invalid")
    return value


def _validated_scopes(values: Iterable[str]) -> frozenset[str]:
    if isinstance(values, (str, bytes)):
        raise TypeError("granted_scopes must be an iterable of scope strings")
    try:
        scopes = frozenset(values)
    except TypeError as exc:
        raise TypeError("granted_scopes must be an iterable of scope strings") from exc
    if any(not isinstance(value, str) or not value for value in scopes):
        raise ValueError("granted_scopes contains an invalid scope")
    return scopes
