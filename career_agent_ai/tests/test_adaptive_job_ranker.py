from datetime import datetime, timezone

import pytest

from career_agent_ai.application.jobs import Company, Job, Location, Salary
from career_agent_ai.application.jobs.adaptive_job_ranker import AdaptiveJobRanker
from career_agent_ai.application.jobs.in_memory_job_application_repository import (
    InMemoryJobApplicationRepository,
)
from career_agent_ai.application.jobs.job_application import JobApplication
from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus
from career_agent_ai.application.jobs.job_filter import JobFilter


def _job(job_id: str, company_id: str, salary: int | None = None) -> Job:
    return Job.create(
        job_id=job_id,
        title="Python Engineer",
        company=Company(company_id=company_id, name=company_id),
        location=Location(country="Germany", city="Berlin"),
        description="Python backend services",
        salary=Salary(minimum=salary, maximum=salary) if salary is not None else None,
    )


def _application(
    application_id: str,
    user_id: str,
    company_id: str,
    outcomes: tuple[JobApplicationStatus, ...],
    *,
    job_title: str = "",
    job_skills: tuple[str, ...] = (),
) -> JobApplication:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = JobApplication(
        application_id=application_id,
        user_id=user_id,
        job_id=f"historic-{application_id}",
        company_id=company_id,
        job_title=job_title,
        job_skills=job_skills,
        status=JobApplicationStatus.SAVED,
        created_at=now,
        updated_at=now,
    )
    for index, status in enumerate(outcomes):
        result = result.transition(status, occurred_at=now, event_id=f"{application_id}-{index}")
    return result


def test_attribute_terms_drop_tokens_too_large_for_durable_job_skills():
    oversized = "x" * 101

    assert AdaptiveJobRanker.attribute_terms(f"Logistics {oversized} python") == (
        "logistics",
        "python",
    )


def test_interview_outcomes_break_equal_relevance_tie_with_explanation():
    repository = InMemoryJobApplicationRepository()
    repository.add(_application("a1", "candidate", "preferred", (JobApplicationStatus.APPLIED, JobApplicationStatus.INTERVIEW)))

    ranked = AdaptiveJobRanker(repository).rank(
        "candidate",
        (_job("other", "other"), _job("preferred", "preferred")),
        JobFilter(keyword="Python"),
    )

    assert [item.job.job_id for item in ranked] == ["preferred", "other"]
    assert 0 < ranked[0].outcome_adjustment < ranked[0].relevance_score * 0.1
    assert ranked[0].evidence_count == 1
    assert "bounded evidence" in ranked[0].reason
    assert ranked[1].outcome_adjustment == 0.0


def test_rejection_adjustment_is_bounded_and_sparse_evidence_is_shrunk():
    repository = InMemoryJobApplicationRepository()
    for index in range(20):
        repository.add(_application(f"r{index}", "candidate", "company", (JobApplicationStatus.APPLIED, JobApplicationStatus.REJECTED)))

    result = AdaptiveJobRanker(repository).rank("candidate", (_job("job", "company"),))[0]

    assert -AdaptiveJobRanker.MAX_ADJUSTMENT <= result.outcome_adjustment < 0
    assert result.outcome_adjustment == pytest.approx(-0.002174)


def test_interview_then_rejection_retains_positive_funnel_signal():
    repository = InMemoryJobApplicationRepository()
    repository.add(_application("a1", "candidate", "company", (JobApplicationStatus.APPLIED, JobApplicationStatus.INTERVIEW, JobApplicationStatus.REJECTED)))

    result = AdaptiveJobRanker(repository).rank("candidate", (_job("job", "company"),))[0]

    assert result.outcome_adjustment == pytest.approx(0.00125)


def test_interview_then_withdrawal_is_neutral():
    repository = InMemoryJobApplicationRepository()
    repository.add(
        _application(
            "a1",
            "candidate",
            "company",
            (
                JobApplicationStatus.APPLIED,
                JobApplicationStatus.INTERVIEW,
                JobApplicationStatus.WITHDRAWN,
            ),
        )
    )

    result = AdaptiveJobRanker(repository).rank("candidate", (_job("job", "company"),))[0]

    assert result.evidence_count == 0
    assert result.outcome_adjustment == 0.0


def test_legacy_scale_history_cannot_overwhelm_large_relevance_gap():
    repository = InMemoryJobApplicationRepository()
    for index in range(20):
        repository.add(
            _application(
                f"offer-{index}",
                "candidate",
                "low-salary",
                (
                    JobApplicationStatus.APPLIED,
                    JobApplicationStatus.INTERVIEW,
                    JobApplicationStatus.OFFER,
                ),
            )
        )

    ranked = AdaptiveJobRanker(repository).rank(
        "candidate",
        (_job("high", "new-company", 200_000), _job("low", "low-salary", 30_000)),
    )

    assert [item.job.job_id for item in ranked] == ["high", "low"]
    assert ranked[1].outcome_adjustment <= ranked[1].relevance_score * 0.1


def test_adaptive_tie_preserves_baseline_salary_order():
    repository = InMemoryJobApplicationRepository()
    ranked = AdaptiveJobRanker(repository).rank(
        "candidate",
        (_job("z-low", "one", 40_000), _job("a-high", "two", 90_000)),
        JobFilter(keyword="Python"),
    )

    assert [item.job.job_id for item in ranked] == ["a-high", "z-low"]


