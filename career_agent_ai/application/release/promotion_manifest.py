"""Strict digest-only promotion and rollback manifest contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_ARTIFACT_NAMES = (
    "candidate-image-digest.txt",
    "candidate-sbom.spdx.json",
    "candidate-vulnerabilities.json",
    "source-commit.txt",
)


class PromotionManifestError(ValueError):
    """Raised when release evidence cannot form a safe promotion manifest."""


@dataclass(frozen=True, slots=True)
class PromotionManifest:
    """Immutable descriptor binding promotion and rollback to reviewed evidence."""

    source_commit: str
    image_digest: str
    evidence_sha256: Mapping[str, str]
    rollback_image_digest: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise PromotionManifestError("unsupported promotion manifest version")
        if not _COMMIT.fullmatch(self.source_commit):
            raise PromotionManifestError("source commit must be a lowercase 40-byte SHA")
        _validate_digest(self.image_digest, "image digest")
        if self.rollback_image_digest is not None:
            _validate_digest(self.rollback_image_digest, "rollback image digest")
            if self.rollback_image_digest == self.image_digest:
                raise PromotionManifestError("rollback digest must differ from candidate digest")
        expected = set(_ARTIFACT_NAMES)
        actual = set(self.evidence_sha256)
        if actual != expected:
            raise PromotionManifestError("evidence checksums must cover the exact release set")
        checksums = dict(self.evidence_sha256)
        for name, digest in checksums.items():
            if Path(name).name != name or not _SHA256.fullmatch(digest):
                raise PromotionManifestError("invalid evidence checksum entry")
        object.__setattr__(self, "evidence_sha256", MappingProxyType(checksums))

    def to_json(self) -> str:
        """Return the canonical JSON representation used for checksum and review."""

        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "source_commit": self.source_commit,
            "candidate_image_digest": self.image_digest,
            "evidence_sha256": dict(sorted(self.evidence_sha256.items())),
        }
        if self.rollback_image_digest is not None:
            payload["rollback_image_digest"] = self.rollback_image_digest
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"

    @classmethod
    def from_json(cls, value: str) -> "PromotionManifest":
        """Parse strict canonical promotion-manifest JSON."""

        try:
            payload = json.loads(value, object_pairs_hook=_reject_duplicate_keys)
        except (json.JSONDecodeError, TypeError) as exc:
            raise PromotionManifestError("invalid promotion manifest JSON") from exc
        if not isinstance(payload, dict):
            raise PromotionManifestError("promotion manifest must be an object")
        allowed = {
            "schema_version",
            "source_commit",
            "candidate_image_digest",
            "rollback_image_digest",
            "evidence_sha256",
        }
        if set(payload) - allowed:
            raise PromotionManifestError("promotion manifest contains unknown fields")
        required = allowed - {"rollback_image_digest"}
        if not required.issubset(payload):
            raise PromotionManifestError("promotion manifest is missing required fields")
        checksums = payload["evidence_sha256"]
        if not isinstance(checksums, dict) or not all(
            isinstance(key, str) and isinstance(item, str)
            for key, item in checksums.items()
        ):
            raise PromotionManifestError("evidence checksums must be a string mapping")
        if type(payload["schema_version"]) is not int:
            raise PromotionManifestError("schema version must be an integer")
        source = payload["source_commit"]
        image = payload["candidate_image_digest"]
        rollback = payload.get("rollback_image_digest")
        if not isinstance(source, str) or not isinstance(image, str):
            raise PromotionManifestError("source and image digests must be strings")
        if rollback is not None and not isinstance(rollback, str):
            raise PromotionManifestError("rollback image digest must be a string")
        manifest = cls(
            schema_version=payload["schema_version"],
            source_commit=source,
            image_digest=image,
            rollback_image_digest=rollback,
            evidence_sha256=checksums,
        )
        if manifest.to_json() != value:
            raise PromotionManifestError("promotion manifest JSON is not canonical")
        return manifest


def create_promotion_manifest(
    evidence_directory: Path,
    *,
    rollback_image_digest: str | None = None,
) -> PromotionManifest:
    """Build a strict manifest from the complete validated release evidence set."""

    checksums: dict[str, str] = {}
    for name in _ARTIFACT_NAMES:
        path = evidence_directory / name
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise PromotionManifestError(f"missing release evidence: {name}") from exc
        if not content:
            raise PromotionManifestError(f"empty release evidence: {name}")
        checksums[name] = f"sha256:{hashlib.sha256(content).hexdigest()}"
    source = _single_line(evidence_directory / "source-commit.txt", "source commit")
    image = _single_line(
        evidence_directory / "candidate-image-digest.txt", "image digest"
    )
    return PromotionManifest(
        source_commit=source,
        image_digest=image,
        rollback_image_digest=rollback_image_digest,
        evidence_sha256=checksums,
    )


def _single_line(path: Path, label: str) -> str:
    try:
        value = path.read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise PromotionManifestError(f"invalid {label} evidence") from exc
    if not value.endswith("\n") or "\n" in value[:-1] or "\r" in value:
        raise PromotionManifestError(f"{label} evidence must contain one line")
    return value[:-1]


def _validate_digest(value: str, label: str) -> None:
    if not _SHA256.fullmatch(value):
        raise PromotionManifestError(f"{label} must be a lowercase SHA-256 digest")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PromotionManifestError("promotion manifest contains duplicate fields")
        result[key] = value
    return result
