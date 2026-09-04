from career_agent_ai.application.agents.job_search import (
    JobSearchAgent,
    JobSearchRequest,
    JobSearchResponse,
)


def test_package_exports():
    assert JobSearchAgent is not None
    assert JobSearchRequest is not None
    assert JobSearchResponse is not None