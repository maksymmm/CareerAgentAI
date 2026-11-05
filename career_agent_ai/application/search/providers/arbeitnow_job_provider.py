from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from html import unescape
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.employment_type import EmploymentType
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_source import JobSource
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.jobs.salary import Salary
from career_agent_ai.application.search.job_provider import JobProvider


class ArbeitnowJobProvider(JobProvider):
    """
    Real job provider backed by the public Arbeitnow Job Board API.

    The provider:
    - downloads real job postings;
    - converts external JSON into our internal Job model;
    - filters by the requested text locally;
    - supports multiple API pages;
    - never requires an API key.
    """

    BASE_URL = "https://www.arbeitnow.com/api/job-board-api"

    def __init__(
        self,
        *,
        timeout: int = 15,
        max_pages: int = 3,
    ) -> None:
        self._timeout = timeout
        self._max_pages = max(1, max_pages)

    def search(
        self,
        query: str,
    ) -> tuple[Job, ...]:
        normalized_query = query.strip().lower()

        jobs: list[Job] = []

        for page in range(1, self._max_pages + 1):
            payload = self._fetch_page(page)

            for item in payload:
                job = self._to_job(item)

                if normalized_query and not self._matches(
                    job,
                    normalized_query,
                ):
                    continue

                jobs.append(job)

        return tuple(jobs)

    def _fetch_page(
        self,
        page: int,
    ) -> list[dict[str, Any]]:
        query = urlencode(
            {
                "page": page,
            }
        )

        request = Request(
            f"{self.BASE_URL}?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": "CareerAgentAI/1.0",
            },
            method="GET",
        )

        with urlopen(
            request,
            timeout=self._timeout,
        ) as response:
            raw = response.read()

        payload = json.loads(
            raw.decode("utf-8")
        )

        if not isinstance(payload, dict):
            return []

        data = payload.get("data", [])

        if not isinstance(data, list):
            return []

        return [
            item
            for item in data
            if isinstance(item, dict)
        ]

    def _to_job(
        self,
        item: dict[str, Any],
    ) -> Job:
        title = self._string(
            item.get("title")
        )

        company_name = self._string(
            item.get("company_name")
        )

        city = self._string(
            item.get("location")
        )

        description = self._clean_html(
            self._string(
                item.get("description")
            )
        )

        url = self._string(
            item.get("url")
        )

        slug = self._string(
            item.get("slug")
        )

        job_id = (
            f"arbeitnow:{slug}"
            if slug
            else f"arbeitnow:{url}"
        )

        remote = bool(
            item.get("remote", False)
        )

        salary = self._extract_salary(
            description
        )

        employment_type = (
            self._extract_employment_type(
                item.get("job_types")
            )
        )

        created_at = self._created_at(
            item.get("created_at")
        )

        tags = item.get("tags", [])

        if isinstance(tags, list):
            tag_text = " ".join(
                self._string(tag)
                for tag in tags
            )
        else:
            tag_text = ""

        full_description = (
            f"{description}\n{tag_text}"
        ).strip()

        return Job.create(
            job_id=job_id,
            title=title,
            company=Company(
                company_id=(
                    f"arbeitnow:{company_name.lower()}"
                ),
                name=company_name,
            ),
            location=Location(
                country="",
                city=city,
                remote=remote,
            ),
            salary=salary,
            employment_type=employment_type,
            source=JobSource.OTHER,
            url=url,
            description=full_description,
            created_at=created_at,
        )

    def _matches(
        self,
        job: Job,
        query: str,
    ) -> bool:
        haystack = " ".join(
            (
                job.title,
                job.company.name,
                job.location.city,
                job.description,
            )
        ).lower()

        terms = [
            term
            for term in re.split(
                r"\s+",
                query,
            )
            if term
        ]

        return all(
            term in haystack
            for term in terms
        )

    @staticmethod
    def _string(
        value: Any,
    ) -> str:
        if value is None:
            return ""

        return str(value).strip()

    @staticmethod
    def _clean_html(
        value: str,
    ) -> str:
        value = unescape(value)

        value = re.sub(
            r"<br\s*/?>",
            "\n",
            value,
            flags=re.IGNORECASE,
        )

        value = re.sub(
            r"</p\s*>",
            "\n",
            value,
            flags=re.IGNORECASE,
        )

        value = re.sub(
            r"</li\s*>",
            "\n",
            value,
            flags=re.IGNORECASE,
        )

        value = re.sub(
            r"<[^>]+>",
            " ",
            value,
        )

        value = re.sub(
            r"[ \t]+",
            " ",
            value,
        )

        value = re.sub(
            r"\n\s*\n+",
            "\n",
            value,
        )

        return value.strip()

    @staticmethod
    def _created_at(
        value: Any,
    ) -> datetime | None:
        if isinstance(
            value,
            (int, float),
        ):
            return datetime.fromtimestamp(
                value,
                tz=UTC,
            )

        if isinstance(value, str):
            try:
                return datetime.fromisoformat(
                    value.replace(
                        "Z",
                        "+00:00",
                    )
                )
            except ValueError:
                return None

        return None

    @staticmethod
    def _extract_salary(
        description: str,
    ) -> Salary:
        """
        Extract a simple annual EUR salary range from
        the job description.

        This is intentionally conservative.
        If the description does not clearly contain
        a salary range, the result is empty.
        """

        patterns = (
            r"€\s?(\d{2,3}(?:[.,]\d{3})?)"
            r"\s*(?:-|–|bis)\s*"
            r"€?\s?(\d{2,3}(?:[.,]\d{3})?)",

            r"(\d{2,3}(?:[.,]\d{3})?)"
            r"\s*€?\s*(?:-|–|bis)\s*"
            r"(\d{2,3}(?:[.,]\d{3})?)"
            r"\s*€",
        )

        for pattern in patterns:
            match = re.search(
                pattern,
                description,
                flags=re.IGNORECASE,
            )

            if not match:
                continue

            minimum = ArbeitnowJobProvider._parse_money(
                match.group(1)
            )

            maximum = ArbeitnowJobProvider._parse_money(
                match.group(2)
            )

            if minimum is None or maximum is None:
                continue

            return Salary(
                minimum=minimum,
                maximum=maximum,
                currency="EUR",
            )

        return Salary(
            minimum=None,
            maximum=None,
            currency="EUR",
        )

    @staticmethod
    def _parse_money(
        value: str,
    ) -> int | None:
        normalized = value.replace(
            ".",
            "",
        ).replace(
            ",",
            "",
        )

        try:
            return int(normalized)
        except ValueError:
            return None

    @staticmethod
    def _extract_employment_type(
        value: Any,
    ) -> EmploymentType | None:
        if not isinstance(value, list):
            return None

        text = " ".join(
            str(item).lower()
            for item in value
        )

        if (
            "full time" in text
            or "full-time" in text
            or "vollzeit" in text
        ):
            return EmploymentType.FULL_TIME

        return None