"""Unit tests for automatic image-promotion resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.release_metadata import ReleaseChannel, ReleaseMetadataError
from scripts.resolve_auto_promotion import main, resolve_auto_promotion

pytestmark = pytest.mark.unit

_SOURCE_SHA = "a" * 40


@pytest.mark.parametrize(
    ("branch", "raw_version", "channel", "environment_name"),
    [
        ("preview/new-flow", "2.0.0-dev.1", ReleaseChannel.PREVIEW, "preview-release"),
        ("main", "2.0.0-beta.1", ReleaseChannel.BETA, "beta-release"),
    ],
)
def test_resolves_supported_branch_and_version(
    branch: str, raw_version: str, channel: ReleaseChannel, environment_name: str
) -> None:
    """Each supported branch maps a matching candidate to its Environment."""
    result = resolve_auto_promotion(
        branch=branch,
        raw_version=raw_version,
        source_sha=_SOURCE_SHA,
        manifest_available=True,
        published_versions={"beta-latest", "latest"},
    )

    assert result.should_promote is True
    assert result.channel is channel
    assert result.environment_name == environment_name
    assert result.tag is None
    assert result.notice is None


def test_skips_when_candidate_manifest_is_missing() -> None:
    """A successful non-candidate CI run does not wait at an Environment gate."""
    result = resolve_auto_promotion(
        branch="main",
        raw_version="2.0.0",
        source_sha=_SOURCE_SHA,
        manifest_available=False,
        published_versions=set(),
    )

    assert result.should_promote is False
    assert result.notice is not None
    assert "promotion-manifest" in result.notice


@pytest.mark.parametrize(
    ("branch", "raw_version"),
    [
        ("main", "2.0.0-dev.1"),
        ("release/2.0.0", "2.0.0-beta.1"),
        ("release/2.0.0", "2.0.0-dev.1"),
        ("release/2.0.1", "2.0.0-beta.1"),
        ("feature/new-flow", "2.0.0-beta.1"),
        ("preview/new-flow", "2.0.0"),
    ],
)
def test_skips_channel_or_branch_mismatch(branch: str, raw_version: str) -> None:
    """Mismatched release metadata is a successful skip, not an approval request."""
    result = resolve_auto_promotion(
        branch=branch,
        raw_version=raw_version,
        source_sha=_SOURCE_SHA,
        manifest_available=True,
        published_versions=set(),
    )

    assert result.should_promote is False
    assert result.notice is not None


def test_skips_version_already_published() -> None:
    """An immutable Docker Hub version tag already published is not promoted again."""
    result = resolve_auto_promotion(
        branch="main",
        raw_version="2.0.0-beta.1",
        source_sha=_SOURCE_SHA,
        manifest_available=True,
        published_versions={"2.0.0-beta.1", "beta-latest"},
    )

    assert result.should_promote is False
    assert result.notice is not None
    assert "already been published" in result.notice


@pytest.mark.parametrize("raw_version", ["0.1.0", "2.0.0"])
def test_ga_publication_stays_paused(raw_version: str) -> None:
    """Neither a placeholder nor a stable version can enable GA by changing the version alone."""
    result = resolve_auto_promotion(
        branch="main",
        raw_version=raw_version,
        source_sha=_SOURCE_SHA,
        manifest_available=True,
        published_versions=set(),
    )

    assert result.should_promote is False
    assert result.channel is ReleaseChannel.GA
    assert result.environment_name is None
    assert result.tag is None
    assert result.notice is not None
    assert "GA publication is paused" in result.notice


def test_rejects_malformed_source_sha() -> None:
    """Only full lowercase commit IDs may be passed to publication."""
    with pytest.raises(ReleaseMetadataError, match="full lowercase"):
        resolve_auto_promotion(
            branch="main",
            raw_version="2.0.0",
            source_sha="not-a-sha",
            manifest_available=True,
            published_versions=set(),
        )


def test_cli_emits_json_resolution(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The workflow-facing CLI emits the same typed resolution as JSON."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "test"\nversion = "2.0.0-beta.1"\n', encoding="utf-8")
    published_versions = tmp_path / "published.txt"
    published_versions.write_text("", encoding="utf-8")

    exit_code = main(
        [
            "--pyproject",
            str(pyproject),
            "--branch",
            "main",
            "--source-sha",
            _SOURCE_SHA,
            "--manifest-available",
            "--published-versions-file",
            str(published_versions),
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "should_promote": True,
        "channel": "beta",
        "environment_name": "beta-release",
        "tag": None,
        "notice": None,
    }