def test_dense_evidence_has_more_influence_than_sparse_evidence_below_cap():
    repository = InMemoryJobApplicationRepository()
    repository.add(
        _application(
            "sparse",
            "candidate",
            "sparse-company",
            (JobApplicationStatus.APPLIED, JobApplicationStatus.INTERVIEW),
        )
    )
    for index in range(8):
        repository.add(
            _application(
                f"dense-{index}",
                "candidate",
                "dense-company",
                (JobApplicationStatus.APPLIED, JobApplicationStatus.INTERVIEW),
            )
        )

    ranked = AdaptiveJobRanker(repository).rank(
        "candidate",
        (_job("sparse", "sparse-company", 100_000), _job("dense", "dense-company", 100_000)),
    )
    by_id = {item.job.job_id: item for item in ranked}

    assert by_id["dense"].outcome_adjustment > by_id["sparse"].outcome_adjustment > 0


@pytest.mark.parametrize(
    "placeholder",
    ["unknown", "arbeitnow:unknown company", "provider:N/A", "not disclosed"],
)
def test_placeholder_company_ids_never_share_outcome_evidence(placeholder):
    repository = InMemoryJobApplicationRepository()
    repository.add(
        _application(
            "anonymous",
            "candidate",
            placeholder,
            (JobApplicationStatus.APPLIED, JobApplicationStatus.INTERVIEW),
        )
    )

    result = AdaptiveJobRanker(repository).rank(
        "candidate", (_job("anonymous-job", placeholder, 100_000),)
    )[0]

    assert result.evidence_count == 0
    assert result.outcome_adjustment == 0.0


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


def test_repeated_role_and_skill_outcomes_transfer_across_employers():
    repository = InMemoryJobApplicationRepository()
    for index in range(2):
        repository.add(
            _application(
                f"python-{index}",
                "candidate",
                f"historic-{index}",
                (JobApplicationStatus.APPLIED, JobApplicationStatus.INTERVIEW),
                job_title="Backend Engineer",
                job_skills=("python",),
            )
        )

    result = AdaptiveJobRanker(repository).rank(
        "candidate",
        (_job("new", "new-company"),),
        JobFilter(keyword="Python backend"),
    )[0]

    assert result.evidence_count == 2
    assert result.outcome_adjustment > 0
    assert "role, or skill" in result.reason


def test_single_attribute_outcome_is_ignored_to_limit_feedback_bias():
    repository = InMemoryJobApplicationRepository()
    repository.add(
        _application(
            "single",
            "candidate",
            "historic",
            (
                JobApplicationStatus.APPLIED,
                JobApplicationStatus.INTERVIEW,
                JobApplicationStatus.OFFER,
            ),
            job_title="Python Engineer",
            job_skills=("python",),
        )
    )

    result = AdaptiveJobRanker(repository).rank(
        "candidate", (_job("new", "new-company"),), JobFilter(keyword="Python")
    )[0]

    assert result.evidence_count == 0
    assert result.outcome_adjustment > 0
    assert "exploration" in result.reason


def test_unseen_attributes_receive_only_small_bounded_exploration():
    repository = InMemoryJobApplicationRepository()
    for index in range(2):
        repository.add(
            _application(
                f"java-{index}", "candidate", f"historic-{index}",
                (JobApplicationStatus.APPLIED, JobApplicationStatus.REJECTED),
                job_title="Java Engineer", job_skills=("java",),
            )
        )

    result = AdaptiveJobRanker(repository).rank(
        "candidate", (_job("python", "new-company"),), JobFilter(keyword="Python")
    )[0]

    cap = max(abs(result.relevance_score) * 0.1, 0.01)
    assert result.outcome_adjustment == pytest.approx(cap * 0.1)
    assert result.evidence_count == 0
    assert "exploration" in result.reason


def test_attribute_evidence_deduplicates_one_application_matching_multiple_terms():
    repository = InMemoryJobApplicationRepository()
    for index in range(2):
        repository.add(
            _application(
                f"multi-{index}", "candidate", f"historic-{index}",
                (JobApplicationStatus.APPLIED, JobApplicationStatus.INTERVIEW),
                job_title="Python Backend", job_skills=("python", "backend"),
            )
        )

    result = AdaptiveJobRanker(repository).rank(
        "candidate", (_job("new", "new-company"),), JobFilter(keyword="Python backend")
    )[0]

    assert result.evidence_count == 2


def test_same_application_is_not_counted_twice_for_employer_and_attributes():
    repository = InMemoryJobApplicationRepository()
    for index in range(2):
        repository.add(
            _application(
                f"same-{index}", "candidate", "same-company",
                (JobApplicationStatus.APPLIED, JobApplicationStatus.INTERVIEW),
                job_title="Python Backend", job_skills=("python",),
            )
        )

    result = AdaptiveJobRanker(repository).rank(
        "candidate", (_job("new", "same-company"),), JobFilter(keyword="Python")
    )[0]

    assert result.evidence_count == 2


@pytest.mark.parametrize("user_id", ["", "   ", None, 7])
def test_invalid_candidate_identity_is_rejected(user_id):
    with pytest.raises(ValueError, match="user_id"):
        AdaptiveJobRanker(InMemoryJobApplicationRepository()).rank(user_id, (_job("job", "company"),))
