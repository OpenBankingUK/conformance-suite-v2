"""Unit tests for :mod:`scripts.validate_promotion` (the CI-facing CLI)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.validate_promotion import main

pytestmark = pytest.mark.unit


def _write_manifest(tmp_path: Path, **overrides: object) -> Path:
    """Write a promotion manifest JSON file with sensible preview defaults.

    Args:
        tmp_path: Pytest-provided temporary directory.
        **overrides: Fields to override on top of the default preview manifest.

    Returns:
        Path to the written manifest JSON file.
    """
    manifest: dict[str, object] = {
        "raw_version": "2.0.0-dev.1",
        "comparison_version": "2.0.0.dev1",
        "channel": "preview",
        "image_name": "ghcr.io/openbankinguk/conformance-suite-v2",
        "source_sha": "a" * 40,
        "platform_digests": {
            "linux/amd64": "sha256:" + "1" * 64,
            "linux/arm64": "sha256:" + "2" * 64,
        },
        "oci_labels": {
            "org.opencontainers.image.revision": "a" * 40,
            "org.opencontainers.image.version": "2.0.0-dev.1",
        },
        "expected_tags": ["2.0.0-dev.1"],
    }
    manifest.update(overrides)
    if "raw_version" in overrides and "oci_labels" not in overrides:
        labels = manifest["oci_labels"]
        assert isinstance(labels, dict)
        labels["org.opencontainers.image.version"] = manifest["raw_version"]
    manifest_path = tmp_path / "promotion-manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    return manifest_path


class TestMain:
    """Behaviour of the ``validate_promotion`` CLI entrypoint."""

    def test_accepts_valid_preview_manifest(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A manifest matching its requested source SHA and branch is accepted."""
        manifest_path = _write_manifest(tmp_path)

        exit_code = main(
            [
                "--manifest",
                str(manifest_path),
                "--source-sha",
                "a" * 40,
                "--branch",
                "preview/new-cert-flow",
                "--channel",
                "preview",
            ]
        )

        assert exit_code == 0
        result = json.loads(capsys.readouterr().out)
        assert result["expected_tags"] == ["2.0.0-dev.1"]

    def test_ga_manifest_expects_latest_tag(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A valid GA manifest's expected tags include the version and latest."""
        manifest_path = _write_manifest(tmp_path, raw_version="2.0.0", channel="ga")

        exit_code = main(
            [
                "--manifest",
                str(manifest_path),
                "--source-sha",
                "a" * 40,
                "--branch",
                "main",
                "--channel",
                "ga",
                "--tag",
                "v2.0.0",
            ]
        )

        assert exit_code == 0
        result = json.loads(capsys.readouterr().out)
        assert result["expected_tags"] == ["2.0.0", "latest"]

    def test_ga_manifest_without_tag_is_rejected(self, tmp_path: Path) -> None:
        """A GA promotion request missing --tag is rejected."""
        manifest_path = _write_manifest(tmp_path, raw_version="2.0.0", channel="ga")

        exit_code = main(
            [
                "--manifest",
                str(manifest_path),
                "--source-sha",
                "a" * 40,
                "--branch",
                "main",
                "--channel",
                "ga",
            ]
        )

        assert exit_code == 1

    def test_rejects_mismatched_source_sha(self, tmp_path: Path) -> None:
        """A manifest whose source SHA differs from the request is rejected."""
        manifest_path = _write_manifest(tmp_path)

        exit_code = main(
            [
                "--manifest",
                str(manifest_path),
                "--source-sha",
                "b" * 40,
                "--branch",
                "preview/new-cert-flow",
                "--channel",
                "preview",
            ]
        )

        assert exit_code == 1

    def test_rejects_mismatched_branch(self, tmp_path: Path) -> None:
        """A preview manifest requested for an incompatible branch is rejected."""
        manifest_path = _write_manifest(tmp_path)

        exit_code = main(
            [
                "--manifest",
                str(manifest_path),
                "--source-sha",
                "a" * 40,
                "--branch",
                "main",
                "--channel",
                "preview",
            ]
        )

        assert exit_code == 1

    def test_rejects_incomplete_platform_set(self, tmp_path: Path) -> None:
        """A manifest missing a required platform digest is rejected."""
        manifest_path = _write_manifest(tmp_path, platform_digests={"linux/amd64": "sha256:" + "1" * 64})

        exit_code = main(
            [
                "--manifest",
                str(manifest_path),
                "--source-sha",
                "a" * 40,
                "--branch",
                "preview/new-cert-flow",
                "--channel",
                "preview",
            ]
        )

        assert exit_code == 1

    def test_rejects_already_published_version(self, tmp_path: Path) -> None:
        """A version already present in the published-versions file is rejected."""
        manifest_path = _write_manifest(tmp_path)
        published_path = tmp_path / "published-versions.txt"
        published_path.write_text("2.0.0-dev.1\n1.9.0\n")

        exit_code = main(
            [
                "--manifest",
                str(manifest_path),
                "--source-sha",
                "a" * 40,
                "--branch",
                "preview/new-cert-flow",
                "--channel",
                "preview",
                "--published-versions-file",
                str(published_path),
            ]
        )

        assert exit_code == 1

    def test_missing_manifest_file_is_rejected(self, tmp_path: Path) -> None:
        """A manifest path that does not exist fails cleanly rather than crashing."""
        exit_code = main(
            [
                "--manifest",
                str(tmp_path / "missing.json"),
                "--source-sha",
                "a" * 40,
                "--branch",
                "preview/new-cert-flow",
                "--channel",
                "preview",
            ]
        )

        assert exit_code == 1

    def test_rejects_channel_mismatch(self, tmp_path: Path) -> None:
        """A caller cannot promote a manifest through a different channel workflow."""
        manifest_path = _write_manifest(tmp_path)

        exit_code = main(
            [
                "--manifest",
                str(manifest_path),
                "--source-sha",
                "a" * 40,
                "--branch",
                "preview/new-cert-flow",
                "--channel",
                "beta",
            ]
        )

        assert exit_code == 1

    def test_rejects_unexpected_image_name(self, tmp_path: Path) -> None:
        """A manifest cannot redirect publication to another registry package."""
        manifest_path = _write_manifest(tmp_path, image_name="ghcr.io/example/other")

        exit_code = main(
            [
                "--manifest",
                str(manifest_path),
                "--source-sha",
                "a" * 40,
                "--branch",
                "preview/new-cert-flow",
                "--channel",
                "preview",
            ]
        )

        assert exit_code == 1

    def test_rejects_mismatched_revision_label(self, tmp_path: Path) -> None:
        """The OCI revision label must bind the image to the requested source SHA."""
        manifest_path = _write_manifest(
            tmp_path,
            oci_labels={"org.opencontainers.image.revision": "b" * 40},
        )

        exit_code = main(
            [
                "--manifest",
                str(manifest_path),
                "--source-sha",
                "a" * 40,
                "--branch",
                "preview/new-cert-flow",
                "--channel",
                "preview",
            ]
        )

        assert exit_code == 1
