"""Release version and channel validation for the hardened container pipeline.

Classifies a project's raw ``pyproject.toml`` version string into a
publication channel (preview ``X.Y.Z-dev.N``, beta ``X.Y.Z-beta.N``, or GA
``X.Y.Z``), checks it is compatible with the branch (and, for GA, the Git tag)
it is being published from, and assembles the machine-readable manifest the
promotion workflow uses to publish an already-built candidate image without
rebuilding it.

``packaging.version.Version`` is used only to validate and order versions —
the raw ``pyproject.toml`` string is always what is published as the image
tag, never a reserialized/normalized form.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from packaging.version import InvalidVersion, Version

IMAGE_NAME = "docker.io/openbanking/conformance-suite-v2"
"""Fully qualified Docker Hub image name (without tag) images are published under."""

MVP_RELEASE = "2.0.0"
"""Release series served by the temporary MVP beta pointer."""

MVP_BETA_TAG = f"{MVP_RELEASE}-beta-latest"
"""Mutable MVP beta tag, frozen once the formal release is published."""


class ReleaseChannel(str, Enum):
    """Publication channel implied by a raw version string's suffix."""

    PREVIEW = "preview"
    BETA = "beta"
    GA = "ga"


class ReleaseMetadataError(ValueError):
    """Raised when release metadata is malformed or violates a publication rule."""


# Non-negative integer with no leading zero (matches SemVer/PEP 440 numeric parts).
_NUMERIC_PART = r"(?:0|[1-9]\d*)"
# Positive integer with no leading zero (build/increment numbers start at 1).
_POSITIVE_PART = r"[1-9]\d*"

_GA_PATTERN = re.compile(rf"^(?P<major>{_NUMERIC_PART})\.(?P<minor>{_NUMERIC_PART})\.(?P<patch>{_NUMERIC_PART})$")
_PREVIEW_PATTERN = re.compile(
    rf"^(?P<major>{_NUMERIC_PART})\.(?P<minor>{_NUMERIC_PART})\.(?P<patch>{_NUMERIC_PART})"
    rf"-dev\.(?P<n>{_POSITIVE_PART})$"
)
_BETA_PATTERN = re.compile(
    rf"^(?P<major>{_NUMERIC_PART})\.(?P<minor>{_NUMERIC_PART})\.(?P<patch>{_NUMERIC_PART})"
    rf"-beta\.(?P<n>{_POSITIVE_PART})$"
)

