from __future__ import annotations

from career_agent_ai.application.agents.agent import Agent
from career_agent_ai.application.agents.agent_result import AgentResult
from career_agent_ai.application.brain.agent_context import AgentContext


class ResumeAgent(Agent):

    @property
    def id(self) -> str:
        return "resume"

    @property
    def name(self) -> str:
        return "Resume Agent"

    @property
    def version(self) -> str:
        return "1.0"

    @property
    def description(self) -> str:
        return "Handles resume operations."

    def execute(
        self,
        context: AgentContext,
    ) -> AgentResult:

        return AgentResult(
            success=True,
            agent_id=self.id,
            messages=("Resume Agent executed.",),
        )

    def supports(
        self,
        action: str,
    ) -> bool:
        return action == "resume"

    def snapshot(self) -> AgentResult:
        return AgentResult(
            success=True,
            agent_id=self.id,
        )