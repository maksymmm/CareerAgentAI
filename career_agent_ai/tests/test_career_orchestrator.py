from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier, Event

import pytest
import sqlite3
import time

from career_agent_ai.application.agents.agent import Agent
from career_agent_ai.application.agents.agent_factory import AgentFactory
from career_agent_ai.application.agents.agent_registry import AgentRegistry
from career_agent_ai.application.agents.agent_result import AgentResult
from career_agent_ai.application.career.career_orchestrator import CareerOrchestrator
from career_agent_ai.application.career.career_run_repository import CareerRunConflictError
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.memory.in_memory_memory_repository import InMemoryMemoryRepository
from career_agent_ai.application.memory.memory_engine import MemoryEngine
from career_agent_ai.application.memory.memory_record import MemoryRecord
from career_agent_ai.application.storage.sqlite_career_run_repository import (
    SQLiteCareerRunRepository,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.workflow.workflow_engine import WorkflowEngine
from career_agent_ai.application.workflow.workflow_state import WorkflowState


class FakeAgent(Agent):
    def __init__(
        self,
        agent_id: str,
        *,
        result: AgentResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self._id = agent_id
        self._result = result
        self._error = error
        self.last_context = None

    @property
    def id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._id

    @property
    def version(self) -> str:
        return "1.0"

    @property
    def description(self) -> str:
        return "fake"

    def execute(self, context):
        self.last_context = context
        if self._error is not None:
            raise self._error
        if self._result is not None:
            return self._result
        return AgentResult(
            success=True,
            agent_id=self._id,
            messages=(f"executed:{self._id}",),
        )

    def supports(self, action: str) -> bool:
        return action == self._id

    def snapshot(self) -> AgentResult:
        return AgentResult(success=True, agent_id=self._id)


def make_orchestrator(
    max_steps: int = 8,
    agents: tuple[FakeAgent, ...] | None = None,
) -> CareerOrchestrator:
    registry = AgentRegistry()
    configured_agents = agents or (
        FakeAgent("job_search"),
        FakeAgent("resume"),
        FakeAgent("job_application"),
    )
    for agent in configured_agents:
        registry.register(agent)
    factory = AgentFactory(registry)
    return CareerOrchestrator(
        memory_engine=MemoryEngine(),
        workflow_engine=WorkflowEngine(),
        agent_factory=factory,
        max_steps=max_steps,
    )


def test_plan_defaults_to_job_search():
    plan = make_orchestrator().plan("Find me a Python job")

    assert plan.objective == "Find me a Python job"
    assert plan.actions() == ("job_search",)


def test_plan_accepts_explicit_actions():
    plan = make_orchestrator().plan(
        "Find and apply",
        {"actions": ["job_search", "resume", "job_application"]},
    )

    assert plan.actions() == (
        "job_search",
        "resume",
        "job_application",
    )


def test_run_executes_all_planned_actions():
    result = make_orchestrator().run(
        user_id="user-1",
        objective="Find and apply",
        payload={"actions": ["job_search", "resume", "job_application"]},
    )

    assert result.success is True
    assert result.stopped_reason is None
    assert len(result.run_id) == 32
    assert tuple(step.action for step in result.steps) == (
        "job_search",
        "resume",
        "job_application",
    )


def test_run_creates_unique_run_ids():
    orchestrator = make_orchestrator()

    first = orchestrator.run("user-1", "Find a job")
    second = orchestrator.run("user-1", "Find another job")

    assert first.run_id != second.run_id


def test_run_respects_max_steps():
    result = make_orchestrator(max_steps=2).run(
        user_id="user-1",
        objective="Find and apply",
        payload={"actions": ["job_search", "resume", "job_application"]},
    )

    assert result.success is False
    assert result.stopped_reason == "max_steps_reached"
    assert len(result.steps) == 2


def test_run_persists_summary_in_memory():
    memory = MemoryEngine()
    registry = AgentRegistry()
    registry.register(FakeAgent("job_search"))
    orchestrator = CareerOrchestrator(
        memory_engine=memory,
        workflow_engine=WorkflowEngine(),
        agent_factory=AgentFactory(registry),
    )

    result = orchestrator.run("user-1", "Find a job")
    record = memory.get(f"career_run:user-1:{result.run_id}")

    assert record is not None
    assert record.value["run_id"] == result.run_id
    assert record.value["success"] is True
    assert record.user_id == "user-1"
    assert record.memory_type == "career_run"



def test_agent_context_memory_snapshot_is_scoped_to_run_user():
    memory = MemoryEngine()
    memory.save(
        MemoryRecord(
            key="user-1:private",
            value="one",
            user_id="user-1",
            memory_type="note",
        )
    )
    memory.save(
        MemoryRecord(
            key="user-2:private",
            value="two",
            user_id="user-2",
            memory_type="note",
        )
    )
    search_agent = FakeAgent("job_search")
    registry = AgentRegistry()
    registry.register(search_agent)
    orchestrator = CareerOrchestrator(
        memory_engine=memory,
        workflow_engine=WorkflowEngine(),
        agent_factory=AgentFactory(registry),
    )

    result = orchestrator.run("user-2", "Find a job")

    assert result.success is True
    snapshot = search_agent.last_context.memory_snapshot
    assert snapshot.size() == 1
    assert snapshot.get("user-2:private") == "two"
    assert snapshot.contains("user-1:private") is False

def test_human_gated_run_can_be_resumed():
    application = FakeAgent(
        "job_application",
        result=AgentResult(
            success=True,
            agent_id="job_application",
            metadata={"requires_human": True},
        ),
    )
    resume_agent = FakeAgent("resume")
    orchestrator = make_orchestrator(agents=(application, resume_agent))

    paused = orchestrator.run(
        "user-1",
        "Apply to the selected job",
        {"actions": ["job_application", "resume"]},
    )

    assert paused.success is False
    assert paused.stopped_reason == "human_action_required"
    assert orchestrator._workflow.workflow.status == WorkflowState.PAUSED

    resumed = orchestrator.resume(paused.run_id, human_result={"approved": True})

    assert resumed.success is True
    assert resumed.stopped_reason is None
    assert tuple(step.action for step in resumed.steps) == (
        "job_application",
        "resume",
    )
    assert resume_agent.last_context.payload["human_result"] == {"approved": True}
    assert paused.run_id == resumed.run_id


def test_human_gated_run_does_not_block_a_new_run():
    application = FakeAgent(
        "job_application",
        result=AgentResult(
            success=True,
            agent_id="job_application",
            metadata={"requires_human": True},
        ),
    )
    search_agent = FakeAgent("job_search")
    orchestrator = make_orchestrator(agents=(application, search_agent))

    paused = orchestrator.run("user-1", "Apply now", {"actions": ["job_application"]})
    independent = orchestrator.run("user-1", "Find another job")

    assert paused.run_id != independent.run_id
    assert paused.stopped_reason == "human_action_required"
    assert independent.success is True


def test_resume_unknown_run_is_rejected():
    with pytest.raises(KeyError):
        make_orchestrator().resume("missing-run")


def test_resume_completed_run_is_rejected_as_unknown():
    orchestrator = make_orchestrator()
    completed = orchestrator.run("user-1", "Find a job")

    with pytest.raises(KeyError, match="Unknown career run"):
        orchestrator.resume(completed.run_id)


def test_run_contains_agent_failure_without_raising():
    agent = FakeAgent(
        "job_search",
        error=RuntimeError("provider unavailable"),
    )

    orchestrator = make_orchestrator(agents=(agent,))
    result = orchestrator.run("user-1", "Find a job")

    assert result.success is False
    assert result.stopped_reason == "agent_exception"
    assert result.steps[0].success is False
    assert "provider unavailable" in result.steps[0].messages[0]


def test_agent_exception_credentials_are_redacted_from_result_and_memory():
    memory = MemoryEngine()
    registry = AgentRegistry()
    registry.register(
        FakeAgent(
            "job_search",
            error=RuntimeError("api_key=super-secret-value"),
        )
    )
    orchestrator = CareerOrchestrator(
        memory_engine=memory,
        workflow_engine=WorkflowEngine(),
        agent_factory=AgentFactory(registry),
    )

    result = orchestrator.run("user-1", "Find a job")
    summary = memory.get(f"career_run:user-1:{result.run_id}", user_id="user-1")

    assert "super-secret-value" not in result.steps[0].messages[0]
    assert "[REDACTED]" in result.steps[0].messages[0]
    assert summary is not None
    serialized_summary = str(summary.value)
    assert "super-secret-value" not in serialized_summary
    assert "[REDACTED]" in serialized_summary


def test_empty_objective_is_rejected():
    with pytest.raises(ValueError):
        make_orchestrator().plan("   ")


def test_empty_user_id_is_rejected():
    with pytest.raises(ValueError):
        make_orchestrator().run("   ", "Find a job")


def test_invalid_max_steps_is_rejected():
    with pytest.raises(ValueError):
        make_orchestrator(max_steps=0)



def test_resume_persists_approval_before_next_agent_execution(tmp_path):
    database = SQLiteDatabase(str(tmp_path / "resume-boundary.sqlite"))
    repository = SQLiteCareerRunRepository(database)
    gated_application = FakeAgent(
        "job_application",
        result=AgentResult(
            success=True,
            agent_id="job_application",
            metadata={"requires_human": True},
        ),
    )
    crash_after_approval = FakeAgent("resume", error=SystemExit("simulated process death"))
    registry = AgentRegistry()
    registry.register(gated_application)
    registry.register(crash_after_approval)
    orchestrator = CareerOrchestrator(
        memory_engine=MemoryEngine(),
        workflow_engine=WorkflowEngine(),
        agent_factory=AgentFactory(registry),
        run_repository=repository,
    )
    paused = orchestrator.run(
        "user-1",
        "Apply to selected job",
        {"actions": ["job_application", "resume"]},
    )

    with pytest.raises(SystemExit, match="simulated process death"):
        orchestrator.resume(paused.run_id, human_result={"approved": True})

    durable = repository.get(paused.run_id)
    assert durable is not None
    assert durable.payload["human_result"] == {"approved": True}
    assert durable.workflow_engine.workflow is not None
    assert durable.workflow_engine.workflow.status == WorkflowState.RUNNING
    assert durable.workflow_engine.workflow.current_step == 1
    database.close()

def test_paused_run_survives_process_restart(tmp_path):
    database_path = str(tmp_path / "career-runs.sqlite")
    first_database = SQLiteDatabase(database_path)
    first_repository = SQLiteCareerRunRepository(first_database)
    gated_application = FakeAgent(
        "job_application",
        result=AgentResult(
            success=True,
            agent_id="job_application",
            metadata={"requires_human": True},
        ),
    )
    first_registry = AgentRegistry()
    first_registry.register(gated_application)
    first_registry.register(FakeAgent("resume"))
    first_orchestrator = CareerOrchestrator(
        memory_engine=MemoryEngine(),
        workflow_engine=WorkflowEngine(),
        agent_factory=AgentFactory(first_registry),
        run_repository=first_repository,
    )

    paused = first_orchestrator.run(
        "user-1",
        "Apply to the selected job",
        {"actions": ["job_application", "resume"]},
    )
    assert paused.stopped_reason == "human_action_required"
    assert first_repository.get(paused.run_id) is not None
    first_database.close()

    second_database = SQLiteDatabase(database_path)
    second_repository = SQLiteCareerRunRepository(second_database)
    resume_agent = FakeAgent("resume")
    second_registry = AgentRegistry()
    second_registry.register(resume_agent)
    second_orchestrator = CareerOrchestrator(
        memory_engine=MemoryEngine(),
        workflow_engine=WorkflowEngine(),
        agent_factory=AgentFactory(second_registry),
        run_repository=second_repository,
    )

    resumed = second_orchestrator.resume(
        paused.run_id,
        human_result={"approved": True},
    )

    assert resumed.success is True
    assert tuple(step.action for step in resumed.steps) == (
        "job_application",
        "resume",
    )
    assert resume_agent.last_context.payload["human_result"] == {"approved": True}
    assert second_repository.get(paused.run_id) is None
    second_database.close()


def test_sqlite_run_repository_rejects_empty_run_id():
    database = SQLiteDatabase()
    repository = SQLiteCareerRunRepository(database)

    with pytest.raises(ValueError, match="run_id"):
        repository.get("   ")
    with pytest.raises(ValueError, match="run_id"):
        repository.delete("   ")

    database.close()


def test_durable_run_normalizes_dataclass_agent_metadata(tmp_path):
    database = SQLiteDatabase(str(tmp_path / "metadata-normalization.sqlite"))
    repository = SQLiteCareerRunRepository(database)
    job = Job(
        job_id="job-1",
        title="Python Developer",
        company="Acme GmbH",
        location="Karlsruhe",
        salary=None,
        employment_type="full_time",
        source="test",
        user_id="user-1",
        url="https://example.test/jobs/1",
        description="Role",
        created_at=datetime(2026, 9, 30, 8, 15, tzinfo=timezone.utc),
    )
    agent = FakeAgent(
        "job_search",
        result=AgentResult(
            success=True,
            agent_id="job_search",
            metadata={"jobs": (job,), "count": 1},
        ),
    )
    registry = AgentRegistry()
    registry.register(agent)
    orchestrator = CareerOrchestrator(
        memory_engine=MemoryEngine(),
        workflow_engine=WorkflowEngine(),
        agent_factory=AgentFactory(registry),
        run_repository=repository,
    )

    result = orchestrator.run(
        "user-1",
        "Find a job",
        {"actions": ["job_search"]},
    )

    assert result.success is True
    jobs = result.steps[0].metadata["jobs"]
    assert isinstance(jobs, list)
    assert jobs[0]["job_id"] == "job-1"
    assert jobs[0]["created_at"] == "2026-09-30T08:15:00+00:00"
    assert repository.get(result.run_id) is None
    database.close()


def test_unsupported_agent_metadata_fails_cleanly_without_breaking_checkpoint(tmp_path):
    database = SQLiteDatabase(str(tmp_path / "metadata-invalid.sqlite"))
    repository = SQLiteCareerRunRepository(database)
    agent = FakeAgent(
        "job_search",
        result=AgentResult(
            success=True,
            agent_id="job_search",
            metadata={"unsupported": object()},
        ),
    )
    registry = AgentRegistry()
    registry.register(agent)
    orchestrator = CareerOrchestrator(
        memory_engine=MemoryEngine(),
        workflow_engine=WorkflowEngine(),
        agent_factory=AgentFactory(registry),
        run_repository=repository,
    )

    result = orchestrator.run(
        "user-1",
        "Find a job",
        {"actions": ["job_search"]},
    )

    assert result.success is False
    assert result.stopped_reason == "invalid_agent_metadata"
    assert result.steps[0].success is False
    assert "not durably serializable" in result.steps[0].messages[0]
    database.close()



@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_agent_metadata_is_rejected_before_durable_finalization(
    non_finite,
):
    agent = FakeAgent(
        "job_search",
        result=AgentResult(
            success=True,
            agent_id="job_search",
            metadata={"score": non_finite},
        ),
    )
    orchestrator = make_orchestrator(agents=(agent,))

    result = orchestrator.run(
        "user-1",
        "Find a job",
        {"actions": ["job_search"]},
    )

    assert result.success is False
    assert result.stopped_reason == "invalid_agent_metadata"
    assert "must be finite" in result.steps[0].messages[0]


def test_sqlite_run_repository_rejects_stale_snapshot_update(tmp_path):
    path = str(tmp_path / "career-run-cas.sqlite")
    database = SQLiteDatabase(path)
    repository = SQLiteCareerRunRepository(database)
    gated = FakeAgent(
        "job_application",
        result=AgentResult(
            success=True,
            agent_id="job_application",
            metadata={"requires_human": True},
        ),
    )
    registry = AgentRegistry()
    registry.register(gated)
    orchestrator = CareerOrchestrator(
        memory_engine=MemoryEngine(),
        workflow_engine=WorkflowEngine(),
        agent_factory=AgentFactory(registry),
        run_repository=repository,
    )
    paused = orchestrator.run(
        "user-1",
        "Apply now",
        {"actions": ["job_application"]},
    )

    first = repository.get(paused.run_id)
    stale = repository.get(paused.run_id)
    assert first is not None and stale is not None
    assert first.version == stale.version

    first.payload["winner"] = True
    repository.save(first)
    stale.payload["stale"] = True

    with pytest.raises(CareerRunConflictError, match="changed"):
        repository.save(stale)

    durable = repository.get(paused.run_id)
    assert durable is not None
    assert durable.payload["winner"] is True
    assert "stale" not in durable.payload
    database.close()


def test_concurrent_cross_process_resumes_execute_next_agent_once(tmp_path):
    path = str(tmp_path / "career-run-resume-race.sqlite")
    setup_db = SQLiteDatabase(path)
    setup_repo = SQLiteCareerRunRepository(setup_db)
    gated = FakeAgent(
        "job_application",
        result=AgentResult(
            success=True,
            agent_id="job_application",
            metadata={"requires_human": True},
        ),
    )
    setup_registry = AgentRegistry()
    setup_registry.register(gated)
    setup_registry.register(FakeAgent("resume"))
    setup = CareerOrchestrator(
        memory_engine=MemoryEngine(),
        workflow_engine=WorkflowEngine(),
        agent_factory=AgentFactory(setup_registry),
        run_repository=setup_repo,
    )
    paused = setup.run(
        "user-1",
        "Apply then resume",
        {"actions": ["job_application", "resume"]},
    )
    setup_db.close()

    barrier = Barrier(2)

    class BarrierRepository(SQLiteCareerRunRepository):
        def get(self, run_id):
            state = super().get(run_id)
            if (
                state is not None
                and state.workflow_engine.workflow is not None
                and state.workflow_engine.workflow.status == WorkflowState.PAUSED
            ):
                barrier.wait(timeout=5)
            return state

    resume_agent = FakeAgent("resume")

    def make_worker():
        database = SQLiteDatabase(path)
        repository = BarrierRepository(database)
        registry = AgentRegistry()
        registry.register(resume_agent)
        orchestrator = CareerOrchestrator(
            memory_engine=MemoryEngine(),
            workflow_engine=WorkflowEngine(),
            agent_factory=AgentFactory(registry),
            run_repository=repository,
        )
        return database, orchestrator

    def resume_once():
        database, orchestrator = make_worker()
        try:
            try:
                return orchestrator.resume(
                    paused.run_id,
                    human_result={"approved": True},
                )
            except (CareerRunConflictError, KeyError):
                return "conflict"
        finally:
            database.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: resume_once(), range(2)))

    assert sum(result == "conflict" for result in results) == 1
    completed = [result for result in results if result != "conflict"]
    assert len(completed) == 1
    assert completed[0].success is True
    assert resume_agent.last_context is not None


