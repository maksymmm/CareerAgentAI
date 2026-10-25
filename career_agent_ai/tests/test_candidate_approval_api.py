from __future__ import annotations

from io import BytesIO
import json
import re
from threading import Thread
from wsgiref.simple_server import WSGIRequestHandler, make_server

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

    def replay_existing_start(self, request, *, run_id, user_id):
        raise KeyError(run_id)

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
    query="",
):
    statuses = []
    encoded = b"" if body is None else json.dumps(body).encode()
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
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


def test_run_collection_binds_authenticated_owner_and_query():
    from career_agent_ai.application.career.autonomous_loop_models import CareerLoopPhase

    class Collection:
        def list_run_statuses(self, **kwargs):
            assert kwargs == {"user_id": "candidate-1", "limit": 2, "after": "run-1",
                              "phase": CareerLoopPhase.APPLICATION_APPROVAL}
            return {"schema_version": 1, "items": [], "next_after": None}
    (status, headers), body = request(
        app(Collection()), path="/v1/candidate/run-statuses",
        query="limit=2&phase=application_approval&after=run-1",
    )
    assert status == "200 OK"
    assert ("Cache-Control", "no-store") in headers
    assert body == {"schema_version": 1, "items": [], "next_after": None}


def test_application_collection_binds_validated_status_filter():
    from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus

    class Collection:
        def list_applications(self, **kwargs):
            assert kwargs == {
                "user_id": "candidate-1", "limit": 2, "after": "app-1",
                "status": JobApplicationStatus.INTERVIEW,
            }
            return {"schema_version": 1, "items": [], "next_after": None}

    (status, _), body = request(
        app(Collection()), path="/v1/candidate/applications",
        query="limit=2&status=interview&after=app-1",
    )
    assert status == "200 OK"
    assert body == {"schema_version": 1, "items": [], "next_after": None}


@pytest.mark.parametrize("path", ["/v1/candidate/run-statuses", "/v1/candidate/applications"])
@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=true", "limit=2&limit=3", "owner=foreign", "after=", "limit=", "limit=2&after=a&other=b", "after=%20padded", "after=" + "a" * 201])
def test_invalid_collection_query_never_opens_storage(query, path):
    (status, _), _ = request(app(object()), path=path, query=query)
    assert status == "400 Bad Request"


@pytest.mark.parametrize("query", ["status=", "status=pending", "status=applied&status=offer"])
def test_invalid_application_status_filter_never_opens_storage(query):
    assert request(app(object()), path="/v1/candidate/applications", query=query)[0][0] == "400 Bad Request"


@pytest.mark.parametrize("query", ["phase=", "phase=pending", "phase=search&phase=failed"])
def test_invalid_run_phase_filter_never_opens_storage(query):
    assert request(app(object()), path="/v1/candidate/run-statuses", query=query)[0][0] == "400 Bad Request"


@pytest.mark.parametrize("path", ["/v1/candidate/run-statuses", "/v1/candidate/applications"])
def test_collection_auth_method_and_backend_error_boundaries(path):
    assert request(app(object()), path=path, token=None)[0][0] == "401 Unauthorized"
    assert request(app(object()), path=path, method="POST")[0][0] == "405 Method Not Allowed"
    assert request(app(object()), path=path)[1] == {"error": "internal_error"}


@pytest.mark.parametrize("cursor,second_id", [("a unicode й", "c"), ("😀" * 200, "😁")])
def test_application_http_page_uses_read_only_owner_storage_after_restart(tmp_path, cursor, second_id):
    from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
    from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
    from career_agent_ai.application.storage.sqlite_job_application_repository import SQLiteJobApplicationRepository
    from career_agent_ai.application.jobs.job_application import JobApplication
    from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus
    from urllib.parse import urlencode
    path = tmp_path / "tracker.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteJobApplicationRepository(database)
    for identifier, owner in [(cursor, "candidate-1"), ("b", "foreign"), (second_id, "candidate-1")]:
        application = JobApplication(application_id=identifier, user_id=owner,
            job_id=identifier, status=JobApplicationStatus.SAVED)
        repository.add(application.transition(JobApplicationStatus.APPLIED, note="private"))
    database.close()
    baseline = path.read_bytes()
    runtime = RuntimeCandidateApp(database_path=str(path), jobs=(),
        resolve_bearer=lambda token: "candidate-1" if token == "valid" else None)
    route = "/v1/candidate/applications"
    (status, headers), first = request(runtime, path=route, query="limit=1")
    assert status == "200 OK"
    assert ("Cache-Control", "no-store") in headers
    assert first["items"][0]["status"] == "applied"
    assert first["next_after"] == cursor
    runtime.close()
    runtime = RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: "candidate-1")
    (_, _), second = request(runtime, path=route, query=urlencode({"after": first["next_after"]}))
    assert [item["application_id"] for item in second["items"]] == [second_id]
    assert second["next_after"] is None
    assert "private" not in str(first)
    assert path.read_bytes() == baseline
    runtime.close()
    contract = candidate_approval_openapi_document()["paths"][route]["get"]
    assert contract["security"] == [{"bearerAuth": []}]
    status_parameter = next(
        parameter for parameter in contract["parameters"]
        if parameter["name"] == "status"
    )
    assert status_parameter["schema"]["enum"] == [
        "saved", "applied", "interview", "offer", "rejected", "withdrawn"
    ]
    schema = contract["responses"]["200"]["content"]["application/json"]["schema"]
    assert set(first["items"][0]) == set(schema["properties"]["items"]["items"]["required"])


