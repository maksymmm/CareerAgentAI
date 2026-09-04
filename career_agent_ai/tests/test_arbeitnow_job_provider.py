from __future__ import annotations

import json
from unittest.mock import patch

from career_agent_ai.application.jobs.job_source import JobSource
from career_agent_ai.application.search.providers.arbeitnow_job_provider import (
    ArbeitnowJobProvider,
)


class FakeResponse:
    def __init__(
        self,
        payload: dict,
    ) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        return False

    def read(self):
        return json.dumps(
            self._payload
        ).encode("utf-8")


def test_arbeitnow_provider_maps_api_job():
    payload = {
        "data": [
            {
                "slug": "python-developer-openai",
                "company_name": "OpenAI",
                "title": "Python Developer",
                "description": (
                    "<p>Python developer.</p>"
                    "<p>70.000 € - 90.000 €</p>"
                ),
                "remote": False,
                "url": (
                    "https://example.com/job"
                ),
                "location": "Berlin",
                "tags": [
                    "Python",
                    "Backend",
                ],
                "job_types": [
                    "Full Time",
                ],
                "created_at": 1700000000,
            }
        ]
    }

    def fake_urlopen(
        request,
        timeout,
    ):
        return FakeResponse(payload)

    with patch(
        "career_agent_ai.application.search.providers."
        "arbeitnow_job_provider.urlopen",
        fake_urlopen,
    ):
        provider = ArbeitnowJobProvider(
            max_pages=1
        )

        result = provider.search(
            "python"
        )

    assert len(result) == 1

    job = result[0]

    assert job.job_id == (
        "arbeitnow:python-developer-openai"
    )

    assert job.title == "Python Developer"

    assert job.company.name == "OpenAI"

    assert job.location.city == "Berlin"

    assert job.source == JobSource.OTHER

    assert job.url == (
        "https://example.com/job"
    )

    assert job.salary.minimum == 70000

    assert job.salary.maximum == 90000


def test_arbeitnow_provider_filters_query():
    payload = {
        "data": [
            {
                "slug": "python-developer",
                "company_name": "OpenAI",
                "title": "Python Developer",
                "description": "Python backend role.",
                "remote": False,
                "url": "https://example.com/python",
                "location": "Berlin",
                "tags": [],
                "job_types": [],
                "created_at": 1700000000,
            },
            {
                "slug": "java-developer",
                "company_name": "Google",
                "title": "Java Developer",
                "description": "Java backend role.",
                "remote": False,
                "url": "https://example.com/java",
                "location": "Munich",
                "tags": [],
                "job_types": [],
                "created_at": 1700000000,
            },
        ]
    }

    def fake_urlopen(
        request,
        timeout,
    ):
        return FakeResponse(payload)

    with patch(
        "career_agent_ai.application.search.providers."
        "arbeitnow_job_provider.urlopen",
        fake_urlopen,
    ):
        provider = ArbeitnowJobProvider(
            max_pages=1
        )

        result = provider.search(
            "python"
        )

    assert tuple(
        job.title
        for job in result
    ) == (
        "Python Developer",
    )


def test_arbeitnow_provider_empty_query_returns_all():
    payload = {
        "data": [
            {
                "slug": "job-one",
                "company_name": "Company One",
                "title": "Job One",
                "description": "",
                "remote": True,
                "url": "https://example.com/one",
                "location": "Berlin",
                "tags": [],
                "job_types": [],
                "created_at": 1700000000,
            },
            {
                "slug": "job-two",
                "company_name": "Company Two",
                "title": "Job Two",
                "description": "",
                "remote": False,
                "url": "https://example.com/two",
                "location": "Munich",
                "tags": [],
                "job_types": [],
                "created_at": 1700000000,
            },
        ]
    }

    def fake_urlopen(
        request,
        timeout,
    ):
        return FakeResponse(payload)

    with patch(
        "career_agent_ai.application.search.providers."
        "arbeitnow_job_provider.urlopen",
        fake_urlopen,
    ):
        provider = ArbeitnowJobProvider(
            max_pages=1
        )

        result = provider.search("")

    assert len(result) == 2