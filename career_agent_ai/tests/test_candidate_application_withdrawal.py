from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path

import pytest

from career_agent_ai.application.api import (
    CandidateApprovalWSGIApp,
    candidate_approval_openapi_document,
)
from career_agent_ai.application.jobs.candidate_application_withdrawal_service import (
    CandidateApplicationWithdrawalService,
)
from career_agent_ai.application.jobs.job_application import JobApplication
from career_agent_ai.application.jobs.job_application_repository import (
    ApplicationConflictError,
    ApplicationQuery,
)
from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus
from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_job_application_repository import (
    SQLiteJobApplicationRepository,
)


def _application(
    application_id: str = "application-1",
    *,
    user_id: str = "candidate-1",
    status: JobApplicationStatus = JobApplicationStatus.SAVED,
) -> JobApplication:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return JobApplication(
        application_id=application_id,
        user_id=user_id,
        job_id="job-1",
        company_id="Sandbox GmbH",
        job_title="Platform Engineer",
        status=status,
        created_at=now,
        updated_at=now,
    )


def _request(
    app,
    *,
    method: str = "GET",
    token: str | None = "valid",
    content_type: str = "application/json",
    body=None,
    path: str = "/v1/candidate/application/withdraw",
    query: str = "",
):
    statuses = []
    encoded = b"" if body is None else json.dumps(body).encode("utf-8")
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "HTTP_AUTHORIZATION": f"Bearer {token}" if token else "",
        "CONTENT_LENGTH": str(len(encoded)),
        "CONTENT_TYPE": content_type,
        "wsgi.input": BytesIO(encoded),
    }
    response = b"".join(
        app(environ, lambda status, headers: statuses.append((status, headers)))
    )
    return statuses[0], json.loads(response)


def _api(loop):
    return CandidateApprovalWSGIApp(
        loop,
        resolve_bearer=lambda token: "candidate-1" if token == "valid" else None,
    )


def _withdrawal_body(**overrides):
    body = {
        "schema_version": 1,
        "application_id": "application-1",
        "state_version": 2,
        "confirm_withdrawal": True,
    }
    return body | overrides


def test_withdrawal_service_persists_candidate_decision_and_preserves_submission_link(tmp_path):
    path = tmp_path / "withdrawal.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteJobApplicationRepository(database)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    submitted = _application().transition(
        JobApplicationStatus.APPLIED,
        occurred_at=now + timedelta(seconds=1),
        operation_id="submission-operation-1",
        note="Sandbox submission completed.",
    )
    repository.add(submitted)
    database.close()

    service_database = SQLiteDatabase(str(path))
    service = CandidateApplicationWithdrawalService(
        SQLiteJobApplicationRepository(service_database),
        clock=lambda: now + timedelta(seconds=2),
    )
    result = service.record_withdrawal(
        "application-1", user_id="candidate-1", expected_state_version=2
    )
    service_database.close()

    assert result == {
        "application_id": "application-1",
        "status": "withdrawn",
        "state_version": 3,
        "updated_at": (now + timedelta(seconds=2)).isoformat(),
    }
    reopened = SQLiteDatabase(str(path))
    restored_repository = SQLiteJobApplicationRepository(reopened)
    restored = restored_repository.get_owned("application-1", user_id="candidate-1")
    assert restored is not None
    assert restored.status is JobApplicationStatus.WITHDRAWN
    assert restored.version == 3
    assert restored.external_action_operation_ids == ("submission-operation-1",)
    assert [event.to_status for event in restored.timeline] == [
        JobApplicationStatus.APPLIED,
        JobApplicationStatus.WITHDRAWN,
    ]
    assert "no employer notification was sent" in restored.timeline[-1].note
    assert restored_repository.find(ApplicationQuery(
        user_id="candidate-1",
        external_action_operation_id="submission-operation-1",
    )) == (restored,)
    assert reopened.connection.execute(
        "SELECT COUNT(*) FROM application_external_actions"
    ).fetchone()[0] == 1
    reopened.close()


def test_withdrawal_owner_lookup_does_not_decode_foreign_private_children(tmp_path):
    path = tmp_path / "foreign.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteJobApplicationRepository(database)
    repository.add(_application(user_id="another-candidate"))
    database.connection.execute(
        "UPDATE job_applications SET job_skills_json = 'not-json' WHERE user_id = ?",
        ("another-candidate",),
    )
    database.connection.commit()
    service = CandidateApplicationWithdrawalService(repository)
    with pytest.raises(KeyError, match="not found"):
        service.record_withdrawal(
            "application-1", user_id="candidate-1", expected_state_version=1
        )
    database.close()


