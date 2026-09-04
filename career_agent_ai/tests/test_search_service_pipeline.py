from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.employment_type import EmploymentType
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_filter import JobFilter
from career_agent_ai.application.jobs.job_query import JobQuery
from career_agent_ai.application.jobs.job_source import JobSource
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.jobs.salary import Salary
from career_agent_ai.application.jobs.in_memory_job_repository import (
    InMemoryJobRepository,
)
from career_agent_ai.application.search.search_service import SearchService


def make_job(
    title: str,
    company: str,
    city: str,
    minimum: int,
    maximum: int,
    description: str = "",
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
            minimum=minimum,
            maximum=maximum,
        ),
        employment_type=EmploymentType.FULL_TIME,
        source=JobSource.INTERNAL,
        description=description,
    )


def make_service() -> SearchService:
    repository = InMemoryJobRepository()

    repository.add(
        make_job(
            "Python Developer",
            "OpenAI",
            "Berlin",
            70000,
            90000,
            "Backend development",
        )
    )

    repository.add(
        make_job(
            "Python Engineer",
            "Microsoft",
            "Hamburg",
            80000,
            100000,
            "Machine learning",
        )
    )

    repository.add(
        make_job(
            "Java Developer",
            "Google",
            "Munich",
            50000,
            70000,
            "Backend development",
        )
    )

    return SearchService(repository)


def test_pipeline_filters_keyword():

    service = make_service()

    response = service.search(
        JobQuery(
            filters=JobFilter(
                keyword="python",
            )
        )
    )

    assert response.total == 2
    assert response.count == 2


def test_pipeline_filters_description():

    service = make_service()

    response = service.search(
        JobQuery(
            filters=JobFilter(
                keyword="machine learning",
            )
        )
    )

    assert response.total == 1
    assert response.jobs[0].title == "Python Engineer"


def test_pipeline_sorts_salary():

    from career_agent_ai.application.jobs.job_sort import JobSort

    service = make_service()

    response = service.search(
        JobQuery(
            sort=JobSort.SALARY_HIGH,
        )
    )

    assert tuple(
        job.title for job in response.jobs
    ) == (
        "Python Engineer",
        "Python Developer",
        "Java Developer",
    )


def test_pipeline_paginates_after_sorting():

    from career_agent_ai.application.jobs.job_sort import JobSort

    service = make_service()

    response = service.search(
        JobQuery(
            sort=JobSort.SALARY_HIGH,
            page=2,
            page_size=1,
        )
    )

    assert response.total == 3
    assert response.count == 1
    assert response.page == 2
    assert response.page_size == 1
    assert response.jobs[0].title == "Python Developer"
