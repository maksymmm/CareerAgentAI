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
        """Prepare an explicit inspectable application artifact for one job."""
        job_id = self._text(context.payload.get("job_id"), "Unknown job")
        job_title = self._text(context.payload.get("job_title"), "Unknown role")
        company = self._text(context.payload.get("company"), "Unknown company")
        candidate = self._text(context.user_id, "Unknown candidate")
        artifact = (
            f"Candidate: {candidate}\n"
            f"Role: {job_title}\n"
            f"Company: {company}\n"
            f"Job ID: {job_id}"
        )
        return AgentResult(
            success=True,
            agent_id=self.id,
            messages=("Resume Agent executed.",),
            metadata={
                "application_artifact": artifact,
                "application_artifact_type": "job_application_profile",
            },
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

    @staticmethod
    def _text(value, fallback: str) -> str:
        """Return bounded printable text for an application artifact field."""
        candidate = str(value or "").strip()
        if not candidate:
            return fallback
        candidate = candidate[:500]
        if any(ord(ch) < 32 and ch not in "\n\t" for ch in candidate):
            raise ValueError("Resume artifact field contains forbidden control characters.")
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in candidate):
            raise ValueError("Resume artifact field contains a forbidden Unicode surrogate.")
        return candidate
