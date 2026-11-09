"""Offline tests for published-image rescan scope and approved-base OpenVEX."""

from __future__ import annotations

import json
import subprocess  # noqa: S404  # test git failures at the subprocess boundary
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest

from scripts import rescan_published_images as rescan
from scripts.list_docker_hub_tags import DockerHubTag
from scripts.validate_docker_base import EXPECTED_RUNTIME_BASE

pytestmark = pytest.mark.unit


def _tag(name: str, digest: str = "a") -> DockerHubTag:
    return DockerHubTag(name, "sha256:" + digest * 64)


def test_supersedes_older_betas_and_keeps_aliases_once_per_digest() -> None:
    """Only current beta.15 and its digest alias survive the old-beta history."""
    records = [_tag(f"2.0.0-beta.{number}", "b") for number in range(2, 15)]
    records += [_tag("2.0.0-beta.15"), _tag("2.0.0-beta-latest")]
    images, superseded = rescan.select_images(records)
    assert len(images) == 1
    assert images[0].version == images[0].tag == "2.0.0-beta.15"
    assert images[0].tags == ["2.0.0-beta-latest", "2.0.0-beta.15"]
    assert set(superseded) == {f"2.0.0-beta.{number}" for number in range(2, 15)}
    assert "beta-latest, 2.0.0-beta.15 (linux/arm64)" in images[0].target("linux/arm64")


def test_channels_are_independent_and_versions_compare_numerically() -> None:
    records = [
        _tag("2.0.0", "b"),
        _tag("2.1.0", "c"),
        _tag("latest", "c"),
        _tag("2.2.0-beta.9", "d"),
        _tag("2.2.0-beta.10", "e"),
        _tag("2.2.0-rc.1", "f"),
    ]
    images, superseded = rescan.select_images(records)
    assert {image.tag for image in images} == {"2.1.0", "2.2.0-beta.10", "2.2.0-rc.1"}
    assert superseded == ["2.0.0", "2.2.0-beta.9"]


def test_unknown_tags_and_their_old_version_aliases_are_kept_conservatively() -> None:
    records = [
        _tag("2.0.0-beta.1", "b"),
        _tag("custom", "b"),
        _tag("2.0.0-beta.2"),
        _tag("preview-feature", "c"),
        _tag("2.0.0-beta.02", "d"),
        _tag("3.0", "e"),
    ]
    images, superseded = rescan.select_images(records)
    assert len(images) == 5
    assert superseded == []
    assert next(image for image in images if "custom" in image.tags).version == "2.0.0-beta.1"
    assert next(image for image in images if "preview-feature" in image.tags).version == ""


@pytest.mark.parametrize(
    "records",
    [
        [],
        [DockerHubTag("latest", None)],
        [DockerHubTag("latest", "invalid")],
        [_tag("bad\nname")],
        [_tag("latest"), _tag("latest", "b")],
    ],
)
def test_invalid_or_empty_enumeration_fails_closed(records: list[DockerHubTag]) -> None:
    with pytest.raises(ValueError):
        rescan.select_images(records)


@pytest.mark.parametrize("version", ["2.0.0-beta.15", "2.0.0-rc.1", "2.0.0"])
def test_base_is_read_from_release_git_tag_and_validated(version: str) -> None:
    calls: list[list[str]] = []

    def runner(args: Sequence[str]) -> str:
        calls.append(list(args))
        return f"v{version}\n" if args[0] == "tag" else f"FROM {EXPECTED_RUNTIME_BASE} AS runtime\n"

    assert rescan.resolve_runtime_base(version, runner) == EXPECTED_RUNTIME_BASE
    assert calls == [["tag", "--list", f"v{version}"], ["show", f"refs/tags/v{version}:Dockerfile"]]


@pytest.mark.parametrize(
    "dockerfile",
    ["FROM python:3.14 AS runtime", "FROM scratch", f"FROM {EXPECTED_RUNTIME_BASE} AS runtime\nFROM scratch"],
)
def test_unapproved_or_ambiguous_base_never_gets_vex(dockerfile: str, capsys: pytest.CaptureFixture[str]) -> None:
    def runner(args: Sequence[str]) -> str:
        return "v2.0.0\n" if args[0] == "tag" else dockerfile

    assert rescan.resolve_runtime_base("2.0.0", runner) == ""
    assert "No approved runtime base" in capsys.readouterr().err


