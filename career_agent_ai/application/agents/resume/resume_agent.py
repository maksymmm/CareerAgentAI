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
        raw_profile = context.payload.get("candidate_profile")
        if raw_profile is None or (isinstance(raw_profile, str) and not raw_profile.strip()):
            profile = None
            artifact = (
                f"Candidate: {candidate}\n"
                f"Target role: {job_title}\n"
                f"Company: {company}\n"
                f"Job ID: {job_id}"
            )
        else:
            profile = self._profile(raw_profile)
            artifact = (
                f"Candidate: {candidate}\n"
                f"Target role: {job_title}\n"
                f"Company: {company}\n"
                f"Job ID: {job_id}\n\n"
                f"Candidate profile:\n{profile}"
            )
        return AgentResult(
            success=True,
            agent_id=self.id,
            messages=("Resume Agent executed.",),
            metadata={
                "application_artifact": artifact,
                "application_artifact_type": "job_application_profile",
                "candidate_profile_included": profile is not None,
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
    def _profile(value) -> str:
        """Return validated candidate-supplied resume/profile text without fabrication."""
        if not isinstance(value, str):
            raise TypeError("candidate_profile must be text.")
        candidate = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not candidate:
            raise ValueError("candidate_profile must not be empty.")
        if len(candidate) > 50_000:
            raise ValueError("candidate_profile must not exceed 50000 characters.")
        if any(ord(ch) < 32 and ch not in "\n\t" for ch in candidate):
            raise ValueError("candidate_profile contains forbidden control characters.")
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in candidate):
            raise ValueError("candidate_profile contains a forbidden Unicode surrogate.")
        return candidate

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
