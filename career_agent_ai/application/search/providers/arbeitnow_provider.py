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


class ArbeitnowProvider(JobProvider):
    """
    Real job provider backed by the public Arbeitnow Job Board API.

    Responsibilities:
    - fetch real job postings;
    - normalize external API records;
    - preserve remote/location/employment/salary information;
    - perform conservative local relevance filtering;
    - expose stable internal Job identifiers.
    """

    BASE_URL = "https://www.arbeitnow.com/api/job-board-api"

    _GERMAN_CITIES = frozenset(
        {
            "berlin",
            "hamburg",
            "munich",
            "münchen",
            "frankfurt",
            "cologne",
            "köln",
            "stuttgart",
            "düsseldorf",
            "dusseldorf",
            "leipzig",
            "dresden",
            "nuremberg",
            "nürnberg",
            "hannover",
            "hanover",
            "bremen",
            "bonn",
            "mannheim",
            "karlsruhe",
            "freiburg",
            "augsburg",
            "mainz",
            "essen",
            "dortmund",
            "bochum",
            "wiesbaden",
            "heidelberg",
            "potsdam",
            "regensburg",
            "ulm",
            "saarbrücken",
            "saarbruecken",
            "erfurt",
            "eckernförde",
            "eschborn",
        }
    )

    _COUNTRY_ALIASES = {
        "germany": "Germany",
        "deutschland": "Germany",
        "de": "Germany",
        "austria": "Austria",
        "österreich": "Austria",
        "at": "Austria",
        "switzerland": "Switzerland",
        "schweiz": "Switzerland",
        "ch": "Switzerland",
        "netherlands": "Netherlands",
        "niederlande": "Netherlands",
        "nl": "Netherlands",
        "france": "France",
        "frankreich": "France",
        "fr": "France",
        "belgium": "Belgium",
        "belgien": "Belgium",
        "be": "Belgium",
        "united kingdom": "United Kingdom",
        "uk": "United Kingdom",
        "great britain": "United Kingdom",
        "ireland": "Ireland",
        "ire": "Ireland",
        "spain": "Spain",
        "italy": "Italy",
        "poland": "Poland",
        "czech republic": "Czech Republic",
        "czechia": "Czech Republic",
        "portugal": "Portugal",
        "sweden": "Sweden",
        "denmark": "Denmark",
        "norway": "Norway",
        "finland": "Finland",
    }

    _SALARY_PATTERNS = (
        re.compile(
            r"(?:€|eur)\s*"
            r"(\d{2,3}(?:[.\s]\d{3})?)"
            r"\s*(?:-|–|—|bis|to)\s*"
            r"(?:€|eur)?\s*"
            r"(\d{2,3}(?:[.\s]\d{3})?)"
            r"(?:\s*(?:pro\s*jahr|per\s*year|yearly|annual|jährlich|p\.a\.))?",
            re.IGNORECASE,
        ),
        re.compile(
            r"(\d{2,3}(?:[.\s]\d{3})?)"
            r"\s*(?:€|eur)"
            r"\s*(?:-|–|—|bis|to)\s*"
            r"(\d{2,3}(?:[.\s]\d{3})?)"
            r"\s*(?:€|eur)?",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:salary|gehalt|compensation|annual salary)"
            r".{0,40}?"
            r"(?:€|eur)?\s*"
            r"(\d{2,3}(?:[.\s]\d{3})?)"
            r"\s*(?:-|–|—|bis|to)\s*"
            r"(?:€|eur)?\s*"
            r"(\d{2,3}(?:[.\s]\d{3})?)",
            re.IGNORECASE | re.DOTALL,
        ),
    )

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        max_pages: int = 3,
    ) -> None:
        if timeout <= 0:
            raise ValueError(
                "timeout must be greater than 0"
            )

        if max_pages < 1:
            raise ValueError(
                "max_pages must be greater than or equal to 1"
            )

        self._timeout = timeout
        self._max_pages = max_pages

    def search(
        self,
        query: str,
    ) -> tuple[Job, ...]:
        normalized_query = self._normalize_text(query)

        jobs: list[Job] = []

        for page in range(
            1,
            self._max_pages + 1,
        ):
            payload = self._fetch_page(
                query=normalized_query,
                page=page,
            )

            page_jobs = payload.get(
                "data",
                [],
            )

            if not isinstance(
                page_jobs,
                list,
            ):
                break

            for item in page_jobs:
                if not isinstance(
                    item,
                    dict,
                ):
                    continue

                job = self._to_job(item)

                if job is None:
                    continue

                if normalized_query:
                    if not self._matches_query(
                        job,
                        normalized_query,
                    ):
                        continue

                jobs.append(job)

            links = payload.get("links")

            if not isinstance(
                links,
                dict,
            ):
                break

            if not links.get("next"):
                break

        return tuple(jobs)

    def _fetch_page(
        self,
        *,
        query: str,
        page: int,
    ) -> dict[str, Any]:
        parameters = {
            "page": str(page),
        }

        if query:
            parameters["search"] = query

        url = (
            f"{self.BASE_URL}"
            f"?{urlencode(parameters)}"
        )

        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "CareerAgentAI/1.0",
            },
            method="GET",
        )

        try:
            with urlopen(
                request,
                timeout=self._timeout,
            ) as response:
                status = getattr(
                    response,
                    "status",
                    200,
                )

                if status < 200 or status >= 300:
                    raise RuntimeError(
                        f"Arbeitnow API returned HTTP {status}"
                    )

                raw_body = response.read()

        except Exception as exc:
            raise RuntimeError(
                "Failed to fetch jobs from "
                f"Arbeitnow: {exc}"
            ) from exc

        try:
            payload = json.loads(
                raw_body.decode("utf-8")
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise RuntimeError(
                "Arbeitnow API returned invalid JSON"
            ) from exc

        if not isinstance(
            payload,
            dict,
        ):
            raise RuntimeError(
                "Arbeitnow API returned "
                "an unexpected response"
            )

        return payload

    def _to_job(
        self,
        item: dict[str, Any],
    ) -> Job | None:
        title = self._string(
            item.get("title")
        )

        if not title:
            return None

        company_name = self._string(
            item.get("company_name")
        )

        if not company_name:
            company_name = "Unknown company"

        raw_location = self._string(
            item.get("location")
        )

        description = self._clean_html(
            self._string(
                item.get("description")
            )
        )

        tags = item.get("tags")

        remote = self._is_remote(
            item.get("remote"),
            raw_location,
            tags,
        )

        city = self._extract_city(
            raw_location,
            remote=remote,
        )

        country = self._extract_country(
            raw_location,
            city=city,
            remote=remote,
            description=description,
        )

        url = self._string(
            item.get("url")
        )

        slug = self._string(
            item.get("slug")
        )

        external_id = slug or url

        job_id = self._build_job_id(
            external_id=external_id,
            title=title,
            company=company_name,
            city=city,
        )

        salary = self._extract_salary(
            item,
            description,
        )

        employment_type = (
            self._extract_employment_type(
                item,
            )
        )

        created_at = (
            self._extract_created_at(
                item,
            )
        )

        return Job.create(
            job_id=job_id,
            title=title,
            company=Company(
                company_id=self._build_company_id(
                    company_name,
                ),
                name=company_name,
            ),
            location=Location(
                country=country,
                city=city,
                remote=remote,
            ),
            salary=salary,
            employment_type=employment_type,
            source=JobSource.OTHER,
            url=url,
            description=description,
            created_at=created_at,
        )

    @staticmethod
    def _string(
        value: Any,
    ) -> str:
        if value is None:
            return ""

        return str(value).strip()

    @staticmethod
    def _normalize_text(
        value: str,
    ) -> str:
        return " ".join(
            value.strip().lower().split()
        )

    @staticmethod
    def _clean_html(
        value: str,
    ) -> str:
        if not value:
            return ""

        text = re.sub(
            r"<br\s*/?>",
            "\n",
            value,
            flags=re.IGNORECASE,
        )

        text = re.sub(
            r"</p\s*>",
            "\n",
            text,
            flags=re.IGNORECASE,
        )

        text = re.sub(
            r"<[^>]+>",
            " ",
            text,
        )

        text = unescape(text)

        return " ".join(
            text.split()
        )

    @staticmethod
    def _is_remote(
        remote_value: Any,
        location: str,
        tags: Any,
    ) -> bool:
        if isinstance(
            remote_value,
            bool,
        ):
            if remote_value:
                return True

        if isinstance(
            remote_value,
            str,
        ):
            normalized = (
                remote_value
                .strip()
                .lower()
            )

            if normalized in {
                "true",
                "yes",
                "remote",
                "1",
            }:
                return True

        if "remote" in location.lower():
            return True

        if isinstance(
            tags,
            list,
        ):
            for tag in tags:
                if (
                    "remote"
                    in str(tag).lower()
                ):
                    return True

        return False

    @classmethod
    def _extract_city(
        cls,
        location: str,
        *,
        remote: bool,
    ) -> str:
        normalized = location.strip()

        if not normalized:
            return ""

        if remote:
            return "Remote"

        parts = [
            part.strip()
            for part in normalized.split(",")
            if part.strip()
        ]

        if parts:
            first = parts[0]

            if (
                first.lower()
                not in cls._COUNTRY_ALIASES
            ):
                return first

        return normalized

    @classmethod
    def _extract_country(
        cls,
        location: str,
        *,
        city: str,
        remote: bool,
        description: str,
    ) -> str:
        normalized = location.strip()

        if normalized:
            parts = [
                part.strip()
                for part in normalized.split(",")
                if part.strip()
            ]

            for part in reversed(parts):
                alias = cls._COUNTRY_ALIASES.get(
                    part.lower()
                )

                if alias:
                    return alias

            location_lower = normalized.lower()

            for key, country in (
                cls._COUNTRY_ALIASES.items()
            ):
                if re.search(
                    rf"\b{re.escape(key)}\b",
                    location_lower,
                ):
                    return country

        city_normalized = (
            city.strip().lower()
        )

        if (
            city_normalized
            in cls._GERMAN_CITIES
        ):
            return "Germany"

        if remote:
            text = (
                f"{normalized} "
                f"{description}"
            ).lower()

            if (
                "germany" in text
                or "deutschland" in text
                or "german market" in text
            ):
                return "Germany"

        return ""

    @classmethod
    def _matches_query(
        cls,
        job: Job,
        query: str,
    ) -> bool:
        terms = [
            term
            for term in query.split()
            if term
        ]

        if not terms:
            return True

        title = cls._normalize_text(
            job.title
        )

        company = cls._normalize_text(
            getattr(
                job.company,
                "name",
                "",
            )
        )

        description = cls._normalize_text(
            job.description
        )

        tags_text = ""

        searchable = " ".join(
            (
                title,
                company,
                description,
                tags_text,
            )
        )

        return all(
            term in searchable
            for term in terms
        )

    @classmethod
    def _extract_salary(
        cls,
        item: dict[str, Any],
        description: str,
    ) -> Salary | None:
        minimum = cls._number(
            item.get("salary_min")
        )

        maximum = cls._number(
            item.get("salary_max")
        )

        if (
            minimum is not None
            or maximum is not None
        ):
            if minimum is None:
                minimum = maximum

            if maximum is None:
                maximum = minimum

            if (
                minimum is not None
                and maximum is not None
            ):
                return Salary(
                    minimum=int(minimum),
                    maximum=int(maximum),
                    currency="EUR",
                )

        for pattern in cls._SALARY_PATTERNS:
            match = pattern.search(
                description
            )

            if not match:
                continue

            parsed_minimum = cls._parse_money(
                match.group(1)
            )

            parsed_maximum = cls._parse_money(
                match.group(2)
            )

            if (
                parsed_minimum is None
                or parsed_maximum is None
            ):
                continue

            if (
                parsed_minimum < 10_000
                or parsed_maximum < 10_000
            ):
                continue

            return Salary(
                minimum=parsed_minimum,
                maximum=parsed_maximum,
                currency="EUR",
            )

        return None

    @staticmethod
    def _number(
        value: Any,
    ) -> float | None:
        if value is None:
            return None

        if isinstance(
            value,
            bool,
        ):
            return None

        if isinstance(
            value,
            (int, float),
        ):
            return float(value)

        text = str(value).strip()

        if not text:
            return None

        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _parse_money(
        value: str,
    ) -> int | None:
        normalized = (
            value
            .replace(".", "")
            .replace(" ", "")
            .replace(",", "")
        )

        try:
            return int(normalized)
        except ValueError:
            return None

    @staticmethod
    def _extract_employment_type(
        item: dict[str, Any],
    ) -> EmploymentType | None:
        values: list[str] = []

        job_types = item.get(
            "job_types"
        )

        if isinstance(
            job_types,
            list,
        ):
            values.extend(
                str(value).strip().lower()
                for value in job_types
            )

        elif isinstance(
            job_types,
            str,
        ):
            values.append(
                job_types.strip().lower()
            )

        text = " ".join(values)

        if (
            "full-time" in text
            or (
                "full" in text
                and "time" in text
            )
            or "vollzeit" in text
        ):
            return EmploymentType.FULL_TIME

        if (
            "part-time" in text
            or (
                "part" in text
                and "time" in text
            )
            or "teilzeit" in text
        ):
            return EmploymentType.PART_TIME

        if "contract" in text:
            return EmploymentType.CONTRACT

        if "intern" in text:
            return EmploymentType.INTERNSHIP

        return None

    @staticmethod
    def _extract_created_at(
        item: dict[str, Any],
    ) -> datetime | None:
        created_at_iso = item.get(
            "created_at_iso"
        )

        if isinstance(
            created_at_iso,
            str,
        ):
            value = created_at_iso.strip()

            if value:
                try:
                    return datetime.fromisoformat(
                        value.replace(
                            "Z",
                            "+00:00",
                        )
                    )
                except ValueError:
                    pass

        created_at = item.get(
            "created_at"
        )

        if isinstance(
            created_at,
            (int, float),
        ):
            try:
                return datetime.fromtimestamp(
                    created_at,
                    tz=UTC,
                )
            except (
                OverflowError,
                OSError,
                ValueError,
            ):
                return None

        return None

    @staticmethod
    def _build_job_id(
        *,
        external_id: str,
        title: str,
        company: str,
        city: str,
    ) -> str:
        if external_id:
            return f"arbeitnow:{external_id}"

        raw = "|".join(
            (
                title.strip().lower(),
                company.strip().lower(),
                city.strip().lower(),
            )
        )

        return f"arbeitnow:{raw}"

    @staticmethod
    def _build_company_id(
        company: str,
    ) -> str:
        normalized = (
            company.strip().lower()
        )

        if not normalized:
            return "arbeitnow:unknown"

        return f"arbeitnow:{normalized}"