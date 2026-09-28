from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from time import sleep
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
    CareerLoopState,
    HumanActionKind,
)
from career_agent_ai.application.career.autonomous_loop_repository import (
    CareerLoopConflictError,
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
PROFILE = (
    "Logistics professional with warehouse picking, packing, inventory control, "
    "new-employee onboarding, and daily workflow coordination experience."
)


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
        candidate_profile=PROFILE,
        location="Karlsruhe",
        sender="candidate@example.test",
        recipient="recruiter@example.test",
        message_subject="Application follow-up",
        message_body="Thank you for considering my application.",
        schedule_event_id="interview-1" if with_schedule else None,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"sender": "candidate@example.test", "recipient": ""},
        {"sender": "", "recipient": "recruiter@example.test"},
        {
            "sender": "candidate@example.test",
            "recipient": "recruiter@example.test",
            "message_subject": "",
        },
        {
            "sender": "candidate@example.test",
            "recipient": "recruiter@example.test",
            "message_body": "",
        },
        {
            "sender": "candidate name@example.test",
            "recipient": "recruiter@example.test",
        },
        {
            "sender": "candidate@example.test",
            "recipient": "r" * 201,
        },
    ],
)
def test_request_rejects_incomplete_enabled_messaging_before_run(changes):
    values = {
        "user_id": "user-1",
        "keyword": "Logistics",
        "candidate_profile": PROFILE,
    }
    values.update(changes)

    with pytest.raises(ValueError):
        CareerLoopRequest(**values)



