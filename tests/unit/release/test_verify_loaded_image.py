"""Tests for comparing daemon imports with verified archive identity."""

from __future__ import annotations

import pytest

from scripts.verify_loaded_image import LoadedImageError, verify_loaded_image

pytestmark = pytest.mark.unit

CONFIG: dict[str, object] = {"User": "app", "Labels": {"org.opencontainers.image.revision": "1" * 40}}
EVIDENCE: dict[str, object] = {
    "platform": "linux/amd64",
    "image_config": CONFIG,
    "rootfs_layers": ["sha256:" + "2" * 64],
}
INSPECTED: dict[str, object] = {
    "Os": "linux",
    "Architecture": "amd64",
    "Config": {
        "User": "app",
        "Labels": {"org.opencontainers.image.revision": "1" * 40},
        "Hostname": "",
        "Domainname": "",
        "AttachStdin": False,
        "Env": None,
    },
    "RootFS": {"Layers": ["sha256:" + "2" * 64]},
}


def test_accepts_daemon_materialized_empty_config_defaults() -> None:
    verify_loaded_image(EVIDENCE, INSPECTED)


@pytest.mark.parametrize(
    ("field", "replacement", "diagnostic"),
    [
        ("Config", {"User": "different", "Labels": {"org.opencontainers.image.revision": "1" * 40}}, "User"),
        ("platform", "linux/arm64", "platform"),
        ("layers", ["sha256:" + "3" * 64], "RootFS layers"),
    ],
)
def test_rejects_daemon_identity_mismatches_without_disclosing_config_values(
    field: str, replacement: object, diagnostic: str
) -> None:
    inspected = dict(INSPECTED)
    if field == "Config":
        inspected["Config"] = replacement
    elif field == "platform":
        inspected["Architecture"] = "arm64"
    else:
        inspected["RootFS"] = {"Layers": replacement}

    with pytest.raises(LoadedImageError, match=diagnostic) as error:
        verify_loaded_image(EVIDENCE, inspected)

    assert "different" not in str(error.value)


def test_rejects_non_default_daemon_only_config_fields() -> None:
    inspected = dict(INSPECTED)
    inspected["Config"] = {**CONFIG, "Env": ["SECRET_VALUE"]}

    with pytest.raises(LoadedImageError, match="Env") as error:
        verify_loaded_image(EVIDENCE, inspected)

    assert "SECRET_VALUE" not in str(error.value)
