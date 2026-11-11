"""Validate candidate DHI references and historical runtime base references."""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from pathlib import Path

EXPECTED_RUNTIME_BASE = (
    "dhi.io/python:3.14-alpine3.24@sha256:4361d30a5f505dd5509622eff5c7bef79afa798b4f3eedd3f5cb1abc92be9168"
)
APPROVED_RUNTIME_BASES = frozenset(
    {
        EXPECTED_RUNTIME_BASE,
        "dhi.io/python:3.14-alpine3.24@sha256:00f9aeadee003dec0cafd5ebd3b36e668ac2a4e120e0c059967ad249e1130179",
        "dhi.io/python:3.14-alpine3.24@sha256:3d7fbfaca07a97d63e0d9efa00a4abc412f0044710efcae292ae7fa211fe1359",
        "dhi.io/python:3.14-debian13@sha256:4491196f15b7afb538ea121eed173cdd136297f616b15b34f66c25610bf80241",
    }
)
_FROM_PATTERN = re.compile(r"^\s*FROM\s+(.+?)\s*$", re.IGNORECASE)
_CANDIDATE_BASE_PATTERNS = {
    "builder": re.compile(r"^dhi\.io/python:3\.14-debian13-dev@sha256:[0-9a-f]{64}$"),
    "runtime": re.compile(r"^dhi\.io/python:3\.14-debian13@sha256:[0-9a-f]{64}$"),
}


@dataclasses.dataclass(frozen=True)
class CandidateDhiBases:
    """The exact builder and runtime references in a current candidate Dockerfile."""

    builder: str
    runtime: str


class DockerBaseError(ValueError):
    """Raised when a Dockerfile does not use the assessed runtime base."""


def get_candidate_dhi_bases(dockerfile: str) -> CandidateDhiBases:
    """Parse the fixed DHI Python 3.14 / Debian 13 builder and runtime pair.

    Unlike :func:`get_runtime_base`, this validates the image family and digest
    syntax rather than an advance-approved digest. The trusted assessment must
    independently verify signed provenance for both returned references.
    """
    stages: dict[str, tuple[str, int]] = {}
    from_count = 0
    for line in dockerfile.splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = _FROM_PATTERN.match(line)
        if match is None:
            continue
        from_count += 1
        tokens = match.group(1).split()
        if tokens and tokens[0].casefold().startswith("--platform="):
            tokens.pop(0)
        aliases = [tokens[index + 1].casefold() for index in range(len(tokens) - 1) if tokens[index].casefold() == "as"]
        if len(tokens) < 3 or len(aliases) != 1 or aliases[0] not in _CANDIDATE_BASE_PATTERNS:
            continue
        alias = aliases[0]
        if alias in stages:
            raise DockerBaseError(f"Dockerfile defines more than one {alias} stage.")
        reference = tokens[0]
        if not _CANDIDATE_BASE_PATTERNS[alias].fullmatch(reference):
            raise DockerBaseError(
                f"The {alias} stage must use the pinned DHI Python 3.14 Debian 13 image family "
                f"with a SHA-256 digest; found {reference!r}."
            )
        stages[alias] = (reference, from_count)

    if set(stages) != set(_CANDIDATE_BASE_PATTERNS):
        raise DockerBaseError("Dockerfile must define exactly one DHI builder and one DHI runtime stage.")
    builder, builder_number = stages["builder"]
    runtime, runtime_number = stages["runtime"]
    if builder_number >= runtime_number or runtime_number != from_count:
        raise DockerBaseError("The DHI builder must precede the final DHI runtime stage.")
    return CandidateDhiBases(builder=builder, runtime=runtime)


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
    if runtime_base not in APPROVED_RUNTIME_BASES:
        raise DockerBaseError(
            f"Runtime base must be an approved exact reference in {sorted(APPROVED_RUNTIME_BASES)!r}; "
            f"found {runtime_base!r}."
        )
    if runtime_stage_number != from_count:
        raise DockerBaseError("The assessed runtime stage must be the final Dockerfile stage.")
    return runtime_base


def validate_runtime_base(dockerfile: str) -> None:
    """Require exactly one final runtime stage using an approved DHI digest.

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
        "--candidate-dhi",
        action="store_true",
        help="Validate both current candidate DHI stages by image family and digest syntax.",
    )
    parser.add_argument(
        "--print-candidate-bases",
        action="store_true",
        help="Print validated candidate builder/runtime references as JSON.",
    )
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
        if args.candidate_dhi:
            bases = get_candidate_dhi_bases(contents)
            runtime_base = bases.runtime
        else:
            bases = None
            runtime_base = get_runtime_base(contents)
        if args.print_candidate_bases and bases is None:
            raise DockerBaseError("--print-candidate-bases requires --candidate-dhi.")
    except (DockerBaseError, OSError) as error:
        sys.stderr.write(f"error: {error}\n")
        return 1

    if args.print_candidate_bases and bases is not None:
        sys.stdout.write(json.dumps(dataclasses.asdict(bases), sort_keys=True) + "\n")
    elif args.print_runtime_base:
        sys.stdout.write(f"{runtime_base}\n")
    else:
        sys.stdout.write(f"Validated runtime base: {runtime_base}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
