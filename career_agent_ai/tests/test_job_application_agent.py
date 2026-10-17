from career_agent_ai.application.agents.job_application.job_application_agent import (
    JobApplicationAgent,
)
from career_agent_ai.application.brain.agent_context import AgentContext
from career_agent_ai.application.jobs.in_memory_job_application_repository import (
    InMemoryJobApplicationRepository,
)
from career_agent_ai.application.jobs.job_application_status import (
    JobApplicationStatus,
)
from career_agent_ai.application.memory.memory_engine import MemoryEngine


def make_context(
    payload: dict,
    user_id: str = "user-1",
) -> AgentContext:
    memory = MemoryEngine()

    return AgentContext(
        user_id=user_id,
        memory_snapshot=memory.snapshot(),
        active_workflow=None,
        payload=payload,
        metadata={},
    )


def make_agent():
    repository = InMemoryJobApplicationRepository()

    return (
        JobApplicationAgent(repository=repository),
        repository,
    )


def test_create_application():
    agent, repository = make_agent()

    result = agent.execute(
        make_context(
            {
                "operation": "create",
                "application_id": "application-1",
                "job_id": "job-1",
            }
        )
    )

    assert result.success
    assert repository.get("application-1") is not None
    assert repository.get("application-1").status == (
        JobApplicationStatus.SAVED
    )


def test_create_application_with_status():
    agent, repository = make_agent()

    result = agent.execute(
        make_context(
            {
                "operation": "create",
                "application_id": "application-1",
                "job_id": "job-1",
                "status": JobApplicationStatus.APPLIED,
            }
        )
    )

    assert result.success
    assert repository.get("application-1").status == (
        JobApplicationStatus.APPLIED
    )


def test_create_duplicate_returns_clean_conflict_failure():
    agent, _ = make_agent()
    payload = {
        "operation": "create",
        "application_id": "application-1",
        "job_id": "job-1",
    }

    assert agent.execute(make_context(payload)).success
    conflict = agent.execute(make_context(payload))

    assert not conflict.success
    assert conflict.messages == ("A job application for this job already exists.",)


def test_get_application():
    agent, repository = make_agent()

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

    result = agent.execute(
        make_context(
            {
                "operation": "get",
                "application_id": "application-1",
            }
        )
    )

    assert result.success
    assert result.metadata["application_id"] == "application-1"
    assert result.metadata["job_id"] == "job-1"
    assert result.metadata["status"] == "applied"


def test_get_missing_application():
    agent, _ = make_agent()

    result = agent.execute(
        make_context(
            {
                "operation": "get",
                "application_id": "missing",
            }
        )
    )

    assert not result.success


def test_list_applications():
    agent, repository = make_agent()

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
            status=JobApplicationStatus.INTERVIEW,
        )
    )

    repository.add(
        JobApplication(
            application_id="application-3",
            user_id="user-2",
            job_id="job-3",
            status=JobApplicationStatus.OFFER,
        )
    )

    result = agent.execute(
        make_context(
            {
                "operation": "list",
            }
        )
    )

    assert result.success
    assert result.metadata["count"] == 2


def test_update_status():
    agent, repository = make_agent()

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

    result = agent.execute(
        make_context(
            {
                "operation": "update_status",
                "application_id": "application-1",
                "status": JobApplicationStatus.APPLIED,
            }
        )
    )

    assert result.success
    assert repository.get("application-1").status == (
        JobApplicationStatus.APPLIED
    )


def test_update_status_returns_clean_repository_conflict_failure():
    from career_agent_ai.application.jobs.in_memory_job_application_repository import (
        InMemoryJobApplicationRepository,
    )
    from career_agent_ai.application.jobs.job_application import JobApplication
    from career_agent_ai.application.jobs.job_application_repository import (
        ApplicationConflictError,
    )

    class ConflictingRepository(InMemoryJobApplicationRepository):
        def update(self, application, *, expected_version):
            raise ApplicationConflictError("concurrent update")

    repository = ConflictingRepository()
    repository.add(
        JobApplication(
            application_id="application-1",
            user_id="user-1",
            job_id="job-1",
            status=JobApplicationStatus.SAVED,
        )
    )
    agent = JobApplicationAgent(repository=repository)

    result = agent.execute(
        make_context(
            {
                "operation": "update_status",
                "application_id": "application-1",
                "status": JobApplicationStatus.APPLIED,
            }
        )
    )

    assert not result.success
    assert result.messages == ("Job application changed; reload it and try again.",)


def test_rejects_unknown_operation():
    agent, _ = make_agent()

    result = agent.execute(
        make_context(
            {
                "operation": "something_else",
            }
        )
    )

    assert not result.success


def test_rejects_missing_application_id():
    agent, _ = make_agent()

    result = agent.execute(
        make_context(
            {
                "operation": "create",
                "job_id": "job-1",
            }
        )
    )

    assert not result.success


def test_rejects_invalid_status():
    agent, _ = make_agent()

    result = agent.execute(
        make_context(
            {
                "operation": "create",
                "application_id": "application-1",
                "job_id": "job-1",
                "status": "invalid",
            }
        )
    )

    assert not result.success
