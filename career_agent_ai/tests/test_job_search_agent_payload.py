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
from career_agent_ai.application.jobs.job_sort import JobSort
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

    repository.add(
        make_job(
            "Python Engineer",
            "Microsoft",
            "Berlin",
        )
    )

    service = SearchService(repository)

    registry = AgentRegistry()
    registry.register(JobSearchAgent())

    factory = AgentFactory(
        registry,
        search_service=service,
    )

    return AgentBrain(
        MemoryEngine(),
        WorkflowEngine(),
        factory,
    )


def test_brain_passes_keyword_to_job_search():
    brain = make_brain()

    response = brain.process(
        AgentRequest(
            request_id="payload-1",
            user_id="user-1",
            action="job_search",
            payload={
                "keyword": "python",
            },
        )
    )

    assert response.success
    assert response.metadata["total"] == 2


def test_brain_passes_location_to_job_search():
    brain = make_brain()

    response = brain.process(
        AgentRequest(
            request_id="payload-2",
            user_id="user-1",
            action="job_search",
            payload={
                "location": "Berlin",
            },
        )
    )

    assert response.success
    assert response.metadata["total"] == 2


def test_brain_passes_pagination_to_job_search():
    brain = make_brain()

    response = brain.process(
        AgentRequest(
            request_id="payload-3",
            user_id="user-1",
            action="job_search",
            payload={
                "page": 2,
                "page_size": 1,
            },
        )
    )

    assert response.success
    assert response.metadata["total"] == 3
    assert response.metadata["page"] == 2
    assert response.metadata["page_size"] == 1


def test_brain_passes_sort_to_job_search():
    brain = make_brain()

    response = brain.process(
        AgentRequest(
            request_id="payload-4",
            user_id="user-1",
            action="job_search",
            payload={
                "sort": JobSort.SALARY_HIGH,
            },
        )
    )

    assert response.success
    assert response.metadata["total"] == 3
