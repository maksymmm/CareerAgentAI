import pytest

from career_agent_ai.application.agents.agent_factory import AgentFactory
from career_agent_ai.application.agents.agent_registry import AgentRegistry
from career_agent_ai.application.agents.job_search.job_search_agent import JobSearchAgent
from career_agent_ai.application.agents.resume.resume_agent import ResumeAgent


def test_factory_resolve_resume():
    registry = AgentRegistry()
    registry.register(ResumeAgent())

    factory = AgentFactory(registry)

    agent = factory.resolve("resume")

    assert agent.id == "resume"


def test_factory_resolve_job_search():
    registry = AgentRegistry()
    registry.register(JobSearchAgent())

    factory = AgentFactory(registry)

    agent = factory.resolve("job_search")

    assert agent.id == "job_search"


def test_factory_unknown():
    factory = AgentFactory(AgentRegistry())

    with pytest.raises(ValueError):
        factory.resolve("unknown")

def test_factory_resolve_job_search_with_search_service():

    from career_agent_ai.application.jobs.company import Company
    from career_agent_ai.application.jobs.employment_type import EmploymentType
    from career_agent_ai.application.jobs.in_memory_job_repository import (
        InMemoryJobRepository,
    )
    from career_agent_ai.application.jobs.job import Job
    from career_agent_ai.application.jobs.job_source import JobSource
    from career_agent_ai.application.jobs.location import Location
    from career_agent_ai.application.jobs.salary import Salary
    from career_agent_ai.application.search.search_service import SearchService

    repository = InMemoryJobRepository()

    repository.add(
        Job.create(
            title="Python Developer",
            company=Company(
                company_id="1",
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
    )

    service = SearchService(repository)

    registry = AgentRegistry()
    registry.register(JobSearchAgent())

    factory = AgentFactory(
        registry,
        search_service=service,
    )

    agent = factory.resolve("job_search")

    result = agent.search(
        __import__(
            "career_agent_ai.application.jobs.job_query",
            fromlist=["JobQuery"],
        ).JobQuery()
    )

    assert agent.id == "job_search"
    assert result.total == 1
    assert result.count == 1
    assert result.jobs[0].title == "Python Developer"


def test_factory_wires_search_service_into_job_search_agent():

    from career_agent_ai.application.jobs.in_memory_job_repository import (
        InMemoryJobRepository,
    )
    from career_agent_ai.application.search.search_service import SearchService

    repository = InMemoryJobRepository()
    service = SearchService(repository)

    registry = AgentRegistry()
    registry.register(JobSearchAgent())

    factory = AgentFactory(
        registry,
        search_service=service,
    )

    agent = factory.resolve("job_search")

    assert isinstance(agent, JobSearchAgent)

    # The public search API must work because the dependency
    # was injected by the factory.
    from career_agent_ai.application.jobs.job_query import JobQuery

    result = agent.search(JobQuery())

    assert result.total == 0
    assert result.empty
