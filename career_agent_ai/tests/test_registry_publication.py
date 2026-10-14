from __future__ import annotations

import hashlib

import pytest

from career_agent_ai.application.release import (
    PromotionManifest,
    PromotionManifestError,
    RegistryPublicationReceipt,
    create_registry_publication_receipt,
)

SOURCE = "a" * 40
DIGEST = "sha256:" + "b" * 64
REFERENCE = "ghcr.io/maksymmm/careeragentai-candidate@" + DIGEST
EVIDENCE = {name: DIGEST for name in (
    "candidate-image-digest.txt", "candidate-sbom.spdx.json",
    "candidate-vulnerabilities.json", "source-commit.txt",
)}


def _manifest() -> PromotionManifest:
    return PromotionManifest(source_commit=SOURCE, local_image_id=DIGEST, evidence_sha256=EVIDENCE)


def _receipt() -> RegistryPublicationReceipt:
    manifest = _manifest()
    manifest_digest = "sha256:" + hashlib.sha256(manifest.to_json().encode()).hexdigest()
    return create_registry_publication_receipt(
        manifest,
        registry_reference=REFERENCE,
        promotion_manifest_sha256=manifest_digest,
        provenance_sha256="sha256:" + "c" * 64,
    )


def test_receipt_binds_source_registry_manifest_and_provenance():
    receipt = _receipt()

    assert receipt.source_commit == SOURCE
    assert receipt.image_digest == DIGEST
    assert receipt.environment == "non-production"
    assert RegistryPublicationReceipt.from_json(receipt.to_json()) == receipt
    receipt.verify_deployment_reference(REFERENCE)


@pytest.mark.parametrize("reference", [
    "ghcr.io/maksymmm/careeragentai-candidate:latest",
    "ghcr.io/maksymmm/careeragentai-candidate@sha256:" + "B" * 64,
    "https://ghcr.io/maksymmm/careeragentai-candidate@" + DIGEST,
    "ghcr.io/Maksymmm/careeragentai-candidate@" + DIGEST,
])
def test_receipt_rejects_mutable_or_noncanonical_registry_identity(reference):
    with pytest.raises(PromotionManifestError, match="immutable"):
        create_registry_publication_receipt(
            _manifest(), registry_reference=reference,
            promotion_manifest_sha256=DIGEST, provenance_sha256=DIGEST,
        )


@pytest.mark.parametrize("reference", [
    "ghcr.io/maksymmm/careeragentai-candidate:latest",
    "ghcr.io/maksymmm/other@" + DIGEST,
    "ghcr.io/maksymmm/careeragentai-candidate@sha256:" + "d" * 64,
])
def test_deployment_must_use_exact_verified_reference(reference):
    with pytest.raises(PromotionManifestError, match="exactly match"):
        _receipt().verify_deployment_reference(reference)


@pytest.mark.parametrize("mutator", [
    lambda value: value.replace('"schema_version":1', '"schema_version":true'),
    lambda value: value.replace('"schema_version":1', '"schema_version":2'),
    lambda value: value.replace('{', '{"unknown":1,', 1),
    lambda value: value.replace('"source_commit"', '"source_commit":"' + SOURCE + '","source_commit"'),
    lambda value: value.replace('{', '{\n', 1),
])
def test_receipt_parser_rejects_untrusted_or_noncanonical_json(mutator):
    with pytest.raises(PromotionManifestError):
        RegistryPublicationReceipt.from_json(mutator(_receipt().to_json()))


def test_receipt_rejects_production_environment():
    with pytest.raises(PromotionManifestError, match="non-production"):
        RegistryPublicationReceipt(
            source_commit=SOURCE, registry_reference=REFERENCE,
            promotion_manifest_sha256=DIGEST, provenance_sha256=DIGEST,
            environment="production",
        )