@pytest.mark.parametrize("query", ["after=" + "x" * 2501, "after=" + "%F0%9F%98%80" * 201])
def test_application_query_bounds_remain_fail_closed(query):
    assert request(app(object()), path="/v1/candidate/applications", query=query)[0][0] == "400 Bad Request"


@pytest.mark.parametrize("query", ["", "application_id=", "application_id=%20padded",
    "application_id=" + "a" * 201, "application_id=x&application_id=y",
    "application_id=x&user_id=foreign", "user_id=x", "application_id",
    "application_id=%ED%A0%80", "application_id=" + "x" * 2501,
    "application_id=%", "application_id=%2", "application_id=%GG",
    "application_id=valid%20prefix%", "application%_id=a"])
@pytest.mark.parametrize("route", ["/v1/candidate/application", "/v1/candidate/application/history"])
def test_invalid_application_detail_query_precedes_storage(query, route):
    assert request(app(object()), path=route, query=query)[0][0] == "400 Bad Request"


@pytest.mark.parametrize("route", ["/v1/candidate/application", "/v1/candidate/application/history"])
def test_application_detail_auth_method_and_sanitized_backend_errors(route):
    assert request(app(object()), path=route, token=None)[0][0] == "401 Unauthorized"
    (status, headers), _ = request(app(object()), path=route, method="POST")
    assert status == "405 Method Not Allowed" and ("Allow", "GET") in headers
    class Broken:
        def get_application(self, application_id, *, user_id):
            assert (application_id, user_id) == ("a", "candidate-1")
            raise RuntimeError("private storage information")
        get_application_history = get_application
    assert request(app(Broken()), path=route, query="application_id=a")[1] == {"error": "internal_error"}


@pytest.mark.parametrize("identifier", ["😀" * 200, "\ufeff/a? +й\ufeff", "%GG%2%"])
@pytest.mark.parametrize("history", [False, True])
def test_application_http_detail_is_owner_only_read_only_after_restart(tmp_path, identifier, history):
    from urllib.parse import urlencode
    from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
    from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
    from career_agent_ai.application.storage.sqlite_job_application_repository import SQLiteJobApplicationRepository
    from career_agent_ai.application.jobs.job_application import JobApplication
    from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus
    path = tmp_path / "detail.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteJobApplicationRepository(database)
    for app_id, owner in [(identifier, "candidate-1"), ("foreign", "other")]:
        repository.add(JobApplication(application_id=app_id, user_id=owner, job_id=app_id,
            status=JobApplicationStatus.SAVED).transition(JobApplicationStatus.APPLIED, note="private"))
    database.connection.execute("UPDATE application_timeline SET metadata_json = 'private invalid JSON'")
    database.connection.execute("UPDATE job_applications SET serialization_version = 999 WHERE user_id = 'other'")
    database.connection.commit()
    database.close()
    baseline = path.read_bytes()
    route = "/v1/candidate/application" + ("/history" if history else "")
    for _ in range(2):
        runtime = RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: "candidate-1")
        (status, headers), body = request(runtime, path=route, query=urlencode({"application_id": identifier}))
        assert status == "200 OK" and ("Cache-Control", "no-store") in headers
        projection = runtime.get_application_history if history else runtime.get_application
        assert body == projection(identifier, user_id="candidate-1")
        if history:
            assert body["state_version"] == 2 and len(body["items"]) == 1
            assert body["items"][0]["from_status"] == "saved"
            assert body["items"][0]["to_status"] == "applied"
        else:
            assert body["application"]["status"] == "applied" and body["application"]["state_version"] == 2
        foreign = request(runtime, path=route, query="application_id=foreign")
        assert foreign == request(runtime, path=route, query="application_id=missing")
        assert foreign[0][0] == "404 Not Found"
        runtime.close()
    assert "private" not in str(body) and path.read_bytes() == baseline
    contract = candidate_approval_openapi_document()["paths"][route]["get"]
    assert contract["security"] == [{"bearerAuth": []}]
    schema = contract["responses"]["200"]["content"]["application/json"]["schema"]
    if history:
        assert set(body) == set(schema["required"])
        assert set(body["items"][0]) == set(schema["properties"]["items"]["items"]["required"])
    else:
        assert set(body["application"]) == set(schema["properties"]["application"]["required"])


def test_history_http_missing_storage_and_corrupt_owned_events_fail_closed(tmp_path):
    from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
    from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
    from career_agent_ai.application.storage.sqlite_job_application_repository import SQLiteJobApplicationRepository
    from career_agent_ai.application.jobs.job_application import JobApplication
    from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus
    path = tmp_path / "history.sqlite"
    route = "/v1/candidate/application/history"
    runtime = RuntimeCandidateApp(database_path=str(path), jobs=(),
        resolve_bearer=lambda token: "candidate-1" if token == "valid" else None)
    assert request(runtime, path=route, token=None, query="application_id=a")[0][0] == "401 Unauthorized"
    assert not path.exists()
    assert request(runtime, path=route, query="application_id=a")[1] == {"error": "internal_error"}
    assert not path.exists()
    runtime.close()
    database = SQLiteDatabase(str(path))
    SQLiteJobApplicationRepository(database).add(JobApplication(application_id="a",
        user_id="candidate-1", job_id="job", status=JobApplicationStatus.SAVED
    ).transition(JobApplicationStatus.APPLIED, note="private"))
    database.connection.execute("UPDATE application_timeline SET occurred_at = 'private malformed timestamp'")
    database.connection.commit()
    database.close()
    baseline = path.read_bytes()
    with RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: "candidate-1") as runtime:
        (status, headers), body = request(runtime, path=route, query="application_id=a")
        assert status == "500 Internal Server Error" and body == {"error": "internal_error"}
        assert ("Cache-Control", "no-store") in headers
    assert path.read_bytes() == baseline


