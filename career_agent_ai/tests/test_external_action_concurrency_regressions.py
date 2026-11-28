from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from threading import Barrier, Event, Lock, Thread
from typing import Any, Mapping

import pytest

from career_agent_ai.application.external_actions.external_action_operation import ExternalActionStatus
from career_agent_ai.application.external_actions.external_action_repository import OperationConflictError
from career_agent_ai.application.external_actions.external_action_service import ExternalActionService
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import SQLiteExternalActionOperationRepository


class RecordingAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Mapping[str, Any]]] = []

    def execute(self, operation_id: str, action_type: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((operation_id, action_type, dict(payload)))
        return {"provider_reference": "ref-123"}


def _prepare(path: str, operation_id: str) -> None:
    database = SQLiteDatabase(path)
    repository = SQLiteExternalActionOperationRepository(database)
    ExternalActionService(repository, RecordingAdapter()).prepare(operation_id, "application", {"job": "1"})
    database.close()


def test_live_duplicate_observes_in_progress_without_reclassifying_provider_call(tmp_path):
    path = str(tmp_path / "live-duplicate.sqlite")

    class ReentrantDuplicateAdapter:
        def __init__(self):
            self.calls = 0
            self.duplicate_status = None

        def execute(self, operation_id, action_type, payload):
            self.calls += 1
            duplicate_database = SQLiteDatabase(path)
            try:
                duplicate_repository = SQLiteExternalActionOperationRepository(duplicate_database)
                duplicate_service = ExternalActionService(duplicate_repository, self)
                duplicate = duplicate_service.execute(operation_id, human_approved=True)
                self.duplicate_status = duplicate.status
            finally:
                duplicate_database.close()
            return {"provider_reference": "live-provider-result"}

    database = SQLiteDatabase(path)
    repository = SQLiteExternalActionOperationRepository(database)
    adapter = ReentrantDuplicateAdapter()
    service = ExternalActionService(repository, adapter)
    service.prepare("operation-live", "application", {"job": "1"})
    result = service.execute("operation-live", human_approved=True)

    assert adapter.calls == 1
    assert adapter.duplicate_status == ExternalActionStatus.IN_PROGRESS
    assert result.status == ExternalActionStatus.SUCCEEDED
    assert repository.get("operation-live") == result
    database.close()


def test_expired_in_progress_operation_moves_to_reconciliation_without_provider_call():
    database = SQLiteDatabase()
    repository = SQLiteExternalActionOperationRepository(database)
    adapter = RecordingAdapter()
    service = ExternalActionService(repository, adapter)
    service.prepare("operation-stale", "application", {})
    in_progress = repository.transition("operation-stale", ExternalActionStatus.PREPARED, ExternalActionStatus.IN_PROGRESS)

    recovered = service.execute("operation-stale", human_approved=True, stale_after_seconds=300,
                                now=in_progress.updated_at + timedelta(seconds=300))

    assert recovered.status == ExternalActionStatus.RECONCILIATION_REQUIRED
    assert "ambiguous outcome" in recovered.error
    assert adapter.calls == []
    database.close()


def test_future_in_progress_timestamp_is_conservatively_treated_as_live():
    database = SQLiteDatabase()
    repository = SQLiteExternalActionOperationRepository(database)
    adapter = RecordingAdapter()
    service = ExternalActionService(repository, adapter)
    service.prepare("operation-future", "application", {})
    in_progress = repository.transition("operation-future", ExternalActionStatus.PREPARED, ExternalActionStatus.IN_PROGRESS)

    observed = service.execute("operation-future", human_approved=True,
                               now=in_progress.updated_at - timedelta(seconds=30))

    assert observed.status == ExternalActionStatus.IN_PROGRESS
    assert repository.get("operation-future").status == ExternalActionStatus.IN_PROGRESS
    assert adapter.calls == []
    database.close()


def test_stale_recovery_returns_concurrent_terminal_winner_instead_of_false_conflict():
    class LostStaleRecoveryRaceRepository(SQLiteExternalActionOperationRepository):
        def transition(self, operation_id, expected_status, status, *, result=None, error=None):
            if expected_status == ExternalActionStatus.IN_PROGRESS and status == ExternalActionStatus.RECONCILIATION_REQUIRED:
                super().transition(operation_id, ExternalActionStatus.IN_PROGRESS, ExternalActionStatus.SUCCEEDED,
                                   result={"provider_reference": "completed-by-original-worker"})
                raise OperationConflictError("another worker completed the operation")
            return super().transition(operation_id, expected_status, status, result=result, error=error)

    database = SQLiteDatabase()
    repository = LostStaleRecoveryRaceRepository(database)
    adapter = RecordingAdapter()
    service = ExternalActionService(repository, adapter)
    service.prepare("operation-stale-race", "application", {})
    in_progress = repository.transition("operation-stale-race", ExternalActionStatus.PREPARED, ExternalActionStatus.IN_PROGRESS)

    observed = service.execute("operation-stale-race", human_approved=True,
                               now=in_progress.updated_at + timedelta(minutes=10))

    assert observed.status == ExternalActionStatus.SUCCEEDED
    assert observed.result == {"provider_reference": "completed-by-original-worker"}
    assert repository.get("operation-stale-race") == observed
    assert adapter.calls == []
    database.close()


def test_concurrent_prepared_claim_loser_observes_winner_without_second_provider_call(tmp_path):
    path = str(tmp_path / "prepared-claim-race.sqlite")
    _prepare(path, "operation-prepared-race")
    barrier = Barrier(2)
    provider_started = Event()
    release_provider = Event()
    first_worker_finished = Event()
    finish_lock = Lock()
    result_lock = Lock()
    finish_count = 0
    results = []
    errors = []

    class BlockingAdapter:
        def __init__(self):
            self.calls = []
            self._lock = Lock()

        def execute(self, operation_id, action_type, payload):
            with self._lock:
                self.calls.append((operation_id, action_type, dict(payload)))
            provider_started.set()
            if not release_provider.wait(timeout=10):
                raise TimeoutError("The test provider was not released.")
            return {"provider_reference": "ref-123"}

    class BarrierReadRepository(SQLiteExternalActionOperationRepository):
        def __init__(self, database):
            super().__init__(database)
            self._initial_read = True
            self._initial_read_lock = Lock()

        def get(self, operation_id):
            operation = super().get(operation_id)
            with self._initial_read_lock:
                wait_for_peer = self._initial_read
                self._initial_read = False
            if wait_for_peer and operation is not None and operation.status == ExternalActionStatus.PREPARED:
                barrier.wait(timeout=10)
            return operation

    adapter = BlockingAdapter()

    def worker():
        nonlocal finish_count
        database = SQLiteDatabase(path)
        try:
            repository = BarrierReadRepository(database)
            service = ExternalActionService(repository, adapter)
            result = service.execute("operation-prepared-race", human_approved=True)
            with result_lock:
                results.append(result)
        except BaseException as exc:
            with result_lock:
                errors.append(exc)
        finally:
            database.close()
            with finish_lock:
                finish_count += 1
                if finish_count == 1:
                    first_worker_finished.set()

    workers = [Thread(target=worker), Thread(target=worker)]
    for thread in workers:
        thread.start()
    provider_was_started = provider_started.wait(timeout=10)
    losing_worker_returned = first_worker_finished.wait(timeout=10) if provider_was_started else False
    release_provider.set()
    for thread in workers:
        thread.join(timeout=15)

    assert provider_was_started
    assert losing_worker_returned
    assert all(not thread.is_alive() for thread in workers)
    assert errors == []
    assert len(results) == 2
    assert {result.status for result in results} == {ExternalActionStatus.IN_PROGRESS, ExternalActionStatus.SUCCEEDED}
    assert len(adapter.calls) == 1

    final_database = SQLiteDatabase(path)
    final_repository = SQLiteExternalActionOperationRepository(final_database)
    final_operation = final_repository.get("operation-prepared-race")
    assert final_operation is not None
    assert final_operation.status == ExternalActionStatus.SUCCEEDED
    assert final_operation.result == {"provider_reference": "ref-123"}
    final_database.close()


def test_lost_prepared_claim_rejects_a_concurrently_rebound_payload():
    class ReboundPreparedClaimRepository(SQLiteExternalActionOperationRepository):
        def transition(self, operation_id, expected_status, status, *, result=None, error=None):
            if expected_status == ExternalActionStatus.PREPARED and status == ExternalActionStatus.IN_PROGRESS:
                self.replace_prepared_payload(operation_id, {"job": "1"}, {"job": "2"})
                super().transition(operation_id, ExternalActionStatus.PREPARED, ExternalActionStatus.IN_PROGRESS)
                raise OperationConflictError("another worker claimed a changed intent")
            return super().transition(operation_id, expected_status, status, result=result, error=error)

    database = SQLiteDatabase()
    repository = ReboundPreparedClaimRepository(database)
    adapter = RecordingAdapter()
    service = ExternalActionService(repository, adapter)
    service.prepare("operation-rebound-claim", "application", {"job": "1"})

    with pytest.raises(OperationConflictError, match="changed intent"):
        service.execute("operation-rebound-claim", human_approved=True)

    current = repository.get("operation-rebound-claim")
    assert current is not None
    assert current.status == ExternalActionStatus.IN_PROGRESS
    assert dict(current.payload) == {"job": "2"}
    assert adapter.calls == []
    database.close()


@pytest.mark.parametrize("seconds", [True, 0, -1, 3601, 1.5])
def test_execution_rejects_invalid_stale_grace_without_mutating_operation(seconds):
    database = SQLiteDatabase()
    repository = SQLiteExternalActionOperationRepository(database)
    adapter = RecordingAdapter()
    service = ExternalActionService(repository, adapter)
    service.prepare("operation-invalid-grace", "application", {})

    with pytest.raises(ValueError, match="stale_after_seconds"):
        service.execute("operation-invalid-grace", human_approved=True, stale_after_seconds=seconds)

    assert repository.get("operation-invalid-grace").status == ExternalActionStatus.PREPARED
    assert adapter.calls == []
    database.close()


def test_ci_enforces_independent_coverage_gate_after_full_pytest():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    pytest_step = workflow.index("- name: Run test suite")
    independent_gate = workflow.index("python -m coverage report --precision=2 --fail-under=90")
    assert "id: pytest" in workflow
    assert independent_gate > pytest_step
    assert "if: always() && !cancelled() && steps.pytest.outcome != 'skipped'" in workflow
