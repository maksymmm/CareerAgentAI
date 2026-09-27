from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest

from career_agent_ai.application.agents.agent_factory import AgentFactory
from career_agent_ai.application.agents.agent_registry import AgentRegistry
from career_agent_ai.application.agents.job_search.job_search_agent import JobSearchAgent
from career_agent_ai.application.agents.resume.resume_agent import ResumeAgent
from career_agent_ai.application.career.application_submission import (
    ApplicationSubmissionService,
    FakeApplicationSubmissionAdapter,
)
from career_agent_ai.application.career.autonomous_career_loop import AutonomousCareerLoop
from career_agent_ai.application.career.autonomous_loop_models import (
    CareerLoopPhase,
    CareerLoopRequest,
    HumanActionKind,
)
from career_agent_ai.application.communication import (
    CommunicationService,
    FakeCommunicationAdapter,
)
from career_agent_ai.application.external_actions import (
    ExternalActionOperation,
    ExternalActionService,
    ExternalActionStatus,
)
from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.in_memory_job_repository import InMemoryJobRepository
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_application import JobApplication
from career_agent_ai.application.jobs.job_application_repository import ApplicationQuery
from career_agent_ai.application.jobs.job_application_status import JobApplicationStatus
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.scheduling import (
    FakeCalendarAdapter,
    ScheduleEvent,
    ScheduleEventType,
    ScheduleStatus,
    SchedulingService,
)
from career_agent_ai.application.search.search_service import SearchService
from career_agent_ai.application.storage.sqlite_career_loop_repository import (
    SQLiteCareerLoopRepository,
)
from career_agent_ai.application.storage.sqlite_communication_repository import (
    SQLiteCommunicationRepository,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import (
    SQLiteExternalActionOperationRepository,
)
from career_agent_ai.application.storage.sqlite_job_application_repository import (
    SQLiteJobApplicationRepository,
)
from career_agent_ai.application.storage.sqlite_scheduling_repository import (
    SQLiteSchedulingRepository,
)


CREATED = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
START = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)


def build_stack(
    path: str, *, with_schedule: bool = True, plain_company: bool = False
):
    database = SQLiteDatabase(path)

    jobs = InMemoryJobRepository()
    company = "Acme Logistics" if plain_company else Company("company-1", "Acme Logistics")
    jobs.add(
        Job.create(
            job_id="job-1",
            title="Logistics Coordinator",
            company=company,
            location=Location("Germany", "Karlsruhe"),
            description="Warehouse logistics coordination",
            created_at=START,
        )
    )
    search = SearchService(jobs)
    registry = AgentRegistry()
    registry.register(JobSearchAgent(search_service=search))
    registry.register(ResumeAgent())
    factory = AgentFactory(registry, search_service=search)

    applications = SQLiteJobApplicationRepository(database)
    operations = SQLiteExternalActionOperationRepository(database)

    submission_provider = FakeApplicationSubmissionAdapter()
    submission = ApplicationSubmissionService(
        submission_provider,
        ExternalActionService(
            operations,
            ApplicationSubmissionService.action_adapter(submission_provider),
        ),
    )

    messages = SQLiteCommunicationRepository(database)
    communication_provider = FakeCommunicationAdapter()
    communication = CommunicationService(
        messages,
        communication_provider,
        ExternalActionService(
            operations,
            CommunicationService.action_adapter(communication_provider, messages),
        ),
    )

    schedules = SQLiteSchedulingRepository(database)
    calendar_provider = FakeCalendarAdapter()
    scheduling = SchedulingService(
        schedules,
        calendar_provider,
        ExternalActionService(
            operations,
            SchedulingService.action_adapter(calendar_provider, schedules),
        ),
    )
    loop = AutonomousCareerLoop(
        agent_factory=factory,
        application_repository=applications,
        submission_service=submission,
        communication_service=communication,
        scheduling_service=scheduling,
        state_repository=SQLiteCareerLoopRepository(database),
    )
    return (
        database,
        loop,
        applications,
        messages,
        scheduling,
        submission_provider,
        communication_provider,
        calendar_provider,
    )


