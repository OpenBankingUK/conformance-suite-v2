"""Validate BuildKit OCI archives and their unchanged publication descriptors.

SLSA max-mode provenance and SPDX attestations must remain attached to the
scanned platform manifest. This checks build evidence, not Docker's signed VEX
or vulnerability policy; those remain the separate trusted assessment's job.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
from pathlib import Path
from urllib.parse import unquote

INDEX = "application/vnd.oci.image.index.v1+json"
MANIFEST = "application/vnd.oci.image.manifest.v1+json"
PROVENANCE = "https://slsa.dev/provenance/v0.2"
SPDX = "https://spdx.dev/Document"
BUILD_TYPE = "https://mobyproject.org/buildkit@v1"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class BuildEvidenceError(ValueError):
    """Required build evidence is absent, corrupt or bound to another image."""


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise BuildEvidenceError("Expected a JSON object.")
    return {str(key): item for key, item in value.items()}


def _objects(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise BuildEvidenceError("Expected a JSON array.")
    return [_object(item) for item in value]


def _digest(descriptor: dict[str, object]) -> str:
    digest = descriptor.get("digest")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise BuildEvidenceError("Invalid OCI descriptor digest.")
    return digest


def _digest_from_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _archive_member(archive: tarfile.TarFile, name: str) -> tarfile.TarInfo:
    if not name or name.startswith("/") or ".." in Path(name).parts:
        raise BuildEvidenceError("Invalid archive member path.")
    member = archive.getmember(name)
    if not member.isfile():
        raise BuildEvidenceError(f"Expected a regular archive member: {name}.")
    return member


class _Archive:
    def __init__(self, archive: tarfile.TarFile) -> None:
        self.archive = archive
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            raise BuildEvidenceError("Duplicate archive members.")
        self.verified: set[str] = set()

    def read(self, name: str) -> bytes:
        member = _archive_member(self.archive, name)
        if member.size > 80 * 1024 * 1024:
            raise BuildEvidenceError(f"Invalid or oversized JSON member: {name}.")
        stream = self.archive.extractfile(member)
        if stream is None:
            raise BuildEvidenceError(f"Missing archive member: {name}.")
        with stream:
            return stream.read()

    def blob(self, descriptor: dict[str, object], *, document: bool = True) -> dict[str, object]:
        digest = _digest(descriptor)
        name = "blobs/sha256/" + digest.removeprefix("sha256:")
        member = self.archive.getmember(name)
        size = descriptor.get("size")
        if type(size) is not int or size != member.size or not member.isfile():
            raise BuildEvidenceError(f"Invalid blob size or type: {digest}.")
        if digest not in self.verified:
            stream = self.archive.extractfile(member)
            if stream is None:
                raise BuildEvidenceError(f"Missing blob: {digest}.")
            checksum = hashlib.sha256()
            with stream:
                while chunk := stream.read(1024 * 1024):
                    checksum.update(chunk)
            actual = "sha256:" + checksum.hexdigest()
            if actual != digest:
                raise BuildEvidenceError(f"Blob checksum mismatch: {digest}.")
            self.verified.add(digest)
        return _object(json.loads(self.read(name))) if document else {}


def _verify_docker_archive(
    archive: _Archive,
    *,
    platform: str,
    source_sha: str,
) -> dict[str, object]:
    """Validate candidate identity in a legacy Docker archive."""
    manifest = _objects(json.loads(archive.read("manifest.json")))
    if len(manifest) != 1:
        raise BuildEvidenceError("Expected exactly one image in the legacy Docker archive.")
    config_path = manifest[0].get("Config")
    layers = manifest[0].get("Layers")
    if not isinstance(config_path, str) or not isinstance(layers, list) or not layers:
        raise BuildEvidenceError("Invalid legacy Docker archive manifest.")
    config_bytes = archive.read(config_path)
    config = _object(json.loads(config_bytes))
    os_name, architecture = platform.split("/")
    if config.get("os") != os_name or config.get("architecture") != architecture:
        raise BuildEvidenceError("Legacy Docker archive platform does not match the candidate.")
    labels = _object(_object(config.get("config")).get("Labels"))
    if labels.get("org.opencontainers.image.revision") not in (None, source_sha):
        raise BuildEvidenceError("Legacy Docker archive revision differs from the candidate commit.")
    for layer in layers:
        if not isinstance(layer, str):
            raise BuildEvidenceError("Invalid legacy Docker archive layer path.")
        _archive_member(archive.archive, layer)
    rootfs = _object(config.get("rootfs", {}))
    diff_ids = rootfs.get("diff_ids", [])
    if not isinstance(diff_ids, list) or not all(isinstance(item, str) for item in diff_ids):
        raise BuildEvidenceError("Invalid legacy Docker archive rootfs metadata.")
    if diff_ids and len(diff_ids) != len(layers):
        raise BuildEvidenceError("Legacy Docker archive layers do not match its rootfs metadata.")
    return {
        "format": "docker",
        "config_digest": "sha256:" + hashlib.sha256(config_bytes).hexdigest(),
        "image_config": _object(config.get("config")),
        "rootfs_layers": diff_ids,
        "platform": platform,
        "image_digest": None,
        "index_digest": None,
        "descriptors": [],
    }


def verify_archive(
    path: Path,
    *,
    platform: str,
    source_sha: str,
    expected_digest: str | None = None,
    runtime_base: str | None = None,
    allow_legacy: bool = False,
    require_revision_label: bool = False,
) -> dict[str, object]:
    """Verify the full OCI graph and platform-bound, build-generated evidence."""
    with tarfile.open(path, "r:*") as tar:
        archive = _Archive(tar)
        if "oci-layout" not in tar.getnames():
            if not allow_legacy:
                raise BuildEvidenceError("Expected a BuildKit OCI archive.")
            if expected_digest is not None or runtime_base is not None:
                raise BuildEvidenceError("Legacy archives cannot be used where OCI evidence is required.")
            return _verify_docker_archive(archive, platform=platform, source_sha=source_sha)
        if _object(json.loads(archive.read("oci-layout"))).get("imageLayoutVersion") != "1.0.0":
            raise BuildEvidenceError("Unsupported OCI layout.")
        index_bytes = archive.read("index.json")
        outer_index = _object(json.loads(index_bytes))
        roots = _objects(outer_index.get("manifests"))
        if len(roots) != 1:
            raise BuildEvidenceError("Expected one image in the BuildKit OCI archive.")
        root = roots[0]
        if root.get("mediaType") == INDEX:
            index = archive.blob(root)
            descriptors = _objects(index.get("manifests"))
            index_digest = _digest(root)
        elif allow_legacy and root.get("mediaType") == MANIFEST:
            # The older Docker exporter writes an OCI image index directly to
            # index.json, with the single runnable image as its child.
            descriptors = roots
            index_digest = _digest_from_bytes(index_bytes)
        else:
            raise BuildEvidenceError("Expected a BuildKit OCI image index.")
        images = [item for item in descriptors if item.get("platform") != {"architecture": "unknown", "os": "unknown"}]
        if len(images) != 1 or images[0].get("platform") != dict(
            zip(("os", "architecture"), platform.split("/"), strict=True)
        ):
            raise BuildEvidenceError("Archive does not contain exactly the requested platform.")
        image = images[0]
        image_digest = _digest(image)
        if expected_digest is not None and image_digest != expected_digest:
            raise BuildEvidenceError("Archive image differs from the scanned platform digest.")
        image_manifest = archive.blob(image)
        if image.get("mediaType") != MANIFEST or image_manifest.get("mediaType") != MANIFEST:
            raise BuildEvidenceError("Expected an OCI platform manifest.")
        config = archive.blob(_object(image_manifest.get("config")))
        requested_os, requested_arch = platform.split("/")
        if config.get("os") != requested_os or config.get("architecture") != requested_arch:
            raise BuildEvidenceError("OCI image config platform differs from its manifest descriptor.")
        image_config = _object(config.get("config"))
        labels = _object(image_config.get("Labels"))
        if labels.get("org.opencontainers.image.revision") not in (
            (source_sha,) if require_revision_label else (None, source_sha)
        ):
            raise BuildEvidenceError("Image revision differs from the candidate commit.")
        for layer in _objects(image_manifest.get("layers")):
            archive.blob(layer, document=False)
        rootfs = _object(config.get("rootfs", {}))
        diff_ids = rootfs.get("diff_ids", [])
        if not isinstance(diff_ids, list) or not all(isinstance(item, str) for item in diff_ids):
            raise BuildEvidenceError("Invalid OCI image rootfs metadata.")
        if len(diff_ids) != len(_objects(image_manifest.get("layers"))):
            raise BuildEvidenceError("OCI image layers do not match its rootfs metadata.")
        attestations = [item for item in descriptors if item not in images]
        if allow_legacy and not attestations and runtime_base is None:
            return {
                "format": "oci-legacy",
                "image_digest": image_digest,
                "config_digest": _digest(_object(image_manifest.get("config"))),
                "image_config": image_config,
                "rootfs_layers": diff_ids,
                "index_digest": index_digest,
                "descriptors": [image],
                "platform": platform,
            }
        if len(attestations) != 1:
            raise BuildEvidenceError("Expected one platform attestation manifest.")
        attestation = attestations[0]
        if _object(attestation.get("annotations")) != {
            "vnd.docker.reference.digest": image_digest,
            "vnd.docker.reference.type": "attestation-manifest",
        }:
            raise BuildEvidenceError("Attestation is not associated with the scanned image.")
        attestation_manifest = archive.blob(attestation)
        if attestation.get("mediaType") != MANIFEST or attestation_manifest.get("mediaType") != MANIFEST:
            raise BuildEvidenceError("Invalid attestation manifest type.")
        archive.blob(_object(attestation_manifest.get("config")))
        predicates: dict[str, dict[str, object]] = {}
        for layer in _objects(attestation_manifest.get("layers")):
            if layer.get("mediaType") != "application/vnd.in-toto+json":
                raise BuildEvidenceError("Invalid attestation layer type.")
            statement = archive.blob(layer)
            kind = statement.get("predicateType")
            if kind not in (PROVENANCE, SPDX) or not isinstance(kind, str) or kind in predicates:
                raise BuildEvidenceError("Missing, duplicate or unexpected attestation predicate.")
            if statement.get("_type") not in ("https://in-toto.io/Statement/v0.1", "https://in-toto.io/Statement/v1"):
                raise BuildEvidenceError("Invalid in-toto statement type.")
            subjects = _objects(statement.get("subject"))
            if not subjects or any(
                _object(subject.get("digest")).get("sha256") != image_digest[7:] for subject in subjects
            ):
                raise BuildEvidenceError("Attestation subject differs from the scanned image.")
            predicates[kind] = _object(statement.get("predicate"))
        if set(predicates) != {PROVENANCE, SPDX}:
            raise BuildEvidenceError("BuildKit provenance and SBOM are both required.")
        provenance = predicates[PROVENANCE]
        if provenance.get("buildType") != BUILD_TYPE or not _objects(
            _object(provenance.get("buildConfig")).get("llbDefinition")
        ):
            raise BuildEvidenceError("BuildKit max-mode LLB evidence is required.")
        parameters = _object(_object(provenance.get("invocation")).get("parameters"))
        if _object(parameters.get("args")).get("build-arg:SOURCE_REVISION") != source_sha:
            raise BuildEvidenceError("Provenance build revision differs from the candidate commit.")
        materials = _objects(provenance.get("materials"))
        if not materials:
            raise BuildEvidenceError("Provenance materials are missing.")
        if runtime_base is not None:
            repository, _, base_digest = runtime_base.partition("@sha256:")
            repository = repository.rsplit(":", 1)[0]
            if not base_digest or not any(
                unquote(str(item.get("uri", ""))).startswith("pkg:docker/" + repository + "@")
                and _object(item.get("digest")).get("sha256") == base_digest
                for item in materials
            ):
                raise BuildEvidenceError("Provenance does not identify the candidate DHI runtime base.")
        sbom = predicates[SPDX]
        if sbom.get("spdxVersion") != "SPDX-2.3" or not _objects(sbom.get("packages")):
            raise BuildEvidenceError("BuildKit SPDX package evidence is missing.")
        return {
            "format": "oci",
            "image_digest": image_digest,
            "config_digest": _digest(_object(image_manifest.get("config"))),
            "image_config": image_config,
            "rootfs_layers": diff_ids,
            "index_digest": index_digest,
            "descriptors": descriptors,
            "platform": platform,
        }


def verify_publication(index: dict[str, object], evidence: list[dict[str, object]]) -> None:
    """Require every original platform/attestation descriptor, without additions."""
    oci = [item for item in evidence if item.get("format") == "oci"]
    legacy = [item for item in evidence if item.get("format") in ("docker", "oci-legacy")]
    expected = [descriptor for item in oci for descriptor in _objects(item.get("descriptors"))]
    actual = _objects(index.get("manifests"))
    if index.get("mediaType") != INDEX or len(actual) != len(expected) + len(legacy):
        raise BuildEvidenceError("Published index contains missing or unexpected descriptors.")
    if any(actual.count(descriptor) != 1 for descriptor in expected):
        raise BuildEvidenceError("Published index does not preserve the original image and attestation descriptors.")
    for item in legacy:
        image_digest = item.get("image_digest")
        platform = item.get("platform")
        os_name, architecture = str(platform).split("/")
        if not any(
            descriptor.get("digest") == image_digest
            and _object(descriptor.get("platform")).get("os") == os_name
            and _object(descriptor.get("platform")).get("architecture") == architecture
            for descriptor in actual
        ):
            raise BuildEvidenceError("Published index does not contain the scanned legacy image descriptor.")


def main(argv: list[str] | None = None) -> int:
    """Verify an archive or the final registry index, failing closed on errors."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--archive", type=Path)
    mode.add_argument("--published-index", type=Path)
    parser.add_argument("--platform", choices=("linux/amd64", "linux/arm64"))
    parser.add_argument("--source-sha")
    parser.add_argument("--expected-digest")
    parser.add_argument("--runtime-base")
    parser.add_argument("--allow-legacy", action="store_true")
    parser.add_argument("--require-revision-label", action="store_true")
    parser.add_argument("--evidence", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    try:
        if args.archive is not None:
            if not args.platform or not args.source_sha:
                raise BuildEvidenceError("--archive requires --platform and --source-sha.")
            result = verify_archive(
                args.archive,
                platform=args.platform,
                source_sha=args.source_sha,
                expected_digest=args.expected_digest,
                runtime_base=args.runtime_base,
                allow_legacy=args.allow_legacy,
                require_revision_label=args.require_revision_label,
            )
            sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
        else:
            if len(args.evidence) != 2:
                raise BuildEvidenceError("Publication requires both platform evidence files.")
            verify_publication(
                _object(json.loads(args.published_index.read_text())),
                [_object(json.loads(path.read_text())) for path in args.evidence],
            )
    except (BuildEvidenceError, OSError, KeyError, json.JSONDecodeError, tarfile.TarError) as error:
        sys.stderr.write(f"error: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
