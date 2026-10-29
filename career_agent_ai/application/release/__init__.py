"""Release promotion contracts for immutable candidate artifacts."""

from career_agent_ai.application.release.promotion_manifest import (
    PromotionManifest,
    PromotionManifestError,
    create_promotion_manifest,
)
from career_agent_ai.application.release.registry_publication import (
    ProvenanceStatement,
    RegistryPublicationReceipt,
    create_registry_publication_receipt,
)
from career_agent_ai.application.release.promotion_plan import (
    DryRunPromotionPlan,
    create_dry_run_promotion_plan,
)
from career_agent_ai.application.release.promotion_execution import (
    DryRunPromotionAdapter,
    PromotionExecutionAdapter,
    PromotionPreExecutionError,
    PromotionExecutionResult,
    PromotionExecutionService,
    validate_promotion_operation_id,
    validate_promotion_plan_payload,
)
from career_agent_ai.application.release.promotion_approval import (
    PromotionApprovalDecision,
    PromotionApprovalPrompt,
    PromotionApprovalSubmission,
    build_promotion_approval_prompt,
    validate_promotion_approval_submission,
)

from career_agent_ai.application.release.promotion_approval_receipt import (
    PromotionApprovalReceiptService,
)

__all__ = [
    "PromotionApprovalReceiptService",
    "PromotionManifest",
    "PromotionManifestError",
    "create_promotion_manifest",
    "RegistryPublicationReceipt",
    "ProvenanceStatement",
    "create_registry_publication_receipt",
    "DryRunPromotionPlan",
    "create_dry_run_promotion_plan",
    "DryRunPromotionAdapter",
    "PromotionExecutionAdapter",
    "PromotionPreExecutionError",
    "PromotionExecutionResult",
    "PromotionExecutionService",
    "validate_promotion_operation_id",
    "validate_promotion_plan_payload",
    "PromotionApprovalDecision",
    "PromotionApprovalPrompt",
    "PromotionApprovalSubmission",
    "build_promotion_approval_prompt",
    "validate_promotion_approval_submission",
]
