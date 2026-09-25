"""Unit tests for :mod:`scripts.build_promotion_manifest` (the CI-facing CLI)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build_promotion_manifest import main

pytestmark = pytest.mark.unit


def _write_pyproject(tmp_path: Path, version: str) -> Path:
    """Write a minimal pyproject.toml with the given raw version.

    Args:
        tmp_path: Pytest-provided temporary directory.
        version: Raw ``[project].version`` string to write.

    Returns:
        Path to the written ``pyproject.toml`` file.
    """
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(f'[project]\nname = "x"\nversion = "{version}"\n')
    return pyproject_path


class TestMain:
    """Behaviour of the ``build_promotion_manifest`` CLI entrypoint."""

    def test_builds_manifest_for_valid_preview_candidate(self, tmp_path: Path) -> None:
        """A valid preview candidate writes the expected manifest JSON."""
        pyproject_path = _write_pyproject(tmp_path, "2.0.0-dev.1")
        output_path = tmp_path / "promotion-manifest.json"

        exit_code = main(
            [
                "--pyproject",
                str(pyproject_path),
                "--branch",
                "preview/new-cert-flow",
                "--source-sha",
                "abc123",
                "--platform-digest",
                "linux/amd64=sha256:aaa",
                "--platform-digest",
                "linux/arm64=sha256:bbb",
                "--oci-label",
                "org.opencontainers.image.revision=abc123",
                "--output",
                str(output_path),
            ]
        )

        assert exit_code == 0
        manifest = json.loads(output_path.read_text())
        assert manifest == {
            "raw_version": "2.0.0-dev.1",
            "comparison_version": "2.0.0.dev1",
            "channel": "preview",
            "image_name": "ghcr.io/openbankinguk/conformance-suite-v2",
            "source_sha": "abc123",
            "platform_digests": {
                "linux/amd64": "sha256:aaa",
                "linux/arm64": "sha256:bbb",
            },
            "oci_labels": {"org.opencontainers.image.revision": "abc123"},
            "expected_tags": ["2.0.0-dev.1"],
        }

    def test_ga_candidate_expects_latest_tag(self, tmp_path: Path) -> None:
        """A GA candidate's manifest expects both the version and latest tags."""
        pyproject_path = _write_pyproject(tmp_path, "2.0.0")
        output_path = tmp_path / "promotion-manifest.json"

        exit_code = main(
            [
                "--pyproject",
                str(pyproject_path),
                "--branch",
                "main",
                "--source-sha",
                "abc123",
                "--platform-digest",
                "linux/amd64=sha256:aaa",
                "--output",
                str(output_path),
            ]
        )

        assert exit_code == 0
        manifest = json.loads(output_path.read_text())
        assert manifest["expected_tags"] == ["2.0.0", "latest"]

    def test_rejects_mismatched_branch(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A preview version on a non-preview branch fails before writing anything."""
        pyproject_path = _write_pyproject(tmp_path, "2.0.0-dev.1")
        output_path = tmp_path / "promotion-manifest.json"

        exit_code = main(
            [
                "--pyproject",
                str(pyproject_path),
                "--branch",
                "main",
                "--source-sha",
                "abc123",
                "--platform-digest",
                "linux/amd64=sha256:aaa",
                "--output",
                str(output_path),
            ]
        )

        assert exit_code == 1
        assert "preview" in capsys.readouterr().err
        assert not output_path.exists()

    def test_rejects_malformed_platform_digest(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A --platform-digest value without an '=' fails with an actionable error."""
        pyproject_path = _write_pyproject(tmp_path, "2.0.0-dev.1")
        output_path = tmp_path / "promotion-manifest.json"

        exit_code = main(
            [
                "--pyproject",
                str(pyproject_path),
                "--branch",
                "preview/new-cert-flow",
                "--source-sha",
                "abc123",
                "--platform-digest",
                "linux-amd64-sha256-aaa",
                "--output",
                str(output_path),
            ]
        )

        assert exit_code == 1
        assert "--platform-digest" in capsys.readouterr().err
        assert not output_path.exists()

    def test_rejects_empty_platform_digests(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """At least one platform digest is required to build a manifest."""
        pyproject_path = _write_pyproject(tmp_path, "2.0.0-dev.1")
        output_path = tmp_path / "promotion-manifest.json"

        exit_code = main(
            [
                "--pyproject",
                str(pyproject_path),
                "--branch",
                "preview/new-cert-flow",
                "--source-sha",
                "abc123",
                "--output",
                str(output_path),
            ]
        )

        assert exit_code == 1
        assert "platform digest" in capsys.readouterr().err
        assert not output_path.exists()
