from __future__ import annotations

import re
from dataclasses import dataclass

from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_filter import JobFilter


@dataclass(frozen=True)
class RelevanceScore:
    """
    Detailed deterministic relevance score for a job.

    The individual components are intentionally exposed so that
    the scoring system can later be replaced or augmented by an
    LLM/embedding-based matcher without changing the search API.
    """

    total: float
    title: float = 0.0
    skills: float = 0.0
    location: float = 0.0
    remote: float = 0.0
    salary: float = 0.0
    employment: float = 0.0


class JobRelevanceScorer:
    """
    Deterministic job-to-filter relevance scorer.

    The title is deliberately weighted much more strongly than the
    description. This prevents a job from ranking highly merely
    because a requested technology is mentioned somewhere in a long
    description.
    """

    TITLE_EXACT = 50.0
    TITLE_TERM = 15.0
    TITLE_PARTIAL = 8.0

    DESCRIPTION_TERM = 1.5
    DESCRIPTION_PHRASE = 4.0

    COMPANY_TERM = 2.0

    LOCATION_COUNTRY = 10.0
    LOCATION_CITY = 15.0

    REMOTE_MATCH = 12.0
    REMOTE_MISMATCH = -15.0

    EMPLOYMENT_MATCH = 8.0
    EMPLOYMENT_MISMATCH = -8.0

    SALARY_MINIMUM_MATCH = 8.0
    SALARY_MAXIMUM_MATCH = 8.0
    SALARY_UNKNOWN = -2.0

    def score(
        self,
        job: Job,
        filters: JobFilter,
    ) -> RelevanceScore:
        title_score = self._score_title(
            job,
            filters.keyword,
        )

        skills_score = self._score_description(
            job,
            filters.keyword,
        )

        location_score = self._score_location(
            job,
            filters,
        )

        remote_score = self._score_remote(
            job,
            filters,
        )

        salary_score = self._score_salary(
            job,
            filters,
        )

        employment_score = self._score_employment(
            job,
            filters,
        )

        total = (
            title_score
            + skills_score
            + location_score
            + remote_score
            + salary_score
            + employment_score
        )

        return RelevanceScore(
            total=total,
            title=title_score,
            skills=skills_score,
            location=location_score,
            remote=remote_score,
            salary=salary_score,
            employment=employment_score,
        )

    def _score_title(
        self,
        job: Job,
        keyword: str,
    ) -> float:
        normalized_keyword = self._normalize(
            keyword
        )

        if not normalized_keyword:
            return 0.0

        title = self._normalize(
            job.title
        )

        if not title:
            return 0.0

        if normalized_keyword in title:
            score = self.TITLE_EXACT
        else:
            score = 0.0

        terms = self._terms(
            normalized_keyword
        )

        for term in terms:
            if term in title:
                score += self.TITLE_TERM

        return score

    def _score_description(
        self,
        job: Job,
        keyword: str,
    ) -> float:
        normalized_keyword = self._normalize(
            keyword
        )

        if not normalized_keyword:
            return 0.0

        description = self._normalize(
            job.description
        )

        if not description:
            return 0.0

        score = 0.0

        if normalized_keyword in description:
            score += self.DESCRIPTION_PHRASE

        for term in self._terms(
            normalized_keyword
        ):
            if term in description:
                score += self.DESCRIPTION_TERM

        return score

    def _score_location(
        self,
        job: Job,
        filters: JobFilter,
    ) -> float:
        location = getattr(
            job,
            "location",
            None,
        )

        if location is None:
            return 0.0

        score = 0.0

        requested_country = self._normalize(
            filters.country
        )

        requested_city = self._normalize(
            filters.city
        )

        job_country = self._normalize(
            getattr(
                location,
                "country",
                "",
            )
        )

        job_city = self._normalize(
            getattr(
                location,
                "city",
                "",
            )
        )

        if requested_country:
            if requested_country == job_country:
                score += self.LOCATION_COUNTRY
            elif requested_country in job_country:
                score += self.LOCATION_COUNTRY / 2

        if requested_city:
            if requested_city == job_city:
                score += self.LOCATION_CITY
            elif requested_city in job_city:
                score += self.LOCATION_CITY / 2

        return score

    def _score_remote(
        self,
        job: Job,
        filters: JobFilter,
    ) -> float:
        if not filters.remote_only:
            return 0.0

        location = getattr(
            job,
            "location",
            None,
        )

        if location is None:
            return self.REMOTE_MISMATCH

        if getattr(
            location,
            "remote",
            False,
        ):
            return self.REMOTE_MATCH

        return self.REMOTE_MISMATCH

    def _score_salary(
        self,
        job: Job,
        filters: JobFilter,
    ) -> float:
        if (
            filters.minimum_salary is None
            and filters.maximum_salary is None
        ):
            return 0.0

        salary = getattr(
            job,
            "salary",
            None,
        )

        if salary is None:
            return self.SALARY_UNKNOWN

        score = 0.0

        minimum = getattr(
            salary,
            "minimum",
            None,
        )

        maximum = getattr(
            salary,
            "maximum",
            None,
        )

        if (
            filters.minimum_salary is not None
            and maximum is not None
            and maximum >= filters.minimum_salary
        ):
            score += self.SALARY_MINIMUM_MATCH

        if (
            filters.maximum_salary is not None
            and minimum is not None
            and minimum <= filters.maximum_salary
        ):
            score += self.SALARY_MAXIMUM_MATCH

        return score

    def _score_employment(
        self,
        job: Job,
        filters: JobFilter,
    ) -> float:
        if filters.employment_type is None:
            return 0.0

        if (
            job.employment_type
            == filters.employment_type
        ):
            return self.EMPLOYMENT_MATCH

        return self.EMPLOYMENT_MISMATCH

    @staticmethod
    def _normalize(
        value: str,
    ) -> str:
        value = str(
            value or ""
        ).lower()

        value = value.replace(
            "–",
            "-",
        ).replace(
            "—",
            "-",
        )

        value = re.sub(
            r"\s+",
            " ",
            value,
        )

        return value.strip()

    @staticmethod
    def _terms(
        value: str,
    ) -> tuple[str, ...]:
        return tuple(
            term
            for term in re.findall(
                r"[a-z0-9äöüß+#.-]+",
                value,
            )
            if term
        )