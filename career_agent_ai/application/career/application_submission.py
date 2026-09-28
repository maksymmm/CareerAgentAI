"""Crash-safe provider boundary for job application submission."""

from __future__ import annotations

from hashlib import sha256
from typing import Any, Mapping, Protocol

from career_agent_ai.application.external_actions import (
    AmbiguousExternalActionError,
    ExternalActionOperation,
    ExternalActionService,
    ExternalActionStatus,
)


def _validated_artifact(content: Any, digest: Any) -> tuple[str, str]:
    """Validate an immutable approved artifact and its SHA-256 digest."""
    if not isinstance(content, str):
        raise TypeError("artifact_content must be text.")
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized or len(normalized) > 100_000:
        raise ValueError("artifact_content must contain 1 to 100000 characters.")
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in normalized):
        raise ValueError("artifact_content contains forbidden control characters.")
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in normalized):
        raise ValueError("artifact_content contains a forbidden Unicode surrogate.")
    if not isinstance(digest, str):
        raise TypeError("artifact_sha256 must be text.")
    normalized_digest = digest.strip().lower()
    if (
        len(normalized_digest) != 64
        or any(ch not in "0123456789abcdef" for ch in normalized_digest)
        or sha256(normalized.encode("utf-8")).hexdigest() != normalized_digest
    ):
        raise ValueError("artifact_sha256 does not match artifact_content.")
    return normalized, normalized_digest


def _provider_result_identifier(
    value: Any, field: str, *, maximum: int = 200
) -> str:
    """Validate a provider-returned identifier before durable serialization."""
    if not isinstance(value, str):
        raise ValueError(f"Provider {field} must be text.")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"Provider {field} is malformed.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in normalized):
        raise ValueError(f"Provider {field} contains forbidden control characters.")
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in normalized):
        raise ValueError(f"Provider {field} contains a forbidden Unicode surrogate.")
    return normalized


class PreSubmissionError(RuntimeError):
    """Signal that submission failed before any external effect was attempted."""


