from dataclasses import FrozenInstanceError

import pytest

from career_agent_ai.application.agents.job_search.job_search_agent import JobSearchAgent
from career_agent_ai.application.agents.job_search.job_search_request import JobSearchRequest
from career_agent_ai.application.agents.job_search.job_search_response import JobSearchResponse
from career_agent_ai.application.brain.agent_context import AgentContext
from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.employment_type import EmploymentType
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_query import JobQuery
from career_agent_ai.application.jobs.job_source import JobSource
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.jobs.salary import Salary
from career_agent_ai.application.memory.memory_snapshot import MemorySnapshot
from career_agent_ai.application.search.search_service import SearchService
from career_agent_ai.application.jobs.in_memory_job_repository import InMemoryJobRepository


def make_context() -> AgentContext:
    return AgentContext(
        user_id="user-1",
        memory_snapshot=MemorySnapshot(),
    )


def make_job(
    title: str = "Python Developer",
) -> Job:
    return Job.create(
        title=title,
        company=Company(
            company_id="company-1",
            name="OpenAI",
        ),
        location=Location(
            country="Germany",
            city="Berlin",
        ),
        salary=Salary(
            minimum=70000,
            maximum=90000,
        ),
        employment_type=EmploymentType.FULL_TIME,
        source=JobSource.INTERNAL,
    )


def make_search_service() -> SearchService:
    repository = InMemoryJobRepository()
    repository.add(make_job())

    return SearchService(repository)


def test_job_search_metadata():
    agent = JobSearchAgent()

    assert agent.id == "job_search"
    assert agent.name == "Job Search Agent"
    assert agent.version == "1.0"
    assert agent.description == "Handles job search requests."


def test_job_search_supports():
    agent = JobSearchAgent()

    assert agent.supports("job_search")
    assert not agent.supports("resume")


def test_job_search_execute():
    agent = JobSearchAgent()

    result = agent.execute(make_context())

    assert result.success
    assert result.agent_id == "job_search"
    assert result.messages == ("Job Search Agent executed.",)


def test_job_search_snapshot():
    agent = JobSearchAgent()

    result = agent.snapshot()

    assert result.success
    assert result.agent_id == "job_search"


def test_request_is_immutable():
    request = JobSearchRequest(
        keywords="Python",
        location="Berlin",
    )

    with pytest.raises(FrozenInstanceError):
        request.location = "Munich"


def test_response_is_immutable():
    response = JobSearchResponse(
        success=True,
        jobs_found=12,
    )

    with pytest.raises(FrozenInstanceError):
        response.jobs_found = 20


def test_job_search_agent_searches_with_search_service():
    agent = JobSearchAgent(
        search_service=make_search_service(),
    )

    response = agent.search(
        JobQuery(
            page=1,
            page_size=20,
        )
    )

    assert response.count == 1
    assert response.total == 1
    assert response.jobs[0].title == "Python Developer"


def test_job_search_agent_requires_search_service():
    agent = JobSearchAgent()

    with pytest.raises(RuntimeError):
        agent.search(JobQuery())
