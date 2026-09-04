from career_agent_ai.application.jobs import (
    Company,
    EmploymentType,
    Job,
    JobSource,
    Location,
    Salary,
)
from career_agent_ai.application.jobs.job_ranker import JobRanker


def make_job(title: str, salary: int) -> Job:
    return Job.create(
        title=title,
        company=Company(
            company_id="1",
            name="OpenAI",
        ),
        location=Location(
            country="Germany",
            city="Berlin",
        ),
        salary=Salary(
            minimum=salary,
            maximum=salary,
        ),
        employment_type=EmploymentType.FULL_TIME,
        source=JobSource.INTERNAL,
    )


def test_rank_count():
    ranker = JobRanker()

    jobs = (
        make_job("A", 50000),
        make_job("B", 70000),
    )

    result = ranker.rank(jobs)

    assert len(result) == 2


def test_rank_order():
    ranker = JobRanker()

    jobs = (
        make_job("Low", 40000),
        make_job("High", 90000),
    )

    result = ranker.rank(jobs)

    assert result[0].job.title == "High"
    assert result[1].job.title == "Low"


def test_rank_score():
    ranker = JobRanker()

    result = ranker.rank(
        (
            make_job("Python", 80000),
        )
    )

    assert result[0].score > 0