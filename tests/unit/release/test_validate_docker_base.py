"""Unit tests for :mod:`scripts.validate_docker_base`."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.validate_docker_base import (
    EXPECTED_RUNTIME_BASE,
    DockerBaseError,
    get_runtime_base,
    main,
    validate_runtime_base,
)

pytestmark = pytest.mark.unit


def test_assessed_digest_matches_snyk_policy() -> None:
    """The runtime guard and both Snyk policy justifications stay bound to one digest."""
    policy_path = Path(__file__).resolve().parents[3] / ".snyk"
    policy = " ".join(policy_path.read_text(encoding="utf-8").split())

    assert policy.count(EXPECTED_RUNTIME_BASE) == 2


def test_accepts_exact_pinned_runtime_base() -> None:
    """The exact digest assessed by the Snyk policy and signed VEX is accepted."""
    dockerfile = f"FROM python:3.14-alpine AS builder\nFROM {EXPECTED_RUNTIME_BASE} AS runtime\n"
    validate_runtime_base(dockerfile)
    assert get_runtime_base(dockerfile) == EXPECTED_RUNTIME_BASE


@pytest.mark.parametrize(
    "runtime_base",
    [
        "dhi.io/python:3.14-debian13",
        "dhi.io/python:3.14-debian13@sha256:" + "0" * 64,
        "python:3.14-alpine",
    ],
)
def test_rejects_unassessed_runtime_base(runtime_base: str) -> None:
    """Tag-only, changed-digest, and unrelated runtime bases fail closed."""
    with pytest.raises(DockerBaseError, match="Runtime base must be exactly"):
        validate_runtime_base(f"FROM {runtime_base} AS runtime\n")


def test_rejects_missing_or_ambiguous_runtime_stage() -> None:
    """A Dockerfile without exactly one runtime stage cannot pass the guard."""
    with pytest.raises(DockerBaseError, match="found 0"):
        validate_runtime_base("FROM python:3.14-alpine AS builder\n")
    with pytest.raises(DockerBaseError, match="found 2"):
        validate_runtime_base(f"FROM {EXPECTED_RUNTIME_BASE} AS runtime\nFROM {EXPECTED_RUNTIME_BASE} AS runtime\n")


def test_rejects_unassessed_final_stage_after_runtime() -> None:
    """A later stage cannot replace the assessed runtime as the built image."""
    dockerfile = f"FROM {EXPECTED_RUNTIME_BASE} AS runtime\nFROM ubuntu:latest AS final\n"

    with pytest.raises(DockerBaseError, match="must be the final"):
        validate_runtime_base(dockerfile)


def test_cli_reads_dockerfile_from_stdin(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Trusted promotion can validate the source commit's Dockerfile via git-show stdin."""
    from io import StringIO

    monkeypatch.setattr("sys.stdin", StringIO(f"FROM {EXPECTED_RUNTIME_BASE} AS runtime\n"))

    assert main(["--stdin"]) == 0
    assert EXPECTED_RUNTIME_BASE in capsys.readouterr().out


def test_cli_prints_runtime_base_with_platform_qualified_from(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The validated image reference supports Docker FROM options and whitespace."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        f"FROM --platform=$BUILDPLATFORM python:3.14-alpine AS builder\n"
        f"  FROM --platform=linux/amd64 {EXPECTED_RUNTIME_BASE} AS runtime\n"
    )

    assert main(["--dockerfile", str(dockerfile), "--print-runtime-base"]) == 0
    assert capsys.readouterr().out == f"{EXPECTED_RUNTIME_BASE}\n"
