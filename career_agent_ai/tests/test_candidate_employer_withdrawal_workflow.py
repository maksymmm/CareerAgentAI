from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from threading import Barrier
import sqlite3
import json

import pytest

from career_agent_ai.application.external_actions import ExternalActionStatus
from career_agent_ai.application.external_actions.external_action_operation import (
    ExternalActionOperation,
)
from career_agent_ai.application.jobs.candidate_employer_application_withdrawal_service import (
    CandidateEmployerApplicationWithdrawalService,
)
from career_agent_ai.application.jobs.job_application import JobApplication
from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus
from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import (
    SQLiteExternalActionOperationRepository,
)
from career_agent_ai.application.storage.sqlite_job_application_repository import (
    SQLiteJobApplicationRepository,
)
from career_agent_ai.application.storage.sqlite_sandbox_submission_provider import (
    SQLiteSandboxSubmissionProvider,
)
from career_agent_ai.application.storage.sqlite_sandbox_withdrawal_provider import (
    SandboxApplicationWithdrawalOutcome,
    SQLiteSandboxApplicationWithdrawalProvider,
    scripted_withdrawal_outcomes,
)

APPLICATION_ID = "application-1"
USER_ID = "candidate-1"
SUBMISSION_OPERATION_ID = "application-submit:application-1"
CONTENT = "Synthetic approved application artifact"
DIGEST = sha256(CONTENT.encode("utf-8")).hexdigest()
SNAPSHOT = "b" * 64
NOW = datetime(2026, 11, 25, tzinfo=timezone.utc)


def _seed_application(path, *, with_submission: bool = True) -> str | None:
    database = SQLiteDatabase(str(path))
    applications = SQLiteJobApplicationRepository(database)
    operations = SQLiteExternalActionOperationRepository(database)
    provider_submission_id = None
    operation_ids: tuple[str, ...] = ()

    if with_submission:
        submission_provider = SQLiteSandboxSubmissionProvider(database)
        result = submission_provider.submit(
            SUBMISSION_OPERATION_ID,
            "job-1",
            APPLICATION_ID,
            CONTENT,
            DIGEST,
            SNAPSHOT,
        )
        provider_submission_id = result["provider_submission_id"]
        operations.create(
            ExternalActionOperation(
                operation_id=SUBMISSION_OPERATION_ID,
                action_type="application.submit",
                payload={
                    "application_id": APPLICATION_ID,
                    "job_id": "job-1",
                    "artifact_sha256": DIGEST,
                    "candidate_snapshot_sha256": SNAPSHOT,
                },
            )
        )
        operations.transition(
            SUBMISSION_OPERATION_ID,
            ExternalActionStatus.PREPARED,
            ExternalActionStatus.IN_PROGRESS,
        )
        operations.transition(
            SUBMISSION_OPERATION_ID,
            ExternalActionStatus.IN_PROGRESS,
            ExternalActionStatus.SUCCEEDED,
            result=result,
        )
        operation_ids = (SUBMISSION_OPERATION_ID,)

    saved = JobApplication(
        application_id=APPLICATION_ID,
        user_id=USER_ID,
        job_id="job-1",
        company_id="Sandbox GmbH",
        job_title="Platform Engineer",
        status=JobApplicationStatus.SAVED,
        created_at=NOW,
        updated_at=NOW,
    )
    applied = saved.transition(
        JobApplicationStatus.APPLIED,
        occurred_at=NOW,
        operation_id=SUBMISSION_OPERATION_ID if with_submission else None,
        note="Synthetic sandbox submission completed." if with_submission else "Imported application.",
    )
    # A provider-backed withdrawal is allowed only when a durable submission operation
    # is linked to the candidate-owned application.
    if not with_submission:
        applied = JobApplication(
            application_id=APPLICATION_ID,
            user_id=USER_ID,
            job_id="job-1",
            company_id="Sandbox GmbH",
            job_title="Platform Engineer",
            status=JobApplicationStatus.APPLIED,
            created_at=NOW,
            updated_at=NOW,
        )
    applications.add(applied)
    database.close()
    return provider_submission_id


