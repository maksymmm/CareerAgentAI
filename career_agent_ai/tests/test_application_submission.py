from __future__ import annotations

from hashlib import sha256

import pytest

from career_agent_ai.application.career.application_submission import (
    ApplicationSubmissionService,
    FakeApplicationSubmissionAdapter,
)
from career_agent_ai.application.external_actions import (
    ExternalActionService,
    ExternalActionStatus,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import (
    SQLiteExternalActionOperationRepository,
)


def test_submission_rejects_non_boolean_approval_before_preparing_operation():
    artifact = "Experienced logistics professional."
    digest = sha256(artifact.encode("utf-8")).hexdigest()
    database = SQLiteDatabase()
    operations = SQLiteExternalActionOperationRepository(database)
    provider = FakeApplicationSubmissionAdapter()
    external = ExternalActionService(
        operations,
        ApplicationSubmissionService.action_adapter(provider),
    )
    service = ApplicationSubmissionService(provider, external)

    with pytest.raises(TypeError, match="boolean"):
        service.submit(
            "submit:non-bool",
            job_id="job-1",
            application_id="application-1",
            artifact_content=artifact,
            artifact_sha256=digest,
            candidate_snapshot_sha256="a" * 64,
            human_approved="false",
        )

    assert operations.get("submit:non-bool") is None
    assert provider.calls == []


def test_submission_persists_and_returns_candidate_snapshot_binding():
    artifact = "Experienced logistics professional."
    artifact_digest = sha256(artifact.encode("utf-8")).hexdigest()
    snapshot_digest = sha256(b"candidate-job-snapshot").hexdigest()
    database = SQLiteDatabase()
    operations = SQLiteExternalActionOperationRepository(database)
    provider = FakeApplicationSubmissionAdapter()
    external = ExternalActionService(
        operations,
        ApplicationSubmissionService.action_adapter(provider),
    )
    service = ApplicationSubmissionService(provider, external)

    result = service.submit(
        "submit:snapshot-bound",
        job_id="job-1",
        application_id="application-1",
        artifact_content=artifact,
        artifact_sha256=artifact_digest,
        candidate_snapshot_sha256=snapshot_digest,
        human_approved=True,
    )

    operation = operations.get("submit:snapshot-bound")
    assert operation is not None
    assert operation.payload["candidate_snapshot_sha256"] == snapshot_digest
    assert operation.result is not None
    assert operation.result["candidate_snapshot_sha256"] == snapshot_digest
    assert result is not None
    assert result["candidate_snapshot_sha256"] == snapshot_digest
    assert provider.candidate_snapshots["submit:snapshot-bound"] == snapshot_digest


def test_submission_replay_rejects_a_different_candidate_snapshot():
    artifact = "Experienced logistics professional."
    artifact_digest = sha256(artifact.encode("utf-8")).hexdigest()
    approved_snapshot = sha256(b"approved-candidate-job-snapshot").hexdigest()
    different_snapshot = sha256(b"different-candidate-job-snapshot").hexdigest()
    database = SQLiteDatabase()
    operations = SQLiteExternalActionOperationRepository(database)
    provider = FakeApplicationSubmissionAdapter()
    external = ExternalActionService(
        operations,
        ApplicationSubmissionService.action_adapter(provider),
    )
    service = ApplicationSubmissionService(provider, external)

    first = service.submit(
        "submit:stable-key",
        job_id="job-1",
        application_id="application-1",
        artifact_content=artifact,
        artifact_sha256=artifact_digest,
        candidate_snapshot_sha256=approved_snapshot,
        human_approved=True,
    )
    assert first is not None

    with pytest.raises(ValueError, match="bound to another submission intent"):
        service.submit(
            "submit:stable-key",
            job_id="job-1",
            application_id="application-1",
            artifact_content=artifact,
            artifact_sha256=artifact_digest,
            candidate_snapshot_sha256=different_snapshot,
            human_approved=True,
        )

    assert provider.calls == [
        ("submit:stable-key", "job-1", "application-1")
    ]


def test_legacy_prepared_intent_is_atomically_bound_before_provider_execution():
    artifact = "Experienced logistics professional."
    artifact_digest = sha256(artifact.encode("utf-8")).hexdigest()
    snapshot_digest = sha256(b"approved-candidate-job-snapshot").hexdigest()
    database = SQLiteDatabase()
    operations = SQLiteExternalActionOperationRepository(database)
    provider = FakeApplicationSubmissionAdapter()
    external = ExternalActionService(
        operations,
        ApplicationSubmissionService.action_adapter(provider),
    )
    service = ApplicationSubmissionService(provider, external)
    external.prepare(
        "submit:legacy-prepared",
        "application.submit",
        {
            "job_id": "job-1",
            "application_id": "application-1",
            "artifact_content": artifact,
            "artifact_sha256": artifact_digest,
        },
    )

    result = service.submit(
        "submit:legacy-prepared",
        job_id="job-1",
        application_id="application-1",
        artifact_content=artifact,
        artifact_sha256=artifact_digest,
        candidate_snapshot_sha256=snapshot_digest,
        human_approved=True,
    )

    operation = operations.get("submit:legacy-prepared")
    assert result is not None
    assert operation is not None
    assert operation.status == ExternalActionStatus.SUCCEEDED
    assert operation.payload["candidate_snapshot_sha256"] == snapshot_digest
    assert provider.calls == [
        ("submit:legacy-prepared", "job-1", "application-1")
    ]


def test_non_text_provider_snapshot_digest_requires_reconciliation():
    class MalformedResultProvider(FakeApplicationSubmissionAdapter):
        def submit(
            self,
            operation_id,
            job_id,
            application_id,
            artifact_content,
            artifact_sha256,
            candidate_snapshot_sha256,
        ):
            result = dict(
                super().submit(
                    operation_id,
                    job_id,
                    application_id,
                    artifact_content,
                    artifact_sha256,
                    candidate_snapshot_sha256,
                )
            )
            result["candidate_snapshot_sha256"] = None
            return result

    artifact = "Experienced logistics professional."
    artifact_digest = sha256(artifact.encode("utf-8")).hexdigest()
    snapshot_digest = sha256(b"approved-candidate-job-snapshot").hexdigest()
    database = SQLiteDatabase()
    operations = SQLiteExternalActionOperationRepository(database)
    provider = MalformedResultProvider()
    external = ExternalActionService(
        operations,
        ApplicationSubmissionService.action_adapter(provider),
    )
    service = ApplicationSubmissionService(provider, external)

    result = service.submit(
        "submit:malformed-provider-result",
        job_id="job-1",
        application_id="application-1",
        artifact_content=artifact,
        artifact_sha256=artifact_digest,
        candidate_snapshot_sha256=snapshot_digest,
        human_approved=True,
    )

    operation = operations.get("submit:malformed-provider-result")
    assert result is None
    assert operation is not None
    assert operation.status == ExternalActionStatus.RECONCILIATION_REQUIRED
