"""Cross-runtime idempotency regression for candidate interview rescheduling."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from threading import Event, Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from wsgiref.simple_server import WSGIRequestHandler, make_server

from career_agent_ai.application.api import CandidateScheduleActionWSGIApp
from career_agent_ai.application.storage.sqlite_sandbox_calendar_provider import (
    SandboxCalendarOutcome,
)
from career_agent_ai.tests.test_candidate_schedule_actions import (
    Candidate,
    END,
    EVENT_ID,
    OWNER,
    START,
    _body,
    _event,
    _seed,
)


class QuietHandler(WSGIRequestHandler):
    """Suppress request logs from the loopback-only integration servers."""

    def log_message(self, format, *args):
        pass


def _serve(candidate):
    """Start one independent candidate runtime behind a loopback WSGI listener."""
    server = make_server(
        "127.0.0.1", 0, candidate.app, handler_class=QuietHandler
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop(server, thread):
    """Stop one test listener and wait for its serving thread to exit."""
    server.shutdown()
    thread.join(timeout=10)
    assert not thread.is_alive()
    server.server_close()


def _post_reschedule(url):
    """Submit one explicit synthetic candidate approval over authenticated HTTP."""
    payload = json.dumps(_body(), separators=(",", ":")).encode("utf-8")
    request = Request(
        url,
        data=payload,
        headers={
            "Authorization": "Bearer owner-token",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def _operation_snapshot(path, operation_id):
    """Read the event, operation, provider attempt, and effect from durable SQLite."""
    connection = sqlite3.connect(path)
    try:
        event = connection.execute(
            "SELECT status, version, active_action_operation_id "
            "FROM scheduling_events WHERE event_id = ?",
            (EVENT_ID,),
        ).fetchone()
        operation = connection.execute(
            "SELECT status FROM external_action_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        attempts = connection.execute(
            "SELECT attempt_no, outcome FROM sandbox_calendar_attempts "
            "WHERE operation_id = ? ORDER BY attempt_no",
            (operation_id,),
        ).fetchall()
        effect = connection.execute(
            "SELECT action, replays FROM sandbox_calendar_effects "
            "WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        return {
            "event": event,
            "operation": operation,
            "attempts": attempts,
            "effect": effect,
        }
    finally:
        connection.close()


def test_duplicate_candidate_reschedule_across_runtimes_has_one_provider_effect(
    tmp_path,
):
    """A live duplicate returns running and a terminal replay never calls the provider."""
    path = tmp_path / "candidate-reschedule-cross-runtime.sqlite"
    _seed(path, _event())

    provider_entered = Event()
    release_provider = Event()
    first_provider_calls = []
    second_provider_calls = []

    def first_plan(operation_id, attempt_no):
        first_provider_calls.append((operation_id, attempt_no))
        provider_entered.set()
        if not release_provider.wait(timeout=10):
            raise TimeoutError("Test did not release the sandbox calendar provider.")
        return SandboxCalendarOutcome.SUCCESS

    def second_plan(operation_id, attempt_no):
        second_provider_calls.append((operation_id, attempt_no))
        return SandboxCalendarOutcome.SUCCESS

    first = Candidate(path, first_plan)
    second = Candidate(path, second_plan)
    first_server = first_thread = second_server = second_thread = None
    request_thread = None
    first_responses = []

    target_start = datetime.fromisoformat(START).astimezone(timezone.utc)
    target_end = datetime.fromisoformat(END).astimezone(timezone.utc)
    operation_id = CandidateScheduleActionWSGIApp._operation_id(
        OWNER, EVENT_ID, 1, target_start, target_end, "Europe/Berlin"
    )

    try:
        first_server, first_thread = _serve(first)
        second_server, second_thread = _serve(second)
        first_url = (
            f"http://127.0.0.1:{first_server.server_port}"
            f"{CandidateScheduleActionWSGIApp.RESCHEDULE_PATH}"
        )
        second_url = (
            f"http://127.0.0.1:{second_server.server_port}"
            f"{CandidateScheduleActionWSGIApp.RESCHEDULE_PATH}"
        )

        request_thread = Thread(
            target=lambda: first_responses.append(_post_reschedule(first_url)),
            daemon=True,
        )
        request_thread.start()
        assert provider_entered.wait(timeout=5)

        # A second runtime sees the same live event-scoped lease. It must not
        # wait for the provider transaction or invoke its own outcome plan.
        duplicate_status, duplicate = _post_reschedule(second_url)
        assert duplicate_status == 202
        assert duplicate == {
            "schema_version": 1,
            "outcome": "running",
            "event_id": EVENT_ID,
        }
        assert first_provider_calls == [(operation_id, 1)]
        assert second_provider_calls == []

        connection = sqlite3.connect(path)
        try:
            assert connection.execute(
                "SELECT COUNT(*) FROM sandbox_calendar_attempts "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT COUNT(*) FROM sandbox_calendar_effects "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone() == (0,)
        finally:
            connection.close()

        release_provider.set()
        request_thread.join(timeout=10)
        assert not request_thread.is_alive()
        assert len(first_responses) == 1
        first_status, first_result = first_responses[0]
        assert first_status == 200
        assert first_result["outcome"] == "completed"
        assert first_result["event"]["event_id"] == EVENT_ID
        assert first_result["event"]["status"] == "reschedule_requested"
        assert first_result["event"]["state_version"] == 2

        expected = {
            "event": ("reschedule_requested", 2, None),
            "operation": ("succeeded",),
            "attempts": [(1, "success")],
            "effect": ("reschedule", 0),
        }
        assert _operation_snapshot(path, operation_id) == expected

        # A replay after the first runtime has committed returns the durable
        # winner from a different runtime without invoking either provider plan.
        replay_status, replay = _post_reschedule(second_url)
        assert replay_status == 200
        assert replay["outcome"] == "completed"
        assert replay["event"] == first_result["event"]
        assert first_provider_calls == [(operation_id, 1)]
        assert second_provider_calls == []
        assert _operation_snapshot(path, operation_id) == expected
    finally:
        release_provider.set()
        if request_thread is not None:
            request_thread.join(timeout=10)
        if first_server is not None:
            _stop(first_server, first_thread)
        if second_server is not None:
            _stop(second_server, second_thread)
        first.close()
        second.close()
