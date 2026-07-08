"""Unit tests for the TestTargetConfig domain model and parse/serialise helpers."""

from __future__ import annotations

import dataclasses

import pytest

from conformance.json_types import JsonObject
from conformance.target_config import (
    TestTargetConfig,
    TestTargetConfigError,
    parse_test_target_config,
    serialise_test_target_config,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_rw_doc() -> JsonObject:
    """Return a minimal valid Read/Write testTarget JSON doc."""
    return {
        "standard": "obl",
        "specification": "read-write",
        "securityProfile": "fapi1-advanced",
        "specificationVersion": "v4.0.1",
        "resourceGroups": ["ais", "pis"],
    }


def _valid_dcr_doc() -> JsonObject:
    """Return a minimal valid DCR testTarget JSON doc."""
    return {
        "standard": "obl",
        "specification": "dynamic-client-registration",
        "securityProfile": "fapi1-advanced",
        "specificationVersion": "3.3",
    }


# ---------------------------------------------------------------------------
# parse_test_target_config — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_read_write_target() -> None:
    target = parse_test_target_config(_valid_rw_doc())
    assert target.standard == "obl"
    assert target.specification == "read-write"
    assert target.security_profile == "fapi1-advanced"
    assert target.specification_version == "v4.0.1"
    assert target.resource_groups == ("ais", "pis")


@pytest.mark.unit
def test_parse_dcr_target_no_resource_groups() -> None:
    target = parse_test_target_config(_valid_dcr_doc())
    assert target.specification == "dynamic-client-registration"
    assert target.resource_groups == ()


@pytest.mark.unit
def test_parse_omits_security_profile_defaults_to_fapi1_advanced() -> None:
    doc = _valid_rw_doc()
    del doc["securityProfile"]
    target = parse_test_target_config(doc)
    assert target.security_profile == "fapi1-advanced"


@pytest.mark.unit
def test_parse_omits_resource_groups_returns_empty_tuple() -> None:
    doc = _valid_rw_doc()
    del doc["resourceGroups"]
    target = parse_test_target_config(doc)
    assert target.resource_groups == ()


@pytest.mark.unit
def test_parse_single_resource_group() -> None:
    doc = _valid_rw_doc()
    doc["resourceGroups"] = ["ais"]
    target = parse_test_target_config(doc)
    assert target.resource_groups == ("ais",)


# ---------------------------------------------------------------------------
# parse_test_target_config — error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_non_object_raises() -> None:
    with pytest.raises(TestTargetConfigError, match="must be a JSON object"):
        parse_test_target_config("not-an-object")


@pytest.mark.unit
def test_parse_missing_standard_raises() -> None:
    doc = _valid_rw_doc()
    del doc["standard"]
    with pytest.raises(TestTargetConfigError, match="standard"):
        parse_test_target_config(doc)


@pytest.mark.unit
def test_parse_unsupported_standard_raises() -> None:
    doc = _valid_rw_doc()
    doc["standard"] = "fca"
    with pytest.raises(TestTargetConfigError, match="'standard'"):
        parse_test_target_config(doc)


@pytest.mark.unit
def test_parse_missing_specification_raises() -> None:
    doc = _valid_rw_doc()
    del doc["specification"]
    with pytest.raises(TestTargetConfigError, match="specification"):
        parse_test_target_config(doc)


@pytest.mark.unit
def test_parse_unsupported_specification_raises() -> None:
    doc = _valid_rw_doc()
    doc["specification"] = "unknown-spec"
    with pytest.raises(TestTargetConfigError, match="'specification'"):
        parse_test_target_config(doc)


@pytest.mark.unit
def test_parse_missing_specification_version_raises() -> None:
    doc = _valid_rw_doc()
    del doc["specificationVersion"]
    with pytest.raises(TestTargetConfigError, match="specificationVersion"):
        parse_test_target_config(doc)


@pytest.mark.unit
def test_parse_empty_specification_version_raises() -> None:
    doc = _valid_rw_doc()
    doc["specificationVersion"] = ""
    with pytest.raises(TestTargetConfigError, match="specificationVersion"):
        parse_test_target_config(doc)


@pytest.mark.unit
def test_parse_unsupported_security_profile_raises() -> None:
    doc = _valid_rw_doc()
    doc["securityProfile"] = "fapi2"
    with pytest.raises(TestTargetConfigError, match="securityProfile"):
        parse_test_target_config(doc)


@pytest.mark.unit
def test_parse_non_string_security_profile_raises() -> None:
    doc = _valid_rw_doc()
    doc["securityProfile"] = 42
    with pytest.raises(TestTargetConfigError, match="securityProfile"):
        parse_test_target_config(doc)


@pytest.mark.unit
def test_parse_resource_groups_not_array_raises() -> None:
    doc = _valid_rw_doc()
    doc["resourceGroups"] = "ais"
    with pytest.raises(TestTargetConfigError, match="resourceGroups"):
        parse_test_target_config(doc)


@pytest.mark.unit
def test_parse_resource_groups_non_string_element_raises() -> None:
    doc = _valid_rw_doc()
    doc["resourceGroups"] = ["ais", 99]
    with pytest.raises(TestTargetConfigError, match="resourceGroups"):
        parse_test_target_config(doc)


@pytest.mark.unit
def test_parse_resource_groups_empty_string_element_raises() -> None:
    doc = _valid_rw_doc()
    doc["resourceGroups"] = ["ais", ""]
    with pytest.raises(TestTargetConfigError, match="resourceGroups"):
        parse_test_target_config(doc)


# ---------------------------------------------------------------------------
# TestTargetConfig is frozen
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_target_config_is_frozen() -> None:
    target = TestTargetConfig(
        standard="obl",
        specification="read-write",
        security_profile="fapi1-advanced",
        specification_version="v4.0.1",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        target.standard = "other"  # type: ignore[misc, assignment]


# ---------------------------------------------------------------------------
# serialise_test_target_config — round-trip
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_serialise_round_trip_with_resource_groups() -> None:
    target = parse_test_target_config(_valid_rw_doc())
    doc = serialise_test_target_config(target)
    restored = parse_test_target_config(doc)
    assert restored == target


@pytest.mark.unit
def test_serialise_round_trip_without_resource_groups() -> None:
    target = parse_test_target_config(_valid_dcr_doc())
    doc = serialise_test_target_config(target)
    restored = parse_test_target_config(doc)
    assert restored == target


@pytest.mark.unit
def test_serialise_omits_resource_groups_key_when_empty() -> None:
    target = parse_test_target_config(_valid_dcr_doc())
    doc = serialise_test_target_config(target)
    assert "resourceGroups" not in doc


@pytest.mark.unit
def test_serialise_includes_resource_groups_when_present() -> None:
    target = parse_test_target_config(_valid_rw_doc())
    doc = serialise_test_target_config(target)
    assert doc["resourceGroups"] == ["ais", "pis"]


@pytest.mark.unit
def test_serialise_camel_case_keys() -> None:
    target = parse_test_target_config(_valid_rw_doc())
    doc = serialise_test_target_config(target)
    assert "specificationVersion" in doc
    assert "securityProfile" in doc
    assert "specification_version" not in doc