def test_durable_running_run_can_continue_after_process_restart(tmp_path):
    path = str(tmp_path / "running-recovery.sqlite")
    first_database = SQLiteDatabase(path)
    first_repository = SQLiteCareerRunRepository(first_database)
    crash_agent = FakeAgent("job_search", error=SystemExit("simulated process death"))
    first_registry = AgentRegistry()
    first_registry.register(crash_agent)
    first = CareerOrchestrator(
        memory_engine=MemoryEngine(),
        workflow_engine=WorkflowEngine(),
        agent_factory=AgentFactory(first_registry),
        run_repository=first_repository,
    )

    with pytest.raises(SystemExit, match="simulated process death"):
        first.run(
            "user-1",
            "Find a job",
            {"actions": ["job_search"]},
        )

    row = first_database.connection.execute(
        "SELECT run_id FROM career_runs"
    ).fetchone()
    assert row is not None
    run_id = str(row[0])
    durable = first_repository.get(run_id)
    assert durable is not None
    assert durable.workflow_engine.workflow is not None
    assert durable.workflow_engine.workflow.status == WorkflowState.RUNNING
    first_database.close()

    second_database = SQLiteDatabase(path)
    second_repository = SQLiteCareerRunRepository(second_database)
    recovered_agent = FakeAgent("job_search")
    second_registry = AgentRegistry()
    second_registry.register(recovered_agent)
    second = CareerOrchestrator(
        memory_engine=MemoryEngine(),
        workflow_engine=WorkflowEngine(),
        agent_factory=AgentFactory(second_registry),
        run_repository=second_repository,
    )

    result = second.continue_run(run_id)

    assert result.success is True
    assert tuple(step.action for step in result.steps) == ("job_search",)
    assert recovered_agent.last_context is not None
    assert second_repository.get(run_id) is None
    second_database.close()


