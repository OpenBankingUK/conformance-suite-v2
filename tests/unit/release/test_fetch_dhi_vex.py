"""Tests for OCI signature, digest, subject and replacement-component boundaries."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts.fetch_dhi_vex import IN_TOTO, OPENVEX, extract_predicate, fetch_vex
from scripts.validate_docker_base import EXPECTED_RUNTIME_BASE
from scripts.vulnerability_gate import GateInputError

pytestmark = pytest.mark.unit
IMAGE = "dhi.io/python:3.14-debian13@sha256:" + "1" * 64
SUBJECT = "sha256:" + "2" * 64


def _statement(subject: str = SUBJECT) -> dict[str, object]:
    return {
        "_type": "https://in-toto.io/Statement/v0.1",
        "predicateType": OPENVEX,
        "subject": [{"digest": {"sha256": subject.removeprefix("sha256:")}}],
        "predicate": {
            "author": "Docker Hardened Images <dhi@docker.com>",
            "statements": [
                {
                    "vulnerability": {"name": "CVE-2026-0001"},
                    "status": "not_affected",
                    "products": [{"@id": purl}],
                }
                for purl in (
                    "pkg:deb/debian/zlib@1.3.1%2Bdhi4",
                    "pkg:deb/debian/python-3.14@3.14.8",
                    "pkg:deb/debian/libpython-3.14-minimal@3.14.8",
                    "pkg:deb/debian/conformance-python@3.14.9",
                    "pkg:pypi/pip@26.2.2",
                )
            ],
        },
    }


def test_extraction_retains_only_unchanged_os_components() -> None:
    predicate = extract_predicate(_statement(), SUBJECT)
    assert predicate["statements"] == [
        {
            "vulnerability": {"name": "CVE-2026-0001"},
            "status": "not_affected",
            "products": [{"@id": "pkg:deb/debian/zlib@1.3.1%2Bdhi4"}],
        }
    ]


@pytest.mark.parametrize("change", ["subject", "predicate", "type", "author"])
def test_malformed_or_misbound_statement_fails_closed(change: str) -> None:
    statement = _statement()
    if change == "subject":
        statement["subject"] = [{"digest": {"sha256": "3" * 64}}]
    elif change == "author":
        statement["predicate"] = {"author": "Mallory", "statements": []}
    elif change == "predicate":
        statement["predicateType"] = "https://untrusted.example"
    else:
        statement["_type"] = "not-in-toto"
    with pytest.raises(GateInputError):
        extract_predicate(statement, SUBJECT)


@pytest.mark.parametrize(
    "failure",
    [
        "none",
        "absent",
        "signature",
        "artifact-digest",
        "artifact-subject",
        "layer-digest",
        "statement-subject",
        "multiple",
    ],
)
def test_fetch_checks_signature_and_entire_digest_chain(monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    platform_manifest = b'{"schemaVersion":2,"layers":[]}'
    subject_digest = "sha256:" + hashlib.sha256(platform_manifest).hexdigest()
    statement = json.dumps(_statement(subject_digest if failure != "statement-subject" else SUBJECT)).encode()
    layer_digest = "sha256:" + hashlib.sha256(statement).hexdigest()
    artifact = json.dumps(
        {
            "subject": {"digest": subject_digest if failure != "artifact-subject" else SUBJECT},
            "layers": [{"mediaType": IN_TOTO, "digest": layer_digest}],
        }
    ).encode()
    artifact_digest = "sha256:" + hashlib.sha256(artifact).hexdigest()
    commands: list[tuple[str, ...]] = []

    def run(args: tuple[str, ...], *, check: bool, stdout: int) -> subprocess.CompletedProcess[bytes]:
        assert check and stdout == subprocess.PIPE
        commands.append(args)
        if args[0] == "cosign":
            if failure == "signature":
                raise subprocess.CalledProcessError(1, args)
            data = b"verified"
        elif args[1:3] == ("artifact", "list"):
            descriptors = [{"digest": artifact_digest}]
            if failure == "absent":
                descriptors = []
            elif failure == "multiple":
                descriptors *= 2
            data = json.dumps(descriptors).encode()
        elif args[1:3] == ("blob", "get"):
            data = statement if failure != "layer-digest" else b"tampered"
        elif "--platform" in args:
            data = platform_manifest
        else:
            data = artifact if failure != "artifact-digest" else b"tampered"
        return subprocess.CompletedProcess(args, 0, data)

    monkeypatch.setattr(subprocess, "run", run)
    if failure not in {"none", "absent"}:
        with pytest.raises((GateInputError, subprocess.CalledProcessError)):
            fetch_vex(IMAGE, "linux/arm64", Path("key.pub"))
    else:
        result = fetch_vex(IMAGE, "linux/arm64", Path("key.pub"))
        assert (result is None) == (failure == "absent")
    if failure not in {"absent", "multiple"}:
        verify = next(args for args in commands if args[0] == "cosign")
        assert verify[-1] == f"dhi.io/python@{artifact_digest}"
        assert "--key" in verify
        assert commands.index(verify) == 2


@pytest.mark.parametrize("image", ["dhi.io/python:latest", "--help", "other.io/python@" + SUBJECT])
def test_mutable_or_untrusted_references_are_rejected(image: str) -> None:
    with pytest.raises(GateInputError, match="immutable Python reference"):
        fetch_vex(image, "linux/arm64", Path("key.pub"))


@pytest.mark.parametrize("matches", [True, False])
def test_published_image_requires_assessed_base_layer_prefix(monkeypatch: pytest.MonkeyPatch, matches: bool) -> None:
    calls: list[tuple[str, ...]] = []
    base_layer = {"digest": "sha256:" + "3" * 64}
    other_layer = {"digest": "sha256:" + "4" * 64}

    def run(args: tuple[str, ...], *, check: bool, stdout: int) -> subprocess.CompletedProcess[bytes]:
        assert check and stdout == subprocess.PIPE
        calls.append(args)
        if args[1:3] == ("artifact", "list"):
            data = b"null"
        elif args[3] == EXPECTED_RUNTIME_BASE:
            data = json.dumps({"layers": [base_layer]}).encode()
        else:
            data = json.dumps({"layers": [base_layer if matches else other_layer, other_layer]}).encode()
        return subprocess.CompletedProcess(args, 0, data)

    monkeypatch.setattr(subprocess, "run", run)

    assert fetch_vex("docker.io/openbanking/conformance-suite-v2:2.0.0-beta.13", "linux/amd64", Path("key.pub")) is None
    assert any(args[1:3] == ("artifact", "list") for args in calls) == matches


def test_scout_receives_raw_findings_and_promotion_checks_the_fetcher() -> None:
    root = Path(__file__).resolve().parents[3]
    action = (root / ".github/actions/vulnerability-scan/action.yml").read_text()
    assert "--vex-author '^$'" in action
    assert "python -m scripts.fetch_dhi_vex" in action
    assert "docker scout vex get" not in action
    assert "scripts/fetch_dhi_vex.py \\" in (root / ".github/workflows/_promote-image.yml").read_text()
