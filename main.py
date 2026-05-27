"""Command-line entry point for the conformance suite."""

import logging
from collections.abc import Sequence

from conformance.cli import run


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface.

    Args:
        argv: Optional argument list to parse instead of `sys.argv`.

    Returns:
        Process-style exit code from the CLI runner.
    """
    return run(argv)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
