"""Unit tests for :mod:`scripts.release_notes`."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.release_metadata import ReleaseMetadataError
from scripts.release_notes import (
    check_changelog,
    extract_changelog_section,
    main,
    previous_release_tag,
    render_release_notes,
)

pytestmark = pytest.mark.unit

DIGEST = "sha256:" + "a" * 64

CHANGELOG = """# Changelog

---

## [Unreleased]

### Added

- Pending change.

## [2.0.0] - 2026-11-01

### Changed

- Formal release.

---

## [2.0.0-beta.2] - 2026-10-02

### Fixed

- Beta two fix.

## [2.0.0-beta.1] - 2026-10-01

### Added

- First beta.

[Unreleased]: https://github.com/OpenBankingUK/conformance-suite-v2/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/OpenBankingUK/conformance-suite-v2/compare/v2.0.0-beta.2...v2.0.0
"""

GENERATED = """## What's Changed
* feat: thing by @dev in https://github.com/OpenBankingUK/conformance-suite-v2/pull/1

**Full Changelog**: https://github.com/OpenBankingUK/conformance-suite-v2/compare/v2.0.0-beta.1...v2.0.0-beta.2
"""


class TestExtractChangelogSection:
    """Keep a Changelog section extraction."""

    def test_extracts_body_and_strips_trailing_rule(self) -> None:
        """The section stops at the next version heading and drops a trailing ``---``."""
        assert extract_changelog_section(CHANGELOG, "2.0.0") == "### Changed\n\n- Formal release."

    def test_last_section_stops_at_link_references(self) -> None:
        """Footer compare-link definitions are not part of the final section."""
        assert extract_changelog_section(CHANGELOG, "2.0.0-beta.1") == "### Added\n\n- First beta."

    def test_missing_version_returns_none(self) -> None:
        """An absent version yields ``None``, not another section's content."""
        assert extract_changelog_section(CHANGELOG, "2.0.0-beta.3") is None

    def test_prefix_versions_do_not_match(self) -> None:
        """``2.0.0`` must not match ``2.0.0-beta.1`` or vice versa."""
        assert extract_changelog_section("## [2.0.0-beta.1]\n\n- x\n", "2.0.0") is None

    def test_empty_section_returns_none(self) -> None:
        """A heading with no body counts as missing."""
        assert extract_changelog_section("## [2.0.0]\n\n---\n\n## [1.0.0]\n- y\n", "2.0.0") is None


class TestPreviousReleaseTag:
    """Previous same-channel tag resolution."""

    TAGS = (
        "v1.9.8-beta1",
        "v1.9.9",
        "v2.0.0-beta.1",
        "v2.0.0-beta.2",
        "v2.0.0-beta.10",
        "v2.0.0-dev.1",
        "v2.0.0",
        "not-a-tag",
    )

    def test_beta_uses_highest_earlier_beta_numerically(self) -> None:
        """Beta numbers compare numerically, ignoring GA, preview and legacy tags."""
        assert previous_release_tag("v2.0.0-beta.11", self.TAGS) == "v2.0.0-beta.10"
        assert previous_release_tag("v2.0.0-beta.3", self.TAGS) == "v2.0.0-beta.2"

    def test_first_beta_has_no_previous(self) -> None:
        """The first beta in the dot-numbered scheme has no predecessor."""
        assert previous_release_tag("v2.0.0-beta.1", self.TAGS) is None

    def test_ga_uses_previous_ga(self) -> None:
        """GA compares against the previous GA tag, never a beta."""
        assert previous_release_tag("v2.0.0", self.TAGS) == "v1.9.9"
        assert previous_release_tag("v2.0.1", self.TAGS) == "v2.0.0"

    @pytest.mark.parametrize("tag", ["2.0.0", "v2.0.0-dev.1", "vfoo"])
    def test_rejects_non_release_tags(self, tag: str) -> None:
        """Only ``v``-prefixed GA and beta tags can be released."""
        with pytest.raises(ReleaseMetadataError):
            previous_release_tag(tag, self.TAGS)


class TestCheckChangelog:
    """GA-required, beta-warning CHANGELOG rule."""

    def test_ga_with_section_passes(self) -> None:
        """A GA version with a curated section passes silently."""
        assert check_changelog("2.0.0", CHANGELOG) is None

    def test_ga_without_section_fails(self) -> None:
        """A GA version without a section is a hard failure."""
        with pytest.raises(ReleaseMetadataError, match="GA releases require"):
            check_changelog("2.0.1", CHANGELOG)

    def test_beta_without_section_warns(self) -> None:
        """A beta without a section only warns."""
        warning = check_changelog("2.0.0-beta.3", CHANGELOG)
        assert warning is not None
        assert "2.0.0-beta.3" in warning

    def test_preview_is_ignored(self) -> None:
        """Preview images never get releases, so no rule applies."""
        assert check_changelog("2.0.0-dev.4", CHANGELOG) is None


