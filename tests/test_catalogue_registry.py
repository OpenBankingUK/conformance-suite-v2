"""Regression tests for bundled catalogue registration and public cleanup."""

from __future__ import annotations

from pathlib import Path

import pytest

from conformance.catalogue import CatalogueKey
from conformance.catalogue_registry import resolve_catalogue, supported_catalogues


@pytest.mark.unit
def test_supported_catalogues_cover_legacy_fcs_api_families() -> None:
    """Bundled catalogues expose each legacy FCS API family through plan specs."""
    keys = {(catalogue.key.standard, catalogue.key.version, catalogue.key.api) for catalogue in supported_catalogues()}

    assert keys == {
        ("open-banking", "v4.0", "ais"),
        ("open-banking", "v4.0", "pis"),
        ("open-banking", "v4.0", "cbpii"),
        ("open-banking", "v4.0", "vrp"),
        ("open-banking", "v4.0", "cvrp"),
    }


@pytest.mark.unit
def test_resolved_catalogues_keep_legacy_fcs_traceability_scope() -> None:
    """Every bundled legacy-derived catalogue case carries FCS provenance."""
    for catalogue in supported_catalogues():
        resolved = resolve_catalogue(catalogue.key)
        assert resolved is catalogue
        for test_case in resolved.test_cases:
            assert any(scope.startswith(("legacy-", "legacy_")) for scope in test_case.compliance_scope)


@pytest.mark.unit
def test_config_package_contains_no_public_example_payloads() -> None:
    """Participant-facing config examples and manifest examples are not shipped."""
    config_dir = Path(__file__).resolve().parents[1] / "config"
    example_payloads = sorted(
        path.name
        for path in config_dir.iterdir()
        if path.is_file() and path.suffix in {".json", ".yaml", ".yml"} and "example" in path.name
    )

    assert example_payloads == []


@pytest.mark.unit
def test_unsupported_catalogue_error_lists_plan_spec_families() -> None:
    """Unsupported plan-spec keys fail with the supported catalogue families."""
    with pytest.raises(ValueError, match="Supported catalogues:"):
        resolve_catalogue(CatalogueKey(standard="open-banking", version="v4.0", api="cards"))