@pytest.mark.parametrize("query", ["", "event_id=", "event_id=%20padded", "event_id=padded%20",
    "event_id=" + "a" * 201, "event_id=x&event_id=y", "event_id=x&user_id=other",
    "application_id=x", "event_id", "event_id=%ED%A0%80", "event_id=" + "x" * 2501,
    "event_id=%", "event_id=%2", "event_id=%GG", "event%_id=a", "event_id=a%2Fb",
    "event_id=%00", "event_id=%F0%9F%98%80"])
def test_schedule_http_invalid_query_precedes_storage(query):
    assert request(app(object()), path="/v1/candidate/schedule-event", query=query)[0][0] == "400 Bad Request"


@pytest.mark.parametrize("failure", [RuntimeError, ValueError, TypeError])
def test_schedule_http_auth_method_and_sanitized_backend_failure(failure):
    route = "/v1/candidate/schedule-event"
    assert request(app(object()), path=route, query="event_id=%", token=None)[0][0] == "401 Unauthorized"
    (status, headers), _ = request(app(object()), path=route, method="POST")
    assert status == "405 Method Not Allowed" and ("Allow", "GET") in headers
    class Broken:
        def get_schedule_event(self, event_id, *, user_id):
            assert (event_id, user_id) == ("a", "candidate-1")
            raise failure("private-storage-secret")
    (status, headers), body = request(app(Broken()), path=route, query="event_id=a")
    assert status == "500 Internal Server Error" and body == {"error": "internal_error"}
    assert ("Cache-Control", "no-store") in headers


@pytest.mark.parametrize("instant,zone,offset,local_time", [
    ("2026-10-25T00:30:00.123456+00:00", "Europe/Berlin", "+02:00", "02:30:00.123456"),
    ("2026-10-25T01:30:00.123456+00:00", "Europe/Berlin", "+01:00", "02:30:00.123456"),
    ("1900-01-01T00:30:00.123456+00:00", "Europe/Amsterdam", "+00:19:32", "00:49:32.123456"),
    ("1900-01-01T00:30:00.123456+00:00", "Africa/Monrovia", "-00:43:08", "23:46:52.123456"),
])
@pytest.mark.parametrize("with_end", [False, True])
def test_schedule_http_restart_owner_precision_and_openapi(tmp_path, instant, zone, offset, local_time, with_end):
    from datetime import datetime, timedelta
    from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
    from career_agent_ai.application.scheduling.models import ScheduleEvent, ScheduleEventType
    from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
    from career_agent_ai.application.storage.sqlite_scheduling_repository import SQLiteSchedulingRepository
    path = tmp_path / "schedule.sqlite"
    database = SQLiteDatabase(str(path))
    repository = SQLiteSchedulingRepository(database)
    start = datetime.fromisoformat(instant)
    identifier = "a" * 199 + "+"
    for event_id, owner in [(identifier, "candidate-1"), ("foreign", "other")]:
        repository.create(ScheduleEvent(event_id=event_id, candidate_id=owner, employer_name="Employer",
            event_type=ScheduleEventType.INTERVIEW, location="Karlsruhe", start_at=start,
            end_at=start + timedelta(hours=2) if with_end else None, timezone_name=zone))
    database.connection.execute("UPDATE scheduling_events SET provider_event_id=char(1)||event_id, application_id='private/bad'")
    database.connection.execute("UPDATE scheduling_events SET timezone_name='private-corrupt' WHERE candidate_id='other'")
    database.connection.commit()
    database.close()
    baseline = path.read_bytes()
    route = "/v1/candidate/schedule-event"
    for _ in range(2):
        with RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: "candidate-1") as runtime:
            (status, headers), body = request(runtime, path=route, query="event_id=" + "a" * 199 + "%2B")
            assert status == "200 OK" and ("Cache-Control", "no-store") in headers
            assert body == runtime.get_schedule_event(identifier, user_id="candidate-1")
            assert body["event"]["local_start_time"] == local_time
            assert body["event"]["utc_offset"] == offset
            assert body["event"]["start_at"] == instant
            assert (body["event"]["end_at"] is not None) == with_end
            foreign = request(runtime, path=route, query="event_id=foreign")
            assert foreign == request(runtime, path=route, query="event_id=missing")
            assert foreign[0][0] == "404 Not Found"
    assert path.read_bytes() == baseline and "private" not in str(body)
    contract = candidate_approval_openapi_document()["paths"][route]["get"]
    assert contract["security"] == [{"bearerAuth": []}]
    assert contract["parameters"][0]["name"] == "event_id"
    schema = contract["responses"]["200"]["content"]["application/json"]["schema"]
    assert set(body) == set(schema["required"])
    assert set(body["event"]) == set(schema["properties"]["event"]["required"])
    event = body["event"]
    for prefix, date_key, time_key, offset_key in [
        ("start_at", "local_date", "local_start_time", "utc_offset"),
        ("end_at", "local_end_date", "local_end_time", "end_utc_offset"),
    ]:
        if event[prefix] is None:
            assert event[offset_key] is None
            continue
        assert re.fullmatch(schema["properties"]["event"]["properties"][offset_key]["pattern"], event[offset_key])
        local = datetime.fromisoformat(event[date_key] + "T" + event[time_key] + event[offset_key])
        assert local == datetime.fromisoformat(event[prefix])


