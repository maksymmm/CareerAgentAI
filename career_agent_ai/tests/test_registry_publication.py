from __future__ import annotations

import hashlib

import pytest

from career_agent_ai.application.release import (
    PromotionManifest,
    PromotionManifestError,
    ProvenanceStatement,
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
    provenance = ProvenanceStatement(source_commit=SOURCE, subject_reference=REFERENCE)
    return create_registry_publication_receipt(
        manifest,
        registry_reference=REFERENCE,
        expected_repository="ghcr.io/maksymmm/careeragentai-candidate",
        provenance_json=provenance.to_json(),
    )


def test_receipt_binds_source_registry_manifest_and_provenance():
    receipt = _receipt()

    assert receipt.source_commit == SOURCE
    assert receipt.image_digest == DIGEST
    assert receipt.environment == "non-production"
    assert receipt.promotion_manifest_sha256 == "sha256:" + hashlib.sha256(
        _manifest().to_json().encode("ascii")
    ).hexdigest()
    provenance = ProvenanceStatement(source_commit=SOURCE, subject_reference=REFERENCE)
    assert RegistryPublicationReceipt.from_json(
        receipt.to_json(),
        manifest=_manifest(),
        expected_repository="ghcr.io/maksymmm/careeragentai-candidate",
        provenance_json=provenance.to_json(),
    ) == receipt
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
            expected_repository="ghcr.io/maksymmm/careeragentai-candidate",
            provenance_json=ProvenanceStatement(
                source_commit=SOURCE, subject_reference=REFERENCE
            ).to_json(),
        )


@pytest.mark.parametrize("reference", [
    "ghcr.io//maksymmm/image@" + DIGEST,
    "ghcr.io/maksymmm/../image@" + DIGEST,
    "ghcr.io.:443/maksymmm/image@" + DIGEST,
    "ghcr..io/maksymmm/image@" + DIGEST,
    "a.-b/maksymmm/image@" + DIGEST,
    "a-.b/maksymmm/image@" + DIGEST,
])
def test_receipt_rejects_ambiguous_registry_names(reference):
    with pytest.raises(PromotionManifestError, match="immutable"):
        create_registry_publication_receipt(
            _manifest(), registry_reference=reference,
            expected_repository="ghcr.io/maksymmm/careeragentai-candidate",
            provenance_json=ProvenanceStatement(
                source_commit=SOURCE, subject_reference=REFERENCE
            ).to_json(),
        )


def test_receipt_rejects_untrusted_repository_even_with_matching_provenance():
    hostile = "attacker.example/image@" + DIGEST
    with pytest.raises(PromotionManifestError, match="trusted repository"):
        create_registry_publication_receipt(
            _manifest(), registry_reference=hostile,
            expected_repository="ghcr.io/maksymmm/careeragentai-candidate",
            provenance_json=ProvenanceStatement(
                source_commit=SOURCE, subject_reference=hostile
            ).to_json(),
        )


@pytest.mark.parametrize("component", ["my__image", "my--image"])
def test_receipt_accepts_valid_repeated_repository_separators(component):
    reference = f"ghcr.io/team/{component}@{DIGEST}"
    provenance = ProvenanceStatement(source_commit=SOURCE, subject_reference=reference)

    receipt = create_registry_publication_receipt(
        _manifest(), registry_reference=reference,
        expected_repository=f"ghcr.io/team/{component}",
        provenance_json=provenance.to_json(),
    )

    assert receipt.registry_reference == reference


@pytest.mark.parametrize("source,subject", [
    ("d" * 40, REFERENCE),
    (SOURCE, "ghcr.io/maksymmm/careeragentai-candidate@sha256:" + "d" * 64),
])
def test_receipt_rejects_provenance_for_another_source_or_subject(source, subject):
    with pytest.raises(PromotionManifestError, match="provenance"):
        create_registry_publication_receipt(
            _manifest(), registry_reference=REFERENCE,
            expected_repository="ghcr.io/maksymmm/careeragentai-candidate",
            provenance_json=ProvenanceStatement(
                source_commit=source, subject_reference=subject
            ).to_json(),
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
    provenance = ProvenanceStatement(source_commit=SOURCE, subject_reference=REFERENCE)
    with pytest.raises(PromotionManifestError):
        RegistryPublicationReceipt.from_json(
            mutator(_receipt().to_json()),
            manifest=_manifest(),
            expected_repository="ghcr.io/maksymmm/careeragentai-candidate",
            provenance_json=provenance.to_json(),
        )


def test_receipt_parser_rejects_canonical_receipt_from_untrusted_storage():
    hostile_reference = "attacker.example/image@" + DIGEST
    hostile = RegistryPublicationReceipt(
        source_commit=SOURCE,
        registry_reference=hostile_reference,
        promotion_manifest_sha256=DIGEST,
        provenance_sha256=DIGEST,
    )
    trusted_provenance = ProvenanceStatement(
        source_commit=SOURCE, subject_reference=REFERENCE
    )

    with pytest.raises(PromotionManifestError):
        RegistryPublicationReceipt.from_json(
            hostile.to_json(),
            manifest=_manifest(),
            expected_repository="ghcr.io/maksymmm/careeragentai-candidate",
            provenance_json=trusted_provenance.to_json(),
        )


def test_receipt_rejects_production_environment():
    with pytest.raises(PromotionManifestError, match="non-production"):
        RegistryPublicationReceipt(
            source_commit=SOURCE, registry_reference=REFERENCE,
            promotion_manifest_sha256=DIGEST, provenance_sha256=DIGEST,
            environment="production",
        )
