"""Command-line entrypoint revalidating a candidate promotion manifest.

Used by the preview/beta/GA promotion workflows immediately before they
publish. Re-derives release metadata from the manifest's own raw version
(never from branch-supplied code) and cross-checks it against the exact
source SHA, branch, tag (for GA), platform set, and previously published
versions supplied by the workflow, then prints the manifest's expected tags
as JSON so the workflow does not have to duplicate that tag-construction
logic. The ungated candidate-location job also uses this entrypoint with
``--summary-file`` and ``--environment-name`` to show proposed release details
before approval, without changing the JSON output contract.

Usage::

    uv run python -m scripts.validate_promotion \\
        --manifest promotion-manifest.json \\
        --source-sha "$GITHUB_SHA" \\
        --branch release/2.0.0 \\
        --tag v2.0.0 \\
        --published-versions-file published-versions.txt
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from html import escape
from pathlib import Path

from scripts.release_metadata import (
    IMAGE_NAME,
    MVP_BETA_TAG,
    ReleaseChannel,
    ReleaseMetadata,
    ReleaseMetadataError,
    classify_raw_version,
    publication_tags,
    require_not_already_published,
    should_update_beta_latest,
    validate_branch_compatibility,
    validate_release_tag,
)

_REQUIRED_PLATFORMS = frozenset({"linux/amd64", "linux/arm64"})
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def _summary_code(value: str) -> str:
    return "<code>" + escape(value).replace("|", "&#124;").replace("\r", " ").replace("\n", " ") + "</code>"


def _render_summary(
    metadata: ReleaseMetadata,
    *,
    branch: str,
    source_sha: str,
    environment_name: str,
    update_beta_latest: bool,
) -> str:
    tags = ", ".join(_summary_code(tag) for tag in publication_tags(metadata))
    if metadata.channel is ReleaseChannel.BETA:
        moving_tag = _summary_code(MVP_BETA_TAG)
        if update_beta_latest:
            moving_tag += " (eligible; rechecked after publication and attestations)"
        else:
            moving_tag += " unchanged (not eligible under current policy)"
    elif metadata.channel is ReleaseChannel.GA:
        moving_tag = _summary_code("latest")
    else:
        moving_tag = "None"
    return (
        "## Release proposed for approval\n\n"
        "Proposed publication only; this summary does not mean the image has been published.\n\n"
        "| Release detail | Value |\n"
        "| --- | --- |\n"
        f"| Version | {_summary_code(metadata.raw_version)} |\n"
        f"| Image | {_summary_code(IMAGE_NAME)} |\n"
        f"| Publication tags | {tags} |\n"
        f"| Moving tag | {moving_tag} |\n"
        f"| Channel | {_summary_code(metadata.channel.value)} |\n"
        f"| Approval environment | {_summary_code(environment_name)} |\n"
        f"| Source branch | {_summary_code(branch)} |\n"
        f"| Source commit | {_summary_code(source_sha)} |\n\n"
        "Tag eligibility reflects the current registry inventory. Publication is revalidated after approval.\n"
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments for the promotion revalidation check.

    Args:
        argv: Argument vector excluding the program name.

    Returns:
        Parsed arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="Path to the candidate promotion manifest JSON.")
    parser.add_argument("--source-sha", required=True, help="Exact commit SHA the promotion was requested for.")
    parser.add_argument("--branch", required=True, help="Exact source branch the promotion was requested for.")
    parser.add_argument("--channel", required=True, choices=[channel.value for channel in ReleaseChannel])
    parser.add_argument("--tag", default=None, help="Git tag on the release commit (required for GA).")
    parser.add_argument(
        "--published-versions-file",
        type=Path,
        default=None,
        help="Path to a newline-delimited file of already-published raw versions, if any.",
    )
    parser.add_argument(
        "--summary-file", type=Path, help="Append a validated pre-approval Markdown summary to this file."
    )
    parser.add_argument("--environment-name", help="Approval environment to show; required with --summary-file.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Revalidate a candidate promotion manifest and print its expected tags.

    Args:
        argv: Argument vector excluding the program name. Defaults to
            ``sys.argv[1:]``.

    Returns:
        Process exit code: ``0`` on success, ``1`` on validation failure.
    """
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))

        if manifest["source_sha"] != args.source_sha:
            raise ReleaseMetadataError(
                f"Manifest source_sha {manifest['source_sha']!r} does not match requested {args.source_sha!r}."
            )

        platforms = frozenset(manifest["platform_digests"])
        if platforms != _REQUIRED_PLATFORMS:
            raise ReleaseMetadataError(
                f"Manifest platform set {sorted(platforms)} must be exactly {sorted(_REQUIRED_PLATFORMS)}."
            )

        # Reclassify from the manifest's own raw version rather than trusting
        # its recorded channel, so a tampered manifest cannot claim a channel
        # its version string does not actually support.
        metadata = classify_raw_version(manifest["raw_version"])
        validate_branch_compatibility(metadata, args.branch)
        if metadata.channel.value != args.channel:
            raise ReleaseMetadataError(
                f"Manifest version belongs to channel {metadata.channel.value!r}, not requested {args.channel!r}."
            )
        if manifest["image_name"] != IMAGE_NAME:
            raise ReleaseMetadataError(f"Manifest image_name must be exactly {IMAGE_NAME!r}.")
        if manifest.get("oci_labels", {}).get("org.opencontainers.image.revision") != args.source_sha:
            raise ReleaseMetadataError("Manifest revision label does not match the requested source SHA.")
        if manifest.get("oci_labels", {}).get("org.opencontainers.image.version") != metadata.raw_version:
            raise ReleaseMetadataError("Manifest version label does not match its raw release version.")
        invalid_digests = [
            digest for digest in manifest["platform_digests"].values() if not _DIGEST_PATTERN.fullmatch(digest)
        ]
        if invalid_digests:
            raise ReleaseMetadataError("Manifest contains an invalid platform image digest.")

        if metadata.channel is ReleaseChannel.GA:
            if args.tag is None:
                raise ReleaseMetadataError("GA promotions require --tag <vX.Y.Z>.")
            validate_release_tag(metadata, args.tag)

        published_versions: list[str] = []
        if args.published_versions_file is not None:
            published_versions = [
                line.strip()
                for line in args.published_versions_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        require_not_already_published(metadata.raw_version, published_versions)
        update_beta_latest = should_update_beta_latest(metadata, published_versions)
        if args.summary_file is not None:
            if not args.environment_name:
                raise ReleaseMetadataError("--environment-name is required with --summary-file.")
            summary = _render_summary(
                metadata,
                branch=args.branch,
                source_sha=args.source_sha,
                environment_name=args.environment_name,
                update_beta_latest=update_beta_latest,
            )
            with args.summary_file.open("a", encoding="utf-8") as summary_file:
                summary_file.write(summary)
    except (ReleaseMetadataError, KeyError, json.JSONDecodeError, OSError) as error:
        sys.stderr.write(f"error: {error}\n")
        return 1

    sys.stdout.write(
        json.dumps(
            {
                "raw_version": manifest["raw_version"],
                "channel": metadata.channel.value,
                "image_name": manifest["image_name"],
                "platform_digests": manifest["platform_digests"],
                "expected_tags": list(publication_tags(metadata)),
                "update_beta_latest": update_beta_latest,
            }
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
