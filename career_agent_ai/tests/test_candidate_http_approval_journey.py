"""HTTP-level candidate approval journey: start, inspect, approve, recover.

All transitions use an in-memory sandbox loop; no provider or calendar I/O.
"""

from io import BytesIO
from types import SimpleNamespace
import json

from career_agent_ai.application.api import CandidateApprovalWSGIApp


def _request(app, path, *, method="GET", token="owner", body=None):
    encoded = b"" if body is None else json.dumps(body).encode()
    status = []
    response = app(
        {
            "PATH_INFO": path,
            "REQUEST_METHOD": method,
            "HTTP_AUTHORIZATION": "Bearer " + token,
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": str(len(encoded)),
            "wsgi.input": BytesIO(encoded),
        },
        lambda value, headers: status.append(value),
    )
    return status[0], json.loads(b"".join(response))


def test_candidate_approval_recovery_journey_preserves_owner_and_does_not_repeat_action():
    class SandboxLoop:
        def __init__(self):
            self.run_id = None
            self.owner = None
            self.phase = "application_approval"
            self.approvals = 0
            self.continuations = 0

        def replay_existing_start(self, request, *, run_id, user_id):
            raise KeyError(run_id)

        def start(self, request, *, run_id=None):
            assert request.user_id == "candidate-1"
            self.run_id = run_id
            self.owner = request.user_id
            return self._result()

        def _result(self):
            return SimpleNamespace(
                run_id=self.run_id,
                phase=SimpleNamespace(value=self.phase),
            )

        def get_candidate_run_status(self, run_id, *, user_id):
            self._check(run_id, user_id)
            return {"run_id": run_id, "phase": self.phase}

        def _check(self, run_id, user_id):
            assert run_id == self.run_id and user_id == self.owner

        def get_candidate_approval_prompt(self, run_id, *, user_id):
            self._check(run_id, user_id)
            assert self.phase == "application_approval"
            return SimpleNamespace(to_dict=lambda: {
                "run_id": run_id, "state_version": 1,
                "action_fingerprint": "a" * 64,
            })

        def resume_candidate_submission(self, *, user_id, submission):
            self._check(submission.run_id, user_id)
            assert submission.state_version == 1
            assert submission.action_fingerprint == "a" * 64
            assert self.phase == "application_approval"
            self.approvals += 1
            self.phase = "message_approval"
            return self._result()

        def continue_run(self, run_id, *, user_id):
            self._check(run_id, user_id)
            assert self.phase == "message_approval"
            self.continuations += 1
            self.phase = "complete"
            return self._result()

    loop = SandboxLoop()
    app = CandidateApprovalWSGIApp(
        loop,
        resolve_bearer=lambda token: "candidate-1" if token == "owner" else None,
    )
    status, started = _request(
        app, "/v1/candidate/runs", method="POST",
        body={"schema_version": 1, "run_id": "journey-1",
              "keyword": "logistics", "candidate_profile": "Warehouse coordinator"},
    )
    assert status == "201 Created"
    run_id = started["run_id"]
    assert run_id != "journey-1"
    approval_path = f"/v1/candidate/runs/{run_id}/approval"
    status_path = f"/v1/candidate/runs/{run_id}/status"

    assert _request(app, status_path, token="intruder")[0] == "401 Unauthorized"
    assert _request(app, approval_path, token="intruder")[0] == "401 Unauthorized"
    assert _request(app, approval_path)[1]["action_fingerprint"] == "a" * 64
    assert _request(app, status_path)[1]["phase"] == "application_approval"

    status, approved = _request(
        app, approval_path, method="POST",
        body={"schema_version": 1, "run_id": run_id, "state_version": 1,
              "action_fingerprint": "a" * 64, "decision": "approve"},
    )
    assert status == "200 OK"
    assert approved["phase"] == "message_approval"
    assert loop.approvals == 1
    assert _request(app, status_path)[1]["phase"] == "message_approval"

    assert _request(app, f"/v1/candidate/runs/{run_id}/continue", token="intruder",
                    method="POST")[0] == "401 Unauthorized"
    status, completed = _request(
        app, f"/v1/candidate/runs/{run_id}/continue", method="POST",
    )
    assert status == "200 OK"
    assert completed == {"run_id": run_id, "phase": "complete"}
    assert _request(app, status_path)[1]["phase"] == "complete"
    assert loop.approvals == 1
    assert loop.continuations == 1