def test_schedule_http_missing_storage_and_corrupt_owned_event(tmp_path):
    from datetime import datetime, timezone
    from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
    from career_agent_ai.application.scheduling.models import ScheduleEvent, ScheduleEventType
    from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
    from career_agent_ai.application.storage.sqlite_scheduling_repository import SQLiteSchedulingRepository
    path = tmp_path / "missing.sqlite"
    route = "/v1/candidate/schedule-event"
    with RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: "candidate-1") as runtime:
        assert request(runtime, path=route, query="event_id=a")[1] == {"error": "internal_error"}
    assert not path.exists()
    database = SQLiteDatabase(str(path))
    SQLiteSchedulingRepository(database).create(ScheduleEvent(event_id="a", candidate_id="candidate-1",
        employer_name="Employer", event_type=ScheduleEventType.INTERVIEW, location="Location",
        start_at=datetime.now(timezone.utc), timezone_name="UTC"))
    database.connection.execute("UPDATE scheduling_events SET start_epoch_us=0")
    database.connection.commit()
    database.close()
    baseline = path.read_bytes()
    with RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: "candidate-1") as runtime:
        (status, headers), body = request(runtime, path=route, query="event_id=a")
        assert status == "500 Internal Server Error" and body == {"error": "internal_error"}
        assert ("Cache-Control", "no-store") in headers
    assert path.read_bytes() == baseline


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=true", "limit=1.0", "limit=-1",
    "limit=1&limit=2", "after=a&after=b", "user_id=foreign", "after=", "limit=",
    "limit=1&after=a&extra=b", "after=%20padded", "after=padded%20", "after=" + "a" * 201,
    "after=a%2Fb", "after=%ED%A0%80", "after=%", "after=%2", "after=%GG",
    "after%GG=a", "limit=%31%", "after=" + "a" * 1001])
def test_schedule_collection_invalid_query_before_storage(query):
    assert request(app(object()), path="/v1/candidate/schedule-events", query=query)[0][0] == "400 Bad Request"


@pytest.mark.parametrize("failure", [RuntimeError, ValueError, TypeError])
def test_schedule_collection_auth_method_and_backend_boundaries(failure):
    route = "/v1/candidate/schedule-events"
    assert request(app(object()), path=route, query="after=%", token=None)[0][0] == "401 Unauthorized"
    (status, headers), _ = request(app(object()), path=route, method="POST")
    assert status == "405 Method Not Allowed" and ("Allow", "GET") in headers
    class Broken:
        def list_schedule_events(self, **kwargs):
            assert kwargs == {"user_id": "candidate-1", "limit": 2, "after": "a"}
            raise failure("private-storage-secret")
    (status, headers), body = request(app(Broken()), path=route, query="limit=2&after=a")
    assert status == "500 Internal Server Error" and body == {"error": "internal_error"}
    assert ("Cache-Control", "no-store") in headers


def test_schedule_http_collection_restart_owner_pagination_and_contract(tmp_path):
    from datetime import datetime, timezone
    from urllib.parse import urlencode
    from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
    from career_agent_ai.application.scheduling.models import ScheduleEvent, ScheduleEventType
    from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
    from career_agent_ai.application.storage.sqlite_scheduling_repository import SQLiteSchedulingRepository
    path = tmp_path / "events.sqlite"
    route = "/v1/candidate/schedule-events"
    database = SQLiteDatabase(str(path))
    repository = SQLiteSchedulingRepository(database)
    cursor = "a" * 199 + "+"
    for identifier, owner in [(cursor, "candidate-1"), ("b", "other"), ("c", "candidate-1")]:
        repository.create(ScheduleEvent(event_id=identifier, candidate_id=owner, employer_name="Employer",
            event_type=ScheduleEventType.INTERVIEW, location="Karlsruhe",
            start_at=datetime(2026, 10, 25, 0, 30, 0, 123456, tzinfo=timezone.utc), timezone_name="Europe/Berlin"))
    database.connection.execute("UPDATE scheduling_events SET timezone_name='private-corrupt' WHERE candidate_id='other'")
    database.connection.execute("UPDATE scheduling_events SET provider_event_id=char(1)||event_id, application_id='private/bad'")
    database.connection.commit()
    database.close()
    baseline = path.read_bytes()
    with RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: "candidate-1") as runtime:
        (status, headers), first = request(runtime, path=route, query="limit=1")
        assert status == "200 OK" and ("Cache-Control", "no-store") in headers
        assert first["next_after"] == cursor and first["items"][0]["event_id"] == cursor
    with RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: "candidate-1") as runtime:
        (status, headers), second = request(runtime, path=route, query=urlencode({"after": first["next_after"], "limit": 100}))
        assert status == "200 OK" and ("Cache-Control", "no-store") in headers
        assert second == runtime.list_schedule_events(user_id="candidate-1", after=cursor, limit=100)
        assert [item["event_id"] for item in second["items"]] == ["c"] and second["next_after"] is None
        assert second["items"][0]["local_start_time"] == "02:30:00.123456"
        assert request(runtime, path=route, query="after=c")[1]["items"] == []
        assert len(request(runtime, path=route)[1]["items"]) == 2
    assert path.read_bytes() == baseline and "private" not in str(first) + str(second)
    operation = candidate_approval_openapi_document()["paths"][route]["get"]
    assert operation["security"] == [{"bearerAuth": []}]
    assert {parameter["name"] for parameter in operation["parameters"]} == {"limit", "after", "status"}
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert set(first) == set(schema["required"])
    assert set(first["items"][0]) == set(schema["properties"]["items"]["items"]["required"])


