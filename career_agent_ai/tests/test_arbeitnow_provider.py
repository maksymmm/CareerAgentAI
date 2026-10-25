import json
from io import BytesIO
from email.message import Message

import pytest
from urllib.request import HTTPRedirectHandler, Request

from career_agent_ai.application.jobs.job_source import JobSource
from career_agent_ai.application.search.providers.arbeitnow_provider import (
    ArbeitnowProvider,
    _TrustedArbeitnowRedirectHandler,
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


def byte_response(body: bytes) -> BytesIO:
    """Build a byte-stream transport fixture with an explicit valid status."""
    response = BytesIO(body)
    response.status = 200
    return response


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


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [
        (-1, 50_000),
        ("NaN", "50000"),
        ("Infinity", "50000"),
        ("-1", "50000"),
        (60_000, 50_000),
    ],
)
def test_invalid_numeric_salary_is_dropped_without_losing_job(
    monkeypatch,
    minimum,
    maximum,
):
    payload = {
        "data": [
            {
                "slug": "warehouse-operator-example-1",
                "company_name": "Example",
                "title": "Warehouse Operator",
                "location": "Karlsruhe",
                "description": "Warehouse operations",
                "salary_min": minimum,
                "salary_max": maximum,
            }
        ]
    }
    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: FakeResponse(payload),
    )

    result = ArbeitnowProvider(max_pages=1).search("")

    assert len(result) == 1
    assert result[0].salary is None


@pytest.mark.parametrize(
    ("minimum", "maximum", "expected"),
    [
        (50_000, 60_000, (50_000, 60_000)),
        ("50000", "60000", (50_000, 60_000)),
        (None, 60_000, (60_000, 60_000)),
        (50_000, None, (50_000, 50_000)),
    ],
)
def test_valid_numeric_salary_is_normalized(monkeypatch, minimum, maximum, expected):
    payload = {
        "data": [
            {
                "slug": "warehouse-operator-example-1",
                "company_name": "Example",
                "title": "Warehouse Operator",
                "location": "Karlsruhe",
                "description": "Warehouse operations",
                "salary_min": minimum,
                "salary_max": maximum,
            }
        ]
    }
    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: FakeResponse(payload),
    )

    salary = ArbeitnowProvider(max_pages=1).search("")[0].salary

    assert (salary.minimum, salary.maximum) == expected


@pytest.mark.parametrize(
    "timeout",
    [
        0,
        -1,
        True,
        False,
        float("nan"),
        float("inf"),
        -float("inf"),
        10**1000,
        -(10**1000),
        "20",
        None,
    ],
)
def test_timeout_requires_finite_positive_number(timeout):
    with pytest.raises(ValueError, match="timeout must be a finite positive number"):
        ArbeitnowProvider(timeout=timeout)


@pytest.mark.parametrize("max_pages", [0, -1, True, False, 1.5, "3", None])
def test_page_limit_requires_positive_integer(max_pages):
    with pytest.raises(ValueError, match="max_pages must be a positive integer"):
        ArbeitnowProvider(max_pages=max_pages)


def test_integer_timeout_is_normalized_before_transport(monkeypatch):
    observed_timeouts = []

    def fetch(request, timeout):
        observed_timeouts.append(timeout)
        return FakeResponse({"data": []})

    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        fetch,
    )

    assert ArbeitnowProvider(timeout=2, max_pages=1).search("") == ()
    assert observed_timeouts == [2.0]


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "100", None])
def test_response_limit_requires_positive_integer(limit):
    with pytest.raises(ValueError, match="max_response_bytes"):
        ArbeitnowProvider(max_response_bytes=limit)


@pytest.mark.parametrize("status", [None, True, 200.0, "200", 99, 600])
def test_invalid_http_status_is_rejected_before_body_read(monkeypatch, status):
    class InvalidStatusResponse(BytesIO):
        def read(self, size=-1):
            pytest.fail("body was read for an invalid HTTP status")

    response = InvalidStatusResponse(b'{"data": []}')
    response.status = status
    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: response,
    )

    with pytest.raises(RuntimeError, match="invalid HTTP status"):
        ArbeitnowProvider().search("")

    assert response.closed


def test_missing_http_status_is_rejected_before_body_read(monkeypatch):
    class MissingStatusResponse(BytesIO):
        def read(self, size=-1):
            pytest.fail("body was read without an HTTP status")

    response = MissingStatusResponse(b'{"data": []}')
    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: response,
    )

    with pytest.raises(RuntimeError, match="invalid HTTP status"):
        ArbeitnowProvider().search("")

    assert response.closed


