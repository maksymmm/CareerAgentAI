from __future__ import annotations

from io import BytesIO
import json
from types import SimpleNamespace

from career_agent_ai.application.api import CandidateApprovalWSGIApp
from career_agent_ai.application.career import ApprovalDecision


class Loop:
    def __init__(self):
        self.prompt = SimpleNamespace(
            to_dict=lambda: {"run_id": "run-1", "state_version": 3}
        )
        self.submission = None

    def get_candidate_approval_prompt(self, run_id, *, user_id):
        assert (run_id, user_id) == ("run-1", "candidate-1")
        return self.prompt

    def resume_candidate_submission(self, *, user_id, submission):
        assert user_id == "candidate-1"
        self.submission = submission
        return SimpleNamespace(run_id="run-1", phase=SimpleNamespace(value="message_approval"))


def request(app, *, method="GET", token="valid", body=None, path="/v1/candidate/runs/run-1/approval"):
    statuses = []
    encoded = b"" if body is None else json.dumps(body).encode()
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "HTTP_AUTHORIZATION": f"Bearer {token}" if token else "",
        "CONTENT_LENGTH": str(len(encoded)),
        "wsgi.input": BytesIO(encoded),
    }
    response = b"".join(app(environ, lambda status, headers: statuses.append((status, headers))))
    return statuses[0], json.loads(response)


def app(loop):
    return CandidateApprovalWSGIApp(
        loop,
        resolve_bearer=lambda token: "candidate-1" if token == "valid" else None,
    )


def test_authentication_happens_before_durable_prompt_access():
    class ForbiddenLoop:
        def get_candidate_approval_prompt(self, *args, **kwargs):
            raise AssertionError("storage must not be accessed")

    (status, headers), payload = request(app(ForbiddenLoop()), token="invalid")
    assert status == "401 Unauthorized"
    assert ("WWW-Authenticate", "Bearer") in headers
    assert payload == {"error": "unauthorized"}


def test_get_returns_only_candidate_prompt():
    (status, _), payload = request(app(Loop()))
    assert status == "200 OK"
    assert payload == {"run_id": "run-1", "state_version": 3}


def test_post_parses_bound_submission_and_resumes_as_authenticated_owner():
    loop = Loop()
    body = {
        "schema_version": 1,
        "run_id": "run-1",
        "state_version": 3,
        "action_fingerprint": "0" * 64,
        "decision": "approve",
    }
    (status, _), payload = request(app(loop), method="POST", body=body)
    assert status == "200 OK"
    assert payload == {"phase": "message_approval", "run_id": "run-1"}
    assert loop.submission.decision is ApprovalDecision.APPROVE


def test_post_rejects_url_mismatch_and_oversized_body():
    body = {
        "schema_version": 1,
        "run_id": "other-run",
        "state_version": 3,
        "action_fingerprint": "0" * 64,
        "decision": "approve",
    }
    (status, _), payload = request(app(Loop()), method="POST", body=body)
    assert status == "400 Bad Request"
    assert payload["error"] == "invalid_request"

    statuses = []
    response = app(Loop())(
        {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/v1/candidate/runs/run-1/approval",
            "HTTP_AUTHORIZATION": "Bearer valid",
            "CONTENT_LENGTH": "16385",
            "wsgi.input": BytesIO(),
        },
        lambda status, headers: statuses.append(status),
    )
    assert statuses == ["400 Bad Request"]
    assert json.loads(b"".join(response))["error"] == "invalid_request"


def test_foreign_or_missing_owner_is_hidden():
    class ForeignLoop:
        def get_candidate_approval_prompt(self, *args, **kwargs):
            raise PermissionError("foreign candidate secret")

    (status, _), payload = request(app(ForeignLoop()))
    assert status == "404 Not Found"
    assert payload == {"error": "not_found"}

    class MissingLoop:
        def get_candidate_approval_prompt(self, *args, **kwargs):
            raise KeyError("run does not exist or belongs to another user")

    (status, _), payload = request(app(MissingLoop()))
    assert status == "404 Not Found"
    assert payload == {"error": "not_found"}


def test_post_rejects_short_body_even_when_prefix_is_valid_json():
    encoded = json.dumps(
        {
            "schema_version": 1,
            "run_id": "run-1",
            "state_version": 3,
            "action_fingerprint": "0" * 64,
            "decision": "approve",
        }
    ).encode()
    statuses = []
    response = app(Loop())(
        {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/v1/candidate/runs/run-1/approval",
            "HTTP_AUTHORIZATION": "Bearer valid",
            "CONTENT_LENGTH": str(len(encoded) + 1),
            "wsgi.input": BytesIO(encoded),
        },
        lambda status, headers: statuses.append(status),
    )

    assert statuses == ["400 Bad Request"]
    assert json.loads(b"".join(response))["error"] == "invalid_request"