def add_interview(scheduling: SchedulingService, application_id: str) -> None:
    scheduling.add_event(
        ScheduleEvent(
            event_id="interview-1",
            candidate_id="user-1",
            employer_name="Acme Logistics",
            event_type=ScheduleEventType.INTERVIEW,
            location="Hauptstrasse 1, Karlsruhe",
            start_at=START + timedelta(days=2),
            end_at=START + timedelta(days=2, hours=1),
            timezone_name="Europe/Berlin",
            application_id=application_id,
            created_at=CREATED,
            updated_at=CREATED,
        )
    )


def request(*, with_schedule: bool = True) -> CareerLoopRequest:
    return CareerLoopRequest(
        user_id="user-1",
        keyword="Logistics",
        location="Karlsruhe",
        sender="candidate@example.test",
        recipient="recruiter@example.test",
        message_subject="Application follow-up",
        message_body="Thank you for considering my application.",
        schedule_event_id="interview-1" if with_schedule else None,
    )


def test_end_to_end_loop_survives_restarts_and_stops_at_each_human_gate(tmp_path):
    path = str(tmp_path / "career-loop.sqlite")
    run_id = "loop-1"

    first = build_stack(path)
    database, loop, applications, _, scheduling, submission_provider, _, _ = first
    add_interview(scheduling, f"{run_id}:application")

    started = loop.start(request(), run_id=run_id)

    assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL
    assert started.human_action is not None
    assert started.human_action.kind == HumanActionKind.APPROVE_APPLICATION
    assert started.human_action.details["job_id"] == "job-1"
    assert started.human_action.details["artifact_content"] == "Resume Agent executed."
    assert started.human_action.details["artifact_sha256"] == sha256(
        b"Resume Agent executed."
    ).hexdigest()
    assert submission_provider.calls == []
    tracked = applications.find(ApplicationQuery(user_id="user-1", job_id="job-1"))
    assert len(tracked) == 1
    assert tracked[0].status == JobApplicationStatus.SAVED
    assert applications.find(ApplicationQuery(company_id="company-1")) == tracked
    database.close()

    second = build_stack(path)
    database, loop, applications, _, _, submission_provider, communication_provider, _ = second
    after_application = loop.resume(run_id, approved=True)

    assert after_application.phase == CareerLoopPhase.MESSAGE_APPROVAL
    assert after_application.human_action is not None
    assert after_application.human_action.kind == HumanActionKind.APPROVE_MESSAGE
    assert after_application.human_action.details["sender"] == "candidate@example.test"
    assert after_application.human_action.details["recipient"] == "recruiter@example.test"
    assert after_application.human_action.details["subject"] == "Application follow-up"
    assert (
        after_application.human_action.details["body"]
        == "Thank you for considering my application."
    )
    assert len(submission_provider.calls) == 1
    tracked = applications.find(ApplicationQuery(user_id="user-1", job_id="job-1"))
    assert len(tracked) == 1
    assert tracked[0].status == JobApplicationStatus.APPLIED
    assert communication_provider.calls == [
        ("draft", f"{run_id}:message")
    ]
    database.close()

    third = build_stack(path)
    database, loop, applications, messages, _, submission_provider, communication_provider, _ = third
    after_message = loop.resume(run_id, approved=True)

    assert after_message.phase == CareerLoopPhase.INTERVIEW_APPROVAL
    assert after_message.human_action is not None
    assert after_message.human_action.kind == HumanActionKind.APPROVE_INTERVIEW
    details = after_message.human_action.details
    assert details["employer"] == "Acme Logistics"
    assert details["location"] == "Hauptstrasse 1, Karlsruhe"
    assert details["local_date"] == "2026-11-12"
    assert details["local_start_time"] == "10:00:00"
    assert details["timezone"] == "Europe/Berlin"
    assert submission_provider.calls == []
    assert [call for call in communication_provider.calls if call[0] == "send"] == [
        ("send", f"{run_id}:message-send")
    ]
    assert messages.get(f"{run_id}:message").direction.value == "outbound"
    assert len(applications.find(ApplicationQuery(user_id="user-1", job_id="job-1"))) == 1
    database.close()

    fourth = build_stack(path)
    database, loop, applications, messages, scheduling, submission_provider, communication_provider, calendar_provider = fourth
    completed = loop.resume(run_id, approved=True)

    assert completed.completed is True
    assert completed.phase == CareerLoopPhase.COMPLETE
    assert calendar_provider.calls == [
        ("accept", f"{run_id}:interview-accept")
    ]
    assert scheduling.get_event("interview-1").status == ScheduleStatus.ACCEPTED
    assert submission_provider.calls == []
    assert communication_provider.calls == []
    assert len(applications.find(ApplicationQuery(user_id="user-1", job_id="job-1"))) == 1
    assert messages.get(f"{run_id}:message").direction.value == "outbound"

    snapshot = loop.get(run_id)
    assert snapshot.completed is True
    assert snapshot.human_action is None
    database.close()


