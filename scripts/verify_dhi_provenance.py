"""Verify Docker-signed SLSA provenance for the candidate DHI base pair."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from scripts.validate_docker_base import CandidateDhiBases, get_candidate_dhi_bases

PROVENANCE_TYPE = "https://slsa.dev/provenance/v1"
_PLATFORM_VALUES = {
    "linux/amd64": ("linux", "amd64"),
    "linux/arm64": ("linux", "arm64"),
}
_DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")


class ProvenanceError(ValueError):
    """Raised when DHI evidence is malformed, invalid, or unverifiable."""


@dataclass(frozen=True)
class VerifiedBase:
    """A verified base reference and its exact platform subject."""

    role: Literal["builder", "runtime"]
    reference: str
    index_digest: str
    manifest_digest: str
    subject_name: str


@dataclass(frozen=True)
class ProvenanceResult:
    """Assessment result for both DHI base images on one platform."""

    state: Literal["verified", "approval_required"]
    platform: str
    verified: tuple[VerifiedBase, ...]
    missing: tuple[str, ...]


def _run(command: list[str], context: str) -> str:
    """Run a trusted local Docker command without invoking a shell."""
    completed = subprocess.run(  # noqa: S603 - fixed Docker CLI, argv-only, with validated image references
        command, capture_output=True, check=False, text=True
    )
    if completed.returncode != 0:
        raise ProvenanceError(f"{context} failed (exit {completed.returncode}).")
    return completed.stdout


def _platform_manifest_digest(reference: str, platform: str) -> str:
    """Resolve and authenticate the requested platform entry in a pinned index."""
    match = re.fullmatch(r"dhi\.io/python:[^@]+@(sha256:[0-9a-f]{64})", reference)
    if match is None:
        raise ProvenanceError(f"Invalid digest-pinned DHI reference {reference!r}.")
    index_digest = match.group(1)
    raw_index = _run(["docker", "buildx", "imagetools", "inspect", "--raw", reference], "DHI index retrieval")
    if hashlib.sha256(raw_index.encode("utf-8")).hexdigest() != index_digest.removeprefix("sha256:"):
        raise ProvenanceError(f"Retrieved index bytes do not match {index_digest}.")
    try:
        document = json.loads(raw_index)
    except json.JSONDecodeError as error:
        raise ProvenanceError("DHI index is not valid JSON.") from error
    if not isinstance(document, dict) or not isinstance(document.get("manifests"), list):
        raise ProvenanceError("DHI reference is not a multi-platform OCI image index.")
    os_name, architecture = _PLATFORM_VALUES[platform]
    matches = [
        item
        for item in document["manifests"]
        if isinstance(item, dict) and item.get("platform") == {"os": os_name, "architecture": architecture}
    ]
    if len(matches) != 1:
        raise ProvenanceError(f"DHI index must contain exactly one {platform} manifest.")
    descriptor = matches[0]
    digest = descriptor.get("digest")
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
        raise ProvenanceError(f"DHI index has an invalid {platform} manifest digest.")
    return digest


def _listed_provenance(reference: str, platform: str, manifest_digest: str) -> bool:
    """Return whether Scout lists SLSA v1 provenance for the exact subject."""
    raw_list = _run(
        [
            "docker",
            "scout",
            "attest",
            "list",
            "--format",
            "json",
            "--platform",
            platform,
            f"registry://{reference}",
        ],
        "DHI attestation listing",
    )
    try:
        entries = json.loads(raw_list)
    except json.JSONDecodeError as error:
        raise ProvenanceError("Docker Scout attestation listing is not valid JSON.") from error
    if not isinstance(entries, list):
        raise ProvenanceError("Docker Scout attestation listing must be a JSON array.")
    provenance = [
        entry for entry in entries if isinstance(entry, dict) and entry.get("predicateType") == PROVENANCE_TYPE
    ]
    if not provenance:
        return False
    expected_subject = f"dhi.io/python@{manifest_digest}"
    if not any(entry.get("subject") == expected_subject for entry in provenance):
        raise ProvenanceError("Listed SLSA provenance does not name the expected platform manifest.")
    return True


def _verified_statement(
    reference: str,
    role: Literal["builder", "runtime"],
    platform: str,
    manifest_digest: str,
    key_path: Path,
) -> str:
    """Fetch and verify the full signed in-toto statement, retaining its subject."""
    with tempfile.TemporaryDirectory(prefix="dhi-provenance-") as directory:
        output_path = Path(directory) / "statement.json"
        _run(
            [
                "docker",
                "scout",
                "attest",
                "get",
                f"registry://{reference}",
                "--platform",
                platform,
                "--predicate-type",
                PROVENANCE_TYPE,
                "--verify",
                "--skip-tlog",
                "--key",
                str(key_path),
                "--output",
                str(output_path),
            ],
            "DHI provenance signature verification",
        )
        try:
            statement = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProvenanceError("Verified Docker Scout statement is missing or malformed.") from error
    if not isinstance(statement, dict) or statement.get("_type") != "https://in-toto.io/Statement/v0.1":
        raise ProvenanceError("Verified DHI provenance is not an in-toto Statement v0.1.")
    if statement.get("predicateType") != PROVENANCE_TYPE:
        raise ProvenanceError("Verified DHI provenance has an unexpected predicate type.")
    subjects = statement.get("subject")
    if not isinstance(subjects, list) or len(subjects) != 1 or not isinstance(subjects[0], dict):
        raise ProvenanceError("Verified DHI provenance must have exactly one subject.")
    subject = subjects[0]
    name = subject.get("name")
    digest = subject.get("digest")
    if (
        not isinstance(name, str)
        or not isinstance(digest, dict)
        or digest.get("sha256") != manifest_digest.removeprefix("sha256:")
    ):
        raise ProvenanceError("Verified DHI provenance subject digest does not match the platform manifest.")
    variant = "-debian13-dev" if role == "builder" else "-debian13"
    expected_suffix = f"{variant}&platform={platform}"
    if not name.startswith("pkg:docker/dhi/python@3.14.") or not name.endswith(expected_suffix):
        raise ProvenanceError(f"Verified DHI provenance subject is not the expected {role} variant for {platform}.")
    return name


def verify_base(
    reference: str,
    role: Literal["builder", "runtime"],
    platform: str,
    key_path: Path,
) -> VerifiedBase | None:
    """Verify one base image; return ``None`` only when SLSA v1 is absent."""
    if platform not in _PLATFORM_VALUES:
        raise ProvenanceError(f"Unsupported platform {platform!r}.")
    if not key_path.is_file():
        raise ProvenanceError(f"Trusted DHI signing key does not exist: {key_path}.")
    manifest_digest = _platform_manifest_digest(reference, platform)
    if not _listed_provenance(reference, platform, manifest_digest):
        return None
    subject_name = _verified_statement(reference, role, platform, manifest_digest, key_path)
    return VerifiedBase(
        role=role,
        reference=reference,
        index_digest=reference.rsplit("@", 1)[1],
        manifest_digest=manifest_digest,
        subject_name=subject_name,
    )


def verify_candidate(
    bases: CandidateDhiBases,
    platform: str,
    key_path: Path,
) -> ProvenanceResult:
    """Verify both candidate base images and classify explicitly missing evidence."""
    verified: list[VerifiedBase] = []
    missing: list[str] = []
    references: tuple[tuple[Literal["builder", "runtime"], str], ...] = (
        ("builder", bases.builder),
        ("runtime", bases.runtime),
    )
    for role, reference in references:
        base = verify_base(reference, role, platform, key_path)
        if base is None:
            missing.append(role)
        else:
            verified.append(base)
    state: Literal["verified", "approval_required"] = "approval_required" if missing else "verified"
    return ProvenanceResult(state=state, platform=platform, verified=tuple(verified), missing=tuple(missing))


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse arguments for DHI provenance verification."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dockerfile", type=Path, default=Path("Dockerfile"))
    parser.add_argument("--platform", required=True, choices=sorted(_PLATFORM_VALUES))
    parser.add_argument("--key", type=Path, required=True, help="Pinned Docker DHI signing public key.")
    parser.add_argument("--output", type=Path, help="Optional JSON result path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Verify candidate provenance, returning 3 for missing evidence only."""
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        bases = get_candidate_dhi_bases(args.dockerfile.read_text(encoding="utf-8"))
        result = verify_candidate(bases, args.platform, args.key)
    except (OSError, ValueError) as error:
        sys.stderr.write(f"error: {error}\n")
        return 2
    serialized = json.dumps(asdict(result), indent=2, sort_keys=True) + "\n"
    if args.output is None:
        sys.stdout.write(serialized)
    else:
        args.output.write_text(serialized, encoding="utf-8")
    return 3 if result.state == "approval_required" else 0


if __name__ == "__main__":
    sys.exit(main())
