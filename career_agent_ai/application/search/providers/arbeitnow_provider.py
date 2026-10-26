from __future__ import annotations

import json
import math
import re

from datetime import UTC, datetime
from html import unescape
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.employment_type import EmploymentType
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_source import JobSource
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.jobs.salary import Salary
from career_agent_ai.application.search.job_provider import JobProvider


def _reject_nonfinite_json_constant(value: str) -> None:
    """Reject non-standard numeric constants accepted by Python's JSON decoder."""
    raise ValueError(f"non-finite JSON constant: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build one JSON object while rejecting ambiguous duplicate member names."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member: {key}")
        result[key] = value
    return result


def _validate_json_depth(value: str, *, maximum: int = 100) -> None:
    """Reject excessive JSON container nesting without counting quoted brackets."""
    depth = 0
    in_string = False
    escaped = False
    for character in value:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > maximum:
                raise ValueError("JSON nesting exceeds limit")
        elif character in "]}":
            depth -= 1


def _validate_arbeitnow_url(response_url: object) -> None:
    """Reject any URL outside the exact Arbeitnow API origin and path."""
    if not isinstance(response_url, str):
        raise RuntimeError("Arbeitnow response URL is invalid")

    expected = urlsplit(ArbeitnowProvider.BASE_URL)
    actual = urlsplit(response_url)
    trusted_authorities = {
        expected.netloc,
        f"{expected.netloc}:443",
    }
    trusted_paths = {
        expected.path,
        f"{expected.path}/",
    }
    if (
        actual.scheme != "https"
        or actual.netloc not in trusted_authorities
        or actual.path not in trusted_paths
        or actual.fragment
    ):
        raise RuntimeError("Arbeitnow response URL is not trusted")


def _validate_content_length(response: Any, *, maximum: int) -> None:
    """Reject an invalid or oversized declared body before reading it."""
    headers = getattr(response, "headers", None)
    if headers is None:
        return
    try:
        get_all = getattr(headers, "get_all", None)
        if callable(get_all):
            values = get_all("Content-Length") or []
        else:
            value = headers.get("Content-Length")
            values = [] if value is None else [value]
    except Exception as exc:
        raise RuntimeError("Arbeitnow response headers are invalid") from exc
    if not values:
        return
    declared_lengths: set[int] = set()
    for value in values:
        if not isinstance(value, str) or not value.isascii() or not value.isdigit():
            raise RuntimeError("Arbeitnow Content-Length is invalid")
        declared = int(value)
        if declared > maximum:
            raise RuntimeError("Arbeitnow response exceeds byte limit")
        declared_lengths.add(declared)
    if len(declared_lengths) != 1:
        raise RuntimeError("Arbeitnow Content-Length values conflict")


def _validate_content_type(response: Any) -> None:
    """Require unambiguous JSON media declarations before reading a body."""
    headers = getattr(response, "headers", None)
    if headers is None:
        return
    try:
        get_all = getattr(headers, "get_all", None)
        if callable(get_all):
            values = get_all("Content-Type") or []
        else:
            value = headers.get("Content-Type")
            values = [] if value is None else [value]
    except Exception as exc:
        raise RuntimeError("Arbeitnow response headers are invalid") from exc
    if not values:
        return

    declarations: set[tuple[str, str | None]] = set()
    for value in values:
        if not isinstance(value, str) or not value.isascii():
            raise RuntimeError("Arbeitnow Content-Type is invalid")
        segments = tuple(segment.strip().lower() for segment in value.split(";"))
        media_type, parameters = segments[0], segments[1:]
        charset: str | None = None
        if len(parameters) == 1:
            name, separator, parameter_value = parameters[0].partition("=")
            parameter_value = parameter_value.strip()
            if (
                separator != "="
                or name.strip() != "charset"
                or parameter_value not in {"utf-8", '"utf-8"'}
            ):
                raise RuntimeError("Arbeitnow response is not JSON")
            charset = "utf-8"
        if media_type != "application/json" or len(parameters) > 1:
            raise RuntimeError("Arbeitnow response is not JSON")
        declarations.add((media_type, charset))
    if len(declarations) != 1:
        raise RuntimeError("Arbeitnow Content-Type values conflict")


def _validate_content_encoding(response: Any) -> None:
    """Reject encoded bodies before reading; the byte limit applies to wire bytes."""
    headers = getattr(response, "headers", None)
    if headers is None:
        return
    try:
        get_all = getattr(headers, "get_all", None)
        if callable(get_all):
            values = get_all("Content-Encoding") or []
        else:
            value = headers.get("Content-Encoding")
            values = [] if value is None else [value]
    except Exception as exc:
        raise RuntimeError("Arbeitnow response headers are invalid") from exc
    for value in values:
        if not isinstance(value, str) or not value.isascii():
            raise RuntimeError("Arbeitnow Content-Encoding is invalid")
        if value.strip().lower() != "identity":
            raise RuntimeError("Arbeitnow response encoding is unsupported")


def _safe_job_url(value: Any) -> str:
    """Keep only absolute HTTP(S) job links without embedded credentials."""
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if not candidate or any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in candidate
    ):
        return ""
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None and not 1 <= port <= 65535
    ):
        return ""
    return candidate


