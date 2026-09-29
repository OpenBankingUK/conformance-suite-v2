"""Validate the pinned runtime base image used by candidate containers.

Docker's signed OpenVEX evidence is consumed by the vulnerability gate
(``scripts/vulnerability_gate.py``) only for the exact Docker Hardened Image
digest below. Candidate CI and trusted promotion both use this check so a
different runtime base cannot inherit that assessment. Updating the digest
(for example from a Dependabot pull request) requires updating this constant
in the same reviewed change.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

EXPECTED_RUNTIME_BASE = (
    "dhi.io/python:3.14-debian13@sha256:e1a5bd571d9585d7eb80c8278b54b69a0e0bf5a9bb2b1424b9e4576374df6659"
)
_FROM_PATTERN = re.compile(r"^\s*FROM\s+(.+?)\s*$", re.IGNORECASE)


class DockerBaseError(ValueError):
    """Raised when a Dockerfile does not use the assessed runtime base."""


def get_runtime_base(dockerfile: str) -> str:
    """Return the validated base reference of the final runtime stage.

    Args:
        dockerfile: Complete Dockerfile contents.

    Returns:
        The assessed runtime base image reference.

    Raises:
        DockerBaseError: If the runtime stage is missing, ambiguous, or uses
            a different image reference.
    """
    runtime_bases: list[tuple[str, int]] = []
    from_count = 0
    for line in dockerfile.splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = _FROM_PATTERN.match(line)
        if match is None:
            continue
        from_count += 1

        tokens = match.group(1).split()
        if tokens and tokens[0].startswith("--platform="):
            tokens.pop(0)
        has_runtime_alias = any(
            tokens[index].casefold() == "as" and tokens[index + 1].casefold() == "runtime"
            for index in range(len(tokens) - 1)
        )
        if len(tokens) >= 3 and has_runtime_alias:
            runtime_bases.append((tokens[0], from_count))

    if len(runtime_bases) != 1:
        raise DockerBaseError(f"Dockerfile must define exactly one runtime stage; found {len(runtime_bases)}.")
    runtime_base, runtime_stage_number = runtime_bases[0]
    if runtime_base != EXPECTED_RUNTIME_BASE:
        raise DockerBaseError(f"Runtime base must be exactly {EXPECTED_RUNTIME_BASE!r}; found {runtime_base!r}.")
    if runtime_stage_number != from_count:
        raise DockerBaseError("The assessed runtime stage must be the final Dockerfile stage.")
    return runtime_base


def validate_runtime_base(dockerfile: str) -> None:
    """Require exactly one final runtime stage using the assessed DHI digest.

    Args:
        dockerfile: Complete Dockerfile contents.

    Raises:
        DockerBaseError: If the runtime stage is missing, ambiguous, or uses
            a different image reference.
    """
    get_runtime_base(dockerfile)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments for the runtime base check.

    Args:
        argv: Argument vector excluding the program name.

    Returns:
        Parsed arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--dockerfile", type=Path, default=Path("Dockerfile"))
    source.add_argument("--stdin", action="store_true", help="Read Dockerfile contents from standard input.")
    parser.add_argument(
        "--print-runtime-base",
        action="store_true",
        help="Print the validated final runtime image reference.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Validate the runtime base and print a useful failure message.

    Args:
        argv: Argument vector excluding the program name. Defaults to
            ``sys.argv[1:]``.

    Returns:
        Process exit code: ``0`` on success, ``1`` on validation failure.
    """
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        contents = sys.stdin.read() if args.stdin else args.dockerfile.read_text(encoding="utf-8")
        runtime_base = get_runtime_base(contents)
    except (DockerBaseError, OSError) as error:
        sys.stderr.write(f"error: {error}\n")
        return 1

    if args.print_runtime_base:
        sys.stdout.write(f"{runtime_base}\n")
    else:
        sys.stdout.write(f"Validated runtime base: {runtime_base}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
