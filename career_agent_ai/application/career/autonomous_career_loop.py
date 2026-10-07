"""Bounded, restart-safe end-to-end autonomous career loop."""

from __future__ import annotations

import sqlite3
from datetime import timezone
from hashlib import sha256
from threading import Event, Thread
from time import monotonic
from typing import Any
from uuid import uuid4

from career_agent_ai.application.agents.agent_factory import AgentFactory
from career_agent_ai.application.brain.agent_context import AgentContext
from career_agent_ai.application.career.application_submission import (
    ApplicationSubmissionService,
)
from career_agent_ai.application.career.approval_contract import (
    ApprovalDecision,
    CandidateApprovalPrompt,
    CandidateApprovalSubmission,
    build_candidate_approval_prompt,
    validate_candidate_approval_submission,
)
from career_agent_ai.application.career.career_decision_engine import CareerDecisionEngine
from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopPhase,
    CareerLoopRequest,
    CareerLoopResult,
    CareerLoopState,
    HumanActionEvent,
    HumanActionKind,
    validate_loop_identifier,
)
from career_agent_ai.application.career.autonomous_loop_repository import (
    CareerLoopConflictError,
    CareerLoopRepository,
)
from career_agent_ai.application.communication import (
    CommunicationMessage,
    CommunicationService,
    MessageDirection,
)
from career_agent_ai.application.external_actions import ExternalActionStatus
from career_agent_ai.application.jobs.job_application import JobApplication
from career_agent_ai.application.jobs.job_application_repository import (
    ApplicationConflictError,
    ApplicationQuery,
    JobApplicationRepository,
)
from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus
from career_agent_ai.application.memory.memory_snapshot import MemorySnapshot
from career_agent_ai.application.observability.structured_logging import redact_text
from career_agent_ai.application.scheduling import ScheduleStatus, SchedulingService


class _RecoverableCareerLoopError(RuntimeError):
    """Signal a durable partial outcome that should be retried after restart."""


class _PostStepPersistenceError(RuntimeError):
    """Signal that work finished but the advanced loop snapshot was not confirmed."""


class HumanActionRequiredError(RuntimeError):
    """Signal that automatic recovery cannot cross a pending human-action gate."""


class _ExecutionLeaseHeartbeat:
    """Renew one durable execution lease while a potentially slow step is running."""

    def __init__(
        self,
        repository: CareerLoopRepository,
        run_id: str,
        owner_id: str,
        *,
        lease_seconds: int,
        interval_seconds: int,
    ) -> None:
        if interval_seconds < 1 or interval_seconds >= lease_seconds:
            raise ValueError("Heartbeat interval must be shorter than the execution lease.")
        self._repository = repository
        self._run_id = run_id
        self._owner_id = owner_id
        self._lease_seconds = lease_seconds
        self._interval_seconds = interval_seconds
        self._stop = Event()
        self._error: Exception | None = None
        self._thread = Thread(
            target=self._run,
            name=f"career-loop-heartbeat:{run_id}",
            daemon=True,
        )

    def start(self) -> None:
        """Start periodic lease renewal."""
        self._thread.start()

    def stop(self) -> None:
        """Stop renewal and wait briefly for the daemon thread to exit."""
        self._stop.set()
        self._thread.join(timeout=max(1, self._interval_seconds + 1))

    def raise_if_failed(self) -> None:
        """Raise when lease ownership could no longer be renewed safely."""
        if self._error is not None:
            raise CareerLoopConflictError(
                "Autonomous-loop execution lease heartbeat failed."
            ) from self._error

    def _run(self) -> None:
        """Renew the lease, retrying transient SQLite lock/busy contention safely."""
        retry_delay = min(1.0, max(0.05, self._interval_seconds / 4))
        retry_budget = max(
            retry_delay,
            self._lease_seconds - self._interval_seconds,
        )
        while not self._stop.wait(self._interval_seconds):
            deadline = monotonic() + retry_budget
            while True:
                try:
                    self._repository.renew_execution(
                        self._run_id,
                        self._owner_id,
                        lease_seconds=self._lease_seconds,
                    )
                    break
                except sqlite3.OperationalError as exc:
                    message = str(exc).lower()
                    if "locked" not in message and "busy" not in message:
                        self._error = exc
                        self._stop.set()
                        return
                    if monotonic() >= deadline:
                        self._error = exc
                        self._stop.set()
                        return
                    if self._stop.wait(retry_delay):
                        return
                except Exception as exc:
                    self._error = exc
                    self._stop.set()
                    return