@pytest.mark.parametrize(
    ("content_types", "error"),
    [
        (("text/html",), "response is not JSON"),
        (("application/json; charset=iso-8859-1",), "response is not JSON"),
        (("application/json; boundary=value",), "response is not JSON"),
        (("application/json; charset=\"utf-8",), "response is not JSON"),
        (
            ("application/json; charset=utf-8; charset=utf-8",),
            "response is not JSON",
        ),
        (("application/json", "text/html"), "response is not JSON"),
        (
            ("application/json", "application/json; charset=utf-8"),
            "Content-Type values conflict",
        ),
        ((True,), "Content-Type is invalid"),
        (("application/js\N{SNOWMAN}on",), "Content-Type is invalid"),
    ],
)
def test_unsafe_content_type_is_rejected_before_body_read(
    monkeypatch, content_types, error
):
    class UnreadResponse(BytesIO):
        def read(self, size=-1):
            pytest.fail("body with unsafe Content-Type was read")

    response = UnreadResponse(b'{"data": []}')
    response.status = 200
    if all(isinstance(value, str) for value in content_types):
        headers = Message()
        for value in content_types:
            headers["Content-Type"] = value
        response.headers = headers
    else:
        response.headers = {"Content-Type": content_types[0]}
    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: response,
    )

    with pytest.raises(RuntimeError, match=error):
        ArbeitnowProvider().search("")

    assert response.closed


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json",
        " Application/JSON ; charset=UTF-8 ",
        'application/json; charset="utf-8"',
        'application/json; charset="UTF-8"',
    ],
)
def test_json_content_type_is_accepted(monkeypatch, content_type):
    response = byte_response(b'{"data": []}')
    response.headers = {"Content-Type": content_type}
    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: response,
    )

    assert ArbeitnowProvider().search("") == ()
    assert response.closed


def test_equivalent_quoted_content_type_duplicates_are_accepted(monkeypatch):
    headers = Message()
    headers["Content-Type"] = "application/json; charset=utf-8"
    headers["Content-Type"] = 'application/json; charset="UTF-8"'
    response = byte_response(b'{"data": []}')
    response.headers = headers
    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: response,
    )

    assert ArbeitnowProvider().search("") == ()
    assert response.closed


@pytest.mark.parametrize(
    ("content_encoding", "error"),
    [
        ("gzip", "response encoding is unsupported"),
        ("br", "response encoding is unsupported"),
        ("identity, gzip", "response encoding is unsupported"),
        (True, "Content-Encoding is invalid"),
        ("identit\N{SNOWMAN}y", "Content-Encoding is invalid"),
    ],
)
def test_unsafe_content_encoding_is_rejected_before_body_read(
    monkeypatch, content_encoding, error
):
    class UnreadResponse(BytesIO):
        def read(self, size=-1):
            pytest.fail("encoded body was read")

    response = UnreadResponse(b'{"data": []}')
    response.status = 200
    response.headers = {"Content-Encoding": content_encoding}
    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: response,
    )

    with pytest.raises(RuntimeError, match=error):
        ArbeitnowProvider().search("")

    assert response.closed


def test_identity_content_encoding_is_accepted(monkeypatch):
    response = byte_response(b'{"data": []}')
    response.headers = {"Content-Encoding": " Identity "}
    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: response,
    )

    assert ArbeitnowProvider().search("") == ()
    assert response.closed


@pytest.mark.parametrize("body", [None, '{"data": []}', bytearray(b'{"data": []}')])
def test_non_bytes_response_body_is_rejected(monkeypatch, body):
    class InvalidBodyResponse:
        status = 200
        closed = False

        def read(self, size=-1):
            return body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            self.closed = True
            return False

    response = InvalidBodyResponse()
    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: response,
    )

    with pytest.raises(RuntimeError, match="invalid body"):
        ArbeitnowProvider().search("")

    assert response.closed


@pytest.mark.parametrize(
    "body",
    [
        b'{"data": [], "data": [{"title": "ambiguous"}]}',
        b'{"data": [], "links": {"next": null, "next": "page-two"}}',
        b'{"data": [], "score": NaN}',
        b'{"data": [], "score": Infinity}',
        b'{"data": [], "score": -Infinity}',
        (b'{"data":' + (b"[" * 101) + b"0" + (b"]" * 101) + b"}"),
    ],
)
def test_non_strict_json_is_rejected(monkeypatch, body):
    response = byte_response(body)
    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: response,
    )

    with pytest.raises(RuntimeError, match="invalid JSON"):
        ArbeitnowProvider().search("")

    assert response.closed


