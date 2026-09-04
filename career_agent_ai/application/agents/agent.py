from __future__ import annotations

from abc import ABC, abstractmethod

from career_agent_ai.application.agents.agent_result import AgentResult
from career_agent_ai.application.brain.agent_context import AgentContext


class Agent(ABC):
    """
    Base interface for all agents.
    """

    @property
    @abstractmethod
    def id(self) -> str:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        ...

    @abstractmethod
    def execute(
        self,
        context: AgentContext,
    ) -> AgentResult:
        ...

    @abstractmethod
    def supports(
        self,
        action: str,
    ) -> bool:
        ...

    @abstractmethod
    def snapshot(self) -> AgentResult:
        ...