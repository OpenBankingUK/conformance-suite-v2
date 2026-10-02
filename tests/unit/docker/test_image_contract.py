"""Regression coverage for main's assessed distroless Docker runtime."""

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
