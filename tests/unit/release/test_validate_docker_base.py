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


def test_accepts_exact_pinned_runtime_base() -> None:
    """The exact digest assessed by Docker's signed VEX is accepted."""
    dockerfile = f"FROM python:3.14-alpine AS builder\nFROM {EXPECTED_RUNTIME_BASE} AS runtime\n"
    validate_runtime_base(dockerfile)
    assert get_runtime_base(dockerfile) == EXPECTED_RUNTIME_BASE


def test_repository_dockerfile_uses_assessed_runtime_base() -> None:
    """The shipped Dockerfile must match the multi-platform base assessed by CI."""
    dockerfile = Path(__file__).resolve().parents[3] / "Dockerfile"

    assert get_runtime_base(dockerfile.read_text(encoding="utf-8")) == EXPECTED_RUNTIME_BASE


@pytest.mark.parametrize(
    "runtime_base",
    [
        "dhi.io/python:3.14-alpine3.24",
        "dhi.io/python:3.14-alpine3.24@sha256:" + "0" * 64,
        "dhi.io/python:3.14-alpine3.24@sha256:4361d30a5f505dd5509622eff5c7bef79afa798b4f3eedd3f5cb1abc92be9168",
        "dhi.io/python:3.14-alpine3.24@sha256:9564e9ed8450fba6a3b6ed808f300d614f9bd7220749c7285789b05bd8ce4d6c",
        "python:3.14-alpine",
    ],
)
def test_rejects_unassessed_runtime_base(runtime_base: str) -> None:
    """Unpinned, former, platform-only, and unrelated runtime bases fail closed."""
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
