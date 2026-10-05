"""Career orchestration and decision-making primitives."""

from .application_submission import (
    ApplicationSubmissionAdapter,
    ApplicationSubmissionService,
    FakeApplicationSubmissionAdapter,
    PreSubmissionError,
)
from .approval_contract import (
    ApprovalDecision,
    CandidateApprovalPrompt,
    CandidateApprovalSubmission,
    build_candidate_approval_prompt,
    validate_candidate_approval_submission,
)
from .autonomous_career_loop import AutonomousCareerLoop
from .autonomous_loop_models import (
    CareerLoopPhase,
    CareerLoopRequest,
    CareerLoopResult,
    HumanActionEvent,
    HumanActionKind,
)
from .autonomous_loop_repository import CareerLoopConflictError, CareerLoopRepository
from .career_orchestrator import CareerOrchestrator, CareerRunResult
from .career_plan import CareerPlan, CareerPlanStep
from .career_step_result import CareerStepResult

__all__ = [
    "ApprovalDecision",
    "ApplicationSubmissionAdapter",
    "ApplicationSubmissionService",
    "AutonomousCareerLoop",
    "CareerLoopPhase",
    "CareerLoopConflictError",
    "CareerLoopRepository",
    "CareerLoopRequest",
    "CareerLoopResult",
    "CareerOrchestrator",
    "CareerPlan",
    "CareerPlanStep",
    "CareerRunResult",
    "CareerStepResult",
    "CandidateApprovalPrompt",
    "CandidateApprovalSubmission",
    "FakeApplicationSubmissionAdapter",
    "HumanActionEvent",
    "HumanActionKind",
    "PreSubmissionError",
    "build_candidate_approval_prompt",
    "validate_candidate_approval_submission",
]
