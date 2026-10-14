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

__all__ = [
    "PromotionManifest",
    "PromotionManifestError",
    "create_promotion_manifest",
    "RegistryPublicationReceipt",
    "ProvenanceStatement",
    "create_registry_publication_receipt",
]
