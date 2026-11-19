"""Regression checks for candidate service probe cache isolation."""

from typing import Any, Callable, Iterable, Mapping

import pytest

from career_agent_ai.application.runtime.candidate_service import (
    CandidateServiceWSGIApp,
)


class _DelegatedApp:
    def __call__(
        self,
        environ: Mapping[str, Any],
        start_response: Callable[..., Any],
    ) -> Iterable[bytes]:
        pytest.fail("probe must not delegate to authenticated candidate API")

    def close(self) -> None:
        pass


@pytest.mark.parametrize(
    ("path", "method", "expected_status", "expected_body"),
    [
        ("/healthz", "GET", "200 OK", b'{"status":"ok"}'),
        ("/readyz", "GET", "503 Service Unavailable", b'{"status":"unavailable"}'),
        ("/healthz", "POST", "405 Method Not Allowed", b""),
        ("/readyz", "POST", "405 Method Not Allowed", b""),
    ],
)
def test_probe_responses_disable_legacy_and_modern_caching(
    path: str,
    method: str,
    expected_status: str,
    expected_body: bytes,
) -> None:
    app = CandidateServiceWSGIApp(
        _DelegatedApp(), database_path="/nonexistent/candidate.db"
    )
    responses: list[tuple[str, list[tuple[str, str]]]] = []

    body = b"".join(
        app(
            {"PATH_INFO": path, "REQUEST_METHOD": method},
            lambda status, headers: responses.append((status, headers)),
        )
    )

    assert len(responses) == 1
    status, headers = responses[0]
    assert status == expected_status
    assert body == expected_body
    assert headers.count(("Cache-Control", "no-store")) == 1
    assert headers.count(("Pragma", "no-cache")) == 1
    assert headers.count(("X-Content-Type-Options", "nosniff")) == 1
