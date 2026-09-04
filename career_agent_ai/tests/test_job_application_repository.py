from career_agent_ai.application.jobs.in_memory_job_application_repository import (
    InMemoryJobApplicationRepository,
)
from career_agent_ai.application.jobs.job_application import JobApplication
from career_agent_ai.application.jobs.job_application_status import (
    JobApplicationStatus,
)


def make_application(app_id: str) -> JobApplication:
    return JobApplication(
        application_id=app_id,
        user_id="user",
        job_id="job",
        status=JobApplicationStatus.APPLIED,
    )


def test_add():
    repo = InMemoryJobApplicationRepository()

    app = make_application("1")

    repo.add(app)

    assert repo.get("1") == app


def test_list():
    repo = InMemoryJobApplicationRepository()

    repo.add(make_application("1"))
    repo.add(make_application("2"))

    assert len(repo.list("user")) == 2


def test_get_missing():
    repo = InMemoryJobApplicationRepository()

    assert repo.get("missing") is None


def test_clear():
    repo = InMemoryJobApplicationRepository()

    repo.add(make_application("1"))

    repo.clear()

    assert len(repo.list("user")) == 0