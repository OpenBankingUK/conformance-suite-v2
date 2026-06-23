"""Unit tests for the RunPlan domain model and its parse/serialise helpers."""

from __future__ import annotations

import pytest

from conformance.json_types import JsonValue
from conformance.model_bank_config import TestValuesConfig
from conformance.run_plan import (
    RunPlan,
    RunPlanParseError,
    RunPlanSuiteCoordinates,
    RunPlanTestData,
    RunPlanTestValues,
    compute_manifest_hash,
    parse_run_plan,
    run_plan_to_test_values_config,
    serialise_run_plan,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_DOC: dict[str, JsonValue] = {
    "schemaVersion": "1",
    "suite": {
        "id": "aisp-v3-2",
        "version": "4.0.1",
        "manifestHash": "sha256:abc123",
    },
    "selectedStepIds": ["step-001", "step-002"],
    "testValues": {
        "profile": "domestic-standard",
        "customValues": {"paymentAmount": "10.00"},
    },
}


def _valid_plan() -> RunPlan:
    """Return a fully populated RunPlan instance for use in assertions.

    Returns:
        A :class:`RunPlan` with all fields populated.
    """
    return RunPlan(
        schema_version="1",
        suite=RunPlanSuiteCoordinates(
            id="aisp-v3-2",
            version="4.0.1",
            manifest_hash="sha256:abc123",
        ),
        selected_step_ids=("step-001", "step-002"),
        test_values=RunPlanTestValues(
            profile="domestic-standard",
            custom_values={"paymentAmount": "10.00"},
        ),
    )


# ---------------------------------------------------------------------------
# parse_run_plan — happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_run_plan_full_document() -> None:
    """parse_run_plan returns a RunPlan for a fully populated valid document."""
    plan = parse_run_plan(_VALID_DOC)
    assert plan.schema_version == "1"
    assert plan.suite.id == "aisp-v3-2"
    assert plan.suite.version == "4.0.1"
    assert plan.suite.manifest_hash == "sha256:abc123"
    assert plan.selected_step_ids == ("step-001", "step-002")
    assert plan.test_values.profile == "domestic-standard"
    assert plan.test_values.custom_values == {"paymentAmount": "10.00"}


@pytest.mark.unit
def test_parse_run_plan_absent_test_values() -> None:
    """parse_run_plan defaults testValues to no profile and no custom values when absent."""
    doc: dict[str, JsonValue] = {
        "schemaVersion": "1",
        "suite": {"id": "x", "version": "1.0", "manifestHash": "sha256:aaa"},
        "selectedStepIds": [],
    }
    plan = parse_run_plan(doc)
    assert plan.test_values.profile is None
    assert dict(plan.test_values.custom_values) == {}


@pytest.mark.unit
def test_parse_run_plan_null_profile() -> None:
    """parse_run_plan accepts JSON null as equivalent to absent profile."""
    doc: dict[str, JsonValue] = {
        "schemaVersion": "1",
        "suite": {"id": "x", "version": "1.0", "manifestHash": "sha256:aaa"},
        "selectedStepIds": [],
        "testValues": {"profile": None, "customValues": {}},
    }
    plan = parse_run_plan(doc)
    assert plan.test_values.profile is None


@pytest.mark.unit
def test_parse_run_plan_empty_selected_step_ids() -> None:
    """parse_run_plan accepts an empty selectedStepIds list."""
    doc: dict[str, JsonValue] = {
        "schemaVersion": "1",
        "suite": {"id": "x", "version": "1.0", "manifestHash": "sha256:aaa"},
        "selectedStepIds": [],
    }
    plan = parse_run_plan(doc)
    assert plan.selected_step_ids == ()


@pytest.mark.unit
def test_parse_run_plan_empty_custom_values() -> None:
    """parse_run_plan accepts an empty customValues object."""
    doc: dict[str, JsonValue] = {
        "schemaVersion": "1",
        "suite": {"id": "x", "version": "1.0", "manifestHash": "sha256:aaa"},
        "selectedStepIds": ["step-001"],
        "testValues": {"customValues": {}},
    }
    plan = parse_run_plan(doc)
    assert dict(plan.test_values.custom_values) == {}
    assert plan.test_values.profile is None


@pytest.mark.unit
def test_parse_run_plan_empty_string_custom_value_is_valid() -> None:
    """parse_run_plan accepts an empty string as a valid customValues value."""
    doc: dict[str, JsonValue] = {
        "schemaVersion": "1",
        "suite": {"id": "x", "version": "1.0", "manifestHash": "sha256:aaa"},
        "selectedStepIds": [],
        "testValues": {"customValues": {"myKey": ""}},
    }
    plan = parse_run_plan(doc)
    assert plan.test_values.custom_values["myKey"] == ""


# ---------------------------------------------------------------------------
# parse_run_plan — error cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_run_plan_not_an_object() -> None:
    """parse_run_plan raises RunPlanParseError when the input is not a JSON object."""
    with pytest.raises(RunPlanParseError, match="JSON object"):
        parse_run_plan("not a dict")


@pytest.mark.unit
def test_parse_run_plan_wrong_schema_version() -> None:
    """parse_run_plan raises RunPlanParseError for an unrecognised schemaVersion."""
    doc = {**_VALID_DOC, "schemaVersion": "99"}
    with pytest.raises(RunPlanParseError, match="schemaVersion"):
        parse_run_plan(doc)


@pytest.mark.unit
def test_parse_run_plan_missing_schema_version() -> None:
    """parse_run_plan raises RunPlanParseError when schemaVersion is absent."""
    doc = {k: v for k, v in _VALID_DOC.items() if k != "schemaVersion"}
    with pytest.raises(RunPlanParseError, match="schemaVersion"):
        parse_run_plan(doc)


@pytest.mark.unit
def test_parse_run_plan_missing_suite() -> None:
    """parse_run_plan raises RunPlanParseError when suite is absent."""
    doc = {k: v for k, v in _VALID_DOC.items() if k != "suite"}
    with pytest.raises(RunPlanParseError, match="suite"):
        parse_run_plan(doc)


@pytest.mark.unit
def test_parse_run_plan_suite_missing_id() -> None:
    """parse_run_plan raises RunPlanParseError when suite.id is absent."""
    doc: dict[str, JsonValue] = {
        **_VALID_DOC,
        "suite": {"version": "1.0", "manifestHash": "sha256:aaa"},
    }
    with pytest.raises(RunPlanParseError, match="'id'"):
        parse_run_plan(doc)


@pytest.mark.unit
def test_parse_run_plan_suite_empty_id() -> None:
    """parse_run_plan raises RunPlanParseError when suite.id is an empty string."""
    doc: dict[str, JsonValue] = {
        **_VALID_DOC,
        "suite": {"id": "", "version": "1.0", "manifestHash": "sha256:aaa"},
    }
    with pytest.raises(RunPlanParseError, match="'id'"):
        parse_run_plan(doc)


@pytest.mark.unit
def test_parse_run_plan_missing_selected_step_ids() -> None:
    """parse_run_plan raises RunPlanParseError when selectedStepIds is absent."""
    doc = {k: v for k, v in _VALID_DOC.items() if k != "selectedStepIds"}
    with pytest.raises(RunPlanParseError, match="selectedStepIds"):
        parse_run_plan(doc)


@pytest.mark.unit
def test_parse_run_plan_selected_step_ids_not_list() -> None:
    """parse_run_plan raises RunPlanParseError when selectedStepIds is not a list."""
    doc: dict[str, JsonValue] = {**_VALID_DOC, "selectedStepIds": "step-001"}
    with pytest.raises(RunPlanParseError, match="selectedStepIds"):
        parse_run_plan(doc)


@pytest.mark.unit
def test_parse_run_plan_step_id_not_string() -> None:
    """parse_run_plan raises RunPlanParseError when a step id is not a string."""
    doc: dict[str, JsonValue] = {**_VALID_DOC, "selectedStepIds": [123]}
    with pytest.raises(RunPlanParseError, match=r"selectedStepIds\[0\]"):
        parse_run_plan(doc)


@pytest.mark.unit
def test_parse_run_plan_step_id_empty_string() -> None:
    """parse_run_plan raises RunPlanParseError when a step id is an empty string."""
    doc: dict[str, JsonValue] = {**_VALID_DOC, "selectedStepIds": [""]}
    with pytest.raises(RunPlanParseError, match=r"selectedStepIds\[0\]"):
        parse_run_plan(doc)


@pytest.mark.unit
def test_parse_run_plan_test_values_not_object() -> None:
    """parse_run_plan raises RunPlanParseError when testValues is not an object."""
    doc: dict[str, JsonValue] = {**_VALID_DOC, "testValues": "bad"}
    with pytest.raises(RunPlanParseError, match="testValues"):
        parse_run_plan(doc)


@pytest.mark.unit
def test_parse_run_plan_profile_empty_string() -> None:
    """parse_run_plan raises RunPlanParseError when testValues.profile is empty string."""
    doc: dict[str, JsonValue] = {
        **_VALID_DOC,
        "testValues": {"profile": "", "customValues": {}},
    }
    with pytest.raises(RunPlanParseError, match="profile"):
        parse_run_plan(doc)


@pytest.mark.unit
def test_parse_run_plan_profile_wrong_type() -> None:
    """parse_run_plan raises RunPlanParseError when testValues.profile is not a string."""
    doc: dict[str, JsonValue] = {
        **_VALID_DOC,
        "testValues": {"profile": 42, "customValues": {}},
    }
    with pytest.raises(RunPlanParseError, match="profile"):
        parse_run_plan(doc)


@pytest.mark.unit
def test_parse_run_plan_custom_values_not_object() -> None:
    """parse_run_plan raises RunPlanParseError when customValues is not an object."""
    doc: dict[str, JsonValue] = {
        **_VALID_DOC,
        "testValues": {"customValues": ["not", "an", "object"]},
    }
    with pytest.raises(RunPlanParseError, match="customValues"):
        parse_run_plan(doc)


@pytest.mark.unit
def test_parse_run_plan_custom_values_non_string_value() -> None:
    """parse_run_plan raises RunPlanParseError when a customValues entry is not a string."""
    doc: dict[str, JsonValue] = {
        **_VALID_DOC,
        "testValues": {"customValues": {"amount": 99}},
    }
    with pytest.raises(RunPlanParseError, match="customValues"):
        parse_run_plan(doc)


# ---------------------------------------------------------------------------
# serialise_run_plan — round-trip
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_serialise_run_plan_camel_case_keys() -> None:
    """serialise_run_plan produces camelCase wire keys."""
    plan = _valid_plan()
    out = serialise_run_plan(plan)
    assert "schemaVersion" in out
    assert "suite" in out
    assert "selectedStepIds" in out
    assert "testValues" in out
    assert "manifestHash" in out["suite"]  # type: ignore[operator]
    assert "customValues" in out["testValues"]  # type: ignore[operator]


@pytest.mark.unit
def test_round_trip_full_document() -> None:
    """parse_run_plan(serialise_run_plan(plan)) is equal to the original plan."""
    plan = _valid_plan()
    serialised = serialise_run_plan(plan)
    restored = parse_run_plan(serialised)
    assert restored.schema_version == plan.schema_version
    assert restored.suite.id == plan.suite.id
    assert restored.suite.version == plan.suite.version
    assert restored.suite.manifest_hash == plan.suite.manifest_hash
    assert restored.selected_step_ids == plan.selected_step_ids
    assert restored.test_values.profile == plan.test_values.profile
    assert dict(restored.test_values.custom_values) == dict(plan.test_values.custom_values)


@pytest.mark.unit
def test_serialise_omits_empty_test_values_section() -> None:
    """serialise_run_plan omits testValues when profile/custom-values are both empty."""
    plan = RunPlan(
        schema_version="1",
        suite=RunPlanSuiteCoordinates(id="x", version="1.0", manifest_hash="sha256:aaa"),
        selected_step_ids=(),
        test_values=RunPlanTestValues(profile=None, custom_values={}),
    )
    out = serialise_run_plan(plan)
    assert "testValues" not in out


@pytest.mark.unit
def test_serialise_empty_string_custom_value_preserved() -> None:
    """serialise_run_plan preserves empty-string custom values."""
    plan = RunPlan(
        schema_version="1",
        suite=RunPlanSuiteCoordinates(id="x", version="1.0", manifest_hash="sha256:aaa"),
        selected_step_ids=(),
        test_values=RunPlanTestValues(profile=None, custom_values={"key": ""}),
    )
    out = serialise_run_plan(plan)
    test_values = out["testValues"]
    assert isinstance(test_values, dict)
    custom = test_values["customValues"]
    assert isinstance(custom, dict)
    assert custom["key"] == ""


# ---------------------------------------------------------------------------
# run_plan_to_test_values_config
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_plan_to_test_values_config_with_profile_and_values() -> None:
    """run_plan_to_test_values_config maps profile and custom_values to TestValuesConfig."""
    plan = _valid_plan()
    config = run_plan_to_test_values_config(plan)
    assert config is not None
    assert isinstance(config, TestValuesConfig)
    assert config.profile == "domestic-standard"
    assert dict(config.overrides) == {"paymentAmount": "10.00"}


@pytest.mark.unit
def test_run_plan_to_test_values_config_profile_only() -> None:
    """run_plan_to_test_values_config returns config with no overrides when only profile set."""
    plan = RunPlan(
        schema_version="1",
        suite=RunPlanSuiteCoordinates(id="x", version="1.0", manifest_hash="sha256:aaa"),
        selected_step_ids=(),
        test_values=RunPlanTestValues(profile="my-profile", custom_values={}),
    )
    config = run_plan_to_test_values_config(plan)
    assert config is not None
    assert config.profile == "my-profile"
    assert dict(config.overrides) == {}


@pytest.mark.unit
def test_run_plan_to_test_values_config_custom_values_only() -> None:
    """run_plan_to_test_values_config returns config with None profile when only custom values set."""
    plan = RunPlan(
        schema_version="1",
        suite=RunPlanSuiteCoordinates(id="x", version="1.0", manifest_hash="sha256:aaa"),
        selected_step_ids=(),
        test_values=RunPlanTestValues(profile=None, custom_values={"k": "v"}),
    )
    config = run_plan_to_test_values_config(plan)
    assert config is not None
    assert config.profile is None
    assert dict(config.overrides) == {"k": "v"}


@pytest.mark.unit
def test_run_plan_to_test_values_config_returns_none_when_no_values() -> None:
    """run_plan_to_test_values_config returns None when neither profile nor custom values present."""
    plan = RunPlan(
        schema_version="1",
        suite=RunPlanSuiteCoordinates(id="x", version="1.0", manifest_hash="sha256:aaa"),
        selected_step_ids=(),
        test_values=RunPlanTestValues(profile=None, custom_values={}),
    )
    assert run_plan_to_test_values_config(plan) is None


@pytest.mark.unit
def test_run_plan_to_test_values_config_empty_string_value_preserved() -> None:
    """run_plan_to_test_values_config preserves empty-string custom values in overrides."""
    plan = RunPlan(
        schema_version="1",
        suite=RunPlanSuiteCoordinates(id="x", version="1.0", manifest_hash="sha256:aaa"),
        selected_step_ids=(),
        test_values=RunPlanTestValues(profile=None, custom_values={"k": ""}),
    )
    config = run_plan_to_test_values_config(plan)
    assert config is not None
    assert config.overrides["k"] == ""


# ---------------------------------------------------------------------------
# compute_manifest_hash
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_compute_manifest_hash_format() -> None:
    """compute_manifest_hash returns a string starting with 'sha256:'."""
    result = compute_manifest_hash(b"hello world")
    assert result.startswith("sha256:")
    hex_part = result[len("sha256:") :]
    assert len(hex_part) == 64
    assert all(c in "0123456789abcdef" for c in hex_part)


@pytest.mark.unit
def test_compute_manifest_hash_deterministic() -> None:
    """compute_manifest_hash returns the same hash for the same bytes."""
    data = b"manifest content"
    assert compute_manifest_hash(data) == compute_manifest_hash(data)


@pytest.mark.unit
def test_compute_manifest_hash_differs_for_different_bytes() -> None:
    """compute_manifest_hash returns different hashes for different inputs."""
    assert compute_manifest_hash(b"aaa") != compute_manifest_hash(b"bbb")


@pytest.mark.unit
def test_compute_manifest_hash_empty_bytes() -> None:
    """compute_manifest_hash handles empty bytes without error."""
    result = compute_manifest_hash(b"")
    assert result.startswith("sha256:")
    assert len(result) == len("sha256:") + 64


@pytest.mark.unit
def test_compute_manifest_hash_known_value() -> None:
    """compute_manifest_hash produces the known SHA-256 of b'hello world'."""
    import hashlib

    expected_hex = hashlib.sha256(b"hello world").hexdigest()
    result = compute_manifest_hash(b"hello world")
    assert result == f"sha256:{expected_hex}"


# ---------------------------------------------------------------------------
# testData support
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_run_plan_absent_test_data_defaults_to_empty_mapping() -> None:
    """parse_run_plan defaults absent testData to an empty immutable mapping."""
    doc: dict[str, JsonValue] = {
        "schemaVersion": "1",
        "suite": {"id": "x", "version": "1.0", "manifestHash": "sha256:aaa"},
        "selectedStepIds": [],
    }

    plan = parse_run_plan(doc)

    assert plan.test_data == RunPlanTestData(values={})


@pytest.mark.unit
def test_parse_run_plan_reads_test_data_values() -> None:
    """parse_run_plan reads custom test-data values from testData.values."""
    doc: dict[str, JsonValue] = {
        "schemaVersion": "1",
        "suite": {"id": "x", "version": "1.0", "manifestHash": "sha256:aaa"},
        "selectedStepIds": [],
        "testData": {"values": {"creditorName": "Test Merchant"}},
    }

    plan = parse_run_plan(doc)

    assert dict(plan.test_data.values) == {"creditorName": "Test Merchant"}


@pytest.mark.unit
def test_parse_run_plan_rejects_non_object_test_data() -> None:
    """parse_run_plan rejects a non-object testData section."""
    doc: dict[str, JsonValue] = {
        **_VALID_DOC,
        "testData": "bad",
    }

    with pytest.raises(RunPlanParseError, match="testData"):
        parse_run_plan(doc)


@pytest.mark.unit
def test_parse_run_plan_rejects_non_string_test_data_value() -> None:
    """parse_run_plan rejects non-string values inside testData.values."""
    doc: dict[str, JsonValue] = {
        **_VALID_DOC,
        "testData": {"values": {"creditorName": 7}},
    }

    with pytest.raises(RunPlanParseError, match="testData.values"):
        parse_run_plan(doc)


@pytest.mark.unit
def test_serialise_run_plan_includes_test_data_only_when_present() -> None:
    """serialise_run_plan emits testData only when the plan stores test-data deltas."""
    plan = RunPlan(
        schema_version="1",
        suite=RunPlanSuiteCoordinates(id="x", version="1.0", manifest_hash="sha256:aaa"),
        selected_step_ids=(),
        test_values=RunPlanTestValues(profile=None, custom_values={}),
        test_data=RunPlanTestData(values={"creditorName": "Test Merchant"}),
    )

    out = serialise_run_plan(plan)

    assert out["testData"] == {"values": {"creditorName": "Test Merchant"}}


@pytest.mark.unit
def test_serialise_run_plan_omits_empty_test_data() -> None:
    """serialise_run_plan omits testData when no test-data deltas are present."""
    plan = RunPlan(
        schema_version="1",
        suite=RunPlanSuiteCoordinates(id="x", version="1.0", manifest_hash="sha256:aaa"),
        selected_step_ids=(),
        test_values=RunPlanTestValues(profile=None, custom_values={}),
        test_data=RunPlanTestData(values={}),
    )

    out = serialise_run_plan(plan)

    assert "testData" not in out
