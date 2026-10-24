from __future__ import annotations

from hashlib import sha256

import pytest

from career_agent_ai.application.career.application_submission import (
    ApplicationSubmissionService,
    FakeApplicationSubmissionAdapter,
)
from career_agent_ai.application.external_actions import ExternalActionService
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
            human_approved="false",
        )

    assert operations.get("submit:non-bool") is None
    assert provider.calls == []
