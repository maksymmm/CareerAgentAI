"""Verified registry publication identity for candidate release artifacts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from career_agent_ai.application.release.promotion_manifest import (
    PromotionManifest,
    PromotionManifestError,
)

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_REGISTRY_REFERENCE = re.compile(
    r"(?P<repository>[a-z0-9.-]+(?::[0-9]+)?/[a-z0-9._/-]+)@"
    r"(?P<digest>sha256:[0-9a-f]{64})\Z"
)


@dataclass(frozen=True, slots=True)
class RegistryPublicationReceipt:
    """Canonical proof that reviewed evidence names one immutable registry image."""

    source_commit: str
    registry_reference: str
    promotion_manifest_sha256: str
    provenance_sha256: str
    environment: str = "non-production"
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise PromotionManifestError("unsupported registry receipt version")
        if self.environment != "non-production":
            raise PromotionManifestError("registry receipt must remain non-production")
        if not _COMMIT.fullmatch(self.source_commit):
            raise PromotionManifestError("source commit must be a lowercase 40-byte SHA")
        if not _REGISTRY_REFERENCE.fullmatch(self.registry_reference):
            raise PromotionManifestError("registry reference must use an immutable SHA-256 digest")
        for value in (self.promotion_manifest_sha256, self.provenance_sha256):
            if not _SHA256.fullmatch(value):
                raise PromotionManifestError("receipt evidence must use lowercase SHA-256 digests")

    @property
    def image_digest(self) -> str:
        """Return the immutable registry manifest digest."""

        match = _REGISTRY_REFERENCE.fullmatch(self.registry_reference)
        assert match is not None
        return match.group("digest")

    def verify_deployment_reference(self, reference: str) -> None:
        """Reject tags, alternate repositories, and any digest substitution."""

        if reference != self.registry_reference:
            raise PromotionManifestError(
                "deployment reference must exactly match the verified registry reference"
            )

    def to_json(self) -> str:
        payload = {
            "environment": self.environment,
            "promotion_manifest_sha256": self.promotion_manifest_sha256,
            "provenance_sha256": self.provenance_sha256,
            "registry_reference": self.registry_reference,
            "schema_version": self.schema_version,
            "source_commit": self.source_commit,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"

    @classmethod
    def from_json(cls, value: str) -> "RegistryPublicationReceipt":
        try:
            payload = json.loads(value, object_pairs_hook=_reject_duplicate_keys)
        except (json.JSONDecodeError, TypeError) as exc:
            raise PromotionManifestError("invalid registry receipt JSON") from exc
        expected = {
            "environment",
            "promotion_manifest_sha256",
            "provenance_sha256",
            "registry_reference",
            "schema_version",
            "source_commit",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise PromotionManifestError("registry receipt fields do not match the schema")
        if type(payload["schema_version"]) is not int or not all(
            isinstance(payload[key], str) for key in expected - {"schema_version"}
        ):
            raise PromotionManifestError("registry receipt contains invalid field types")
        receipt = cls(**payload)
        if receipt.to_json() != value:
            raise PromotionManifestError("registry receipt JSON is not canonical")
        return receipt


def create_registry_publication_receipt(
    manifest: PromotionManifest,
    *,
    registry_reference: str,
    promotion_manifest_sha256: str,
    provenance_sha256: str,
) -> RegistryPublicationReceipt:
    """Bind a validated pre-promotion manifest to registry and provenance identities."""

    if manifest.publication_status != "unpublished":
        raise PromotionManifestError("publication must start from unpublished evidence")
    return RegistryPublicationReceipt(
        source_commit=manifest.source_commit,
        registry_reference=registry_reference,
        promotion_manifest_sha256=promotion_manifest_sha256,
        provenance_sha256=provenance_sha256,
    )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PromotionManifestError("registry receipt contains duplicate fields")
        result[key] = value
    return result
