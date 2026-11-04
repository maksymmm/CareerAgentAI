"""Candidate probes reject missing or malformed WSGI request methods."""

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


@pytest.mark.parametrize("path", ["/healthz", "/readyz"])
@pytest.mark.parametrize(
    "method",
    [None, "", "POST", "HEAD", "get", b"GET", True],
)
def test_probe_rejects_missing_or_non_get_method_without_storage_access(
    path: str,
    method: object,
) -> None:
    app = CandidateServiceWSGIApp(
        _DelegatedApp(),
        database_path="/nonexistent/db.sqlite",
    )
    statuses: list[str] = []
    headers: list[tuple[str, str]] = []
    environ: dict[str, object] = {"PATH_INFO": path}
    if method is not None:
        environ["REQUEST_METHOD"] = method

    body = list(
        app(
            environ,
            lambda status, fields: (
                statuses.append(status),
                headers.extend(fields),
            ),
        )
    )

    assert statuses == ["405 Method Not Allowed"]
    assert body == [b""]
    assert ("Allow", "GET") in headers
    assert ("Cache-Control", "no-store") in headers