def test_withdrawal_rejects_stale_version_and_terminal_status_without_mutation(tmp_path):
    path = tmp_path / "conflicts.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteJobApplicationRepository(database)
    applied = _application().transition(
        JobApplicationStatus.APPLIED,
        occurred_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
    )
    repository.add(applied)
    repository.add(_application("rejected", status=JobApplicationStatus.REJECTED))
    service = CandidateApplicationWithdrawalService(repository)

    with pytest.raises(ApplicationConflictError):
        service.record_withdrawal(
            "application-1", user_id="candidate-1", expected_state_version=1
        )
    with pytest.raises(ApplicationConflictError):
        service.record_withdrawal(
            "rejected", user_id="candidate-1", expected_state_version=1
        )
    assert repository.get("application-1") == applied
    assert repository.get("rejected").status is JobApplicationStatus.REJECTED
    database.close()


def test_withdrawal_cannot_race_an_inflight_submission_claim(tmp_path):
    path = tmp_path / "claim.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteJobApplicationRepository(database)
    repository.add(_application())
    repository.claim_submission(
        "application-1", "submission-operation-1",
        expected_version=1, claim_owner_id="submission-owner",
    )
    service = CandidateApplicationWithdrawalService(repository)

    with pytest.raises(ApplicationConflictError, match="reserved"):
        service.record_withdrawal(
            "application-1", user_id="candidate-1", expected_state_version=1
        )
    assert repository.get("application-1").status is JobApplicationStatus.SAVED
    database.close()


@pytest.mark.parametrize("expected_version", [True, False, 0, -1, 2**63])
def test_withdrawal_service_rejects_invalid_versions_before_lookup(tmp_path, expected_version):
    path = tmp_path / "invalid-version.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteJobApplicationRepository(database)
    service = CandidateApplicationWithdrawalService(repository)
    with pytest.raises(ValueError):
        service.record_withdrawal(
            "application-1", user_id="candidate-1",
            expected_state_version=expected_version,
        )
    assert repository.get("application-1") is None
    database.close()


class _WithdrawalLoop:
    def record_application_withdrawal(
        self, application_id: str, *, user_id: str, expected_state_version: int
    ):
        self.call = (application_id, user_id, expected_state_version)
        return {
            "application_id": application_id,
            "status": "withdrawn",
            "state_version": expected_state_version + 1,
            "updated_at": "2026-01-01T00:00:02+00:00",
        }


def test_withdrawal_http_binds_authenticated_owner_and_exact_version():
    loop = _WithdrawalLoop()
    (status, headers), body = _request(
        _api(loop), method="POST", body=_withdrawal_body()
    )
    assert status == "200 OK"
    assert ("Cache-Control", "no-store") in headers
    assert loop.call == ("application-1", "candidate-1", 2)
    assert body["status"] == "withdrawn"
    assert body["state_version"] == 3


@pytest.mark.parametrize("body", [
    {"schema_version": 1, "application_id": "application-1", "state_version": 2},
    _withdrawal_body(confirm_withdrawal=False),
    _withdrawal_body(confirm_withdrawal=1),
    _withdrawal_body(schema_version=True),
    _withdrawal_body(state_version=0),
    _withdrawal_body(state_version=2**63),
    _withdrawal_body(application_id=" padded"),
    _withdrawal_body(application_id="x" * 201),
    _withdrawal_body(user_id="another-candidate"),
])
def test_withdrawal_http_rejects_unconfirmed_or_ambiguous_body(body):
    loop = _WithdrawalLoop()
    (status, _), response = _request(_api(loop), method="POST", body=body)
    assert status == "400 Bad Request"
    assert response["error"] == "invalid_request"
    assert not hasattr(loop, "call")


def test_withdrawal_http_auth_method_content_type_and_query_are_strict():
    loop = _WithdrawalLoop()
    assert _request(_api(loop), token=None, method="POST", body=_withdrawal_body())[0][0] == "401 Unauthorized"
    (status, headers), body = _request(_api(loop), method="GET")
    assert status == "405 Method Not Allowed"
    assert ("Allow", "POST") in headers
    assert body == {"error": "method_not_allowed"}
    assert _request(
        _api(loop), method="POST", content_type="text/plain", body=_withdrawal_body()
    )[0][0] == "415 Unsupported Media Type"
    assert _request(
        _api(loop), method="POST", body=_withdrawal_body(), query="application_id=other"
    )[0][0] == "400 Bad Request"
    assert not hasattr(loop, "call")