@pytest.mark.parametrize("version", ["", "2.0.0-beta.15"])
def test_unknown_or_missing_git_tag_scans_without_vex(version: str, capsys: pytest.CaptureFixture[str]) -> None:
    def runner(args: Sequence[str]) -> str:
        assert list(args) == ["tag", "--list", "v2.0.0-beta.15"]
        return ""

    assert rescan.resolve_runtime_base(version, runner) == ""
    assert "without VEX" in capsys.readouterr().err


def test_invalid_version_is_not_used_as_git_argument() -> None:
    with pytest.raises(ValueError, match="Invalid release version"):
        rescan.resolve_runtime_base("--help")


def test_git_errors_are_not_downgraded_to_missing_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(args: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert list(args) == ["git", "tag", "--list", "v2.0.0"]
        assert kwargs == {"check": True, "capture_output": True, "text": True}
        raise subprocess.CalledProcessError(128, args)

    monkeypatch.setattr(subprocess, "run", run)
    assert rescan.main(["base", "--version", "2.0.0"]) == 1


@pytest.mark.parametrize("base", [EXPECTED_RUNTIME_BASE, "python:3.14"])
def test_base_cli_prints_only_approved_reference(
    base: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def run(args: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        output = "v2.0.0\n" if args[1] == "tag" else f"FROM {base} AS runtime\n"
        return subprocess.CompletedProcess(args, 0, output)

    monkeypatch.setattr(subprocess, "run", run)
    assert rescan.main(["base", "--version", "2.0.0"]) == 0
    assert capsys.readouterr().out == (EXPECTED_RUNTIME_BASE if base == EXPECTED_RUNTIME_BASE else "") + "\n"


def test_selection_cli_records_exact_current_scope_and_supersession(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def get(url: str, **kwargs: object) -> httpx.Response:
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "results": [
                    {"name": tag.name, "digest": tag.digest}
                    for tag in [_tag("2.0.0-beta.14", "b"), _tag("2.0.0-beta.15"), _tag("2.0.0-beta-latest")]
                ],
                "next": None,
            },
        )

    monkeypatch.setattr(httpx, "get", get)
    output = tmp_path / "scope.json"
    assert rescan.main(["select", "--branches", '["main","release/2.0.0"]', "--output", str(output)]) == 0
    scope = json.loads(output.read_text())
    assert scope["superseded"] == ["2.0.0-beta.14"]
    assert scope["images"] == [
        {
            "tag": "2.0.0-beta.15",
            "digest": "sha256:" + "a" * 64,
            "tags": ["2.0.0-beta-latest", "2.0.0-beta.15"],
            "version": "2.0.0-beta.15",
        }
    ]
    assert scope["expected_targets"] == [
        "branch main (linux/amd64)",
        "branch release/2.0.0 (linux/amd64)",
        "published openbanking/conformance-suite-v2:2.0.0-beta-latest, 2.0.0-beta.15 (linux/amd64)",
        "published openbanking/conformance-suite-v2:2.0.0-beta-latest, 2.0.0-beta.15 (linux/arm64)",
    ]


@pytest.mark.parametrize("branches", ["[]", "{}", '["main",42]', "not-json"])
def test_selection_cli_rejects_invalid_branch_scope(branches: str, tmp_path: Path) -> None:
    output = tmp_path / "scope.json"
    assert rescan.main(["select", "--branches", branches, "--output", str(output)]) == 1
    assert not output.exists()


def test_hub_errors_prevent_a_scope_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: httpx.Response(503, request=httpx.Request("GET", url)))
    output = tmp_path / "scope.json"
    assert rescan.main(["select", "--branches", '["main"]', "--output", str(output)]) == 1
    assert not output.exists()


def test_workflow_uses_selected_digest_base_and_coverage_manifest() -> None:
    """Keep the caller wired to trusted approval, immutable scans and closure evidence."""
    workflow = Path(".github/workflows/security-rescan.yml").read_text()
    published = workflow.split("  scan-image:\n", 1)[1].split("  issues:\n", 1)[0]
    assert "fetch-depth: 0" in published
    assert "scripts.rescan_published_images base" in published
    assert "image: docker.io/openbanking/conformance-suite-v2@${{ matrix.image.digest }}" in published
    assert "vex-image: ${{ steps.base.outputs.runtime_base }}" in published
    assert 'vex-requires-dhi-login: "true"' in published
    assert "join(matrix.image.tags, ', ')" in published
    assert '--scope "$RUNNER_TEMP/scope/rescan-targets.json"' in workflow
    assert '(. == $default or startswith("release/"))' in workflow
