from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from career_agent_ai.application.api.operational_api import (
    OperationalApiService,
    OperationalWSGIApp,
)
from career_agent_ai.application.observability.models import (
    OperationalIssue,
    OperationalSeverity,
)


NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
TOKEN = "operator-test-token-" + "x" * 32


class StaticProbe:
    """Return one synthetic issue containing untrusted diagnostic fields."""

    def __init__(self, issue: OperationalIssue) -> None:
        self._issue = issue

    def inspect(self, *, now: datetime, stale_after_seconds: int) -> tuple[OperationalIssue, ...]:
        assert now == NOW
        assert stale_after_seconds == 60
        return (self._issue,)


def _issue() -> OperationalIssue:
    return OperationalIssue(
        issue_type="external_action_failed token=issue-type-secret",
        entity_id="operation:Bearer operation-id-secret",
        severity=OperationalSeverity.ERROR,
        updated_at=NOW,
        details={
            "action_type": 'calendar.accept api_key="action-key-secret"',
            "authorization": "Basic authorization-secret",
            "nested": {
                "client_secret": "nested-client-secret",
                "diagnostic": 'provider failed Bearer provider-token-secret password="password-secret"',
                "safe": "visible diagnostic",
            },
            "numbers": [1, float("nan")],
        },
    )


def _call(app: Any, *, token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/v1/operational/issues",
        "QUERY_STRING": "stale_after_seconds=60",
        "HTTP_AUTHORIZATION": f"Bearer {token}",
    }
    body = b"".join(app(environ, start_response))
    return captured, json.loads(body.decode("utf-8"))


def test_operational_issue_projection_redacts_identifiers_and_nested_details() -> None:
    service = OperationalApiService(StaticProbe(_issue()), clock=lambda: NOW)

    payload = service.issues(stale_after_seconds=60)
    rendered = json.dumps(payload, ensure_ascii=False)

    for secret in (
        "issue-type-secret",
        "operation-id-secret",
        "action-key-secret",
        "authorization-secret",
        "nested-client-secret",
        "provider-token-secret",
        "password-secret",
    ):
        assert secret not in rendered

    issue = payload["issues"][0]
    assert issue["issue_type"] == "external_action_failed token=[REDACTED]"
    assert issue["entity_id"] == "operation:Bearer [REDACTED]"
    assert issue["details"]["action_type"] == "calendar.accept api_key=[REDACTED]"
    assert issue["details"]["authorization"] == "[REDACTED]"
    assert issue["details"]["nested"] == {
        "client_secret": "[REDACTED]",
        "diagnostic": 'provider failed Bearer [REDACTED] password="[REDACTED]"',
        "safe": "visible diagnostic",
    }
    assert issue["details"]["numbers"] == [1, "[NON_FINITE]"]


def test_operational_http_response_never_serializes_unredacted_probe_data() -> None:
    service = OperationalApiService(StaticProbe(_issue()), clock=lambda: NOW)
    app = OperationalWSGIApp(service, bearer_token=TOKEN)

    metadata, payload = _call(app, token=TOKEN)
    rendered = json.dumps(payload, ensure_ascii=False)

    assert metadata["status"] == "200 OK"
    assert metadata["headers"]["Cache-Control"] == "no-store"
    assert metadata["headers"]["X-Content-Type-Options"] == "nosniff"
    assert "provider-token-secret" not in rendered
    assert "operation-id-secret" not in rendered
    assert payload["issues"][0]["details"]["nested"]["safe"] == "visible diagnostic"


def test_operational_http_authentication_still_precedes_probe_access() -> None:
    class ForbiddenProbe:
        def inspect(self, *, now: datetime, stale_after_seconds: int) -> tuple[OperationalIssue, ...]:
            raise AssertionError("unauthenticated requests must not inspect durable state")

    app = OperationalWSGIApp(
        OperationalApiService(ForbiddenProbe(), clock=lambda: NOW),
        bearer_token=TOKEN,
    )

    metadata, payload = _call(app, token="wrong-token")

    assert metadata["status"] == "401 Unauthorized"
    assert payload == {"error": "unauthorized"}
