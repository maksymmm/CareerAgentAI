"""Deployable, sandbox-safe HTTP startup for the candidate approval service."""

from __future__ import annotations

import hmac
import ipaddress
import os
import signal
import socket
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping
from wsgiref.simple_server import WSGIServer, make_server

from career_agent_ai.application.api import OperationalWSGIApp
from career_agent_ai.application.career.autonomous_loop_models import (
    validate_loop_identifier,
)
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase

from .composition import RuntimeCandidateApp, build_candidate_sandbox_app_from_env
from .config import RuntimeConfig


class _IPv6WSGIServer(WSGIServer):
    """Bind the standard-library WSGI server to an explicit IPv6 listener."""

    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        """Make IPv6-only exposure deterministic before binding the socket."""
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()


@dataclass(frozen=True)
class CandidateServiceConfig:
    """Validated listener and single-candidate authentication configuration."""

    host: str
    port: int
    bearer_token: str
    user_id: str

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "CandidateServiceConfig":
        """Load startup-only settings without opening storage or logging secrets."""
        source = os.environ if environ is None else environ
        host = source.get("CAREER_AGENT_CANDIDATE_HOST", "127.0.0.1").strip()
        try:
            ipaddress.ip_address(host)
        except ValueError as exc:
            raise ValueError(
                "CAREER_AGENT_CANDIDATE_HOST must be an IPv4 or IPv6 address."
            ) from exc

        raw_port = source.get("CAREER_AGENT_CANDIDATE_PORT", "8080").strip()
        if not raw_port.isascii() or not raw_port.isdecimal():
            raise ValueError("CAREER_AGENT_CANDIDATE_PORT must be an integer.")
        port = int(raw_port)
        if not 0 <= port <= 65535:
            raise ValueError("CAREER_AGENT_CANDIDATE_PORT must be between 0 and 65535.")

        bearer_token = source.get("CAREER_AGENT_CANDIDATE_BEARER_TOKEN", "")
        OperationalWSGIApp.validate_bearer_token(bearer_token)
        if bearer_token != bearer_token.strip():
            raise ValueError(
                "CAREER_AGENT_CANDIDATE_BEARER_TOKEN must not start or end with whitespace."
            )
        user_id = validate_loop_identifier(
            source.get("CAREER_AGENT_CANDIDATE_USER_ID", ""),
            "CAREER_AGENT_CANDIDATE_USER_ID",
        )
        return cls(host=host, port=port, bearer_token=bearer_token, user_id=user_id)

    def resolve_bearer(self, presented: str) -> str | None:
        """Resolve the configured candidate with constant-time token comparison."""
        if not isinstance(presented, str):
            return None
        try:
            presented_bytes = presented.encode("ascii")
        except UnicodeEncodeError:
            return None
        expected = self.bearer_token.encode("ascii")
        return self.user_id if hmac.compare_digest(presented_bytes, expected) else None


class CandidateServiceWSGIApp:
    """Expose unauthenticated probe routes around the authenticated candidate API."""

    _HEALTH_BODY = b'{"status":"ok"}'
    _UNAVAILABLE_BODY = b'{"status":"unavailable"}'

    def __init__(self, app: RuntimeCandidateApp, *, database_path: str) -> None:
        self._app = app
        self._database_path = database_path

    @staticmethod
    def _respond(
        start_response: Callable[..., Any], status: str, body: bytes
    ) -> list[bytes]:
        start_response(
            status,
            [
                ("Content-Type", "application/json; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
                ("X-Content-Type-Options", "nosniff"),
            ],
        )
        return [body]

    def __call__(
        self, environ: Mapping[str, Any], start_response: Callable[..., Any]
    ) -> Iterable[bytes]:
        path = environ.get("PATH_INFO", "")
        if path not in {"/healthz", "/readyz"}:
            return self._app(environ, start_response)
        if environ.get("REQUEST_METHOD", "GET") != "GET":
            start_response(
                "405 Method Not Allowed",
                [
                    ("Allow", "GET"),
                    ("Content-Length", "0"),
                    ("Cache-Control", "no-store"),
                    ("X-Content-Type-Options", "nosniff"),
                ],
            )
            return [b""]
        if path == "/healthz":
            return self._respond(start_response, "200 OK", self._HEALTH_BODY)

        database = None
        try:
            database = SQLiteDatabase.open_read_only(self._database_path)
            database.connection.execute("SELECT 1").fetchone()
        except Exception:
            return self._respond(
                start_response, "503 Service Unavailable", self._UNAVAILABLE_BODY
            )
        finally:
            if database is not None:
                database.close()
        return self._respond(start_response, "200 OK", self._HEALTH_BODY)

    def close(self) -> None:
        """Close the delegated candidate runtime."""
        self._app.close()


class CandidateServiceRuntime:
    """Own the HTTP listener and close it together with the candidate runtime."""

    def __init__(self, app: Any, server: WSGIServer) -> None:
        self._app = app
        self._server = server
        self._closed = False

    @property
    def server_port(self) -> int:
        """Return the effective listener port, including an OS-assigned port."""
        return int(self._server.server_port)

    def serve_forever(self) -> None:
        """Serve until interrupted and always release listener and runtime state."""
        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self.close()

    def close(self) -> None:
        """Idempotently stop accepting connections and close the composed runtime."""
        if self._closed:
            return
        self._closed = True
        self._server.server_close()
        self._app.close()

    def __enter__(self) -> "CandidateServiceRuntime":
        """Return an open runtime for deployment smoke tests."""
        if self._closed:
            raise RuntimeError("Candidate service runtime is closed.")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def build_candidate_service_from_env(
    *,
    jobs: Iterable[Job] = (),
    environ: Mapping[str, str] | None = None,
) -> CandidateServiceRuntime:
    """Validate auth first, compose the safe app, and bind its HTTP listener."""
    config = CandidateServiceConfig.from_env(environ)
    runtime_config = RuntimeConfig.from_env(environ)
    candidate_app = build_candidate_sandbox_app_from_env(
        resolve_bearer=config.resolve_bearer,
        jobs=jobs,
        environ=environ,
    )
    app = CandidateServiceWSGIApp(
        candidate_app, database_path=runtime_config.database_path
    )
    try:
        server_class = (
            _IPv6WSGIServer
            if ipaddress.ip_address(config.host).version == 6
            else WSGIServer
        )
        server = make_server(
            config.host,
            config.port,
            app,
            server_class=server_class,
        )
    except BaseException:
        app.close()
        raise
    return CandidateServiceRuntime(app, server)


def main() -> None:
    """Run the candidate service and translate SIGTERM into graceful shutdown."""
    runtime = build_candidate_service_from_env()
    previous_handler: Any = signal.getsignal(signal.SIGTERM)

    def stop_on_sigterm(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_on_sigterm)
    try:
        runtime.serve_forever()
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


if __name__ == "__main__":
    main()
