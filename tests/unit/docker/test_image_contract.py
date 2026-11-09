"""Regression coverage for main's assessed distroless Docker runtime."""

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.validate_docker_base import validate_runtime_base

pytestmark = pytest.mark.unit


def test_main_image_uses_assessed_hardened_contract() -> None:
    """Main must use the assessed base and activate existing hardened CI."""
    root = Path(__file__).resolve().parents[3]
    dockerfile = (root / "Dockerfile").read_text()
    validate_runtime_base(dockerfile)
    assert "\nUSER 65532:65532\n" in dockerfile
    assert 'ENTRYPOINT ["python3", "/app/docker/entrypoint.py"]' in dockerfile
    assert 'CMD ["python3", "/app/docker/healthcheck.py"]' in dockerfile
    assert (root / "docker/entrypoint.py").is_file()


def test_dockerignore_excludes_local_only_artifacts() -> None:
    """Local configuration and generated artifacts must not enter the image."""
    root = Path(__file__).resolve().parents[3]
    ignored_paths = set((root / ".dockerignore").read_text().splitlines())
    assert {"local-config/", "out/", "ai/"} <= ignored_paths


def test_image_uses_debian_binary_wheels_and_removes_unused_pip() -> None:
    """Keep glibc stages aligned without the obsolete Alpine package overlay."""
    root = Path(__file__).resolve().parents[3]
    dockerfile = (root / "Dockerfile").read_text()
    stages = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]
    assert stages == [
        "FROM dhi.io/python:3.14-debian13-dev@sha256:"
        "290766784d2488991d638bc8c52a53a3c5012513075da9b7bc67705510d7cb2d AS builder",
        "FROM dhi.io/python:3.14-debian13@sha256:"
        "4491196f15b7afb538ea121eed173cdd136297f616b15b34f66c25610bf80241 AS runtime",
    ]
    assert "uv sync --frozen --no-dev --no-install-project --no-build\n" in dockerfile
    assert "apk" not in dockerfile
    assert "package-patch" not in dockerfile
    assert not (root / "docker/patch_base_packages.py").exists()
    runtime = dockerfile.split(" AS runtime\n", 1)[1]
    cleanup, non_root = runtime.split("USER 65532:65532\n", 1)
    assert "USER 0:0\nRUN [" in cleanup
    assert "USER 0:0" not in non_root


@pytest.mark.parametrize(
    "layout",
    ["expected", "missing-pip", "missing-metadata", "missing-bin", "missing-bin3", "duplicate-metadata", "unexpected"],
)
def test_system_pip_removal_fails_closed_on_layout_drift(tmp_path: Path, layout: str) -> None:
    """Execute the actual Docker build cleanup against a disposable Debian layout."""
    root = Path(__file__).resolve().parents[3]
    dockerfile = (root / "Dockerfile").read_text()
    run = next(line.removeprefix("RUN ") for line in dockerfile.splitlines() if line.startswith("RUN ["))
    command = json.loads(run)
    packages = tmp_path / "usr/lib/python3/dist-packages"
    packages.mkdir(parents=True)
    (packages / "unrelated").mkdir()
    if layout != "missing-pip":
        (packages / "pip/_vendor").mkdir(parents=True)
        (packages / "pip/_vendor/package.py").write_text("vendored")
    if layout != "missing-metadata":
        (packages / "pip-26.3.dist-info").mkdir()
    if layout == "duplicate-metadata":
        (packages / "pip-26.2.dist-info").mkdir()
    if layout == "unexpected":
        (packages / "pip-extra").mkdir()
    binaries = tmp_path / "usr/bin"
    binaries.mkdir(parents=True)
    for binary, missing in (("pip", "missing-bin"), ("pip3", "missing-bin3")):
        if layout != missing:
            (binaries / binary).write_text("unused pip")
    result = subprocess.run(  # noqa: S603 - repository-owned Dockerfile code, with paths redirected to tmp_path
        [sys.executable, "-c", command[2].replace("/usr/", f"{tmp_path}/usr/")],
        capture_output=True,
        text=True,
        check=False,
    )
    if layout == "expected":
        assert result.returncode == 0, result.stderr
        assert not list(packages.glob("pip*"))
        assert not list(binaries.iterdir())
        assert (packages / "unrelated").is_dir()
    else:
        assert result.returncode != 0
        assert result.stderr


def test_debian_smoke_check_is_valid_exec_form_python() -> None:
    """The shell must preserve the Python checks passed to the distroless image."""
    root = Path(__file__).resolve().parents[3]
    smoke = (root / "scripts/docker_smoke_test.sh").read_text()
    check = smoke.split(
        'echo "==> Checking Debian/glibc, binary wheels, system pip removal, and OpenSSL inventory"\n', 1
    )[1].split('echo "==> Creating persistent volume', 1)[0]
    tokens = shlex.split(check)
    code = tokens[tokens.index("-c") + 1]
    compile(code, "debian-smoke-check", "exec")
    assert "CS_GNU_LIBC_VERSION" in code
    assert "manylinux" in code
    assert "/usr/lib/python3/dist-packages" in code
    assert "/var/lib/dpkg/status" in code
