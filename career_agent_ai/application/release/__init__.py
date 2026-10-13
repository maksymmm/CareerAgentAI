"""Release promotion contracts for immutable candidate artifacts."""

from career_agent_ai.application.release.promotion_manifest import (
    PromotionManifest,
    PromotionManifestError,
    create_promotion_manifest,
)

__all__ = [
    "PromotionManifest",
    "PromotionManifestError",
    "create_promotion_manifest",
]
