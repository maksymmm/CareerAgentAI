from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.employment_type import EmploymentType
from career_agent_ai.application.jobs.in_memory_job_repository import (
    InMemoryJobRepository,
)
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_filter import JobFilter
from career_agent_ai.application.jobs.job_query import JobQuery
from career_agent_ai.application.jobs.job_source import JobSource
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.jobs.salary import Salary


def make_job(
    title: str,
    company: str,
    country: str,
    city: str,
    employment_type: EmploymentType,
) -> Job:
    return Job.create(
        title=title,
        company=Company(
            company_id=f"{company}-{title}",
            name=company,
        ),
        location=Location(
            country=country,
            city=city,
        ),
        salary=Salary(
            minimum=50000,
            maximum=80000,
        ),
        employment_type=employment_type,
        source=JobSource.INTERNAL,
    )


def make_repository() -> InMemoryJobRepository:
    repository = InMemoryJobRepository()

    repository.add(
        make_job(
            title="Python Developer",
            company="OpenAI",
            country="Germany",
            city="Berlin",
            employment_type=EmploymentType.FULL_TIME,
        )
    )

    repository.add(
        make_job(
            title="Java Engineer",
            company="Google",
            country="Germany",
            city="Munich",
            employment_type=EmploymentType.FULL_TIME,
        )
    )

    repository.add(
        make_job(
            title="Python Engineer",
            company="Microsoft",
            country="Germany",
            city="Remote",
            employment_type=EmploymentType.PART_TIME,
        )
    )

    repository.add(
        make_job(
            title="Backend Developer",
            company="OpenAI",
            country="Netherlands",
            city="Amsterdam",
            employment_type=EmploymentType.FULL_TIME,
        )
    )

    return repository


def test_filter_by_country():
    repository = make_repository()

    result = repository.search(
        JobQuery(
            filters=JobFilter(
                country="Germany",
            ),
        )
    )

    assert result.total == 3


def test_filter_by_city():
    repository = make_repository()

    result = repository.search(
        JobQuery(
            filters=JobFilter(
                city="Berlin",
            ),
        )
    )

    assert result.total == 1
    assert result.jobs[0].title == "Python Developer"


def test_filter_by_company():
    repository = make_repository()

    result = repository.search(
        JobQuery(
            filters=JobFilter(
                company="OpenAI",
            ),
        )
    )

    assert result.total == 2


def test_filter_by_employment_type():
    repository = make_repository()

    result = repository.search(
        JobQuery(
            filters=JobFilter(
                employment_type=EmploymentType.PART_TIME,
            ),
        )
    )

    assert result.total == 1
    assert result.jobs[0].title == "Python Engineer"


def test_filter_remote_only():
    repository = make_repository()

    result = repository.search(
        JobQuery(
            filters=JobFilter(
                remote_only=True,
            ),
        )
    )

    assert result.total == 1
    assert result.jobs[0].title == "Python Engineer"


def test_filter_keyword_matches_title():
    repository = make_repository()

    result = repository.search(
        JobQuery(
            filters=JobFilter(
                keyword="python",
            ),
        )
    )

    assert result.total == 2


def test_filter_keyword_matches_company():
    repository = make_repository()

    result = repository.search(
        JobQuery(
            filters=JobFilter(
                keyword="openai",
            ),
        )
    )

    assert result.total == 2


def test_combined_filters():
    repository = make_repository()

    result = repository.search(
        JobQuery(
            filters=JobFilter(
                keyword="python",
                country="Germany",
                remote_only=True,
                employment_type=EmploymentType.PART_TIME,
            ),
        )
    )

    assert result.total == 1
    assert result.jobs[0].title == "Python Engineer"


def test_no_filters_returns_all_jobs():
    repository = make_repository()

    result = repository.search(
        JobQuery()
    )

    assert result.total == 4
    assert len(result.jobs) == 4
