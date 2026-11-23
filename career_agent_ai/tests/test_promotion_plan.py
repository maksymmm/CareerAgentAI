from __future__ import annotations

import hashlib

import pytest

from career_agent_ai.application.release import (
    DryRunPromotionPlan,
    PromotionManifest,
    PromotionManifestError,
    ProvenanceStatement,
    create_dry_run_promotion_plan,
    create_registry_publication_receipt,
)

SOURCE = "a" * 40
DIGEST = "sha256:" + "b" * 64
OLD_DIGEST = "sha256:" + "c" * 64
REPOSITORY = "ghcr.io/maksymmm/careeragentai-candidate"
TARGET = REPOSITORY + "@" + DIGEST
CURRENT = REPOSITORY + "@" + OLD_DIGEST
EVIDENCE = {
    name: DIGEST
    for name in (
        "candidate-image-digest.txt",
        "candidate-sbom.spdx.json",
        "candidate-vulnerabilities.json",
        "source-commit.txt",
    )
}


def _evidence() -> tuple[PromotionManifest, str, str]:
    manifest = PromotionManifest(
        source_commit=SOURCE, local_image_id=DIGEST, evidence_sha256=EVIDENCE
    )
    provenance_json = ProvenanceStatement(
        source_commit=SOURCE, subject_reference=TARGET
    ).to_json()
    receipt_json = create_registry_publication_receipt(
        manifest,
        registry_reference=TARGET,
        expected_repository=REPOSITORY,
        provenance_json=provenance_json,
    ).to_json()
    return manifest, provenance_json, receipt_json


def _plan() -> DryRunPromotionPlan:
    manifest, provenance_json, receipt_json = _evidence()
    return create_dry_run_promotion_plan(
        manifest=manifest,
        expected_repository=REPOSITORY,
        provenance_json=provenance_json,
        receipt_json=receipt_json,
        current_reference=CURRENT,
    )


def test_plan_binds_verified_target_rollback_and_receipt():
    manifest, provenance_json, receipt_json = _evidence()
    plan = _plan()

    assert plan.source_commit == SOURCE
    assert plan.target_reference == TARGET
    assert plan.rollback_reference == CURRENT
    assert plan.environment == "non-production"
    assert plan.execution_mode == "dry-run"
    assert plan.publication_receipt_sha256 == "sha256:" + hashlib.sha256(
        receipt_json.encode("ascii")
    ).hexdigest()
    assert DryRunPromotionPlan.from_json(
        plan.to_json(),
        manifest=manifest,
        expected_repository=REPOSITORY,
        provenance_json=provenance_json,
        receipt_json=receipt_json,
        current_reference=CURRENT,
    ) == plan
    plan.verify_target_reference(TARGET)
    plan.verify_rollback_reference(CURRENT)


@pytest.mark.parametrize(
    "current,error",
    [
        (REPOSITORY + ":latest", "immutable"),
        ("attacker.example/image@" + OLD_DIGEST, "trusted repository"),
        (TARGET, "must differ"),
    ],
)
def test_plan_rejects_untrusted_or_non_rollback_current_identity(current, error):
    manifest, provenance_json, receipt_json = _evidence()

    with pytest.raises(PromotionManifestError, match=error):
        create_dry_run_promotion_plan(
            manifest=manifest,
            expected_repository=REPOSITORY,
            provenance_json=provenance_json,
            receipt_json=receipt_json,
            current_reference=current,
        )


def test_plan_revalidates_receipt_against_trusted_evidence():
    manifest, provenance_json, receipt_json = _evidence()
    tampered = receipt_json.replace(SOURCE, "d" * 40)

    with pytest.raises(PromotionManifestError, match="trusted evidence"):
        create_dry_run_promotion_plan(
            manifest=manifest,
            expected_repository=REPOSITORY,
            provenance_json=provenance_json,
            receipt_json=tampered,
            current_reference=CURRENT,
        )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.replace('"schema_version":1', '"schema_version":true'),
        lambda value: value.replace('"schema_version":1', '"schema_version":2'),
        lambda value: value.replace('{', '{"unknown":1,', 1),
        lambda value: value.replace(
            '"source_commit"', '"source_commit":"' + SOURCE + '","source_commit"'
        ),
        lambda value: value.replace('{', '{\n', 1),
        lambda value: value.replace('"dry-run"', '"execute"'),
        lambda value: value.replace('"non-production"', '"production"'),
        lambda value: value.replace(CURRENT, REPOSITORY + "@" + "sha256:" + "d" * 64),
    ],
)
def test_plan_parser_rejects_untrusted_or_noncanonical_json(mutator):
    manifest, provenance_json, receipt_json = _evidence()

    with pytest.raises(PromotionManifestError):
        DryRunPromotionPlan.from_json(
            mutator(_plan().to_json()),
            manifest=manifest,
            expected_repository=REPOSITORY,
            provenance_json=provenance_json,
            receipt_json=receipt_json,
            current_reference=CURRENT,
        )


@pytest.mark.parametrize(
    "method,reference",
    [
        ("verify_target_reference", REPOSITORY + "@" + OLD_DIGEST),
        ("verify_rollback_reference", TARGET),
        ("verify_target_reference", REPOSITORY + ":latest"),
    ],
)
def test_plan_requires_exact_transition_references(method, reference):
    with pytest.raises(PromotionManifestError, match="exactly match"):
        getattr(_plan(), method)(reference)


def test_plan_rejects_malformed_receipt_digest():
    with pytest.raises(PromotionManifestError, match="receipt digest"):
        DryRunPromotionPlan(
            source_commit=SOURCE,
            target_reference=TARGET,
            rollback_reference=CURRENT,
            publication_receipt_sha256="sha256:" + "B" * 64,
        )
