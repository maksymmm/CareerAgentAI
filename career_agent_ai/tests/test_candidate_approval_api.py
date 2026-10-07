from __future__ import annotations

from io import BytesIO
import json
import re

import pytest
from types import SimpleNamespace

from career_agent_ai.application.api import (
    CandidateApprovalWSGIApp,
    candidate_approval_openapi_document,
)
from career_agent_ai.application.career import (
    ApprovalDecision,
    CareerLoopConflictError,
    HumanActionRequiredError,
)


class Loop:
    def __init__(self):
        self.prompt = SimpleNamespace(
            to_dict=lambda: {"run_id": "run-1", "state_version": 3}
        )
        self.submission = None
        self.started = None

    def start(self, request, *, run_id=None):
        self.started = (request, run_id)
        return SimpleNamespace(run_id=run_id, phase=SimpleNamespace(value="application_approval"))

    def get_candidate_approval_prompt(self, run_id, *, user_id):
        assert (run_id, user_id) == ("run-1", "candidate-1")
        return self.prompt

    def resume_candidate_submission(self, *, user_id, submission):
        assert user_id == "candidate-1"
        self.submission = submission
        return SimpleNamespace(run_id="run-1", phase=SimpleNamespace(value="message_approval"))

    def continue_run(self, run_id, *, user_id):
        assert (run_id, user_id) == ("run-1", "candidate-1")
        return SimpleNamespace(run_id=run_id, phase=SimpleNamespace(value="complete"))


def request(
    app,
    *,
    method="GET",
    token="valid",
    auth_scheme="Bearer",
    content_type="application/json",
    body=None,
    path="/v1/candidate/runs/run-1/approval",
):
    statuses = []
    encoded = b"" if body is None else json.dumps(body).encode()
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "HTTP_AUTHORIZATION": f"{auth_scheme} {token}" if token else "",
        "CONTENT_LENGTH": str(len(encoded)),
        "CONTENT_TYPE": content_type,
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


def test_bearer_authentication_scheme_is_case_insensitive():
    (status, _), payload = request(app(Loop()), auth_scheme="bEaReR")

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


def test_post_continue_recovers_authenticated_owner_without_body():
    (status, _), payload = request(
        app(Loop()),
        method="POST",
        path="/v1/candidate/runs/run-1/continue",
    )
    assert status == "200 OK"
    assert payload == {"phase": "complete", "run_id": "run-1"}


def test_post_runs_starts_versioned_request_as_authenticated_owner():
    loop = Loop()
    body = {
        "schema_version": 1,
        "run_id": "run-new",
        "keyword": "logistics",
        "candidate_profile": "Warehouse coordinator",
        "location": "Karlsruhe",
    }
    (status, _), payload = request(
        app(loop), method="POST", body=body, path="/v1/candidate/runs"
    )

    assert status == "201 Created"
    assert payload == {"phase": "application_approval", "run_id": "run-new"}
    started, run_id = loop.started
    assert run_id == "run-new"
    assert started.user_id == "candidate-1"
    assert started.keyword == "logistics"
    assert started.location == "Karlsruhe"
    assert started.sender == ""
    assert started.schedule_event_id is None


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": 2},
        {"user_id": "attacker"},
        {"provider": "network"},
        {"keyword": ""},
        {"keyword": " "},
        {"keyword": f" {'x' * 500} "},
        {"keyword": "a\u0000b"},
        {"keyword": "a\rb"},
        {"keyword": "\ufefflogistics"},
        {"run_id": "bad/run"},
        {"run_id": " run-new "},
        {"candidate_profile": " "},
        {"location": " Karlsruhe "},
        {"candidate_profile": "x" * 12_001},
    ],
)
def test_post_runs_rejects_invalid_or_privileged_fields(change):
    body = {
        "schema_version": 1,
        "run_id": "run-new",
        "keyword": "logistics",
        "candidate_profile": "Warehouse coordinator",
        **change,
    }
    (status, _), payload = request(
        app(Loop()), method="POST", body=body, path="/v1/candidate/runs"
    )

    assert status == "400 Bad Request"
    assert payload["error"] == "invalid_request"


def test_post_runs_accepts_maximum_astral_profile_within_schema():
    loop = Loop()
    body = {
        "schema_version": 1,
        "run_id": "run-unicode",
        "keyword": "logistics",
        "candidate_profile": "😀" * 12_000,
    }

    (status, _), payload = request(
        app(loop), method="POST", body=body, path="/v1/candidate/runs"
    )

    assert status == "201 Created"
    assert payload == {"phase": "application_approval", "run_id": "run-unicode"}
    assert loop.started[0].candidate_profile == "😀" * 12_000


def test_post_runs_authenticates_before_reading_body_or_storage():
    class ForbiddenBody:
        def read(self, length):
            raise AssertionError("body must not be read")

    statuses = []
    response = app(Loop())(
        {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/v1/candidate/runs",
            "HTTP_AUTHORIZATION": "Bearer invalid",
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": "10",
            "wsgi.input": ForbiddenBody(),
        },
        lambda status, headers: statuses.append(status),
    )

    assert statuses == ["401 Unauthorized"]
    assert json.loads(b"".join(response)) == {"error": "unauthorized"}


