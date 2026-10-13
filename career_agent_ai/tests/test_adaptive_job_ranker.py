from datetime import datetime, timezone

import pytest

from career_agent_ai.application.jobs import Company, Job, Location
from career_agent_ai.application.jobs.adaptive_job_ranker import AdaptiveJobRanker
from career_agent_ai.application.jobs.in_memory_job_application_repository import (
    InMemoryJobApplicationRepository,
)
from career_agent_ai.application.jobs.job_application import JobApplication
from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus
from career_agent_ai.application.jobs.job_filter import JobFilter


def _job(job_id: str, company_id: str) -> Job:
    return Job.create(
        job_id=job_id,
        title="Python Engineer",
        company=Company(company_id=company_id, name=company_id),
        location=Location(country="Germany", city="Berlin"),
        description="Python backend services",
    )


def _application(
    application_id: str,
    user_id: str,
    company_id: str,
    outcomes: tuple[JobApplicationStatus, ...],
) -> JobApplication:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = JobApplication(
        application_id=application_id,
        user_id=user_id,
        job_id=f"historic-{application_id}",
        company_id=company_id,
        status=JobApplicationStatus.SAVED,
        created_at=now,
        updated_at=now,
    )
    for index, status in enumerate(outcomes):
        result = result.transition(status, occurred_at=now, event_id=f"{application_id}-{index}")
    return result


def test_interview_outcomes_break_equal_relevance_tie_with_explanation():
    repository = InMemoryJobApplicationRepository()
    repository.add(_application("a1", "candidate", "preferred", (JobApplicationStatus.APPLIED, JobApplicationStatus.INTERVIEW)))

    ranked = AdaptiveJobRanker(repository).rank(
        "candidate",
        (_job("other", "other"), _job("preferred", "preferred")),
        JobFilter(keyword="Python"),
    )

    assert [item.job.job_id for item in ranked] == ["preferred", "other"]
    assert ranked[0].outcome_adjustment == 1.0
    assert ranked[0].evidence_count == 1
    assert "bounded evidence" in ranked[0].reason
    assert ranked[1].outcome_adjustment == 0.0


def test_rejection_adjustment_is_bounded_and_sparse_evidence_is_shrunk():
    repository = InMemoryJobApplicationRepository()
    for index in range(20):
        repository.add(_application(f"r{index}", "candidate", "company", (JobApplicationStatus.APPLIED, JobApplicationStatus.REJECTED)))

    result = AdaptiveJobRanker(repository).rank("candidate", (_job("job", "company"),))[0]

    assert -AdaptiveJobRanker.MAX_ADJUSTMENT <= result.outcome_adjustment < 0
    assert result.outcome_adjustment == pytest.approx(-1.73913)


def test_interview_then_rejection_retains_positive_funnel_signal():
    repository = InMemoryJobApplicationRepository()
    repository.add(_application("a1", "candidate", "company", (JobApplicationStatus.APPLIED, JobApplicationStatus.INTERVIEW, JobApplicationStatus.REJECTED)))

    result = AdaptiveJobRanker(repository).rank("candidate", (_job("job", "company"),))[0]

    assert result.outcome_adjustment == 1.0


def test_history_is_tenant_isolated_and_non_outcomes_remain_neutral():
    repository = InMemoryJobApplicationRepository()
    repository.add(
        _application(
            "foreign",
            "other-user",
            "company",
            (
                JobApplicationStatus.APPLIED,
                JobApplicationStatus.INTERVIEW,
                JobApplicationStatus.OFFER,
            ),
        )
    )
    repository.add(_application("saved", "candidate", "company", ()))

    result = AdaptiveJobRanker(repository).rank("candidate", (_job("job", "company"),))[0]

    assert result.evidence_count == 0
    assert result.outcome_adjustment == 0.0


@pytest.mark.parametrize("user_id", ["", "   ", None, 7])
def test_invalid_candidate_identity_is_rejected(user_id):
    with pytest.raises(ValueError, match="user_id"):
        AdaptiveJobRanker(InMemoryJobApplicationRepository()).rank(user_id, (_job("job", "company"),))