def test_loop_without_message_or_interview_completes_after_application_approval(tmp_path):
    path = str(tmp_path / "minimal-loop.sqlite")
    database, loop, applications, _, _, submission_provider, _, _ = build_stack(
        path, with_schedule=False
    )
    minimal = CareerLoopRequest(
        user_id="user-1",
        keyword="Logistics",
        location="Karlsruhe",
    )

    started = loop.start(minimal, run_id="minimal")
    assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL

    completed = loop.resume("minimal", approved=True)

    assert completed.completed is True
    assert submission_provider.calls == [
        (
            f"application-submit:{sha256('minimal:application'.encode('utf-8')).hexdigest()}",
            "job-1",
            "minimal:application",
        )
    ]
    tracked = applications.get("minimal:application")
    assert tracked is not None and tracked.status == JobApplicationStatus.APPLIED
    database.close()


def test_human_decline_stops_without_consequential_external_action(tmp_path):
    path = str(tmp_path / "decline-loop.sqlite")
    database, loop, applications, _, _, submission_provider, communication_provider, calendar_provider = build_stack(path)

    started = loop.start(request(with_schedule=False), run_id="declined")
    assert started.human_action is not None

    stopped = loop.resume("declined", approved=False)

    assert stopped.phase == CareerLoopPhase.FAILED
    assert stopped.error == "human_declined:approve_application"
    assert submission_provider.calls == []
    assert communication_provider.calls == []
    assert calendar_provider.calls == []
    assert applications.get("declined:application").status == JobApplicationStatus.SAVED
    database.close()


def test_loop_is_globally_bounded_across_resumes(tmp_path):
    path = str(tmp_path / "bounded-loop.sqlite")
    database = SQLiteDatabase(path)
    jobs = InMemoryJobRepository()
    jobs.add(
        Job.create(
            job_id="job-1",
            title="Logistics",
            company=Company("company-1", "Acme"),
            location=Location("Germany", "Karlsruhe"),
            created_at=START,
        )
    )
    search = SearchService(jobs)
    registry = AgentRegistry()
    registry.register(JobSearchAgent(search_service=search))
    registry.register(ResumeAgent())
    factory = AgentFactory(registry, search_service=search)
    applications = SQLiteJobApplicationRepository(database)
    operations = SQLiteExternalActionOperationRepository(database)
    submission_provider = FakeApplicationSubmissionAdapter()
    submission = ApplicationSubmissionService(
        submission_provider,
        ExternalActionService(
            operations,
            ApplicationSubmissionService.action_adapter(submission_provider),
        ),
    )
    message_repo = SQLiteCommunicationRepository(database)
    message_provider = FakeCommunicationAdapter()
    communication = CommunicationService(
        message_repo,
        message_provider,
        ExternalActionService(
            operations,
            CommunicationService.action_adapter(message_provider, message_repo),
        ),
    )
    schedule_repo = SQLiteSchedulingRepository(database)
    calendar_provider = FakeCalendarAdapter()
    scheduling = SchedulingService(
        schedule_repo,
        calendar_provider,
        ExternalActionService(
            operations,
            SchedulingService.action_adapter(calendar_provider, schedule_repo),
        ),
    )
    loop = AutonomousCareerLoop(
        agent_factory=factory,
        application_repository=applications,
        submission_service=submission,
        communication_service=communication,
        scheduling_service=scheduling,
        state_repository=SQLiteCareerLoopRepository(database),
        max_iterations=2,
    )

    result = loop.start(
        CareerLoopRequest(user_id="user-1", keyword="Logistics"),
        run_id="bounded",
    )

    assert result.phase == CareerLoopPhase.FAILED
    assert result.error == "max_iterations_reached"
    assert result.iterations == 2
    assert submission_provider.calls == []
    database.close()