def test_post_runs_returns_sanitized_conflict_for_duplicate_identifier():
    class ConflictLoop(Loop):
        def start(self, request, *, run_id=None):
            raise CareerLoopConflictError("private existing owner")

    body = {
        "schema_version": 1,
        "run_id": "run-new",
        "keyword": "logistics",
        "candidate_profile": "Warehouse coordinator",
    }
    (status, _), payload = request(
        app(ConflictLoop()), method="POST", body=body, path="/v1/candidate/runs"
    )

    assert status == "409 Conflict"
    assert payload == {"error": "run_conflict"}


def test_continue_rejects_get_and_human_gated_run():
    (status, headers), payload = request(
        app(Loop()), path="/v1/candidate/runs/run-1/continue"
    )
    assert status == "405 Method Not Allowed"
    assert ("Allow", "POST") in headers
    assert payload == {"error": "method_not_allowed"}

    class WaitingLoop(Loop):
        def continue_run(self, run_id, *, user_id):
            raise HumanActionRequiredError("private pending action")

    (status, _), payload = request(
        app(WaitingLoop()),
        method="POST",
        path="/v1/candidate/runs/run-1/continue",
    )
    assert status == "409 Conflict"
    assert payload == {"error": "recovery_unavailable"}


def test_continue_distinguishes_execution_conflict_from_human_gate():
    class ConflictingLoop(Loop):
        def continue_run(self, run_id, *, user_id):
            raise CareerLoopConflictError("private execution lease")

    (status, _), payload = request(
        app(ConflictingLoop()),
        method="POST",
        path="/v1/candidate/runs/run-1/continue",
    )

    assert status == "409 Conflict"
    assert payload == {"error": "approval_conflict"}


def test_continue_does_not_mask_unexpected_runtime_failure():
    class BrokenLoop(Loop):
        def continue_run(self, run_id, *, user_id):
            raise RuntimeError("database credential=do-not-expose")

    (status, _), payload = request(
        app(BrokenLoop()),
        method="POST",
        path="/v1/candidate/runs/run-1/continue",
    )

    assert status == "500 Internal Server Error"
    assert payload == {"error": "internal_error"}


def test_post_rejects_non_json_media_type_before_reading_or_resuming():
    class ForbiddenLoop:
        def resume_candidate_submission(self, *args, **kwargs):
            raise AssertionError("loop must not be resumed")

    body = {
        "schema_version": 1,
        "run_id": "run-1",
        "state_version": 3,
        "action_fingerprint": "0" * 64,
        "decision": "approve",
    }
    (status, _), payload = request(
        app(ForbiddenLoop()), method="POST", body=body, content_type="text/plain"
    )

    assert status == "415 Unsupported Media Type"
    assert payload == {"error": "unsupported_media_type"}


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
                "CONTENT_TYPE": "application/json",
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
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": str(len(encoded) + 1),
            "wsgi.input": BytesIO(encoded),
        },
        lambda status, headers: statuses.append(status),
    )

    assert statuses == ["400 Bad Request"]
    assert json.loads(b"".join(response))["error"] == "invalid_request"


