from datetime import UTC, datetime, timedelta

from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_sort import JobSort
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.jobs.salary import Salary
from career_agent_ai.application.search.search_sorting import SearchSorting


def make_job(
    job_id: str,
    minimum: int = 50000,
    maximum: int = 70000,
    created_at: datetime | None = None,
) -> Job:
    return Job(
        job_id=job_id,
        title=f"Job {job_id}",
        company=Company(
            company_id=job_id,
            name=f"Company {job_id}",
        ),
        location=Location(
            country="Germany",
            city="Berlin",
        ),
        salary=Salary(
            minimum=minimum,
            maximum=maximum,
        ),
        created_at=created_at,
    )


def test_sort_salary_high():
    jobs = (
        make_job("1", maximum=60000),
        make_job("2", maximum=90000),
        make_job("3", maximum=70000),
    )

    result = SearchSorting().sort(
        jobs,
        JobSort.SALARY_HIGH,
    )

    assert tuple(job.job_id for job in result) == ("2", "3", "1")


def test_sort_salary_low():
    jobs = (
        make_job("1", minimum=60000),
        make_job("2", minimum=30000),
        make_job("3", minimum=50000),
    )

    result = SearchSorting().sort(
        jobs,
        JobSort.SALARY_LOW,
    )

    assert tuple(job.job_id for job in result) == ("2", "3", "1")


def test_sort_newest():
    now = datetime.now(UTC)

    jobs = (
        make_job(
            "1",
            created_at=now - timedelta(days=2),
        ),
        make_job(
            "2",
            created_at=now,
        ),
        make_job(
            "3",
            created_at=now - timedelta(days=1),
        ),
    )

    result = SearchSorting().sort(
        jobs,
        JobSort.NEWEST,
    )

    assert tuple(job.job_id for job in result) == ("2", "3", "1")


def test_sort_empty():
    result = SearchSorting().sort(
        (),
        JobSort.NEWEST,
    )

    assert result == ()