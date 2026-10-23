import json
from io import BytesIO

import pytest

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

    def read(self, size: int = -1) -> bytes:
        body = json.dumps(
            self._payload,
        ).encode("utf-8")
        return body if size < 0 else body[:size]

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


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "100", None])
def test_response_limit_requires_positive_integer(limit):
    with pytest.raises(ValueError, match="max_response_bytes"):
        ArbeitnowProvider(max_response_bytes=limit)


@pytest.mark.parametrize("oversized", [False, True])
def test_response_byte_boundary_before_json_decode(monkeypatch, oversized):
    body = json.dumps({"data": [], "label": "é"}, ensure_ascii=False).encode()
    limit = len(body)
    response = BytesIO(body + (b" " if oversized else b""))
    reads = []
    original_read = response.read

    def bounded_read(size=-1):
        reads.append(size)
        return original_read(size)

    response.read = bounded_read
    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: response,
    )
    provider = ArbeitnowProvider(max_response_bytes=limit)
    if oversized:
        monkeypatch.setattr(json, "loads", lambda value: pytest.fail("decoded oversized body"))
        with pytest.raises(RuntimeError, match="exceeds byte limit"):
            provider.search("")
    else:
        assert provider.search("") == ()
    assert reads == [limit + 1]
    assert response.closed


def test_oversized_second_page_stops_pagination_and_allows_retry(monkeypatch):
    first = json.dumps({"data": [], "links": {"next": "page-two"}}).encode()
    responses = [BytesIO(first), BytesIO(b"x" * 1000)]
    calls = []

    def fetch(request, timeout):
        calls.append(request.full_url)
        return responses[len(calls) - 1]

    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen", fetch
    )
    provider = ArbeitnowProvider(max_pages=3, max_response_bytes=100)
    with pytest.raises(RuntimeError, match="exceeds byte limit"):
        provider.search("")
    assert len(calls) == 2
    assert all(response.closed for response in responses)
    responses.append(BytesIO(b'{"data": []}'))
    assert provider.search("") == ()
    assert calls[-1].endswith("page=1")