def _body(**overrides):
    return {
        "schema_version": 1,
        "application_id": APPLICATION_ID,
        "state_version": 2,
        "confirm_employer_withdrawal": True,
    } | overrides


def _request(app, body, *, token: str | None = "valid", method: str = "POST"):
    statuses = []
    encoded = json.dumps(body).encode("utf-8")
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": "/v1/candidate/application/withdraw-employer",
        "QUERY_STRING": "",
        "HTTP_AUTHORIZATION": f"Bearer {token}" if token else "",
        "CONTENT_LENGTH": str(len(encoded)),
        "CONTENT_TYPE": "application/json",
        "wsgi.input": BytesIO(encoded),
    }
    response = b"".join(
        app(environ, lambda status, headers: statuses.append((status, headers)))
    )
    return statuses[0][0], dict(statuses[0][1]), json.loads(response)


def _app(path, plan=None, *, token_owner=USER_ID):
    return RuntimeCandidateApp(
        database_path=str(path),
        jobs=(),
        resolve_bearer=lambda token: token_owner if token == "valid" else None,
        withdrawal_outcome_plan=plan,
    )


def _inspect(path, operation_id):
    database = SQLiteDatabase(str(path))
    provider = SQLiteSandboxApplicationWithdrawalProvider(database)
    operations = SQLiteExternalActionOperationRepository(database)
    applications = SQLiteJobApplicationRepository(database)
    result = (
        provider.attempts(operation_id),
        provider.ledger_entries(),
        operations.get(operation_id),
        applications.get_owned(APPLICATION_ID, user_id=USER_ID),
    )
    database.close()
    return result


def test_candidate_approved_withdrawal_records_one_provider_effect_and_tracker_transition(tmp_path):
    path = tmp_path / "candidate-withdrawal.sqlite"
    _seed_application(path)
    operation_id = CandidateEmployerApplicationWithdrawalService.operation_id_for(
        USER_ID, APPLICATION_ID
    )
    app = _app(path)

    status, headers, body = _request(app, _body())

    assert status == "200 OK"
    assert headers["Cache-Control"] == "no-store"
    assert body == {
        "application_id": APPLICATION_ID,
        "status": "withdrawn",
        "state_version": 3,
        "updated_at": body["updated_at"],
        "provider_status": "confirmed",
    }
    assert datetime.fromisoformat(body["updated_at"]) >= NOW
    attempts, effects, operation, application = _inspect(path, operation_id)
    assert attempts == (SandboxApplicationWithdrawalOutcome.SUCCESS,)
    assert len(effects) == 1
    assert operation.status is ExternalActionStatus.SUCCEEDED
    assert application.status is JobApplicationStatus.WITHDRAWN
    assert operation_id in application.external_action_operation_ids
    assert application.timeline[-1].metadata["provider_withdrawal_confirmed"] is True

    # A refreshed, version-bound replay is read-only and does not call the provider.
    replay_status, _, replay_body = _request(
        app, _body(state_version=3)
    )
    assert replay_status == "200 OK"
    assert replay_body == body
    attempts, effects, _, _ = _inspect(path, operation_id)
    assert attempts == (SandboxApplicationWithdrawalOutcome.SUCCESS,)
    assert effects[0].replays == 0
    app.close()


def test_confirmed_no_effect_can_retry_only_on_a_second_explicit_candidate_request(tmp_path):
    path = tmp_path / "candidate-withdrawal-no-effect.sqlite"
    _seed_application(path)
    operation_id = CandidateEmployerApplicationWithdrawalService.operation_id_for(
        USER_ID, APPLICATION_ID
    )
    plan = scripted_withdrawal_outcomes({
        operation_id: [
            SandboxApplicationWithdrawalOutcome.NO_EFFECT,
            SandboxApplicationWithdrawalOutcome.SUCCESS,
        ]
    })
    app = _app(path, plan)

    first_status, _, first_body = _request(app, _body())
    assert first_status == "409 Conflict"
    assert first_body == {"error": "approval_conflict"}
    attempts, effects, operation, application = _inspect(path, operation_id)
    assert attempts == (SandboxApplicationWithdrawalOutcome.NO_EFFECT,)
    assert effects == ()
    assert operation.status is ExternalActionStatus.FAILED
    assert application.status is JobApplicationStatus.APPLIED

    second_status, _, second_body = _request(app, _body())
    assert second_status == "200 OK"
    assert second_body["provider_status"] == "confirmed"
    attempts, effects, operation, application = _inspect(path, operation_id)
    assert attempts == (
        SandboxApplicationWithdrawalOutcome.NO_EFFECT,
        SandboxApplicationWithdrawalOutcome.SUCCESS,
    )
    assert len(effects) == 1 and effects[0].replays == 0
    assert operation.status is ExternalActionStatus.SUCCEEDED
    assert application.status is JobApplicationStatus.WITHDRAWN
    app.close()