def test_schedule_http_collection_filters_status():
    from career_agent_ai.application.scheduling.models import ScheduleStatus
    route = "/v1/candidate/schedule-events"
    class Loop:
        def list_schedule_events(self, **kwargs):
            assert kwargs == {"user_id": "candidate-1", "limit": 2, "after": "a",
                              "status": ScheduleStatus.ACCEPTED}
            return {"schema_version": 1, "items": [], "next_after": None}
    assert request(app(Loop()), path=route, query="limit=2&after=a&status=accepted")[0][0] == "200 OK"
    for query in ("status=", "status=unknown", "status=accepted&status=proposed"):
        assert request(app(Loop()), path=route, query=query)[0][0] == "400 Bad Request"


def test_schedule_http_collection_missing_storage_and_corrupt_owned_row(tmp_path):
    from datetime import datetime, timezone
    from career_agent_ai.application.runtime.composition import RuntimeCandidateApp
    from career_agent_ai.application.scheduling.models import ScheduleEvent, ScheduleEventType
    from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
    from career_agent_ai.application.storage.sqlite_scheduling_repository import SQLiteSchedulingRepository
    path = tmp_path / "missing.sqlite"
    route = "/v1/candidate/schedule-events"
    with RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: "candidate-1") as runtime:
        assert request(runtime, path=route)[1] == {"error": "internal_error"}
    assert not path.exists()
    database = SQLiteDatabase(str(path))
    SQLiteSchedulingRepository(database).create(ScheduleEvent(event_id="a", candidate_id="candidate-1",
        employer_name="Employer", event_type=ScheduleEventType.INTERVIEW, location="Location",
        start_at=datetime.now(timezone.utc), timezone_name="UTC"))
    database.connection.execute("UPDATE scheduling_events SET start_epoch_us=0")
    database.connection.commit()
    database.close()
    baseline = path.read_bytes()
    with RuntimeCandidateApp(database_path=str(path), jobs=(), resolve_bearer=lambda _: "candidate-1") as runtime:
        (status, headers), body = request(runtime, path=route)
        assert status == "500 Internal Server Error" and body == {"error": "internal_error"}
        assert ("Cache-Control", "no-store") in headers
    assert path.read_bytes() == baseline


def raw_request(app, *, method="GET", path="/sandbox/candidate"):
    statuses = []
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": "0",
        "wsgi.input": BytesIO(),
    }
    response = b"".join(
        app(environ, lambda status, headers: statuses.append((status, headers)))
    )
    return statuses[0], response


