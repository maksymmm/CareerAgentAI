from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.employment_type import EmploymentType
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_source import JobSource
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.jobs.salary import Salary
from career_agent_ai.application.search.job_deduplicator import (
    JobDeduplicator,
)


def make_job(
    title: str,
    company: str,
    city: str,
) -> Job:
    return Job.create(
        title=title,
        company=Company(
            company_id=company,
            name=company,
        ),
        location=Location(
            country="Germany",
            city=city,
        ),
        salary=Salary(
            minimum=50000,
            maximum=70000,
        ),
        employment_type=EmploymentType.FULL_TIME,
        source=JobSource.INTERNAL,
    )


def test_deduplicates_same_job_id():

    job = make_job(
        "Python Developer",
        "OpenAI",
        "Berlin",
    )

    duplicate = Job(
        job_id=job.job_id,
        title=job.title,
        company=job.company,
        location=job.location,
        salary=job.salary,
        employment_type=job.employment_type,
        source=job.source,
        user_id=job.user_id,
        url=job.url,
        description=job.description,
        created_at=job.created_at,
    )

    result = JobDeduplicator().deduplicate(
        (job, duplicate)
    )

    assert len(result) == 1
    assert result[0] == job


def test_deduplicates_same_title_company_city():

    first = make_job(
        "Python Developer",
        "OpenAI",
        "Berlin",
    )

    second = make_job(
        "Python Developer",
        "OpenAI",
        "Berlin",
    )

    assert first.job_id != second.job_id

    result = JobDeduplicator().deduplicate(
        (first, second)
    )

    assert len(result) == 1
    assert result[0] == first


def test_keeps_different_jobs():

    first = make_job(
        "Python Developer",
        "OpenAI",
        "Berlin",
    )

    second = make_job(
        "Python Developer",
        "OpenAI",
        "Munich",
    )

    result = JobDeduplicator().deduplicate(
        (first, second)
    )

    assert len(result) == 2


def test_keeps_empty_input():

    result = JobDeduplicator().deduplicate(())

    assert result == ()


def test_preserves_order():

    first = make_job(
        "Python Developer",
        "OpenAI",
        "Berlin",
    )

    second = make_job(
        "Java Developer",
        "Google",
        "Munich",
    )

    third = make_job(
        "Data Engineer",
        "Microsoft",
        "Hamburg",
    )

    result = JobDeduplicator().deduplicate(
        (first, second, third)
    )

    assert tuple(
        job.title for job in result
    ) == (
        "Python Developer",
        "Java Developer",
        "Data Engineer",
    )