def test_timeout_after_provider_effect_is_reconciled_without_a_second_attempt(tmp_path):
    path = tmp_path / "candidate-withdrawal-timeout.sqlite"
    _seed_application(path)
    operation_id = CandidateEmployerApplicationWithdrawalService.operation_id_for(
        USER_ID, APPLICATION_ID
    )
    plan = scripted_withdrawal_outcomes({
        operation_id: [SandboxApplicationWithdrawalOutcome.TIMEOUT_AFTER_EFFECT]
    })
    app = _app(path, plan)

    status, _, body = _request(app, _body())

    assert status == "200 OK"
    assert body["provider_status"] == "confirmed"
    attempts, effects, operation, application = _inspect(path, operation_id)
    assert attempts == (SandboxApplicationWithdrawalOutcome.TIMEOUT_AFTER_EFFECT,)
    assert len(effects) == 1 and effects[0].replays == 0
    assert operation.status is ExternalActionStatus.SUCCEEDED
    assert application.status is JobApplicationStatus.WITHDRAWN
    app.close()


def test_foreign_owner_and_missing_submission_fail_before_withdrawal_provider_effect(tmp_path):
    foreign_path = tmp_path / "foreign-owner.sqlite"
    _seed_application(foreign_path)
    operation_id = CandidateEmployerApplicationWithdrawalService.operation_id_for(
        USER_ID, APPLICATION_ID
    )
    foreign_app = _app(foreign_path, token_owner="another-candidate")
    status, _, body = _request(foreign_app, _body())
    assert status == "404 Not Found"
    assert body == {"error": "not_found"}
    attempts, effects, _, _ = _inspect(foreign_path, operation_id)
    assert attempts == () and effects == ()
    foreign_app.close()

    missing_path = tmp_path / "missing-submission.sqlite"
    _seed_application(missing_path, with_submission=False)
    missing_app = _app(missing_path)
    status, _, body = _request(missing_app, _body(state_version=1))
    assert status == "409 Conflict"
    assert body == {"error": "approval_conflict"}
    attempts, effects, _, application = _inspect(missing_path, operation_id)
    assert attempts == () and effects == ()
    assert application.status is JobApplicationStatus.APPLIED
    missing_app.close()


@pytest.mark.parametrize(
    "overrides",
    [
        {"confirm_employer_withdrawal": False},
        {"provider_submission_id": "client-controlled"},
        {"unknown": "field"},
        {"state_version": 0},
        {"state_version": 2**63},
    ],
)
def test_withdrawal_http_rejects_unapproved_or_noncanonical_body_without_provider_effect(
    tmp_path, overrides
):
    path = tmp_path / "invalid-body.sqlite"
    _seed_application(path)
    app = _app(path)
    status, _, body = _request(app, _body(**overrides))
    assert status == "400 Bad Request"
    assert body["error"] == "invalid_request"
    operation_id = CandidateEmployerApplicationWithdrawalService.operation_id_for(
        USER_ID, APPLICATION_ID
    )
    attempts, effects, operation, application = _inspect(path, operation_id)
    assert attempts == () and effects == ()
    assert operation is None
    assert application.status is JobApplicationStatus.APPLIED
    app.close()