def test_concurrent_running_recovery_is_fenced_before_agent_execution(tmp_path):
    path = str(tmp_path / "running-recovery-race.sqlite")
    setup_database = SQLiteDatabase(path)
    setup_repository = SQLiteCareerRunRepository(setup_database)
    crash_agent = FakeAgent("job_search", error=SystemExit("simulated process death"))
    setup_registry = AgentRegistry()
    setup_registry.register(crash_agent)
    setup = CareerOrchestrator(
        memory_engine=MemoryEngine(),
        workflow_engine=WorkflowEngine(),
        agent_factory=AgentFactory(setup_registry),
        run_repository=setup_repository,
    )

    with pytest.raises(SystemExit):
        setup.run("user-1", "Find a job", {"actions": ["job_search"]})
    row = setup_database.connection.execute(
        "SELECT run_id FROM career_runs"
    ).fetchone()
    assert row is not None
    run_id = str(row[0])
    setup_database.close()

    barrier = Barrier(2)
    executed = []
    execution_lock = __import__("threading").Lock()

    class BarrierRepository(SQLiteCareerRunRepository):
        def get(self, requested_run_id):
            state = super().get(requested_run_id)
            if (
                state is not None
                and state.workflow_engine.workflow is not None
                and state.workflow_engine.workflow.status == WorkflowState.RUNNING
                and state.version == 1
            ):
                barrier.wait(timeout=5)
            return state

    class CountingAgent(FakeAgent):
        def execute(self, context):
            with execution_lock:
                executed.append(context.metadata["career_run_id"])
            return super().execute(context)

    agent = CountingAgent("job_search")

    def recover_once():
        database = SQLiteDatabase(path)
        repository = BarrierRepository(database)
        registry = AgentRegistry()
        registry.register(agent)
        worker = CareerOrchestrator(
            memory_engine=MemoryEngine(),
            workflow_engine=WorkflowEngine(),
            agent_factory=AgentFactory(registry),
            run_repository=repository,
        )
        try:
            try:
                return worker.continue_run(run_id)
            except (CareerRunConflictError, KeyError):
                return "conflict"
        finally:
            database.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: recover_once(), range(2)))

    assert sum(result == "conflict" for result in results) == 1
    completed = [result for result in results if result != "conflict"]
    assert len(completed) == 1
    assert completed[0].success is True
    assert executed == [run_id]