def test_restart_before_message_phase_does_not_duplicate_application(tmp_path):
    path = str(tmp_path / "duplicate-protection.sqlite")
    run_id = "restart-safe"
    first = build_stack(path, with_schedule=False)
    database, loop, applications, _, _, _, _, _ = first

    minimal_request = CareerLoopRequest(
        user_id="user-1",
        keyword="Logistics",
        location="Karlsruhe",
    )
    started = loop.start(minimal_request, run_id=run_id)
    assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL
    assert len(applications.find(ApplicationQuery(user_id="user-1", job_id="job-1"))) == 1
    database.close()

    second = build_stack(path, with_schedule=False)
    database, loop, applications, _, _, submission_provider, _, _ = second
    completed = loop.resume(run_id, approved=True)

    assert completed.completed is True
    assert len(applications.find(ApplicationQuery(user_id="user-1", job_id="job-1"))) == 1
    assert len(submission_provider.calls) == 1
    database.close()



def test_continue_run_recovers_approved_phase_after_process_restart(tmp_path):
    path = str(tmp_path / "approved-crash.sqlite")
    run_id = "approved-crash"

    first = build_stack(path, with_schedule=False)
    database, loop, _, _, _, _, _, _ = first
    started = loop.start(
        CareerLoopRequest(user_id="user-1", keyword="Logistics"),
        run_id=run_id,
    )
    assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL

    states = SQLiteCareerLoopRepository(database)
    state = states.get(run_id)
    assert state is not None and state.pending_human_action is not None
    state.approved_human_action = state.pending_human_action
    state.pending_human_action = None
    state.phase = CareerLoopPhase.APPLICATION_SUBMIT
    state.touch()
    states.save(state)
    database.close()

    second = build_stack(path, with_schedule=False)
    database, loop, applications, _, _, submission_provider, _, _ = second
    recovered = loop.continue_run(run_id)

    assert recovered.completed is True
    assert submission_provider.calls == [
        (
            f"application-submit:{sha256(f'{run_id}:application'.encode('utf-8')).hexdigest()}",
            "job-1",
            f"{run_id}:application",
        )
    ]
    tracked = applications.get(f"{run_id}:application")
    assert tracked is not None and tracked.status == JobApplicationStatus.APPLIED
    database.close()




def test_post_action_crash_after_application_success_recovers_without_resubmit(tmp_path):
    path = str(tmp_path / "post-submit-crash.sqlite")
    run_id = "post-submit-crash"
    database, loop, applications, _, _, submission_provider, _, _ = build_stack(
        path, with_schedule=False
    )
    started = loop.start(
        CareerLoopRequest(user_id="user-1", keyword="Logistics"),
        run_id=run_id,
    )
    assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL

    states = SQLiteCareerLoopRepository(database)
    state = states.get(run_id)
    assert state is not None and state.pending_human_action is not None
    state.approved_human_action = state.pending_human_action
    state.pending_human_action = None
    state.phase = CareerLoopPhase.APPLICATION_SUBMIT
    states.save(state)

    application = applications.get(f"{run_id}:application")
    assert application is not None
    operation_id = loop._application_submission_operation_id(application.application_id)
    operations = SQLiteExternalActionOperationRepository(database)
    operations.create(
        ExternalActionOperation(
            operation_id=operation_id,
            action_type="application.submit",
            payload={"job_id": "job-1", "application_id": application.application_id},
        )
    )
    operations.transition(
        operation_id,
        ExternalActionStatus.PREPARED,
        ExternalActionStatus.IN_PROGRESS,
    )
    operations.transition(
        operation_id,
        ExternalActionStatus.IN_PROGRESS,
        ExternalActionStatus.SUCCEEDED,
        result={
            "job_id": "job-1",
            "application_id": application.application_id,
            "provider_submission_id": "already-sent",
        },
    )
    applied = application.transition(
        JobApplicationStatus.APPLIED,
        operation_id=operation_id,
        event_id=loop._application_applied_event_id(application.application_id),
    )
    applications.update(applied, expected_version=application.version)
    database.close()

    restarted = build_stack(path, with_schedule=False)
    database, loop, applications, _, _, submission_provider, _, _ = restarted
    recovered = loop.continue_run(run_id)

    assert recovered.completed is True
    assert submission_provider.calls == []
    assert applications.get(f"{run_id}:application").status == JobApplicationStatus.APPLIED
    database.close()


