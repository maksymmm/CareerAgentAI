from dataclasses import FrozenInstanceError

import pytest

from career_agent_ai.application.workflow.workflow import Workflow
from career_agent_ai.application.workflow.workflow_engine import WorkflowEngine
from career_agent_ai.application.workflow.workflow_state import WorkflowState
from career_agent_ai.application.workflow.workflow_step import WorkflowStep


def make_workflow() -> Workflow:
    return Workflow(
        workflow_id="wf-001",
        name="Job Search",
        description="Demo workflow",
        steps=(
            WorkflowStep(
                id="step-1",
                name="Search Jobs",
                order=1,
                task="SearchJobs",
            ),
            WorkflowStep(
                id="step-2",
                name="Prepare Resume",
                order=2,
                task="PrepareResume",
            ),
        ),
    )


def test_workflow_creation():
    workflow = make_workflow()

    assert workflow.workflow_id == "wf-001"
    assert workflow.current_step == 0
    assert workflow.status == WorkflowState.PENDING
    assert len(workflow.steps) == 2


def test_workflow_is_immutable():
    workflow = make_workflow()

    with pytest.raises(FrozenInstanceError):
        workflow.name = "Changed"


def test_engine_start():
    engine = WorkflowEngine()

    started = engine.start(make_workflow())

    assert started.status == WorkflowState.RUNNING


def test_next_step():
    engine = WorkflowEngine()

    engine.start(make_workflow())

    workflow = engine.next_step()

    assert workflow.current_step == 1


def test_complete_step():
    engine = WorkflowEngine()

    engine.start(make_workflow())

    engine.next_step()

    workflow = engine.complete_step()

    assert workflow.status == WorkflowState.COMPLETED


def test_fail_step():
    engine = WorkflowEngine()

    engine.start(make_workflow())

    workflow = engine.fail_step()

    assert workflow.status == WorkflowState.FAILED


def test_pause_resume():
    engine = WorkflowEngine()

    engine.start(make_workflow())

    paused = engine.pause()

    assert paused.status == WorkflowState.PAUSED

    resumed = engine.resume()

    assert resumed.status == WorkflowState.RUNNING


def test_cancel():
    engine = WorkflowEngine()

    engine.start(make_workflow())

    cancelled = engine.cancel()

    assert cancelled.status == WorkflowState.CANCELLED


def test_snapshot():
    engine = WorkflowEngine()

    engine.start(make_workflow())

    snapshot = engine.snapshot()

    assert snapshot.workflow_id == "wf-001"
    assert snapshot.completed_steps == 0


def test_invalid_next_step_without_workflow():
    engine = WorkflowEngine()

    with pytest.raises(RuntimeError):
        engine.next_step()