class _TrustedArbeitnowRedirectHandler(HTTPRedirectHandler):
    """Validate a redirect destination before urllib sends the next request."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        _validate_arbeitnow_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def urlopen(request: Request, *, timeout: float) -> Any:
    """Open one Arbeitnow request with fail-closed redirect validation."""
    return build_opener(_TrustedArbeitnowRedirectHandler()).open(
        request,
        timeout=timeout,
    )


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
        max_response_bytes: int = 2_000_000,
    ) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeout must be a finite positive number")
        try:
            normalized_timeout = float(timeout)
        except OverflowError as exc:
            raise ValueError("timeout must be a finite positive number") from exc
        if not math.isfinite(normalized_timeout) or normalized_timeout <= 0:
            raise ValueError("timeout must be a finite positive number")

        if type(max_pages) is not int or max_pages < 1:
            raise ValueError("max_pages must be a positive integer")

        if type(max_response_bytes) is not int or max_response_bytes < 1:
            raise ValueError("max_response_bytes must be a positive integer")

        self._timeout = normalized_timeout
        self._max_pages = max_pages
        self._max_response_bytes = max_response_bytes

    def search(
        self,
        query: str,
    ) -> tuple[Job, ...]:
        normalized_query = self._normalize_text(query)

        jobs: list[Job] = []
        accepted_job_ids: set[str] = set()

        for page in range(
            1,
            self._max_pages + 1,
        ):
            payload = self._fetch_page(
                query=normalized_query,
                page=page,
            )

            page_jobs, has_next = self._validate_page_envelope(
                payload,
                expected_next_page=page + 1,
            )

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

                if job.job_id in accepted_job_ids:
                    continue
                accepted_job_ids.add(job.job_id)
                jobs.append(job)

            if not has_next:
                break

        return tuple(jobs)

    @staticmethod
    def _validate_page_envelope(
        payload: dict[str, Any],
        *,
        expected_next_page: int,
    ) -> tuple[list[Any], bool]:
        """Validate pagination fields before accepting any page results."""
        page_jobs = payload.get("data")
        if not isinstance(page_jobs, list):
            raise RuntimeError("Arbeitnow API returned invalid page data")

        links = payload.get("links")
        if links is None:
            return page_jobs, False
        if not isinstance(links, dict):
            raise RuntimeError("Arbeitnow API returned invalid pagination links")

        next_page = links.get("next")
        if next_page is None:
            return page_jobs, False
        if not isinstance(next_page, str) or not next_page.strip():
            raise RuntimeError("Arbeitnow API returned invalid next-page state")
        try:
            _validate_arbeitnow_url(next_page)
            page_values = [
                value
                for key, value in parse_qsl(
                    urlsplit(next_page).query,
                    keep_blank_values=True,
                )
                if key == "page"
            ]
        except (RuntimeError, ValueError) as exc:
            raise RuntimeError(
                "Arbeitnow API returned invalid next-page state"
            ) from exc
        if page_values != [str(expected_next_page)]:
            raise RuntimeError("Arbeitnow API returned invalid next-page state")
        return page_jobs, True

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
                    None,
                )

                if type(status) is not int or not 100 <= status <= 599:
                    raise RuntimeError(
                        "Arbeitnow API returned an invalid HTTP status"
                    )

                if not 200 <= status < 300:
                    raise RuntimeError(
                        f"Arbeitnow API returned HTTP {status}"
                    )

                response_url = getattr(response, "geturl", lambda: url)()
                self._validate_response_url(response_url)

                _validate_content_type(response)
                _validate_content_encoding(response)
                _validate_content_length(
                    response,
                    maximum=self._max_response_bytes,
                )

                raw_body = response.read(self._max_response_bytes + 1)
                if not isinstance(raw_body, bytes):
                    raise RuntimeError("Arbeitnow API returned an invalid body")
                if len(raw_body) > self._max_response_bytes:
                    raise RuntimeError("Arbeitnow response exceeds byte limit")

        except Exception as exc:
            raise RuntimeError(
                "Failed to fetch jobs from "
                f"Arbeitnow: {exc}"
            ) from exc

        try:
            decoded_body = raw_body.decode("utf-8")
            _validate_json_depth(decoded_body)
            payload = json.loads(
                decoded_body,
                parse_constant=_reject_nonfinite_json_constant,
                object_pairs_hook=_unique_json_object,
            )
        except (
            UnicodeDecodeError,
            ValueError,
            RecursionError,
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

    def _validate_response_url(self, response_url: object) -> None:
        _validate_arbeitnow_url(response_url)

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

        url = _safe_job_url(
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
        raw_minimum = item.get("salary_min")
        raw_maximum = item.get("salary_max")
        minimum = cls._number(raw_minimum)
        maximum = cls._number(raw_maximum)

        if (
            (raw_minimum is not None and minimum is None)
            or (raw_maximum is not None and maximum is None)
        ):
            return None

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
                if minimum > maximum:
                    return None
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
            number = float(value)
            return number if math.isfinite(number) and number >= 0 else None

        text = str(value).strip()

        if not text:
            return None

        try:
            number = float(text)
        except (OverflowError, ValueError):
            return None
        return number if math.isfinite(number) and number >= 0 else None

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
