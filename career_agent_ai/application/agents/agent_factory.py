from __future__ import annotations

from career_agent_ai.application.agents.agent import Agent
from career_agent_ai.application.agents.agent_registry import AgentRegistry
from career_agent_ai.application.jobs.job_application_repository import (
    JobApplicationRepository,
)
from career_agent_ai.application.search.search_service import SearchService


class AgentFactory:
    def __init__(
        self,
        registry: AgentRegistry,
        search_service: SearchService | None = None,
        job_application_repository: JobApplicationRepository | None = None,
    ) -> None:
        self._registry = registry
        self._search_service = search_service
        self._job_application_repository = job_application_repository

    @classmethod
    def create(cls, agent_id: str) -> Agent:
        from career_agent_ai.application.agents.resume.resume_agent import (
            ResumeAgent,
        )
        from career_agent_ai.application.agents.job_search.job_search_agent import (
            JobSearchAgent,
        )
        from career_agent_ai.application.agents.job_application.job_application_agent import (
            JobApplicationAgent,
        )

        if agent_id == "resume":
            return ResumeAgent()

        if agent_id == "job_search":
            return cls._create_job_search_agent()

        if agent_id == "job_application":
            return JobApplicationAgent()

        raise ValueError(
            f"Unknown agent: {agent_id}"
        )

    @classmethod
    def _create_job_search_agent(cls) -> Agent:
        from career_agent_ai.application.agents.job_search.job_search_agent import (
            JobSearchAgent,
        )
        from career_agent_ai.application.search.provider_job_repository import (
            ProviderJobRepository,
        )
        from career_agent_ai.application.search.providers.arbeitnow_provider import (
            ArbeitnowProvider,
        )

        provider = ArbeitnowProvider()

        repository = ProviderJobRepository(
            providers=(provider,),
        )

        search_service = SearchService(repository)

        return JobSearchAgent(
            search_service=search_service,
        )

    def resolve(
        self,
        action: str,
    ) -> Agent:
        for agent in self._registry.all():
            if agent.id != action:
                continue

            if agent.id == "job_search":
                if self._search_service is not None:
                    from career_agent_ai.application.agents.job_search.job_search_agent import (
                        JobSearchAgent,
                    )

                    return JobSearchAgent(
                        search_service=self._search_service,
                    )

            if agent.id == "job_application":
                if self._job_application_repository is not None:
                    from career_agent_ai.application.agents.job_application.job_application_agent import (
                        JobApplicationAgent,
                    )

                    return JobApplicationAgent(
                        repository=self._job_application_repository,
                    )

            return agent

        raise ValueError(
            f"No agent registered for action '{action}'"
        )