def test_withdrawal_openapi_is_authenticated_strict_and_tracker_only():
    operation = candidate_approval_openapi_document()["paths"][
        "/v1/candidate/application/withdraw"
    ]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert operation["security"] == [{"bearerAuth": []}]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["confirm_withdrawal"] == {
        "type": "boolean", "const": True
    }
    assert operation["responses"]["200"]["headers"]["Cache-Control"]["schema"]["const"] == "no-store"
    assert "no employer notification" in operation["responses"]["200"]["description"]


def test_runtime_withdrawal_survives_restart_and_rejects_stale_replay(tmp_path):
    path = tmp_path / "runtime-withdrawal.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteJobApplicationRepository(database)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    repository.add(_application().transition(
        JobApplicationStatus.APPLIED,
        occurred_at=now + timedelta(seconds=1),
        operation_id="submission-operation-1",
        note="Submitted in sandbox.",
    ))
    repository.add(_application("foreign-application", user_id="another-candidate"))
    database.close()

    runtime = RuntimeCandidateApp(
        database_path=str(path), jobs=(),
        resolve_bearer=lambda token: "candidate-1" if token == "valid" else None,
    )
    (status, _), result = _request(
        runtime, method="POST", body=_withdrawal_body()
    )
    assert status == "200 OK"
    assert result["status"] == "withdrawn"
    runtime.close()

    restarted = RuntimeCandidateApp(
        database_path=str(path), jobs=(),
        resolve_bearer=lambda token: "candidate-1" if token == "valid" else None,
    )
    (status, _), detail = _request(
        restarted, path="/v1/candidate/application",
        query="application_id=application-1",
    )
    assert status == "200 OK"
    assert detail["application"]["status"] == "withdrawn"
    assert detail["application"]["state_version"] == 3
    (status, _), history = _request(
        restarted, path="/v1/candidate/application/history",
        query="application_id=application-1",
    )
    assert status == "200 OK"
    assert history["state_version"] == 3
    assert [item["to_status"] for item in history["items"]] == ["applied", "withdrawn"]

    (status, _), conflict = _request(
        restarted, method="POST", body=_withdrawal_body()
    )
    assert status == "409 Conflict"
    assert conflict == {"error": "approval_conflict"}
    for application_id in ("foreign-application", "missing-application"):
        (status, _), missing = _request(
            restarted, method="POST",
            body=_withdrawal_body(application_id=application_id, state_version=1),
        )
        assert status == "404 Not Found"
        assert missing == {"error": "not_found"}
    restarted.close()


def test_candidate_browser_records_tracker_withdrawal_without_employer_action():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from career_agent_ai.application.api.candidate_sandbox_ui import candidate_sandbox_html

    application = {
        "application_id": "application-1",
        "job_id": "job-1",
        "company_id": "Sandbox GmbH",
        "job_title": "Platform Engineer",
        "status": "applied",
        "state_version": 2,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:01+00:00",
    }
    calls = []

    def route_request(route):
        request = route.request
        if request.url.endswith("/sandbox/candidate"):
            route.fulfill(status=200, content_type="text/html; charset=utf-8",
                          body=candidate_sandbox_html().decode("utf-8"))
            return
        calls.append((request.method, request.url, request.post_data))
        assert request.headers.get("authorization") == "Bearer valid"
        if request.method == "GET" and "/v1/candidate/applications" in request.url:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "schema_version": 1, "items": [application], "next_after": None,
            }))
        elif request.method == "GET" and "/v1/candidate/application?" in request.url:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "schema_version": 1, "application": application,
            }))
        elif request.method == "POST" and request.url.endswith("/v1/candidate/application/withdraw"):
            calls[-1] = (request.method, request.url, request.post_data_json)
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "application_id": "application-1", "status": "withdrawn",
                "state_version": 3, "updated_at": "2026-01-01T00:00:02+00:00",
            }))
        else:
            route.fulfill(status=404, content_type="application/json",
                          body=json.dumps({"error": "not_found"}))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.route("http://candidate.test/**", route_request)
            page.on("dialog", lambda dialog: dialog.accept())
            page.goto("http://candidate.test/sandbox/candidate")
            page.locator("#token").fill("valid")
            page.locator("#list-applications").click()
            page.get_by_role("button", name="View application 1: application-1").click()
            page.wait_for_function(
                "document.querySelector('#withdraw-application') && "
                "!document.querySelector('#withdraw-application').disabled"
            )
            page.locator("#withdraw-application").click()
            page.wait_for_function(
                "document.querySelector('#application-detail-state').textContent.includes("
                "'No employer notification was sent.')"
            )
            assert '"status": "withdrawn"' in page.locator("#application-detail").inner_text()
            assert '"state_version": 3' in page.locator("#application-detail").inner_text()
            assert page.locator("#withdraw-application").is_disabled()
            assert any(
                method == "POST" and url.endswith("/v1/candidate/application/withdraw")
                and body == {
                    "schema_version": 1, "application_id": "application-1",
                    "state_version": 2, "confirm_withdrawal": True,
                }
                for method, url, body in calls
            )
            assert len([
                call for call in calls
                if call[0] == "POST" and call[1].endswith("/v1/candidate/application/withdraw")
            ]) == 1
        finally:
            browser.close()



