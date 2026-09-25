"""Unit tests for :mod:`scripts.validate_release` (the CI-facing CLI)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.validate_release import main

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
    """Behaviour of the ``validate_release`` CLI entrypoint."""

    def test_valid_preview_candidate_succeeds(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A preview version on a matching preview branch exits 0 and prints JSON metadata."""
        pyproject_path = _write_pyproject(tmp_path, "2.0.0-dev.1")
        exit_code = main(
            [
                "--pyproject",
                str(pyproject_path),
                "--branch",
                "preview/new-cert-flow",
            ]
        )
        assert exit_code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload == {
            "raw_version": "2.0.0-dev.1",
            "comparison_version": "2.0.0.dev1",
            "channel": "preview",
            "base_release": "2.0.0",
        }

    def test_ga_candidate_requires_tag(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A GA version without --tag fails with an actionable error."""
        pyproject_path = _write_pyproject(tmp_path, "2.0.0")
        exit_code = main(["--pyproject", str(pyproject_path), "--branch", "main"])
        assert exit_code == 1
        assert "--tag" in capsys.readouterr().err

    def test_ga_candidate_with_matching_tag_succeeds(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A GA version with a matching tag on main exits 0."""
        pyproject_path = _write_pyproject(tmp_path, "2.0.0")
        exit_code = main(
            [
                "--pyproject",
                str(pyproject_path),
                "--branch",
                "main",
                "--tag",
                "v2.0.0",
            ]
        )
        assert exit_code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["channel"] == "ga"

    def test_mismatched_branch_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A beta version on the wrong branch fails with an actionable error."""
        pyproject_path = _write_pyproject(tmp_path, "2.0.0-beta.1")
        exit_code = main(
            [
                "--pyproject",
                str(pyproject_path),
                "--branch",
                "release/2.0.1",
            ]
        )
        assert exit_code == 1
        assert "release/2.0.0" in capsys.readouterr().err

    def test_non_increasing_previous_version_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A candidate not strictly greater than the previous version fails."""
        pyproject_path = _write_pyproject(tmp_path, "2.0.0-beta.1")
        exit_code = main(
            [
                "--pyproject",
                str(pyproject_path),
                "--branch",
                "release/2.0.0",
                "--previous-version",
                "2.0.0-beta.1",
            ]
        )
        assert exit_code == 1
        assert "strictly greater" in capsys.readouterr().err