def test_post_action_crash_after_message_send_replays_without_duplicate(tmp_path):
    path = str(tmp_path / "post-message-crash.sqlite")
    run_id = "post-message-crash"
    first = build_stack(path, with_schedule=False)
    database, loop, _, messages, _, _, communication_provider, _ = first
    started = loop.start(request(with_schedule=False), run_id=run_id)
    assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL
    message_gate = loop.resume(run_id, approved=True)
    assert message_gate.phase == CareerLoopPhase.MESSAGE_APPROVAL

    states = SQLiteCareerLoopRepository(database)
    state = states.get(run_id)
    assert state is not None and state.pending_human_action is not None
    state.approved_human_action = state.pending_human_action
    state.pending_human_action = None
    state.phase = CareerLoopPhase.MESSAGE_SEND
    states.save(state)

    operations = SQLiteExternalActionOperationRepository(database)
    communication = CommunicationService(
        messages,
        communication_provider,
        ExternalActionService(
            operations,
            CommunicationService.action_adapter(communication_provider, messages),
        ),
    )
    delivered = communication.send(
        f"{run_id}:message-send",
        f"{run_id}:message",
        human_approved=True,
    )
    assert delivered is not None
    assert communication_provider.calls.count(("send", f"{run_id}:message-send")) == 1
    database.close()

    second = build_stack(path, with_schedule=False)
    database, loop, _, messages, _, _, restarted_provider, _ = second
    recovered = loop.continue_run(run_id)

    assert recovered.completed is True
    assert restarted_provider.calls == []
    assert messages.get(f"{run_id}:message").direction.value == "outbound"
    database.close()


def test_post_action_crash_after_interview_accept_replays_without_duplicate(tmp_path):
    path = str(tmp_path / "post-interview-crash.sqlite")
    run_id = "post-interview-crash"
    first = build_stack(path)
    database, loop, _, _, scheduling, _, _, _ = first
    add_interview(scheduling, f"{run_id}:application")
    assert loop.start(request(), run_id=run_id).phase == CareerLoopPhase.APPLICATION_APPROVAL
    assert loop.resume(run_id, approved=True).phase == CareerLoopPhase.MESSAGE_APPROVAL
    gate = loop.resume(run_id, approved=True)
    assert gate.phase == CareerLoopPhase.INTERVIEW_APPROVAL

    states = SQLiteCareerLoopRepository(database)
    state = states.get(run_id)
    assert state is not None and state.pending_human_action is not None
    state.approved_human_action = state.pending_human_action
    state.pending_human_action = None
    state.phase = CareerLoopPhase.INTERVIEW_ACCEPT
    states.save(state)

    accepted = scheduling.accept(
        f"{run_id}:interview-accept",
        "interview-1",
        human_approved=True,
    )
    assert accepted is not None and accepted.status == ScheduleStatus.ACCEPTED
    database.close()

    second = build_stack(path)
    database, loop, _, _, scheduling, _, _, calendar_provider = second
    recovered = loop.continue_run(run_id)

    assert recovered.completed is True
    assert calendar_provider.calls == []
    assert scheduling.get_event("interview-1").status == ScheduleStatus.ACCEPTED
    database.close()