def test_employer_withdrawal_openapi_is_strict_and_separate_from_tracker_only_actions():
    from career_agent_ai.application.api import candidate_approval_openapi_document

    document = candidate_approval_openapi_document()
    operation = document["paths"]["/v1/candidate/application/withdraw-employer"]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["confirm_employer_withdrawal"]["const"] is True
    assert "provider_submission_id" not in schema["properties"]
    assert operation["responses"]["200"]["description"].startswith(
        "Candidate-approved sandbox provider withdrawal"
    )


def test_foreign_owner_lookup_does_not_decode_corrupt_private_application_children(tmp_path):
    path = tmp_path / "foreign-corrupt-application.sqlite"
    _seed_application(path)
    raw = sqlite3.connect(path)
    raw.execute(
        "UPDATE job_applications SET job_skills_json = 'not-json' WHERE application_id = ?",
        (APPLICATION_ID,),
    )
    raw.commit()
    raw.close()

    operation_id = CandidateEmployerApplicationWithdrawalService.operation_id_for(
        USER_ID, APPLICATION_ID
    )
    app = _app(path, token_owner="another-candidate")
    status, _, body = _request(app, _body())
    assert status == "404 Not Found"
    assert body == {"error": "not_found"}

    database = SQLiteDatabase(str(path))
    provider = SQLiteSandboxApplicationWithdrawalProvider(database)
    assert provider.attempts(operation_id) == ()
    assert provider.ledger_entries() == ()
    database.close()
    app.close()


def test_stale_version_and_get_method_fail_before_provider_effect(tmp_path):
    path = tmp_path / "candidate-withdrawal-stale.sqlite"
    _seed_application(path)
    operation_id = CandidateEmployerApplicationWithdrawalService.operation_id_for(
        USER_ID, APPLICATION_ID
    )
    app = _app(path)

    stale_status, _, stale_body = _request(app, _body(state_version=1))
    assert stale_status == "409 Conflict"
    assert stale_body == {"error": "approval_conflict"}
    get_status, _, get_body = _request(app, _body(), method="GET")
    assert get_status == "405 Method Not Allowed"
    assert get_body == {"error": "method_not_allowed"}

    attempts, effects, operation, application = _inspect(path, operation_id)
    assert attempts == () and effects == () and operation is None
    assert application.status is JobApplicationStatus.APPLIED
    app.close()


def test_retry_fails_closed_when_durable_withdrawal_intent_is_rebound(tmp_path):
    path = tmp_path / "candidate-withdrawal-corrupt-intent.sqlite"
    _seed_application(path)
    operation_id = CandidateEmployerApplicationWithdrawalService.operation_id_for(
        USER_ID, APPLICATION_ID
    )
    plan = scripted_withdrawal_outcomes({
        operation_id: [
            SandboxApplicationWithdrawalOutcome.NO_EFFECT,
            SandboxApplicationWithdrawalOutcome.SUCCESS,
        ]
    })
    app = _app(path, plan)
    first_status, _, _ = _request(app, _body())
    assert first_status == "409 Conflict"

    database = SQLiteDatabase(str(path))
    database.connection.execute(
        "UPDATE sandbox_application_withdrawal_intents "
        "SET provider_submission_id = ? WHERE operation_id = ?",
        ("different-submission", operation_id),
    )
    database.connection.commit()
    database.close()

    second_status, _, second_body = _request(app, _body())
    assert second_status == "409 Conflict"
    assert second_body == {"error": "approval_conflict"}
    attempts, effects, operation, application = _inspect(path, operation_id)
    assert attempts == (SandboxApplicationWithdrawalOutcome.NO_EFFECT,)
    assert effects == ()
    assert operation.status is ExternalActionStatus.FAILED
    assert application.status is JobApplicationStatus.APPLIED
    app.close()


