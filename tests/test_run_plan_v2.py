"""Unit tests for the RunPlanV2 domain model and parse/serialise helpers."""

from __future__ import annotations

import dataclasses
from typing import cast

import pytest

from conformance.json_types import JsonObject, JsonValue
from conformance.run_plan_v2 import (
    RunPlanV2ParseError,
    parse_run_plan_v2,
    serialise_run_plan_v2,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_doc() -> dict[str, JsonValue]:
    """Return a fully populated valid RunPlanV2 JSON document."""
    return {
        "schemaVersion": "2",
        "target": {
            "standard": "obl",
            "specification": "read-write",
            "securityProfile": "fapi1-advanced",
            "specificationVersion": "v4.0.1",
            "catalogueHash": "sha256:abc123",
        },
        "resourceGroups": ["ais", "pis"],
        "endpointSelections": [
            {
                "endpointId": "get-accounts",
                "operation": "GET",
                "selected": True,
                "fieldValues": {"accountId": "12345"},
            },
            {
                "endpointId": "get-account",
                "operation": "GET",
                "selected": False,
            },
        ],
        "testData": {"debtorAccountIdentification": "11223344"},
    }


def _minimal_doc() -> dict[str, JsonValue]:
    """Return the minimal valid RunPlanV2 JSON document."""
    return {
        "schemaVersion": "2",
        "target": {
            "standard": "obl",
            "specification": "dynamic-client-registration",
            "securityProfile": "fapi1-advanced",
            "specificationVersion": "3.3",
            "catalogueHash": "sha256:dcr",
        },
    }


# ---------------------------------------------------------------------------
# parse_run_plan_v2 — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_full_doc() -> None:
    plan = parse_run_plan_v2(_valid_doc())
    assert plan.schema_version == "2"
    assert plan.target.standard == "obl"
    assert plan.target.specification == "read-write"
    assert plan.target.security_profile == "fapi1-advanced"
    assert plan.target.specification_version == "v4.0.1"
    assert plan.target.catalogue_hash == "sha256:abc123"
    assert plan.resource_groups == ("ais", "pis")
    assert len(plan.endpoint_selections) == 2
    assert plan.test_data == {"debtorAccountIdentification": "11223344"}


@pytest.mark.unit
def test_parse_minimal_doc() -> None:
    plan = parse_run_plan_v2(_minimal_doc())
    assert plan.schema_version == "2"
    assert plan.resource_groups == ()
    assert plan.endpoint_selections == ()
    assert dict(plan.test_data) == {}


@pytest.mark.unit
def test_parse_endpoint_selection_selected_true() -> None:
    plan = parse_run_plan_v2(_valid_doc())
    sel = plan.endpoint_selections[0]
    assert sel.endpoint_id == "get-accounts"
    assert sel.operation == "GET"
    assert sel.selected is True
    assert sel.field_values == {"accountId": "12345"}


@pytest.mark.unit
def test_parse_endpoint_selection_selected_false_no_field_values() -> None:
    plan = parse_run_plan_v2(_valid_doc())
    sel = plan.endpoint_selections[1]
    assert sel.selected is False
    assert dict(sel.field_values) == {}


@pytest.mark.unit
def test_parse_omits_resource_groups_returns_empty_tuple() -> None:
    doc = _valid_doc()
    del doc["resourceGroups"]
    plan = parse_run_plan_v2(doc)
    assert plan.resource_groups == ()


@pytest.mark.unit
def test_parse_omits_endpoint_selections_returns_empty_tuple() -> None:
    doc = _valid_doc()
    del doc["endpointSelections"]
    plan = parse_run_plan_v2(doc)
    assert plan.endpoint_selections == ()


@pytest.mark.unit
def test_parse_omits_test_data_returns_empty_mapping() -> None:
    doc = _valid_doc()
    del doc["testData"]
    plan = parse_run_plan_v2(doc)
    assert dict(plan.test_data) == {}


# ---------------------------------------------------------------------------
# parse_run_plan_v2 — schema version checks
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_schema_version_1_raises() -> None:
    doc = _valid_doc()
    doc["schemaVersion"] = "1"
    with pytest.raises(RunPlanV2ParseError, match='"2"'):
        parse_run_plan_v2(doc)


@pytest.mark.unit
def test_parse_schema_version_missing_raises() -> None:
    doc = _valid_doc()
    del doc["schemaVersion"]
    with pytest.raises(RunPlanV2ParseError, match="schemaVersion"):
        parse_run_plan_v2(doc)


@pytest.mark.unit
def test_parse_schema_version_integer_raises() -> None:
    doc = _valid_doc()
    doc["schemaVersion"] = 2
    with pytest.raises(RunPlanV2ParseError, match="schemaVersion"):
        parse_run_plan_v2(doc)


# ---------------------------------------------------------------------------
# parse_run_plan_v2 — target error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_missing_target_raises() -> None:
    doc = _valid_doc()
    del doc["target"]
    with pytest.raises(RunPlanV2ParseError, match="target"):
        parse_run_plan_v2(doc)


@pytest.mark.unit
def test_parse_target_missing_standard_raises() -> None:
    doc = _valid_doc()
    cast(JsonObject, doc["target"]).pop("standard")
    with pytest.raises(RunPlanV2ParseError, match="standard"):
        parse_run_plan_v2(doc)


@pytest.mark.unit
def test_parse_target_empty_catalogue_hash_raises() -> None:
    doc = _valid_doc()
    cast(JsonObject, doc["target"])["catalogueHash"] = ""
    with pytest.raises(RunPlanV2ParseError, match="catalogueHash"):
        parse_run_plan_v2(doc)


# ---------------------------------------------------------------------------
# parse_run_plan_v2 — endpointSelections error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_endpoint_selections_not_array_raises() -> None:
    doc = _valid_doc()
    doc["endpointSelections"] = "bad"
    with pytest.raises(RunPlanV2ParseError, match="endpointSelections"):
        parse_run_plan_v2(doc)


@pytest.mark.unit
def test_parse_endpoint_selection_missing_endpoint_id_raises() -> None:
    doc = _valid_doc()
    doc["endpointSelections"] = [{"operation": "GET", "selected": True}]
    with pytest.raises(RunPlanV2ParseError, match="endpointId"):
        parse_run_plan_v2(doc)


@pytest.mark.unit
def test_parse_endpoint_selection_non_bool_selected_raises() -> None:
    doc = _valid_doc()
    doc["endpointSelections"] = [{"endpointId": "e", "operation": "GET", "selected": "yes"}]
    with pytest.raises(RunPlanV2ParseError, match="selected"):
        parse_run_plan_v2(doc)


@pytest.mark.unit
def test_parse_field_values_not_object_raises() -> None:
    doc = _valid_doc()
    doc["endpointSelections"] = [{"endpointId": "e", "operation": "GET", "selected": True, "fieldValues": "bad"}]
    with pytest.raises(RunPlanV2ParseError, match="fieldValues"):
        parse_run_plan_v2(doc)


@pytest.mark.unit
def test_parse_field_values_non_string_value_raises() -> None:
    doc = _valid_doc()
    doc["endpointSelections"] = [{"endpointId": "e", "operation": "GET", "selected": True, "fieldValues": {"k": 42}}]
    with pytest.raises(RunPlanV2ParseError, match="fieldValues"):
        parse_run_plan_v2(doc)


@pytest.mark.unit
def test_parse_test_data_not_object_raises() -> None:
    doc = _valid_doc()
    doc["testData"] = ["not", "an", "object"]
    with pytest.raises(RunPlanV2ParseError, match="testData"):
        parse_run_plan_v2(doc)


@pytest.mark.unit
def test_parse_test_data_non_string_value_raises() -> None:
    doc = _valid_doc()
    doc["testData"] = {"key": 99}
    with pytest.raises(RunPlanV2ParseError, match="testData"):
        parse_run_plan_v2(doc)


# ---------------------------------------------------------------------------
# RunPlanV2 is frozen
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_plan_v2_is_frozen() -> None:
    plan = parse_run_plan_v2(_minimal_doc())
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.schema_version = "3"  # type: ignore[misc, assignment]


@pytest.mark.unit
def test_endpoint_selection_is_frozen() -> None:
    plan = parse_run_plan_v2(_valid_doc())
    sel = plan.endpoint_selections[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        sel.endpoint_id = "mutated"  # type: ignore[misc]  # type: ignore[misc]


@pytest.mark.unit
def test_target_coordinates_is_frozen() -> None:
    plan = parse_run_plan_v2(_valid_doc())
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.target.standard = "other"  # type: ignore[misc]  # type: ignore[misc]


# ---------------------------------------------------------------------------
# serialise_run_plan_v2 — round-trip
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_serialise_round_trip_full() -> None:
    plan = parse_run_plan_v2(_valid_doc())
    doc = serialise_run_plan_v2(plan)
    restored = parse_run_plan_v2(doc)
    assert restored == plan


@pytest.mark.unit
def test_serialise_round_trip_minimal() -> None:
    plan = parse_run_plan_v2(_minimal_doc())
    doc = serialise_run_plan_v2(plan)
    restored = parse_run_plan_v2(doc)
    assert restored == plan


@pytest.mark.unit
def test_serialise_omits_resource_groups_when_empty() -> None:
    plan = parse_run_plan_v2(_minimal_doc())
    doc = serialise_run_plan_v2(plan)
    assert "resourceGroups" not in doc


@pytest.mark.unit
def test_serialise_omits_endpoint_selections_when_empty() -> None:
    plan = parse_run_plan_v2(_minimal_doc())
    doc = serialise_run_plan_v2(plan)
    assert "endpointSelections" not in doc


@pytest.mark.unit
def test_serialise_omits_test_data_when_empty() -> None:
    plan = parse_run_plan_v2(_minimal_doc())
    doc = serialise_run_plan_v2(plan)
    assert "testData" not in doc


@pytest.mark.unit
def test_serialise_omits_field_values_when_empty() -> None:
    plan = parse_run_plan_v2(_valid_doc())
    doc = serialise_run_plan_v2(plan)
    sel_doc = cast(JsonObject, cast("list[object]", doc["endpointSelections"])[1])
    assert "fieldValues" not in sel_doc  # sel_doc is JsonObject (endpointSelection serialised)


@pytest.mark.unit
def test_serialise_uses_camel_case_keys() -> None:
    plan = parse_run_plan_v2(_valid_doc())
    doc = serialise_run_plan_v2(plan)
    target = doc["target"]
    assert "specificationVersion" in target  # type: ignore[operator]
    assert "securityProfile" in target  # type: ignore[operator]
    assert "catalogueHash" in target  # type: ignore[operator]
    assert "specification_version" not in target  # type: ignore[operator]


@pytest.mark.unit
def test_serialise_schema_version_is_two() -> None:
    plan = parse_run_plan_v2(_minimal_doc())
    doc = serialise_run_plan_v2(plan)
    assert doc["schemaVersion"] == "2"
