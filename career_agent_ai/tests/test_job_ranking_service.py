from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.employment_type import EmploymentType
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_source import JobSource
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.jobs.salary import Salary
from career_agent_ai.application.search.job_ranking_service import (
    JobRankingService,
)


def make_job(
    title: str,
    minimum: int,
    maximum: int,
    city: str = "Berlin",
) -> Job:
    return Job.create(
        title=title,
        company=Company(
            company_id=title,
            name=title,
        ),
        location=Location(
            country="Germany",
            city=city,
        ),
        salary=Salary(
            minimum=minimum,
            maximum=maximum,
        ),
        employment_type=EmploymentType.FULL_TIME,
        source=JobSource.INTERNAL,
    )


def test_rank_returns_jobs():

    jobs = (
        make_job(
            "Low Salary",
            40000,
            50000,
        ),
        make_job(
            "High Salary",
            80000,
            100000,
        ),
    )

    result = JobRankingService().rank(jobs)

    assert tuple(
        job.title for job in result
    ) == (
        "High Salary",
        "Low Salary",
    )


def test_rank_empty():

    result = JobRankingService().rank(())

    assert result == ()


def test_rank_preserves_job_objects():

    job = make_job(
        "Python Developer",
        70000,
        90000,
    )

    result = JobRankingService().rank((job,))

    assert len(result) == 1
    assert result[0] is job
