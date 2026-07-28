from __future__ import annotations

from dataclasses import replace

from career_agent_ai.application.memory.memory_engine import MemoryEngine
from career_agent_ai.application.workflow.workflow import Workflow
from career_agent_ai.application.workflow.workflow_engine import WorkflowEngine

from .agent_brain_state import AgentBrainState
from .agent_context import AgentContext
from .agent_request import AgentRequest
from .agent_response import AgentResponse


class AgentBrain:
    """
    Central application orchestrator.

    Coordinates MemoryEngine and WorkflowEngine.
    Contains no business logic.
    """

    def __init__(
        self,
        memory_engine: MemoryEngine,
        workflow_engine: WorkflowEngine,
    ) -> None:
        self._memory_engine = memory_engine
        self._workflow_engine = workflow_engine
        self._state = AgentBrainState.IDLE

    @property
    def state(self) -> AgentBrainState:
        return self._state

    def process(self, request: AgentRequest) -> AgentResponse:
        """
        Processes a request using the current memory snapshot and workflow.
        """

        self._state = AgentBrainState.RUNNING

        memory_snapshot = self._memory_engine.load_snapshot()

        context = AgentContext(
            user_id=request.user_id,
            memory_snapshot=memory_snapshot,
            active_workflow=self._workflow_engine.workflow,
        )

        response = AgentResponse(
            request_id=request.request_id,
            success=True,
            workflow_id=(
                context.active_workflow.workflow_id
                if context.active_workflow is not None
                else None
            ),
            messages=("Request processed.",),
            metadata={
                "brain_state": self._state.value,
            },
        )

        self._state = AgentBrainState.IDLE

        return response

    def start_workflow(self, workflow: Workflow) -> Workflow:
        """
        Starts a workflow.
        """
        self._state = AgentBrainState.RUNNING
        workflow = self._workflow_engine.start(workflow)
        self._state = AgentBrainState.IDLE
        return workflow

    def continue_workflow(self) -> Workflow:
        """
        Continues the active workflow.
        """
        self._state = AgentBrainState.RUNNING
        workflow = self._workflow_engine.next_step()
        self._state = AgentBrainState.IDLE
        return workflow

    def pause_workflow(self) -> Workflow:
        """
        Pauses the active workflow.
        """
        return self._workflow_engine.pause()

    def resume_workflow(self) -> Workflow:
        """
        Resumes the active workflow.
        """
        return self._workflow_engine.resume()

    def cancel_workflow(self) -> Workflow:
        """
        Cancels the active workflow.
        """
        return self._workflow_engine.cancel()

    def snapshot(self):
        """
        Returns the current workflow snapshot.
        """
        return self._workflow_engine.snapshot()