def test_recent_in_progress_withdrawal_is_not_reclassified_or_retried(tmp_path):
    path = tmp_path / "candidate-withdrawal-live-operation.sqlite"
    provider_submission_id = _seed_application(path)
    operation_id = CandidateEmployerApplicationWithdrawalService.operation_id_for(
        USER_ID, APPLICATION_ID
    )
    database = SQLiteDatabase(str(path))
    operations = SQLiteExternalActionOperationRepository(database)
    operations.create(
        ExternalActionOperation(
            operation_id=operation_id,
            action_type="application.withdraw",
            payload={
                "application_id": APPLICATION_ID,
                "provider_submission_id": provider_submission_id,
            },
        )
    )
    operations.transition(
        operation_id,
        ExternalActionStatus.PREPARED,
        ExternalActionStatus.IN_PROGRESS,
    )
    database.close()

    app = _app(path)
    status, _, body = _request(app, _body())
    assert status == "409 Conflict"
    assert body == {"error": "approval_conflict"}
    attempts, effects, operation, application = _inspect(path, operation_id)
    assert attempts == () and effects == ()
    assert operation.status is ExternalActionStatus.IN_PROGRESS
    assert application.status is JobApplicationStatus.APPLIED
    app.close()


def test_concurrent_duplicate_candidate_approvals_create_one_provider_effect(tmp_path):
    path = tmp_path / "candidate-withdrawal-concurrent.sqlite"
    _seed_application(path)
    operation_id = CandidateEmployerApplicationWithdrawalService.operation_id_for(
        USER_ID, APPLICATION_ID
    )
    app = _app(path)
    barrier = Barrier(2)

    def invoke():
        barrier.wait()
        return _request(app, _body())

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: invoke(), range(2)))

    statuses = sorted(result[0] for result in results)
    assert statuses == ["200 OK", "409 Conflict"]
    attempts, effects, operation, application = _inspect(path, operation_id)
    assert attempts == (SandboxApplicationWithdrawalOutcome.SUCCESS,)
    assert len(effects) == 1 and effects[0].replays == 0
    assert operation.status is ExternalActionStatus.SUCCEEDED
    assert application.status is JobApplicationStatus.WITHDRAWN
    app.close()


