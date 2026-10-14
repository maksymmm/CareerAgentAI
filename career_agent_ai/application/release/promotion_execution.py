"""Crash-safe, provider-neutral execution boundary for dry-run promotion plans."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from career_agent_ai.application.external_actions import (
    AmbiguousExternalActionError,
    ExternalActionOperation,
    ExternalActionService,
    ExternalActionStatus,
)
from career_agent_ai.application.release.promotion_manifest import PromotionManifestError
from career_agent_ai.application.release.promotion_plan import DryRunPromotionPlan

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z")
_ACTION_TYPE = "release.promote.dry-run"


@dataclass(frozen=True, slots=True)
class PromotionExecutionResult:
    """Validated outcome returned by a promotion execution adapter."""

    execution_id: str
    target_reference: str
    rollback_reference: str
    execution_mode: str = "dry-run"

    def __post_init__(self) -> None:
        if not isinstance(self.execution_id, str) or not _IDENTIFIER.fullmatch(
            self.execution_id
        ):
            raise PromotionManifestError("promotion execution ID is invalid")
        if self.execution_mode != "dry-run":
            raise PromotionManifestError("promotion execution must remain dry-run")
        # DryRunPromotionPlan owns the registry grammar. The bridge below compares
        # these values to an already validated plan before accepting the result.
        if not isinstance(self.target_reference, str) or not self.target_reference:
            raise PromotionManifestError("promotion result target is invalid")
        if not isinstance(self.rollback_reference, str) or not self.rollback_reference:
            raise PromotionManifestError("promotion result rollback target is invalid")

    def as_dict(self) -> dict[str, str]:
        """Return the strict JSON-safe durable result shape."""

        return {
            "execution_id": self.execution_id,
            "execution_mode": self.execution_mode,
            "rollback_reference": self.rollback_reference,
            "target_reference": self.target_reference,
        }


class PromotionExecutionAdapter(Protocol):
    """Provider-neutral port for executing one exact promotion transition."""

    def promote(
        self,
        operation_id: str,
        *,
        target_reference: str,
        rollback_reference: str,
    ) -> PromotionExecutionResult:
        """Execute idempotently using ``operation_id`` as the provider key."""


class DryRunPromotionAdapter:
    """Deterministic no-I/O adapter that cannot contact a deployment provider."""

    def __init__(self) -> None:
        self._results: dict[str, PromotionExecutionResult] = {}

    def promote(
        self,
        operation_id: str,
        *,
        target_reference: str,
        rollback_reference: str,
    ) -> PromotionExecutionResult:
        """Record and return a deterministic dry-run result without external I/O."""

        material = "\0".join((operation_id, target_reference, rollback_reference))
        execution_id = "dry-run:" + hashlib.sha256(material.encode("ascii")).hexdigest()
        result = PromotionExecutionResult(
            execution_id=execution_id,
            target_reference=target_reference,
            rollback_reference=rollback_reference,
        )
        existing = self._results.setdefault(operation_id, result)
        if existing != result:
            raise ValueError("operation_id is already bound to another promotion")
        return existing


class _PromotionActionAdapter:
    """Bridge the typed promotion port to the generic external-action coordinator."""

    def __init__(self, adapter: PromotionExecutionAdapter) -> None:
        self._adapter = adapter

    def execute(
        self,
        operation_id: str,
        action_type: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if action_type != _ACTION_TYPE:
            raise ValueError("unsupported promotion action type")
        plan = _plan_from_payload(payload)
        result = self._adapter.promote(
            operation_id,
            target_reference=plan.target_reference,
            rollback_reference=plan.rollback_reference,
        )
        if not isinstance(result, PromotionExecutionResult):
            raise AmbiguousExternalActionError(
                "Promotion provider returned a malformed result."
            )
        if (
            result.target_reference != plan.target_reference
            or result.rollback_reference != plan.rollback_reference
        ):
            raise AmbiguousExternalActionError(
                "Promotion provider result does not match the prepared transition."
            )
        return result.as_dict()


class PromotionExecutionService:
    """Prepare and execute one human-approved dry-run promotion idempotently."""

    def __init__(self, external_actions: ExternalActionService) -> None:
        self._external_actions = external_actions

    @staticmethod
    def action_adapter(adapter: PromotionExecutionAdapter) -> _PromotionActionAdapter:
        """Build the generic external-action bridge for a promotion adapter."""

        return _PromotionActionAdapter(adapter)

    def get_operation(self, operation_id: str) -> ExternalActionOperation | None:
        """Return durable promotion state without invoking the adapter."""

        return self._external_actions.get(_validated_operation_id(operation_id))

    def execute(
        self,
        operation_id: str,
        *,
        plan: DryRunPromotionPlan,
        human_approved: bool,
    ) -> PromotionExecutionResult | None:
        """Execute the exact prepared transition after literal human approval."""

        operation_id = _validated_operation_id(operation_id)
        if not isinstance(plan, DryRunPromotionPlan):
            raise TypeError("plan must be a DryRunPromotionPlan")
        if not isinstance(human_approved, bool):
            raise TypeError("human_approved must be a boolean")
        payload = _plan_payload(plan)
        existing = self._external_actions.get(operation_id)
        if existing is None:
            self._external_actions.prepare(operation_id, _ACTION_TYPE, payload)
        elif existing.action_type != _ACTION_TYPE or dict(existing.payload) != payload:
            raise ValueError("operation_id is already bound to another promotion intent")
        operation = self._external_actions.execute(
            operation_id, human_approved=human_approved
        )
        if operation.status != ExternalActionStatus.SUCCEEDED:
            return None
        return _result_from_operation(operation, plan)

    def resolve_reconciliation(
        self,
        operation_id: str,
        *,
        promoted: bool,
        execution_id: str | None = None,
    ) -> ExternalActionOperation:
        """Resolve an ambiguous transition after independent provider verification."""

        operation_id = _validated_operation_id(operation_id)
        if not isinstance(promoted, bool):
            raise TypeError("promoted must be a boolean")
        operation = self._external_actions.get(operation_id)
        if operation is None:
            raise KeyError(f"Unknown promotion operation: {operation_id!r}")
        if operation.action_type != _ACTION_TYPE:
            raise ValueError("Operation is not a promotion")
        if operation.status == ExternalActionStatus.IN_PROGRESS:
            operation = self._external_actions.execute(
                operation_id, human_approved=True
            )
        if operation.status != ExternalActionStatus.RECONCILIATION_REQUIRED:
            raise ValueError("Promotion is not awaiting reconciliation")
        if not promoted:
            if execution_id is not None:
                raise ValueError("execution_id must be omitted when no promotion occurred")
            return self._external_actions.resolve_reconciliation(
                operation_id, confirmed_succeeded=False
            )
        if execution_id is None:
            raise ValueError("confirmed promotion requires execution_id")
        plan = _plan_from_payload(operation.payload)
        result = PromotionExecutionResult(
            execution_id=execution_id,
            target_reference=plan.target_reference,
            rollback_reference=plan.rollback_reference,
        )
        return self._external_actions.resolve_reconciliation(
            operation_id,
            confirmed_succeeded=True,
            result=result.as_dict(),
        )


def _plan_payload(plan: DryRunPromotionPlan) -> dict[str, object]:
    return {
        "environment": plan.environment,
        "execution_mode": plan.execution_mode,
        "plan_sha256": "sha256:" + hashlib.sha256(plan.to_json().encode("ascii")).hexdigest(),
        "publication_receipt_sha256": plan.publication_receipt_sha256,
        "rollback_reference": plan.rollback_reference,
        "schema_version": plan.schema_version,
        "source_commit": plan.source_commit,
        "target_reference": plan.target_reference,
    }


def _plan_from_payload(payload: Mapping[str, Any]) -> DryRunPromotionPlan:
    expected_fields = {
        "environment",
        "execution_mode",
        "plan_sha256",
        "publication_receipt_sha256",
        "rollback_reference",
        "schema_version",
        "source_commit",
        "target_reference",
    }
    if set(payload) != expected_fields:
        raise ValueError("promotion action payload does not match the schema")
    try:
        plan = DryRunPromotionPlan(
            source_commit=payload["source_commit"],
            target_reference=payload["target_reference"],
            rollback_reference=payload["rollback_reference"],
            publication_receipt_sha256=payload["publication_receipt_sha256"],
            environment=payload["environment"],
            execution_mode=payload["execution_mode"],
            schema_version=payload["schema_version"],
        )
    except (TypeError, PromotionManifestError) as exc:
        raise ValueError("promotion action payload is invalid") from exc
    if payload["plan_sha256"] != _plan_payload(plan)["plan_sha256"]:
        raise ValueError("promotion action plan checksum does not match")
    return plan


def _result_from_operation(
    operation: ExternalActionOperation,
    plan: DryRunPromotionPlan,
) -> PromotionExecutionResult:
    if operation.result is None:
        raise ValueError("Successful promotion has no durable result")
    try:
        result = PromotionExecutionResult(**dict(operation.result))
    except (TypeError, PromotionManifestError) as exc:
        raise ValueError("Successful promotion result is malformed") from exc
    plan.verify_target_reference(result.target_reference)
    plan.verify_rollback_reference(result.rollback_reference)
    return result


def _validated_operation_id(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError("operation_id is invalid")
    return value
