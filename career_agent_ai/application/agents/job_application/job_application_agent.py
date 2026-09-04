from __future__ import annotations

from career_agent_ai.application.agents.agent import Agent
from career_agent_ai.application.agents.agent_result import AgentResult
from career_agent_ai.application.brain.agent_context import AgentContext
from career_agent_ai.application.jobs.in_memory_job_application_repository import (
    InMemoryJobApplicationRepository,
)
from career_agent_ai.application.jobs.job_application import JobApplication
from career_agent_ai.application.jobs.job_application_repository import (
    JobApplicationRepository,
)
from career_agent_ai.application.jobs.job_application_status import (
    JobApplicationStatus,
)


class JobApplicationAgent(Agent):

    def __init__(
        self,
        repository: JobApplicationRepository | None = None,
    ) -> None:
        self._repository = repository

    @property
    def id(self) -> str:
        return "job_application"

    @property
    def name(self) -> str:
        return "Job Application Agent"

    @property
    def version(self) -> str:
        return "1.0"

    @property
    def description(self) -> str:
        return "Handles job application tracking operations."

    def execute(
        self,
        context: AgentContext,
    ) -> AgentResult:

        if self._repository is None:
            return AgentResult(
                success=True,
                agent_id=self.id,
                messages=("Job Application Agent executed.",),
            )

        payload = context.payload
        operation = payload.get("operation")

        if operation == "create":
            return self._create(
                context=context,
                payload=payload,
            )

        if operation == "get":
            return self._get(
                context=context,
                payload=payload,
            )

        if operation == "list":
            return self._list(
                context=context,
            )

        if operation == "update_status":
            return self._update_status(
                context=context,
                payload=payload,
            )

        return AgentResult(
            success=False,
            agent_id=self.id,
            messages=(
                f"Unknown job application operation: {operation}",
            ),
        )

    def _create(
        self,
        context: AgentContext,
        payload: dict,
    ) -> AgentResult:

        application_id = payload.get("application_id")
        job_id = payload.get("job_id")

        if not application_id:
            return self._failure(
                "application_id is required."
            )

        if not job_id:
            return self._failure(
                "job_id is required."
            )

        status = self._parse_status(
            payload.get(
                "status",
                JobApplicationStatus.SAVED,
            )
        )

        if status is None:
            return self._failure(
                "Invalid application status."
            )

        application = JobApplication(
            application_id=application_id,
            user_id=context.user_id,
            job_id=job_id,
            status=status,
        )

        self._repository.add(application)

        return AgentResult(
            success=True,
            agent_id=self.id,
            messages=(
                "Job application created.",
            ),
            metadata={
                "application_id": application.application_id,
                "job_id": application.job_id,
                "status": application.status.value,
            },
        )

    def _get(
        self,
        context: AgentContext,
        payload: dict,
    ) -> AgentResult:

        application_id = payload.get("application_id")

        if not application_id:
            return self._failure(
                "application_id is required."
            )

        application = self._repository.get(
            application_id,
        )

        if application is None:
            return self._failure(
                "Job application not found."
            )

        if application.user_id != context.user_id:
            return self._failure(
                "Job application not found."
            )

        return AgentResult(
            success=True,
            agent_id=self.id,
            messages=(
                "Job application found.",
            ),
            metadata={
                "application_id": application.application_id,
                "job_id": application.job_id,
                "status": application.status.value,
            },
        )

    def _list(
        self,
        context: AgentContext,
    ) -> AgentResult:

        applications = self._repository.list(
            context.user_id,
        )

        return AgentResult(
            success=True,
            agent_id=self.id,
            messages=(
                f"Found {len(applications)} job applications.",
            ),
            metadata={
                "count": len(applications),
                "applications": tuple(
                    {
                        "application_id": application.application_id,
                        "job_id": application.job_id,
                        "status": application.status.value,
                    }
                    for application in applications
                ),
            },
        )

    def _update_status(
        self,
        context: AgentContext,
        payload: dict,
    ) -> AgentResult:

        application_id = payload.get("application_id")

        if not application_id:
            return self._failure(
                "application_id is required."
            )

        application = self._repository.get(
            application_id,
        )

        if application is None:
            return self._failure(
                "Job application not found."
            )

        if application.user_id != context.user_id:
            return self._failure(
                "Job application not found."
            )

        status = self._parse_status(
            payload.get("status")
        )

        if status is None:
            return self._failure(
                "Invalid application status."
            )

        updated_application = JobApplication(
            application_id=application.application_id,
            user_id=application.user_id,
            job_id=application.job_id,
            status=status,
        )

        self._repository.add(
            updated_application,
        )

        return AgentResult(
            success=True,
            agent_id=self.id,
            messages=(
                "Job application status updated.",
            ),
            metadata={
                "application_id": updated_application.application_id,
                "job_id": updated_application.job_id,
                "status": updated_application.status.value,
            },
        )

    @staticmethod
    def _parse_status(
        value: object,
    ) -> JobApplicationStatus | None:

        if isinstance(value, JobApplicationStatus):
            return value

        if isinstance(value, str):
            try:
                return JobApplicationStatus(value)
            except ValueError:
                return None

        return None

    def _failure(
        self,
        message: str,
    ) -> AgentResult:

        return AgentResult(
            success=False,
            agent_id=self.id,
            messages=(message,),
        )

    def supports(
        self,
        action: str,
    ) -> bool:
        return action == "job_application"

    def snapshot(self) -> AgentResult:
        return AgentResult(
            success=True,
            agent_id=self.id,
        )