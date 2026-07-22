"""Unit tests for environment capability metadata and compatibility helpers."""

from __future__ import annotations

import pytest

from conformance.environment_capabilities import (
    list_environment_presets,
)


@pytest.mark.unit
def test_environment_presets_contains_ozone_obie_preprod() -> None:
    """Preset list should expose the known Ozone model-bank environment."""
    presets = list_environment_presets()

    assert any(item.key == "ozone-obie-preprod" for item in presets)
