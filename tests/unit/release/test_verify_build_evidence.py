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
    add_docker_load_manifest,
    main,
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


def _read_archive(path: Path) -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    with tarfile.open(path, "r:*") as archive:
        for member in archive.getmembers():
            if member.isfile():
                stream = archive.extractfile(member)
                if stream is not None:
                    members[member.name] = stream.read()
    return members


def _descriptor(media_type: str, content: bytes) -> dict[str, object]:
    return {"mediaType": media_type, "digest": _digest(content), "size": len(content)}


def _blob_name(descriptor: dict[str, object]) -> str:
    return "blobs/sha256/" + str(descriptor["digest"])[7:]


def _rewrite_graph_without_predicate(path: Path, predicate: str) -> None:
    members = _read_archive(path)
    outer = json.loads(members["index.json"])
    root = outer["manifests"][0]
    inner = json.loads(members[_blob_name(root)])
    attestation = next(item for item in inner["manifests"] if item.get("annotations"))
    manifest = json.loads(members[_blob_name(attestation)])
    manifest["layers"] = [
        layer for layer in manifest["layers"] if layer["annotations"]["in-toto.io/predicate-type"] != predicate
    ]
    manifest_bytes = _json(manifest)
    replacement_attestation = {**attestation, "digest": _digest(manifest_bytes), "size": len(manifest_bytes)}
    members[_blob_name(replacement_attestation)] = manifest_bytes
    inner["manifests"][inner["manifests"].index(attestation)] = replacement_attestation
    inner_bytes = _json(inner)
    replacement_root = {**root, "digest": _digest(inner_bytes), "size": len(inner_bytes)}
    members[_blob_name(replacement_root)] = inner_bytes
    outer["manifests"][0] = replacement_root
    members["index.json"] = _json(outer)
    _write_archive(path, members)


