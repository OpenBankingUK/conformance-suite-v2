"""Internal CLI for OBL certification report validation."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from conformance.certification_validator import (
    CertificationValidationError,
    render_confluence_summary,
    validate_certification_report,
)

logger = logging.getLogger(__name__)


def run(argv: Sequence[str] | None = None) -> int:
    """Validate a submitted certification report from the command line.

    Args:
        argv: Optional argument list to parse instead of ``sys.argv``.

    Returns:
        Process-style exit code: 0 when the report is valid, 1 when the
        report fails certification validation, 2 for invalid inputs, and 3
        when the Confluence-ready summary cannot be written.
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else 2

    try:
        result = validate_certification_report(
            args.report,
            manifest_path=args.manifest,
            approved_releases_path=args.approved_releases,
        )
    except CertificationValidationError as error:
        logger.error("Certification validation input error: %s", error)
        return 2

    summary = render_confluence_summary(result)
    if args.summary_output is None:
        sys.stdout.write(f"{summary}\n")
    else:
        try:
            _write_summary(args.summary_output, summary)
        except OSError as error:
            logger.error("Unable to write certification summary to %s: %s", args.summary_output, error)
            return 3

    return 0 if result.valid else 1


def _build_parser() -> argparse.ArgumentParser:
    """Build the certification validation argument parser.

    Returns:
        Parser for the internal OBL certification validation command.
    """
    parser = argparse.ArgumentParser(description="Validate a submitted certification report")
    parser.add_argument("report", type=Path, help="Path to the submitted report JSON file")
    parser.add_argument("--manifest", required=True, type=Path, help="Manifest JSON file used for the original run")
    parser.add_argument(
        "--approved-releases",
        required=True,
        type=Path,
        help="Approved-release policy JSON file supplied by OBL",
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        help="Optional path to write the Confluence-ready summary instead of stdout",
    )
    return parser


def _write_summary(path: Path, summary: str) -> None:
    """Write a Confluence-ready validation summary to disk.

    Args:
        path: Destination path for the summary text file.
        summary: Rendered Confluence-ready summary.

    Raises:
        OSError: If the destination directory or summary file cannot be
            written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{summary}\n", encoding="utf-8")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(run())
