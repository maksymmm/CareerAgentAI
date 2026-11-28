from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import sqlite3
from threading import Event

from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopPhase,
    CareerLoopRequest,
    CareerLoopState,
    HumanActionEvent,
    HumanActionKind,
)
from career_agent_ai.application.jobs.job_application import JobApplication
from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus
from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
from career_agent_ai.application.scheduling.models import (
    HumanScheduleView,
    ScheduleEvent,
    ScheduleEventType,
)
from career_agent_ai.application.storage.sqlite_career_loop_repository import (
    SQLiteCareerLoopRepository,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_job_application_repository import (
    SQLiteJobApplicationRepository,
)
from career_agent_ai.application.storage.sqlite_sandbox_calendar_provider import (
    SQLiteSandboxCalendarProvider,
)
from career_agent_ai.application.storage.sqlite_scheduling_repository import (
    SQLiteSchedulingRepository,
)


OWNER = "candidate-runtime-race"
TOKEN = "candidate-runtime-race-token"
RUN_ID = "candidate-runtime-race"
EVENT_ID = "candidate-runtime-race-interview"
APPLICATION_ID = "candidate-runtime-race-application"
OPERATION_ID = RUN_ID + ":interview-accept"
NOW = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)


def _seed_pending_interview(path) -> None:
    """Persist one exact interview approval gate and its already-applied application."""
    database = SQLiteDatabase(str(path))
    try:
        event = ScheduleEvent(
            event_id=EVENT_ID,
            candidate_id=OWNER,
            employer_name="Sandbox Logistics",
            event_type=ScheduleEventType.INTERVIEW,
            location="Karlsruhe",
            start_at=NOW + timedelta(days=5),
            end_at=NOW + timedelta(days=5, hours=1),
            timezone_name="Europe/Berlin",
            application_id=APPLICATION_ID,
            created_at=NOW,
            updated_at=NOW,
        )
        SQLiteSchedulingRepository(database).create(event)
        SQLiteJobApplicationRepository(database).add(
            JobApplication(
                application_id=APPLICATION_ID,
                user_id=OWNER,
                job_id="sandbox-job-1",
                status=JobApplicationStatus.APPLIED,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        view = HumanScheduleView.from_event(event)
        details = {
            "event_id": view.event_id,
            "event_version": event.version,
            "employer": view.employer_name,
            "event_type": view.event_type.value,
            "location": view.location,
            "local_date": view.local_date,
            "local_start_time": view.local_start_time,
            "local_end_date": view.local_end_date,
            "local_end_time": view.local_end_time,
            "timezone": view.timezone_name,
            "utc_offset": view.utc_offset,
            "end_utc_offset": view.end_utc_offset,
            "status": view.status.value,
        }
        state = CareerLoopState(
            run_id=RUN_ID,
            request=CareerLoopRequest(
                user_id=OWNER,
                keyword="logistics",
                candidate_profile="Experienced logistics candidate.",
                schedule_event_id=EVENT_ID,
            ),
            phase=CareerLoopPhase.INTERVIEW_APPROVAL,
            application_id=APPLICATION_ID,
            pending_human_action=HumanActionEvent(
                kind=HumanActionKind.APPROVE_INTERVIEW,
                title="Approve interview/trial-day calendar response",
                details=details,
            ),
            created_at=NOW,
            updated_at=NOW,
        )
        SQLiteCareerLoopRepository(database).save(state)
    finally:
        database.close()


def _request(app, path: str, *, method: str = "GET", body=None):
    """Call one authenticated WSGI route and decode its JSON response."""
    encoded = b"" if body is None else json.dumps(body).encode("utf-8")
    statuses = []
    response = b"".join(
        app(
            {
                "REQUEST_METHOD": method,
                "PATH_INFO": path,
                "QUERY_STRING": "",
                "CONTENT_TYPE": "application/json",
                "CONTENT_LENGTH": str(len(encoded)),
                "HTTP_AUTHORIZATION": "Bearer " + TOKEN,
                "wsgi.input": BytesIO(encoded),
            },
            lambda status, headers: statuses.append((status, headers)),
        )
    )
    return statuses[0], json.loads(response)


def _runtime(path) -> RuntimeCandidateApp:
    """Build an isolated request-local runtime for the synthetic candidate."""
    return RuntimeCandidateApp(
        database_path=str(path),
        jobs=(),
        resolve_bearer=lambda token: OWNER if token == TOKEN else None,
    )


def test_duplicate_candidate_interview_approval_is_fenced_across_runtime_instances(
    tmp_path, monkeypatch
):
    """A stale duplicate HTTP approval cannot reach the calendar provider twice."""
    path = tmp_path / "candidate-interview-runtime-race.sqlite"
    _seed_pending_interview(path)
    approval_path = f"/v1/candidate/runs/{RUN_ID}/approval"
    status_path = f"/v1/candidate/runs/{RUN_ID}/status"

    first_runtime = _runtime(path)
    second_runtime = _runtime(path)
    provider_entered = Event()
    release_provider = Event()
    provider_calls = []
    original_deliver = SQLiteSandboxCalendarProvider._deliver

    def blocked_deliver(provider, action, operation_id, delivered, **kwargs):
        provider_calls.append((action, operation_id))
        provider_entered.set()
        if not release_provider.wait(timeout=10):
            raise TimeoutError("The test did not release the sandbox calendar provider.")
        return original_deliver(
            provider,
            action,
            operation_id,
            delivered,
            **kwargs,
        )

    monkeypatch.setattr(SQLiteSandboxCalendarProvider, "_deliver", blocked_deliver)

    try:
        (prompt_status, _), prompt = _request(
            first_runtime,
            approval_path,
        )
        assert prompt_status == "200 OK"
        assert prompt["details"]["event_version"] == 1
        assert prompt["details"]["status"] == "proposed"
        assert "provider_event_id" not in prompt["details"]
        submission = {
            "schema_version": 1,
            "run_id": RUN_ID,
            "state_version": prompt["state_version"],
            "action_fingerprint": prompt["action_fingerprint"],
            "decision": "approve",
        }

        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(
                _request,
                first_runtime,
                approval_path,
                method="POST",
                body=submission,
            )
            try:
                assert provider_entered.wait(timeout=5)
                duplicate_status, duplicate_body = _request(
                    second_runtime,
                    approval_path,
                    method="POST",
                    body=submission,
                )
                assert duplicate_status[0] == "409 Conflict"
                assert duplicate_body == {"error": "approval_conflict"}
                assert provider_calls == [("accept", OPERATION_ID)]
            finally:
                release_provider.set()
            first_status, first_body = first.result(timeout=10)

        assert first_status[0] == "200 OK"
        assert first_body == {"run_id": RUN_ID, "phase": "complete"}
        assert provider_calls == [("accept", OPERATION_ID)]
    finally:
        release_provider.set()
        first_runtime.close()
        second_runtime.close()

    # A new runtime must observe the terminal workflow state without another provider call.
    with _runtime(path) as restarted:
        (status, _), snapshot = _request(restarted, status_path)
        assert status[0] == "200 OK"
        assert snapshot["phase"] == "complete"
        assert snapshot["terminal"] is True
        assert snapshot["waiting_for_human"] is False

    connection = sqlite3.connect(path)
    try:
        assert connection.execute(
            "SELECT status FROM external_action_operations WHERE operation_id = ?",
            (OPERATION_ID,),
        ).fetchone() == ("succeeded",)
        assert connection.execute(
            "SELECT COUNT(*) FROM sandbox_calendar_attempts WHERE operation_id = ?",
            (OPERATION_ID,),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*), MIN(replays), MAX(replays) "
            "FROM sandbox_calendar_effects WHERE operation_id = ?",
            (OPERATION_ID,),
        ).fetchone() == (1, 0, 0)
        assert connection.execute(
            "SELECT status, version, provider_event_id FROM scheduling_events "
            "WHERE event_id = ?",
            (EVENT_ID,),
        ).fetchone() == ("accepted", 2, "fake-" + EVENT_ID)
        assert connection.execute(
            "SELECT status, version FROM job_applications WHERE application_id = ?",
            (APPLICATION_ID,),
        ).fetchone() == ("interview", 2)
    finally:
        connection.close()
