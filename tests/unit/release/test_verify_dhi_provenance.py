"""Unit tests for Docker-signed DHI provenance verification."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Literal, Protocol

import pytest

import scripts.verify_dhi_provenance as verifier
from scripts.validate_docker_base import CandidateDhiBases

pytestmark = pytest.mark.unit

PLATFORMS = ("linux/amd64", "linux/arm64")
PLATFORM_DIGESTS = {
    "linux/amd64": "sha256:" + "a" * 64,
    "linux/arm64": "sha256:" + "b" * 64,
}


class DockerRunner(Protocol):
    """Callable shape used by the provenance verifier's Docker boundary."""

    def __call__(
        self, command: list[str], *, capture_output: bool, check: bool, text: bool
    ) -> subprocess.CompletedProcess[str]: ...


def _index() -> str:
    return json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "digest": digest,
                    "platform": {"os": os_name, "architecture": architecture},
                }
                for os_name, architecture, digest in (
                    ("linux", "amd64", PLATFORM_DIGESTS["linux/amd64"]),
                    ("linux", "arm64", PLATFORM_DIGESTS["linux/arm64"]),
                )
            ],
        },
        separators=(",", ":"),
    )


def _reference(role: Literal["builder", "runtime"]) -> tuple[str, str]:
    index = _index()
    digest = hashlib.sha256(index.encode()).hexdigest()
    tag = "3.14-debian13-dev" if role == "builder" else "3.14-debian13"
    return f"dhi.io/python:{tag}@sha256:{digest}", index


def _statement(role: str, platform: str, digest: str) -> dict[str, object]:
    version = "3.14.99-debian13-dev" if role == "builder" else "3.14.99-debian13"
    return {
        "_type": "https://in-toto.io/Statement/v0.1",
        "predicateType": verifier.PROVENANCE_TYPE,
        "subject": [
            {
                "name": f"pkg:docker/dhi/python@{version}&platform={platform}",
                "digest": {"sha256": digest.removeprefix("sha256:")},
            }
        ],
        "predicate": {
            "buildType": "https://github.com/moby/buildkit/blob/master/docs/attestations/slsa-definitions.md"
        },
    }


def _fake_runner(
    *,
    missing: frozenset[str] = frozenset(),
    invalid_subject: frozenset[str] = frozenset(),
    invalid_signature: frozenset[str] = frozenset(),
) -> DockerRunner:
    refs = {"builder": _reference("builder"), "runtime": _reference("runtime")}

    def run(command: list[str], *, capture_output: bool, check: bool, text: bool) -> subprocess.CompletedProcess[str]:
        del capture_output, check, text
        role: Literal["builder", "runtime"] = "builder" if "debian13-dev" in " ".join(command) else "runtime"
        platform = command[command.index("--platform") + 1] if "--platform" in command else PLATFORMS[0]
        if command[1:5] == ["buildx", "imagetools", "inspect", "--raw"]:
            return subprocess.CompletedProcess(command, 0, refs[role][1], "")
        if command[1:4] == ["scout", "attest", "list"]:
            if role in missing:
                listing: list[dict[str, str]] = []
            else:
                subject_digest = PLATFORM_DIGESTS[platform]
                if role in invalid_subject:
                    subject_digest = "sha256:" + "c" * 64
                listing = [
                    {
                        "predicateType": verifier.PROVENANCE_TYPE,
                        "subject": f"dhi.io/python@{subject_digest}",
                    }
                ]
            return subprocess.CompletedProcess(command, 0, json.dumps(listing), "")
        if command[1:4] == ["scout", "attest", "get"]:
            if role in invalid_signature:
                return subprocess.CompletedProcess(command, 1, "", "signature verification failed")
            output_path = Path(command[command.index("--output") + 1])
            output_path.write_text(
                json.dumps(_statement(role, platform, PLATFORM_DIGESTS[platform])),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(f"Unexpected Docker command: {command}")

    return run


@pytest.mark.parametrize("platform", PLATFORMS)
def test_verifies_both_signed_provenance_subjects_against_index_manifests(
    platform: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both variants resolve to the correct per-platform subject on both architectures."""
    builder, _ = _reference("builder")
    runtime, _ = _reference("runtime")
    monkeypatch.setattr(subprocess, "run", _fake_runner())
    key = tmp_path / "key.pub"
    key.write_text("test key", encoding="utf-8")

    result = verifier.verify_candidate(CandidateDhiBases(builder, runtime), platform, key)

    assert result.state == "verified"
    assert result.missing == ()
    assert {item.role for item in result.verified} == {"builder", "runtime"}
    assert {item.manifest_digest for item in result.verified} == {PLATFORM_DIGESTS[platform]}


def test_missing_slsa_evidence_is_separately_classified_for_approval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only an empty successful listing becomes an approval-eligible trust gap."""
    builder, _ = _reference("builder")
    runtime, _ = _reference("runtime")
    monkeypatch.setattr(subprocess, "run", _fake_runner(missing=frozenset({"builder"})))
    key = tmp_path / "key.pub"
    key.write_text("test key", encoding="utf-8")

    result = verifier.verify_candidate(CandidateDhiBases(builder, runtime), "linux/amd64", key)

    assert result.state == "approval_required"
    assert result.missing == ("builder",)


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ({"invalid_subject": frozenset({"runtime"})}, "expected platform manifest"),
        ({"invalid_signature": frozenset({"builder"})}, "signature verification failed"),
    ],
)
def test_invalid_subject_or_signature_never_becomes_an_approval_gap(
    failure: dict[str, frozenset[str]], message: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A listed but mismatched subject or failed signature is an unoverrideable error."""
    builder, _ = _reference("builder")
    runtime, _ = _reference("runtime")
    monkeypatch.setattr(subprocess, "run", _fake_runner(**failure))
    key = tmp_path / "key.pub"
    key.write_text("test key", encoding="utf-8")

    with pytest.raises(verifier.ProvenanceError, match=message):
        verifier.verify_candidate(CandidateDhiBases(builder, runtime), "linux/amd64", key)


def test_verification_command_keeps_full_statement_and_uses_pinned_key_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The verifier requests signature validation without dropping the in-toto subject."""
    builder, _ = _reference("builder")
    key = tmp_path / "key.pub"
    key.write_text("test key", encoding="utf-8")
    commands: list[list[str]] = []
    runner = _fake_runner()

    def capture(
        command: list[str], *, capture_output: bool, check: bool, text: bool
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return runner(command, capture_output=capture_output, check=check, text=text)

    monkeypatch.setattr(subprocess, "run", capture)

    verifier.verify_base(builder, "builder", "linux/amd64", key)

    get_command = next(command for command in commands if command[1:4] == ["scout", "attest", "get"])
    assert "--verify" in get_command
    assert "--skip-tlog" in get_command
    assert "--predicate" not in get_command
    assert get_command[get_command.index("--predicate-type") + 1] == verifier.PROVENANCE_TYPE