def test_execution_lease_heartbeat_blocks_reclaim_during_long_agent_call(tmp_path):
    path = str(tmp_path / "running-lease-heartbeat.sqlite")
    setup_database = SQLiteDatabase(path)
    setup_repository = SQLiteCareerRunRepository(setup_database)
    crash_agent = FakeAgent("job_search", error=SystemExit("simulated process death"))
    setup_registry = AgentRegistry()
    setup_registry.register(crash_agent)
    setup = CareerOrchestrator(
        memory_engine=MemoryEngine(),
        workflow_engine=WorkflowEngine(),
        agent_factory=AgentFactory(setup_registry),
        run_repository=setup_repository,
    )

    with pytest.raises(SystemExit):
        setup.run("user-1", "Find a job", {"actions": ["job_search"]})
    row = setup_database.connection.execute(
        "SELECT run_id FROM career_runs"
    ).fetchone()
    assert row is not None
    run_id = str(row[0])
    setup_database.close()

    entered = Event()
    release = Event()
    executed: list[str] = []

    class FastLeaseOrchestrator(CareerOrchestrator):
        LEASE_TTL_SECONDS = 1
        LEASE_HEARTBEAT_SECONDS = 0.2

    class BlockingAgent(FakeAgent):
        def execute(self, context):
            executed.append(context.metadata["career_run_id"])
            entered.set()
            assert release.wait(timeout=5)
            return super().execute(context)

    blocking_agent = BlockingAgent("job_search")

    def make_worker(agent):
        database = SQLiteDatabase(path)
        repository = SQLiteCareerRunRepository(database)
        registry = AgentRegistry()
        registry.register(agent)
        worker = FastLeaseOrchestrator(
            memory_engine=MemoryEngine(),
            workflow_engine=WorkflowEngine(),
            agent_factory=AgentFactory(registry),
            run_repository=repository,
        )
        return database, worker

    def run_first_worker():
        first_db, first = make_worker(blocking_agent)
        try:
            return first.continue_run(run_id)
        finally:
            first_db.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run_first_worker)
        assert entered.wait(timeout=5)

        # Wait beyond the original lease TTL. Heartbeat renewal must keep
        # the live worker's durable ownership intact.
        time.sleep(1.2)

        second_db, second = make_worker(FakeAgent("job_search"))
        try:
            with pytest.raises(CareerRunConflictError, match="leased"):
                second.continue_run(run_id)
        finally:
            second_db.close()

        release.set()
        result = future.result(timeout=5)

    assert result.success is True
    assert executed == [run_id]


