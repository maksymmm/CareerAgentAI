"""Verified registry publication identity for candidate release artifacts."""

from __future__ import annotations

import json
import re
import hashlib
import ipaddress
from dataclasses import dataclass

from career_agent_ai.application.release.promotion_manifest import (
    PromotionManifest,
    PromotionManifestError,
)

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_HOST_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
_HOST = rf"(?P<host>(?:{_HOST_LABEL})(?:\.(?:{_HOST_LABEL}))*|\[[0-9A-Fa-f:]+\])"
_PATH_COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
_REGISTRY_REFERENCE = re.compile(
    rf"(?P<repository>{_HOST}(?::[0-9]+)?"
    rf"/(?P<repository_path>(?:{_PATH_COMPONENT})(?:/(?:{_PATH_COMPONENT}))*))@"
    r"(?P<digest>sha256:[0-9a-f]{64})\Z"
)


@dataclass(frozen=True, slots=True)
class ProvenanceStatement:
    """Minimal canonical provenance subject verified before receipt issuance."""

    source_commit: str
    subject_reference: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise PromotionManifestError("unsupported provenance statement version")
        if not _COMMIT.fullmatch(self.source_commit):
            raise PromotionManifestError("provenance source must be a lowercase commit SHA")
        _parse_registry_reference(self.subject_reference, "provenance subject")

    def to_json(self) -> str:
        """Serialize the provenance statement as canonical newline-terminated JSON."""

        return json.dumps(
            {
                "schema_version": self.schema_version,
                "source_commit": self.source_commit,
                "subject_reference": self.subject_reference,
            },
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"

    @classmethod
    def from_json(cls, value: str) -> "ProvenanceStatement":
        """Parse and validate an exact canonical provenance JSON document."""

        try:
            payload = json.loads(value, object_pairs_hook=_reject_duplicate_keys)
        except (json.JSONDecodeError, TypeError) as exc:
            raise PromotionManifestError("invalid provenance statement JSON") from exc
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version", "source_commit", "subject_reference"
        }:
            raise PromotionManifestError("provenance statement fields do not match the schema")
        if type(payload["schema_version"]) is not int or not all(
            isinstance(payload[key], str) for key in ("source_commit", "subject_reference")
        ):
            raise PromotionManifestError("provenance statement contains invalid field types")
        statement = cls(**payload)
        if statement.to_json() != value:
            raise PromotionManifestError("provenance statement JSON is not canonical")
        return statement


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
        _parse_registry_reference(self.registry_reference, "registry reference")
        for value in (self.promotion_manifest_sha256, self.provenance_sha256):
            if not _SHA256.fullmatch(value):
                raise PromotionManifestError("receipt evidence must use lowercase SHA-256 digests")

    @property
    def image_digest(self) -> str:
        """Return the immutable registry manifest digest."""

        match = _parse_registry_reference(self.registry_reference, "registry reference")
        return match.group("digest")

    def verify_deployment_reference(self, reference: str) -> None:
        """Reject tags, alternate repositories, and any digest substitution."""

        if reference != self.registry_reference:
            raise PromotionManifestError(
                "deployment reference must exactly match the verified registry reference"
            )

    def to_json(self) -> str:
        """Serialize the publication receipt as canonical newline-terminated JSON."""

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
    def from_json(
        cls,
        value: str,
        *,
        manifest: PromotionManifest,
        expected_repository: str,
        provenance_json: str,
    ) -> "RegistryPublicationReceipt":
        """Parse a canonical receipt and revalidate it against trusted evidence."""

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
        expected = create_registry_publication_receipt(
            manifest,
            registry_reference=receipt.registry_reference,
            expected_repository=expected_repository,
            provenance_json=provenance_json,
        )
        if receipt != expected:
            raise PromotionManifestError("registry receipt does not match trusted evidence")
        return receipt


def create_registry_publication_receipt(
    manifest: PromotionManifest,
    *,
    registry_reference: str,
    expected_repository: str,
    provenance_json: str,
) -> RegistryPublicationReceipt:
    """Bind a validated pre-promotion manifest to registry and provenance identities."""

    if manifest.publication_status != "unpublished":
        raise PromotionManifestError("publication must start from unpublished evidence")
    match = _parse_registry_reference(registry_reference, "registry reference")
    if match.group("repository") != expected_repository:
        raise PromotionManifestError("registry reference does not match the trusted repository")
    provenance = ProvenanceStatement.from_json(provenance_json)
    if provenance.source_commit != manifest.source_commit:
        raise PromotionManifestError("provenance source does not match the promotion manifest")
    if provenance.subject_reference != registry_reference:
        raise PromotionManifestError("provenance subject does not match the registry reference")
    manifest_digest = hashlib.sha256(manifest.to_json().encode("ascii")).hexdigest()
    provenance_digest = hashlib.sha256(provenance_json.encode("ascii")).hexdigest()
    return RegistryPublicationReceipt(
        source_commit=manifest.source_commit,
        registry_reference=registry_reference,
        promotion_manifest_sha256=f"sha256:{manifest_digest}",
        provenance_sha256=f"sha256:{provenance_digest}",
    )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PromotionManifestError("registry receipt contains duplicate fields")
        result[key] = value
    return result


def _parse_registry_reference(value: str, label: str) -> re.Match[str]:
    match = _REGISTRY_REFERENCE.fullmatch(value)
    if match is None:
        raise PromotionManifestError(f"{label} must be an immutable SHA-256 registry reference")
    if len(match.group("repository_path")) > 255:
        raise PromotionManifestError(f"{label} repository exceeds 255 characters")
    host = match.group("host")
    repository_path = match.group("repository_path")
    if host.lower() == "index.docker.io":
        raise PromotionManifestError(f"{label} uses a noncanonical Docker Hub host")
    if host.lower() == "docker.io" and "/" not in repository_path:
        raise PromotionManifestError(f"{label} must include the Docker Hub library namespace")
    if host.startswith("["):
        try:
            ipaddress.IPv6Address(host[1:-1])
        except ipaddress.AddressValueError as exc:
            raise PromotionManifestError(f"{label} contains an invalid IPv6 host") from exc
    else:
        repository_suffix = match.group("repository")[len(host):]
        if (
            "." not in host
            and host != "localhost"
            and host == host.lower()
            and not repository_suffix.startswith(":")
        ):
            raise PromotionManifestError(f"{label} contains an ambiguous registry host")
    return match