class ApplicationSubmissionAdapter(Protocol):
    """Provider-neutral boundary for one consequential job application submission."""

    @property
    def is_dry_run(self) -> bool:
        """Return whether the adapter is guaranteed to avoid external side effects."""

    def submit(
        self,
        operation_id: str,
        job_id: str,
        application_id: str,
        artifact_content: str,
        artifact_sha256: str,
    ) -> Mapping[str, Any]:
        """Submit one exact approved artifact using operation_id as the idempotency key."""


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
        artifact_content, artifact_digest = _validated_artifact(
            payload.get("artifact_content"),
            payload.get("artifact_sha256"),
        )
        if not job_id or not application_id:
            raise ValueError("Prepared application submission identifiers are malformed.")
        try:
            result = self._provider.submit(
                operation_id,
                job_id,
                application_id,
                artifact_content,
                artifact_digest,
            )
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
        try:
            returned_application = _provider_result_identifier(
                result.get("application_id"), "application_id"
            )
            returned_job = _provider_result_identifier(result.get("job_id"), "job_id")
            returned_digest = _provider_result_identifier(
                result.get("artifact_sha256"), "artifact_sha256"
            ).lower()
            provider_submission_id = _provider_result_identifier(
                result.get("provider_submission_id"),
                "provider_submission_id",
                maximum=1_000,
            )
        except ValueError as exc:
            raise AmbiguousExternalActionError(
                "Application provider returned malformed submission data."
            ) from exc
        if (
            returned_application != application_id
            or returned_job != job_id
            or returned_digest != artifact_digest
        ):
            raise AmbiguousExternalActionError(
                "Application provider result does not match the prepared intent."
            )
        # Persist only the validated, JSON-safe contract. Provider-specific extras
        # may contain arbitrary objects and are deliberately excluded.
        return {
            "job_id": returned_job,
            "application_id": returned_application,
            "provider_submission_id": provider_submission_id,
            "artifact_sha256": returned_digest,
        }


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
        artifact_content: str,
        artifact_sha256: str,
        human_approved: bool,
    ) -> Mapping[str, Any] | None:
        """Submit one exact approved application artifact after explicit human approval."""
        operation_id = self._identifier(operation_id, "operation_id")
        job_id = self._identifier(job_id, "job_id")
        application_id = self._identifier(application_id, "application_id")
        artifact_content, artifact_sha256 = _validated_artifact(
            artifact_content, artifact_sha256
        )
        payload = {
            "job_id": job_id,
            "application_id": application_id,
            "artifact_content": artifact_content,
            "artifact_sha256": artifact_sha256,
        }
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

    def resolve_reconciliation(
        self,
        operation_id: str,
        *,
        submitted: bool,
        provider_submission_id: str | None = None,
    ) -> ExternalActionOperation:
        """Resolve an ambiguous submission after explicit provider-side verification."""
        operation_id = self._identifier(operation_id, "operation_id")
        if not isinstance(submitted, bool):
            raise TypeError("submitted must be a boolean.")
        operation = self._external_actions.get(operation_id)
        if operation is None:
            raise KeyError(f"Unknown application submission operation: {operation_id!r}")
        if operation.action_type != "application.submit":
            raise ValueError("Operation is not an application submission.")
        if submitted and operation.status == ExternalActionStatus.SUCCEEDED:
            if operation.result is None:
                raise ValueError("Resolved submission has no durable result.")
            expected_provider = _provider_result_identifier(
                provider_submission_id,
                "provider_submission_id",
                maximum=1_000,
            )
            if operation.result.get("provider_submission_id") != expected_provider:
                raise ValueError(
                    "Resolved submission does not match provider_submission_id."
                )
            return operation
        if not submitted and operation.status == ExternalActionStatus.PREPARED:
            if provider_submission_id is not None:
                raise ValueError(
                    "provider_submission_id must be omitted when no submission occurred."
                )
            return operation
        if operation.status != ExternalActionStatus.RECONCILIATION_REQUIRED:
            raise ValueError("Application submission is not awaiting reconciliation.")
        if submitted:
            payload = dict(operation.payload)
            artifact_content, artifact_digest = _validated_artifact(
                payload.get("artifact_content"),
                payload.get("artifact_sha256"),
            )
            del artifact_content
            job_id = self._identifier(payload.get("job_id"), "job_id")
            application_id = self._identifier(
                payload.get("application_id"), "application_id"
            )
            provider_id = _provider_result_identifier(
                provider_submission_id,
                "provider_submission_id",
                maximum=1_000,
            )
            return self._external_actions.resolve_reconciliation(
                operation_id,
                confirmed_succeeded=True,
                result={
                    "job_id": job_id,
                    "application_id": application_id,
                    "provider_submission_id": provider_id,
                    "artifact_sha256": artifact_digest,
                },
            )
        if provider_submission_id is not None:
            raise ValueError(
                "provider_submission_id must be omitted when no submission occurred."
            )
        return self._external_actions.resolve_reconciliation(
            operation_id,
            confirmed_succeeded=False,
        )

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
        self.artifacts: dict[str, tuple[str, str]] = {}
        self._results: dict[str, Mapping[str, Any]] = {}
        self.failure: Exception | None = None

    @property
    def is_dry_run(self) -> bool:
        return True

    def submit(
        self,
        operation_id: str,
        job_id: str,
        application_id: str,
        artifact_content: str,
        artifact_sha256: str,
    ) -> Mapping[str, Any]:
        existing = self._results.get(operation_id)
        if existing is not None:
            return existing
        self.calls.append((operation_id, job_id, application_id))
        self.artifacts[operation_id] = (artifact_content, artifact_sha256)
        if self.failure is not None:
            raise PreSubmissionError(str(self.failure)) from self.failure
        result = {
            "job_id": job_id,
            "application_id": application_id,
            "provider_submission_id": f"fake:{application_id}",
            "artifact_sha256": artifact_sha256,
        }
        self._results[operation_id] = result
        return result