class AutonomousCareerLoop:
    """Connect career discovery through interview coordination with durable gates."""

    DEFAULT_MAX_ITERATIONS = 16
    EXECUTION_LEASE_SECONDS = 900
    EXECUTION_HEARTBEAT_SECONDS = 60
    POST_EFFECT_RECOVERY_PHASES = frozenset({
        CareerLoopPhase.APPLICATION_SUBMIT,
        CareerLoopPhase.MESSAGE_SEND,
        CareerLoopPhase.INTERVIEW_ACCEPT,
    })

    def __init__(
        self,
        *,
        agent_factory: AgentFactory,
        application_repository: JobApplicationRepository,
        submission_service: ApplicationSubmissionService,
        communication_service: CommunicationService,
        scheduling_service: SchedulingService,
        state_repository: CareerLoopRepository,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
    ) -> None:
        if not isinstance(max_iterations, int) or isinstance(max_iterations, bool) or max_iterations < 1:
            raise ValueError("max_iterations must be a positive integer.")
        self._agents = agent_factory
        self._applications = application_repository
        self._submission = submission_service
        self._communication = communication_service
        self._scheduling = scheduling_service
        self._states = state_repository
        self._max_iterations = max_iterations
        self._decision_engine = CareerDecisionEngine()

    def start(
        self, request: CareerLoopRequest, *, run_id: str | None = None
    ) -> CareerLoopResult:
        """Create one durable bounded run and continue until a human gate or terminal state."""
        if not isinstance(request, CareerLoopRequest):
            raise TypeError("request must be a CareerLoopRequest.")
        raw_identifier = run_id if run_id is not None else uuid4().hex
        identifier = validate_loop_identifier(raw_identifier, "run_id", maximum=120)
        if self._states.get(identifier) is not None:
            raise ValueError("run_id already exists.")
        state = CareerLoopState(run_id=identifier, request=request)
        self._persist(state)
        return self._continue(state)

    def resume(self, run_id: str, *, user_id: str, approved: bool) -> CareerLoopResult:
        """Resume one human-gated run using an explicit approve/decline decision."""
        state = self._owned_state(run_id, user_id)
        if state.pending_human_action is None:
            raise RuntimeError("Career loop is not waiting for human action.")
        if not isinstance(approved, bool):
            raise TypeError("approved must be a boolean.")
        if state.pending_human_action.kind in {
            HumanActionKind.RECONCILE_APPLICATION_SUBMISSION,
            HumanActionKind.RECONCILE_MESSAGE_DELIVERY,
            HumanActionKind.RECONCILE_INTERVIEW_RESPONSE,
        }:
            raise RuntimeError(
                "Use the dedicated reconciliation resolver for ambiguous external outcomes."
            )
        return self._apply_human_decision(state, approved=approved)

    def get_candidate_approval_prompt(
        self, run_id: str, *, user_id: str
    ) -> CandidateApprovalPrompt:
        """Return the owner-scoped immutable prompt for one ordinary approval gate."""
        return build_candidate_approval_prompt(
            self._owned_state(run_id, user_id), user_id=user_id
        )

    def resume_candidate_submission(
        self,
        *,
        user_id: str,
        submission: CandidateApprovalSubmission,
    ) -> CareerLoopResult:
        """Atomically validate and persist a prompt-bound candidate decision."""
        if not isinstance(submission, CandidateApprovalSubmission):
            raise TypeError("submission must be a CandidateApprovalSubmission.")
        state = self._owned_state(submission.run_id, user_id)
        approval_owner = f"approval:{uuid4().hex}"
        self._states.claim_execution(
            state.run_id,
            approval_owner,
            expected_version=submission.state_version,
            lease_seconds=self.EXECUTION_LEASE_SECONDS,
        )
        try:
            decision = validate_candidate_approval_submission(
                state, user_id=user_id, submission=submission
            )
            result = self._apply_human_decision(
                state,
                approved=decision is ApprovalDecision.APPROVE,
                expected_owner_id=approval_owner,
                continue_after_approval=False,
            )
        finally:
            try:
                self._states.release_execution(state.run_id, approval_owner)
            except CareerLoopConflictError:
                pass
        if result.phase in {CareerLoopPhase.COMPLETE, CareerLoopPhase.FAILED}:
            return result
        return self._continue(state)

    def _apply_human_decision(
        self,
        state: CareerLoopState,
        *,
        approved: bool,
        expected_owner_id: str | None = None,
        continue_after_approval: bool = True,
    ) -> CareerLoopResult:
        """Persist one already-authorized ordinary decision and continue the loop."""
        if not approved:
            state.last_error = f"human_declined:{state.pending_human_action.kind.value}"
            state.pending_human_action = None
            state.phase = CareerLoopPhase.FAILED
            state.touch()
            self._persist(state, expected_owner_id=expected_owner_id)
            return self._result(state)

        approved_action = state.pending_human_action
        kind = approved_action.kind
        state.approved_human_action = approved_action
        state.pending_human_action = None
        if kind == HumanActionKind.APPROVE_APPLICATION:
            state.phase = CareerLoopPhase.APPLICATION_SUBMIT
        elif kind == HumanActionKind.APPROVE_MESSAGE:
            state.phase = CareerLoopPhase.MESSAGE_SEND
        elif kind == HumanActionKind.APPROVE_INTERVIEW:
            state.phase = CareerLoopPhase.INTERVIEW_ACCEPT
        else:
            raise ValueError("Unsupported pending human action.")
        state.touch()
        self._persist(state, expected_owner_id=expected_owner_id)
        if continue_after_approval:
            return self._continue(state)
        return self._result(state)

    def resolve_application_reconciliation(
        self,
        run_id: str,
        *,
        user_id: str,
        submitted: bool,
        provider_submission_id: str | None = None,
    ) -> CareerLoopResult:
        """Resolve a provider-verified ambiguous application submission outcome.

        submitted=True confirms that the provider accepted the application and
        requires its provider submission identifier. submitted=False confirms
        that no external effect occurred and safely reopens the same idempotent
        operation for retry under the already approved application intent.
        """
        state = self._owned_state(run_id, user_id)
        action = state.pending_human_action
        if (
            state.phase != CareerLoopPhase.APPLICATION_RECONCILIATION
            or action is None
            or action.kind != HumanActionKind.RECONCILE_APPLICATION_SUBMISSION
        ):
            raise RuntimeError("Career loop is not awaiting application reconciliation.")
        if not isinstance(submitted, bool):
            raise TypeError("submitted must be a boolean.")
        operation_id = action.details.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id:
            raise RuntimeError("Reconciliation action has no valid operation_id.")
        expected_application = action.details.get("application_id")
        if expected_application != state.application_id:
            raise RuntimeError("Reconciliation action does not match the tracked application.")

        resolution_owner = f"resolution:{uuid4().hex}"
        self._states.claim_execution(
            state.run_id,
            resolution_owner,
            expected_version=state.version,
            lease_seconds=self.EXECUTION_LEASE_SECONDS,
        )
        try:
            self._submission.resolve_reconciliation(
                operation_id,
                submitted=submitted,
                provider_submission_id=provider_submission_id,
            )
            state.pending_human_action = None
            state.last_error = None
            state.phase = CareerLoopPhase.APPLICATION_SUBMIT
            state.touch()
            self._persist(state, expected_owner_id=resolution_owner)
        finally:
            try:
                self._states.release_execution(state.run_id, resolution_owner)
            except CareerLoopConflictError:
                pass
        return self._continue(state)

    def resolve_message_reconciliation(
        self,
        run_id: str,
        *,
        user_id: str,
        delivered: bool,
    ) -> CareerLoopResult:
        """Resolve a verified ambiguous recruiter/employer message outcome."""
        state = self._owned_state(run_id, user_id)
        action = state.pending_human_action
        if (
            state.phase != CareerLoopPhase.MESSAGE_RECONCILIATION
            or action is None
            or action.kind != HumanActionKind.RECONCILE_MESSAGE_DELIVERY
        ):
            raise RuntimeError("Career loop is not awaiting message reconciliation.")
        operation_id = action.details.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id:
            raise RuntimeError("Message reconciliation action has no valid operation_id.")
        resolution_owner = f"resolution:{uuid4().hex}"
        self._states.claim_execution(
            state.run_id,
            resolution_owner,
            expected_version=state.version,
            lease_seconds=self.EXECUTION_LEASE_SECONDS,
        )
        try:
            self._communication.resolve_reconciliation(
                operation_id,
                delivered=delivered,
            )
            state.pending_human_action = None
            state.last_error = None
            state.phase = CareerLoopPhase.MESSAGE_SEND
            state.touch()
            self._persist(state, expected_owner_id=resolution_owner)
        finally:
            try:
                self._states.release_execution(state.run_id, resolution_owner)
            except CareerLoopConflictError:
                pass
        return self._continue(state)

    def resolve_interview_reconciliation(
        self,
        run_id: str,
        *,
        user_id: str,
        completed: bool,
        provider_event_id: str | None = None,
    ) -> CareerLoopResult:
        """Resolve a verified ambiguous interview/calendar response outcome."""
        state = self._owned_state(run_id, user_id)
        action = state.pending_human_action
        if (
            state.phase != CareerLoopPhase.INTERVIEW_RECONCILIATION
            or action is None
            or action.kind != HumanActionKind.RECONCILE_INTERVIEW_RESPONSE
        ):
            raise RuntimeError("Career loop is not awaiting interview reconciliation.")
        operation_id = action.details.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id:
            raise RuntimeError("Interview reconciliation action has no valid operation_id.")
        resolution_owner = f"resolution:{uuid4().hex}"
        self._states.claim_execution(
            state.run_id,
            resolution_owner,
            expected_version=state.version,
            lease_seconds=self.EXECUTION_LEASE_SECONDS,
        )
        try:
            self._scheduling.resolve_reconciliation(
                operation_id,
                completed=completed,
                provider_event_id=provider_event_id,
            )
            state.pending_human_action = None
            state.last_error = None
            state.phase = CareerLoopPhase.INTERVIEW_ACCEPT
            state.touch()
            self._persist(state, expected_owner_id=resolution_owner)
        finally:
            try:
                self._states.release_execution(state.run_id, resolution_owner)
            except CareerLoopConflictError:
                pass
        return self._continue(state)

    def get(self, run_id: str, *, user_id: str) -> CareerLoopResult:
        """Return a durable loop snapshot without executing work."""
        state = self._owned_state(run_id, user_id)
        return self._result(state)

    def continue_run(self, run_id: str, *, user_id: str) -> CareerLoopResult:
        """Continue a persisted non-human phase after a process restart.

        This is the recovery entry point for a crash that occurred after an explicit
        approval was durably recorded but before the next phase completed.
        """
        state = self._owned_state(run_id, user_id)
        if state.pending_human_action is not None:
            raise HumanActionRequiredError(
                "Career loop is waiting for explicit human action."
            )
        if state.phase in {CareerLoopPhase.COMPLETE, CareerLoopPhase.FAILED}:
            return self._result(state)
        return self._continue(state)

    def _owned_state(self, run_id: str, user_id: str) -> CareerLoopState:
        """Load a run only for its authenticated caller before any gate or side effect."""
        caller = validate_loop_identifier(user_id, "user_id")
        identifier = validate_loop_identifier(run_id, "run_id", maximum=120)
        state = self._states.get(identifier)
        if state is None or state.request.user_id != caller:
            # Do not disclose whether another tenant owns this identifier.
            raise KeyError("Unknown autonomous career loop for this user.")
        return state

    def _continue(self, state: CareerLoopState) -> CareerLoopResult:
        execution_owner = f"worker:{uuid4().hex}"
        self._states.claim_execution(
            state.run_id,
            execution_owner,
            expected_version=state.version,
            lease_seconds=self.EXECUTION_LEASE_SECONDS,
        )
        heartbeat = _ExecutionLeaseHeartbeat(
            self._states,
            state.run_id,
            execution_owner,
            lease_seconds=self.EXECUTION_LEASE_SECONDS,
            interval_seconds=self.EXECUTION_HEARTBEAT_SECONDS,
        )
        heartbeat.start()
        segment_iterations = 0
        try:
            try:
                while state.phase not in {
                    CareerLoopPhase.COMPLETE,
                    CareerLoopPhase.FAILED,
                }:
                    if state.pending_human_action is not None:
                        break
                    if segment_iterations >= self._max_iterations:
                        if state.phase in self.POST_EFFECT_RECOVERY_PHASES:
                            state.last_error = "max_iterations_reached_recovery_pending"
                            state.touch()
                            self._persist(state, expected_owner_id=execution_owner)
                            break
                        state.last_error = "max_iterations_reached"
                        state.phase = CareerLoopPhase.FAILED
                        state.touch()
                        self._persist(state, expected_owner_id=execution_owner)
                        break
                    segment_iterations += 1
                    state.iterations += 1
                    self._step(state)
                    heartbeat.raise_if_failed()
                    state.last_error = None
                    state.touch()
                    try:
                        self._persist(state, expected_owner_id=execution_owner)
                    except CareerLoopConflictError:
                        raise
                    except Exception as exc:
                        raise _PostStepPersistenceError(
                            "Post-step snapshot persistence is temporarily unavailable."
                        ) from exc
                    heartbeat.raise_if_failed()
                    if (
                        state.phase not in {
                            CareerLoopPhase.COMPLETE,
                            CareerLoopPhase.FAILED,
                        }
                        and state.pending_human_action is None
                    ):
                        self._states.claim_execution(
                            state.run_id,
                            execution_owner,
                            expected_version=state.version,
                            lease_seconds=self.EXECUTION_LEASE_SECONDS,
                        )
            except CareerLoopConflictError:
                raise
            except _PostStepPersistenceError as exc:
                # Never convert a completed external action into a terminal loop failure
                # merely because its subsequent loop snapshot write was unavailable.
                # Reload whatever snapshot is durably visible and let a future
                # continue_run() replay the idempotent phase if necessary.
                try:
                    durable = self._states.get(state.run_id)
                except Exception:
                    durable = None
                if durable is not None:
                    state = durable
                state.last_error = (
                    f"{type(exc).__name__}: {redact_text(str(exc))}"
                )
            except _RecoverableCareerLoopError as exc:
                state.last_error = (
                    f"{type(exc).__name__}: {redact_text(str(exc))}"
                )
                state.touch()
                self._persist(state, expected_owner_id=execution_owner)
            except Exception as exc:
                state.last_error = (
                    f"{type(exc).__name__}: {redact_text(str(exc))}"
                )
                state.phase = CareerLoopPhase.FAILED
                state.touch()
                self._persist(state, expected_owner_id=execution_owner)
            return self._result(state)
        finally:
            heartbeat.stop()
            try:
                self._states.release_execution(state.run_id, execution_owner)
            except CareerLoopConflictError:
                # An expired lease may have been recovered by another worker. Never
                # clear a lease we no longer own.
                pass

    def _step(self, state: CareerLoopState) -> None:
        if state.phase == CareerLoopPhase.SEARCH:
            self._search(state)
        elif state.phase == CareerLoopPhase.DECISION:
            self._decide(state)
        elif state.phase == CareerLoopPhase.RESUME:
            self._resume_prepare(state)
        elif state.phase == CareerLoopPhase.APPLICATION_PREPARE:
            self._prepare_application(state)
        elif state.phase == CareerLoopPhase.APPLICATION_SUBMIT:
            self._submit_application(state)
        elif state.phase == CareerLoopPhase.APPLICATION_RECONCILIATION:
            raise RuntimeError(
                "Application reconciliation requires an explicit human resolution."
            )
        elif state.phase == CareerLoopPhase.MESSAGE_PREPARE:
            self._prepare_message(state)
        elif state.phase == CareerLoopPhase.MESSAGE_SEND:
            self._send_message(state)
        elif state.phase == CareerLoopPhase.MESSAGE_RECONCILIATION:
            raise RuntimeError(
                "Message reconciliation requires an explicit human resolution."
            )
        elif state.phase == CareerLoopPhase.TRACK:
            self._track(state)
        elif state.phase == CareerLoopPhase.INTERVIEW_COORDINATION:
            self._coordinate_interview(state)
        elif state.phase == CareerLoopPhase.INTERVIEW_ACCEPT:
            self._accept_interview(state)
        elif state.phase == CareerLoopPhase.INTERVIEW_RECONCILIATION:
            raise RuntimeError(
                "Interview reconciliation requires an explicit human resolution."
            )
        else:
            raise RuntimeError(f"Unsupported autonomous-loop phase: {state.phase.value}")

    def _search(self, state: CareerLoopState) -> None:
        context = self._context(
            state,
            {
                "keyword": state.request.keyword,
                "location": state.request.location,
                "page_size": 20,
            },
        )
        result = self._agents.resolve("job_search").execute(context)
        if not result.success:
            raise RuntimeError("Job discovery failed.")
        jobs = result.metadata.get("jobs", ())
        if not isinstance(jobs, (tuple, list)) or not jobs:
            raise RuntimeError("Job discovery returned no opportunities.")
        selected = jobs[0]
        job_id = getattr(selected, "job_id", None)
        title = getattr(selected, "title", None)
        company = getattr(selected, "company", None)
        company_id = getattr(company, "company_id", None)
        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("Selected job has no stable identifier.")
        state.selected_job_id = self._external_identifier(job_id, "job_id")
        state.selected_job_title = self._external_text(
            str(title or ""), "job_title", maximum=500, fallback="Unknown role"
        )
        state.selected_company = self._company_name(company)
        state.selected_company_id = (
            self._external_identifier(company_id, "company_id")
            if isinstance(company_id, str) and company_id.strip()
            else None
        )
        state.phase = CareerLoopPhase.DECISION

    def _decide(self, state: CareerLoopState) -> None:
        decision = self._decision_engine.next_action(
            "find a suitable job",
            ("job_search",),
            {"job_id": state.selected_job_id},
        )
        if decision.action != "resume":
            raise RuntimeError("Career decision engine did not advance to resume preparation.")
        state.phase = CareerLoopPhase.RESUME

    def _resume_prepare(self, state: CareerLoopState) -> None:
        result = self._agents.resolve("resume").execute(
            self._context(
                state,
                {
                    "job_id": state.selected_job_id,
                    "job_title": state.selected_job_title,
                    "company": state.selected_company,
                    "candidate_profile": state.request.candidate_profile,
                },
            )
        )
        if not result.success:
            raise RuntimeError("Resume preparation failed.")
        artifact_content = self._resume_artifact_content(result)
        state.application_artifact_content = artifact_content
        state.application_artifact_sha256 = sha256(
            artifact_content.encode("utf-8")
        ).hexdigest()
        state.phase = CareerLoopPhase.APPLICATION_PREPARE

    def _prepare_application(self, state: CareerLoopState) -> None:
        if state.selected_job_id is None:
            raise RuntimeError("Application preparation requires a selected job.")

        matches = self._applications.find(
            ApplicationQuery(
                user_id=state.request.user_id,
                job_id=state.selected_job_id,
            )
        )
        if matches:
            tracked = matches[0]
            application_id = tracked.application_id
        else:
            application_id = state.application_id or f"{state.run_id}:application"
            collision = self._applications.get(application_id)
            if collision is not None:
                raise RuntimeError(
                    "Derived application_id is already owned by another application."
                )
            application = JobApplication(
                application_id=application_id,
                user_id=state.request.user_id,
                job_id=state.selected_job_id,
                company_id=state.selected_company_id or "",
                status=JobApplicationStatus.SAVED,
                created_at=state.created_at.astimezone(timezone.utc),
                updated_at=state.created_at.astimezone(timezone.utc),
            )
            try:
                self._applications.add(application)
                tracked = application
            except ApplicationConflictError:
                matches = self._applications.find(
                    ApplicationQuery(
                        user_id=state.request.user_id,
                        job_id=state.selected_job_id,
                    )
                )
                if not matches:
                    raise
                tracked = matches[0]
                application_id = tracked.application_id

        if (
            tracked.user_id != state.request.user_id
            or tracked.job_id != state.selected_job_id
        ):
            raise RuntimeError(
                "Tracked application ownership does not match the current career loop."
            )
        if tracked.status != JobApplicationStatus.SAVED:
            raise RuntimeError(
                "A tracked application for this job has already progressed beyond draft; "
                "refusing duplicate submission."
            )
        state.application_id = application_id
        state.phase = CareerLoopPhase.APPLICATION_APPROVAL
        state.pending_human_action = HumanActionEvent(
            kind=HumanActionKind.APPROVE_APPLICATION,
            title="Approve job application submission",
            details={
                "job_id": state.selected_job_id,
                "job_title": state.selected_job_title,
                "company": state.selected_company,
                "company_id": state.selected_company_id,
                "application_id": state.application_id,
                "application_version": tracked.version,
                "application_status": tracked.status.value,
                "artifact_content": state.application_artifact_content,
                "artifact_sha256": state.application_artifact_sha256,
            },
        )

    def _submit_application(self, state: CareerLoopState) -> None:
        if state.application_id is None or state.selected_job_id is None:
            raise RuntimeError("Application submission state is incomplete.")
        approved = self._require_approved_action(
            state, HumanActionKind.APPROVE_APPLICATION
        )
        application = self._applications.get(state.application_id)
        if application is None:
            raise RuntimeError("Tracked application disappeared.")
        if not self._artifact_is_consistent(state):
            raise RuntimeError(
                "Application submission artifact is missing or corrupted."
            )
        artifact_content = state.application_artifact_content
        artifact_digest = state.application_artifact_sha256
        if not isinstance(artifact_content, str) or not isinstance(artifact_digest, str):
            raise RuntimeError("Application submission artifact is incomplete.")
        operation_id = self._application_submission_operation_id(state.application_id)
        if (
            application.status == JobApplicationStatus.APPLIED
            and operation_id in application.external_action_operation_ids
        ):
            durable_operation = self._submission.get_operation(operation_id)
            if (
                durable_operation is None
                or durable_operation.status != ExternalActionStatus.SUCCEEDED
            ):
                raise RuntimeError(
                    "Applied tracker state lacks a confirmed durable submission outcome."
                )
            replay = self._submission.submit(
                operation_id,
                job_id=state.selected_job_id,
                application_id=state.application_id,
                artifact_content=artifact_content,
                artifact_sha256=artifact_digest,
                human_approved=True,
            )
            if replay is None:
                raise RuntimeError("Confirmed application submission could not be replayed.")
            state.approved_human_action = None
            state.phase = CareerLoopPhase.MESSAGE_PREPARE
            return
        if (
            approved.details.get("application_id") != state.application_id
            or approved.details.get("job_id") != state.selected_job_id
            or approved.details.get("application_version") != application.version
            or approved.details.get("application_status") != application.status.value
            or approved.details.get("artifact_content")
            != state.application_artifact_content
            or approved.details.get("artifact_sha256")
            != state.application_artifact_sha256
            or not self._artifact_is_consistent(state)
            or application.status != JobApplicationStatus.SAVED
            or application.job_id != state.selected_job_id
        ):
            raise RuntimeError(
                "Approved application intent is stale; refusing external submission."
            )
        claimed = self._applications.claim_submission(
            state.application_id,
            operation_id,
            expected_version=application.version,
            claim_owner_id=state.run_id,
        )
        try:
            result = self._submission.submit(
                operation_id,
                job_id=state.selected_job_id,
                application_id=state.application_id,
                artifact_content=artifact_content,
                artifact_sha256=artifact_digest,
                human_approved=True,
            )
        except Exception:
            durable_operation = self._submission.get_operation(operation_id)
            if durable_operation is None or durable_operation.status in {
                ExternalActionStatus.PREPARED,
                ExternalActionStatus.FAILED,
            }:
                self._applications.release_submission(
                    state.application_id,
                    operation_id,
                    claim_owner_id=state.run_id,
                )
            raise
        if result is None:
            durable_operation = self._submission.get_operation(operation_id)
            if (
                durable_operation is not None
                and durable_operation.status == ExternalActionStatus.FAILED
            ):
                self._applications.release_submission(
                    state.application_id,
                    operation_id,
                    claim_owner_id=state.run_id,
                )
                raise _RecoverableCareerLoopError(
                    "Application submission failed before any provider effect; "
                    "the approved operation can be retried deliberately."
                )
            if (
                durable_operation is not None
                and durable_operation.status
                == ExternalActionStatus.RECONCILIATION_REQUIRED
            ):
                state.phase = CareerLoopPhase.APPLICATION_RECONCILIATION
                state.pending_human_action = HumanActionEvent(
                    kind=HumanActionKind.RECONCILE_APPLICATION_SUBMISSION,
                    title="Reconcile ambiguous application submission",
                    details={
                        "operation_id": operation_id,
                        "application_id": state.application_id,
                        "job_id": state.selected_job_id,
                        "company": state.selected_company,
                        "artifact_content": artifact_content,
                        "artifact_sha256": artifact_digest,
                        "instruction": (
                            "Verify with the application provider whether the exact "
                            "approved application was submitted before continuing."
                        ),
                    },
                )
                state.last_error = (
                    "application_submission_outcome_requires_reconciliation"
                )
                return
            raise RuntimeError("Application submission did not reach a confirmed outcome.")
        updated = claimed.transition(
            JobApplicationStatus.APPLIED,
            operation_id=operation_id,
            event_id=self._application_applied_event_id(state.application_id),
            note="Submitted by autonomous career loop after human approval.",
        )
        try:
            self._applications.complete_submission(
                updated,
                operation_id,
                expected_version=claimed.version,
                claim_owner_id=state.run_id,
            )
        except Exception as exc:
            durable_operation = self._submission.get_operation(operation_id)
            if (
                durable_operation is not None
                and durable_operation.status == ExternalActionStatus.SUCCEEDED
            ):
                raise _RecoverableCareerLoopError(
                    "Application was durably submitted, but tracker completion must be retried."
                ) from exc
            raise
        state.last_error = None
        state.approved_human_action = None
        state.phase = CareerLoopPhase.MESSAGE_PREPARE

    def _prepare_message(self, state: CareerLoopState) -> None:
        if not state.request.sender or not state.request.recipient:
            state.approved_human_action = None
            state.phase = CareerLoopPhase.TRACK
            return
        message_id = state.message_id or f"{state.run_id}:message"
        message = CommunicationMessage(
            message_id=message_id,
            thread_id=f"{state.run_id}:thread",
            sender=state.request.sender,
            recipient=state.request.recipient,
            subject=state.request.message_subject,
            body=state.request.message_body,
            direction=MessageDirection.DRAFT,
            created_at=state.created_at,
        )
        try:
            persisted = self._communication.get_persisted(message_id)
        except (sqlite3.OperationalError, TimeoutError, ConnectionError, OSError) as exc:
            raise _RecoverableCareerLoopError(
                "Message preparation storage is temporarily unavailable."
            ) from exc
        if persisted is None:
            try:
                self._communication.create_draft(message)
            except (sqlite3.OperationalError, TimeoutError, ConnectionError, OSError) as exc:
                raise _RecoverableCareerLoopError(
                    "Message draft persistence is temporarily unavailable."
                ) from exc
        elif persisted != message:
            raise RuntimeError("Persisted loop message does not match the prepared intent.")
        state.message_id = message_id
        state.phase = CareerLoopPhase.MESSAGE_APPROVAL
        state.pending_human_action = HumanActionEvent(
            kind=HumanActionKind.APPROVE_MESSAGE,
            title="Approve recruiter/employer message",
            details={
                "message_id": message_id,
                "sender": state.request.sender,
                "recipient": state.request.recipient,
                "subject": state.request.message_subject,
                "body": state.request.message_body,
            },
        )

    def _send_message(self, state: CareerLoopState) -> None:
        if state.message_id is None:
            raise RuntimeError("Message send phase has no prepared message.")
        approved = self._require_approved_action(
            state, HumanActionKind.APPROVE_MESSAGE
        )
        persisted = self._communication.get_persisted(state.message_id)
        if (
            persisted is None
            or approved.details.get("message_id") != state.message_id
            or approved.details.get("sender") != persisted.sender
            or approved.details.get("recipient") != persisted.recipient
            or approved.details.get("subject") != persisted.subject
            or approved.details.get("body") != persisted.body
            or persisted.direction
            not in {MessageDirection.DRAFT, MessageDirection.OUTBOUND}
        ):
            raise RuntimeError(
                "Approved message intent is stale; refusing external send."
            )
        operation_id = f"{state.run_id}:message-send"
        durable_before_send = self._communication.get_operation(operation_id)
        if (
            durable_before_send is not None
            and durable_before_send.status == ExternalActionStatus.FAILED
        ):
            self._communication.reopen_failed_send(operation_id, state.message_id)
        delivered = self._communication.send(
            operation_id,
            state.message_id,
            human_approved=True,
        )
        if delivered is None:
            durable_operation = self._communication.get_operation(operation_id)
            if (
                durable_operation is not None
                and durable_operation.status == ExternalActionStatus.FAILED
            ):
                raise _RecoverableCareerLoopError(
                    "Message delivery failed before any provider effect; "
                    "the approved operation can be retried deliberately."
                )
            if (
                durable_operation is not None
                and durable_operation.status
                == ExternalActionStatus.RECONCILIATION_REQUIRED
            ):
                state.phase = CareerLoopPhase.MESSAGE_RECONCILIATION
                state.pending_human_action = HumanActionEvent(
                    kind=HumanActionKind.RECONCILE_MESSAGE_DELIVERY,
                    title="Reconcile ambiguous recruiter/employer message delivery",
                    details={
                        "operation_id": operation_id,
                        "message_id": state.message_id,
                        "sender": persisted.sender,
                        "recipient": persisted.recipient,
                        "subject": persisted.subject,
                        "body": persisted.body,
                        "content_sha256": sha256(
                            (
                                persisted.sender
                                + "\n"
                                + persisted.recipient
                                + "\n"
                                + persisted.subject
                                + "\n"
                                + persisted.body
                            ).encode("utf-8")
                        ).hexdigest(),
                        "instruction": (
                            "Verify with the communication provider whether this exact "
                            "message was delivered before continuing."
                        ),
                    },
                )
                state.last_error = "message_delivery_outcome_requires_reconciliation"
                return
            raise RuntimeError("Message send did not reach a confirmed outcome.")
        state.approved_human_action = None
        state.phase = CareerLoopPhase.TRACK

    def _track(self, state: CareerLoopState) -> None:
        if state.application_id is None:
            raise RuntimeError("Tracking requires an application.")
        try:
            application = self._applications.get(state.application_id)
        except (sqlite3.OperationalError, TimeoutError, ConnectionError, OSError) as exc:
            raise _RecoverableCareerLoopError(
                "Application tracker is temporarily unavailable."
            ) from exc
        if application is None or application.status != JobApplicationStatus.APPLIED:
            raise RuntimeError("Application tracker is not in applied state.")
        if state.request.schedule_event_id:
            state.phase = CareerLoopPhase.INTERVIEW_COORDINATION
        else:
            state.phase = CareerLoopPhase.COMPLETE

    def _coordinate_interview(self, state: CareerLoopState) -> None:
        event_id = state.request.schedule_event_id
        if event_id is None:
            state.phase = CareerLoopPhase.COMPLETE
            return
        try:
            event = self._scheduling.get_event(event_id)
        except (sqlite3.OperationalError, TimeoutError, ConnectionError, OSError) as exc:
            raise _RecoverableCareerLoopError(
                "Interview scheduling storage is temporarily unavailable."
            ) from exc
        if event.candidate_id != state.request.user_id:
            raise RuntimeError("Interview event belongs to a different candidate.")
        if event.application_id is not None and event.application_id != state.application_id:
            raise RuntimeError("Interview event is linked to a different application.")
        if event.status == ScheduleStatus.ACCEPTED:
            self._record_interview_status(state, event_id)
            state.phase = CareerLoopPhase.COMPLETE
            return
        if event.status not in {
            ScheduleStatus.PROPOSED,
            ScheduleStatus.RESCHEDULE_REQUESTED,
        }:
            raise RuntimeError("Interview event is not awaiting acceptance.")
        try:
            view = self._scheduling.human_view(event_id)
        except (sqlite3.OperationalError, TimeoutError, ConnectionError, OSError) as exc:
            raise _RecoverableCareerLoopError(
                "Interview scheduling view is temporarily unavailable."
            ) from exc
        state.phase = CareerLoopPhase.INTERVIEW_APPROVAL
        state.pending_human_action = HumanActionEvent(
            kind=HumanActionKind.APPROVE_INTERVIEW,
            title="Approve interview/trial-day calendar response",
            details={
                "event_id": view.event_id,
                "employer": view.employer_name,
                "event_type": view.event_type.value,
                "location": view.location,
                "local_date": view.local_date,
                "local_start_time": view.local_start_time,
                "local_end_date": view.local_end_date,
                "local_end_time": view.local_end_time,
                "timezone": view.timezone_name,
                "utc_offset": view.utc_offset,
                "end_utc_offset": view.end_utc_offset,
                "status": view.status.value,
            },
        )

    def _accept_interview(self, state: CareerLoopState) -> None:
        event_id = state.request.schedule_event_id
        if event_id is None:
            raise RuntimeError("Interview approval has no scheduling event.")
        approved = self._require_approved_action(
            state, HumanActionKind.APPROVE_INTERVIEW
        )
        view = self._scheduling.human_view(event_id)
        current_details = {
            "event_id": view.event_id,
            "employer": view.employer_name,
            "event_type": view.event_type.value,
            "location": view.location,
            "local_date": view.local_date,
            "local_start_time": view.local_start_time,
            "local_end_date": view.local_end_date,
            "local_end_time": view.local_end_time,
            "timezone": view.timezone_name,
            "utc_offset": view.utc_offset,
            "end_utc_offset": view.end_utc_offset,
            "status": view.status.value,
        }
        approved_details = dict(approved.details)
        if view.status == ScheduleStatus.ACCEPTED:
            current_without_status = dict(current_details)
            approved_without_status = dict(approved_details)
            current_without_status.pop("status", None)
            approved_without_status.pop("status", None)
            if current_without_status != approved_without_status:
                raise RuntimeError(
                    "Approved interview intent is stale; refusing calendar response."
                )
        elif approved_details != current_details:
            raise RuntimeError(
                "Approved interview intent is stale; refusing calendar response."
            )
        if view.status == ScheduleStatus.ACCEPTED:
            self._record_interview_status(state, event_id)
            state.approved_human_action = None
            state.phase = CareerLoopPhase.COMPLETE
            return
        operation_id = f"{state.run_id}:interview-accept"
        accepted = self._scheduling.accept(
            operation_id,
            event_id,
            human_approved=True,
        )
        if accepted is None:
            durable_operation = self._scheduling.get_operation(operation_id)
            if (
                durable_operation is not None
                and durable_operation.status == ExternalActionStatus.FAILED
            ):
                raise _RecoverableCareerLoopError(
                    "Interview response failed before any provider effect; "
                    "the approved operation can be retried deliberately."
                )
            if (
                durable_operation is not None
                and durable_operation.status
                == ExternalActionStatus.RECONCILIATION_REQUIRED
            ):
                state.phase = CareerLoopPhase.INTERVIEW_RECONCILIATION
                state.pending_human_action = HumanActionEvent(
                    kind=HumanActionKind.RECONCILE_INTERVIEW_RESPONSE,
                    title="Reconcile ambiguous interview/calendar response",
                    details={
                        "operation_id": operation_id,
                        "event_id": event_id,
                        "employer": view.employer_name,
                        "local_date": view.local_date,
                        "local_start_time": view.local_start_time,
                        "timezone": view.timezone_name,
                        "instruction": (
                            "Verify with the calendar provider whether the interview "
                            "acceptance was recorded before continuing."
                        ),
                    },
                )
                state.last_error = "interview_response_outcome_requires_reconciliation"
                return
            raise RuntimeError("Interview acceptance did not reach a confirmed outcome.")
        self._record_interview_status(state, event_id)
        state.approved_human_action = None
        state.phase = CareerLoopPhase.COMPLETE

    def _record_interview_status(
        self, state: CareerLoopState, event_id: str
    ) -> None:
        """Persist an accepted interview in the application lifecycle exactly once."""
        if state.application_id is None:
            raise RuntimeError("Interview tracking requires an application.")
        try:
            application = self._applications.get(state.application_id)
        except (sqlite3.OperationalError, TimeoutError, ConnectionError, OSError) as exc:
            raise _RecoverableCareerLoopError(
                "Application tracker is temporarily unavailable after interview acceptance."
            ) from exc
        if application is None:
            raise RuntimeError("Tracked application disappeared before interview update.")
        if application.status in {
            JobApplicationStatus.INTERVIEW,
            JobApplicationStatus.OFFER,
        }:
            return
        if application.status != JobApplicationStatus.APPLIED:
            raise RuntimeError(
                "Accepted interview cannot be recorded from the current application status."
            )
        event_digest = sha256(
            f"{application.application_id}\n{event_id}".encode("utf-8")
        ).hexdigest()[:40]
        updated = application.transition(
            JobApplicationStatus.INTERVIEW,
            event_id=f"interview-{event_digest}",
            note="Interview accepted by autonomous career loop after human approval.",
        )
        try:
            self._applications.update(updated, expected_version=application.version)
        except ApplicationConflictError:
            current = self._applications.get(state.application_id)
            if current is not None and current.status in {
                JobApplicationStatus.INTERVIEW,
                JobApplicationStatus.OFFER,
            }:
                return
            raise
        except (sqlite3.OperationalError, TimeoutError, ConnectionError, OSError) as exc:
            raise _RecoverableCareerLoopError(
                "Accepted interview is durable, but tracker update must be retried."
            ) from exc

    @staticmethod
    def _require_approved_action(
        state: CareerLoopState, kind: HumanActionKind
    ) -> HumanActionEvent:
        action = state.approved_human_action
        if action is None or action.kind != kind:
            raise RuntimeError("Career loop has no matching durable human approval.")
        return action

    def _context(
        self, state: CareerLoopState, payload: dict[str, Any]
    ) -> AgentContext:
        return AgentContext(
            user_id=state.request.user_id,
            memory_snapshot=MemorySnapshot(),
            payload=payload,
            metadata={"career_loop_run_id": state.run_id},
        )

    def _persist(
        self, state: CareerLoopState, *, expected_owner_id: str | None = None
    ) -> None:
        """Persist one snapshot, fencing writes by lease owner when executing."""
        self._states.save(state, expected_owner_id=expected_owner_id)

    @staticmethod
    def _resume_artifact_content(result: Any) -> str:
        """Return the exact bounded text artifact produced for application approval."""
        metadata = getattr(result, "metadata", {})
        if not (isinstance(metadata, dict) or hasattr(metadata, "get")):
            raise RuntimeError("Resume preparation produced no artifact metadata.")
        candidate = metadata.get("application_artifact")
        artifact_type = metadata.get("application_artifact_type")
        profile_included = metadata.get("candidate_profile_included")
        if not isinstance(candidate, str) or not candidate.strip():
            raise RuntimeError("Resume preparation produced no inspectable application artifact.")
        if artifact_type != "job_application_profile":
            raise RuntimeError("Resume preparation produced an unsupported artifact type.")
        if profile_included is not True:
            raise RuntimeError(
                "Resume preparation did not include real candidate profile data."
            )
        normalized = candidate.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            raise RuntimeError("Resume preparation produced an empty application artifact.")
        if len(normalized) > 100_000:
            raise ValueError("Application artifact exceeds 100000 characters.")
        if any(ord(ch) < 32 and ch not in "\n\t" for ch in normalized):
            raise ValueError("Application artifact contains forbidden control characters.")
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in normalized):
            raise ValueError("Application artifact contains a forbidden Unicode surrogate.")
        return normalized

    @staticmethod
    def _artifact_is_consistent(state: CareerLoopState) -> bool:
        content = state.application_artifact_content
        digest = state.application_artifact_sha256
        return (
            isinstance(content, str)
            and isinstance(digest, str)
            and sha256(content.encode("utf-8")).hexdigest() == digest
        )

    @staticmethod
    def _application_submission_operation_id(application_id: str) -> str:
        """Return a stable bounded provider idempotency key for one application."""
        digest = sha256(application_id.encode("utf-8")).hexdigest()
        return f"application-submit:{digest}"

    @staticmethod
    def _application_applied_event_id(application_id: str) -> str:
        """Return a stable bounded timeline event ID for one submitted application."""
        digest = sha256(application_id.encode("utf-8")).hexdigest()
        return f"application-applied:{digest}"

    @classmethod
    def _company_name(cls, value: Any) -> str:
        name = getattr(value, "name", None)
        candidate = name if isinstance(name, str) else str(value or "")
        return cls._external_text(
            candidate, "company", maximum=200, fallback="Unknown company"
        )

    @staticmethod
    def _external_identifier(value: str, field: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{field} must be text.")
        normalized = value.strip()
        if not normalized or len(normalized) > 200:
            raise ValueError(f"{field} must be a non-empty string of at most 200 characters.")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in normalized):
            raise ValueError(f"{field} contains forbidden control characters.")
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in normalized):
            raise ValueError(f"{field} contains a forbidden Unicode surrogate.")
        return normalized

    @staticmethod
    def _external_text(
        value: str, field: str, *, maximum: int, fallback: str
    ) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{field} must be text.")
        normalized = value.strip()
        if not normalized:
            return fallback
        if len(normalized) > maximum:
            normalized = normalized[:maximum]
        if any(ord(ch) < 32 and ch not in "\n\t" for ch in normalized):
            raise ValueError(f"{field} contains forbidden control characters.")
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in normalized):
            raise ValueError(f"{field} contains a forbidden Unicode surrogate.")
        return normalized

    @staticmethod
    def _result(state: CareerLoopState) -> CareerLoopResult:
        return CareerLoopResult(
            run_id=state.run_id,
            phase=state.phase,
            completed=state.phase == CareerLoopPhase.COMPLETE,
            human_action=state.pending_human_action,
            selected_job_id=state.selected_job_id,
            application_id=state.application_id,
            message_id=state.message_id,
            iterations=state.iterations,
            error=state.last_error,
        )
