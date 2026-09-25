"""Command-line entrypoint validating release metadata for a candidate build.

Used by candidate-image CI to fail fast — before building, smoke-testing, or
scanning any container image — when the raw project version, source branch,
or release tag are incompatible with the agreed publication channels.

Usage::

    uv run python -m scripts.validate_release --branch preview/new-cert-flow
    uv run python -m scripts.validate_release --branch main --tag v2.0.0
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.release_metadata import (
    ReleaseChannel,
    ReleaseMetadataError,
    classify_raw_version,
    read_pyproject_raw_version,
    require_version_increase,
    validate_branch_compatibility,
    validate_release_tag,
)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments for the release metadata check.

    Args:
        argv: Argument vector excluding the program name.

    Returns:
        Parsed arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", required=True, help="Exact source branch name.")
    parser.add_argument("--tag", default=None, help="Git tag on the release commit (required for GA).")
    parser.add_argument(
        "--previous-version",
        default=None,
        help="Raw version string most recently published on this branch/channel, if any.",
    )
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path("pyproject.toml"),
        help="Path to pyproject.toml (default: ./pyproject.toml).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Validate release metadata for a candidate build and print it as JSON.

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

        if metadata.channel is ReleaseChannel.GA:
            if args.tag is None:
                raise ReleaseMetadataError("GA releases require --tag <vX.Y.Z>.")
            validate_release_tag(metadata, args.tag)

        require_version_increase(metadata, args.previous_version)
    except ReleaseMetadataError as error:
        sys.stderr.write(f"error: {error}\n")
        return 1

    sys.stdout.write(
        json.dumps(
            {
                "raw_version": metadata.raw_version,
                "comparison_version": str(metadata.comparison_version),
                "channel": metadata.channel.value,
                "base_release": metadata.base_release,
            }
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
