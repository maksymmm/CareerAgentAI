from career_agent_ai.application.agents.agent_factory import AgentFactory
from career_agent_ai.application.agents.agent_registry import AgentRegistry
from career_agent_ai.application.agents.job_application.job_application_agent import (
    JobApplicationAgent,
)
from career_agent_ai.application.brain.agent_brain import AgentBrain
from career_agent_ai.application.brain.agent_request import AgentRequest
from career_agent_ai.application.jobs.in_memory_job_application_repository import (
    InMemoryJobApplicationRepository,
)
from career_agent_ai.application.jobs.job_application_status import (
    JobApplicationStatus,
)
from career_agent_ai.application.memory.memory_engine import MemoryEngine
from career_agent_ai.application.workflow.workflow_engine import WorkflowEngine


def make_brain():
    repository = InMemoryJobApplicationRepository()

    registry = AgentRegistry()
    registry.register(
        JobApplicationAgent(
            repository=repository,
        )
    )

    factory = AgentFactory(
        registry=registry,
        job_application_repository=repository,
    )

    brain = AgentBrain(
        memory_engine=MemoryEngine(),
        workflow_engine=WorkflowEngine(),
        agent_factory=factory,
    )

    return brain, repository


def test_brain_creates_job_application():
    brain, repository = make_brain()

    response = brain.process(
        AgentRequest(
            request_id="application-1",
            user_id="user-1",
            action="job_application",
            payload={
                "operation": "create",
                "application_id": "application-1",
                "job_id": "job-1",
                "status": JobApplicationStatus.APPLIED,
            },
        )
    )

    assert response.success

    application = repository.get("application-1")

    assert application is not None
    assert application.user_id == "user-1"
    assert application.job_id == "job-1"
    assert application.status == JobApplicationStatus.APPLIED


def test_brain_gets_job_application():
    brain, repository = make_brain()

    from career_agent_ai.application.jobs.job_application import (
        JobApplication,
    )

    repository.add(
        JobApplication(
            application_id="application-1",
            user_id="user-1",
            job_id="job-1",
            status=JobApplicationStatus.INTERVIEW,
        )
    )

    response = brain.process(
        AgentRequest(
            request_id="application-2",
            user_id="user-1",
            action="job_application",
            payload={
                "operation": "get",
                "application_id": "application-1",
            },
        )
    )

    assert response.success
    assert response.metadata["application_id"] == "application-1"
    assert response.metadata["status"] == "interview"


def test_brain_lists_job_applications():
    brain, repository = make_brain()

    from career_agent_ai.application.jobs.job_application import (
        JobApplication,
    )

    repository.add(
        JobApplication(
            application_id="application-1",
            user_id="user-1",
            job_id="job-1",
            status=JobApplicationStatus.APPLIED,
        )
    )

    repository.add(
        JobApplication(
            application_id="application-2",
            user_id="user-1",
            job_id="job-2",
            status=JobApplicationStatus.OFFER,
        )
    )

    response = brain.process(
        AgentRequest(
            request_id="application-3",
            user_id="user-1",
            action="job_application",
            payload={
                "operation": "list",
            },
        )
    )

    assert response.success
    assert response.metadata["count"] == 2


def test_brain_updates_application_status():
    brain, repository = make_brain()

    from career_agent_ai.application.jobs.job_application import (
        JobApplication,
    )

    repository.add(
        JobApplication(
            application_id="application-1",
            user_id="user-1",
            job_id="job-1",
            status=JobApplicationStatus.SAVED,
        )
    )

    response = brain.process(
        AgentRequest(
            request_id="application-4",
            user_id="user-1",
            action="job_application",
            payload={
                "operation": "update_status",
                "application_id": "application-1",
                "status": JobApplicationStatus.APPLIED,
            },
        )
    )

    assert response.success
    assert repository.get("application-1").status == (
        JobApplicationStatus.APPLIED
    )


def test_brain_does_not_expose_other_users_application():
    brain, repository = make_brain()

    from career_agent_ai.application.jobs.job_application import (
        JobApplication,
    )

    repository.add(
        JobApplication(
            application_id="application-1",
            user_id="user-2",
            job_id="job-1",
            status=JobApplicationStatus.APPLIED,
        )
    )

    response = brain.process(
        AgentRequest(
            request_id="application-5",
            user_id="user-1",
            action="job_application",
            payload={
                "operation": "get",
                "application_id": "application-1",
            },
        )
    )

    assert not response.success