def test_json_depth_validation_ignores_quoted_brackets(monkeypatch):
    body = json.dumps({"data": [], "label": "[" * 101}).encode()
    response = byte_response(body)
    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: response,
    )

    assert ArbeitnowProvider().search("") == ()
    assert response.closed


@pytest.mark.parametrize("oversized", [False, True])
def test_response_byte_boundary_before_json_decode(monkeypatch, oversized):
    body = json.dumps({"data": [], "label": "é"}, ensure_ascii=False).encode()
    limit = len(body)
    response = byte_response(body + (b" " if oversized else b""))
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
    responses = [byte_response(first), byte_response(b"x" * 1000)]
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
    responses.append(byte_response(b'{"data": []}'))
    assert provider.search("") == ()
    assert calls[-1].endswith("page=1")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "invalid page data"),
        ({"data": {}}, "invalid page data"),
        ({"data": [], "links": []}, "invalid pagination links"),
        ({"data": [], "links": {"next": True}}, "invalid next-page state"),
        ({"data": [], "links": {"next": "  "}}, "invalid next-page state"),
    ],
)
def test_invalid_page_envelope_fails_closed(monkeypatch, payload, message):
    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: FakeResponse(payload),
    )

    with pytest.raises(RuntimeError, match=message):
        ArbeitnowProvider().search("")


def test_invalid_second_page_returns_no_partial_results_and_retry_starts_over(
    monkeypatch,
):
    first_page = {
        "data": [
            {
                "slug": "warehouse-operator-example-1",
                "company_name": "Example",
                "title": "Warehouse Operator",
                "location": "Karlsruhe",
                "description": "Warehouse operations",
            }
        ],
        "links": {"next": "page-two"},
    }
    payloads = [
        first_page,
        {"data": [], "links": {"next": False}},
        {"data": [], "links": {"next": None}},
    ]
    calls = []

    def fetch(request, timeout):
        calls.append(request.full_url)
        return FakeResponse(payloads.pop(0))

    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        fetch,
    )
    provider = ArbeitnowProvider(max_pages=3)

    with pytest.raises(RuntimeError, match="invalid next-page state"):
        provider.search("")

    assert len(calls) == 2
    assert calls[0].endswith("page=1")
    assert calls[1].endswith("page=2")
    assert provider.search("") == ()
    assert calls[-1].endswith("page=1")


@pytest.mark.parametrize(
    "response_url",
    [
        "http://www.arbeitnow.com/api/job-board-api?page=1",
        "https://evil.example/api/job-board-api?page=1",
        "https://www.arbeitnow.com.evil.example/api/job-board-api?page=1",
        "https://www.arbeitnow.com/other?page=1",
        "https://user@www.arbeitnow.com/api/job-board-api?page=1",
        "https://www.arbeitnow.com:/api/job-board-api?page=1",
        "https://www.arbeitnow.com:0443/api/job-board-api?page=1",
        "https://www.arbeitnow.com:444/api/job-board-api?page=1",
        "https://www.arbeitnow.com/api/job-board-api//?page=1",
        "https://www.arbeitnow.com/api/job-board-api////?page=1",
        "https://www.arbeitnow.com/api/job-board-api?page=1#fragment",
    ],
)
def test_untrusted_final_response_url_is_rejected_before_read(
    monkeypatch, response_url
):
    class RedirectedResponse(BytesIO):
        status = 200

        def geturl(self):
            return response_url

        def read(self, size=-1):
            pytest.fail("untrusted redirect body was read")

    response = RedirectedResponse(b'{"data": []}')
    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: response,
    )

    with pytest.raises(RuntimeError, match="response URL is not trusted"):
        ArbeitnowProvider().search("")

    assert response.closed


@pytest.mark.parametrize(
    "response_url",
    [
        "https://www.arbeitnow.com/api/job-board-api?page=1",
        "https://www.arbeitnow.com:443/api/job-board-api/?page=1",
    ],
)
def test_trusted_final_response_url_is_accepted(monkeypatch, response_url):
    class TrustedResponse(BytesIO):
        status = 200

        def geturl(self):
            return response_url

    response = TrustedResponse(b'{"data": []}')
    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: response,
    )

    assert ArbeitnowProvider().search("") == ()
    assert response.closed