def _oci_archive(
    path: Path,
    *,
    attest: bool = True,
    arch: str = "arm64",
    config_arch: str | None = None,
    revision: str = SOURCE_SHA,
    labels_mode: str = "present",
) -> list[dict[str, object]]:
    source = "sha256:" + "2" * 64
    runtime_config: dict[str, object] = {"Labels": {"org.opencontainers.image.revision": revision}}
    if labels_mode == "missing":
        runtime_config = {}
    elif labels_mode == "null":
        runtime_config = {"Labels": None}
    elif labels_mode == "invalid":
        runtime_config = {"Labels": []}
    config = _json(
        {
            "os": "linux",
            "architecture": config_arch or arch,
            "config": runtime_config,
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


def test_docker_import_view_preserves_every_original_oci_member_and_evidence(tmp_path: Path) -> None:
    path = tmp_path / "combined.tar"
    descriptors = _oci_archive(path)
    original_members = _read_archive(path)
    original_evidence = verify_archive(path, platform="linux/arm64", source_sha=SOURCE_SHA)

    add_docker_load_manifest(path, platform="linux/arm64", source_sha=SOURCE_SHA)

    combined = _read_archive(path)
    assert set(combined) == {*original_members, "manifest.json"}
    assert {name: combined[name] for name in original_members} == original_members
    assert verify_archive(path, platform="linux/arm64", source_sha=SOURCE_SHA) == original_evidence
    manifest = json.loads(combined["manifest.json"])[0]
    assert manifest["RepoTags"] == [f"ob-conformance-candidate:linux-arm64-{SOURCE_SHA}"]
    assert _digest(combined[manifest["Config"]]) == original_evidence["config_digest"]
    image = json.loads(combined[_blob_name(descriptors[0])])
    assert manifest["Layers"] == [_blob_name(layer) for layer in image["layers"]]
    first_archive_bytes = path.read_bytes()
    add_docker_load_manifest(path, platform="linux/arm64", source_sha=SOURCE_SHA)
    assert path.read_bytes() == first_archive_bytes


@pytest.mark.parametrize("field", ["Config", "Layers", "RepoTags"])
def test_rejects_docker_import_view_bound_to_different_image_or_tag(tmp_path: Path, field: str) -> None:
    path = tmp_path / "combined.tar"
    _oci_archive(path)
    add_docker_load_manifest(path, platform="linux/arm64", source_sha=SOURCE_SHA)
    combined = _read_archive(path)
    manifest = json.loads(combined["manifest.json"])
    manifest[0][field] = "wrong" if field == "Config" else ["wrong"]
    combined["manifest.json"] = _json(manifest)
    _write_archive(path, combined)

    with pytest.raises(BuildEvidenceError, match="Docker import manifest differs"):
        verify_archive(path, platform="linux/arm64", source_sha=SOURCE_SHA)


def test_cli_adds_import_view_only_after_evidence_verification(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "combined.tar"
    _oci_archive(path)
    assert (
        main(
            [
                "--archive",
                str(path),
                "--platform",
                "linux/arm64",
                "--source-sha",
                SOURCE_SHA,
                "--runtime-base",
                RUNTIME_BASE,
                "--add-docker-load-manifest",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["format"] == "oci"
    assert "manifest.json" in _read_archive(path)

    wrong = tmp_path / "wrong.tar"
    _oci_archive(wrong)
    before = wrong.read_bytes()
    assert (
        main(
            [
                "--archive",
                str(wrong),
                "--platform",
                "linux/arm64",
                "--source-sha",
                "f" * 40,
                "--add-docker-load-manifest",
            ]
        )
        == 1
    )
    assert wrong.read_bytes() == before


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


@pytest.mark.parametrize("labels_mode", ["missing", "null", "invalid"])
@pytest.mark.parametrize("archive_format", ["docker", "oci-legacy"])
def test_legacy_revision_labels_are_optional_but_malformed_labels_fail(
    tmp_path: Path, labels_mode: str, archive_format: str
) -> None:
    path = tmp_path / "legacy.tar"
    if archive_format == "oci-legacy":
        _oci_archive(path, attest=False, labels_mode=labels_mode)
    else:
        runtime_config: dict[str, object] = {}
        if labels_mode == "null":
            runtime_config["Labels"] = None
        elif labels_mode == "invalid":
            runtime_config["Labels"] = []
        _write_archive(
            path,
            {
                "manifest.json": _json([{"Config": "config.json", "Layers": ["layer.tar"]}]),
                "config.json": _json(
                    {
                        "os": "linux",
                        "architecture": "arm64",
                        "config": runtime_config,
                        "rootfs": {"diff_ids": [_digest(b"layer")]},
                    }
                ),
                "layer.tar": b"layer",
            },
        )

    if labels_mode == "invalid":
        with pytest.raises(BuildEvidenceError, match="JSON object"):
            verify_archive(path, platform="linux/arm64", source_sha=SOURCE_SHA, allow_legacy=True)
    else:
        assert (
            verify_archive(path, platform="linux/arm64", source_sha=SOURCE_SHA, allow_legacy=True)["format"]
            == archive_format
        )


@pytest.mark.parametrize("labels_mode", ["missing", "null"])
def test_required_revision_label_remains_required(tmp_path: Path, labels_mode: str) -> None:
    path = tmp_path / "attested.tar"
    _oci_archive(path, labels_mode=labels_mode)
    with pytest.raises(BuildEvidenceError, match="revision"):
        verify_archive(path, platform="linux/arm64", source_sha=SOURCE_SHA, require_revision_label=True)


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


@pytest.mark.parametrize(
    "media_type",
    [
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.index.v1+json",
    ],
)
def test_accepts_legacy_publication_index_types_and_still_checks_scanned_digests(media_type: str) -> None:
    descriptors: list[dict[str, object]] = [
        {"digest": "sha256:" + str(index) * 64, "platform": {"os": "linux", "architecture": arch}}
        for index, arch in ((1, "amd64"), (2, "arm64"))
    ]
    evidence: list[dict[str, object]] = [
        {"format": "docker", "image_digest": item["digest"], "platform": f"linux/{arch}"}
        for item, arch in zip(descriptors, ("amd64", "arm64"), strict=True)
    ]
    verify_publication({"mediaType": media_type, "manifests": descriptors}, evidence)
    wrong = [{**descriptors[0], "digest": "sha256:" + "f" * 64}, descriptors[1]]
    with pytest.raises(BuildEvidenceError, match="scanned legacy image"):
        verify_publication({"mediaType": media_type, "manifests": wrong}, evidence)


def test_attested_publication_still_requires_oci_index() -> None:
    descriptor: dict[str, object] = {"digest": "sha256:" + "1" * 64}
    evidence: list[dict[str, object]] = [{"format": "oci", "descriptors": [descriptor]}]
    with pytest.raises(BuildEvidenceError, match="missing or unexpected"):
        verify_publication(
            {
                "mediaType": "application/vnd.docker.distribution.manifest.list.v2+json",
                "manifests": [descriptor],
            },
            evidence,
        )


@pytest.mark.parametrize("removed", [PROVENANCE, SPDX])
def test_archive_rejects_missing_provenance_or_sbom_descriptors(tmp_path: Path, removed: str) -> None:
    archive = tmp_path / "missing-attestation.tar"
    _oci_archive(archive)
    _rewrite_graph_without_predicate(archive, removed)

    with pytest.raises(BuildEvidenceError, match="provenance and SBOM are both required"):
        verify_archive(
            archive,
            platform="linux/arm64",
            source_sha=SOURCE_SHA,
            runtime_base=RUNTIME_BASE,
        )


@pytest.mark.parametrize("corrupted", [PROVENANCE, SPDX])
def test_archive_rejects_corrupted_attestation_blobs(tmp_path: Path, corrupted: str) -> None:
    archive = tmp_path / "corrupted-attestation.tar"
    descriptors = _oci_archive(archive)
    members = _read_archive(archive)
    attestation = json.loads(members[_blob_name(descriptors[1])])
    layer = next(
        item for item in attestation["layers"] if item["annotations"]["in-toto.io/predicate-type"] == corrupted
    )
    blob = _blob_name(layer)
    original = members[blob]
    members[blob] = bytes([original[0] ^ 1]) + original[1:]
    _write_archive(archive, members)

    with pytest.raises(BuildEvidenceError, match="Blob checksum mismatch"):
        verify_archive(
            archive,
            platform="linux/arm64",
            source_sha=SOURCE_SHA,
            runtime_base=RUNTIME_BASE,
        )


@pytest.mark.parametrize("altered_index", [0, 1])
def test_publication_rejects_missing_or_altered_image_and_attestation_descriptors(altered_index: int) -> None:
    descriptors: list[dict[str, object]] = [
        {"digest": "sha256:" + str(index) * 64, "platform": {"os": "linux", "architecture": arch}}
        for index, arch in ((1, "amd64"), (2, "arm64"))
    ]
    attestation_descriptors: list[dict[str, object]] = [
        {**descriptor, "annotations": {"vnd.docker.reference.type": "attestation-manifest"}}
        for descriptor in descriptors
    ]
    evidence: list[dict[str, object]] = [
        {"format": "oci", "descriptors": [descriptors[0], attestation_descriptors[0]]},
        {"format": "oci", "descriptors": [descriptors[1], attestation_descriptors[1]]},
    ]
    expected = [descriptors[0], attestation_descriptors[0], descriptors[1], attestation_descriptors[1]]

    for mode in ("missing", "altered"):
        published = expected.copy()
        if mode == "missing":
            published.pop(altered_index)
        else:
            published[altered_index] = {**published[altered_index], "digest": "sha256:" + "f" * 64}
        with pytest.raises(BuildEvidenceError, match="missing or unexpected|does not preserve"):
            verify_publication(
                {"mediaType": "application/vnd.oci.image.index.v1+json", "manifests": published}, evidence
            )
