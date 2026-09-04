from __future__ import annotations

from career_agent_ai.application.agents.agent import Agent


class AgentRegistry:

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        self._agents[agent.id] = agent

    def get(self, agent_id: str) -> Agent:
        return self._agents[agent_id]

    def exists(self, agent_id: str) -> bool:
        return agent_id in self._agents

    def all(self) -> tuple[Agent, ...]:
        return tuple(self._agents.values())

    def clear(self) -> None:
        self._agents.clear()