def test_execution_lease_heartbeat_retries_transient_sqlite_lock(tmp_path):
    path = str(tmp_path / "running-lease-transient-lock.sqlite")
    setup_database = SQLiteDatabase(path)
    setup_repository = SQLiteCareerRunRepository(setup_database)
    crash_agent = FakeAgent("job_search", error=SystemExit("simulated process death"))
    setup_registry = AgentRegistry()
    setup_registry.register(crash_agent)
    setup = CareerOrchestrator(
        memory_engine=MemoryEngine(),
        workflow_engine=WorkflowEngine(),
        agent_factory=AgentFactory(setup_registry),
        run_repository=setup_repository,
    )

    with pytest.raises(SystemExit):
        setup.run("user-1", "Find a job", {"actions": ["job_search"]})
    row = setup_database.connection.execute(
        "SELECT run_id FROM career_runs"
    ).fetchone()
    assert row is not None
    run_id = str(row[0])
    setup_database.close()

    entered = Event()
    release = Event()
    renew_attempts = 0

    class FastLeaseOrchestrator(CareerOrchestrator):
        LEASE_TTL_SECONDS = 1
        LEASE_HEARTBEAT_SECONDS = 0.1

    class FlakyHeartbeatRepository(SQLiteCareerRunRepository):
        def renew_lease(self, requested_run_id, owner_id, *, ttl_seconds):
            nonlocal renew_attempts
            renew_attempts += 1
            if renew_attempts == 1:
                raise sqlite3.OperationalError("database is locked")
            return super().renew_lease(
                requested_run_id, owner_id, ttl_seconds=ttl_seconds
            )

    class BlockingAgent(FakeAgent):
        def execute(self, context):
            entered.set()
            assert release.wait(timeout=5)
            return super().execute(context)

    def run_first():
        first_db = SQLiteDatabase(path)
        first_repo = FlakyHeartbeatRepository(first_db)
        first_registry = AgentRegistry()
        first_registry.register(BlockingAgent("job_search"))
        first = FastLeaseOrchestrator(
            memory_engine=MemoryEngine(),
            workflow_engine=WorkflowEngine(),
            agent_factory=AgentFactory(first_registry),
            run_repository=first_repo,
        )
        try:
            return first.continue_run(run_id)
        finally:
            first_db.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run_first)
        assert entered.wait(timeout=5)
        time.sleep(1.15)

        second_db = SQLiteDatabase(path)
        second_repo = SQLiteCareerRunRepository(second_db)
        second_registry = AgentRegistry()
        second_registry.register(FakeAgent("job_search"))
        second = FastLeaseOrchestrator(
            memory_engine=MemoryEngine(),
            workflow_engine=WorkflowEngine(),
            agent_factory=AgentFactory(second_registry),
            run_repository=second_repo,
        )
        try:
            with pytest.raises(CareerRunConflictError, match="leased"):
                second.continue_run(run_id)
        finally:
            second_db.close()

        release.set()
        result = future.result(timeout=5)

    assert result.success is True
    assert renew_attempts >= 2


