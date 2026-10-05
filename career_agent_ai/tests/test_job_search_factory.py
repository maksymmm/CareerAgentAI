import pytest

from career_agent_ai.application.agents.agent_factory import AgentFactory
from career_agent_ai.application.agents.job_search.job_search_agent import JobSearchAgent
from career_agent_ai.application.jobs.job_filter import JobFilter
from career_agent_ai.application.jobs.job_query import JobQuery
from career_agent_ai.application.runtime import RuntimeConfig
from career_agent_ai.application.search.providers.arbeitnow_provider import (
    ArbeitnowProvider,
)


def test_factory_creates_job_search_agent():
    agent = AgentFactory.create("job_search")

    assert isinstance(agent, JobSearchAgent)
    assert agent.id == "job_search"


def test_factory_job_search_obeys_disabled_runtime_network_policy(monkeypatch):
    calls = []

    def record_search(self, query):
        calls.append(query)
        return ()

    monkeypatch.setattr(ArbeitnowProvider, "search", record_search)
    agent = AgentFactory.create(
        "job_search",
        runtime_config=RuntimeConfig.from_env({}),
    )

    with pytest.raises(PermissionError, match="Network providers are disabled"):
        agent.search(JobQuery(filters=JobFilter(keyword="python")))

    assert calls == []
