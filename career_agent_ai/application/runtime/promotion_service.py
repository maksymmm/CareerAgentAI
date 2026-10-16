"""Loopback-only HTTP entrypoint for explicitly enabled promotion dry runs."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hmac
import ipaddress
import os
import signal
from typing import Iterator, Mapping
from wsgiref.simple_server import WSGIServer, make_server

from career_agent_ai.application.api import OperationalWSGIApp, ReleaseOperator
from career_agent_ai.application.career.autonomous_loop_models import validate_loop_identifier
from .promotion_sandbox import build_promotion_sandbox_app_from_env


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


@contextmanager
def promotion_service_from_env(environ: Mapping[str, str] | None = None) -> Iterator[WSGIServer]:
    """Own the listener and sandbox lifecycle, including listener-bind failures."""
    config = PromotionServiceConfig.from_env(environ)
    with build_promotion_sandbox_app_from_env(resolve_bearer=config.resolve_bearer, environ=environ) as app:
        with make_server(config.host, config.port, app) as server:
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