def test_request_requires_real_candidate_profile_before_run():
    with pytest.raises(ValueError, match="candidate_profile"):
        CareerLoopRequest(
            user_id="user-1",
            keyword="Logistics",
            candidate_profile="",
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
    assert started.human_action.details["artifact_content"] == (
        "Candidate: user-1\n"
        "Target role: Logistics Coordinator\n"
        "Company: Acme Logistics\n"
        "Job ID: job-1\n\n"
        f"Candidate profile:\n{PROFILE}"
    )
    assert started.human_action.details["artifact_sha256"] == sha256(
        started.human_action.details["artifact_content"].encode("utf-8")
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
    submit_operation_id = submission_provider.calls[0][0]
    submitted_content, submitted_digest = submission_provider.artifacts[
        submit_operation_id
    ]
    assert submitted_content == started.human_action.details["artifact_content"]
    assert submitted_digest == started.human_action.details["artifact_sha256"]
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
        candidate_profile=PROFILE,
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
        CareerLoopRequest(user_id="user-1", keyword="Logistics", candidate_profile=PROFILE),
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
        candidate_profile=PROFILE,
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
        CareerLoopRequest(user_id="user-1", keyword="Logistics", candidate_profile=PROFILE),
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






def test_submission_provider_extras_are_not_persisted_in_external_action_result():
    provider = FakeApplicationSubmissionAdapter()

    class ExtraResultProvider(FakeApplicationSubmissionAdapter):
        def submit(
            self,
            operation_id,
            job_id,
            application_id,
            artifact_content,
            artifact_sha256,
        ):
            result = dict(
                super().submit(
                    operation_id,
                    job_id,
                    application_id,
                    artifact_content,
                    artifact_sha256,
                )
            )
            result["provider_debug_timestamp"] = CREATED
            return result

    provider = ExtraResultProvider()
    database = SQLiteDatabase()
    operations = SQLiteExternalActionOperationRepository(database)
    submission = ApplicationSubmissionService(
        provider,
        ExternalActionService(
            operations,
            ApplicationSubmissionService.action_adapter(provider),
        ),
    )
    artifact = "Candidate profile content"
    digest = sha256(artifact.encode("utf-8")).hexdigest()

    result = submission.submit(
        "submission:normalized",
        job_id="job-1",
        application_id="application-1",
        artifact_content=artifact,
        artifact_sha256=digest,
        human_approved=True,
    )

    assert result == {
        "job_id": "job-1",
        "application_id": "application-1",
        "provider_submission_id": "fake:application-1",
        "artifact_sha256": digest,
    }
    durable = operations.get("submission:normalized")
    assert durable is not None
    assert durable.status == ExternalActionStatus.SUCCEEDED
    assert "provider_debug_timestamp" not in durable.result
    database.close()

def test_tracker_completion_failure_after_durable_submit_remains_recoverable(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "tracker-reconcile.sqlite")
    run_id = "tracker-reconcile"
    database, loop, applications, _, _, submission_provider, _, _ = build_stack(
        path, with_schedule=False
    )
    started = loop.start(
        CareerLoopRequest(user_id="user-1", keyword="Logistics", candidate_profile=PROFILE),
        run_id=run_id,
    )
    assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL

    original_complete = applications.complete_submission
    attempts = {"count": 0}

    def fail_once(application, operation_id, *, expected_version):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("temporary tracker write failure")
        return original_complete(
            application,
            operation_id,
            expected_version=expected_version,
        )

    monkeypatch.setattr(applications, "complete_submission", fail_once)

    partial = loop.resume(run_id, approved=True)

    assert partial.phase == CareerLoopPhase.APPLICATION_SUBMIT
    assert partial.completed is False
    assert "must be retried" in (partial.error or "")
    assert len(submission_provider.calls) == 1
    tracked = applications.get(f"{run_id}:application")
    assert tracked is not None
    assert tracked.status == JobApplicationStatus.SAVED
    database.close()

    database, restarted_loop, applications, _, _, restarted_provider, _, _ = build_stack(
        path, with_schedule=False
    )
    recovered = restarted_loop.continue_run(run_id)

    assert recovered.completed is True
    assert restarted_provider.calls == []
    tracked = applications.get(f"{run_id}:application")
    assert tracked is not None
    assert tracked.status == JobApplicationStatus.APPLIED
    database.close()

def test_post_action_crash_after_application_success_recovers_without_resubmit(tmp_path):
    path = str(tmp_path / "post-submit-crash.sqlite")
    run_id = "post-submit-crash"
    database, loop, applications, _, _, submission_provider, _, _ = build_stack(
        path, with_schedule=False
    )
    started = loop.start(
        CareerLoopRequest(user_id="user-1", keyword="Logistics", candidate_profile=PROFILE),
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
            payload={
                "job_id": "job-1",
                "application_id": application.application_id,
                "artifact_content": state.application_artifact_content,
                "artifact_sha256": state.application_artifact_sha256,
            },
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
            "artifact_sha256": state.application_artifact_sha256,
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
        candidate_profile=PROFILE,
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
        CareerLoopRequest(user_id="user-1", keyword="Logistics", candidate_profile=PROFILE),
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
        CareerLoopRequest(user_id="user-1", keyword="Logistics", candidate_profile=PROFILE),
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
        CareerLoopRequest(user_id="user-1", keyword="Logistics", candidate_profile=PROFILE),
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
        CareerLoopRequest(user_id="user-1", keyword="Logistics", candidate_profile=PROFILE),
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
        CareerLoopRequest(user_id="user-1", keyword="Logistics", candidate_profile=PROFILE),
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
            CareerLoopRequest(user_id="user-1", keyword="Logistics", candidate_profile=PROFILE),
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
        CareerLoopRequest(user_id="user-1", keyword="Logistics", candidate_profile=PROFILE),
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
        CareerLoopRequest(user_id="user-1", keyword="Logistics", candidate_profile=PROFILE),
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



def test_loop_execution_lease_blocks_concurrent_recovered_worker(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "loop-execution-lease.sqlite")
    run_id = "lease-run"
    database, loop, _, _, _, _, _, _ = build_stack(path, with_schedule=False)
    assert loop.start(request(with_schedule=False), run_id=run_id).phase == CareerLoopPhase.APPLICATION_APPROVAL
    assert loop.resume(run_id, approved=True).phase == CareerLoopPhase.MESSAGE_APPROVAL

    states = SQLiteCareerLoopRepository(database)
    state = states.get(run_id)
    assert state is not None and state.pending_human_action is not None
    state.approved_human_action = state.pending_human_action
    state.pending_human_action = None
    state.phase = CareerLoopPhase.MESSAGE_SEND
    states.save(state)
    database.close()

    entered_send = Event()
    release_send = Event()

    def continue_first():
        first_db, first_loop, _, _, _, _, _, _ = build_stack(
            path, with_schedule=False
        )
        original_send = first_loop._communication.send

        def blocking_send(*args, **kwargs):
            entered_send.set()
            assert release_send.wait(timeout=5)
            return original_send(*args, **kwargs)

        first_loop._communication.send = blocking_send
        try:
            return first_loop.continue_run(run_id)
        finally:
            first_db.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(continue_first)
        assert entered_send.wait(timeout=5)

        second_db, second_loop, _, _, _, _, second_message_provider, _ = build_stack(
            path, with_schedule=False
        )
        with pytest.raises(CareerLoopConflictError, match="already executing"):
            second_loop.continue_run(run_id)
        assert second_message_provider.calls == []
        second_db.close()

        release_send.set()
        completed = future.result(timeout=5)

    assert completed.completed is True


def test_expired_loop_execution_lease_can_be_recovered(tmp_path):
    path = str(tmp_path / "expired-execution-lease.sqlite")
    database = SQLiteDatabase(path)
    repository = SQLiteCareerLoopRepository(database)
    state = CareerLoopState(
        run_id="expired-lease",
        request=CareerLoopRequest(
            user_id="user-1",
            keyword="Logistics",
            candidate_profile=PROFILE,
        ),
    )
    repository.save(state)
    repository.claim_execution(
        state.run_id,
        "worker:old",
        expected_version=state.version,
        lease_seconds=900,
    )
    database.connection.execute(
        """UPDATE autonomous_career_loops
           SET execution_claim_expires_at = ?
           WHERE run_id = ?""",
        ((CREATED - timedelta(minutes=1)).isoformat(), state.run_id),
    )
    database.connection.commit()

    repository.claim_execution(
        state.run_id,
        "worker:new",
        expected_version=state.version,
        lease_seconds=900,
    )
    with pytest.raises(CareerLoopConflictError):
        repository.release_execution(state.run_id, "worker:old")
    repository.release_execution(state.run_id, "worker:new")
    database.close()

def test_loop_repository_compare_and_swap_rejects_stale_snapshot(tmp_path):
    path = str(tmp_path / "loop-cas.sqlite")
    first_db = SQLiteDatabase(path)
    first_repo = SQLiteCareerLoopRepository(first_db)
    state = CareerLoopState(
        run_id="cas-run",
        request=CareerLoopRequest(
            user_id="user-1",
            keyword="Logistics",
            candidate_profile=PROFILE,
        ),
    )
    first_repo.save(state)
    assert state.version == 1

    second_db = SQLiteDatabase(path)
    second_repo = SQLiteCareerLoopRepository(second_db)
    first_snapshot = first_repo.get("cas-run")
    stale_snapshot = second_repo.get("cas-run")
    assert first_snapshot is not None and stale_snapshot is not None
    assert first_snapshot.version == stale_snapshot.version == 1

    first_snapshot.phase = CareerLoopPhase.DECISION
    first_repo.save(first_snapshot)
    assert first_snapshot.version == 2

    stale_snapshot.phase = CareerLoopPhase.FAILED
    with pytest.raises(CareerLoopConflictError):
        second_repo.save(stale_snapshot)

    durable = first_repo.get("cas-run")
    assert durable is not None
    assert durable.phase == CareerLoopPhase.DECISION
    assert durable.version == 2
    first_db.close()
    second_db.close()


def test_transient_message_prepare_failure_after_submission_is_resumable(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "message-prepare-retry.sqlite")
    run_id = "message-prepare-retry"
    database, loop, applications, _, _, submission_provider, _, _ = build_stack(
        path, with_schedule=False
    )
    started = loop.start(request(with_schedule=False), run_id=run_id)
    assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL

    original_create_draft = loop._communication.create_draft
    attempts = {"count": 0}

    def fail_once(message):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise sqlite3.OperationalError("temporary message database outage")
        return original_create_draft(message)

    monkeypatch.setattr(loop._communication, "create_draft", fail_once)
    partial = loop.resume(run_id, approved=True)

    assert partial.phase == CareerLoopPhase.MESSAGE_PREPARE
    assert partial.completed is False
    assert "temporarily unavailable" in (partial.error or "")
    assert len(submission_provider.calls) == 1
    tracked = applications.get(f"{run_id}:application")
    assert tracked is not None and tracked.status == JobApplicationStatus.APPLIED
    database.close()

    database, restarted_loop, _, _, _, restarted_submission, _, _ = build_stack(
        path, with_schedule=False
    )
    recovered = restarted_loop.continue_run(run_id)

    assert recovered.phase == CareerLoopPhase.MESSAGE_APPROVAL
    assert recovered.error is None
    assert restarted_submission.calls == []
    database.close()


def test_transient_interview_read_after_submission_and_message_is_resumable(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "interview-read-retry.sqlite")
    run_id = "interview-read-retry"
    database, loop, _, _, scheduling, submission_provider, communication_provider, _ = build_stack(
        path
    )
    add_interview(scheduling, f"{run_id}:application")
    assert loop.start(request(), run_id=run_id).phase == CareerLoopPhase.APPLICATION_APPROVAL
    assert loop.resume(run_id, approved=True).phase == CareerLoopPhase.MESSAGE_APPROVAL

    original_get_event = scheduling.get_event
    attempts = {"count": 0}

    def fail_once(event_id):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise sqlite3.OperationalError("temporary scheduling database outage")
        return original_get_event(event_id)

    monkeypatch.setattr(scheduling, "get_event", fail_once)
    partial = loop.resume(run_id, approved=True)

    assert partial.phase == CareerLoopPhase.INTERVIEW_COORDINATION
    assert partial.completed is False
    assert "temporarily unavailable" in (partial.error or "")
    assert len(submission_provider.calls) == 1
    assert communication_provider.calls.count(("send", f"{run_id}:message-send")) == 1
    database.close()

    database, restarted_loop, _, _, _, restarted_submission, restarted_message, _ = build_stack(
        path
    )
    recovered = restarted_loop.continue_run(run_id)

    assert recovered.phase == CareerLoopPhase.INTERVIEW_APPROVAL
    assert recovered.error is None
    assert restarted_submission.calls == []
    assert restarted_message.calls == []
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
                "candidate_profile": PROFILE,
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


def test_post_message_send_snapshot_failure_remains_resumable_without_duplicate_send(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "post-send-snapshot-failure.sqlite")
    run_id = "post-send-save-recovery"
    database, loop, _, _, _, _, message_provider, _ = build_stack(
        path, with_schedule=False
    )
    assert loop.start(
        CareerLoopRequest(
            user_id="user-1",
            keyword="Logistics",
            candidate_profile=PROFILE,
            sender="candidate@example.test",
            recipient="recruiter@example.test",
        ),
        run_id=run_id,
    ).phase == CareerLoopPhase.APPLICATION_APPROVAL
    assert loop.resume(run_id, approved=True).phase == CareerLoopPhase.MESSAGE_APPROVAL

    original_save = loop._states.save
    failed_once = {"value": False}

    def fail_once_after_send(state):
        if state.phase == CareerLoopPhase.TRACK and not failed_once["value"]:
            failed_once["value"] = True
            raise sqlite3.OperationalError("temporary state database outage")
        return original_save(state)

    monkeypatch.setattr(loop._states, "save", fail_once_after_send)
    partial = loop.resume(run_id, approved=True)

    assert partial.phase == CareerLoopPhase.MESSAGE_SEND
    assert partial.completed is False
    assert "Post-step snapshot persistence" in (partial.error or "")
    assert message_provider.calls.count(("send", f"{run_id}:message-send")) == 1
    database.close()

    database, restarted, _, _, _, _, restarted_messages, _ = build_stack(
        path, with_schedule=False
    )
    completed = restarted.continue_run(run_id)

    assert completed.completed is True
    assert completed.error is None
    assert restarted_messages.calls == []
    database.close()


def test_execution_lease_heartbeat_blocks_reclaim_during_long_provider_call(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "heartbeat-long-step.sqlite")
    run_id = "heartbeat-long-step"
    database, loop, _, _, _, _, _, _ = build_stack(path, with_schedule=False)
    assert loop.start(
        CareerLoopRequest(
            user_id="user-1",
            keyword="Logistics",
            candidate_profile=PROFILE,
            sender="candidate@example.test",
            recipient="recruiter@example.test",
        ),
        run_id=run_id,
    ).phase == CareerLoopPhase.APPLICATION_APPROVAL
    assert loop.resume(run_id, approved=True).phase == CareerLoopPhase.MESSAGE_APPROVAL

    states = SQLiteCareerLoopRepository(database)
    state = states.get(run_id)
    assert state is not None and state.pending_human_action is not None
    state.approved_human_action = state.pending_human_action
    state.pending_human_action = None
    state.phase = CareerLoopPhase.MESSAGE_SEND
    states.save(state)
    database.close()

    monkeypatch.setattr(AutonomousCareerLoop, "EXECUTION_LEASE_SECONDS", 2)
    monkeypatch.setattr(AutonomousCareerLoop, "EXECUTION_HEARTBEAT_SECONDS", 1)
    entered = Event()
    release = Event()

    def run_first():
        first_db, first_loop, _, _, _, _, _, _ = build_stack(
            path, with_schedule=False
        )
        original_send = first_loop._communication.send

        def blocking_send(*args, **kwargs):
            entered.set()
            assert release.wait(timeout=6)
            return original_send(*args, **kwargs)

        first_loop._communication.send = blocking_send
        try:
            return first_loop.continue_run(run_id)
        finally:
            first_db.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run_first)
        assert entered.wait(timeout=5)
        sleep(2.4)

        second_db, second_loop, _, _, _, _, second_provider, _ = build_stack(
            path, with_schedule=False
        )
        with pytest.raises(CareerLoopConflictError, match="already executing"):
            second_loop.continue_run(run_id)
        assert second_provider.calls == []
        second_db.close()

        release.set()
        completed = future.result(timeout=6)

    assert completed.completed is True


def test_post_interview_accept_snapshot_failure_replays_without_duplicate_calendar_action(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "post-interview-save-failure.sqlite")
    run_id = "post-interview-save-recovery"
    database, loop, _, _, scheduling, _, _, calendar_provider = build_stack(path)
    add_interview(scheduling, f"{run_id}:application")

    assert loop.start(request(), run_id=run_id).phase == CareerLoopPhase.APPLICATION_APPROVAL
    assert loop.resume(run_id, approved=True).phase == CareerLoopPhase.MESSAGE_APPROVAL
    assert loop.resume(run_id, approved=True).phase == CareerLoopPhase.INTERVIEW_APPROVAL

    original_save = loop._states.save
    failed_once = {"value": False}

    def fail_once_after_accept(state):
        if state.phase == CareerLoopPhase.COMPLETE and not failed_once["value"]:
            failed_once["value"] = True
            raise sqlite3.OperationalError("temporary state database outage")
        return original_save(state)

    monkeypatch.setattr(loop._states, "save", fail_once_after_accept)
    partial = loop.resume(run_id, approved=True)

    assert partial.phase == CareerLoopPhase.INTERVIEW_ACCEPT
    assert partial.completed is False
    assert "Post-step snapshot persistence" in (partial.error or "")
    assert calendar_provider.calls.count(
        ("accept", f"{run_id}:interview-accept")
    ) == 1
    assert scheduling.get_event("interview-1").status == ScheduleStatus.ACCEPTED
    database.close()

    database, restarted, _, _, restarted_scheduling, _, _, restarted_calendar = build_stack(
        path
    )
    completed = restarted.continue_run(run_id)

    assert completed.completed is True
    assert completed.error is None
    assert restarted_calendar.calls == []
    assert restarted_scheduling.get_event("interview-1").status == ScheduleStatus.ACCEPTED
    database.close()


def test_ambiguous_application_submission_enters_explicit_reconciliation_and_can_confirm_success(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "application-reconciliation-success.sqlite")
    run_id = "reconcile-submitted"
    database, loop, applications, _, _, submission_provider, _, _ = build_stack(
        path, with_schedule=False
    )
    started = loop.start(
        CareerLoopRequest(
            user_id="user-1",
            keyword="Logistics",
            candidate_profile=PROFILE,
        ),
        run_id=run_id,
    )
    assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL

    calls = {"count": 0}

    def ambiguous_submit(*args, **kwargs):
        calls["count"] += 1
        raise RuntimeError("connection lost after provider may have accepted submission")

    monkeypatch.setattr(submission_provider, "submit", ambiguous_submit)
    ambiguous = loop.resume(run_id, approved=True)

    assert ambiguous.phase == CareerLoopPhase.APPLICATION_RECONCILIATION
    assert ambiguous.completed is False
    assert ambiguous.human_action is not None
    assert (
        ambiguous.human_action.kind
        == HumanActionKind.RECONCILE_APPLICATION_SUBMISSION
    )
    operation_id = ambiguous.human_action.details["operation_id"]
    tracked = applications.get(f"{run_id}:application")
    assert tracked is not None and tracked.status == JobApplicationStatus.SAVED
    assert calls["count"] == 1

    with pytest.raises(RuntimeError, match="dedicated reconciliation resolver"):
        loop.resume(run_id, approved=True)

    completed = loop.resolve_application_reconciliation(
        run_id,
        submitted=True,
        provider_submission_id="provider/confirmed=123",
    )

    assert completed.completed is True
    assert completed.error is None
    assert calls["count"] == 1
    tracked = applications.get(f"{run_id}:application")
    assert tracked is not None and tracked.status == JobApplicationStatus.APPLIED
    assert operation_id in tracked.external_action_operation_ids
    database.close()


def test_ambiguous_application_submission_confirmed_no_effect_retries_same_operation_once(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "application-reconciliation-no-effect.sqlite")
    run_id = "reconcile-not-submitted"
    database, loop, applications, _, _, submission_provider, _, _ = build_stack(
        path, with_schedule=False
    )
    started = loop.start(
        CareerLoopRequest(
            user_id="user-1",
            keyword="Logistics",
            candidate_profile=PROFILE,
        ),
        run_id=run_id,
    )
    assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL

    original_submit = submission_provider.submit
    attempts = {"count": 0}

    def ambiguous_once(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("request outcome unknown")
        return original_submit(*args, **kwargs)

    monkeypatch.setattr(submission_provider, "submit", ambiguous_once)
    ambiguous = loop.resume(run_id, approved=True)

    assert ambiguous.phase == CareerLoopPhase.APPLICATION_RECONCILIATION
    operation_id = ambiguous.human_action.details["operation_id"]

    completed = loop.resolve_application_reconciliation(
        run_id,
        submitted=False,
    )

    assert completed.completed is True
    assert completed.error is None
    assert attempts["count"] == 2
    assert submission_provider.calls == [
        (operation_id, "job-1", f"{run_id}:application")
    ]
    tracked = applications.get(f"{run_id}:application")
    assert tracked is not None and tracked.status == JobApplicationStatus.APPLIED
    database.close()


def test_ambiguous_message_delivery_enters_reconciliation_and_can_confirm_success(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "message-reconciliation-success.sqlite")
    run_id = "reconcile-message-delivered"
    database, loop, _, messages, _, _, communication_provider, _ = build_stack(
        path, with_schedule=False
    )
    started = loop.start(
        CareerLoopRequest(
            user_id="user-1",
            keyword="Logistics",
            candidate_profile=PROFILE,
            sender="candidate@example.test",
            recipient="recruiter@example.test",
        ),
        run_id=run_id,
    )
    assert started.phase == CareerLoopPhase.APPLICATION_APPROVAL
    assert loop.resume(run_id, approved=True).phase == CareerLoopPhase.MESSAGE_APPROVAL

    calls = {"count": 0}

    def ambiguous_send(*args, **kwargs):
        calls["count"] += 1
        raise RuntimeError("connection lost after message may have been delivered")

    monkeypatch.setattr(communication_provider, "send", ambiguous_send)
    ambiguous = loop.resume(run_id, approved=True)

    assert ambiguous.phase == CareerLoopPhase.MESSAGE_RECONCILIATION
    assert ambiguous.human_action is not None
    assert ambiguous.human_action.kind == HumanActionKind.RECONCILE_MESSAGE_DELIVERY
    assert calls["count"] == 1

    completed = loop.resolve_message_reconciliation(run_id, delivered=True)

    assert completed.completed is True
    assert calls["count"] == 1
    assert messages.get(f"{run_id}:message").direction.value == "outbound"
    database.close()


def test_ambiguous_message_delivery_confirmed_no_effect_retries_same_operation(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "message-reconciliation-no-effect.sqlite")
    run_id = "reconcile-message-not-delivered"
    database, loop, _, _, _, _, communication_provider, _ = build_stack(
        path, with_schedule=False
    )
    loop.start(
        CareerLoopRequest(
            user_id="user-1",
            keyword="Logistics",
            candidate_profile=PROFILE,
            sender="candidate@example.test",
            recipient="recruiter@example.test",
        ),
        run_id=run_id,
    )
    assert loop.resume(run_id, approved=True).phase == CareerLoopPhase.MESSAGE_APPROVAL

    original_send = communication_provider.send
    attempts = {"count": 0}

    def ambiguous_once(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("unknown message outcome")
        return original_send(*args, **kwargs)

    monkeypatch.setattr(communication_provider, "send", ambiguous_once)
    assert loop.resume(run_id, approved=True).phase == CareerLoopPhase.MESSAGE_RECONCILIATION

    completed = loop.resolve_message_reconciliation(run_id, delivered=False)

    assert completed.completed is True
    assert attempts["count"] == 2
    assert communication_provider.calls.count(
        ("send", f"{run_id}:message-send")
    ) == 1
    database.close()


def test_ambiguous_interview_response_enters_reconciliation_and_can_confirm_success(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "interview-reconciliation-success.sqlite")
    run_id = "reconcile-interview-accepted"
    database, loop, _, _, scheduling, _, _, calendar_provider = build_stack(path)
    add_interview(scheduling, f"{run_id}:application")

    assert loop.start(request(), run_id=run_id).phase == CareerLoopPhase.APPLICATION_APPROVAL
    assert loop.resume(run_id, approved=True).phase == CareerLoopPhase.MESSAGE_APPROVAL
    assert loop.resume(run_id, approved=True).phase == CareerLoopPhase.INTERVIEW_APPROVAL

    calls = {"count": 0}

    def ambiguous_accept(*args, **kwargs):
        calls["count"] += 1
        raise RuntimeError("calendar response outcome unknown")

    monkeypatch.setattr(calendar_provider, "accept", ambiguous_accept)
    ambiguous = loop.resume(run_id, approved=True)

    assert ambiguous.phase == CareerLoopPhase.INTERVIEW_RECONCILIATION
    assert ambiguous.human_action is not None
    assert (
        ambiguous.human_action.kind
        == HumanActionKind.RECONCILE_INTERVIEW_RESPONSE
    )
    assert calls["count"] == 1

    completed = loop.resolve_interview_reconciliation(
        run_id,
        completed=True,
        provider_event_id="provider-confirmed-interview-1",
    )

    assert completed.completed is True
    assert calls["count"] == 1
    accepted = scheduling.get_event("interview-1")
    assert accepted.status == ScheduleStatus.ACCEPTED
    assert accepted.provider_event_id == "provider-confirmed-interview-1"
    database.close()


def test_ambiguous_interview_response_confirmed_no_effect_retries_same_operation(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "interview-reconciliation-no-effect.sqlite")
    run_id = "reconcile-interview-not-accepted"
    database, loop, _, _, scheduling, _, _, calendar_provider = build_stack(path)
    add_interview(scheduling, f"{run_id}:application")

    loop.start(request(), run_id=run_id)
    assert loop.resume(run_id, approved=True).phase == CareerLoopPhase.MESSAGE_APPROVAL
    assert loop.resume(run_id, approved=True).phase == CareerLoopPhase.INTERVIEW_APPROVAL

    original_accept = calendar_provider.accept
    attempts = {"count": 0}

    def ambiguous_once(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("unknown calendar outcome")
        return original_accept(*args, **kwargs)

    monkeypatch.setattr(calendar_provider, "accept", ambiguous_once)
    assert loop.resume(run_id, approved=True).phase == CareerLoopPhase.INTERVIEW_RECONCILIATION

    completed = loop.resolve_interview_reconciliation(run_id, completed=False)

    assert completed.completed is True
    assert attempts["count"] == 2
    assert calendar_provider.calls.count(
        ("accept", f"{run_id}:interview-accept")
    ) == 1
    assert scheduling.get_event("interview-1").status == ScheduleStatus.ACCEPTED
    database.close()


def test_generic_decline_cannot_terminalize_application_reconciliation(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "reconciliation-generic-decline.sqlite")
    run_id = "reconcile-generic-decline"
    database, loop, applications, _, _, submission_provider, _, _ = build_stack(
        path, with_schedule=False
    )
    loop.start(
        CareerLoopRequest(
            user_id="user-1",
            keyword="Logistics",
            candidate_profile=PROFILE,
        ),
        run_id=run_id,
    )

    def ambiguous_submit(*args, **kwargs):
        raise RuntimeError("ambiguous provider outcome")

    monkeypatch.setattr(submission_provider, "submit", ambiguous_submit)
    ambiguous = loop.resume(run_id, approved=True)
    assert ambiguous.phase == CareerLoopPhase.APPLICATION_RECONCILIATION

    with pytest.raises(RuntimeError, match="dedicated reconciliation resolver"):
        loop.resume(run_id, approved=False)

    still_waiting = loop.get(run_id)
    assert still_waiting.phase == CareerLoopPhase.APPLICATION_RECONCILIATION
    assert still_waiting.human_action is not None
    tracked = applications.get(f"{run_id}:application")
    assert tracked is not None and tracked.status == JobApplicationStatus.SAVED
    database.close()


def test_application_reconciliation_resolution_survives_snapshot_failure(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "reconciliation-resolution-restart.sqlite")
    run_id = "reconcile-resolution-restart"
    database, loop, applications, _, _, submission_provider, _, _ = build_stack(
        path, with_schedule=False
    )
    loop.start(
        CareerLoopRequest(
            user_id="user-1",
            keyword="Logistics",
            candidate_profile=PROFILE,
        ),
        run_id=run_id,
    )

    def ambiguous_submit(*args, **kwargs):
        raise RuntimeError("ambiguous provider outcome")

    monkeypatch.setattr(submission_provider, "submit", ambiguous_submit)
    ambiguous = loop.resume(run_id, approved=True)
    assert ambiguous.phase == CareerLoopPhase.APPLICATION_RECONCILIATION

    original_save = loop._states.save
    failed_once = {"value": False}

    def fail_resolution_snapshot_once(state):
        if state.phase == CareerLoopPhase.APPLICATION_SUBMIT and not failed_once["value"]:
            failed_once["value"] = True
            raise sqlite3.OperationalError("snapshot unavailable after reconciliation")
        return original_save(state)

    monkeypatch.setattr(loop._states, "save", fail_resolution_snapshot_once)
    with pytest.raises(sqlite3.OperationalError):
        loop.resolve_application_reconciliation(
            run_id,
            submitted=True,
            provider_submission_id="provider-confirmed-after-crash",
        )

    persisted_gate = loop.get(run_id)
    assert persisted_gate.phase == CareerLoopPhase.APPLICATION_RECONCILIATION

    completed = loop.resolve_application_reconciliation(
        run_id,
        submitted=True,
        provider_submission_id="provider-confirmed-after-crash",
    )

    assert completed.completed is True
    tracked = applications.get(f"{run_id}:application")
    assert tracked is not None and tracked.status == JobApplicationStatus.APPLIED
    database.close()


def test_message_reconciliation_resolution_survives_snapshot_failure(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "message-reconciliation-resolution-restart.sqlite")
    run_id = "message-reconcile-resolution-restart"
    database, loop, _, messages, _, _, communication_provider, _ = build_stack(
        path, with_schedule=False
    )
    loop.start(
        CareerLoopRequest(
            user_id="user-1",
            keyword="Logistics",
            candidate_profile=PROFILE,
            sender="candidate@example.test",
            recipient="recruiter@example.test",
        ),
        run_id=run_id,
    )
    assert loop.resume(run_id, approved=True).phase == CareerLoopPhase.MESSAGE_APPROVAL

    def ambiguous_send(*args, **kwargs):
        raise RuntimeError("ambiguous message outcome")

    monkeypatch.setattr(communication_provider, "send", ambiguous_send)
    assert loop.resume(run_id, approved=True).phase == CareerLoopPhase.MESSAGE_RECONCILIATION

    original_save = loop._states.save
    failed_once = {"value": False}

    def fail_resolution_snapshot_once(state):
        if state.phase == CareerLoopPhase.MESSAGE_SEND and not failed_once["value"]:
            failed_once["value"] = True
            raise sqlite3.OperationalError("snapshot unavailable after reconciliation")
        return original_save(state)

    monkeypatch.setattr(loop._states, "save", fail_resolution_snapshot_once)
    with pytest.raises(sqlite3.OperationalError):
        loop.resolve_message_reconciliation(run_id, delivered=True)

    assert loop.get(run_id).phase == CareerLoopPhase.MESSAGE_RECONCILIATION
    completed = loop.resolve_message_reconciliation(run_id, delivered=True)

    assert completed.completed is True
    assert messages.get(f"{run_id}:message").direction.value == "outbound"
    database.close()
