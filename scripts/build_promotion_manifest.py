"""Command-line entrypoint building the candidate promotion manifest.

Used by candidate-image CI after the full hardened smoke test and vulnerability
scan pass. Captures exactly what was built — the source commit, the raw
version/channel, each platform's built image digest, and the OCI labels
attached to those images — as a JSON file. The promotion workflow revalidates
this manifest and republishes the same, already-scanned digests; it never
rebuilds.

Usage::

    uv run python -m scripts.build_promotion_manifest \\
        --branch preview/new-cert-flow \\
        --source-sha "$GITHUB_SHA" \\
        --platform-digest linux/amd64=sha256:... \\
        --platform-digest linux/arm64=sha256:... \\
        --oci-label org.opencontainers.image.revision="$GITHUB_SHA" \\
        --output promotion-manifest.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.release_metadata import (
    ReleaseMetadataError,
    build_promotion_manifest,
    classify_raw_version,
    read_pyproject_raw_version,
    validate_branch_compatibility,
)


def _parse_key_value(raw: str, *, flag: str) -> tuple[str, str]:
    """Split a ``key=value`` command-line argument.

    Args:
        raw: The raw ``key=value`` string supplied on the command line.
        flag: The originating flag name, used only for the error message.

    Returns:
        The ``(key, value)`` pair.

    Raises:
        ReleaseMetadataError: If ``raw`` does not contain a literal ``=``.
    """
    key, separator, value = raw.partition("=")
    if not separator:
        raise ReleaseMetadataError(f"{flag} value {raw!r} must be in key=value form.")
    return key, value


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments for the promotion-manifest builder.

    Args:
        argv: Argument vector excluding the program name.

    Returns:
        Parsed arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", required=True, help="Exact source branch name.")
    parser.add_argument("--source-sha", required=True, help="Exact Git commit SHA the candidate was built from.")
    parser.add_argument(
        "--platform-digest",
        action="append",
        default=[],
        metavar="PLATFORM=DIGEST",
        help="Repeatable. e.g. linux/amd64=sha256:...",
    )
    parser.add_argument(
        "--oci-label",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Repeatable. OCI label attached to the candidate images.",
    )
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path("pyproject.toml"),
        help="Path to pyproject.toml (default: ./pyproject.toml).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write the promotion manifest JSON file.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Build and write the candidate promotion manifest.

    Args:
        argv: Argument vector excluding the program name. Defaults to
            ``sys.argv[1:]``.

    Returns:
        Process exit code: ``0`` on success, ``1`` on validation failure.
    """
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    try:
        raw_version = read_pyproject_raw_version(args.pyproject)
        metadata = classify_raw_version(raw_version)
        validate_branch_compatibility(metadata, args.branch)

        platform_digests = dict(_parse_key_value(entry, flag="--platform-digest") for entry in args.platform_digest)
        oci_labels = dict(_parse_key_value(entry, flag="--oci-label") for entry in args.oci_label)

        manifest = build_promotion_manifest(
            metadata=metadata,
            source_sha=args.source_sha,
            platform_digests=platform_digests,
            oci_labels=oci_labels,
        )
    except ReleaseMetadataError as error:
        sys.stderr.write(f"error: {error}\n")
        return 1

    args.output.write_text(json.dumps(manifest.to_json_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    sys.stdout.write(f"Wrote promotion manifest to {args.output}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
