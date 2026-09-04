from career_agent_ai.application.agents.agent_factory import AgentFactory
from career_agent_ai.application.agents.agent_registry import AgentRegistry
from career_agent_ai.application.agents.job_search.job_search_agent import JobSearchAgent
from career_agent_ai.application.brain.agent_brain import AgentBrain
from career_agent_ai.application.brain.agent_request import AgentRequest
from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.employment_type import EmploymentType
from career_agent_ai.application.jobs.in_memory_job_repository import (
    InMemoryJobRepository,
)
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_source import JobSource
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.jobs.salary import Salary
from career_agent_ai.application.memory.memory_engine import MemoryEngine
from career_agent_ai.application.search.search_service import SearchService
from career_agent_ai.application.workflow.workflow_engine import WorkflowEngine


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
            minimum=60000,
            maximum=90000,
        ),
        employment_type=EmploymentType.FULL_TIME,
        source=JobSource.INTERNAL,
    )


def make_brain() -> AgentBrain:

    repository = InMemoryJobRepository()

    repository.add(
        make_job(
            "Python Developer",
            "OpenAI",
            "Berlin",
        )
    )

    repository.add(
        make_job(
            "Java Developer",
            "Google",
            "Munich",
        )
    )

    search_service = SearchService(repository)

    registry = AgentRegistry()

    # Factory is responsible for wiring the SearchService
    # into the JobSearchAgent.
    registry.register(JobSearchAgent())

    factory = AgentFactory(
        registry,
        search_service=search_service,
    )

    return AgentBrain(
        MemoryEngine(),
        WorkflowEngine(),
        factory,
    )


def test_brain_executes_job_search_agent():

    brain = make_brain()

    response = brain.process(
        AgentRequest(
            request_id="search-1",
            user_id="user-1",
            action="job_search",
        )
    )

    assert response.success
    assert response.request_id == "search-1"
    assert response.messages == (
        "Found 2 jobs.",
    )


def test_brain_returns_job_search_metadata():

    brain = make_brain()

    response = brain.process(
        AgentRequest(
            request_id="search-2",
            user_id="user-1",
            action="job_search",
        )
    )

    assert response.success
    assert response.metadata["total"] == 2
    assert response.metadata["page"] == 1
    assert response.metadata["page_size"] == 20


def test_brain_search_without_workflow():

    brain = make_brain()

    response = brain.process(
        AgentRequest(
            request_id="search-3",
            user_id="user-1",
            action="job_search",
        )
    )

    assert response.workflow_id is None
