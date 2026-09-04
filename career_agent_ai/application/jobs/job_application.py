from dataclasses import dataclass

from career_agent_ai.application.jobs.job_application_status import (
    JobApplicationStatus,
)


@dataclass(frozen=True)
class JobApplication:
    application_id: str

    user_id: str

    job_id: str

    status: JobApplicationStatus