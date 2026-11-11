"""Tests for BuildKit evidence and legacy image archive validation."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from scripts.verify_build_evidence import (
    BuildEvidenceError,
    verify_archive,
    verify_publication,
)

pytestmark = pytest.mark.unit

SOURCE_SHA = "1" * 40
RUNTIME_BASE = "dhi.io/python:3.14-debian13@sha256:" + "2" * 64
PROVENANCE = "https://slsa.dev/provenance/v0.2"
SPDX = "https://spdx.dev/Document"
MANIFEST_TYPE = "application/vnd.oci.image.manifest.v1+json"


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _write_archive(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w") as archive:
        for name, content in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))


def _json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def _descriptor(media_type: str, content: bytes) -> dict[str, object]:
    return {"mediaType": media_type, "digest": _digest(content), "size": len(content)}


def _oci_archive(
    path: Path,
    *,
    attest: bool = True,
    arch: str = "arm64",
    config_arch: str | None = None,
    revision: str = SOURCE_SHA,
) -> list[dict[str, object]]:
    source = "sha256:" + "2" * 64
    config = _json(
        {
            "os": "linux",
            "architecture": config_arch or arch,
            "config": {"Labels": {"org.opencontainers.image.revision": revision}},
            "rootfs": {"diff_ids": [_digest(b"fixture layer")]},
        }
    )
    layer = b"fixture layer"
    config_descriptor = _descriptor("application/vnd.oci.image.config.v1+json", config)
    layer_descriptor = _descriptor("application/vnd.oci.image.layer.v1.tar", layer)
    image = _json(
        {
            "schemaVersion": 2,
            "mediaType": MANIFEST_TYPE,
            "config": config_descriptor,
            "layers": [layer_descriptor],
        }
    )
    image_descriptor = {**_descriptor(MANIFEST_TYPE, image), "platform": {"os": "linux", "architecture": arch}}
    descriptors: list[dict[str, object]] = [image_descriptor]
    members = {
        "oci-layout": _json({"imageLayoutVersion": "1.0.0"}),
        "blobs/sha256/" + str(config_descriptor["digest"])[7:]: config,
        "blobs/sha256/" + str(layer_descriptor["digest"])[7:]: layer,
        "blobs/sha256/" + str(image_descriptor["digest"])[7:]: image,
    }

    if attest:
        base_material = {
            "uri": "pkg:docker/dhi.io/python@3.14-debian13?digest=" + source[7:],
            "digest": {"sha256": source[7:]},
        }
        statements = {
            PROVENANCE: {
                "_type": "https://in-toto.io/Statement/v1",
                "subject": [{"name": "image", "digest": {"sha256": str(image_descriptor["digest"])[7:]}}],
                "predicateType": PROVENANCE,
                "predicate": {
                    "buildType": "https://mobyproject.org/buildkit@v1",
                    "buildConfig": {"llbDefinition": [{"id": "step0"}]},
                    "invocation": {"parameters": {"args": {"build-arg:SOURCE_REVISION": SOURCE_SHA}}},
                    "materials": [base_material],
                },
            },
            SPDX: {
                "_type": "https://in-toto.io/Statement/v1",
                "subject": [{"name": "image", "digest": {"sha256": str(image_descriptor["digest"])[7:]}}],
                "predicateType": SPDX,
                "predicate": {"spdxVersion": "SPDX-2.3", "packages": [{"name": "fixture"}]},
            },
        }
        attestation_layers = []
        for predicate, statement in statements.items():
            content = _json(statement)
            descriptor = {
                **_descriptor("application/vnd.in-toto+json", content),
                "annotations": {"in-toto.io/predicate-type": predicate},
            }
            members["blobs/sha256/" + str(descriptor["digest"])[7:]] = content
            attestation_layers.append(descriptor)
        empty = b"{}"
        empty_descriptor = _descriptor("application/vnd.oci.empty.v1+json", empty)
        attestation_manifest = _json(
            {
                "schemaVersion": 2,
                "mediaType": MANIFEST_TYPE,
                "config": empty_descriptor,
                "layers": attestation_layers,
            }
        )
        attestation_descriptor = {
            **_descriptor(MANIFEST_TYPE, attestation_manifest),
            "platform": {"os": "unknown", "architecture": "unknown"},
            "annotations": {
                "vnd.docker.reference.digest": image_descriptor["digest"],
                "vnd.docker.reference.type": "attestation-manifest",
            },
        }
        members["blobs/sha256/" + str(empty_descriptor["digest"])[7:]] = empty
        members["blobs/sha256/" + str(attestation_descriptor["digest"])[7:]] = attestation_manifest
        descriptors.append(attestation_descriptor)

    if attest:
        index = _json(
            {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json", "manifests": descriptors}
        )
        root = _descriptor("application/vnd.oci.image.index.v1+json", index)
        members["blobs/sha256/" + str(root["digest"])[7:]] = index
        top_level = {"schemaVersion": 2, "manifests": [root]}
    else:
        top_level = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": descriptors,
        }
    members["index.json"] = _json(top_level)
    _write_archive(path, members)
    return descriptors


def test_verifies_buildkit_provenance_sbom_and_dhi_base(tmp_path: Path) -> None:
    archive = tmp_path / "candidate.tar"
    descriptors = _oci_archive(archive)

    result = verify_archive(
        archive,
        platform="linux/arm64",
        source_sha=SOURCE_SHA,
        expected_digest=str(descriptors[0]["digest"]),
        runtime_base=RUNTIME_BASE,
        require_revision_label=True,
    )

    assert result["format"] == "oci"
    assert result["image_digest"] == descriptors[0]["digest"]
    assert result["descriptors"] == descriptors


def test_accepts_legacy_docker_exporter_oci_archive(tmp_path: Path) -> None:
    archive = tmp_path / "legacy-oci.tar"
    _oci_archive(archive, attest=False)

    result = verify_archive(archive, platform="linux/arm64", source_sha=SOURCE_SHA, allow_legacy=True)
    assert result["format"] == "oci-legacy"


def test_accepts_legacy_docker_archive_for_compatibility(tmp_path: Path) -> None:
    archive = tmp_path / "legacy-docker.tar"
    config = _json(
        {
            "os": "linux",
            "architecture": "amd64",
            "config": {"Labels": {"org.opencontainers.image.revision": SOURCE_SHA}},
            "rootfs": {"diff_ids": [_digest(b"legacy layer")]},
        }
    )
    _write_archive(
        archive,
        {
            "manifest.json": _json([{"Config": "image/config.json", "Layers": ["image/layer.tar"]}]),
            "image/config.json": config,
            "image/layer.tar": b"legacy layer",
        },
    )

    result = verify_archive(
        archive,
        platform="linux/amd64",
        source_sha=SOURCE_SHA,
        allow_legacy=True,
    )
    assert result["format"] == "docker"


def test_rejects_mismatched_attestation_and_legacy_revision(tmp_path: Path) -> None:
    archive = tmp_path / "candidate.tar"
    _oci_archive(archive)
    with pytest.raises(BuildEvidenceError, match="scanned platform digest"):
        verify_archive(
            archive,
            platform="linux/arm64",
            source_sha=SOURCE_SHA,
            expected_digest="sha256:" + "f" * 64,
        )

    legacy = tmp_path / "legacy.tar"
    config = _json(
        {
            "os": "linux",
            "architecture": "amd64",
            "config": {"Labels": {"org.opencontainers.image.revision": "wrong"}},
            "rootfs": {"diff_ids": [_digest(b"layer")]},
        }
    )
    _write_archive(
        legacy,
        {
            "manifest.json": _json([{"Config": "config.json", "Layers": ["layer.tar"]}]),
            "config.json": config,
            "layer.tar": b"layer",
        },
    )
    with pytest.raises(BuildEvidenceError, match="revision"):
        verify_archive(legacy, platform="linux/amd64", source_sha=SOURCE_SHA, allow_legacy=True)


def test_rejects_matching_layers_with_mismatched_oci_config_or_platform(tmp_path: Path) -> None:
    wrong_platform = tmp_path / "wrong-platform.tar"
    _oci_archive(wrong_platform, attest=False, arch="amd64", config_arch="arm64")
    with pytest.raises(BuildEvidenceError, match="config platform"):
        verify_archive(
            wrong_platform,
            platform="linux/amd64",
            source_sha=SOURCE_SHA,
            allow_legacy=True,
        )

    wrong_config = tmp_path / "wrong-config.tar"
    _oci_archive(wrong_config, attest=False, arch="amd64", revision="different")
    with pytest.raises(BuildEvidenceError, match="revision"):
        verify_archive(
            wrong_config,
            platform="linux/amd64",
            source_sha=SOURCE_SHA,
            allow_legacy=True,
        )


def test_publication_requires_exact_original_attestation_descriptors() -> None:
    descriptors: list[dict[str, object]] = [
        {"digest": "sha256:" + str(index) * 64, "platform": {"os": "linux", "architecture": arch}}
        for index, arch in ((1, "amd64"), (2, "arm64"))
    ]
    evidence: list[dict[str, object]] = [
        {"format": "oci", "descriptors": [descriptors[0]]},
        {"format": "oci", "descriptors": [descriptors[1]]},
    ]
    published: dict[str, object] = {
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": descriptors,
    }
    verify_publication(published, evidence)

    with pytest.raises(BuildEvidenceError, match="missing or unexpected"):
        verify_publication({**published, "manifests": descriptors + [descriptors[0]]}, evidence)
