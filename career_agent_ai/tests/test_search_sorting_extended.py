from datetime import UTC, datetime, timedelta

from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.employment_type import EmploymentType
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_sort import JobSort
from career_agent_ai.application.jobs.job_source import JobSource
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.jobs.salary import Salary
from career_agent_ai.application.search.search_sorting import SearchSorting


def make_job(
    job_id: str,
    title: str,
    company: str,
    city: str,
    minimum: int,
    maximum: int,
    created_at: datetime | None = None,
) -> Job:
    return Job.create(
        job_id=job_id,
        title=title,
        company=Company(
            company_id=f"company-{job_id}",
            name=company,
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
        created_at=created_at,
    )


def make_jobs() -> tuple[Job, ...]:
    now = datetime.now(UTC)

    return (
        make_job(
            "1",
            "Python Developer",
            "OpenAI",
            "Berlin",
            70000,
            90000,
            now - timedelta(days=2),
        ),
        make_job(
            "2",
            "Java Engineer",
            "Google",
            "Munich",
            50000,
            65000,
            now,
        ),
        make_job(
            "3",
            "Backend Engineer",
            "Microsoft",
            "Hamburg",
            60000,
            80000,
            now - timedelta(days=1),
        ),
    )


def test_sort_title():
    result = SearchSorting().sort(
        make_jobs(),
        JobSort.TITLE,
    )

    assert tuple(job.title for job in result) == (
        "Backend Engineer",
        "Java Engineer",
        "Python Developer",
    )


def test_sort_company():
    result = SearchSorting().sort(
        make_jobs(),
        JobSort.COMPANY,
    )

    assert tuple(job.company.name for job in result) == (
        "Google",
        "Microsoft",
        "OpenAI",
    )


def test_sort_city():
    result = SearchSorting().sort(
        make_jobs(),
        JobSort.CITY,
    )

    assert tuple(job.location.city for job in result) == (
        "Berlin",
        "Hamburg",
        "Munich",
    )


def test_sort_salary():
    result = SearchSorting().sort(
        make_jobs(),
        JobSort.SALARY,
    )

    assert tuple(job.job_id for job in result) == (
        "1",
        "3",
        "2",
    )


def test_sort_salary_high():
    result = SearchSorting().sort(
        make_jobs(),
        JobSort.SALARY_HIGH,
    )

    assert tuple(job.job_id for job in result) == (
        "1",
        "3",
        "2",
    )


def test_sort_salary_low():
    result = SearchSorting().sort(
        make_jobs(),
        JobSort.SALARY_LOW,
    )

    assert tuple(job.job_id for job in result) == (
        "2",
        "3",
        "1",
    )


def test_sort_newest():
    result = SearchSorting().sort(
        make_jobs(),
        JobSort.NEWEST,
    )

    assert tuple(job.job_id for job in result) == (
        "2",
        "3",
        "1",
    )


def test_sort_empty():
    result = SearchSorting().sort(
        (),
        JobSort.TITLE,
    )

    assert result == ()
