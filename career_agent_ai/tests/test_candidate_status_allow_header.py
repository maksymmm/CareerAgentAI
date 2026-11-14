"""Regression for GET-only candidate run status method contract."""

from io import BytesIO
import json

import pytest

from career_agent_ai.application.api import CandidateApprovalWSGIApp


@pytest.mark.parametrize("method", ["POST", "DELETE", "PATCH", "OPTIONS"])
def test_status_method_rejection_advertises_get_only(method):
    class NoStorage:
        def get_candidate_run_status(self, *args, **kwargs):
            pytest.fail("rejected status method must not read storage")

    app = CandidateApprovalWSGIApp(
        NoStorage(), resolve_bearer=lambda token: "candidate-1",
    )
    responses = []
    body = b"".join(app(
        {
            "PATH_INFO": "/v1/candidate/runs/run-1/status",
            "REQUEST_METHOD": method,
            "HTTP_AUTHORIZATION": "Bearer valid",
            "CONTENT_LENGTH": "0",
            "wsgi.input": BytesIO(),
        },
        lambda status, headers: responses.append((status, dict(headers))),
    ))
    assert responses[0][0] == "405 Method Not Allowed"
    assert responses[0][1]["Allow"] == "GET"
    assert json.loads(body) == {"error": "method_not_allowed"}
