"""Credential-free promotion and rollback planning for verified candidates."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from career_agent_ai.application.release.promotion_manifest import (
    PromotionManifest,
    PromotionManifestError,
)
from career_agent_ai.application.release.registry_publication import (
    RegistryPublicationReceipt,
    _parse_registry_reference,
)

_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class DryRunPromotionPlan:
    """Canonical, non-executable transition between immutable image identities."""

    source_commit: str
    target_reference: str
    rollback_reference: str
    publication_receipt_sha256: str
    environment: str = "non-production"
    execution_mode: str = "dry-run"
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise PromotionManifestError("unsupported promotion plan version")
        if self.environment != "non-production":
            raise PromotionManifestError("promotion plan must remain non-production")
        if self.execution_mode != "dry-run":
            raise PromotionManifestError("promotion plan must remain non-executable")
        if not _COMMIT.fullmatch(self.source_commit):
            raise PromotionManifestError("source commit must be a lowercase 40-byte SHA")
        target = _parse_registry_reference(self.target_reference, "promotion target")
        rollback = _parse_registry_reference(self.rollback_reference, "rollback target")
        if target.group("repository") != rollback.group("repository"):
            raise PromotionManifestError("promotion and rollback must use the same repository")
        if self.target_reference == self.rollback_reference:
            raise PromotionManifestError("rollback target must differ from promotion target")
        if not _SHA256.fullmatch(self.publication_receipt_sha256):
            raise PromotionManifestError("promotion plan must bind a lowercase receipt digest")

    def verify_target_reference(self, reference: str) -> None:
        """Require an exact digest-only match before a future promotion adapter runs."""

        if reference != self.target_reference:
            raise PromotionManifestError("promotion target must exactly match the verified plan")

    def verify_rollback_reference(self, reference: str) -> None:
        """Require an exact digest-only match before a future rollback adapter runs."""

        if reference != self.rollback_reference:
            raise PromotionManifestError("rollback target must exactly match the verified plan")

    def to_json(self) -> str:
        """Serialize the plan as canonical newline-terminated JSON."""

        return json.dumps(
            {
                "environment": self.environment,
                "execution_mode": self.execution_mode,
                "publication_receipt_sha256": self.publication_receipt_sha256,
                "rollback_reference": self.rollback_reference,
                "schema_version": self.schema_version,
                "source_commit": self.source_commit,
                "target_reference": self.target_reference,
            },
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"

    @classmethod
    def from_json(
        cls,
        value: str,
        *,
        manifest: PromotionManifest,
        expected_repository: str,
        provenance_json: str,
        receipt_json: str,
        current_reference: str,
    ) -> "DryRunPromotionPlan":
        """Parse a canonical plan and rebuild it from all trusted evidence."""

        try:
            payload = json.loads(value, object_pairs_hook=_reject_duplicate_keys)
        except (json.JSONDecodeError, TypeError) as exc:
            raise PromotionManifestError("invalid promotion plan JSON") from exc
        expected_fields = {
            "environment",
            "execution_mode",
            "publication_receipt_sha256",
            "rollback_reference",
            "schema_version",
            "source_commit",
            "target_reference",
        }
        if not isinstance(payload, dict) or set(payload) != expected_fields:
            raise PromotionManifestError("promotion plan fields do not match the schema")
        if type(payload["schema_version"]) is not int or not all(
            isinstance(payload[key], str) for key in expected_fields - {"schema_version"}
        ):
            raise PromotionManifestError("promotion plan contains invalid field types")
        plan = cls(**payload)
        if plan.to_json() != value:
            raise PromotionManifestError("promotion plan JSON is not canonical")
        expected = create_dry_run_promotion_plan(
            manifest=manifest,
            expected_repository=expected_repository,
            provenance_json=provenance_json,
            receipt_json=receipt_json,
            current_reference=current_reference,
        )
        if plan != expected:
            raise PromotionManifestError("promotion plan does not match trusted evidence")
        return plan


def create_dry_run_promotion_plan(
    *,
    manifest: PromotionManifest,
    expected_repository: str,
    provenance_json: str,
    receipt_json: str,
    current_reference: str,
) -> DryRunPromotionPlan:
    """Build a dry-run plan after revalidating its complete publication chain."""

    receipt = RegistryPublicationReceipt.from_json(
        receipt_json,
        manifest=manifest,
        expected_repository=expected_repository,
        provenance_json=provenance_json,
    )
    current = _parse_registry_reference(current_reference, "current deployment")
    if current.group("repository") != expected_repository:
        raise PromotionManifestError("current deployment does not match the trusted repository")
    receipt_digest = hashlib.sha256(receipt_json.encode("ascii")).hexdigest()
    return DryRunPromotionPlan(
        source_commit=receipt.source_commit,
        target_reference=receipt.registry_reference,
        rollback_reference=current_reference,
        publication_receipt_sha256=f"sha256:{receipt_digest}",
    )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PromotionManifestError("promotion plan contains duplicate fields")
        result[key] = value
    return result