_PREVIEW_BRANCH_PATTERN = re.compile(r"^preview/[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class ReleaseMetadata:
    """Validated, classified release metadata for one candidate publication."""

    raw_version: str
    """Exact ``[project].version`` string; always used verbatim as the image tag."""

    comparison_version: Version
    """Normalized version used only for validity/ordering comparisons."""

    channel: ReleaseChannel
    """Publication channel implied by ``raw_version``."""

    base_release: str
    """The ``X.Y.Z`` release this version belongs to, with any suffix stripped."""


def classify_raw_version(raw_version: str) -> ReleaseMetadata:
    """Classify and validate a raw version string into release metadata.

    Args:
        raw_version: Exact ``[project].version`` string from ``pyproject.toml``.

    Returns:
        Validated release metadata for the raw version.

    Raises:
        ReleaseMetadataError: If the raw version does not match exactly one of
            the agreed preview/beta/GA raw formats.
    """
    if ga_match := _GA_PATTERN.match(raw_version):
        channel = ReleaseChannel.GA
        match = ga_match
    elif preview_match := _PREVIEW_PATTERN.match(raw_version):
        channel = ReleaseChannel.PREVIEW
        match = preview_match
    elif beta_match := _BETA_PATTERN.match(raw_version):
        channel = ReleaseChannel.BETA
        match = beta_match
    else:
        raise ReleaseMetadataError(
            f"Raw version {raw_version!r} does not match an allowed publication format "
            "(GA 'X.Y.Z', beta 'X.Y.Z-beta.N', or preview 'X.Y.Z-dev.N')."
        )

    return ReleaseMetadata(
        raw_version=raw_version,
        comparison_version=_parse_comparable(raw_version),
        channel=channel,
        base_release=f"{match['major']}.{match['minor']}.{match['patch']}",
    )


def _parse_comparable(raw_version: str) -> Version:
    """Parse a raw version string into a comparable :class:`Version`.

    Args:
        raw_version: Raw version string already matched by one of the
            channel patterns.

    Returns:
        The parsed, orderable version.

    Raises:
        ReleaseMetadataError: If ``packaging`` cannot parse the raw version.
    """
    try:
        return Version(raw_version)
    except InvalidVersion as error:
        raise ReleaseMetadataError(f"Raw version {raw_version!r} is not a valid version: {error}") from error


def validate_branch_compatibility(metadata: ReleaseMetadata, branch: str) -> None:
    """Validate that a branch is allowed to publish the given release metadata.

    Args:
        metadata: Classified release metadata for the candidate version.
        branch: Exact source branch name (e.g. ``preview/new-cert-flow``,
            ``release/2.0.0``, or ``main``).

    Raises:
        ReleaseMetadataError: If the branch and channel are incompatible.
    """
    if metadata.channel is ReleaseChannel.PREVIEW:
        if not _PREVIEW_BRANCH_PATTERN.match(branch):
            raise ReleaseMetadataError(
                f"Preview version {metadata.raw_version!r} must be published from a "
                f"'preview/<feature>' branch, not {branch!r}."
            )
        return

    if metadata.channel is ReleaseChannel.BETA:
        expected_branch = f"release/{metadata.base_release}"
        if branch != expected_branch:
            raise ReleaseMetadataError(
                f"Beta version {metadata.raw_version!r} must be published from {expected_branch!r}, not {branch!r}."
            )
        return

    if branch != "main":
        raise ReleaseMetadataError(
            f"GA version {metadata.raw_version!r} must be published from 'main', not {branch!r}."
        )


def validate_release_tag(metadata: ReleaseMetadata, tag: str) -> None:
    """Validate a Git tag matches a GA release's raw version.

    Args:
        metadata: Classified release metadata; must be a GA channel version.
        tag: Exact Git tag name associated with the release commit.

    Raises:
        ReleaseMetadataError: If ``metadata`` is not GA, or ``tag`` does not
            exactly match ``v<raw_version>``.
    """
    if metadata.channel is not ReleaseChannel.GA:
        raise ReleaseMetadataError("Only GA releases require a Git tag check.")

    expected_tag = f"v{metadata.raw_version}"
    if tag != expected_tag:
        raise ReleaseMetadataError(f"GA release tag must be exactly {expected_tag!r}, got {tag!r}.")


def require_version_increase(metadata: ReleaseMetadata, previous_raw_version: str | None) -> None:
    """Require a candidate version to strictly exceed the previous published one.

    Args:
        metadata: Classified release metadata for the candidate version.
        previous_raw_version: Raw version string of the most recently
            published version on the same branch/channel, or ``None`` if none
            has been published yet.

    Raises:
        ReleaseMetadataError: If ``previous_raw_version`` is malformed, or the
            candidate version is not strictly greater than it.
    """
    if previous_raw_version is None:
        return

    previous_metadata = classify_raw_version(previous_raw_version)
    if metadata.comparison_version <= previous_metadata.comparison_version:
        raise ReleaseMetadataError(
            f"Candidate version {metadata.raw_version!r} must be strictly greater than "
            f"the previously published version {previous_raw_version!r}."
        )


def require_not_already_published(raw_version: str, published_versions: Iterable[str]) -> None:
    """Require a raw version has not already been published.

    Exact version tags are immutable, so publication must fail outright
    rather than overwrite an existing tag.

    Args:
        raw_version: Candidate raw version string.
        published_versions: Raw version strings already published as image
            tags.

    Raises:
        ReleaseMetadataError: If ``raw_version`` exactly matches an already
            published version.
    """
    if raw_version in set(published_versions):
        raise ReleaseMetadataError(f"Version {raw_version!r} has already been published and is immutable.")


def publication_tags(metadata: ReleaseMetadata) -> tuple[str, ...]:
    """Return initial publication tags; the MVP beta pointer moves after attestations."""
    return (metadata.raw_version, "latest") if metadata.channel is ReleaseChannel.GA else (metadata.raw_version,)


def should_update_beta_latest(metadata: ReleaseMetadata, published_tags: Iterable[str]) -> bool:
    """Select the highest MVP beta until Docker Hub contains the formal release."""
    tags = set(published_tags)
    if metadata.channel is not ReleaseChannel.BETA or metadata.base_release != MVP_RELEASE or MVP_RELEASE in tags:
        return False
    return all(
        metadata.comparison_version >= classify_raw_version(tag).comparison_version
        for tag in tags
        if _BETA_PATTERN.fullmatch(tag) and classify_raw_version(tag).base_release == MVP_RELEASE
    )


def read_pyproject_raw_version(pyproject_path: Path) -> str:
    """Read the exact ``[project].version`` string from ``pyproject.toml``.

    Args:
        pyproject_path: Path to the ``pyproject.toml`` file to read.

    Returns:
        The raw version string exactly as written in the file (surrounding
        whitespace stripped only).

    Raises:
        ReleaseMetadataError: If the file is missing, malformed, or does not
            contain a non-empty ``[project].version`` string.
    """
    try:
        with pyproject_path.open("rb") as pyproject_file:
            parsed_pyproject: object = tomllib.load(pyproject_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ReleaseMetadataError(f"Could not read {pyproject_path}: {error}") from error

    project_table = parsed_pyproject.get("project") if isinstance(parsed_pyproject, dict) else None
    raw_version = project_table.get("version") if isinstance(project_table, dict) else None
    if not isinstance(raw_version, str) or not raw_version.strip():
        raise ReleaseMetadataError(f"{pyproject_path} does not contain a non-empty [project].version string.")
    return raw_version.strip()


@dataclass(frozen=True)
class PromotionManifest:
    """Machine-readable candidate manifest consumed by the promotion workflow.

    Identifies exactly what a candidate build produced so the promotion
    workflow can revalidate it and publish the same bytes without rebuilding.
    """

    raw_version: str
    comparison_version: str
    channel: ReleaseChannel
    image_name: str
    source_sha: str
    platform_digests: Mapping[str, str]
    oci_labels: Mapping[str, str]
    expected_tags: tuple[str, ...]

    def to_json_dict(self) -> dict[str, object]:
        """Serialize this manifest to a JSON-compatible dict.

        Returns:
            A dict of JSON-primitive values, suitable for ``json.dumps`` and
            the promotion workflow's checksum step.
        """
        return {
            "raw_version": self.raw_version,
            "comparison_version": self.comparison_version,
            "channel": self.channel.value,
            "image_name": self.image_name,
            "source_sha": self.source_sha,
            "platform_digests": dict(self.platform_digests),
            "oci_labels": dict(self.oci_labels),
            "expected_tags": list(self.expected_tags),
        }


def build_promotion_manifest(
    *,
    metadata: ReleaseMetadata,
    source_sha: str,
    platform_digests: Mapping[str, str],
    oci_labels: Mapping[str, str],
    image_name: str = IMAGE_NAME,
) -> PromotionManifest:
    """Assemble the promotion manifest for one validated candidate build.

    Args:
        metadata: Classified, validated release metadata for the candidate.
        source_sha: Exact Git commit SHA the candidate images were built from.
        platform_digests: Mapping of platform (e.g. ``linux/amd64``) to the
            exact built image digest for that platform.
        oci_labels: OCI image labels attached to the candidate images, used by
            the promotion workflow to cross-check the built artefact against
            this metadata before publishing.
        image_name: Fully qualified registry image name, without a tag.

    Returns:
        The manifest describing exactly what the promotion workflow is
        authorised to publish, and under which tags.

    Raises:
        ReleaseMetadataError: If ``platform_digests`` is empty.
    """
    if not platform_digests:
        raise ReleaseMetadataError("At least one platform digest is required to promote a candidate.")

    expected_tags = publication_tags(metadata)

    return PromotionManifest(
        raw_version=metadata.raw_version,
        comparison_version=str(metadata.comparison_version),
        channel=metadata.channel,
        image_name=image_name,
        source_sha=source_sha,
        platform_digests=dict(platform_digests),
        oci_labels=dict(oci_labels),
        expected_tags=expected_tags,
    )
