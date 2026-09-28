"""Unit tests for :mod:`scripts.release_metadata`."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.release_metadata import (
    IMAGE_NAME,
    ReleaseChannel,
    ReleaseMetadataError,
    build_promotion_manifest,
    classify_raw_version,
    read_pyproject_raw_version,
    require_not_already_published,
    require_version_increase,
    validate_branch_compatibility,
    validate_release_tag,
)

pytestmark = pytest.mark.unit


class TestClassifyRawVersion:
    """Behaviour of :func:`classify_raw_version` for valid and invalid inputs."""

    @pytest.mark.parametrize(
        ("raw_version", "expected_channel", "expected_base_release"),
        [
            ("2.0.0", ReleaseChannel.GA, "2.0.0"),
            ("0.1.0", ReleaseChannel.GA, "0.1.0"),
            ("2.0.0-beta.1", ReleaseChannel.BETA, "2.0.0"),
            ("2.0.0-beta.10", ReleaseChannel.BETA, "2.0.0"),
            ("2.0.0-dev.1", ReleaseChannel.PREVIEW, "2.0.0"),
            ("2.0.0-dev.42", ReleaseChannel.PREVIEW, "2.0.0"),
        ],
    )
    def test_classifies_allowed_formats(
        self, raw_version: str, expected_channel: ReleaseChannel, expected_base_release: str
    ) -> None:
        """Each allowed raw format is classified into the correct channel."""
        metadata = classify_raw_version(raw_version)
        assert metadata.raw_version == raw_version
        assert metadata.channel is expected_channel
        assert metadata.base_release == expected_base_release

    @pytest.mark.parametrize(
        "raw_version",
        [
            "v2.0.0",  # no 'v' prefix allowed
            "2.0",  # missing patch
            "2.0.0.1",  # extra component
            "2.0.0-alpha.1",  # only dev/beta suffixes are allowed
            "2.0.0-beta",  # missing build number
            "2.0.0-beta.0",  # build numbers start at 1
            "2.0.0-beta.01",  # leading zero
            "2.0.0-dev",  # missing build number
            "2.0.0.dev1",  # must use hyphenated raw form, not PEP 440 native form
            "02.0.0",  # leading zero in a numeric part
            "",
            "not-a-version",
        ],
    )
    def test_rejects_disallowed_formats(self, raw_version: str) -> None:
        """Anything outside the exact GA/beta/preview raw formats is rejected."""
        with pytest.raises(ReleaseMetadataError):
            classify_raw_version(raw_version)

    def test_comparison_version_orders_channels_correctly(self) -> None:
        """A GA version compares greater than its beta and preview precursors."""
        preview = classify_raw_version("2.0.0-dev.1")
        beta = classify_raw_version("2.0.0-beta.1")
        ga = classify_raw_version("2.0.0")
        assert preview.comparison_version < beta.comparison_version < ga.comparison_version


class TestValidateBranchCompatibility:
    """Behaviour of :func:`validate_branch_compatibility`."""

    def test_preview_requires_preview_branch(self) -> None:
        """A preview version may only be published from a preview/<feature> branch."""
        metadata = classify_raw_version("2.0.0-dev.1")
        validate_branch_compatibility(metadata, "preview/new-cert-flow")
        with pytest.raises(ReleaseMetadataError):
            validate_branch_compatibility(metadata, "develop")

    def test_beta_requires_matching_release_branch(self) -> None:
        """A beta version may only be published from its matching release/<version> branch."""
        metadata = classify_raw_version("2.0.0-beta.1")
        validate_branch_compatibility(metadata, "release/2.0.0")
        with pytest.raises(ReleaseMetadataError):
            validate_branch_compatibility(metadata, "release/2.0.1")
        with pytest.raises(ReleaseMetadataError):
            validate_branch_compatibility(metadata, "main")

    def test_ga_requires_main(self) -> None:
        """A GA version may only be published from main."""
        metadata = classify_raw_version("2.0.0")
        validate_branch_compatibility(metadata, "main")
        with pytest.raises(ReleaseMetadataError):
            validate_branch_compatibility(metadata, "release/2.0.0")


class TestValidateReleaseTag:
    """Behaviour of :func:`validate_release_tag`."""

    def test_ga_tag_must_match_exactly(self) -> None:
        """A GA release tag must be exactly v<raw_version>."""
        metadata = classify_raw_version("2.0.0")
        validate_release_tag(metadata, "v2.0.0")
        with pytest.raises(ReleaseMetadataError):
            validate_release_tag(metadata, "2.0.0")
        with pytest.raises(ReleaseMetadataError):
            validate_release_tag(metadata, "v2.0.1")

    def test_non_ga_channel_rejected(self) -> None:
        """Only GA releases require or accept a Git tag check."""
        metadata = classify_raw_version("2.0.0-beta.1")
        with pytest.raises(ReleaseMetadataError):
            validate_release_tag(metadata, "v2.0.0-beta.1")


class TestRequireVersionIncrease:
    """Behaviour of :func:`require_version_increase`."""

    def test_allows_first_publication(self) -> None:
        """No previous version means any valid candidate is allowed."""
        metadata = classify_raw_version("2.0.0-beta.1")
        require_version_increase(metadata, None)

    def test_requires_strict_increase(self) -> None:
        """Equal or lower previous versions are rejected."""
        metadata = classify_raw_version("2.0.0-beta.1")
        require_version_increase(metadata, "2.0.0-dev.9")
        with pytest.raises(ReleaseMetadataError):
            require_version_increase(metadata, "2.0.0-beta.1")
        with pytest.raises(ReleaseMetadataError):
            require_version_increase(metadata, "2.0.0-beta.2")

    def test_rejects_malformed_previous_version(self) -> None:
        """A malformed previous version fails validation rather than being ignored."""
        metadata = classify_raw_version("2.0.0")
        with pytest.raises(ReleaseMetadataError):
            require_version_increase(metadata, "not-a-version")


class TestRequireNotAlreadyPublished:
    """Behaviour of :func:`require_not_already_published`."""

    def test_rejects_exact_duplicate(self) -> None:
        """An already published raw version cannot be republished."""
        with pytest.raises(ReleaseMetadataError):
            require_not_already_published("2.0.0", ["1.9.0", "2.0.0"])

    def test_allows_new_version(self) -> None:
        """A version not present in the published set is allowed."""
        require_not_already_published("2.0.0", ["1.9.0"])


class TestReadPyprojectRawVersion:
    """Behaviour of :func:`read_pyproject_raw_version`."""

    def test_reads_raw_version_verbatim(self, tmp_path: Path) -> None:
        """The exact string in [project].version is returned, not reserialized."""
        pyproject_path = tmp_path / "pyproject.toml"
        pyproject_path.write_text('[project]\nname = "x"\nversion = "2.0.0-beta.1"\n')
        assert read_pyproject_raw_version(pyproject_path) == "2.0.0-beta.1"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        """A missing pyproject.toml is a validation error, not an uncaught OSError."""
        with pytest.raises(ReleaseMetadataError):
            read_pyproject_raw_version(tmp_path / "missing.toml")

    def test_missing_version_raises(self, tmp_path: Path) -> None:
        """A pyproject.toml without [project].version is a validation error."""
        pyproject_path = tmp_path / "pyproject.toml"
        pyproject_path.write_text('[project]\nname = "x"\n')
        with pytest.raises(ReleaseMetadataError):
            read_pyproject_raw_version(pyproject_path)

    def test_malformed_toml_raises(self, tmp_path: Path) -> None:
        """Malformed TOML is a validation error, not an uncaught exception."""
        pyproject_path = tmp_path / "pyproject.toml"
        pyproject_path.write_text("not valid toml [[[")
        with pytest.raises(ReleaseMetadataError):
            read_pyproject_raw_version(pyproject_path)


class TestBuildPromotionManifest:
    """Behaviour of :func:`build_promotion_manifest`."""

    def test_ga_expects_version_and_latest_tags(self) -> None:
        """A GA promotion manifest expects both the exact version and latest tags."""
        metadata = classify_raw_version("2.0.0")
        manifest = build_promotion_manifest(
            metadata=metadata,
            source_sha="a" * 40,
            platform_digests={"linux/amd64": "sha256:aaa", "linux/arm64": "sha256:bbb"},
            oci_labels={"org.opencontainers.image.version": "2.0.0"},
        )
        assert manifest.expected_tags == ("2.0.0", "latest")
        assert manifest.image_name == IMAGE_NAME
        assert manifest.to_json_dict()["expected_tags"] == ["2.0.0", "latest"]

    def test_non_ga_expects_only_exact_version_tag(self) -> None:
        """Preview and beta promotion manifests never publish latest."""
        metadata = classify_raw_version("2.0.0-beta.1")
        manifest = build_promotion_manifest(
            metadata=metadata,
            source_sha="b" * 40,
            platform_digests={"linux/amd64": "sha256:ccc"},
            oci_labels={},
        )
        assert manifest.expected_tags == ("2.0.0-beta.1",)

    def test_requires_at_least_one_platform_digest(self) -> None:
        """An empty platform digest map cannot be promoted."""
        metadata = classify_raw_version("2.0.0")
        with pytest.raises(ReleaseMetadataError):
            build_promotion_manifest(
                metadata=metadata,
                source_sha="c" * 40,
                platform_digests={},
                oci_labels={},
            )
