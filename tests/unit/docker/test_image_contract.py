"""Regression coverage for the release's assessed distroless Docker runtime."""

import shlex
from pathlib import Path

import pytest

from scripts.validate_docker_base import get_candidate_dhi_bases

pytestmark = pytest.mark.unit


def test_release_image_uses_assessed_hardened_contract() -> None:
    """Preserve static assets and pinned DHI families; trusted CI assesses provenance."""
    root = Path(__file__).resolve().parents[3]
    dockerfile = (root / "Dockerfile").read_text()
    get_candidate_dhi_bases(dockerfile)
    assert "\nUSER 65532:65532\n" in dockerfile
    assert 'ENTRYPOINT ["python3", "/app/docker/entrypoint.py"]' in dockerfile
    assert 'CMD ["python3", "/app/docker/healthcheck.py"]' in dockerfile
    assert (root / "docker/entrypoint.py").is_file()
    assert "RUN .venv/bin/python manage.py collectstatic --noinput\n" in dockerfile


def test_dockerignore_excludes_local_only_artifacts() -> None:
    """Local configuration and generated artifacts must not enter the image."""
    root = Path(__file__).resolve().parents[3]
    ignored_paths = set((root / ".dockerignore").read_text().splitlines())
    assert {"local-config/", "out/", "ai/"} <= ignored_paths


def test_image_uses_alpine_binary_wheels_without_modifying_runtime_packages() -> None:
    """Use the upstream pip-free runtime without a privileged package overlay."""
    root = Path(__file__).resolve().parents[3]
    dockerfile = (root / "Dockerfile").read_text()
    bases = get_candidate_dhi_bases(dockerfile)
    stages = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]
    assert stages == [
        f"FROM {bases.builder} AS builder",
        f"FROM {bases.runtime} AS runtime",
    ]
    assert "uv sync --frozen --no-dev --no-install-project --no-build\n" in dockerfile
    assert "package-patch" not in dockerfile
    assert not (root / "docker/patch_base_packages.py").exists()
    runtime = dockerfile.split(" AS runtime\n", 1)[1]
    assert ":3.14-alpine@" in bases.runtime
    assert "USER 0:0" not in runtime


def test_runtime_smoke_check_is_valid_exec_form_python() -> None:
    """The shell must preserve the Python checks passed to the distroless image."""
    root = Path(__file__).resolve().parents[3]
    smoke = (root / "scripts/docker_smoke_test.sh").read_text()
    start = 'echo "==> Checking runtime libc, binary wheels, pip absence, and OpenSSL inventory"\n'
    check = smoke.split(start, 1)[1].split('echo "==> Creating persistent volume', 1)[0]
    tokens = shlex.split(check)
    code = tokens[tokens.index("-c") + 1]
    compile(code, "runtime-smoke-check", "exec")
    assert "CS_GNU_LIBC_VERSION" in code
    assert "manylinux" in code
    assert "/usr/lib/python3/dist-packages" in code
    assert "/var/lib/dpkg/status" in code
    assert "musllinux" in code
    assert "/lib/apk/db/installed" in code