@pytest.mark.parametrize(
    "content_length",
    ["-1", "+1", " 1", "1 ", "1.0", "１２", "not-a-number", 1, True],
)
def test_invalid_content_length_is_rejected_before_read(
    monkeypatch, content_length
):
    class InvalidLengthResponse(BytesIO):
        status = 200
        headers = {"Content-Length": content_length}

        def read(self, size=-1):
            pytest.fail("body with invalid Content-Length was read")

    response = InvalidLengthResponse(b'{"data": []}')
    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: response,
    )

    with pytest.raises(RuntimeError, match="Content-Length is invalid"):
        ArbeitnowProvider().search("")

    assert response.closed


def test_oversized_content_length_is_rejected_before_read(monkeypatch):
    class OversizedResponse(BytesIO):
        status = 200
        headers = {"Content-Length": "11"}

        def read(self, size=-1):
            pytest.fail("declared oversized body was read")

    response = OversizedResponse(b'{"data": []}')
    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: response,
    )

    with pytest.raises(RuntimeError, match="response exceeds byte limit"):
        ArbeitnowProvider(max_response_bytes=10).search("")

    assert response.closed


def test_valid_content_length_allows_body_read(monkeypatch):
    body = b'{"data": []}'

    class SizedResponse(BytesIO):
        status = 200
        headers = {"Content-Length": str(len(body))}

    response = SizedResponse(body)
    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: response,
    )

    assert ArbeitnowProvider().search("") == ()
    assert response.closed


@pytest.mark.parametrize(
    ("lengths", "error"),
    [
        (("12", "99999999"), "response exceeds byte limit"),
        (("12", "13"), "Content-Length values conflict"),
        (("12", "invalid"), "Content-Length is invalid"),
    ],
)
def test_all_content_length_occurrences_are_validated_before_read(
    monkeypatch, lengths, error
):
    headers = Message()
    for value in lengths:
        headers["Content-Length"] = value

    class DuplicateLengthResponse(BytesIO):
        status = 200

        def read(self, size=-1):
            pytest.fail("body with unsafe duplicate Content-Length was read")

    response = DuplicateLengthResponse(b'{"data": []}')
    response.headers = headers
    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: response,
    )

    with pytest.raises(RuntimeError, match=error):
        ArbeitnowProvider().search("")

    assert response.closed


def test_identical_content_length_occurrences_are_accepted(monkeypatch):
    body = b'{"data": []}'
    headers = Message()
    headers["Content-Length"] = str(len(body))
    headers["Content-Length"] = str(len(body))

    class DuplicateLengthResponse(BytesIO):
        status = 200

    response = DuplicateLengthResponse(body)
    response.headers = headers
    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: response,
    )

    assert ArbeitnowProvider().search("") == ()
    assert response.closed


@pytest.mark.parametrize(
    "redirect_url",
    [
        "http://www.arbeitnow.com/api/job-board-api?page=1",
        "https://evil.example/api/job-board-api?page=1",
        "https://www.arbeitnow.com.evil.example/api/job-board-api?page=1",
        "https://user@www.arbeitnow.com/api/job-board-api?page=1",
        "https://www.arbeitnow.com:444/api/job-board-api?page=1",
        "https://www.arbeitnow.com/other?page=1",
        "https://www.arbeitnow.com/api/job-board-api?page=1#fragment",
    ],
)
def test_untrusted_redirect_is_rejected_before_following(monkeypatch, redirect_url):
    followed = []

    def record_follow(self, req, fp, code, msg, headers, newurl):
        followed.append(newurl)
        return req

    monkeypatch.setattr(HTTPRedirectHandler, "redirect_request", record_follow)
    handler = _TrustedArbeitnowRedirectHandler()

    with pytest.raises(RuntimeError, match="response URL is not trusted"):
        handler.redirect_request(
            Request(ArbeitnowProvider.BASE_URL),
            None,
            302,
            "Found",
            {},
            redirect_url,
        )

    assert followed == []


@pytest.mark.parametrize(
    "redirect_url",
    [
        "https://www.arbeitnow.com/api/job-board-api?page=2",
        "https://www.arbeitnow.com:443/api/job-board-api/?page=2",
    ],
)
def test_trusted_redirect_is_followed(monkeypatch, redirect_url):
    followed = []
    expected_request = Request(redirect_url)

    def record_follow(self, req, fp, code, msg, headers, newurl):
        followed.append(newurl)
        return expected_request

    monkeypatch.setattr(HTTPRedirectHandler, "redirect_request", record_follow)
    handler = _TrustedArbeitnowRedirectHandler()

    result = handler.redirect_request(
        Request(ArbeitnowProvider.BASE_URL),
        None,
        302,
        "Found",
        {},
        redirect_url,
    )

    assert result is expected_request
    assert followed == [redirect_url]
