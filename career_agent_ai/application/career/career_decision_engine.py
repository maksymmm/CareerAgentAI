from __future__ import annotations

from typing import Any, Mapping

from career_agent_ai.application.career.career_decision import CareerDecision


class CareerDecisionEngine:
    """Selects the next career action from an objective and current context.

    The engine is deliberately deterministic and provider-independent. A future
    LLM or ML planner can implement the same decision contract without changing
    the orchestrator.
    """

    _ACTION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
        (
            "job_application",
            ("apply", "application", "bewerben", "bewerbung"),
        ),
        (
            "resume",
            ("resume", "cv", "lebenslauf", "curriculum vitae"),
        ),
        (
            "job_search",
            (
                "job",
                "jobs",
                "role",
                "roles",
                "position",
                "positions",
                "vacancy",
                "vacancies",
                "stellen",
                "stelle",
                "career",
            ),
        ),
    )

    def decide(
        self,
        objective: str,
        payload: Mapping[str, Any] | None = None,
    ) -> CareerDecision:
        normalized = objective.strip()
        if not normalized:
            raise ValueError("objective must not be empty.")

        data = dict(payload or {})
        explicit = self._explicit_action(data)
        if explicit is not None:
            return CareerDecision(
                action=explicit,
                reason="The requested action was explicitly provided.",
                confidence=1.0,
                metadata={"source": "explicit"},
            )

        text = normalized.casefold()

        if any(
            keyword in text
            for keyword in ("apply", "application", "bewerb", "bewerbung")
        ):
            return CareerDecision(
                action="job_application",
                reason="The objective explicitly describes applying for a job.",
                metadata={"source": "objective"},
            )

        if any(
            keyword in text
            for keyword in ("resume", "cv", "lebenslauf")
        ):
            return CareerDecision(
                action="resume",
                reason="The objective explicitly describes resume work.",
                metadata={"source": "objective"},
            )

        if any(
            keyword in text
            for keyword in (
                "find",
                "search",
                "discover",
                "looking for",
                "suche",
                "finden",
                "job",
                "position",
                "stelle",
            )
        ):
            return CareerDecision(
                action="job_search",
                reason="The objective requires discovering suitable opportunities.",
                metadata={"source": "objective"},
            )

        return CareerDecision(
            action="job_search",
            reason="No stronger action was identified, so opportunity discovery is the safest first step.",
            confidence=0.75,
            metadata={"source": "default"},
        )

    def next_action(
        self,
        objective: str,
        completed_actions: tuple[str, ...] = (),
        payload: Mapping[str, Any] | None = None,
    ) -> CareerDecision:
        """Choose the next action while avoiding completed work when possible."""

        decision = self.decide(objective, payload)
        completed = set(completed_actions)

        if decision.action not in completed:
            return decision

        if decision.action == "job_search" and "resume" not in completed:
            return CareerDecision(
                action="resume",
                reason="Job discovery is already complete; resume preparation is the next useful step.",
                confidence=0.8,
                metadata={"source": "progression"},
            )

        if decision.action == "resume" and "job_application" not in completed:
            return CareerDecision(
                action="job_application",
                reason="Resume work is already complete; the next useful step is application processing.",
                confidence=0.8,
                metadata={"source": "progression"},
            )

        return CareerDecision(
            action="job_search",
            reason="The primary actions are already complete; refresh opportunity discovery for the next cycle.",
            confidence=0.6,
            metadata={"source": "cycle"},
        )

    @staticmethod
    def _explicit_action(payload: Mapping[str, Any]) -> str | None:
        value = payload.get("action")
        if value is None:
            return None

        action = str(value).strip()
        return action or None
