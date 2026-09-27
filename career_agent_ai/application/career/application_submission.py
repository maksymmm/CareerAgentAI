"""Crash-safe provider boundary for job application submission."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from career_agent_ai.application.external_actions import (
    AmbiguousExternalActionError,
    ExternalActionOperation,
    ExternalActionService,
    ExternalActionStatus,
)


class PreSubmissionError(RuntimeError):
    """Signal that submission failed before any external effect was attempted."""


class ApplicationSubmissionAdapter(Protocol):
    """Provider-neutral boundary for one consequential job application submission."""

    @property
    def is_dry_run(self) -> bool:
        """Return whether the adapter is guaranteed to avoid external side effects."""

    def submit(
        self, operation_id: str, job_id: str, application_id: str
    ) -> Mapping[str, Any]:
        """Submit once using operation_id as the provider idempotency key."""


class _SubmissionActionAdapter:
    def __init__(self, provider: ApplicationSubmissionAdapter) -> None:
        self._provider = provider

    def execute(
        self, operation_id: str, action_type: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if action_type != "application.submit":
            raise ValueError("Unsupported application submission action type.")
        job_id = str(payload["job_id"]).strip()
        application_id = str(payload["application_id"]).strip()
        if not job_id or not application_id:
            raise ValueError("Prepared application submission identifiers are malformed.")
        try:
            result = self._provider.submit(operation_id, job_id, application_id)
        except PreSubmissionError:
            raise
        except Exception as exc:
            raise AmbiguousExternalActionError(
                "Application provider outcome is uncertain and requires reconciliation."
            ) from exc
        if not isinstance(result, Mapping):
            raise AmbiguousExternalActionError(
                "Application provider returned malformed submission data."
            )
        returned_application = str(result.get("application_id", "")).strip()
        returned_job = str(result.get("job_id", "")).strip()
        if returned_application != application_id or returned_job != job_id:
            raise AmbiguousExternalActionError(
                "Application provider result does not match the prepared intent."
            )
        return dict(result)


class ApplicationSubmissionService:
    """Prepare and execute human-approved, idempotent application submissions."""

    def __init__(
        self,
        provider: ApplicationSubmissionAdapter,
        external_actions: ExternalActionService,
    ) -> None:
        self._provider = provider
        self._external_actions = external_actions

    @staticmethod
    def action_adapter(provider: ApplicationSubmissionAdapter) -> _SubmissionActionAdapter:
        """Build the external-action bridge for application submissions."""
        return _SubmissionActionAdapter(provider)

    def get_operation(self, operation_id: str) -> ExternalActionOperation | None:
        """Return durable submission-operation state without executing a provider call."""
        operation_id = self._identifier(operation_id, "operation_id")
        return self._external_actions.get(operation_id)

    def submit(
        self,
        operation_id: str,
        *,
        job_id: str,
        application_id: str,
        human_approved: bool,
    ) -> Mapping[str, Any] | None:
        """Submit one exact application intent after explicit human approval."""
        operation_id = self._identifier(operation_id, "operation_id")
        job_id = self._identifier(job_id, "job_id")
        application_id = self._identifier(application_id, "application_id")
        payload = {"job_id": job_id, "application_id": application_id}
        existing = self._external_actions.get(operation_id)
        if existing is not None:
            if existing.action_type != "application.submit" or dict(existing.payload) != payload:
                raise ValueError("operation_id is already bound to another submission intent.")
        else:
            self._external_actions.prepare(operation_id, "application.submit", payload)
        operation = self._external_actions.execute(
            operation_id, human_approved=human_approved
        )
        if operation.status != ExternalActionStatus.SUCCEEDED:
            return None
        if operation.result is None:
            raise ValueError("Successful application submission has no durable result.")
        return dict(operation.result)

    @staticmethod
    def _identifier(value: str, field: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{field} must be text.")
        normalized = value.strip()
        if not normalized or len(normalized) > 200:
            raise ValueError(f"{field} must be a non-empty string of at most 200 characters.")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in normalized):
            raise ValueError(f"{field} contains forbidden control characters.")
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in normalized):
            raise ValueError(f"{field} contains a forbidden Unicode surrogate.")
        return normalized


class FakeApplicationSubmissionAdapter:
    """Deterministic no-I/O submission provider for tests and local development."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self._results: dict[str, Mapping[str, Any]] = {}
        self.failure: Exception | None = None

    @property
    def is_dry_run(self) -> bool:
        return True

    def submit(
        self, operation_id: str, job_id: str, application_id: str
    ) -> Mapping[str, Any]:
        existing = self._results.get(operation_id)
        if existing is not None:
            return existing
        self.calls.append((operation_id, job_id, application_id))
        if self.failure is not None:
            raise PreSubmissionError(str(self.failure)) from self.failure
        result = {
            "job_id": job_id,
            "application_id": application_id,
            "provider_submission_id": f"fake:{application_id}",
        }
        self._results[operation_id] = result
        return result
