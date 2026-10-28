"""Unit tests for :mod:`scripts.validate_docker_base`."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.validate_docker_base import (
    APPROVED_RUNTIME_BASES,
    EXPECTED_RUNTIME_BASE,
    DockerBaseError,
    get_runtime_base,
    main,
    validate_runtime_base,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "runtime_base",
    [
        EXPECTED_RUNTIME_BASE,
        "dhi.io/python:3.14-alpine3.24@sha256:00f9aeadee003dec0cafd5ebd3b36e668ac2a4e120e0c059967ad249e1130179",
    ],
)
def test_accepts_exact_pinned_runtime_base(runtime_base: str) -> None:
    """Both reviewed index references are accepted and returned unchanged."""
    dockerfile = f"FROM python:3.14-alpine AS builder\nFROM {runtime_base} AS runtime\n"
    validate_runtime_base(dockerfile)
    assert get_runtime_base(dockerfile) == runtime_base


def test_approval_set_is_limited_to_reviewed_indexes() -> None:
    """Approval is immutable and limited to the baseline and refreshed Alpine indexes."""
    assert APPROVED_RUNTIME_BASES == frozenset(
        {
            EXPECTED_RUNTIME_BASE,
            "dhi.io/python:3.14-alpine3.24@sha256:00f9aeadee003dec0cafd5ebd3b36e668ac2a4e120e0c059967ad249e1130179",
        }
    )


@pytest.mark.parametrize(
    "runtime_base",
    [
        "dhi.io/python:3.14-alpine3.24",
        "dhi.io/python:3.14-alpine3.24@sha256:" + "0" * 64,
        "dhi.io/python:3.14-alpine3.24@sha256:9564e9ed8450fba6a3b6ed808f300d614f9bd7220749c7285789b05bd8ce4d6c",
        "dhi.io/python:3.14-alpine3.24@sha256:ffcb7098a6d37a4f92c34959bcde8e78284c7535a72c5c9d34ee3b014d46c8b4",
        "python:3.14-alpine",
    ],
)
def test_rejects_unassessed_runtime_base(runtime_base: str) -> None:
    """Tag-only, changed, platform-only, and unrelated references fail closed."""
    with pytest.raises(DockerBaseError, match="Runtime base must be an approved exact reference"):
        validate_runtime_base(f"FROM {runtime_base} AS runtime\n")


@pytest.mark.parametrize("runtime_base", sorted(APPROVED_RUNTIME_BASES))
def test_rejects_missing_or_ambiguous_runtime_stage(runtime_base: str) -> None:
    """A Dockerfile without exactly one runtime stage cannot pass the guard."""
    with pytest.raises(DockerBaseError, match="found 0"):
        validate_runtime_base("FROM python:3.14-alpine AS builder\n")
    with pytest.raises(DockerBaseError, match="found 2"):
        validate_runtime_base(f"FROM {runtime_base} AS runtime\nFROM {runtime_base} AS runtime\n")


@pytest.mark.parametrize("runtime_base", sorted(APPROVED_RUNTIME_BASES))
def test_rejects_unassessed_final_stage_after_runtime(runtime_base: str) -> None:
    """A later stage cannot replace the assessed runtime as the built image."""
    dockerfile = f"FROM {runtime_base} AS runtime\nFROM ubuntu:latest AS final\n"

    with pytest.raises(DockerBaseError, match="must be the final"):
        validate_runtime_base(dockerfile)


@pytest.mark.parametrize("runtime_base", sorted(APPROVED_RUNTIME_BASES))
def test_cli_reads_dockerfile_from_stdin(
    runtime_base: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Trusted promotion can validate the source commit's Dockerfile via git-show stdin."""
    from io import StringIO

    monkeypatch.setattr("sys.stdin", StringIO(f"FROM {runtime_base} AS runtime\n"))

    assert main(["--stdin"]) == 0
    assert capsys.readouterr().out == f"Validated runtime base: {runtime_base}\n"


@pytest.mark.parametrize("runtime_base", sorted(APPROVED_RUNTIME_BASES))
def test_cli_prints_runtime_base_with_platform_qualified_from(
    runtime_base: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The validated image reference supports Docker FROM options and whitespace."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        f"FROM --platform=$BUILDPLATFORM python:3.14-alpine AS builder\n"
        f"  FROM --platform=linux/amd64 {runtime_base} AS runtime\n"
    )

    assert main(["--dockerfile", str(dockerfile), "--print-runtime-base"]) == 0
    assert capsys.readouterr().out == f"{runtime_base}\n"
