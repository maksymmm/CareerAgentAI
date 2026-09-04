from career_agent_ai.application.agents.agent_factory import AgentFactory
from career_agent_ai.application.agents.job_search.job_search_agent import JobSearchAgent


def test_factory_creates_job_search_agent():
    agent = AgentFactory.create("job_search")

    assert isinstance(agent, JobSearchAgent)
    assert agent.id == "job_search"