def test_anonymous_temporary_sqlite_disables_background_lease_renewal():
    database = SQLiteDatabase("")
    repository = SQLiteCareerRunRepository(database)

    assert repository.supports_background_lease_renewal is False

    database.close()


def test_anonymous_temporary_sqlite_run_does_not_open_separate_heartbeat_database():
    database = SQLiteDatabase("")
    repository = SQLiteCareerRunRepository(database)

    class FastLeaseOrchestrator(CareerOrchestrator):
        LEASE_TTL_SECONDS = 1
        LEASE_HEARTBEAT_SECONDS = 0.1

    class SlowAgent(FakeAgent):
        def execute(self, context):
            time.sleep(0.25)
            return super().execute(context)

    registry = AgentRegistry()
    registry.register(SlowAgent("job_search"))
    orchestrator = FastLeaseOrchestrator(
        memory_engine=MemoryEngine(),
        workflow_engine=WorkflowEngine(),
        agent_factory=AgentFactory(registry),
        run_repository=repository,
    )

    result = orchestrator.run(
        "user-1",
        "Find a job",
        {"actions": ["job_search"]},
    )

    assert result.success is True
    database.close()

def test_continue_run_rejects_paused_human_gate(tmp_path):
    path = str(tmp_path / "paused-not-running.sqlite")
    database = SQLiteDatabase(path)
    repository = SQLiteCareerRunRepository(database)
    gated = FakeAgent(
        "job_application",
        result=AgentResult(
            success=True,
            agent_id="job_application",
            metadata={"requires_human": True},
        ),
    )
    registry = AgentRegistry()
    registry.register(gated)
    orchestrator = CareerOrchestrator(
        memory_engine=MemoryEngine(),
        workflow_engine=WorkflowEngine(),
        agent_factory=AgentFactory(registry),
        run_repository=repository,
    )
    paused = orchestrator.run(
        "user-1",
        "Apply now",
        {"actions": ["job_application"]},
    )

    restarted = CareerOrchestrator(
        memory_engine=MemoryEngine(),
        workflow_engine=WorkflowEngine(),
        agent_factory=AgentFactory(AgentRegistry()),
        run_repository=repository,
    )
    with pytest.raises(RuntimeError, match="running"):
        restarted.continue_run(paused.run_id)
    database.close()


