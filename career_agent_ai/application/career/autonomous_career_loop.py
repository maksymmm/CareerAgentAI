"""Bounded, restart-safe end-to-end autonomous career loop."""

from __future__ import annotations

from datetime import timezone
from typing import Any
from uuid import uuid4

from career_agent_ai.application.agents.agent_factory import AgentFactory
from career_agent_ai.application.brain.agent_context import AgentContext
from career_agent_ai.application.career.application_submission import (
    ApplicationSubmissionService,
)
from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopPhase,
    CareerLoopRequest,
    CareerLoopResult,
    CareerLoopState,
    HumanActionEvent,
    HumanActionKind,
)
from career_agent_ai.application.communication import (
    CommunicationMessage,
    CommunicationService,
    MessageDirection,
)
from career_agent_ai.application.jobs.job_application import JobApplication
from career_agent_ai.application.jobs.job_application_repository import (
    ApplicationConflictError,
    ApplicationQuery,
    JobApplicationRepository,
)
from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus
from career_agent_ai.application.memory.memory_snapshot import MemorySnapshot
from career_agent_ai.application.scheduling import ScheduleStatus, SchedulingService


class AutonomousCareerLoop:
    """Connect career discovery through interview coordination with durable gates."""

    DEFAULT_MAX_ITERATIONS = 16

    def __init__(
        self,
        *,
        agent_factory: AgentFactory,
        application_repository: JobApplicationRepository,
        submission_service: ApplicationSubmissionService,
        communication_service: CommunicationService,
        scheduling_service: SchedulingService,
        state_repository: Any,
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

    def start(
        self, request: CareerLoopRequest, *, run_id: str | None = None
    ) -> CareerLoopResult:
        """Create one durable bounded run and continue until a human gate or terminal state."""
        if not isinstance(request, CareerLoopRequest):
            raise TypeError("request must be a CareerLoopRequest.")
        identifier = (run_id or uuid4().hex).strip()
        if not identifier or len(identifier) > 200:
            raise ValueError("run_id must be a non-empty string of at most 200 characters.")
        if self._states.get(identifier) is not None:
            raise ValueError("run_id already exists.")
        state = CareerLoopState(run_id=identifier, request=request)
        self._persist(state)
        return self._continue(state)

    def resume(self, run_id: str, *, approved: bool) -> CareerLoopResult:
        """Resume one human-gated run using an explicit approve/decline decision."""
        state = self._states.get(run_id)
        if state is None:
            raise KeyError(f"Unknown autonomous career loop: {run_id!r}")
        if state.pending_human_action is None:
            raise RuntimeError("Career loop is not waiting for human action.")
        if not isinstance(approved, bool):
            raise TypeError("approved must be a boolean.")
        if not approved:
            state.last_error = f"human_declined:{state.pending_human_action.kind.value}"
            state.pending_human_action = None
            state.phase = CareerLoopPhase.FAILED
            state.touch()
            self._persist(state)
            return self._result(state)

        kind = state.pending_human_action.kind
        state.pending_human_action = None
        if kind == HumanActionKind.APPROVE_APPLICATION:
            state.phase = CareerLoopPhase.APPLICATION_SUBMIT
        elif kind == HumanActionKind.APPROVE_MESSAGE:
            state.phase = CareerLoopPhase.MESSAGE_SEND
        elif kind == HumanActionKind.APPROVE_INTERVIEW:
            state.phase = CareerLoopPhase.INTERVIEW_COORDINATION
        else:
            raise ValueError("Unsupported pending human action.")
        state.touch()
        self._persist(state)
        return self._continue(state)

    def get(self, run_id: str) -> CareerLoopResult:
        """Return a durable loop snapshot without executing work."""
        state = self._states.get(run_id)
        if state is None:
            raise KeyError(f"Unknown autonomous career loop: {run_id!r}")
        return self._result(state)

    def _continue(self, state: CareerLoopState) -> CareerLoopResult:
        segment_iterations = 0
        try:
            while state.phase not in {CareerLoopPhase.COMPLETE, CareerLoopPhase.FAILED}:
                if state.pending_human_action is not None:
                    break
                if segment_iterations >= self._max_iterations:
                    state.last_error = "max_iterations_reached"
                    state.phase = CareerLoopPhase.FAILED
                    state.touch()
                    self._persist(state)
                    break
                segment_iterations += 1
                state.iterations += 1
                self._step(state)
                state.touch()
                self._persist(state)
        except Exception as exc:
            state.last_error = f"{type(exc).__name__}: {str(exc)[:1000]}"
            state.phase = CareerLoopPhase.FAILED
            state.touch()
            self._persist(state)
        return self._result(state)

    def _step(self, state: CareerLoopState) -> None:
        if state.phase == CareerLoopPhase.SEARCH:
            self._search(state)
        elif state.phase == CareerLoopPhase.RESUME:
            self._resume_prepare(state)
        elif state.phase == CareerLoopPhase.APPLICATION_PREPARE:
            self._prepare_application(state)
        elif state.phase == CareerLoopPhase.APPLICATION_SUBMIT:
            self._submit_application(state)
        elif state.phase == CareerLoopPhase.MESSAGE_PREPARE:
            self._prepare_message(state)
        elif state.phase == CareerLoopPhase.MESSAGE_SEND:
            self._send_message(state)
        elif state.phase == CareerLoopPhase.TRACK:
            self._track(state)
        elif state.phase == CareerLoopPhase.INTERVIEW_COORDINATION:
            self._coordinate_interview(state)
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
        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("Selected job has no stable identifier.")
        state.selected_job_id = job_id.strip()
        state.selected_job_title = str(title or "").strip() or "Unknown role"
        state.selected_company = self._company_name(company)
        state.phase = CareerLoopPhase.RESUME

    def _resume_prepare(self, state: CareerLoopState) -> None:
        result = self._agents.resolve("resume").execute(
            self._context(
                state,
                {
                    "job_id": state.selected_job_id,
                    "job_title": state.selected_job_title,
                    "company": state.selected_company,
                },
            )
        )
        if not result.success:
            raise RuntimeError("Resume preparation failed.")
        state.phase = CareerLoopPhase.APPLICATION_PREPARE

    def _prepare_application(self, state: CareerLoopState) -> None:
        if state.selected_job_id is None:
            raise RuntimeError("Application preparation requires a selected job.")
        application_id = state.application_id or f"{state.run_id}:application"
        existing = self._applications.get(application_id)
        if existing is None:
            matches = self._applications.find(
                ApplicationQuery(
                    user_id=state.request.user_id,
                    job_id=state.selected_job_id,
                )
            )
            if matches:
                existing = matches[0]
                application_id = existing.application_id
            else:
                application = JobApplication(
                    application_id=application_id,
                    user_id=state.request.user_id,
                    job_id=state.selected_job_id,
                    company_id=(state.selected_company or "")[:200],
                    status=JobApplicationStatus.SAVED,
                    created_at=state.created_at.astimezone(timezone.utc),
                    updated_at=state.created_at.astimezone(timezone.utc),
                )
                try:
                    self._applications.add(application)
                except ApplicationConflictError:
                    matches = self._applications.find(
                        ApplicationQuery(
                            user_id=state.request.user_id,
                            job_id=state.selected_job_id,
                        )
                    )
                    if not matches:
                        raise
                    application_id = matches[0].application_id
        state.application_id = application_id
        state.phase = CareerLoopPhase.APPLICATION_APPROVAL
        state.pending_human_action = HumanActionEvent(
            kind=HumanActionKind.APPROVE_APPLICATION,
            title="Approve job application submission",
            details={
                "job_id": state.selected_job_id,
                "job_title": state.selected_job_title,
                "company": state.selected_company,
                "application_id": state.application_id,
            },
        )

    def _submit_application(self, state: CareerLoopState) -> None:
        if state.application_id is None or state.selected_job_id is None:
            raise RuntimeError("Application submission state is incomplete.")
        operation_id = f"{state.run_id}:application-submit"
        result = self._submission.submit(
            operation_id,
            job_id=state.selected_job_id,
            application_id=state.application_id,
            human_approved=True,
        )
        if result is None:
            raise RuntimeError("Application submission did not reach a confirmed outcome.")
        application = self._applications.get(state.application_id)
        if application is None:
            raise RuntimeError("Tracked application disappeared.")
        if application.status == JobApplicationStatus.SAVED:
            updated = application.transition(
                JobApplicationStatus.APPLIED,
                operation_id=operation_id,
                event_id=f"{state.run_id}:applied",
                note="Submitted by autonomous career loop after human approval.",
            )
            self._applications.update(updated, expected_version=application.version)
        elif application.status != JobApplicationStatus.APPLIED:
            raise RuntimeError("Tracked application is in an incompatible state.")
        state.phase = CareerLoopPhase.MESSAGE_PREPARE

    def _prepare_message(self, state: CareerLoopState) -> None:
        if not state.request.sender or not state.request.recipient:
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
        self._communication.create_draft(message)
        state.message_id = message_id
        state.phase = CareerLoopPhase.MESSAGE_APPROVAL
        state.pending_human_action = HumanActionEvent(
            kind=HumanActionKind.APPROVE_MESSAGE,
            title="Approve recruiter/employer message",
            details={
                "message_id": message_id,
                "recipient": state.request.recipient,
                "subject": state.request.message_subject,
            },
        )

    def _send_message(self, state: CareerLoopState) -> None:
        if state.message_id is None:
            raise RuntimeError("Message send phase has no prepared message.")
        delivered = self._communication.send(
            f"{state.run_id}:message-send",
            state.message_id,
            human_approved=True,
        )
        if delivered is None:
            raise RuntimeError("Message send did not reach a confirmed outcome.")
        state.phase = CareerLoopPhase.TRACK

    def _track(self, state: CareerLoopState) -> None:
        if state.application_id is None:
            raise RuntimeError("Tracking requires an application.")
        application = self._applications.get(state.application_id)
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
        event = self._scheduling.get_event(event_id)
        if event.status == ScheduleStatus.ACCEPTED:
            state.phase = CareerLoopPhase.COMPLETE
            return
        if event.status not in {
            ScheduleStatus.PROPOSED,
            ScheduleStatus.RESCHEDULE_REQUESTED,
        }:
            raise RuntimeError("Interview event is not awaiting acceptance.")

        if state.phase == CareerLoopPhase.INTERVIEW_COORDINATION and state.pending_human_action is None:
            view = self._scheduling.human_view(event_id)
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
                    "timezone": view.timezone_name,
                    "utc_offset": view.utc_offset,
                    "status": view.status.value,
                },
            )
            return

        accepted = self._scheduling.accept(
            f"{state.run_id}:interview-accept",
            event_id,
            human_approved=True,
        )
        if accepted is None:
            raise RuntimeError("Interview acceptance did not reach a confirmed outcome.")
        state.phase = CareerLoopPhase.COMPLETE

    def _context(
        self, state: CareerLoopState, payload: dict[str, Any]
    ) -> AgentContext:
        return AgentContext(
            user_id=state.request.user_id,
            memory_snapshot=MemorySnapshot(),
            payload=payload,
            metadata={"career_loop_run_id": state.run_id},
        )

    def _persist(self, state: CareerLoopState) -> None:
        self._states.save(state)

    @staticmethod
    def _company_name(value: Any) -> str:
        name = getattr(value, "name", None)
        candidate = name if isinstance(name, str) else str(value or "")
        normalized = candidate.strip()
        return normalized[:200] or "Unknown company"

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
