"""Production composition helpers that enforce runtime safety flags."""

from __future__ import annotations

import os
from dataclasses import dataclass
from hashlib import sha256
from career_agent_ai.application.storage.sqlite_candidate_schedule import SQLiteCandidateSchedule
from career_agent_ai.application.storage.sqlite_candidate_interview_intents import SQLiteCandidateInterviewIntents
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
    CandidateApprovalConflictError,
)
from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopPhase,
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
from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus
from career_agent_ai.application.jobs.in_memory_job_repository import InMemoryJobRepository
from career_agent_ai.application.observability import OperationalIssue
from career_agent_ai.application.search.job_provider import JobProvider
from career_agent_ai.application.search.search_service import SearchService
from career_agent_ai.application.scheduling import FakeCalendarAdapter, SchedulingService
from career_agent_ai.application.scheduling.calendar_adapter import CalendarReconciliationEvidenceError
from career_agent_ai.application.scheduling.models import ScheduleStatus, validate_schedule_identifier
from career_agent_ai.application.scheduling.scheduling_repository import SchedulingConflictError
from career_agent_ai.application.external_actions.external_action_operation import ExternalActionStatus
from career_agent_ai.application.storage.sqlite_career_loop_repository import (
    SQLiteCandidateRunStatusProjection,
    SQLiteCareerLoopRepository,
)
from career_agent_ai.application.storage.sqlite_communication_repository import (
    SQLiteCommunicationRepository,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_sandbox_submission_provider import (
    SandboxOutcomePlan,
    SQLiteSandboxSubmissionProvider,
)
from career_agent_ai.application.storage.sqlite_sandbox_communication_provider import (
    SQLiteSandboxCommunicationProvider,
)
from career_agent_ai.application.storage.sqlite_sandbox_calendar_provider import (
    SandboxCalendarOutcomePlan,
    SQLiteSandboxCalendarProvider,
)
from career_agent_ai.application.storage.sqlite_candidate_applications import SQLiteCandidateApplications
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
        sandbox_outcome_plan: SandboxOutcomePlan | None = None,
        sandbox_calendar_outcome_plan: SandboxCalendarOutcomePlan | None = None,
    ) -> None:
        self._database_path = database_path
        self._jobs = jobs
        self._resolve_bearer = resolve_bearer
        self._sandbox_outcome_plan = sandbox_outcome_plan
        self._sandbox_calendar_outcome_plan = sandbox_calendar_outcome_plan
        self._closed = False

    def __call__(self, environ: Mapping[str, Any], start_response) -> Iterable[bytes]:
        """Serve a candidate approval request while the sandbox is open."""
        if self._closed:
            raise RuntimeError("Candidate sandbox runtime is closed.")
        app = CandidateApprovalWSGIApp(
            _RequestCandidateLoop(
                self._database_path, self._jobs, self._sandbox_outcome_plan,
                self._sandbox_calendar_outcome_plan
            ),
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
            loop = _build_candidate_loop(
                database, self._jobs, self._sandbox_outcome_plan,
                self._sandbox_calendar_outcome_plan
            )
            if run_id is None:
                return loop.start(request)
            return start_owner_scoped_run(loop, request, client_run_id=run_id)
        finally:
            database.close()

    def close(self) -> None:
        """Prevent new requests and sandbox runs."""
        self._closed = True

    def list_run_statuses(self, *, user_id: str, limit: int = 25, after: str | None = None,
                          phase: CareerLoopPhase | None = None) -> dict[str, Any]:
        """List trusted caller's run snapshots without creating storage or executing work."""
        if self._closed:
            raise RuntimeError("Candidate sandbox runtime is closed.")
        database = SQLiteDatabase.open_read_only(self._database_path)
        try:
            return SQLiteCandidateRunStatusProjection(database).list_page(
                user_id=user_id, limit=limit, after=after, phase=phase)
        finally:
            database.close()

    def list_applications(self, *, user_id: str, limit: int = 25,
                          after: str | None = None,
                          status: JobApplicationStatus | None = None) -> dict[str, Any]:
        """Read trusted caller's bounded application snapshots without workflow actions."""
        if self._closed:
            raise RuntimeError("Candidate sandbox runtime is closed.")
        return SQLiteCandidateApplications(self._database_path).list_page(
            user_id=user_id, limit=limit, after=after, status=status)

    def get_application(self, application_id: str, *, user_id: str) -> dict[str, Any]:
        """Read an owned application snapshot using the trusted caller's identity."""
        if self._closed:
            raise RuntimeError("Candidate sandbox runtime is closed.")
        return SQLiteCandidateApplications(self._database_path).get_snapshot(
            application_id, user_id=user_id)

    def get_application_history(self, application_id: str, *, user_id: str) -> dict[str, Any]:
        """Read trusted caller's minimal lifecycle history without execution or writes."""
        if self._closed:
            raise RuntimeError("Candidate sandbox runtime is closed.")
        return SQLiteCandidateApplications(self._database_path).get_history(
            application_id, user_id=user_id)

    def get_schedule_event(self, event_id: str, *, user_id: str) -> dict[str, Any]:
        """Read trusted caller's event details without calendar I/O or writes."""
        if self._closed:
            raise RuntimeError("Candidate sandbox runtime is closed.")
        return SQLiteCandidateSchedule(self._database_path).get_snapshot(event_id, user_id=user_id)

    def list_schedule_events(self, *, user_id: str, limit: int = 25,
                             after: str | None = None,
                             status: ScheduleStatus | None = None) -> dict[str, Any]:
        """Read trusted caller's owned event pages without writes or calendar actions."""
        if self._closed:
            raise RuntimeError("Candidate sandbox runtime is closed.")
        return SQLiteCandidateSchedule(self._database_path).list_page(
            user_id=user_id, limit=limit, after=after, status=status)

    def list_interview_intents(self, *, user_id: str, limit: int = 25,
                               after: str | None = None) -> dict[str, Any]:
        """Discover owned pending interview actions through read-only storage."""
        if self._closed:
            raise RuntimeError("Candidate sandbox runtime is closed.")
        return SQLiteCandidateInterviewIntents(self._database_path).list_page(
            user_id=user_id, limit=limit, after=after)

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

    def __init__(
        self,
        database_path: str,
        jobs: tuple[Job, ...],
        outcome_plan: SandboxOutcomePlan | None = None,
        calendar_outcome_plan: SandboxCalendarOutcomePlan | None = None,
    ) -> None:
        self._database_path = database_path
        self._jobs = jobs
        self._outcome_plan = outcome_plan
        self._calendar_outcome_plan = calendar_outcome_plan

    def list_interview_intents(self, *, user_id: str, limit: int = 25,
                               after: str | None = None) -> dict[str, Any]:
        """Discover owned pending interview actions through request-local read-only storage."""
        return SQLiteCandidateInterviewIntents(self._database_path).list_page(
            user_id=user_id, limit=limit, after=after)

    def decline_schedule_event(
        self, event_id: str, *, user_id: str, expected_state_version: int
    ) -> dict[str, Any]:
        """Execute one explicitly confirmed, owner/version-fenced sandbox decline."""
        owner = validate_schedule_identifier(user_id, "user_id")
        identifier = validate_schedule_identifier(event_id, "event_id")
        if type(expected_state_version) is not int or expected_state_version < 1:
            raise ValueError("expected_state_version must be a positive integer.")
        visible = SQLiteCandidateSchedule(self._database_path).get_snapshot(
            identifier, user_id=owner
        )["event"]
        if visible["state_version"] != expected_state_version:
            raise CandidateApprovalConflictError("Schedule event changed; reload it.")
        database = SQLiteDatabase(self._database_path)
        try:
            scheduling = _build_scheduling_service(
                database,
                self._calendar_outcome_plan,
                SQLiteExternalActionOperationRepository(database),
            )
            event = scheduling.get_event(identifier)
            if event.candidate_id != owner:
                raise KeyError("Schedule event not found.")
            if event.version != expected_state_version:
                raise CandidateApprovalConflictError("Schedule event changed; reload it.")
            operation_id = _candidate_calendar_decline_operation_id(
                owner, identifier, expected_state_version
            )
            try:
                declined = scheduling.decline(
                    operation_id,
                    identifier,
                    human_approved=True,
                    expected_version=expected_state_version,
                )
            except SchedulingConflictError as exc:
                raise CandidateApprovalConflictError("Schedule event changed; reload it.") from exc
            operation = scheduling.get_operation(operation_id)
            if declined is None:
                if operation is None:
                    raise CandidateApprovalConflictError(
                        "Calendar decline outcome is unavailable."
                    )
                if operation.status == ExternalActionStatus.FAILED:
                    return {
                        "schema_version": 1,
                        "event_id": identifier,
                        "status": event.status.value,
                        "state_version": expected_state_version,
                        "provider_status": "no_effect_confirmed",
                    }
                if operation.status != ExternalActionStatus.RECONCILIATION_REQUIRED:
                    raise CandidateApprovalConflictError(
                        "Calendar decline did not reach a confirmed or reconcilable outcome."
                    )
                return {
                    "schema_version": 1,
                    "event_id": identifier,
                    "status": event.status.value,
                    "state_version": expected_state_version,
                    "provider_status": "reconciliation_required",
                }
            return {
                "schema_version": 1,
                "event_id": identifier,
                "status": declined.status.value,
                "state_version": declined.version,
                "provider_status": "confirmed",
            }
        finally:
            database.close()

    def resolve_schedule_event_decline(
        self,
        event_id: str,
        *,
        user_id: str,
        expected_state_version: int,
        completed: bool,
    ) -> dict[str, Any]:
        """Reconcile one ambiguous decline using only the sandbox provider's evidence."""
        owner = validate_schedule_identifier(user_id, "user_id")
        identifier = validate_schedule_identifier(event_id, "event_id")
        if type(expected_state_version) is not int or expected_state_version < 1:
            raise ValueError("expected_state_version must be a positive integer.")
        if type(completed) is not bool:
            raise ValueError("completed must be a boolean.")
        visible = SQLiteCandidateSchedule(self._database_path).get_snapshot(
            identifier, user_id=owner
        )["event"]
        database = SQLiteDatabase(self._database_path)
        try:
            scheduling = _build_scheduling_service(
                database,
                self._calendar_outcome_plan,
                SQLiteExternalActionOperationRepository(database),
            )
            operation_id = _candidate_calendar_decline_operation_id(
                owner, identifier, expected_state_version
            )
            operation = scheduling.get_operation(operation_id)
            if (
                operation is None
                or operation.action_type != "calendar.decline"
                or operation.payload.get("event_id") != identifier
                or operation.payload.get("event_version") != expected_state_version
            ):
                raise KeyError("Pending decline not found.")
            if operation.status == ExternalActionStatus.RECONCILIATION_REQUIRED:
                if visible["state_version"] != expected_state_version:
                    raise CandidateApprovalConflictError("Schedule event changed; reload it.")
            elif operation.status == ExternalActionStatus.SUCCEEDED and completed:
                pass
            else:
                raise CandidateApprovalConflictError(
                    "Decline is no longer awaiting reconciliation."
                )
            try:
                resolved = scheduling.resolve_reconciliation(
                    operation_id, completed=completed
                )
            except CalendarReconciliationEvidenceError as exc:
                raise CandidateApprovalConflictError(
                    "Sandbox provider evidence does not support this outcome."
                ) from exc
            if completed:
                snapshot = resolved.result.get("event_snapshot") if resolved.result else None
                if not isinstance(snapshot, dict):
                    raise CandidateApprovalConflictError("Decline result is unavailable.")
                return {
                    "schema_version": 1,
                    "event_id": identifier,
                    "status": snapshot["status"],
                    "state_version": snapshot["version"],
                    "provider_status": "confirmed",
                }
            current = scheduling.get_event(identifier)
            return {
                "schema_version": 1,
                "event_id": identifier,
                "status": current.status.value,
                "state_version": current.version,
                "provider_status": "no_effect_confirmed",
            }
        finally:
            database.close()

    def get_schedule_event(self, event_id: str, *, user_id: str) -> dict[str, Any]:
        """Read authenticated owner's event without writable storage or calendar I/O."""
        return SQLiteCandidateSchedule(self._database_path).get_snapshot(event_id, user_id=user_id)

    def list_schedule_events(self, *, user_id: str, limit: int = 25,
                             after: str | None = None,
                             status: ScheduleStatus | None = None) -> dict[str, Any]:
        """Read authenticated event pages using request-local read-only storage."""
        return SQLiteCandidateSchedule(self._database_path).list_page(
            user_id=user_id, limit=limit, after=after, status=status)

    def get_application_history(self, application_id: str, *, user_id: str) -> dict[str, Any]:
        """Read authenticated owner's history through request-local read-only storage."""
        return SQLiteCandidateApplications(self._database_path).get_history(
            application_id, user_id=user_id)

    def get_application(self, application_id: str, *, user_id: str) -> dict[str, Any]:
        """Read authenticated owner's snapshot without constructing writable storage."""
        return SQLiteCandidateApplications(self._database_path).get_snapshot(
            application_id, user_id=user_id)

    def list_applications(self, *, user_id: str, limit: int = 25,
                          after: str | None = None,
                          status: JobApplicationStatus | None = None) -> dict[str, Any]:
        """Read authenticated owner snapshots without building writable repositories."""
        return SQLiteCandidateApplications(self._database_path).list_page(
            user_id=user_id, limit=limit, after=after, status=status)

    def list_run_statuses(self, *, user_id: str, limit: int = 25, after: str | None = None,
                          phase: CareerLoopPhase | None = None) -> dict[str, Any]:
        """Read an authenticated owner-only collection using request-local storage."""
        database = SQLiteDatabase.open_read_only(self._database_path)
        try:
            return SQLiteCandidateRunStatusProjection(database).list_page(
                user_id=user_id, limit=limit, after=after, phase=phase)
        finally:
            database.close()

    def start(self, request: CareerLoopRequest, *, run_id: str | None = None):
        """Start one authenticated sandbox run using request-thread storage."""
        database = SQLiteDatabase(self._database_path)
        try:
            return _build_candidate_loop(
                database, self._jobs, self._outcome_plan,
                self._calendar_outcome_plan
            ).start(
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
            return _build_candidate_loop(
                database, self._jobs, self._outcome_plan,
                self._calendar_outcome_plan
            ).replay_existing_start(
                request, run_id=run_id, user_id=user_id
            )
        finally:
            database.close()

    def get_candidate_run_status(self, run_id: str, *, user_id: str) -> dict[str, Any]:
        """Read status without permitting database creation, migration, or writes."""
        database = SQLiteDatabase.open_read_only(self._database_path)
        try:
            return SQLiteCandidateRunStatusProjection(database).get(run_id, user_id=user_id)
        finally:
            database.close()

    def get_candidate_approval_prompt(self, run_id: str, *, user_id: str):
        """Load one owner-scoped prompt using request-thread storage."""
        database = SQLiteDatabase(self._database_path)
        try:
            return _build_candidate_loop(
                database, self._jobs, self._outcome_plan,
                self._calendar_outcome_plan
            ).get_candidate_approval_prompt(run_id, user_id=user_id)
        finally:
            database.close()

    def resume_candidate_submission(self, *, user_id: str, submission):
        """Resume one owner-scoped run using request-thread storage."""
        database = SQLiteDatabase(self._database_path)
        try:
            return _build_candidate_loop(
                database, self._jobs, self._outcome_plan,
                self._calendar_outcome_plan
            ).resume_candidate_submission(user_id=user_id, submission=submission)
        finally:
            database.close()

    def continue_run(self, run_id: str, *, user_id: str):
        """Recover one owner-scoped non-human phase using request-thread storage."""
        database = SQLiteDatabase(self._database_path)
        try:
            return _build_candidate_loop(
                database, self._jobs, self._outcome_plan,
                self._calendar_outcome_plan
            ).continue_run(
                run_id, user_id=user_id
            )
        finally:
            database.close()

    def resolve_application_reconciliation(
        self,
        run_id: str,
        *,
        user_id: str,
        submitted: bool,
        provider_submission_id: str | None = None,
        expected_state_version: int | None = None,
    ):
        """Resolve one owner-scoped provider observation using request storage."""
        database = SQLiteDatabase(self._database_path)
        try:
            return _build_candidate_loop(
                database, self._jobs, self._outcome_plan,
                self._calendar_outcome_plan
            ).resolve_application_reconciliation(
                run_id,
                user_id=user_id,
                submitted=submitted,
                provider_submission_id=provider_submission_id,
                expected_state_version=expected_state_version,
            )
        finally:
            database.close()

    def resolve_message_reconciliation(
        self,
        run_id: str,
        *,
        user_id: str,
        delivered: bool,
        expected_state_version: int | None = None,
    ):
        """Resolve one owner-scoped message observation using request storage."""
        database = SQLiteDatabase(self._database_path)
        try:
            return _build_candidate_loop(
                database, self._jobs, self._outcome_plan,
                self._calendar_outcome_plan
            ).resolve_message_reconciliation(
                run_id,
                user_id=user_id,
                delivered=delivered,
                expected_state_version=expected_state_version,
            )
        finally:
            database.close()

    def resolve_interview_reconciliation(
        self, run_id: str, *, user_id: str, completed: bool,
        provider_event_id: str | None = None,
        expected_state_version: int | None = None,
    ):
        """Resolve one owner-scoped calendar observation using request storage."""
        database = SQLiteDatabase(self._database_path)
        try:
            return _build_candidate_loop(
                database, self._jobs, self._outcome_plan,
                self._calendar_outcome_plan
            ).resolve_interview_reconciliation(
                run_id, user_id=user_id, completed=completed,
                provider_event_id=provider_event_id,
                expected_state_version=expected_state_version,
            )
        finally:
            database.close()


def build_candidate_sandbox_app_from_env(
    *,
    resolve_bearer: Callable[[str], str | None],
    jobs: Iterable[Job] = (),
    environ: Mapping[str, str] | None = None,
    sandbox_outcome_plan: SandboxOutcomePlan | None = None,
    sandbox_calendar_outcome_plan: SandboxCalendarOutcomePlan | None = None,
) -> RuntimeCandidateApp:
    """Compose a durable candidate API using only deterministic no-I/O adapters."""
    config = validate_candidate_sandbox_environment(environ)
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
        _build_candidate_loop(
            database, configured_jobs, sandbox_outcome_plan, sandbox_calendar_outcome_plan
        )
    finally:
        database.close()
    return RuntimeCandidateApp(
        database_path=config.database_path,
        jobs=configured_jobs,
        resolve_bearer=resolve_bearer,
        sandbox_outcome_plan=sandbox_outcome_plan,
        sandbox_calendar_outcome_plan=sandbox_calendar_outcome_plan,
    )


def validate_candidate_sandbox_environment(
    environ: Mapping[str, str] | None = None,
) -> RuntimeConfig:
    """Validate candidate policy without opening or mutating durable storage."""
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
    return config


def _candidate_calendar_decline_operation_id(
    user_id: str, event_id: str, event_version: int
) -> str:
    digest = sha256(
        f"{user_id}\n{event_id}\n{event_version}".encode("utf-8")
    ).hexdigest()
    return f"candidate-calendar-decline:{digest}"


def _build_scheduling_service(
    database: SQLiteDatabase,
    calendar_outcome_plan: SandboxCalendarOutcomePlan | None = None,
    operations: SQLiteExternalActionOperationRepository | None = None,
) -> SchedulingService:
    repository = SQLiteSchedulingRepository(database)
    provider = SQLiteSandboxCalendarProvider(database, calendar_outcome_plan)
    operation_repository = operations or SQLiteExternalActionOperationRepository(database)
    return SchedulingService(
        repository,
        provider,
        ExternalActionService(
            operation_repository,
            SchedulingService.action_adapter(provider, repository),
        ),
        require_provider_evidence=True,
    )


def _build_candidate_loop(
    database: SQLiteDatabase,
    jobs: tuple[Job, ...],
    outcome_plan: SandboxOutcomePlan | None = None,
    calendar_outcome_plan: SandboxCalendarOutcomePlan | None = None,
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
    submission_adapter = SQLiteSandboxSubmissionProvider(database, outcome_plan)
    submission = ApplicationSubmissionService(
        submission_adapter,
        ExternalActionService(
            operations,
            ApplicationSubmissionService.action_adapter(submission_adapter),
        ),
        require_provider_evidence=True,
    )
    communication_repository = SQLiteCommunicationRepository(database)
    communication_adapter = SQLiteSandboxCommunicationProvider(database)
    communication = CommunicationService(
        communication_repository,
        communication_adapter,
        ExternalActionService(
            operations,
            CommunicationService.action_adapter(
                communication_adapter, communication_repository
            ),
        ),
        require_provider_evidence=True,
    )
    scheduling = _build_scheduling_service(
        database, calendar_outcome_plan, operations
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