def test_candidate_sandbox_browser_completes_prompt_bound_approval():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

    class BrowserLoop(Loop):
        def __init__(self):
            super().__init__()
            self.run_id = None

        def start(self, request, *, run_id=None):
            self.started = (request, run_id)
            self.run_id = run_id
            return SimpleNamespace(
                run_id=run_id,
                phase=SimpleNamespace(value="application_approval"),
            )

        def get_candidate_approval_prompt(self, run_id, *, user_id):
            assert (run_id, user_id) == (self.run_id, "candidate-1")
            return SimpleNamespace(
                to_dict=lambda: {
                    "schema_version": 1,
                    "run_id": run_id,
                    "state_version": 3,
                    "action_kind": "approve_application",
                    "allowed_decisions": ["approve", "decline"],
                    "action_fingerprint": "0" * 64,
                    "title": "Approve application",
                    "details": {"company": "Sandbox GmbH"},
                }
            )

        def resume_candidate_submission(self, *, user_id, submission):
            assert user_id == "candidate-1"
            self.submission = submission
            return SimpleNamespace(
                run_id=self.run_id,
                phase=SimpleNamespace(value="message_approval"),
            )

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, format, *args):
            pass

    loop = BrowserLoop()
    server = make_server("127.0.0.1", 0, app(loop), handler_class=QuietHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/sandbox/candidate")
            page.locator("#token").fill("valid")
            page.locator("#start-run-id").fill("browser-smoke")
            page.locator("#keyword").fill("platform engineer")
            page.locator("#location").fill("Berlin")
            page.locator("#profile").fill("Senior Python engineer")
            page.locator("#start").click()
            page.locator("#result").get_by_text('"phase": "application_approval"').wait_for()

            page.locator("#prompt").click()
            page.locator("#action-title").get_by_text("Approve application").wait_for()
            assert page.locator("#action-details").text_content() == (
                '{\n  "company": "Sandbox GmbH"\n}'
            )

            page.locator("#token").fill("changed")
            assert page.locator("#approve").is_disabled()
            assert page.locator("#decline").is_disabled()
            assert page.locator("#action-details").text_content() == ""
            page.locator("#token").fill("valid")
            assert page.locator("#approve").is_disabled()
            page.locator("#prompt").click()
            page.locator("#action-title").get_by_text("Approve application").wait_for()
            page.locator("#approve").click()
            page.locator("#result").get_by_text('"phase": "message_approval"').wait_for()
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert loop.submission is not None
    assert loop.submission.run_id == loop.run_id
    assert loop.submission.state_version == 3
    assert loop.submission.action_fingerprint == "0" * 64
    assert loop.submission.decision is ApprovalDecision.APPROVE


def test_candidate_sandbox_ui_is_public_static_and_hardened():
    (status, headers), body = raw_request(app(Loop()))
    html = body.decode("utf-8")

    assert status == "200 OK"
    assert ("Content-Type", "text/html; charset=utf-8") in headers
    assert ("Cache-Control", "no-store") in headers
    assert ("Referrer-Policy", "no-referrer") in headers
    assert any(name == "Content-Security-Policy" for name, _ in headers)
    assert "/v1/candidate/runs" in html
    assert "const resultRequest = ++latestResultRequest" in html
    assert "resultRequest === latestResultRequest" in html
    assert "error.resultRequest = resultRequest" in html
    assert "error.resultRequest === undefined || error.resultRequest === latestResultRequest" in html
    assert "action_fingerprint: approvalPrompt.action_fingerprint" in html
    assert "state_version: approvalPrompt.state_version" in html
    assert "if (decisionInFlight)" in html
    prompt_handler = html.index("byId('prompt').addEventListener")
    prompt_get = html.index("const loadedPrompt = await call", prompt_handler)
    assert html.index("if (decisionInFlight)", prompt_handler) < prompt_get
    assert html.index("invalidateApprovalPrompt('Loading approval prompt…')", prompt_handler) < prompt_get
    invalidation_helper = html.index("function invalidateApprovalPrompt")
    assert html.index(
        "byId('action-details').textContent = ''", invalidation_helper
    ) < html.index("function selectRun", invalidation_helper)
    assert "Approval prompt unavailable. Load it again." in html
    start_handler = html.index("byId('start').addEventListener")
    start_post = html.index("await call('/v1/candidate/runs'", start_handler)
    start_assignment = html.index("selectRun(payload.run_id)", start_post)
    assert html.index("if (startInFlight)", start_handler) < start_post
    assert html.index("startInFlight = true", start_handler) < start_post
    assert html.index("byId('start').disabled = true", start_handler) < start_post
    assert html.index("aria-busy', 'true'", start_handler) < start_post
    assert html.index("const requestNumber = ++latestStartRequest", start_handler) < start_post
    assert "requestNumber === latestStartRequest" in html[start_post:start_assignment]
    assert html.index("startInFlight = false", start_assignment) > start_assignment
    assert html.index("byId('start').disabled = false", start_assignment) > start_assignment
    assert html.index("aria-busy', 'false'", start_assignment) > start_assignment
    assert "byId('run-id').addEventListener('input'" in html
    assert "byId('token').addEventListener('input', invalidateInputContext)" in html
    for start_input in ("start-run-id", "keyword", "location", "profile"):
        assert f"'{start_input}'" in html[
            html.index("for (const id of ['start-run-id'") :
            html.index("byId('clear-session').addEventListener")
        ]
    assert "byId(id).addEventListener('input', invalidateInputContext)" in html
    assert "contextGeneration !== inputContextGeneration" in html
    assert 'id="run-overview" role="status"' in html
    assert 'id="owned-runs"' in html
    assert "byId('token').addEventListener('input', clearRunList)" in html
    assert "generation !== listGeneration" in html
    assert "button.textContent = item.run_id" in html
    assert "requestNumber !== latestResultRequest" in html
    assert "snapshot.run_id !== runId" in html
    assert "Number.isSafeInteger(snapshot.state_version)" in html
    assert "byId('run-overview').textContent = state" in html
    selection = html[html.index("function selectRun"):html.index("async function call")]
    assert "inputContextGeneration += 1" in selection
    assert "latestResultRequest += 1" in selection
    assert html.index("contextGeneration !== inputContextGeneration") < html.index("show({status: response.status, payload})")
    assert "approvalPromptGeneration += 1" in html
    assert "invalidateApprovalPrompt();" in html[start_assignment:prompt_handler]
    assert "const promptGeneration = approvalPromptGeneration" in html[prompt_handler:prompt_get]
    assert "const promptRunId = byId('run-id').value" in html[prompt_handler:prompt_get]
    assert "promptGeneration !== approvalPromptGeneration" in html[prompt_get:]
    assert "promptRunId !== byId('run-id').value" in html[prompt_get:]
    assert "activePromptLoadGeneration = null" in html[invalidation_helper:prompt_handler]
    assert "activePromptLoadGeneration = promptGeneration" in html[prompt_handler:prompt_get]
    assert "activePromptLoadGeneration === promptGeneration" in html[prompt_get:]
    recovery_handler = html.index("byId('continue').addEventListener")
    recovery_post = html.index("await call(runPath('/continue')", recovery_handler)
    assert html.index("if (decisionInFlight)", recovery_handler) < recovery_post
    assert html.index("recoveryInFlight = true", recovery_handler) < recovery_post
    assert html.index("byId('approve').disabled = true", recovery_handler) < recovery_post
    assert "Recovery finished. Load the current approval prompt." in html[recovery_post:prompt_handler]
    assert "error.code = payload.error" in html
    assert "error.code === 'recovery_unavailable'" in html[recovery_post:prompt_handler]
    assert "byId('approve').disabled = !approvalPrompt" in html[recovery_post:prompt_handler]
    assert "Recovery result is uncertain. Load the current approval prompt." in html[recovery_post:prompt_handler]
    assert html.index("byId('approve').disabled = true", prompt_handler) < prompt_get
    assert html.index("byId('decline').disabled = true", prompt_handler) < prompt_get
    decision_start = html.index("async function decide")
    decision_post = html.index("await call('/v1/candidate/runs/'", decision_start)
    assert html.index("if (activePromptLoadGeneration !== null)", decision_start) < decision_post
    assert html.index("if (recoveryInFlight)", decision_start) < decision_post
    assert "encodeURIComponent(payload.run_id) + '/approval'" in html[decision_start:]
    assert "runPath(" not in html[decision_start:]
    assert html.index("decisionInFlight = true", decision_start) < decision_post
    assert html.index(
        "byId('approve').disabled = true", decision_start
    ) < decision_post
    assert html.index("byId('prompt').disabled = true", decision_start) < decision_post
    assert html.index("byId('continue').disabled = true", decision_start) < decision_post
    assert html.index("byId('prompt').disabled = false", decision_post) > decision_post
    assert "localStorage" not in html
    assert "sessionStorage" not in html
    assert ".innerHTML" not in html


def test_candidate_sandbox_ui_rejects_non_get_methods_without_authentication():
    (status, headers), body = raw_request(app(Loop()), method="POST")

    assert status == "405 Method Not Allowed"
    assert ("Allow", "GET") in headers
    assert json.loads(body) == {"error": "method_not_allowed"}


def test_openapi_publishes_public_candidate_sandbox_route():
    operation = candidate_approval_openapi_document()["paths"]["/sandbox/candidate"]["get"]

    assert operation["security"] == []
    assert operation["responses"]["200"]["content"] == {
        "text/html": {"schema": {"type": "string"}}
    }
    assert "405" in operation["responses"]


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
    scoped_run_id = CandidateApprovalWSGIApp._owner_scoped_run_id(
        "candidate-1", "run-new"
    )
    assert payload == {"phase": "application_approval", "run_id": scoped_run_id}
    started, run_id = loop.started
    assert run_id == scoped_run_id
    assert started.user_id == "candidate-1"
    assert started.keyword == "logistics"
    assert started.location == "Karlsruhe"
    assert started.sender == ""
    assert started.schedule_event_id is None


def test_post_runs_replays_owned_legacy_identifier_without_creating_scoped_duplicate():
    class LegacyLoop(Loop):
        def replay_existing_start(self, request, *, run_id, user_id):
            assert run_id == "run-legacy"
            assert user_id == request.user_id == "candidate-1"
            return SimpleNamespace(
                run_id=run_id, phase=SimpleNamespace(value="application_approval")
            )

        def start(self, request, *, run_id=None):
            raise AssertionError("legacy replay must not create a scoped duplicate")

    (status, _), payload = request(
        app(LegacyLoop()),
        method="POST",
        body={
            "schema_version": 1,
            "run_id": "run-legacy",
            "keyword": "logistics",
            "candidate_profile": "Warehouse coordinator",
        },
        path="/v1/candidate/runs",
    )

    assert status == "201 Created"
    assert payload == {"phase": "application_approval", "run_id": "run-legacy"}


def test_post_runs_replays_transitional_scoped_identifier_before_v2_create():
    transitional_id = CandidateApprovalWSGIApp._legacy_owner_scoped_run_id(
        "candidate-1", "run-transition"
    )

    class TransitionalLoop(Loop):
        def replay_existing_start(self, request, *, run_id, user_id):
            if run_id == "run-transition":
                raise KeyError(run_id)
            assert run_id == transitional_id
            assert user_id == request.user_id == "candidate-1"
            return SimpleNamespace(
                run_id=run_id, phase=SimpleNamespace(value="application_approval")
            )

        def start(self, request, *, run_id=None):
            raise AssertionError("transitional replay must not create a v2 duplicate")

    (status, _), payload = request(
        app(TransitionalLoop()),
        method="POST",
        body={
            "schema_version": 1,
            "run_id": "run-transition",
            "keyword": "logistics",
            "candidate_profile": "Warehouse coordinator",
        },
        path="/v1/candidate/runs",
    )

    assert status == "201 Created"
    assert payload == {"phase": "application_approval", "run_id": transitional_id}


def test_post_runs_maps_unsupported_decimal_exponent_to_invalid_request():
    encoded = (
        b'{"schema_version":1e999999999999999999999999999999999999999999,'
        b'"run_id":"run-exponent","keyword":"logistics",'
        b'"candidate_profile":"Warehouse coordinator"}'
    )
    statuses = []

    response = app(Loop())(
        {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/v1/candidate/runs",
            "HTTP_AUTHORIZATION": "Bearer valid",
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": str(len(encoded)),
            "wsgi.input": BytesIO(encoded),
        },
        lambda status, headers: statuses.append(status),
    )

    assert statuses == ["400 Bad Request"]
    assert json.loads(b"".join(response))["error"] == "invalid_request"


def test_post_approval_maps_unsupported_decimal_exponent_to_invalid_request():
    encoded = (
        b'{"schema_version":1e999999999999999999999999999999999999999999,'
        b'"run_id":"run-1","state_version":3,"action_fingerprint":"'
        + (b"0" * 64)
        + b'","decision":"approve"}'
    )
    statuses = []

    response = app(Loop())(
        {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/v1/candidate/runs/run-1/approval",
            "HTTP_AUTHORIZATION": "Bearer valid",
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": str(len(encoded)),
            "wsgi.input": BytesIO(encoded),
        },
        lambda status, headers: statuses.append(status),
    )

    assert statuses == ["400 Bad Request"]
    assert json.loads(b"".join(response))["error"] == "invalid_request"


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
    assert payload == {
        "phase": "application_approval",
        "run_id": CandidateApprovalWSGIApp._owner_scoped_run_id(
            "candidate-1", "run-unicode"
        ),
    }
    assert loop.started[0].candidate_profile == "😀" * 12_000


def test_post_runs_scopes_same_client_run_id_to_authenticated_owner():
    payload = {
        "schema_version": 1.0,
        "run_id": "run-shared",
        "keyword": "logistics",
        "candidate_profile": "Warehouse coordinator",
    }

    first_request, first_id = CandidateApprovalWSGIApp._start_request(
        payload, user_id="candidate-1"
    )
    second_request, second_id = CandidateApprovalWSGIApp._start_request(
        payload, user_id="candidate-2"
    )

    assert first_request.user_id == "candidate-1"
    assert second_request.user_id == "candidate-2"
    assert first_id != second_id
    assert len(first_id) == len(second_id) == 138
    assert first_id.startswith("scoped-v2:")
    assert first_id == CandidateApprovalWSGIApp._owner_scoped_run_id(
        "candidate-1", "run-shared"
    )


def test_scoped_v2_identifier_cannot_be_preseeded_through_legacy_start_input():
    derived_id = CandidateApprovalWSGIApp._owner_scoped_run_id(
        "candidate-2", "anticipated"
    )

    assert len(derived_id) > 120
    with pytest.raises(ValueError, match="120 characters"):
        CandidateApprovalWSGIApp._start_request(
            {
                "schema_version": 1,
                "run_id": derived_id,
                "keyword": "logistics",
                "candidate_profile": "Warehouse coordinator",
            },
            user_id="attacker",
        )


def test_post_runs_hashes_canonical_owner_and_parses_version_exactly():
    canonical_request, canonical_id = CandidateApprovalWSGIApp._start_request(
        {
            "schema_version": 1,
            "run_id": "run-shared",
            "keyword": "logistics",
            "candidate_profile": "Warehouse coordinator",
        },
        user_id=" candidate-1 ",
    )
    assert canonical_request.user_id == "candidate-1"
    assert canonical_id == CandidateApprovalWSGIApp._owner_scoped_run_id(
        "candidate-1", "run-shared"
    )

    for literal in ("1.0", "1e0"):
        encoded = (
            '{"schema_version":' + literal + ',"run_id":"run-exact",'
            '"keyword":"logistics","candidate_profile":"Warehouse coordinator"}'
        ).encode()
        statuses = []
        response = app(Loop())(
            {
                "REQUEST_METHOD": "POST",
                "PATH_INFO": "/v1/candidate/runs",
                "HTTP_AUTHORIZATION": "Bearer valid",
                "CONTENT_TYPE": "application/json",
                "CONTENT_LENGTH": str(len(encoded)),
                "wsgi.input": BytesIO(encoded),
            },
            lambda status, headers: statuses.append(status),
        )
        assert statuses == ["201 Created"]
        assert json.loads(b"".join(response))["phase"] == "application_approval"

    for literal in ("1.0000000000000001", "0.99999999999999999"):
        encoded = (
            '{"schema_version":' + literal + ',"run_id":"run-inexact",'
            '"keyword":"logistics","candidate_profile":"Warehouse coordinator"}'
        ).encode()
        statuses = []
        response = app(Loop())(
            {
                "REQUEST_METHOD": "POST",
                "PATH_INFO": "/v1/candidate/runs",
                "HTTP_AUTHORIZATION": "Bearer valid",
                "CONTENT_TYPE": "application/json",
                "CONTENT_LENGTH": str(len(encoded)),
                "wsgi.input": BytesIO(encoded),
            },
            lambda status, headers: statuses.append(status),
        )
        assert statuses == ["400 Bad Request"]
        assert json.loads(b"".join(response))["error"] == "invalid_request"


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
    from career_agent_ai.application.career.autonomous_loop_models import CareerLoopPhase

    document = candidate_approval_openapi_document()
    route = document["paths"]["/v1/candidate/runs/{run_id}/approval"]

    assert document["openapi"] == "3.1.0"
    assert document["components"]["securitySchemes"]["bearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
    }
    health = document["paths"]["/healthz"]["get"]
    readiness = document["paths"]["/readyz"]["get"]
    assert health["security"] == []
    assert set(health["responses"]) == {"200", "405"}
    assert readiness["security"] == []
    assert set(readiness["responses"]) == {"200", "405", "503"}
    assert readiness["responses"]["503"]["content"]["application/json"][
        "schema"
    ]["properties"]["status"]["const"] == "unavailable"
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
    run_collection = document["paths"]["/v1/candidate/run-statuses"]["get"]
    phase_parameter = next(
        parameter for parameter in run_collection["parameters"]
        if parameter["name"] == "phase"
    )
    assert phase_parameter["schema"]["enum"] == [
        phase.value for phase in CareerLoopPhase
    ]
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


@pytest.mark.parametrize('method,token,expected', [
    ('GET', '', '401 Unauthorized'),
    ('GET', 'invalid', '401 Unauthorized'),
    ('POST', 'valid', '405 Method Not Allowed'),
    ('DELETE', 'valid', '405 Method Not Allowed'),
])
def test_status_auth_and_method_reject_before_storage(method, token, expected):
    class ForbiddenLoop:
        def get_candidate_run_status(self, *args, **kwargs):
            pytest.fail('rejected request must not access storage')

    (status, headers), _ = request(
        app(ForbiddenLoop()), path='/v1/candidate/runs/run-1/status',
        method=method, token=token,
    )
    assert status == expected
    assert ('Cache-Control', 'no-store') in headers
    if expected.startswith('405'):
        assert ('Allow', 'GET') in headers


def test_status_storage_failure_is_sanitized():
    class BrokenLoop:
        def get_candidate_run_status(self, *args, **kwargs):
            raise RuntimeError('private provider and profile details')

    (status, _), payload = request(app(BrokenLoop()), path='/v1/candidate/runs/run-1/status')
    assert status == '500 Internal Server Error'
    assert payload == {'error': 'internal_error'}
