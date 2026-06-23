"""Unit tests for v1 manifest ``testValues`` parsing."""

from __future__ import annotations

from typing import cast

import pytest

from conformance.json_types import JsonValue
from conformance.manifest import ManifestError, _parse_v1_test_values


def _raw_manifest_with_test_values() -> dict[str, JsonValue]:
    """Build a minimal manifest object declaring ``testValues``.

    Returns:
        Raw v1 manifest JSON object suitable for parser tests.
    """
    return {
        "schemaVersion": "v1",
        "name": "Test values",
        "testValues": {
            "baseline": {"creditorName": "Test Merchant"},
            "generatedKeys": {"consentIdempotencyKey": "per-run-uuid"},
            "allowedCustomKeys": ["creditorName"],
        },
        "steps": [],
    }


@pytest.mark.unit
def test_parse_v1_test_values_parses_minimal_valid_block() -> None:
    """Parser returns ManifestTestValues and known keys for a valid block."""
    parsed, known_keys = _parse_v1_test_values(_raw_manifest_with_test_values())

    assert parsed is not None
    assert dict(parsed.baseline) == {"creditorName": "Test Merchant"}
    assert dict(parsed.generated_keys) == {"consentIdempotencyKey": "per-run-uuid"}
    assert parsed.allowed_custom_keys == frozenset({"creditorName"})
    assert known_keys == frozenset({"creditorName", "consentIdempotencyKey"})


@pytest.mark.unit
def test_parse_v1_test_values_rejects_missing_baseline() -> None:
    """Parser raises ManifestError when baseline is absent."""
    raw_manifest = _raw_manifest_with_test_values()
    raw_test_values = cast(dict[str, JsonValue], raw_manifest["testValues"])
    del raw_test_values["baseline"]

    with pytest.raises(ManifestError, match="baseline"):
        _parse_v1_test_values(raw_manifest)


@pytest.mark.unit
def test_parse_v1_test_values_rejects_invalid_key_names() -> None:
    """Parser raises ManifestError for invalid baseline or allow-list key names."""
    raw_manifest = _raw_manifest_with_test_values()
    raw_test_values = cast(dict[str, JsonValue], raw_manifest["testValues"])
    raw_test_values["baseline"] = {"1bad": "value"}

    with pytest.raises(ManifestError, match="invalid"):
        _parse_v1_test_values(raw_manifest)


@pytest.mark.unit
def test_parse_v1_test_values_rejects_unknown_generation_strategy() -> None:
    """Parser raises ManifestError for unknown generated-key strategies."""
    raw_manifest = _raw_manifest_with_test_values()
    raw_test_values = cast(dict[str, JsonValue], raw_manifest["testValues"])
    raw_test_values["generatedKeys"] = {"consentIdempotencyKey": "bad-strategy"}

    with pytest.raises(ManifestError, match="per-run-uuid"):
        _parse_v1_test_values(raw_manifest)


@pytest.mark.unit
def test_parse_v1_test_values_parses_allowed_custom_keys_as_frozenset() -> None:
    """Parser stores allowedCustomKeys as a frozenset."""
    parsed, _ = _parse_v1_test_values(_raw_manifest_with_test_values())

    assert parsed is not None
    assert isinstance(parsed.allowed_custom_keys, frozenset)


@pytest.mark.unit
def test_parse_v1_test_values_allows_omitted_generated_keys() -> None:
    """Parser accepts testValues blocks that omit generatedKeys."""
    raw_manifest = _raw_manifest_with_test_values()
    raw_test_values = cast(dict[str, JsonValue], raw_manifest["testValues"])
    del raw_test_values["generatedKeys"]

    parsed, known_keys = _parse_v1_test_values(raw_manifest)

    assert parsed is not None
    assert dict(parsed.generated_keys) == {}
    assert known_keys == frozenset({"creditorName"})
