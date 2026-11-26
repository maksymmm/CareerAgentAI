from __future__ import annotations

from io import BytesIO
import json

from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
from career_agent_ai.application.storage.sqlite_candidate_applications import SQLiteCandidateApplications
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_sandbox_submission_provider import SandboxOutcome


def _request(runtime, method: str, path: str, body=None, *, token: str = "valid"):
    encoded = b"" if body is None else json.dumps(body).encode("utf-8")
    statuses = []
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "HTTP_AUTHORIZATION": "Bearer " + token,
        "CONTENT_LENGTH": str(len(encoded)),
        "CONTENT_TYPE": "application/json",
        "wsgi.input": BytesIO(encoded),
    }
    response = b"".join(
        runtime(environ, lambda status, headers: statuses.append((status, headers)))
    )
    assert len(statuses) == 1
    status, headers = statuses[0]
    return status, dict(headers), json.loads(response)


def _runtime(path, outcome_plan):
    job = Job.create(
        job_id="job-1",
        title="Logistics Coordinator",
        company=Company("company-1", "Acme Logistics"),
        location=Location("Germany", "Karlsruhe"),
        description="Warehouse logistics coordination",
    )
    return RuntimeCandidateApp(
        database_path=str(path),
        jobs=(job,),
        resolve_bearer=lambda token: {
            "valid": "candidate-1",
            "foreign": "candidate-2",
        }.get(token),
        sandbox_outcome_plan=outcome_plan,
    )


def _provider_rows(path):
    database = SQLiteDatabase.open_read_only(str(path))
    try:
        attempts = database.connection.execute(
            "SELECT idempotency_key, attempt_no, outcome "
            "FROM sandbox_submission_attempts ORDER BY idempotency_key, attempt_no"
        ).fetchall()
        effects = database.connection.execute(
            "SELECT idempotency_key, application_id, provider_submission_id, replays "
            "FROM sandbox_submission_ledger ORDER BY idempotency_key"
        ).fetchall()
        return attempts, effects
    finally:
        database.close()


def test_candidate_retries_confirmed_no_effect_only_after_explicit_continue_after_restart(
    tmp_path,
):
    path = tmp_path / "candidate-no-effect-restart.sqlite"
    plan_calls = []

    def outcome_plan(operation_id: str, attempt_no: int) -> SandboxOutcome:
        plan_calls.append((operation_id, attempt_no))
        if attempt_no == 1:
            return SandboxOutcome.NO_EFFECT
        if attempt_no == 2:
            return SandboxOutcome.SUCCESS
        raise AssertionError("The candidate workflow attempted an unexpected submission.")

    first = _runtime(path, outcome_plan)
    try:
        status, headers, started = _request(
            first,
            "POST",
            "/v1/candidate/runs",
            {
                "schema_version": 1,
                "run_id": "no-effect-restart",
                "keyword": "Logistics",
                "candidate_profile": (
                    "Logistics professional with warehouse picking, packing, "
                    "inventory control, and daily workflow coordination experience."
                ),
                "location": "Karlsruhe",
            },
        )
        assert status == "201 Created"
        assert headers["Cache-Control"] == "no-store"
        assert started["phase"] == "application_approval"
        run_id = started["run_id"]

        status, _, prompt = _request(
            first, "GET", f"/v1/candidate/runs/{run_id}/approval"
        )
        assert status == "200 OK"
        application_id = prompt["details"]["application_id"]

        status, _, approved = _request(
            first,
            "POST",
            f"/v1/candidate/runs/{run_id}/approval",
            {
                "schema_version": 1,
                "run_id": run_id,
                "state_version": prompt["state_version"],
                "action_fingerprint": prompt["action_fingerprint"],
                "decision": "approve",
            },
        )
        assert status == "200 OK"
        assert approved == {"run_id": run_id, "phase": "application_submit"}
        assert len(plan_calls) == 1
    finally:
        first.close()

    attempts, effects = _provider_rows(path)
    assert len(attempts) == 1
    assert attempts[0][1:] == (1, "no_effect")
    assert effects == []
    assert SQLiteCandidateApplications(str(path)).get_snapshot(
        application_id, user_id="candidate-1"
    )["application"]["status"] == "saved"

    second = _runtime(path, outcome_plan)
    try:
        status, _, foreign = _request(
            second, "GET", f"/v1/candidate/runs/{run_id}/status", token="foreign"
        )
        assert status == "404 Not Found"
        assert foreign == {"error": "not_found"}

        status, _, observed = _request(
            second, "GET", f"/v1/candidate/runs/{run_id}/status"
        )
        assert status == "200 OK"
        assert observed["phase"] == "application_submit"
        assert observed["waiting_for_human"] is False
        assert observed["terminal"] is False

        status, _, unavailable = _request(
            second, "GET", f"/v1/candidate/runs/{run_id}/approval"
        )
        assert status == "409 Conflict"
        assert unavailable == {"error": "approval_unavailable"}
        assert len(plan_calls) == 1
        assert _provider_rows(path) == (attempts, effects)

        status, _, resumed = _request(
            second, "POST", f"/v1/candidate/runs/{run_id}/continue"
        )
        assert status == "200 OK"
        assert resumed == {"run_id": run_id, "phase": "complete"}
    finally:
        second.close()

    attempts, effects = _provider_rows(path)
    assert len(attempts) == 2
    assert attempts[0][0] == attempts[1][0]
    assert [row[1:] for row in attempts] == [
        (1, "no_effect"),
        (2, "success"),
    ]
    assert len(effects) == 1
    assert effects[0][0] == attempts[0][0]
    assert effects[0][1] == application_id
    assert effects[0][2]
    assert effects[0][3] == 0
    assert plan_calls == [(attempts[0][0], 1), (attempts[0][0], 2)]
    assert SQLiteCandidateApplications(str(path)).get_snapshot(
        application_id, user_id="candidate-1"
    )["application"]["status"] == "applied"

    third = _runtime(path, outcome_plan)
    try:
        status, _, terminal = _request(
            third, "GET", f"/v1/candidate/runs/{run_id}/status"
        )
        assert status == "200 OK"
        assert terminal["phase"] == "complete"
        assert terminal["waiting_for_human"] is False
        assert terminal["terminal"] is True

        status, _, replayed = _request(
            third, "POST", f"/v1/candidate/runs/{run_id}/continue"
        )
        assert status == "200 OK"
        assert replayed == {"run_id": run_id, "phase": "complete"}
    finally:
        third.close()

    assert _provider_rows(path) == (attempts, effects)
    assert len(plan_calls) == 2