class TestRenderReleaseNotes:
    """Release body rendering."""

    def test_beta_body_has_separate_sections_in_order(self) -> None:
        """Summary, image reference, and demoted PR notes appear in that order."""
        body = render_release_notes(
            raw_version="2.0.0-beta.2",
            digest=DIGEST,
            changelog=CHANGELOG,
            generated_notes=GENERATED,
            beta_latest_updated=True,
        )
        summary = body.index("## Summary")
        image = body.index("## Docker image")
        pull_requests = body.index("## Pull requests")
        assert summary < image < pull_requests
        assert "- Beta two fix." in body[summary:image]
        assert "docker pull openbanking/conformance-suite-v2:2.0.0-beta.2" in body
        assert f"openbanking/conformance-suite-v2@{DIGEST}" in body
        assert "`2.0.0-beta-latest` now points to this image." in body
        assert "not valid for certification" in body
        assert "### What's Changed" in body[pull_requests:]
        assert "## What's Changed" not in body.replace("### What's Changed", "")

    def test_beta_without_changelog_uses_placeholder(self) -> None:
        """A beta without a section still renders, with an explicit placeholder."""
        body = render_release_notes(
            raw_version="2.0.0-beta.3", digest=DIGEST, changelog=CHANGELOG, generated_notes=GENERATED
        )
        assert "No curated CHANGELOG.md entry" in body
        assert "2.0.0-beta-latest" not in body

    def test_ga_body_mentions_latest(self) -> None:
        """GA notes note the moving ``latest`` tag and omit the beta warning."""
        body = render_release_notes(raw_version="2.0.0", digest=DIGEST, changelog=CHANGELOG, generated_notes="")
        assert "- Formal release." in body
        assert "Also published as `latest`." in body
        assert "certification" not in body
        assert "_No pull requests._" in body

    def test_ga_without_changelog_fails(self) -> None:
        """GA rendering enforces the same CHANGELOG rule as CI."""
        with pytest.raises(ReleaseMetadataError):
            render_release_notes(raw_version="2.0.1", digest=DIGEST, changelog=CHANGELOG, generated_notes="")

    @pytest.mark.parametrize(
        ("version", "digest"),
        [("2.0.0-dev.1", DIGEST), ("2.0.0", "sha256:ABC"), ("2.0.0", "a" * 64)],
    )
    def test_rejects_preview_and_bad_digests(self, version: str, digest: str) -> None:
        """Previews and malformed digests never produce a release body."""
        with pytest.raises(ReleaseMetadataError):
            render_release_notes(raw_version=version, digest=digest, changelog=CHANGELOG, generated_notes="")


class TestMain:
    """CLI behaviour used by CI and the finalize workflow."""

    def _write(self, tmp_path: Path, name: str, text: str) -> Path:
        """Write a file into the temporary directory and return its path."""
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_check_changelog_blocks_ga_from_pyproject(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """CI fails a GA pyproject version with no CHANGELOG section."""
        pyproject = self._write(tmp_path, "pyproject.toml", '[project]\nname = "x"\nversion = "2.0.1"\n')
        changelog = self._write(tmp_path, "CHANGELOG.md", CHANGELOG)
        assert main(["check-changelog", "--pyproject", str(pyproject), "--changelog", str(changelog)]) == 1
        assert "GA releases require" in capsys.readouterr().err

    def test_check_changelog_skips_unchanged_version(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A PR that keeps the base version prepares no release, so a missing GA entry is allowed."""
        pyproject = self._write(tmp_path, "pyproject.toml", '[project]\nname = "x"\nversion = "0.1.0"\n')
        base = self._write(tmp_path, "base.toml", '[project]\nname = "x"\nversion = "0.1.0"\n')
        changelog = self._write(tmp_path, "CHANGELOG.md", CHANGELOG)
        args = ["check-changelog", "--pyproject", str(pyproject), "--changelog", str(changelog)]
        assert main([*args, "--base-pyproject", str(base)]) == 0
        assert "unchanged (0.1.0)" in capsys.readouterr().out
        assert main(args) == 1

    def test_check_changelog_enforces_changed_ga_version(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Changing the version to a GA release without a section fails."""
        pyproject = self._write(tmp_path, "pyproject.toml", '[project]\nname = "x"\nversion = "2.0.1"\n')
        base = self._write(tmp_path, "base.toml", '[project]\nname = "x"\nversion = "2.0.0"\n')
        changelog = self._write(tmp_path, "CHANGELOG.md", CHANGELOG)
        exit_code = main(
            [
                "check-changelog",
                "--pyproject",
                str(pyproject),
                "--base-pyproject",
                str(base),
                "--changelog",
                str(changelog),
            ]
        )
        assert exit_code == 1
        assert "GA releases require" in capsys.readouterr().err

    def test_check_changelog_warns_for_beta_version(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A beta without a section emits a GitHub Actions warning and succeeds."""
        changelog = self._write(tmp_path, "CHANGELOG.md", CHANGELOG)
        assert main(["check-changelog", "--version", "2.0.0-beta.3", "--changelog", str(changelog)]) == 0
        assert capsys.readouterr().out.startswith("::warning::")

    def test_previous_tag_prints_empty_line_when_none(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """No previous tag prints an empty line so the workflow can test for it."""
        tags = self._write(tmp_path, "tags.txt", "v1.9.9\n")
        assert main(["previous-tag", "--tag", "v2.0.0-beta.1", "--tags-file", str(tags)]) == 0
        assert capsys.readouterr().out == "\n"

    def test_render_writes_body(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """The render subcommand writes the full body to stdout."""
        changelog = self._write(tmp_path, "CHANGELOG.md", CHANGELOG)
        generated = self._write(tmp_path, "generated.md", GENERATED)
        exit_code = main(
            [
                "render",
                "--version",
                "2.0.0-beta.2",
                "--digest",
                DIGEST,
                "--changelog",
                str(changelog),
                "--generated-notes",
                str(generated),
                "--beta-latest-updated",
                "true",
            ]
        )
        assert exit_code == 0
        assert capsys.readouterr().out.startswith("## Summary\n\n### Fixed")