def test_resume_without_explicit_approval_does_not_cross_human_gate():
    gated = FakeAgent(
        "job_application",
        result=AgentResult(
            success=True,
            agent_id="job_application",
            metadata={"requires_human": True},
        ),
    )
    next_agent = FakeAgent("resume")
    orchestrator = make_orchestrator(agents=(gated, next_agent))
    paused = orchestrator.run(
        "user-1",
        "Apply then continue",
        {"actions": ["job_application", "resume"]},
    )

    with pytest.raises(PermissionError, match="affirmative human approval"):
        orchestrator.resume(paused.run_id)

    assert orchestrator._workflow.workflow is not None
    assert orchestrator._workflow.workflow.status == WorkflowState.PAUSED
    assert next_agent.last_context is None


@pytest.mark.parametrize(
    "human_result",
    [
        False,
        {"approved": False},
        {"approved": None},
        {"decision": "approve"},
        "approved",
    ],
)
def test_resume_rejects_non_affirmative_human_results(human_result):
    gated = FakeAgent(
        "job_application",
        result=AgentResult(
            success=True,
            agent_id="job_application",
            metadata={"requires_human": True},
        ),
    )
    next_agent = FakeAgent("resume")
    orchestrator = make_orchestrator(agents=(gated, next_agent))
    paused = orchestrator.run(
        "user-1",
        "Apply then continue",
        {"actions": ["job_application", "resume"]},
    )

    with pytest.raises(PermissionError, match="affirmative human approval"):
        orchestrator.resume(paused.run_id, human_result=human_result)

    assert orchestrator._workflow.workflow is not None
    assert orchestrator._workflow.workflow.status == WorkflowState.PAUSED
    assert next_agent.last_context is None