def test_openapi_contract_documents_authenticated_get_and_strict_post():
    document = candidate_approval_openapi_document()
    route = document["paths"]["/v1/candidate/runs/{run_id}/approval"]

    assert document["openapi"] == "3.1.0"
    assert document["components"]["securitySchemes"]["bearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
    }
    assert route["get"]["security"] == [{"bearerAuth": []}]
    assert route["post"]["security"] == [{"bearerAuth": []}]
    request_schema = route["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert request_schema["additionalProperties"] is False
    assert request_schema["required"] == [
        "schema_version", "run_id", "state_version", "action_fingerprint", "decision",
    ]
    assert request_schema["properties"]["decision"]["enum"] == ["approve", "decline"]
    assert request_schema["properties"]["action_fingerprint"]["pattern"] == (
        "^[0-9a-f]{64}$"
    )
    assert {"400", "401", "404", "409", "415", "500"} <= set(
        route["post"]["responses"]
    )
    assert "409" in route["get"]["responses"]
    assert "500" in route["get"]["responses"]
    start = document["paths"]["/v1/candidate/runs"]["post"]
    assert start["security"] == [{"bearerAuth": []}]
    start_schema = start["requestBody"]["content"]["application/json"]["schema"]
    assert start_schema["additionalProperties"] is False
    assert start_schema["required"] == [
        "schema_version", "run_id", "keyword", "candidate_profile",
    ]
    assert "user_id" not in start_schema["properties"]
    keyword_pattern = re.compile(start_schema["properties"]["keyword"]["pattern"])
    assert keyword_pattern.fullmatch("logistics\ncoordinator")
    assert keyword_pattern.fullmatch("a\u0000b") is None
    assert keyword_pattern.fullmatch("a\rb") is None
    assert keyword_pattern.fullmatch("\ufefflogistics") is None
    assert {"201", "400", "401", "404", "409", "415", "500"} <= set(
        start["responses"]
    )
    recovery = document["paths"]["/v1/candidate/runs/{run_id}/continue"]["post"]
    conflict_schema = recovery["responses"]["409"]["content"]["application/json"][
        "schema"
    ]
    assert {
        choice["properties"]["error"]["const"]
        for choice in conflict_schema["oneOf"]
    } == {"recovery_unavailable", "approval_conflict"}


def test_only_durable_state_conflicts_return_409():
    class ConflictLoop(Loop):
        def resume_candidate_submission(self, *, user_id, submission):
            raise CareerLoopConflictError("stale durable state secret")

    body = {
        "schema_version": 1,
        "run_id": "run-1",
        "state_version": 3,
        "action_fingerprint": "0" * 64,
        "decision": "approve",
    }
    (status, _), payload = request(app(ConflictLoop()), method="POST", body=body)

    assert status == "409 Conflict"
    assert payload == {"error": "approval_conflict"}


def test_unexpected_loop_failure_is_sanitized_as_500():
    class BrokenLoop:
        def get_candidate_approval_prompt(self, *args, **kwargs):
            raise RuntimeError("database credential=do-not-expose")

    (status, _), payload = request(app(BrokenLoop()))

    assert status == "500 Internal Server Error"
    assert payload == {"error": "internal_error"}


def test_bearer_resolver_failure_is_sanitized_as_500():
    def broken_resolver(token):
        raise RuntimeError("identity provider credential=do-not-expose")

    candidate_app = CandidateApprovalWSGIApp(Loop(), resolve_bearer=broken_resolver)
    (status, headers), payload = request(candidate_app)

    assert status == "500 Internal Server Error"
    assert ("Cache-Control", "no-store") in headers
    assert payload == {"error": "internal_error"}


def test_bearer_resolver_ownership_shaped_failure_is_still_500():
    def broken_resolver(token):
        raise KeyError("identity record disappeared")

    candidate_app = CandidateApprovalWSGIApp(Loop(), resolve_bearer=broken_resolver)
    (status, _), payload = request(candidate_app)

    assert status == "500 Internal Server Error"
    assert payload == {"error": "internal_error"}


def test_durable_state_value_error_is_sanitized_as_500():
    class CorruptStateLoop:
        def get_candidate_approval_prompt(self, *args, **kwargs):
            raise ValueError("persisted candidate secret=do-not-expose")

    (status, _), payload = request(app(CorruptStateLoop()))

    assert status == "500 Internal Server Error"
    assert payload == {"error": "internal_error"}


def test_get_rejects_malformed_run_id_before_loop_access():
    class ForbiddenLoop:
        def get_candidate_approval_prompt(self, *args, **kwargs):
            raise AssertionError("loop must not be accessed")

    (status, _), payload = request(
        app(ForbiddenLoop()), path="/v1/candidate/runs/bad id/approval"
    )

    assert status == "400 Bad Request"
    assert payload["error"] == "invalid_request"


def test_prompt_fingerprint_mismatch_is_an_approval_conflict():
    class PromptConflictLoop(Loop):
        def resume_candidate_submission(self, *, user_id, submission):
            from career_agent_ai.application.career import (
                CandidateApprovalConflictError,
            )

            raise CandidateApprovalConflictError("pending action changed")

    body = {
        "schema_version": 1,
        "run_id": "run-1",
        "state_version": 3,
        "action_fingerprint": "f" * 64,
        "decision": "approve",
    }
    (status, _), payload = request(app(PromptConflictLoop()), method="POST", body=body)

    assert status == "409 Conflict"
    assert payload == {"error": "approval_conflict"}


def test_get_without_an_ordinary_pending_prompt_returns_client_state_conflict():
    from career_agent_ai.application.career import CandidateApprovalUnavailableError

    class CompletedLoop:
        def get_candidate_approval_prompt(self, *args, **kwargs):
            raise CandidateApprovalUnavailableError("run is complete")

    (status, _), payload = request(app(CompletedLoop()))

    assert status == "409 Conflict"
    assert payload == {"error": "approval_unavailable"}


@pytest.mark.parametrize("duplicate", [
    '"decision":"decline","decision":"approve"',
    '"decision":"approve","decision":"approve"',
    '"decision":"decline","\\u0064ecision":"approve"',
    '"state_version":2,"state_version":3,"decision":"approve"',
])
def test_post_rejects_duplicate_json_members_before_resuming(duplicate):
    loop = Loop()
    encoded = (
        '{"schema_version":1,"run_id":"run-1",'
        '"action_fingerprint":"' + "0" * 64 + '",' + duplicate + '}'
    ).encode()
    statuses = []
    response = app(loop)({
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/v1/candidate/runs/run-1/approval",
        "HTTP_AUTHORIZATION": "Bearer valid",
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(encoded)),
        "wsgi.input": BytesIO(encoded),
    }, lambda status, headers: statuses.append(status))
    assert statuses == ["400 Bad Request"]
    assert json.loads(b"".join(response))["message"] == (
        "approval body contains duplicate JSON members."
    )
    assert loop.submission is None
