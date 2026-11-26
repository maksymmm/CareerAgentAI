"""Candidate-approved, evidence-verified sandbox application withdrawal."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Callable, Mapping, Any

from career_agent_ai.application.external_actions import (
    AmbiguousExternalActionError,
    ExternalActionService,
    ExternalActionStatus,
)
from career_agent_ai.application.external_actions.external_action_operation import (
    ExternalActionOperation,
)
from career_agent_ai.application.external_actions.external_action_repository import (
    ExternalActionOperationRepository,
    OperationConflictError,
)
from career_agent_ai.application.jobs.job_application import JobApplication
from career_agent_ai.application.jobs.job_application_repository import (
    ApplicationConflictError,
    JobApplicationRepository,
)
from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus
from career_agent_ai.application.storage.sqlite_sandbox_withdrawal_provider import (
    SQLiteSandboxApplicationWithdrawalProvider,
    WithdrawalEvidenceState,
)


class _ApplicationWithdrawalActionAdapter:
    """Translate the sandbox provider contract into the generic action boundary."""

    def __init__(self, provider: SQLiteSandboxApplicationWithdrawalProvider) -> None:
        self._provider = provider

    def execute(
        self,
        operation_id: str,
        action_type: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if action_type != "application.withdraw":
            raise ValueError("Unsupported application-withdrawal action type.")
        application_id = payload.get("application_id")
        provider_submission_id = payload.get("provider_submission_id")
        if not isinstance(application_id, str) or not isinstance(provider_submission_id, str):
            raise ValueError("Prepared application-withdrawal intent is malformed.")
        try:
            receipt = self._provider.withdraw(
                operation_id, application_id, provider_submission_id
            )
        except (TimeoutError, ConnectionError) as exc:
            raise AmbiguousExternalActionError(
                "Sandbox withdrawal outcome requires provider reconciliation."
            ) from exc
        return {
            "application_id": receipt.application_id,
            "provider_submission_id": receipt.provider_submission_id,
            "provider_withdrawal_id": receipt.provider_withdrawal_id,
        }


class CandidateEmployerApplicationWithdrawalService:
    """Run a separately approved provider withdrawal and then update the local tracker.

    The operation is permanently bound to one candidate/application pair. A prior
    successful application submission must be present in the durable external-action
    history, and the provider independently verifies its submission ledger before
    recording any withdrawal effect.
    """

    _WITHDRAWABLE = frozenset({
        JobApplicationStatus.APPLIED,
        JobApplicationStatus.INTERVIEW,
        JobApplicationStatus.OFFER,
    })
    _IN_PROGRESS_GRACE = timedelta(minutes=5)

    def __init__(
        self,
        applications: JobApplicationRepository,
        operations: ExternalActionOperationRepository,
        provider: SQLiteSandboxApplicationWithdrawalProvider,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._applications = applications
        self._operations = operations
        self._provider = provider
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._external_actions = ExternalActionService(
            operations, _ApplicationWithdrawalActionAdapter(provider)
        )

    @staticmethod
    def operation_id_for(user_id: str, application_id: str) -> str:
        """Return the stable non-PII idempotency key for one candidate application."""
        user = CandidateEmployerApplicationWithdrawalService._identifier(user_id, "user_id")
        application = CandidateEmployerApplicationWithdrawalService._identifier(
            application_id, "application_id"
        )
        digest = sha256(f"{user}\0{application}".encode("utf-8")).hexdigest()
        return f"candidate-application-withdrawal:{digest}"

    def withdraw(
        self,
        application_id: str,
        *,
        user_id: str,
        expected_state_version: int,
    ) -> dict[str, object]:
        """Require explicit candidate approval, verify the provider effect, then CAS."""
        application_id = self._identifier(application_id, "application_id")
        user_id = self._identifier(user_id, "user_id")
        if (
            type(expected_state_version) is not int
            or not 1 <= expected_state_version <= (2**63 - 1)
        ):
            raise ValueError("expected_state_version must be a positive SQLite integer.")

        application = self._applications.get_owned(application_id, user_id=user_id)
        if application is None:
            raise KeyError("Application not found.")
        if application.version != expected_state_version:
            raise ApplicationConflictError("Application state has changed.")

        _submission_operation_id, provider_submission_id = self._confirmed_submission(application)
        operation_id = self.operation_id_for(user_id, application_id)
        payload = {
            "application_id": application_id,
            "provider_submission_id": provider_submission_id,
        }
        operation = self._operations.get(operation_id)

        if application.status is JobApplicationStatus.WITHDRAWN:
            if (
                operation_id not in application.external_action_operation_ids
                or operation is None
                or operation.action_type != "application.withdraw"
                or operation.status is not ExternalActionStatus.SUCCEEDED
            ):
                raise ApplicationConflictError(
                    "Application was withdrawn locally without a confirmed provider withdrawal."
                )
            self._require_confirmed_evidence(operation_id, application_id, provider_submission_id)
            return self._result(application)

        if application.status not in self._WITHDRAWABLE:
            raise ApplicationConflictError(
                "A provider withdrawal requires an already submitted application."
            )

        if operation is None:
            try:
                operation = self._external_actions.prepare(
                    operation_id, "application.withdraw", payload
                )
            except OperationConflictError as exc:
                raise ApplicationConflictError("Withdrawal operation conflicts with existing state.") from exc
        elif operation.action_type != "application.withdraw" or dict(operation.payload) != payload:
            raise ApplicationConflictError(
                "Withdrawal operation is already bound to a different application submission."
            )

        operation = self._execute_or_reconcile(
            operation, application_id, provider_submission_id
        )
        if operation.status is not ExternalActionStatus.SUCCEEDED:
            raise ApplicationConflictError(
                "Provider withdrawal is not yet confirmed; refresh and retry only after reconciliation."
            )
        self._require_confirmed_evidence(operation_id, application_id, provider_submission_id)

        occurred_at = self._clock()
        if (
            not isinstance(occurred_at, datetime)
            or occurred_at.tzinfo is None
            or occurred_at.utcoffset() is None
        ):
            raise ValueError("Withdrawal timestamp must be timezone-aware.")
        if occurred_at < application.updated_at:
            occurred_at = application.updated_at
        updated = application.transition(
            JobApplicationStatus.WITHDRAWN,
            occurred_at=occurred_at,
            operation_id=operation_id,
            note="Candidate approved employer-side withdrawal; the sandbox provider confirmed the effect.",
            metadata={
                "provider_kind": "sqlite_sandbox",
                "provider_withdrawal_confirmed": True,
            },
        )
        self._applications.update(updated, expected_version=expected_state_version)
        return self._result(updated)

    def _confirmed_submission(self, application: JobApplication) -> tuple[str, str]:
        matches: list[tuple[str, str]] = []
        for operation_id in application.external_action_operation_ids:
            operation = self._operations.get(operation_id)
            if (
                operation is None
                or operation.action_type != "application.submit"
                or operation.status is not ExternalActionStatus.SUCCEEDED
                or operation.result is None
                or operation.payload.get("application_id") != application.application_id
            ):
                continue
            provider_id = operation.result.get("provider_submission_id")
            if (
                not isinstance(provider_id, str)
                or not provider_id
                or provider_id != provider_id.strip()
                or len(provider_id) > 1_000
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in provider_id)
                or any(0xD800 <= ord(ch) <= 0xDFFF for ch in provider_id)
            ):
                raise ApplicationConflictError(
                    "The prior application submission has malformed provider evidence."
                )
            matches.append((operation_id, provider_id))
        if len(matches) != 1:
            raise ApplicationConflictError(
                "A unique, confirmed prior application submission is required."
            )
        return matches[0]

    def _execute_or_reconcile(
        self,
        operation: ExternalActionOperation,
        application_id: str,
        provider_submission_id: str,
    ) -> ExternalActionOperation:
        operation_id = operation.operation_id
        if operation.status is ExternalActionStatus.IN_PROGRESS:
            now = self._clock()
            if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("Withdrawal clock must return a timezone-aware datetime.")
            age = now - operation.updated_at
            if age < self._IN_PROGRESS_GRACE:
                raise ApplicationConflictError(
                    "A withdrawal attempt is still in progress; do not start another attempt."
                )
            operation = self._external_actions.execute(operation_id, human_approved=True)
        elif operation.status is ExternalActionStatus.FAILED:
            present, _ = self._evidence(operation_id, application_id, provider_submission_id)
            if present:
                raise ApplicationConflictError(
                    "Provider evidence conflicts with the failed withdrawal record."
                )
            operation = self._external_actions.reopen_failed(operation_id)
            operation = self._external_actions.execute(operation_id, human_approved=True)
        elif operation.status is ExternalActionStatus.RECONCILIATION_REQUIRED:
            present, result = self._evidence(operation_id, application_id, provider_submission_id)
            if present:
                operation = self._external_actions.resolve_reconciliation(
                    operation_id, confirmed_succeeded=True, result=result
                )
            else:
                operation = self._external_actions.resolve_reconciliation(
                    operation_id, confirmed_succeeded=False
                )
                operation = self._external_actions.execute(operation_id, human_approved=True)
        elif operation.status is ExternalActionStatus.PREPARED:
            operation = self._external_actions.execute(operation_id, human_approved=True)

        if operation.status is ExternalActionStatus.RECONCILIATION_REQUIRED:
            present, result = self._evidence(
                operation_id, application_id, provider_submission_id
            )
            if present:
                operation = self._external_actions.resolve_reconciliation(
                    operation_id, confirmed_succeeded=True, result=result
                )
            else:
                operation = self._external_actions.resolve_reconciliation(
                    operation_id, confirmed_succeeded=False
                )
                operation = self._external_actions.execute(operation_id, human_approved=True)
                if operation.status is ExternalActionStatus.RECONCILIATION_REQUIRED:
                    present, result = self._evidence(
                        operation_id, application_id, provider_submission_id
                    )
                    if present:
                        operation = self._external_actions.resolve_reconciliation(
                            operation_id, confirmed_succeeded=True, result=result
                        )

        if operation.status is ExternalActionStatus.FAILED:
            present, _ = self._evidence(operation_id, application_id, provider_submission_id)
            if present:
                raise ApplicationConflictError(
                    "Provider evidence conflicts with the failed withdrawal record."
                )
        if operation.status is ExternalActionStatus.SUCCEEDED:
            self._require_confirmed_evidence(
                operation_id, application_id, provider_submission_id
            )
        return operation

    def _evidence(
        self, operation_id: str, application_id: str, provider_submission_id: str
    ) -> tuple[bool, dict[str, str] | None]:
        try:
            evidence = self._provider.verify_withdrawal(operation_id)
        except Exception as exc:
            raise ApplicationConflictError(
                "Withdrawal provider evidence is temporarily unavailable."
            ) from exc
        if evidence.state is WithdrawalEvidenceState.ABSENT_CONFIRMED:
            return False, None
        if (
            evidence.application_id != application_id
            or evidence.provider_submission_id != provider_submission_id
            or not evidence.provider_withdrawal_id
        ):
            raise ApplicationConflictError(
                "Withdrawal provider evidence does not match the approved intent."
            )
        return True, {
            "application_id": application_id,
            "provider_submission_id": provider_submission_id,
            "provider_withdrawal_id": evidence.provider_withdrawal_id,
        }

    def _require_confirmed_evidence(
        self, operation_id: str, application_id: str, provider_submission_id: str
    ) -> None:
        present, _ = self._evidence(operation_id, application_id, provider_submission_id)
        if not present:
            raise ApplicationConflictError(
                "The durable withdrawal operation has no matching provider effect."
            )

    @staticmethod
    def _result(application: JobApplication) -> dict[str, object]:
        return {
            "application_id": application.application_id,
            "status": application.status.value,
            "state_version": application.version,
            "updated_at": application.updated_at.isoformat(),
            "provider_status": "confirmed",
        }

    @staticmethod
    def _identifier(value: object, name: str) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 200
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
            or any(0xD800 <= ord(ch) <= 0xDFFF for ch in value)
        ):
            raise ValueError(f"{name} must be a canonical non-empty identifier.")
        value.encode("utf-8", errors="strict")
        return value
