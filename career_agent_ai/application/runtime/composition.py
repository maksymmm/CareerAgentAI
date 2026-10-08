"""Production composition helpers that enforce runtime safety flags."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping

from career_agent_ai.application.api import (
    CandidateApprovalWSGIApp,
    OperationalApiService,
    OperationalWSGIApp,
    start_owner_scoped_run,
)
from career_agent_ai.application.agents.agent_factory import AgentFactory
from career_agent_ai.application.agents.agent_registry import AgentRegistry
from career_agent_ai.application.agents.job_search.job_search_agent import JobSearchAgent
from career_agent_ai.application.agents.resume.resume_agent import ResumeAgent
from career_agent_ai.application.career import (
    ApplicationSubmissionService,
    AutonomousCareerLoop,
    FakeApplicationSubmissionAdapter,
)
from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopRequest,
    CareerLoopResult,
    validate_loop_identifier,
)
from career_agent_ai.application.communication import (
    CommunicationService,
    FakeCommunicationAdapter,
)

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
from career_agent_ai.application.jobs.in_memory_job_repository import InMemoryJobRepository
from career_agent_ai.application.observability import OperationalIssue
from career_agent_ai.application.search.job_provider import JobProvider
from career_agent_ai.application.search.search_service import SearchService
from career_agent_ai.application.scheduling import FakeCalendarAdapter, SchedulingService
from career_agent_ai.application.storage.sqlite_career_loop_repository import (
    SQLiteCareerLoopRepository,
)
from career_agent_ai.application.storage.sqlite_communication_repository import (
    SQLiteCommunicationRepository,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_operational_probe import (
    SQLiteOperationalProbe,
)
from career_agent_ai.application.storage.sqlite_external_action_repository import (
    SQLiteExternalActionOperationRepository,
)
from career_agent_ai.application.storage.sqlite_job_application_repository import (
    SQLiteJobApplicationRepository,
)
from career_agent_ai.application.storage.sqlite_scheduling_repository import (
    SQLiteSchedulingRepository,
)

from .config import RuntimeConfig


class RuntimeOperationalApp:
    """Open an isolated SQLite connection for each operational WSGI request."""

    def __init__(self, *, database_path: str, bearer_token: str) -> None:
        probe = _RequestSQLiteOperationalProbe(database_path)
        service = OperationalApiService(probe)
        self._app = OperationalWSGIApp(service, bearer_token=bearer_token)
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
        """Prevent new requests after all active request calls have returned."""
        self._closed = True

    def __enter__(self) -> "RuntimeOperationalApp":
        """Return this runtime for context-managed deployment checks."""
        if self._closed:
            raise RuntimeError("Operational runtime is closed.")
        return self

    def __exit__(self, *_: object) -> None:
        """Prevent new requests on context exit."""
        self.close()


class RuntimeCandidateApp:
    """Create request-local candidate loops over one durable SQLite database."""

    def __init__(
        self,
        *,
        database_path: str,
        jobs: tuple[Job, ...],
        resolve_bearer: Callable[[str], str | None],
    ) -> None:
        self._database_path = database_path
        self._jobs = jobs
        self._resolve_bearer = resolve_bearer
        self._closed = False

    def __call__(self, environ: Mapping[str, Any], start_response) -> Iterable[bytes]:
        """Serve a candidate approval request while the sandbox is open."""
        if self._closed:
            raise RuntimeError("Candidate sandbox runtime is closed.")
        app = CandidateApprovalWSGIApp(
            _RequestCandidateLoop(self._database_path, self._jobs),
            resolve_bearer=self._resolve_bearer,
        )
        return app(environ, start_response)

    def start(
        self, request: CareerLoopRequest, *, run_id: str | None = None
    ) -> CareerLoopResult:
        """Start a sandbox run through the owner-scoped transition."""
        if self._closed:
            raise RuntimeError("Candidate sandbox runtime is closed.")
        if run_id is not None:
            validated_run_id = validate_loop_identifier(
                run_id, "run_id", maximum=120
            )
            if validated_run_id != run_id:
                raise ValueError("run_id must match the published identifier pattern.")
        database = SQLiteDatabase(self._database_path)
        try:
            loop = _build_candidate_loop(database, self._jobs)
            if run_id is None:
                return loop.start(request)
            return start_owner_scoped_run(loop, request, client_run_id=run_id)
        finally:
            database.close()

    def close(self) -> None:
        """Prevent new requests and sandbox runs."""
        self._closed = True

    def __enter__(self) -> "RuntimeCandidateApp":
        """Return this open candidate sandbox runtime."""
        if self._closed:
            raise RuntimeError("Candidate sandbox runtime is closed.")
        return self

    def __exit__(self, *_: object) -> None:
        """Close the sandbox on context exit."""
        self.close()


class _RequestCandidateLoop:
    """Open durable storage only after the API authenticates and routes a request."""

    def __init__(self, database_path: str, jobs: tuple[Job, ...]) -> None:
        self._database_path = database_path
        self._jobs = jobs

    def start(self, request: CareerLoopRequest, *, run_id: str | None = None):
        """Start one authenticated sandbox run using request-thread storage."""
        database = SQLiteDatabase(self._database_path)
        try:
            return _build_candidate_loop(database, self._jobs).start(
                request, run_id=run_id
            )
        finally:
            database.close()

    def replay_existing_start(
        self, request: CareerLoopRequest, *, run_id: str, user_id: str
    ):
        """Replay an owned legacy start using request-thread storage."""
        database = SQLiteDatabase(self._database_path)
        try:
            return _build_candidate_loop(database, self._jobs).replay_existing_start(
                request, run_id=run_id, user_id=user_id
            )
        finally:
            database.close()

    def get_candidate_run_status(self, run_id: str, *, user_id: str) -> dict[str, Any]:
        """Read a candidate status snapshot using request-thread storage."""
        database = SQLiteDatabase(self._database_path)
        try:
            return _build_candidate_loop(database, self._jobs).get_candidate_run_status(
                run_id, user_id=user_id
            )
        finally:
            database.close()

    def get_candidate_approval_prompt(self, run_id: str, *, user_id: str):
        """Load one owner-scoped prompt using request-thread storage."""
        database = SQLiteDatabase(self._database_path)
        try:
            return _build_candidate_loop(
                database, self._jobs
            ).get_candidate_approval_prompt(run_id, user_id=user_id)
        finally:
            database.close()

    def resume_candidate_submission(self, *, user_id: str, submission):
        """Resume one owner-scoped run using request-thread storage."""
        database = SQLiteDatabase(self._database_path)
        try:
            return _build_candidate_loop(
                database, self._jobs
            ).resume_candidate_submission(user_id=user_id, submission=submission)
        finally:
            database.close()

    def continue_run(self, run_id: str, *, user_id: str):
        """Recover one owner-scoped non-human phase using request-thread storage."""
        database = SQLiteDatabase(self._database_path)
        try:
            return _build_candidate_loop(database, self._jobs).continue_run(
                run_id, user_id=user_id
            )
        finally:
            database.close()


def build_candidate_sandbox_app_from_env(
    *,
    resolve_bearer: Callable[[str], str | None],
    jobs: Iterable[Job] = (),
    environ: Mapping[str, str] | None = None,
) -> RuntimeCandidateApp:
    """Compose a durable candidate API using only deterministic no-I/O adapters."""
    source = environ if environ is not None else os.environ
    if not RuntimeConfig._parse_bool(
        source.get("CAREER_AGENT_CANDIDATE_SANDBOX", "false"),
        "CAREER_AGENT_CANDIDATE_SANDBOX",
    ):
        raise PermissionError("Candidate sandbox must be explicitly enabled.")
    if source.get("CAREER_AGENT_CANDIDATE_ID_CUTOVER") != "drain-and-replace-scoped-v2":
        raise PermissionError(
            "Candidate sandbox requires the drain-and-replace-scoped-v2 ID cutover gate."
        )
    config = RuntimeConfig.from_env(environ)
    if config.allow_network_providers or config.allow_consequential_actions:
        raise ValueError(
            "Candidate sandbox forbids network providers and consequential actions."
        )
    if not callable(resolve_bearer):
        raise TypeError("resolve_bearer must be callable.")

    configured_jobs = tuple(jobs)
    for job in configured_jobs:
        if not isinstance(job, Job):
            raise TypeError("jobs must contain Job instances.")
    database = SQLiteDatabase(config.database_path)
    try:
        if database.is_memory:
            raise ValueError("Candidate sandbox requires durable SQLite storage.")
        _build_candidate_loop(database, configured_jobs)
    finally:
        database.close()
    return RuntimeCandidateApp(
        database_path=config.database_path,
        jobs=configured_jobs,
        resolve_bearer=resolve_bearer,
    )


def _build_candidate_loop(
    database: SQLiteDatabase, jobs: tuple[Job, ...]
) -> AutonomousCareerLoop:
    """Build one loop whose repositories share a request-owned connection."""
    job_repository = InMemoryJobRepository()
    for job in jobs:
        job_repository.add(job)
    search = SearchService(job_repository)
    registry = AgentRegistry()
    registry.register(JobSearchAgent(search_service=search))
    registry.register(ResumeAgent())
    factory = AgentFactory(registry, search_service=search)
    applications = SQLiteJobApplicationRepository(database)
    operations = SQLiteExternalActionOperationRepository(database)
    submission_adapter = FakeApplicationSubmissionAdapter()
    submission = ApplicationSubmissionService(
        submission_adapter,
        ExternalActionService(
            operations,
            ApplicationSubmissionService.action_adapter(submission_adapter),
        ),
    )
    communication_repository = SQLiteCommunicationRepository(database)
    communication_adapter = FakeCommunicationAdapter()
    communication = CommunicationService(
        communication_repository,
        communication_adapter,
        ExternalActionService(
            operations,
            CommunicationService.action_adapter(
                communication_adapter, communication_repository
            ),
        ),
    )
    scheduling_repository = SQLiteSchedulingRepository(database)
    calendar_adapter = FakeCalendarAdapter()
    scheduling = SchedulingService(
        scheduling_repository,
        calendar_adapter,
        ExternalActionService(
            operations,
            SchedulingService.action_adapter(calendar_adapter, scheduling_repository),
        ),
    )
    return AutonomousCareerLoop(
        agent_factory=factory,
        application_repository=applications,
        submission_service=submission,
        communication_service=communication,
        scheduling_service=scheduling,
        state_repository=SQLiteCareerLoopRepository(database),
    )


class _RequestSQLiteOperationalProbe:
    """Open SQLite only when a routed request needs durable inspection."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def inspect(
        self, *, now: datetime, stale_after_seconds: int
    ) -> tuple[OperationalIssue, ...]:
        """Inspect through a connection owned by the current request thread."""
        database = SQLiteDatabase.open_read_only(self._database_path)
        try:
            return SQLiteOperationalProbe(database).inspect(
                now=now,
                stale_after_seconds=stale_after_seconds,
            )
        finally:
            database.close()


def build_operational_app_from_env(
    environ: Mapping[str, str] | None = None,
) -> RuntimeOperationalApp:
    """Compose the operational WSGI app from validated runtime environment values."""
    source = environ if environ is not None else os.environ
    bearer_token = source.get("CAREER_AGENT_OPERATIONAL_BEARER_TOKEN", "")
    OperationalWSGIApp.validate_bearer_token(bearer_token)
    config = RuntimeConfig.from_env(environ)
    return RuntimeOperationalApp(
        database_path=config.database_path,
        bearer_token=bearer_token,
    )


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
