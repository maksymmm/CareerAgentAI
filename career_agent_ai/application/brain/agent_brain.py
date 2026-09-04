from __future__ import annotations

from .agent_context import AgentContext
from .agent_request import AgentRequest
from .agent_response import AgentResponse

from career_agent_ai.application.agents.agent_factory import AgentFactory
from career_agent_ai.application.memory.memory_engine import MemoryEngine
from career_agent_ai.application.workflow.workflow import Workflow
from career_agent_ai.application.workflow.workflow_engine import WorkflowEngine


class AgentBrain:
    """
    Central coordinator.
    """

    def __init__(
        self,
        memory_engine: MemoryEngine,
        workflow_engine: WorkflowEngine,
        agent_factory: AgentFactory,
    ) -> None:
        self._memory = memory_engine
        self._workflow = workflow_engine
        self._factory = agent_factory

    def process(
        self,
        request: AgentRequest,
    ) -> AgentResponse:

        context = AgentContext(
            user_id=request.user_id,
            memory_snapshot=self._memory.snapshot(),
            active_workflow=self._workflow.workflow,
            payload=request.payload,
            metadata=request.metadata,
        )

        agent = self._factory.resolve(
            request.action,
        )

        result = agent.execute(context)

        return AgentResponse(
            request_id=request.request_id,
            success=result.success,
            workflow_id=(
                self._workflow.workflow.workflow_id
                if self._workflow.workflow
                else None
            ),
            messages=result.messages,
            metadata=result.metadata,
        )

    def start_workflow(
        self,
        workflow: Workflow,
    ):
        return self._workflow.start(workflow)

    def continue_workflow(self):
        return self._workflow.next_step()

    def pause_workflow(self):
        return self._workflow.pause()

    def resume_workflow(self):
        return self._workflow.resume()

    def cancel_workflow(self):
        return self._workflow.cancel()

    def snapshot(self):
        return self._workflow.snapshot()