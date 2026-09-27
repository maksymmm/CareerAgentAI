"""Non-consequential provider boundaries used by the autonomous career loop."""

from __future__ import annotations

from typing import Protocol

from .autonomous_loop_models import LoopJobSnapshot, PreparedResume


class ResumePreparationAdapter(Protocol):
    """Prepare resume material for one selected job without submitting it."""

    def prepare(
        self,
        resume_id: str,
        user_id: str,
        job: LoopJobSnapshot,
    ) -> PreparedResume:
        """Prepare or replay stable resume material for resume_id."""


class EmployerContactResolver(Protocol):
    """Resolve a validated employer/recruiter communication recipient."""

    def resolve(self, job: LoopJobSnapshot) -> str:
        """Return a provider-neutral recipient identifier for the selected job."""


class FakeResumePreparationAdapter:
    """Deterministic in-memory resume preparer for integration tests."""

    def __init__(self) -> None:
        self._prepared: dict[str, PreparedResume] = {}
        self.calls: list[tuple[str, str]] = []

    def prepare(
        self,
        resume_id: str,
        user_id: str,
        job: LoopJobSnapshot,
    ) -> PreparedResume:
        existing = self._prepared.get(resume_id)
        if existing is not None:
            if existing.job_id != job.job_id:
                raise ValueError("resume_id is already bound to another job.")
            return existing
        self.calls.append((resume_id, job.job_id))
        prepared = PreparedResume(
            resume_id=resume_id,
            job_id=job.job_id,
            summary=(
                f"Prepared resume for {job.title} at {job.company_name} "
                f"for candidate {user_id}."
            ),
        )
        self._prepared[resume_id] = prepared
        return prepared


class FakeEmployerContactResolver:
    """Deterministic resolver that never contacts an external service."""

    def __init__(
        self,
        contacts: dict[str, str] | None = None,
        *,
        default_recipient: str = "recruiter@example.test",
    ) -> None:
        self._contacts = dict(contacts or {})
        self._default = default_recipient
        self.calls: list[str] = []

    def resolve(self, job: LoopJobSnapshot) -> str:
        self.calls.append(job.job_id)
        return self._contacts.get(job.job_id, self._default)
