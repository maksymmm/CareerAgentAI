"""Crash-safe, human-gated job-application submission use cases."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from career_agent_ai.application.external_actions import (
    AmbiguousExternalActionError,
    ExternalActionService,
    ExternalActionStatus,
)

from .application_submission_adapter import (
    ApplicationSubmissionAdapter,
    ApplicationSubmissionReceipt,
    PreApplicationSubmissionError,
    validate_submission_identifier,
)
from .application_submission_claim_repository import (
    ApplicationSubmissionClaimRepository,
)


class _ApplicationSubmissionActionAdapter:
    def __init__(
        self,
        provider: ApplicationSubmissionAdapter,
        claims: ApplicationSubmissionClaimRepository,
    ) -> None:
        self._provider = provider
        self._claims = claims

    def execute(
        self,
        operation_id: str,
        action_type: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if action_type != "application.submit":
            raise ValueError("Unsupported application submission action type.")
        application_id = validate_submission_identifier(
            str(payload["application_id"]), "application_id"
        )
        job_id = validate_submission_identifier(str(payload["job_id"]), "job_id")
        resume_id = validate_submission_identifier(
            str(payload["resume_id"]), "resume_id"
        )
        self._claims.claim(application_id, operation_id)
        try:
            receipt = self._provider.submit(
                operation_id,
                application_id,
                job_id,
                resume_id,
            )
        except PreApplicationSubmissionError:
            self._release_after_definite_failure(application_id, operation_id)
            raise
        except Exception as exc:
            raise AmbiguousExternalActionError(
                "Application provider outcome is uncertain and requires reconciliation."
            ) from exc

        try:
            self._validate_receipt(receipt, application_id, job_id)
            self._claims.complete(application_id, operation_id)
        except Exception as exc:
            raise AmbiguousExternalActionError(
                "Application may have been submitted, but its outcome could not "
                "be validated or persisted."
            ) from exc
        return {
            "application_id": receipt.application_id,
            "job_id": receipt.job_id,
            "provider_submission_id": receipt.provider_submission_id,
            "submitted_at": receipt.submitted_at.isoformat(),
        }

    def _release_after_definite_failure(
        self, application_id: str, operation_id: str
    ) -> None:
        try:
            self._claims.release(application_id, operation_id)
        except Exception as exc:
            raise AmbiguousExternalActionError(
                "Application was not submitted, but local claim cleanup could not "
                "be confirmed."
            ) from exc

    @staticmethod
    def _validate_receipt(
        receipt: ApplicationSubmissionReceipt,
        application_id: str,
        job_id: str,
    ) -> None:
        if not isinstance(receipt, ApplicationSubmissionReceipt):
            raise ValueError("Application provider returned malformed receipt data.")
        if receipt.application_id != application_id or receipt.job_id != job_id:
            raise ValueError(
                "Application provider receipt does not match the prepared intent."
            )


class ApplicationSubmissionService:
    """Coordinate one externally submitted application with human approval."""

    def __init__(
        self,
        provider: ApplicationSubmissionAdapter,
        claims: ApplicationSubmissionClaimRepository,
        external_actions: ExternalActionService,
    ) -> None:
        self._provider = provider
        self._claims = claims
        self._external_actions = external_actions

    @staticmethod
    def action_adapter(
        provider: ApplicationSubmissionAdapter,
        claims: ApplicationSubmissionClaimRepository,
    ) -> _ApplicationSubmissionActionAdapter:
        """Build the crash-safe external-action adapter bridge."""
        return _ApplicationSubmissionActionAdapter(provider, claims)

    def submit(
        self,
        operation_id: str,
        application_id: str,
        job_id: str,
        resume_id: str,
        *,
        human_approved: bool,
    ) -> ApplicationSubmissionReceipt | None:
        """Submit one stable intent once after explicit human approval."""
        operation_id = validate_submission_identifier(operation_id, "operation_id")
        application_id = validate_submission_identifier(
            application_id, "application_id"
        )
        job_id = validate_submission_identifier(job_id, "job_id")
        resume_id = validate_submission_identifier(resume_id, "resume_id")
        self._external_actions.prepare(
            operation_id,
            "application.submit",
            {
                "application_id": application_id,
                "job_id": job_id,
                "resume_id": resume_id,
            },
        )
        operation = self._external_actions.execute(
            operation_id, human_approved=human_approved
        )
        if operation.status != ExternalActionStatus.SUCCEEDED:
            return None
        if operation.result is None:
            raise ValueError("Successful application submission has no result.")
        try:
            receipt = ApplicationSubmissionReceipt(
                application_id=str(operation.result["application_id"]),
                job_id=str(operation.result["job_id"]),
                provider_submission_id=str(
                    operation.result["provider_submission_id"]
                ),
                submitted_at=datetime.fromisoformat(
                    str(operation.result["submitted_at"])
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "Persisted application submission result is malformed."
            ) from exc
        if receipt.application_id != application_id or receipt.job_id != job_id:
            raise ValueError(
                "Persisted application submission result does not match intent."
            )
        return receipt
