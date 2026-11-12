"""Malformed WSGI methods cannot read candidate state or trigger transitions."""

from io import BytesIO
import json

import pytest

from career_agent_ai.application.api import CandidateApprovalWSGIApp


@pytest.mark.parametrize("method", [None, "", "get", "POST ", "TRACE", 42, True])
@pytest.mark.parametrize(
    "path",
    ["/v1/candidate/runs", "/v1/candidate/runs/run-1/approval",
     "/v1/candidate/runs/run-1/status",
     "/v1/candidate/runs/run-1/continue",
     "/v1/candidate/applications", "/v1/candidate/schedule-events",
     "/sandbox/candidate"],
)
def test_noncanonical_method_rejected_without_accessing_candidate_loop(method, path):
    class ForbiddenLoop:
        def __getattr__(self, name):
            raise AssertionError("invalid HTTP method accessed candidate loop")

    app = CandidateApprovalWSGIApp(
        ForbiddenLoop(), resolve_bearer=lambda token: "candidate-1"
    )
    statuses = []
    environ = {
        "PATH_INFO": path,
        "HTTP_AUTHORIZATION": "Bearer test",
        "CONTENT_LENGTH": "0",
        "wsgi.input": BytesIO(),
    }
    if method is not None:
        environ["REQUEST_METHOD"] = method
    response = b"".join(app(environ, lambda status, headers: statuses.append((status, dict(headers)))))
    assert statuses[0][0] == "405 Method Not Allowed"
    assert statuses[0][1]["Cache-Control"] == "no-store"
    assert json.loads(response) == {"error": "method_not_allowed"}
