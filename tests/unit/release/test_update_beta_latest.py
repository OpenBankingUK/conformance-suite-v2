"""Offline tests of post-attestation Docker Hub beta alias publication."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from scripts.release_metadata import IMAGE_NAME
from scripts.update_beta_latest import main

pytestmark = pytest.mark.unit

_MANIFEST = b'{"mediaType":"application/vnd.oci.image.index.v1+json","manifests":[]}'
_DIGEST = f"sha256:{hashlib.sha256(_MANIFEST).hexdigest()}"
_ARGS = ["--version", "2.0.0-beta.10", "--digest", _DIGEST]


def _tag_response(tags: list[str]) -> httpx.Response:
    return httpx.Response(
        200,
        request=httpx.Request("GET", "https://hub.docker.com"),
        json={"results": [{"name": tag} for tag in tags], "next": None},
    )


@pytest.mark.parametrize(
    "tags",
    [
        ["2.0.0-beta.10"],
        ["2.0.0-beta.9", "2.0.0-beta.10", "beta-latest", "2.0.0-beta-latest", "2.1.0-beta.1"],
    ],
)
def test_updates_by_digest_and_verifies_alias(tags: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    """The public CLI copies the complete index and reports verified identity."""
    with (
        patch("scripts.list_docker_hub_tags.httpx.get", return_value=_tag_response(tags)),
        patch(
            "scripts.update_beta_latest.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout=_MANIFEST),
        ) as docker,
    ):
        assert main(_ARGS) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["updated"] is True
    assert _DIGEST in result["notice"]
    assert [call.args[0] for call in docker.call_args_list] == [
        ["docker", "buildx", "imagetools", "inspect", f"{IMAGE_NAME}:2.0.0-beta.10", "--raw"],
        [
            "docker",
            "buildx",
            "imagetools",
            "create",
            "--tag",
            f"{IMAGE_NAME}:2.0.0-beta-latest",
            f"{IMAGE_NAME}@{_DIGEST}",
        ],
        ["docker", "buildx", "imagetools", "inspect", f"{IMAGE_NAME}:2.0.0-beta-latest", "--raw"],
    ]


@pytest.mark.parametrize("blocking_tag", ["2.0.0-beta.11", "2.0.0"])
def test_ineligible_beta_never_writes_alias(blocking_tag: str, capsys: pytest.CaptureFixture[str]) -> None:
    """Registry inventory prevents backwards movement and freezes the alias after GA."""
    with (
        patch(
            "scripts.list_docker_hub_tags.httpx.get",
            return_value=_tag_response(["2.0.0-beta.10", blocking_tag]),
        ),
        patch("scripts.update_beta_latest.subprocess.run") as docker,
    ):
        assert main(_ARGS) == 0
    assert json.loads(capsys.readouterr().out)["updated"] is False
    docker.assert_not_called()


def test_other_release_series_skips_alias(capsys: pytest.CaptureFixture[str]) -> None:
    """A future beta promotion succeeds without touching the temporary MVP pointer."""
    with (
        patch("scripts.list_docker_hub_tags.httpx.get", return_value=_tag_response(["2.1.0-beta.1"])),
        patch("scripts.update_beta_latest.subprocess.run") as docker,
    ):
        assert main(["--version", "2.1.0-beta.1", "--digest", _DIGEST]) == 0
    assert json.loads(capsys.readouterr().out)["updated"] is False
    docker.assert_not_called()


@pytest.mark.parametrize("version", ["2.0.0", "2.0.0-dev.1"])
def test_rejects_other_channels(version: str, capsys: pytest.CaptureFixture[str]) -> None:
    """Neither GA nor preview can mutate the beta pointer."""
    with patch("scripts.list_docker_hub_tags.httpx.get") as registry:
        assert main(["--version", version, "--digest", _DIGEST]) == 1
    registry.assert_not_called()
    assert "Only beta" in capsys.readouterr().err


def test_missing_published_version_fails(capsys: pytest.CaptureFixture[str]) -> None:
    """An incomplete or stale registry inventory never permits an alias write."""
    with (
        patch("scripts.list_docker_hub_tags.httpx.get", return_value=_tag_response([])),
        patch("scripts.update_beta_latest.subprocess.run") as docker,
    ):
        assert main(_ARGS) == 1
    docker.assert_not_called()
    assert "not visible in Docker Hub" in capsys.readouterr().err


def test_registry_failure_is_explicit(capsys: pytest.CaptureFixture[str]) -> None:
    """A registry outage fails the job and warns about partial publication."""
    with patch("scripts.list_docker_hub_tags.httpx.get", side_effect=httpx.ConnectError("registry unavailable")):
        assert main(_ARGS) == 1
    assert "version may already be published" in capsys.readouterr().err


@pytest.mark.parametrize("failure_stage", [0, 1, 2])
def test_docker_failures_are_explicit(failure_stage: int, capsys: pytest.CaptureFixture[str]) -> None:
    """Inspect, push and verification failures are never reported as success."""
    results = [
        subprocess.CompletedProcess([], 0, stdout=_MANIFEST),
        subprocess.CompletedProcess([], 0, stdout=b""),
        subprocess.CompletedProcess([], 0, stdout=_MANIFEST),
    ]
    outcomes: list[subprocess.CompletedProcess[bytes] | subprocess.CalledProcessError] = list(results)
    outcomes[failure_stage] = subprocess.CalledProcessError(1, ["docker"])
    with (
        patch("scripts.list_docker_hub_tags.httpx.get", return_value=_tag_response(["2.0.0-beta.10"])),
        patch("scripts.update_beta_latest.subprocess.run", side_effect=outcomes),
    ):
        assert main(_ARGS) == 1
    assert "update failed" in capsys.readouterr().err


@pytest.mark.parametrize("mismatch_stage", [0, 2])
def test_digest_mismatch_fails(mismatch_stage: int, capsys: pytest.CaptureFixture[str]) -> None:
    """Both source and alias must match the exact attested manifest bytes."""
    results = [
        subprocess.CompletedProcess([], 0, stdout=_MANIFEST),
        subprocess.CompletedProcess([], 0, stdout=b""),
        subprocess.CompletedProcess([], 0, stdout=_MANIFEST),
    ]
    results[mismatch_stage] = subprocess.CompletedProcess([], 0, stdout=b"different manifest")
    with (
        patch("scripts.list_docker_hub_tags.httpx.get", return_value=_tag_response(["2.0.0-beta.10"])),
        patch("scripts.update_beta_latest.subprocess.run", side_effect=results) as docker,
    ):
        assert main(_ARGS) == 1
    assert docker.call_count == mismatch_stage + 1
    assert "manifest digest" in capsys.readouterr().err


def test_invalid_digest_never_contacts_registry(capsys: pytest.CaptureFixture[str]) -> None:
    """Malformed manifest digests fail before any external operation."""
    with patch("scripts.list_docker_hub_tags.httpx.get") as registry:
        assert main(["--version", "2.0.0-beta.10", "--digest", "invalid"]) == 1
    registry.assert_not_called()
    assert "SHA-256" in capsys.readouterr().err


def test_workflow_updates_only_after_all_attestations() -> None:
    """The beta-only update is wired after every attestation and uses published outputs."""
    workflow = (Path(__file__).resolve().parents[3] / ".github/workflows/_promote-image.yml").read_text()
    update = workflow.index("- name: Update 2.0.0-beta-latest")
    for step in ("Attest build provenance", "Attest amd64 SBOM", "Attest arm64 SBOM"):
        assert workflow.index(f"- name: {step}") < update
    assert "if: inputs.channel == 'beta'" in workflow[update:]
    assert "steps.publish.outputs.published_digest" in workflow[update:]
    assert "python -m scripts.update_beta_latest" in workflow[update:]
