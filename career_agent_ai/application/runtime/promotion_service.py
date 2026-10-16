"""Loopback-only HTTP entrypoint for explicitly enabled promotion dry runs."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hmac
import ipaddress
import os
import signal
from typing import Any, Callable, Iterable, Iterator, Mapping
from wsgiref.simple_server import WSGIServer, make_server

from career_agent_ai.application.api import OperationalWSGIApp, ReleaseOperator, promotion_approval_openapi_document
from career_agent_ai.application.career.autonomous_loop_models import validate_loop_identifier
from .promotion_sandbox import build_promotion_sandbox_app_from_env
from .config import RuntimeConfig
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import SQLiteExternalActionOperationRepository


@dataclass(frozen=True)
class PromotionServiceConfig:
    """Validated local listener and server-owned single-operator identity."""

    host: str
    port: int
    operator_id: str
    bearer_token: str = field(repr=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> PromotionServiceConfig:
        """Validate all listener/auth settings before storage is opened."""
        source = os.environ if environ is None else environ
        host = source.get("CAREER_AGENT_PROMOTION_HOST", "127.0.0.1")
        address = ipaddress.ip_address(host)
        if address.version != 4 or not address.is_loopback:
            raise ValueError("Promotion service requires an IPv4 loopback listener")
        raw_port = source.get("CAREER_AGENT_PROMOTION_PORT", "8081")
        if not raw_port.isascii() or not raw_port.isdecimal() or len(raw_port) > 5:
            raise ValueError("Promotion port must be an integer between 0 and 65535")
        port = int(raw_port)
        if port > 65535:
            raise ValueError("Promotion port must be between 0 and 65535")
        token = source.get("CAREER_AGENT_PROMOTION_BEARER_TOKEN", "")
        OperationalWSGIApp.validate_bearer_token(token)
        if token != token.strip():
            raise ValueError("Promotion bearer token must not have surrounding whitespace")
        operator = validate_loop_identifier(source.get("CAREER_AGENT_PROMOTION_OPERATOR_ID", ""), "operator_id")
        return cls(host=host, port=port, operator_id=operator, bearer_token=token)

    def resolve_bearer(self, presented: str) -> ReleaseOperator | None:
        """Compare ASCII tokens in constant time and assign only dry-run scope."""
        if not isinstance(presented, str) or not presented.isascii():
            return None
        if not hmac.compare_digest(presented.encode("ascii"), self.bearer_token.encode("ascii")):
            return None
        return ReleaseOperator(self.operator_id, frozenset({"release:promote:dry-run"}))


class PromotionServiceWSGIApp:
    """Add storage-independent liveness and non-mutating readiness probes."""

    def __init__(self, app: Callable, *, database_path: str) -> None:
        self._app = app
        self._database_path = database_path

    def __call__(self, environ: Mapping[str, Any], start_response: Callable) -> Iterable[bytes]:
        """Serve GET-only probes without exposing storage or operation details."""
        path = environ.get("PATH_INFO", "")
        if path not in {"/healthz", "/readyz"}:
            return self._app(environ, start_response)
        if environ.get("REQUEST_METHOD", "GET") != "GET":
            return self._respond(start_response, "405 Method Not Allowed", b'{"error":"method_not_allowed"}', [("Allow", "GET")])
        if path == "/readyz":
            database = None
            try:
                database = SQLiteDatabase.open_read_only(self._database_path)
                database.connection.execute("SELECT operation_id, action_type, payload_json, status, result_json, error, created_at, updated_at FROM external_action_operations LIMIT 0")
            except Exception:
                return self._respond(start_response, "503 Service Unavailable", b'{"status":"unavailable"}')
            finally:
                if database is not None:
                    database.close()
        return self._respond(start_response, "200 OK", b'{"status":"ok"}')

    @staticmethod
    def _respond(start_response: Callable, status: str, body: bytes, extra: list | None = None) -> list[bytes]:
        start_response(status, [("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))), ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"), *(extra or [])])
        return [body]


def promotion_service_openapi_document() -> dict[str, Any]:
    """Extend the authenticated promotion contract with public GET-only probes."""
    document = promotion_approval_openapi_document()
    for path in ("/healthz", "/readyz"):
        responses = {"200": {"description": "Service is live" if path == "/healthz" else "Existing storage schema is readable"},
                     "405": {"description": "Only GET is supported"}}
        if path == "/readyz":
            responses["503"] = {"description": "Storage unavailable; sanitized response, no database creation"}
        document["paths"][path] = {"get": {"security": [], "responses": responses}}
    return document


@contextmanager
def promotion_service_from_env(environ: Mapping[str, str] | None = None) -> Iterator[WSGIServer]:
    """Own the listener and sandbox lifecycle, including listener-bind failures."""
    config = PromotionServiceConfig.from_env(environ)
    with build_promotion_sandbox_app_from_env(resolve_bearer=config.resolve_bearer, environ=environ) as app:
        database_path = RuntimeConfig.from_env(environ).database_path
        database = SQLiteDatabase(database_path)
        try:
            SQLiteExternalActionOperationRepository(database)
        finally:
            database.close()
        service = PromotionServiceWSGIApp(app, database_path=database_path)
        with make_server(config.host, config.port, service) as server:
            yield server


def main() -> None:
    """Serve local dry runs and translate SIGTERM into context-managed cleanup."""
    previous = signal.getsignal(signal.SIGTERM)
    def stop(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt
    with promotion_service_from_env() as server:
        signal.signal(signal.SIGTERM, stop)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    main()
