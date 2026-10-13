from __future__ import annotations

import json
from pathlib import Path

import pytest

from career_agent_ai.application.release import (
    PromotionManifest,
    PromotionManifestError,
    create_promotion_manifest,
)

SOURCE = "a" * 40
IMAGE = "sha256:" + "b" * 64


def _write_evidence(tmp_path) -> None:
    (tmp_path / "source-commit.txt").write_text(SOURCE + "\n", encoding="ascii")
    (tmp_path / "candidate-image-digest.txt").write_text(IMAGE + "\n", encoding="ascii")
    (tmp_path / "candidate-sbom.spdx.json").write_text(
        '{"spdxVersion":"SPDX-2.3"}\n', encoding="utf-8"
    )
    (tmp_path / "candidate-vulnerabilities.json").write_text(
        '{"matches":[]}\n', encoding="utf-8"
    )


def test_create_manifest_binds_complete_unpublished_evidence(tmp_path):
    _write_evidence(tmp_path)

    manifest = create_promotion_manifest(tmp_path)

    assert manifest.source_commit == SOURCE
    assert manifest.local_image_id == IMAGE
    assert manifest.publication_status == "unpublished"
    assert set(manifest.evidence_sha256) == {
        "source-commit.txt",
        "candidate-image-digest.txt",
        "candidate-sbom.spdx.json",
        "candidate-vulnerabilities.json",
    }
    assert all(value.startswith("sha256:") for value in manifest.evidence_sha256.values())
    assert PromotionManifest.from_json(manifest.to_json()) == manifest


def test_manifest_is_canonical_and_deeply_immutable(tmp_path):
    _write_evidence(tmp_path)
    manifest = create_promotion_manifest(tmp_path)

    assert manifest.to_json() == json.dumps(
        json.loads(manifest.to_json()),
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    with pytest.raises(TypeError):
        manifest.evidence_sha256["source-commit.txt"] = "sha256:" + "d" * 64


@pytest.mark.parametrize(
    "name,value",
    [
        ("source-commit.txt", "A" * 40 + "\n"),
        ("source-commit.txt", SOURCE + "\nextra\n"),
        ("candidate-image-digest.txt", "b" * 64 + "\n"),
        ("candidate-image-digest.txt", "sha256:" + "B" * 64 + "\n"),
    ],
)
def test_create_manifest_rejects_ambiguous_identity_evidence(tmp_path, name, value):
    _write_evidence(tmp_path)
    (tmp_path / name).write_text(value, encoding="ascii")

    with pytest.raises(PromotionManifestError):
        create_promotion_manifest(tmp_path)


def test_create_manifest_rejects_missing_or_empty_evidence(tmp_path):
    _write_evidence(tmp_path)
    (tmp_path / "candidate-sbom.spdx.json").write_bytes(b"")

    with pytest.raises(PromotionManifestError, match="empty release evidence"):
        create_promotion_manifest(tmp_path)
    (tmp_path / "candidate-sbom.spdx.json").unlink()
    with pytest.raises(PromotionManifestError, match="missing release evidence"):
        create_promotion_manifest(tmp_path)


def test_manifest_rejects_boolean_schema_version(tmp_path):
    _write_evidence(tmp_path)
    valid = create_promotion_manifest(tmp_path)

    with pytest.raises(PromotionManifestError, match="version"):
        PromotionManifest(
            source_commit=valid.source_commit,
            local_image_id=valid.local_image_id,
            evidence_sha256=valid.evidence_sha256,
            schema_version=True,
        )


def test_manifest_rejects_published_status_without_registry_identity(tmp_path):
    _write_evidence(tmp_path)
    valid = create_promotion_manifest(tmp_path)

    with pytest.raises(PromotionManifestError, match="unpublished"):
        PromotionManifest(
            source_commit=valid.source_commit,
            local_image_id=valid.local_image_id,
            evidence_sha256=valid.evidence_sha256,
            publication_status="published",
        )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.replace('"schema_version":1', '"schema_version":true'),
        lambda value: value.replace('"schema_version":1', '"schema_version":2'),
        lambda value: value.replace('{', '{"unknown":1,', 1),
        lambda value: value.replace('"schema_version":1', '"schema_version":1,"schema_version":1'),
        lambda value: value.replace('"source_commit"', '"missing_source"'),
        lambda value: value.replace('{', '{\n', 1),
    ],
)
def test_manifest_parser_rejects_noncanonical_or_untrusted_json(tmp_path, mutator):
    _write_evidence(tmp_path)
    value = create_promotion_manifest(tmp_path).to_json()

    with pytest.raises(PromotionManifestError):
        PromotionManifest.from_json(mutator(value))


def test_release_evidence_targets_the_integration_ref_name():
    workflow = (
        Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")

    assert "github.event_name == 'push'" in workflow
    assert (
        "github.ref_name == 'feat/decision-engine-implementation'"
        in workflow
    )
    assert (
        "github.ref == 'refs/heads/feat/decision-engine-implementation'"
        not in workflow
    )
