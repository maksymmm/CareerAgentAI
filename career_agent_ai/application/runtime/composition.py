"""Production composition helpers that enforce runtime safety flags."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from career_agent_ai.application.api import OperationalApiService, OperationalWSGIApp

from career_agent_ai.application.career.opportunity_signal_provider import (
    OpportunitySignalProvider,
)
from career_agent_ai.application.career.opportunity_signal import OpportunitySignal
from career_agent_ai.application.external_actions import (
    ExternalActionAdapter,
    ExternalActionService,
)
from career_agent_ai.application.external_actions.external_action_repository import (
    ExternalActionOperationRepository,
)
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.search.job_provider import JobProvider
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_operational_probe import (
    SQLiteOperationalProbe,
)

from .config import RuntimeConfig


class RuntimeOperationalApp:
    """Own the database lifecycle for the composed operational WSGI app."""

    def __init__(self, app: OperationalWSGIApp, database: SQLiteDatabase) -> None:
        self._app = app
        self._database = database
        self._closed = False

    def __call__(
        self,
        environ: Mapping[str, Any],
        start_response: Callable[[str, list[tuple[str, str]]], Any],
    ) -> Iterable[bytes]:
        """Delegate a WSGI request while the runtime remains open."""
        if self._closed:
            raise RuntimeError("Operational runtime is closed.")
        return self._app(environ, start_response)

    def close(self) -> None:
        """Close the owned database connection idempotently."""
        if not self._closed:
            self._database.close()
            self._closed = True

    def __enter__(self) -> "RuntimeOperationalApp":
        """Return this runtime for context-managed deployment checks."""
        if self._closed:
            raise RuntimeError("Operational runtime is closed.")
        return self

    def __exit__(self, *_: object) -> None:
        """Close the owned database connection on context exit."""
        self.close()


def build_operational_app_from_env(
    environ: Mapping[str, str] | None = None,
) -> RuntimeOperationalApp:
    """Compose the operational WSGI app from validated runtime environment values."""
    config = RuntimeConfig.from_env(environ)
    source = environ if environ is not None else os.environ
    bearer_token = source.get("CAREER_AGENT_OPERATIONAL_BEARER_TOKEN", "")
    OperationalWSGIApp.validate_bearer_token(bearer_token)

    database = SQLiteDatabase(config.database_path)
    try:
        probe = SQLiteOperationalProbe(database)
        service = OperationalApiService(probe)
        app = OperationalWSGIApp(service, bearer_token=bearer_token)
        return RuntimeOperationalApp(app, database)
    except Exception:
        database.close()
        raise


@dataclass(frozen=True)
class RuntimeGuardedSignalProvider:
    """Block network-backed signal collection unless runtime policy enables it."""

    config: RuntimeConfig
    provider: OpportunitySignalProvider

    def collect(self) -> tuple[OpportunitySignal, ...]:
        """Collect signals only when network providers are explicitly enabled."""
        if not self.config.allow_network_providers:
            raise PermissionError("Network providers are disabled by runtime policy.")
        return self.provider.collect()


@dataclass(frozen=True)
class RuntimeGuardedJobProvider(JobProvider):
    """Block live job searches unless runtime policy enables network providers."""

    config: RuntimeConfig
    provider: JobProvider

    def search(self, query: str) -> tuple[Job, ...]:
        """Search only when the runtime network-provider flag is enabled."""
        if not self.config.allow_network_providers:
            raise PermissionError("Network providers are disabled by runtime policy.")
        return self.provider.search(query)


def build_external_action_service(
    config: RuntimeConfig,
    repository: ExternalActionOperationRepository,
    adapter: ExternalActionAdapter,
) -> ExternalActionService:
    """Build an action service whose provider call obeys the runtime kill switch."""
    if not isinstance(config, RuntimeConfig):
        raise TypeError("config must be a RuntimeConfig.")
    return ExternalActionService(
        repository,
        adapter,
        execution_allowed=lambda: config.allow_consequential_actions,
    )


def guard_signal_provider(
    config: RuntimeConfig,
    provider: OpportunitySignalProvider,
) -> RuntimeGuardedSignalProvider:
    """Wrap a signal provider so disabled network policy prevents collection."""
    if not isinstance(config, RuntimeConfig):
        raise TypeError("config must be a RuntimeConfig.")
    return RuntimeGuardedSignalProvider(config=config, provider=provider)


def guard_job_provider(
    config: RuntimeConfig,
    provider: JobProvider,
) -> RuntimeGuardedJobProvider:
    """Wrap a job provider so disabled network policy prevents live search."""
    if not isinstance(config, RuntimeConfig):
        raise TypeError("config must be a RuntimeConfig.")
    return RuntimeGuardedJobProvider(config=config, provider=provider)
