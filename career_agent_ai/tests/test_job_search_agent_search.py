from career_agent_ai.application.agents.job_search.job_search_agent import JobSearchAgent
from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.employment_type import EmploymentType
from career_agent_ai.application.jobs.in_memory_job_repository import InMemoryJobRepository
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_filter import JobFilter
from career_agent_ai.application.jobs.job_query import JobQuery
from career_agent_ai.application.jobs.job_source import JobSource
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.jobs.salary import Salary
from career_agent_ai.application.search.search_service import SearchService


def make_job(
    title: str,
    city: str = "Berlin",
    minimum: int = 50000,
    maximum: int = 70000,
) -> Job:
    return Job.create(
        title=title,
        company=Company(
            company_id=title,
            name="OpenAI",
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


def make_agent() -> JobSearchAgent:
    repository = InMemoryJobRepository()

    repository.add(
        make_job(
            "Python Developer",
            "Berlin",
            70000,
            90000,
        )
    )

    repository.add(
        make_job(
            "Java Engineer",
            "Munich",
            50000,
            65000,
        )
    )

    repository.add(
        make_job(
            "Python Engineer",
            "Hamburg",
            60000,
            80000,
        )
    )

    service = SearchService(repository)

    return JobSearchAgent(
        search_service=service,
    )


def test_search_returns_matching_jobs():
    agent = make_agent()

    response = agent.search(
        JobQuery(
            filters=JobFilter(
                keyword="python",
            ),
        )
    )

    assert response.total == 2
    assert response.count == 2


def test_search_respects_pagination():
    agent = make_agent()

    response = agent.search(
        JobQuery(
            page=2,
            page_size=2,
        )
    )

    assert response.page == 2
    assert response.page_size == 2
    assert response.count == 1


def test_search_returns_empty_result():
    agent = make_agent()

    response = agent.search(
        JobQuery(
            filters=JobFilter(
                keyword="Rust",
            ),
        )
    )

    assert response.total == 0
    assert response.empty