def test_ci_runs_independent_coverage_cli_after_pytest():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    pytest_step = workflow.index("python -m pytest --cov=career_agent_ai") if "python -m pytest --cov=career_agent_ai" in workflow else workflow.index("--cov=career_agent_ai")
    independent_gate = workflow.index(
        "python -m coverage report --fail-under=90"
    )
    assert pytest_step < independent_gate
    assert "Enforce independent coverage threshold" in workflow



def test_withdrawal_uses_monotonic_timestamp_and_rejects_naive_clock(tmp_path):
    path = tmp_path / "clock.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteJobApplicationRepository(database)
    latest = datetime(2026, 1, 1, 0, 0, 5, tzinfo=timezone.utc)
    application = _application().transition(
        JobApplicationStatus.APPLIED, occurred_at=latest
    )
    repository.add(application)
    monotonic = CandidateApplicationWithdrawalService(
        repository, clock=lambda: latest - timedelta(seconds=10)
    )
    result = monotonic.record_withdrawal(
        "application-1", user_id="candidate-1", expected_state_version=2
    )
    assert result["updated_at"] == latest.isoformat()
    assert repository.get("application-1").timeline[-1].occurred_at == latest

    second = _application("application-2").transition(
        JobApplicationStatus.APPLIED, occurred_at=latest
    )
    repository.add(second)
    invalid_clock = CandidateApplicationWithdrawalService(
        repository, clock=lambda: datetime(2026, 1, 1)
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        invalid_clock.record_withdrawal(
            "application-2", user_id="candidate-1", expected_state_version=2
        )
    assert repository.get("application-2") == second
    database.close()


@pytest.mark.parametrize("application_id,user_id", [
    ("", "candidate-1"), (" padded", "candidate-1"),
    ("application-1", ""), ("application-1", "candidate-1 "),
])
def test_withdrawal_service_rejects_noncanonical_identifiers(
    tmp_path, application_id, user_id
):
    path = tmp_path / "identifier.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteJobApplicationRepository(database)
    service = CandidateApplicationWithdrawalService(repository)
    with pytest.raises(ValueError):
        service.record_withdrawal(
            application_id, user_id=user_id, expected_state_version=1
        )
    assert repository.get("application-1") is None
    database.close()


def test_runtime_public_withdrawal_method_requires_open_runtime(tmp_path):
    path = tmp_path / "runtime-method.sqlite"
    database = SQLiteDatabase(str(path))
    SQLiteJobApplicationRepository(database).add(_application())
    database.close()
    runtime = RuntimeCandidateApp(
        database_path=str(path), jobs=(),
        resolve_bearer=lambda token: "candidate-1" if token == "valid" else None,
    )
    result = runtime.record_application_withdrawal(
        "application-1", user_id="candidate-1", expected_state_version=1
    )
    assert result["status"] == "withdrawn"
    assert result["state_version"] == 2
    runtime.close()
    with pytest.raises(RuntimeError, match="closed"):
        runtime.record_application_withdrawal(
            "application-1", user_id="candidate-1", expected_state_version=2
        )
