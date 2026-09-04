from career_agent_ai.application.agents.agent_registry import AgentRegistry
from career_agent_ai.application.agents.resume.resume_agent import ResumeAgent
from career_agent_ai.application.agents.job_search.job_search_agent import JobSearchAgent


class DummyAgent:
    def __init__(self, agent_id: str):
        self._id = agent_id

    @property
    def id(self):
        return self._id


def test_register():
    registry = AgentRegistry()

    agent = DummyAgent("dummy")

    registry.register(agent)

    assert registry.exists("dummy")


def test_get():
    registry = AgentRegistry()

    agent = DummyAgent("dummy")

    registry.register(agent)

    assert registry.get("dummy") is agent


def test_exists():
    registry = AgentRegistry()

    assert not registry.exists("dummy")

    registry.register(DummyAgent("dummy"))

    assert registry.exists("dummy")


def test_all():
    registry = AgentRegistry()

    registry.register(DummyAgent("a"))
    registry.register(DummyAgent("b"))

    assert len(registry.all()) == 2


def test_clear():
    registry = AgentRegistry()

    registry.register(DummyAgent("a"))

    registry.clear()

    assert len(registry.all()) == 0


def test_registry_multiple_agents():
    registry = AgentRegistry()

    registry.register(ResumeAgent())
    registry.register(JobSearchAgent())

    assert registry.exists("resume")
    assert registry.exists("job_search")
    assert len(registry.all()) == 2