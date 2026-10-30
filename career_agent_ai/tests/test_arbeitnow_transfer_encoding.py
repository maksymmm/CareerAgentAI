"""Regression tests for fail-closed Arbeitnow transfer framing."""

from io import BytesIO
from email.message import Message

import pytest

from career_agent_ai.application.search.providers.arbeitnow_provider import (
    ArbeitnowProvider,
)


@pytest.mark.parametrize("transfer_encoding", ["chunked", "identity", "gzip", "", "chunked, gzip"])
def test_transfer_encoding_is_rejected_before_body_read(monkeypatch, transfer_encoding):
    class UnreadResponse(BytesIO):
        status = 200

        def read(self, size=-1):
            pytest.fail("provider read body with ambiguous transfer framing")

    response = UnreadResponse(b'{"data": []}')
    headers = Message()
    headers["Content-Type"] = "application/json"
    headers["Content-Length"] = str(len(b'{"data": []}'))
    headers["Transfer-Encoding"] = transfer_encoding
    response.headers = headers

    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: response,
    )

    with pytest.raises(RuntimeError, match="Transfer-Encoding is unsupported"):
        ArbeitnowProvider(max_pages=1).search("")

    assert response.closed


def test_transfer_encoding_header_lookup_failure_is_fail_closed(monkeypatch):
    class BrokenHeaders:
        def get(self, name):
            raise ValueError("private upstream detail")

    class UnreadResponse(BytesIO):
        status = 200
        headers = BrokenHeaders()

        def read(self, size=-1):
            pytest.fail("body read after header lookup failure")

    response = UnreadResponse(b'{"data": []}')
    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: response,
    )
    with pytest.raises(RuntimeError, match="response headers are invalid"):
        ArbeitnowProvider(max_pages=1).search("")
    assert response.closed