def test_unlinked_interview_for_another_candidate_is_rejected(tmp_path):
    path = str(tmp_path / "foreign-interview.sqlite")
    run_id = "foreign-interview"
    database, loop, _, _, scheduling, _, _, calendar_provider = build_stack(path)
    scheduling.add_event(
        ScheduleEvent(
            event_id="interview-1",
            candidate_id="different-user",
            employer_name="Acme Logistics",
            event_type=ScheduleEventType.INTERVIEW,
            location="Office",
            start_at=START + timedelta(days=2),
            end_at=START + timedelta(days=2, hours=1),
            timezone_name="Europe/Berlin",
            application_id=None,
            created_at=CREATED,
            updated_at=CREATED,
        )
    )
    req = CareerLoopRequest(
        user_id="user-1",
        keyword="Logistics",
        schedule_event_id="interview-1",
    )
    assert loop.start(req, run_id=run_id).phase == CareerLoopPhase.APPLICATION_APPROVAL

    result = loop.resume(run_id, approved=True)

    assert result.phase == CareerLoopPhase.FAILED
    assert "different candidate" in (result.error or "")
    assert calendar_provider.calls == []
    database.close()


def test_plain_string_company_uses_empty_aggregate_company_id(tmp_path):
    path = str(tmp_path / "plain-company.sqlite")
    database, loop, applications, _, _, submission_provider, _, _ = build_stack(
        path, with_schedule=False, plain_company=True
    )

    started = loop.start(
        CareerLoopRequest(user_id="user-1", keyword="Logistics"),
        run_id="plain-company",
    )

    assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL
    tracked = applications.find(
        ApplicationQuery(user_id="user-1", job_id="job-1")
    )
    assert len(tracked) == 1
    assert tracked[0].company_id == ""
    assert started.human_action is not None
    assert started.human_action.details["company"] == "Acme Logistics"
    assert submission_provider.calls == []
    database.close()


def test_application_artifact_survives_restart_and_is_bound_to_approval(tmp_path):
    path = str(tmp_path / "artifact-restart.sqlite")
    run_id = "artifact-restart"
    first = build_stack(path, with_schedule=False)
    database, loop, _, _, _, submission_provider, _, _ = first

    started = loop.start(
        CareerLoopRequest(user_id="user-1", keyword="Logistics"),
        run_id=run_id,
    )
    assert started.human_action is not None
    artifact = started.human_action.details["artifact_content"]
    digest = started.human_action.details["artifact_sha256"]
    assert digest == sha256(artifact.encode("utf-8")).hexdigest()
    assert submission_provider.calls == []
    database.close()

    second = build_stack(path, with_schedule=False)
    database, loop, _, _, _, submission_provider, _, _ = second
    restored = loop.get(run_id)

    assert restored.human_action is not None
    assert restored.human_action.details["artifact_content"] == artifact
    assert restored.human_action.details["artifact_sha256"] == digest

    completed = loop.resume(run_id, approved=True)
    assert completed.completed is True
    assert len(submission_provider.calls) == 1
    database.close()


def test_linked_interview_for_another_candidate_is_rejected(tmp_path):
    path = str(tmp_path / "foreign-linked-interview.sqlite")
    run_id = "foreign-linked-interview"
    database, loop, _, _, scheduling, _, _, calendar_provider = build_stack(path)
    scheduling.add_event(
        ScheduleEvent(
            event_id="interview-1",
            candidate_id="different-user",
            employer_name="Acme Logistics",
            event_type=ScheduleEventType.INTERVIEW,
            location="Office",
            start_at=START + timedelta(days=2),
            end_at=START + timedelta(days=2, hours=1),
            timezone_name="Europe/Berlin",
            application_id=f"{run_id}:application",
            created_at=CREATED,
            updated_at=CREATED,
        )
    )

    started = loop.start(request(), run_id=run_id)
    assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL
    message_gate = loop.resume(run_id, approved=True)
    assert message_gate.phase == CareerLoopPhase.MESSAGE_APPROVAL

    result = loop.resume(run_id, approved=True)

    assert result.phase == CareerLoopPhase.FAILED
    assert "different candidate" in (result.error or "")
    assert calendar_provider.calls == []
    database.close()

