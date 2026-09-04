import json

from career_agent_ai.application.jobs.job_source import JobSource
from career_agent_ai.application.search.providers.arbeitnow_provider import (
    ArbeitnowProvider,
)


class FakeResponse:
    def __init__(
        self,
        payload: dict,
    ) -> None:
        self._payload = payload
        self.status = 200

    def read(self) -> bytes:
        return json.dumps(
            self._payload,
        ).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        return False


def test_arbeitnow_provider_converts_job(
    monkeypatch,
):
    payload = {
        "data": [
            {
                "slug": "python-developer-openai-123",
                "company_name": "OpenAI",
                "title": "Python Developer",
                "remote": False,
                "location": "Berlin",
                "tags": [
                    "Python",
                    "Backend",
                ],
                "job_types": [
                    "Full-time",
                ],
                "url": (
                    "https://www.arbeitnow.com/view/"
                    "python-developer-openai-123"
                ),
                "description": (
                    "Python backend development"
                ),
                "created_at_iso": (
                    "2026-08-20T10:00:00Z"
                ),
            }
        ],
        "links": {
            "next": None,
        },
    }

    def fake_urlopen(
        request,
        timeout,
    ):
        return FakeResponse(payload)

    monkeypatch.setattr(
        "career_agent_ai.application.search.providers."
        "arbeitnow_provider.urlopen",
        fake_urlopen,
    )

    provider = ArbeitnowProvider()

    result = provider.search("python")

    assert len(result) == 1

    job = result[0]

    assert job.title == "Python Developer"
    assert job.company.name == "OpenAI"
    assert job.location.city == "Berlin"
    assert job.source == JobSource.OTHER
    assert job.url.startswith(
        "https://www.arbeitnow.com/"
    )
    assert "Python backend" in job.description


def test_arbeitnow_provider_detects_remote(
    monkeypatch,
):
    payload = {
        "data": [
            {
                "slug": "remote-python-123",
                "company_name": "Example",
                "title": "Remote Python Engineer",
                "remote": True,
                "location": "Remote",
                "tags": [
                    "Python",
                ],
                "job_types": [
                    "Full-time",
                ],
                "url": "https://example.com/job",
                "description": "Remote Python job",
            }
        ],
        "links": {
            "next": None,
        },
    }

    def fake_urlopen(
        request,
        timeout,
    ):
        return FakeResponse(payload)

    monkeypatch.setattr(
        "career_agent_ai.application.search.providers."
        "arbeitnow_provider.urlopen",
        fake_urlopen,
    )

    provider = ArbeitnowProvider()

    result = provider.search("python")

    assert len(result) == 1
    assert result[0].location.city == "Remote"


def test_arbeitnow_provider_empty_page(
    monkeypatch,
):
    payload = {
        "data": [],
        "links": {
            "next": None,
        },
    }

    def fake_urlopen(
        request,
        timeout,
    ):
        return FakeResponse(payload)

    monkeypatch.setattr(
        "career_agent_ai.application.search.providers."
        "arbeitnow_provider.urlopen",
        fake_urlopen,
    )

    provider = ArbeitnowProvider()

    result = provider.search("python")

    assert result == ()