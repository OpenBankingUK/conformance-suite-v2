"""GitHub Release notes and CHANGELOG checks for published images.

A GitHub Release is the record of a successful, Environment-approved image
publication, never its trigger. Release immutability is enabled on the
repository, so the finalizer must create each Release complete in one call.
The body keeps three sections separate:

1. the version's curated ``CHANGELOG.md`` section (Keep a Changelog),
2. the published Docker image reference and its manifest digest, and
3. GitHub's generated pull-request notes since the previous same-channel tag.

GA releases must carry a ``CHANGELOG.md`` section; betas only warn. The same
rule runs in CI on pull requests that change ``[project].version`` (that is,
prepare a release), so a missing GA entry is caught before merge rather than
after publication.

Usage::

    uv run python -m scripts.release_notes check-changelog --pyproject pyproject.toml --changelog CHANGELOG.md
    uv run python -m scripts.release_notes previous-tag --tag v2.0.0-beta.7 --tags-file tags.txt
    uv run python -m scripts.release_notes render --version 2.0.0-beta.7 --digest sha256:... \\
        --changelog CHANGELOG.md --generated-notes generated.md
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from pathlib import Path

from scripts.release_metadata import (
    IMAGE_NAME,
    MVP_BETA_TAG,
    ReleaseChannel,
    ReleaseMetadata,
    ReleaseMetadataError,
    classify_raw_version,
    read_pyproject_raw_version,
)

_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_VERSION_HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\]")
_LINK_REFERENCE = re.compile(r"^\[[^\]]+\]:\s")
_TRAILING_RULE = re.compile(r"(?:^|\n)-{3,}\s*$")
_PUBLIC_IMAGE = IMAGE_NAME.removeprefix("docker.io/")


def extract_changelog_section(changelog: str, version: str) -> str | None:
    """Return the body of a version's Keep a Changelog section.

    Args:
        changelog: Full ``CHANGELOG.md`` text.
        version: Exact raw version, matched against ``## [<version>]``.

    Returns:
        The stripped section body, or ``None`` if the section is missing or
        has no content.
    """
    collected: list[str] | None = None
    for line in changelog.splitlines():
        if collected is None:
            heading = _VERSION_HEADING.match(line)
            if heading and heading["version"] == version:
                collected = []
            continue
        if line.startswith("## ") or _LINK_REFERENCE.match(line):
            break
        collected.append(line)
    if collected is None:
        return None
    body = _TRAILING_RULE.sub("", "\n".join(collected).strip()).strip()
    return body or None


def _classify_tag(tag: str) -> ReleaseMetadata | None:
    """Classify a ``v``-prefixed tag, returning ``None`` for non-release tags."""
    if not tag.startswith("v"):
        return None
    try:
        return classify_raw_version(tag[1:])
    except ReleaseMetadataError:
        return None


def previous_release_tag(tag: str, existing_tags: Iterable[str]) -> str | None:
    """Return the highest earlier release tag on the same channel.

    Betas compare against earlier ``vX.Y.Z-beta.N`` tags and GA against earlier
    ``vX.Y.Z`` tags. Legacy or unrelated tag formats are ignored.

    Args:
        tag: The ``v``-prefixed tag being released.
        existing_tags: All Git tag names in the repository.

    Returns:
        The previous tag name, or ``None`` if there is none.

    Raises:
        ReleaseMetadataError: If ``tag`` is not a ``v``-prefixed beta or GA tag.
    """
    current = _classify_tag(tag)
    if current is None or current.channel is ReleaseChannel.PREVIEW:
        raise ReleaseMetadataError(f"Release tag {tag!r} must be 'vX.Y.Z' or 'vX.Y.Z-beta.N'.")
    best_tag: str | None = None
    best: ReleaseMetadata | None = None
    for raw_candidate in existing_tags:
        candidate = raw_candidate.strip()
        metadata = _classify_tag(candidate)
        if metadata is None or metadata.channel is not current.channel:
            continue
        if metadata.comparison_version >= current.comparison_version:
            continue
        if best is None or metadata.comparison_version > best.comparison_version:
            best_tag, best = candidate, metadata
    return best_tag


def check_changelog(raw_version: str, changelog: str) -> str | None:
    """Apply the CHANGELOG rule for a version before merge or release.

    Args:
        raw_version: Exact ``[project].version`` string.
        changelog: Full ``CHANGELOG.md`` text.

    Returns:
        A warning message for a beta without a section, otherwise ``None``.

    Raises:
        ReleaseMetadataError: If a GA version has no ``CHANGELOG.md`` section.
    """
    metadata = classify_raw_version(raw_version)
    if metadata.channel is ReleaseChannel.PREVIEW or extract_changelog_section(changelog, raw_version):
        return None
    message = f"CHANGELOG.md has no '## [{raw_version}]' section."
    if metadata.channel is ReleaseChannel.GA:
        raise ReleaseMetadataError(f"{message} GA releases require a curated changelog entry.")
    return f"{message} The beta release notes will contain generated PR notes only."


def render_release_notes(
    *,
    raw_version: str,
    digest: str,
    changelog: str,
    generated_notes: str,
    beta_latest_updated: bool = False,
) -> str:
    """Render the complete GitHub Release body for a published image.

    Args:
        raw_version: Exact published version (also the Docker image tag).
        digest: Published multi-architecture manifest digest.
        changelog: Full ``CHANGELOG.md`` text at the released commit.
        generated_notes: Body returned by GitHub's generate-notes API.
        beta_latest_updated: Whether the MVP beta pointer now targets this image.

    Returns:
        Markdown release body.

    Raises:
        ReleaseMetadataError: If the version is not beta/GA, the digest is
            malformed, or a GA version has no changelog section.
    """
    metadata = classify_raw_version(raw_version)
    if metadata.channel is ReleaseChannel.PREVIEW:
        raise ReleaseMetadataError("Preview images do not get GitHub Releases.")
    if not _DIGEST_PATTERN.fullmatch(digest):
        raise ReleaseMetadataError("Published manifest digest must be a lowercase SHA-256 digest.")
    check_changelog(raw_version, changelog)
    summary = extract_changelog_section(changelog, raw_version) or (
        "_No curated CHANGELOG.md entry was recorded for this beta; see the pull requests below._"
    )

    image_lines = [
        "```bash",
        f"docker pull {_PUBLIC_IMAGE}:{raw_version}",
        "```",
        "",
        f"Digest-pinned reference: `{_PUBLIC_IMAGE}@{digest}`",
    ]
    if metadata.channel is ReleaseChannel.GA:
        image_lines.append("\nAlso published as `latest`.")
    elif beta_latest_updated:
        image_lines.append(f"\n`{MVP_BETA_TAG}` now points to this image.")
    if metadata.channel is ReleaseChannel.BETA:
        image_lines.append("\n> Beta images are pre-release builds and are not valid for certification.")

    pull_requests = re.sub(r"^## ", "### ", generated_notes.strip(), flags=re.MULTILINE) or "_No pull requests._"
    sections = [
        "## Summary",
        summary,
        "## Docker image",
        "\n".join(image_lines),
        "## Pull requests",
        pull_requests,
    ]
    return "\n\n".join(sections) + "\n"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments for the release-notes subcommands."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check-changelog", help="Require (GA) or warn about (beta) a changelog section.")
    check.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    check.add_argument("--version", default=None, help="Raw version to check instead of reading --pyproject.")
    check.add_argument(
        "--base-pyproject",
        type=Path,
        default=None,
        help="Pull request base pyproject.toml; the check is skipped when the version is unchanged.",
    )
    check.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))

    previous = commands.add_parser("previous-tag", help="Print the previous same-channel release tag, if any.")
    previous.add_argument("--tag", required=True)
    previous.add_argument("--tags-file", type=Path, required=True, help="Newline-delimited Git tag names.")

    render = commands.add_parser("render", help="Render the GitHub Release body.")
    render.add_argument("--version", required=True)
    render.add_argument("--digest", required=True)
    render.add_argument("--changelog", type=Path, required=True)
    render.add_argument("--generated-notes", type=Path, required=True)
    render.add_argument("--beta-latest-updated", choices=("true", "false"), default="false")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run a release-notes subcommand.

    Args:
        argv: Argument vector excluding the program name.

    Returns:
        Process exit code: ``0`` on success, ``1`` on validation failure.
    """
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "check-changelog":
            raw_version = args.version or read_pyproject_raw_version(args.pyproject)
            if args.base_pyproject is not None and read_pyproject_raw_version(args.base_pyproject) == raw_version:
                sys.stdout.write(f"[project].version is unchanged ({raw_version}); no release is prepared.\n")
                return 0
            warning = check_changelog(raw_version, args.changelog.read_text(encoding="utf-8"))
            if warning:
                sys.stdout.write(f"::warning::{warning}\n")
            else:
                sys.stdout.write(f"CHANGELOG.md check passed for {raw_version}.\n")
        elif args.command == "previous-tag":
            tags = args.tags_file.read_text(encoding="utf-8").splitlines()
            sys.stdout.write((previous_release_tag(args.tag, tags) or "") + "\n")
        else:
            sys.stdout.write(
                render_release_notes(
                    raw_version=args.version,
                    digest=args.digest,
                    changelog=args.changelog.read_text(encoding="utf-8"),
                    generated_notes=args.generated_notes.read_text(encoding="utf-8"),
                    beta_latest_updated=args.beta_latest_updated == "true",
                )
            )
    except (OSError, ReleaseMetadataError) as error:
        sys.stderr.write(f"error: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