def test_application_approval_becomes_stale_before_external_submission(tmp_path):
    path = str(tmp_path / "stale-application-approval.sqlite")
    database, loop, applications, _, _, submission_provider, _, _ = build_stack(
        path, with_schedule=False
    )
    started = loop.start(
        CareerLoopRequest(user_id="user-1", keyword="Logistics"),
        run_id="stale-app",
    )
    assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL

    application = applications.get("stale-app:application")
    assert application is not None
    withdrawn = application.transition(JobApplicationStatus.WITHDRAWN)
    applications.update(withdrawn, expected_version=application.version)

    result = loop.resume("stale-app", approved=True)

    assert result.phase == CareerLoopPhase.FAILED
    assert "Approved application intent is stale" in (result.error or "")
    assert submission_provider.calls == []
    database.close()


def test_interview_approval_is_bound_to_the_exact_displayed_slot(tmp_path):
    path = str(tmp_path / "stale-interview-approval.sqlite")
    run_id = "stale-interview"

    first = build_stack(path)
    database, loop, _, _, scheduling, _, _, _ = first
    add_interview(scheduling, f"{run_id}:application")
    started = loop.start(request(), run_id=run_id)
    assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL
    database.close()

    second = build_stack(path)
    database, loop, _, _, _, _, _, _ = second
    message_gate = loop.resume(run_id, approved=True)
    assert message_gate.phase == CareerLoopPhase.MESSAGE_APPROVAL
    database.close()

    third = build_stack(path)
    database, loop, _, _, scheduling, _, _, calendar_provider = third
    interview_gate = loop.resume(run_id, approved=True)
    assert interview_gate.phase == CareerLoopPhase.INTERVIEW_APPROVAL
    original_start = scheduling.get_event("interview-1").start_at
    moved_start = original_start + timedelta(days=1)
    changed = scheduling.reschedule(
        "external-reschedule",
        "interview-1",
        start_at=moved_start,
        end_at=moved_start + timedelta(hours=1),
        timezone_name="Europe/Berlin",
        human_approved=True,
    )
    assert changed is not None
    provider_calls_before = list(calendar_provider.calls)

    result = loop.resume(run_id, approved=True)

    assert result.phase == CareerLoopPhase.FAILED
    assert "Approved interview intent is stale" in (result.error or "")
    assert calendar_provider.calls == provider_calls_before
    assert all(call[1] != f"{run_id}:interview-accept" for call in calendar_provider.calls)
    database.close()

def test_new_run_cannot_resubmit_a_job_that_is_already_applied(tmp_path):
    path = str(tmp_path / "cross-run-duplicate.sqlite")

    first = build_stack(path, with_schedule=False)
    database, loop, applications, _, _, first_submission, _, _ = first
    initial = loop.start(
        CareerLoopRequest(user_id="user-1", keyword="Logistics"),
        run_id="first-run",
    )
    assert initial.phase == CareerLoopPhase.APPLICATION_APPROVAL
    completed = loop.resume("first-run", approved=True)
    assert completed.completed is True
    assert len(first_submission.calls) == 1
    database.close()

    second = build_stack(path, with_schedule=False)
    database, loop, applications, _, _, second_submission, _, _ = second
    duplicate = loop.start(
        CareerLoopRequest(user_id="user-1", keyword="Logistics"),
        run_id="second-run",
    )

    assert duplicate.phase == CareerLoopPhase.FAILED
    assert "refusing duplicate submission" in (duplicate.error or "")
    assert second_submission.calls == []
    matches = applications.find(ApplicationQuery(user_id="user-1", job_id="job-1"))
    assert len(matches) == 1
    assert matches[0].status == JobApplicationStatus.APPLIED
    database.close()


@pytest.mark.parametrize("bad_run_id", ["has space", "../path", "\ud800bad"])
def test_start_rejects_run_ids_that_cannot_derive_safe_operation_ids(tmp_path, bad_run_id):
    path = str(tmp_path / "bad-run-id.sqlite")
    database, loop, _, _, _, _, _, _ = build_stack(path, with_schedule=False)

    with pytest.raises(ValueError):
        loop.start(
            CareerLoopRequest(user_id="user-1", keyword="Logistics"),
            run_id=bad_run_id,
        )

    database.close()



