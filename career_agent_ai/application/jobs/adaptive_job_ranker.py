"""Outcome-aware, explainable job ranking without external model dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_application import JobApplication
from career_agent_ai.application.jobs.job_application_repository import JobApplicationRepository
from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus
from career_agent_ai.application.jobs.job_filter import JobFilter
from career_agent_ai.application.jobs.job_ranker import JobRanker


@dataclass(frozen=True)
class AdaptiveJobScore:
    """An explainable score combining job relevance with observed outcomes."""

    job: Job
    relevance_score: float
    outcome_adjustment: float
    score: float
    evidence_count: int
    reason: str


class AdaptiveJobRanker:
    """Re-rank jobs using bounded candidate-specific employer outcome evidence.

    Sparse history is deliberately shrunk toward zero. Outcome evidence can refine
    relevance ordering, but cannot overwhelm the job-to-request relevance score.
    """

    MAX_ADJUSTMENT = 8.0
    PRIOR_STRENGTH = 3.0

    def __init__(
        self,
        applications: JobApplicationRepository,
        ranker: JobRanker | None = None,
    ) -> None:
        self._applications = applications
        self._ranker = ranker or JobRanker()

    def rank(
        self,
        user_id: str,
        jobs: tuple[Job, ...],
        filters: JobFilter | None = None,
    ) -> tuple[AdaptiveJobScore, ...]:
        """Return deterministic scores adapted only from this candidate's history."""
        normalized_user_id = self._required_user_id(user_id)
        if not jobs:
            return ()

        history = self._applications.list(normalized_user_id)
        by_company: dict[str, list[float]] = {}
        for application in history:
            signal = self._outcome_signal(application)
            if self._is_identifying_company_id(application.company_id) and signal is not None:
                by_company.setdefault(application.company_id, []).append(signal)

        results: list[AdaptiveJobScore] = []
        for baseline in self._ranker.rank(jobs, filters):
            company_id = self._company_id(baseline.job)
            evidence = by_company.get(company_id, [])
            adjustment = self._adjustment(evidence, baseline.score)
            results.append(
                AdaptiveJobScore(
                    job=baseline.job,
                    relevance_score=baseline.score,
                    outcome_adjustment=adjustment,
                    score=baseline.score + adjustment,
                    evidence_count=len(evidence),
                    reason=self._reason(evidence, adjustment),
                )
            )

        # Python's sort is stable, so an adaptive tie preserves every secondary
        # ordering decision already made by the wrapped ranker (including salary).
        results.sort(key=lambda item: item.score, reverse=True)
        return tuple(results)

    @classmethod
    def _adjustment(cls, evidence: list[float], relevance_score: float) -> float:
        if not evidence:
            return 0.0
        mean = sum(evidence) / len(evidence)
        confidence = len(evidence) / (len(evidence) + cls.PRIOR_STRENGTH)
        # The wrapped ranker has two public scales: legacy results are commonly
        # 0-2 while filter relevance can exceed 50. Cap against the active score
        # so history refines either scale instead of overwhelming it. Applying
        # confidence to that cap preserves the sparse-to-dense evidence gradient.
        active_cap = min(cls.MAX_ADJUSTMENT, max(abs(relevance_score) * 0.1, 0.01))
        return round(active_cap * mean * confidence, 6)

    @staticmethod
    def _outcome_signal(application: JobApplication) -> float | None:
        if application.status is JobApplicationStatus.WITHDRAWN:
            return None
        statuses = {event.to_status for event in application.timeline}
        statuses.add(application.status)
        if JobApplicationStatus.OFFER in statuses:
            return 1.0
        if JobApplicationStatus.INTERVIEW in statuses:
            return 0.5
        if application.status is JobApplicationStatus.REJECTED:
            return -0.25
        return None

    @staticmethod
    def _company_id(job: Job) -> str:
        value = getattr(getattr(job, "company", None), "company_id", "")
        normalized = value.strip() if isinstance(value, str) else ""
        return normalized if AdaptiveJobRanker._is_identifying_company_id(normalized) else ""

    @staticmethod
    def _is_identifying_company_id(value: str) -> bool:
        if not isinstance(value, str) or not value.strip():
            return False
        label = " ".join(value.rsplit(":", 1)[-1].lower().split())
        return label not in {"unknown", "unknown company", "n/a", "not disclosed"}

    @staticmethod
    def _reason(evidence: list[float], adjustment: float) -> str:
        if not evidence:
            return "No comparable candidate outcome evidence; relevance score unchanged."
        direction = "increased" if adjustment > 0 else "decreased" if adjustment < 0 else "unchanged"
        return (
            f"Score {direction} by bounded evidence from {len(evidence)} "
            "prior application outcome(s) at this employer."
        )

    @staticmethod
    def _required_user_id(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("user_id must not be empty.")
        return value.strip()
