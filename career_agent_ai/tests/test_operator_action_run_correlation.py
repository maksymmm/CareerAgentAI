from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json

import pytest

import career_agent_ai.application.runtime.composition as runtime_composition
from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopPhase,
    CareerLoopRequest,
)
from career_agent_ai.application.external_actions import (
    ExternalActionOperation,
    ExternalActionStatus,
)
from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.observability.references import stable_reference
from career_agent_ai.application.runtime.composition import (
    RuntimeOperationalApp,
    build_candidate_sandbox_app_from_env,
)
from career_agent_ai.application.scheduling.models import (
    ScheduleEvent,
    ScheduleEventType,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import (
    SQLiteExternalActionOperationRepository,
)
from career_agent_ai.application.storage.sqlite_operational_probe import (
    SQLiteOperationalProbe,
)

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
PROFILE = "Experienced logistics professional."
OPERATOR_TOKEN = "operator-correlation-test-token-" + "x" * 32


def _candidate_call(app, path: str, *, method: str = "GET", body=None):
    encoded = b"" if body is None else json.dumps(body).encode("utf-8")
    statuses = []
    response = app(
        {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "HTTP_AUTHORIZATION": "Bearer owner",
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": str(len(encoded)),
            "wsgi.input": BytesIO(encoded),
        },
        lambda status, headers: statuses.append(status),
    )
    return statuses[0], json.loads(b"".join(response))


def _operator_call(app):
    statuses = []
    response = app(
        {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/v1/operational/actions",
            "QUERY_STRING": "",
            "HTTP_AUTHORIZATION": f"Bearer {OPERATOR_TOKEN}",
            "wsgi.input": BytesIO(b""),
        },
        lambda status, headers: statuses.append(status),
    )
    return statuses[0], json.loads(b"".join(response))


def test_candidate_action_operations_correlate_across_all_sandbox_providers(
    tmp_path, monkeypatch
):
    """One durable candidate run is safely cross-referenced through all three providers."""
    database_path = str(tmp_path / "candidate-action-correlation.sqlite")
    environ = {
        "CAREER_AGENT_DB_PATH": database_path,
        "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
        "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
    }
    jobs = (
        Job.create(
            job_id="correlation-job",
            title="Logistics Coordinator",
            company=Company("correlation-company", "Sandbox Logistics"),
            location=Location("Germany", "Karlsruhe"),
            description="logistics coordination",
            created_at=NOW,
        ),
    )

    attempts = {"application": 0, "communication": 0, "calendar": 0}
    original_submit = runtime_agent = runtime_composition.SQLiteSandboxSubmissionProvider.submit
    original_send = runtime_composition.FakeCommunicationAdapter.send
    original_accept = runtime_composition.FakeCalendarAdapter.accept

    def ambiguous_application_once(self, *args, **kwargs):
        attempts["application"] += 1
        if attempts["application"] == 1:
            raise RuntimeError("synthetic application timeout before effect")
        return original_submit(self, *args, **kwargs)

    def ambiguous_message_once(self, *args, **kwargs):
        attempts["communication"] += 1
        if attempts["communication"] == 1:
            raise RuntimeError("synthetic message timeout before effect")
        return original_send(self, *args, **kwargs)

    def ambiguous_calendar_once(self, *args, **kwargs):
        attempts["calendar"] += 1
        if attempts["calendar"] == 1:
            raise RuntimeError("synthetic calendar timeout before effect")
        return original_accept(self, *args, **kwargs)

    monkeypatch.setattr(
        runtime_composition.SQLiteSandboxSubmissionProvider,
        "submit",
        ambiguous_application_once,
    )
    monkeypatch.setattr(
        runtime_composition.FakeCommunicationAdapter,
        "send",
        ambiguous_message_once,
    )
    monkeypatch.setattr(
        runtime_composition.FakeCalendarAdapter,
        "accept",
        ambiguous_calendar_once,
    )

    resolve_bearer = lambda token: "candidate-1" if token == "owner" else None
    candidate = build_candidate_sandbox_app_from_env(
        resolve_bearer=resolve_bearer, jobs=jobs, environ=environ
    )
    operator = RuntimeOperationalApp(
        database_path=database_path,
        bearer_token=OPERATOR_TOKEN,
    )
    run_id = None
    try:
        started = candidate.start(
            CareerLoopRequest(
                user_id="candidate-1",
                keyword="logistics",
                candidate_profile=PROFILE,
                location="Karlsruhe",
                sender="candidate@example.test",
                recipient="recruiter@example.test",
                schedule_event_id="correlation-interview-event",
            ),
            run_id="operator-correlation-workflow",
        )
        run_id = started.run_id
        assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL

        database = SQLiteDatabase(database_path)
        try:
            from career_agent_ai.application.storage.sqlite_scheduling_repository import (
                SQLiteSchedulingRepository,
            )

            SQLiteSchedulingRepository(database).create(
                ScheduleEvent(
                    event_id="correlation-interview-event",
                    candidate_id="candidate-1",
                    employer_name="Sandbox Logistics",
                    event_type=ScheduleEventType.INTERVIEW,
                    location="Karlsruhe",
                    start_at=NOW + timedelta(days=2),
                    end_at=NOW + timedelta(days=2, hours=1),
                    timezone_name="Europe/Berlin",
                    application_id=f"{run_id}:application",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        finally:
            database.close()

        approval_path = f"/v1/candidate/runs/{run_id}/approval"
        status_path = f"/v1/candidate/runs/{run_id}/status"

        def approve_current_action():
            status, prompt = _candidate_call(candidate, approval_path)
            assert status == "200 OK"
            return _candidate_call(
                candidate,
                approval_path,
                method="POST",
                body={
                    "schema_version": 1,
                    "run_id": run_id,
                    "state_version": prompt["state_version"],
                    "action_fingerprint": prompt["action_fingerprint"],
                    "decision": "approve",
                },
            )

        def status_snapshot():
            status, snapshot = _candidate_call(candidate, status_path)
            assert status == "200 OK"
            assert snapshot["run_ref"] == stable_reference(run_id)
            return snapshot

        def assert_operator_correlation(expected_family: str):
            before = (tmp_path / "candidate-action-correlation.sqlite").read_bytes()
            status, projection = _operator_call(operator)
            assert status == "200 OK"
            assert (tmp_path / "candidate-action-correlation.sqlite").read_bytes() == before
            assert projection["schema_version"] == 1
            assert len(projection["items"]) == 1
            item = projection["items"][0]
            assert item["action_family"] == expected_family
            assert item["candidate_run_ref"] == stable_reference(run_id)
            assert run_id not in json.dumps(projection)
            return item

        status, result = approve_current_action()
        assert status == "200 OK"
        assert result["phase"] == CareerLoopPhase.APPLICATION_RECONCILIATION.value
        snapshot = status_snapshot()
        assert_operator_correlation("application_submission")
        status, result = _candidate_call(
            candidate,
            f"/v1/candidate/runs/{run_id}/application-reconciliation",
            method="POST",
            body={
                "schema_version": 1,
                "state_version": snapshot["state_version"],
                "submitted": False,
            },
        )
        assert status == "200 OK"
        assert result["phase"] == CareerLoopPhase.MESSAGE_APPROVAL.value

        status, result = approve_current_action()
        assert status == "200 OK"
        assert result["phase"] == CareerLoopPhase.MESSAGE_RECONCILIATION.value
        snapshot = status_snapshot()
        assert_operator_correlation("communication")
        status, result = _candidate_call(
            candidate,
            f"/v1/candidate/runs/{run_id}/message-reconciliation",
            method="POST",
            body={
                "schema_version": 1,
                "state_version": snapshot["state_version"],
                "delivered": False,
            },
        )
        assert status == "200 OK"
        assert result["phase"] == CareerLoopPhase.INTERVIEW_APPROVAL.value

        status, result = approve_current_action()
        assert status == "200 OK"
        assert result["phase"] == CareerLoopPhase.INTERVIEW_RECONCILIATION.value
        snapshot = status_snapshot()
        assert_operator_correlation("calendar")
        status, result = _candidate_call(
            candidate,
            f"/v1/candidate/runs/{run_id}/interview-reconciliation",
            method="POST",
            body={
                "schema_version": 1,
                "state_version": snapshot["state_version"],
                "completed": False,
            },
        )
        assert status == "200 OK"
        assert result["phase"] == CareerLoopPhase.COMPLETE.value
        assert attempts == {"application": 2, "communication": 2, "calendar": 2}
    finally:
        operator.close()
        candidate.close()

    database = SQLiteDatabase(database_path)
    try:
        rows = database.connection.execute(
            "SELECT action_type, status, correlation_id "
            "FROM external_action_operations ORDER BY action_type"
        ).fetchall()
    finally:
        database.close()
    assert rows == [
        ("application.submit", ExternalActionStatus.SUCCEEDED.value, run_id),
        ("calendar.accept", ExternalActionStatus.SUCCEEDED.value, run_id),
        ("communication.send", ExternalActionStatus.SUCCEEDED.value, run_id),
    ]


def test_legacy_action_schema_remains_readable_and_migrates_correlation_metadata(tmp_path):
    path = tmp_path / "legacy-action-schema.sqlite"
    database = SQLiteDatabase(str(path))
    database.connection.execute(
        """
        CREATE TABLE external_action_operations (
            operation_id TEXT PRIMARY KEY,
            action_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    database.connection.execute(
        """
        INSERT INTO external_action_operations (
            operation_id, action_type, payload_json, status,
            result_json, error, created_at, updated_at
        ) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)
        """,
        (
            "legacy-operation",
            "application.submit",
            '{"application_id":"legacy-app"}',
            "prepared",
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )
    database.connection.commit()
    database.close()

    read_only = SQLiteDatabase.open_read_only(str(path))
    try:
        legacy_page = SQLiteOperationalProbe(read_only).list_action_statuses()
        assert len(legacy_page["items"]) == 1
        assert legacy_page["items"][0]["candidate_run_ref"] is None
    finally:
        read_only.close()

    database = SQLiteDatabase(str(path))
    try:
        repository = SQLiteExternalActionOperationRepository(database)
        legacy = repository.get("legacy-operation")
        assert legacy is not None
        assert legacy.correlation_id is None

        original = ExternalActionOperation(
            operation_id="correlated-operation",
            action_type="application.submit",
            payload={"application_id": "correlated-app"},
            created_at=NOW,
            updated_at=NOW,
            correlation_id="candidate-run-a",
        )
        created = repository.create(original)
        replay = repository.create(
            ExternalActionOperation(
                operation_id="correlated-operation",
                action_type="application.submit",
                payload={"application_id": "correlated-app"},
                created_at=NOW,
                updated_at=NOW,
                correlation_id="candidate-run-b",
            )
        )
        assert created.correlation_id == "candidate-run-a"
        assert replay.correlation_id == "candidate-run-a"

        page = SQLiteOperationalProbe(database).list_action_statuses()
        by_ref = {item["operation_ref"]: item for item in page["items"]}
        assert by_ref[stable_reference("correlated-operation")]["candidate_run_ref"] == (
            stable_reference("candidate-run-a")
        )
    finally:
        database.close()


@pytest.mark.parametrize(
    "correlation_id",
    ["", " leading", "trailing ", "x" * 201, "bad\nrun", chr(0xD800)],
)
def test_external_action_rejects_malformed_correlation_id(correlation_id):
    with pytest.raises((TypeError, ValueError)):
        ExternalActionOperation(
            operation_id="valid-operation",
            action_type="application.submit",
            payload={},
            correlation_id=correlation_id,
        )
