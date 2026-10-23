"""Fetch Docker-signed OCI OpenVEX bound to an immutable platform manifest.

Scout's VEX exporter cannot currently export DHI's in-toto wrapper. Verify the
OCI artifact with cosign instead, then verify both content digests and subject
bindings before extracting OpenVEX. Vendor Python assessments are deliberately
excluded: this repository replaces that component with its own CPython build.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

from scripts.validate_docker_base import EXPECTED_RUNTIME_BASE
from scripts.vulnerability_gate import GateInputError, parse_openvex, parse_purl

OPENVEX = "https://openvex.dev/ns/v0.2.0"
IN_TOTO = "application/vnd.in-toto+json"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_REFERENCE = re.compile(r"dhi\.io/python(?::[\w.-]+)?@(sha256:[0-9a-f]{64})")
_PUBLISHED = re.compile(r"docker\.io/openbanking/conformance-suite-v2(?::[\w.-]+|@sha256:[0-9a-f]{64})")


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise GateInputError("Expected an OCI/OpenVEX JSON object.")
    return value


def _objects(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise GateInputError("Expected an OCI/OpenVEX JSON array.")
    return [_object(item) for item in value]


def _digest(value: object) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise GateInputError(f"Invalid OCI SHA-256 digest: {value!r}.")
    return value


def _run(*args: str) -> bytes:
    return subprocess.run(  # noqa: S603 - Fixed executables and argument vectors, never shell or repository-provided commands.
        args, check=True, stdout=subprocess.PIPE
    ).stdout


def _checked_json(content: bytes, digest: str) -> dict[str, object]:
    if "sha256:" + hashlib.sha256(content).hexdigest() != digest:
        raise GateInputError("OCI content does not match its descriptor digest.")
    return _object(json.loads(content))


def extract_predicate(statement: object, subject_digest: str) -> dict[str, object]:
    """Validate in-toto subject binding and exclude all replaced Python products."""
    root = _object(statement)
    if (
        root.get("_type")
        not in (
            "https://in-toto.io/Statement/v0.1",
            "https://in-toto.io/Statement/v1",
        )
        or root.get("predicateType") != OPENVEX
    ):
        raise GateInputError("Expected an in-toto OpenVEX statement.")
    subjects = _objects(root.get("subject"))
    if len(subjects) != 1 or _object(subjects[0].get("digest")) != {"sha256": subject_digest.removeprefix("sha256:")}:
        raise GateInputError("OpenVEX statement is not bound to the assessed platform manifest.")
    predicate = _object(root.get("predicate"))
    parse_openvex(predicate)
    filtered: list[dict[str, object]] = []
    for entry in _objects(predicate.get("statements")):
        products: list[dict[str, object]] = []
        for product in _objects(entry.get("products", [])):
            product_id = product.get("@id")
            purl = parse_purl(product_id) if isinstance(product_id, str) else None
            if (
                purl is not None
                and purl[0] == "deb"
                and not purl[1].startswith(("python", "libpython"))
                and purl[1] not in {"conformance-python", "pip", "setuptools", "wheel"}
            ):
                products.append(product)
        if products:
            filtered.append({**entry, "products": products})
    return {**predicate, "statements": filtered}


def fetch_vex(image: str, platform: str, key: Path) -> dict[str, object] | None:
    """Verify exact-platform DHI evidence, including published base ancestry.

    Registry, signature, malformed-data and binding failures propagate. Only a
    successful referrers response with no OpenVEX, or a published image without
    the assessed base's layer prefix, permits a scan without VEX.
    """
    if platform not in {"linux/amd64", "linux/arm64"}:
        raise GateInputError("DHI OpenVEX requires an immutable Python reference and supported platform.")
    if _PUBLISHED.fullmatch(image):
        published = _object(
            json.loads(_run("regctl", "manifest", "get", image, "--platform", platform, "--format", "raw-body"))
        )
        base = _object(
            json.loads(
                _run("regctl", "manifest", "get", EXPECTED_RUNTIME_BASE, "--platform", platform, "--format", "raw-body")
            )
        )
        base_layers = [entry.get("digest") for entry in _objects(base.get("layers"))]
        image_layers = [entry.get("digest") for entry in _objects(published.get("layers"))]
        if not base_layers or image_layers[: len(base_layers)] != base_layers:
            return None
        image = EXPECTED_RUNTIME_BASE
    if not _REFERENCE.fullmatch(image):
        raise GateInputError("DHI OpenVEX requires an immutable Python reference and supported platform.")
    manifest = _run("regctl", "manifest", "get", image, "--platform", platform, "--format", "raw-body")
    subject_digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
    subject = f"dhi.io/python@{subject_digest}"
    descriptors = json.loads(
        _run(
            "regctl",
            "artifact",
            "list",
            subject,
            "--filter-annotation",
            f"in-toto.io/predicate-type={OPENVEX}",
            "--format",
            "{{json .Descriptors}}",
        )
    )
    referrers = [] if descriptors is None else _objects(descriptors)
    if not referrers:
        return None
    if len(referrers) != 1:
        raise GateInputError("Expected exactly one Docker OpenVEX artifact for the platform.")
    artifact_digest = _digest(referrers[0].get("digest"))
    artifact = f"dhi.io/python@{artifact_digest}"
    _run(
        "cosign",
        "verify",
        "--key",
        str(key),
        "--insecure-ignore-tlog=true",
        "--experimental-oci11",
        artifact,
    )
    artifact_manifest = _checked_json(
        _run("regctl", "manifest", "get", artifact, "--format", "raw-body"), artifact_digest
    )
    if _object(artifact_manifest.get("subject")).get("digest") != subject_digest:
        raise GateInputError("Signed OCI artifact refers to a different platform manifest.")
    layers = _objects(artifact_manifest.get("layers"))
    if len(layers) != 1 or layers[0].get("mediaType") != IN_TOTO:
        raise GateInputError("Expected a single in-toto OpenVEX layer.")
    layer_digest = _digest(layers[0].get("digest"))
    statement = _checked_json(_run("regctl", "blob", "get", "dhi.io/python", layer_digest), layer_digest)
    return extract_predicate(statement, subject_digest)


def main() -> None:
    """Write verified OpenVEX; absent or inapplicable evidence leaves no output."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.unlink(missing_ok=True)
    predicate = fetch_vex(args.image, args.platform, args.key)
    if predicate is not None:
        args.output.write_text(json.dumps(predicate) + "\n")


if __name__ == "__main__":
    main()
