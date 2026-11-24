"""Resolve whether a successful CI run should start an image promotion.

This module is called only by the trusted ``workflow_run`` workflow after it
has checked the CI run and its promotion-manifest artifact. It decides which
channel and GitHub Environment apply, and turns ineligible or already-published
candidates into a successful skip rather than a publication attempt.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from scripts.release_metadata import (
    ReleaseChannel,
    ReleaseMetadataError,
    classify_raw_version,
    read_pyproject_raw_version,
    require_not_already_published,
    validate_branch_compatibility,
)

_BRANCH_CHANNELS: dict[str, tuple[ReleaseChannel, str]] = {
    "main": (ReleaseChannel.BETA, "beta-release"),
}
_SOURCE_SHA_LENGTH = 40


@dataclass(frozen=True)
class AutoPromotionResolution:
    """Decision and workflow-call parameters for an automatic promotion."""

    should_promote: bool
    channel: ReleaseChannel | None
    environment_name: str | None
    tag: str | None
    notice: str | None


def _branch_channel(branch: str) -> tuple[ReleaseChannel, str] | None:
    """Return the expected channel and Environment for a supported branch."""
    if branch in _BRANCH_CHANNELS:
        return _BRANCH_CHANNELS[branch]
    if branch.startswith("preview/"):
        return ReleaseChannel.PREVIEW, "preview-release"
    return None


def resolve_auto_promotion(
    *,
    branch: str,
    raw_version: str,
    source_sha: str,
    manifest_available: bool,
    published_versions: set[str],
) -> AutoPromotionResolution:
    """Decide whether an eligible CI candidate should proceed to publication.

    Args:
        branch: Branch on which the successful CI run completed.
        raw_version: Exact ``[project].version`` from that run's source commit.
        source_sha: Full commit SHA the candidate was built from.
        manifest_available: Whether that CI run uploaded ``promotion-manifest``.
        published_versions: Immutable version tags already present in Docker Hub.

    Returns:
        The automatic promotion decision and, if eligible, its reusable
        workflow inputs.

    Raises:
        ReleaseMetadataError: If the source SHA is malformed.
    """
    if not manifest_available:
        return AutoPromotionResolution(False, None, None, None, "CI run did not produce a promotion-manifest artifact.")

    if len(source_sha) != _SOURCE_SHA_LENGTH or any(character not in "0123456789abcdef" for character in source_sha):
        raise ReleaseMetadataError("Source SHA must be a full lowercase 40-character Git SHA.")

    branch_channel = _branch_channel(branch)
    if branch_channel is None:
        return AutoPromotionResolution(
            False, None, None, None, f"Branch {branch!r} is not configured for automatic image promotion."
        )

    expected_channel, environment_name = branch_channel
    try:
        metadata = classify_raw_version(raw_version)
        validate_branch_compatibility(metadata, branch)
    except ReleaseMetadataError as error:
        return AutoPromotionResolution(
            False,
            expected_channel,
            environment_name,
            None,
            f"Version {raw_version!r} is not eligible for promotion from {branch!r}: {error}",
        )

    if metadata.channel is ReleaseChannel.GA:
        return AutoPromotionResolution(
            False,
            metadata.channel,
            None,
            None,
            "GA publication is paused; a stable version does not enable automatic publication.",
        )

    try:
        require_not_already_published(metadata.raw_version, published_versions)
    except ReleaseMetadataError as error:
        return AutoPromotionResolution(False, expected_channel, environment_name, None, str(error))

    return AutoPromotionResolution(True, metadata.channel, environment_name, None, None)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments for the trusted workflow resolver."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pyproject", type=Path, required=True, help="Trusted copy of candidate pyproject.toml.")
    parser.add_argument("--branch", required=True, help="Branch on which the CI run completed.")
    parser.add_argument("--source-sha", required=True, help="Full SHA used to build the candidate.")
    parser.add_argument(
        "--manifest-available",
        action="store_true",
        help="Set when the CI run uploaded its promotion-manifest artifact.",
    )
    parser.add_argument(
        "--published-versions-file",
        type=Path,
        required=True,
        help="Newline-delimited Docker Hub version tags.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Resolve a workflow decision and print it as JSON."""
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    try:
        raw_version = read_pyproject_raw_version(args.pyproject)
        published_versions = {
            version.strip()
            for version in args.published_versions_file.read_text(encoding="utf-8").splitlines()
            if version.strip()
        }
        resolution = resolve_auto_promotion(
            branch=args.branch,
            raw_version=raw_version,
            source_sha=args.source_sha,
            manifest_available=args.manifest_available,
            published_versions=published_versions,
        )
    except (OSError, ReleaseMetadataError) as error:
        sys.stderr.write(f"error: {error}\n")
        return 1

    sys.stdout.write(
        json.dumps(
            {
                "should_promote": resolution.should_promote,
                "channel": resolution.channel.value if resolution.channel is not None else None,
                "environment_name": resolution.environment_name,
                "tag": resolution.tag,
                "notice": resolution.notice,
            }
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