def _start_candidate_browser_server(app):
    from threading import Thread
    from wsgiref.simple_server import WSGIRequestHandler, make_server

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, format, *args):
            pass

    server = make_server("127.0.0.1", 0, app, handler_class=QuietHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _open_candidate_browser(playwright, server):
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(f"http://127.0.0.1:{server.server_port}/sandbox/candidate")
    page.locator("#token").fill("valid")
    page.locator("#list-applications").click()
    page.locator("#owned-applications button").first.wait_for()
    page.locator("#owned-applications button").first.click()
    page.locator("#application-detail").get_by_text('"status": "applied"').wait_for()
    return browser, page


def test_candidate_browser_explicitly_approves_sandbox_employer_withdrawal_and_refreshes_history(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    path = tmp_path / "candidate-employer-withdrawal-browser.sqlite"
    _seed_application(path)
    operation_id = CandidateEmployerApplicationWithdrawalService.operation_id_for(
        USER_ID, APPLICATION_ID
    )
    app = _app(path)
    server, thread = _start_candidate_browser_server(app)
    try:
        with sync_playwright() as playwright:
            browser, page = _open_candidate_browser(playwright, server)
            confirmation = page.locator("#confirm-employer-withdrawal")
            submit = page.locator("#withdraw-employer-application")
            assert confirmation.is_enabled()
            assert submit.is_disabled()
            confirmation.check()
            assert submit.is_enabled()
            submit.click()
            page.locator("#employer-withdrawal-state").get_by_text("Sandbox provider confirmed").wait_for()
            detail = page.locator("#application-detail").text_content()
            assert '"status": "withdrawn"' in detail
            assert '"state_version": 3' in detail
            assert confirmation.is_disabled()
            assert submit.is_disabled()

            page.locator("#load-application-history").click()
            page.locator("#application-history").get_by_text('"to_status": "withdrawn"').wait_for()
            assert "No real employer was contacted" in page.locator(
                "#employer-withdrawal-state"
            ).text_content()
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        app.close()

    attempts, effects, operation, application = _inspect(path, operation_id)
    assert attempts == (SandboxApplicationWithdrawalOutcome.SUCCESS,)
    assert len(effects) == 1 and effects[0].replays == 0
    assert operation.status is ExternalActionStatus.SUCCEEDED
    assert application.status is JobApplicationStatus.WITHDRAWN
    assert application.version == 3


def test_candidate_browser_requires_fresh_explicit_confirmation_after_no_effect(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    path = tmp_path / "candidate-employer-withdrawal-retry-browser.sqlite"
    _seed_application(path)
    operation_id = CandidateEmployerApplicationWithdrawalService.operation_id_for(
        USER_ID, APPLICATION_ID
    )
    plan = scripted_withdrawal_outcomes({
        operation_id: [
            SandboxApplicationWithdrawalOutcome.NO_EFFECT,
            SandboxApplicationWithdrawalOutcome.SUCCESS,
        ]
    })
    app = _app(path, plan)
    server, thread = _start_candidate_browser_server(app)
    try:
        with sync_playwright() as playwright:
            browser, page = _open_candidate_browser(playwright, server)
            page.locator("#confirm-employer-withdrawal").check()
            page.locator("#withdraw-employer-application").click()
            page.locator("#employer-withdrawal-state").get_by_text("No automatic retry occurred").wait_for()
            assert page.locator("#application-detail").text_content() == ""
            assert page.locator("#confirm-employer-withdrawal").is_disabled()
            assert page.locator("#withdraw-employer-application").is_disabled()

            attempts, effects, operation, application = _inspect(path, operation_id)
            assert attempts == (SandboxApplicationWithdrawalOutcome.NO_EFFECT,)
            assert effects == ()
            assert operation.status is ExternalActionStatus.FAILED
            assert application.status is JobApplicationStatus.APPLIED

            # A new detail read and a second explicit checkbox decision are required.
            page.locator("#owned-applications button").first.click()
            page.locator("#application-detail").get_by_text('"status": "applied"').wait_for()
            page.locator("#confirm-employer-withdrawal").check()
            page.locator("#withdraw-employer-application").click()
            page.locator("#employer-withdrawal-state").get_by_text("Sandbox provider confirmed").wait_for()
            assert '"status": "withdrawn"' in page.locator(
                "#application-detail"
            ).text_content()
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        app.close()

    attempts, effects, operation, application = _inspect(path, operation_id)
    assert attempts == (
        SandboxApplicationWithdrawalOutcome.NO_EFFECT,
        SandboxApplicationWithdrawalOutcome.SUCCESS,
    )
    assert len(effects) == 1 and effects[0].replays == 0
    assert operation.status is ExternalActionStatus.SUCCEEDED
    assert application.status is JobApplicationStatus.WITHDRAWN
    assert application.version == 3


def test_candidate_browser_history_advance_invalidates_old_withdrawal_confirmation(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    path = tmp_path / "candidate-employer-withdrawal-stale-confirmation.sqlite"
    _seed_application(path)
    operation_id = CandidateEmployerApplicationWithdrawalService.operation_id_for(
        USER_ID, APPLICATION_ID
    )
    app = _app(path)
    server, thread = _start_candidate_browser_server(app)
    try:
        with sync_playwright() as playwright:
            browser, page = _open_candidate_browser(playwright, server)
            confirmation = page.locator("#confirm-employer-withdrawal")
            submit = page.locator("#withdraw-employer-application")
            confirmation.check()
            assert submit.is_enabled()

            # Simulate a second candidate tab completing the same approved action.
            status, _, body = _request(app, _body())
            assert status == "200 OK"
            assert body["provider_status"] == "confirmed"

            page.locator("#load-application-history").click()
            page.locator("#application-history").get_by_text('"state_version": 3').wait_for()
            assert not confirmation.is_checked()
            assert confirmation.is_disabled()
            assert submit.is_disabled()
            assert '"status": "withdrawn"' in page.locator(
                "#application-detail"
            ).text_content()
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        app.close()

    attempts, effects, operation, application = _inspect(path, operation_id)
    assert attempts == (SandboxApplicationWithdrawalOutcome.SUCCESS,)
    assert len(effects) == 1 and effects[0].replays == 0
    assert operation.status is ExternalActionStatus.SUCCEEDED
    assert application.status is JobApplicationStatus.WITHDRAWN
    assert application.version == 3