def test_resume_accepts_boolean_true_as_explicit_approval():
    gated = FakeAgent(
        "job_application",
        result=AgentResult(
            success=True,
            agent_id="job_application",
            metadata={"requires_human": True},
        ),
    )
    next_agent = FakeAgent("resume")
    orchestrator = make_orchestrator(agents=(gated, next_agent))
    paused = orchestrator.run(
        "user-1",
        "Apply then continue",
        {"actions": ["job_application", "resume"]},
    )

    resumed = orchestrator.resume(paused.run_id, human_result=True)

    assert resumed.success is True
    assert next_agent.last_context is not None
    assert next_agent.last_context.payload["human_result"] is True


def test_completed_run_finalization_can_retry_after_memory_failure(tmp_path):
    path = str(tmp_path / "completed-finalization-retry.sqlite")
    database = SQLiteDatabase(path)
    repository = SQLiteCareerRunRepository(database)

    class FailOnceMemoryRepository(InMemoryMemoryRepository):
        def __init__(self):
            super().__init__()
            self.fail_once = True

        def save(self, record):
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("memory temporarily unavailable")
            return super().save(record)

    memory_repository = FailOnceMemoryRepository()
    memory = MemoryEngine(memory_repository)
    agent = FakeAgent("job_search")
    registry = AgentRegistry()
    registry.register(agent)
    orchestrator = CareerOrchestrator(
        memory_engine=memory,
        workflow_engine=WorkflowEngine(),
        agent_factory=AgentFactory(registry),
        run_repository=repository,
    )

    with pytest.raises(RuntimeError, match="memory temporarily unavailable"):
        orchestrator.run("user-1", "Find a job")

    row = database.connection.execute(
        "SELECT run_id FROM career_runs"
    ).fetchone()
    assert row is not None
    run_id = str(row[0])
    durable = repository.get(run_id)
    assert durable is not None
    assert durable.workflow_engine.workflow is not None
    assert durable.workflow_engine.workflow.status == WorkflowState.COMPLETED
    assert agent.last_context is not None

    restarted = CareerOrchestrator(
        memory_engine=memory,
        workflow_engine=WorkflowEngine(),
        agent_factory=AgentFactory(AgentRegistry()),
        run_repository=repository,
    )
    result = restarted.continue_run(run_id)

    assert result.success is True
    assert tuple(step.action for step in result.steps) == ("job_search",)
    summary = memory.get(f"career_run:user-1:{run_id}", user_id="user-1")
    assert summary is not None
    assert summary.value["success"] is True
    assert repository.get(run_id) is None
    database.close()
