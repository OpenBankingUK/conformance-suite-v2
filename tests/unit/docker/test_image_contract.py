"""Regression coverage for main's assessed distroless Docker runtime."""

from pathlib import Path

import pytest

from docker.patch_base_packages import PATCHED_VERSIONS
from scripts.validate_docker_base import get_runtime_base, validate_runtime_base

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


def test_image_applies_pinned_package_overlay_before_returning_to_non_root() -> None:
    """Keep the patched files and APK inventory aligned in the final runtime."""
    root = Path(__file__).resolve().parents[3]
    dockerfile = (root / "Dockerfile").read_text()
    runtime_base = get_runtime_base(dockerfile)
    assert f"FROM {runtime_base} AS runtime-base\n" in dockerfile
    _, patch_stage = dockerfile.split(" AS package-patch\n", 1)
    patch_stage, _ = patch_stage.split(" AS builder\n", 1)
    assert "COPY --from=runtime-base /lib/apk/db/installed /patch/lib/apk/db/installed" in patch_stage
    assert "COPY docker/patch_base_packages.py /patch_base_packages.py" in patch_stage
    assert "--repository https://dl-cdn.alpinelinux.org/alpine/v3.24/main" in patch_stage
    assert "--allow-untrusted" not in patch_stage
    for package, version in PATCHED_VERSIONS.items():
        assert f"'{package}={version}'" in patch_stage
    assert "python3 /patch_base_packages.py" in patch_stage
    assert "cp -a /usr/lib/libexpat.so.1* /patch/usr/lib/" in patch_stage
    assert "cp -a /usr/lib/libz.so.1* /patch/usr/lib/" in patch_stage
    assert "cp -a /usr/bin/xmlwf /patch/usr/bin/" in patch_stage

    _, runtime = dockerfile.split(f"FROM {runtime_base} AS runtime\n", 1)
    cleanup, overlay = runtime.split("COPY --from=package-patch /patch/ /\n", 1)
    assert "USER 0:0\n" in cleanup
    assert "('libexpat.so.1*', 'libz.so.1*')" in cleanup
    assert "Path('/usr/bin/xmlwf').unlink(missing_ok=True)" in cleanup
    assert overlay.startswith("USER 65532:65532\n")
    assert "USER 0:0" not in overlay