def test_derived_application_id_collision_cannot_cross_user_ownership(tmp_path):
    path = str(tmp_path / "application-ownership.sqlite")
    database, loop, applications, _, _, submission_provider, _, _ = build_stack(
        path, with_schedule=False
    )
    applications.add(
        JobApplication(
            application_id="collision:application",
            user_id="other-user",
            job_id="job-1",
            company_id="company-1",
            status=JobApplicationStatus.SAVED,
            created_at=CREATED,
            updated_at=CREATED,
        )
    )

    result = loop.start(
        CareerLoopRequest(user_id="user-1", keyword="Logistics"),
        run_id="collision",
    )

    assert result.phase == CareerLoopPhase.FAILED
    assert "already owned" in (result.error or "")
    assert submission_provider.calls == []
    assert applications.get("collision:application").user_id == "other-user"
    database.close()


def test_long_existing_application_id_uses_bounded_stable_submission_key(tmp_path):
    path = str(tmp_path / "long-application-id.sqlite")
    database, loop, applications, _, _, submission_provider, _, _ = build_stack(
        path, with_schedule=False
    )
    application_id = "a" * 200
    applications.add(
        JobApplication(
            application_id=application_id,
            user_id="user-1",
            job_id="job-1",
            company_id="company-1",
            status=JobApplicationStatus.SAVED,
            created_at=CREATED,
            updated_at=CREATED,
        )
    )

    started = loop.start(
        CareerLoopRequest(user_id="user-1", keyword="Logistics"),
        run_id="long-id",
    )
    assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL
    assert started.application_id == application_id

    completed = loop.resume("long-id", approved=True)

    assert completed.completed is True
    assert len(submission_provider.calls) == 1
    operation_id, job_id, submitted_application_id = submission_provider.calls[0]
    assert operation_id == (
        f"application-submit:{sha256(application_id.encode('utf-8')).hexdigest()}"
    )
    assert len(operation_id) <= 200
    assert job_id == "job-1"
    assert submitted_application_id == application_id
    database.close()

def test_repository_rejects_payload_run_id_mismatch(tmp_path):
    path = str(tmp_path / "mismatched-run.sqlite")
    database = SQLiteDatabase(path)
    repository = SQLiteCareerLoopRepository(database)
    state = repository._deserialize(
        {
            "serialization_version": 1,
            "run_id": "payload-run",
            "request": {
                "user_id": "user-1",
                "keyword": "Logistics",
                "location": "",
                "sender": "",
                "recipient": "",
                "message_subject": "Application follow-up",
                "message_body": "I am interested in this opportunity.",
                "schedule_event_id": None,
            },
            "phase": "search",
            "selected_job_id": None,
            "selected_job_title": None,
            "selected_company": None,
            "application_id": None,
            "message_id": None,
            "iterations": 0,
            "pending_human_action": None,
            "last_error": None,
            "created_at": CREATED.isoformat(),
            "updated_at": CREATED.isoformat(),
        }
    )
    repository.save(state)
    database.connection.execute(
        "UPDATE autonomous_career_loops SET run_id = ? WHERE run_id = ?",
        ("storage-run", "payload-run"),
    )
    database.connection.commit()

    with pytest.raises(ValueError, match="storage key"):
        repository.get("storage-run")

    database.close()

def test_repository_rejects_malformed_persisted_loop_json(tmp_path):
    path = str(tmp_path / "malformed-loop.sqlite")
    database = SQLiteDatabase(path)
    repository = SQLiteCareerLoopRepository(database)
    database.connection.execute(
        "INSERT INTO autonomous_career_loops(run_id, payload_json, updated_at) VALUES (?, ?, ?)",
        ("bad", "{not-json", START.isoformat()),
    )
    database.connection.commit()

    with pytest.raises(ValueError, match="malformed"):
        repository.get("bad")

    database.close()
