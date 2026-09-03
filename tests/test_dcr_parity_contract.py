"""Regression guards for the pinned Open Banking DCR 3.4 parity contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
"""Recursive JSON value used by the DCR parity contract fixture."""

CONTRACT_PATH = (
    Path(__file__).resolve().parents[1] / "conformance" / "standards" / "ob_dcr" / "v3_4" / "parity-contract.json"
)
"""Repository path of the machine-checkable DCR 3.4 parity contract."""

EXPECTED_SCENARIO_COUNTS = {
    "DCR-001": (1, 1),
    "DCR-002": (3, 6),
    "DCR-003": (4, 8),
    "DCR-004": (5, 15),
    "DCR-005": (4, 10),
    "DCR-007": (4, 9),
    "DCR-008": (4, 9),
    "DCR-009": (4, 9),
    "DCR-010": (4, 8),
    "DCR-011": (1, 4),
}
"""Expected case and step counts keyed by the preserved legacy scenario ID."""

EXPECTED_SCENARIO_STATUSES = {
    "DCR-001": [()],
    "DCR-002": [(201,), (200,), (204,)],
    "DCR-003": [(201,), (200,), (204,), (401,)],
    "DCR-004": [(400,), (400,), (400,), (400,), (400,)],
    "DCR-005": [(201,), (200,), (200,), (204,)],
    "DCR-007": [(201,), (401,), (200,), (204,)],
    "DCR-008": [(201,), (200,), (200,), (204,)],
    "DCR-009": [(201,), (200,), (204,), (401,)],
    "DCR-010": [(201,), (200,), (204,), (401,)],
    "DCR-011": [(400,)],
}
"""Expected HTTP statuses for each case in legacy scenario order."""

EXPECTED_SCENARIO_METHODS = {
    "DCR-001": ["POST"],
    "DCR-002": ["POST"],
    "DCR-003": ["POST", "GET", "DELETE"],
    "DCR-004": ["POST"],
    "DCR-005": ["POST", "GET"],
    "DCR-007": ["POST", "GET"],
    "DCR-008": ["POST", "PUT"],
    "DCR-009": ["POST", "PUT", "DELETE"],
    "DCR-010": ["POST", "GET", "DELETE"],
    "DCR-011": ["POST"],
}
"""Endpoint-selection gates for each DCR scenario."""

EXPECTED_LEGACY_CONFIG_FIELDS = {
    "spec_version",
    "wellknown_endpoint",
    "kid",
    "private_key",
    "transport_cert",
    "transport_key",
    "transport_root_cas",
    "discovery.token_endpoint_auth_methods_supported",
    "discovery.token_endpoint_auth_signing_alg_values_supported",
    "brand",
    "environment",
    "get_implemented",
    "put_implemented",
    "delete_implemented",
    "ssa",
    "aud",
    "issuer",
    "redirect_uris",
    "transport_cert_subject_dn",
    "use_oid",
    "signing certificate input",
    "CLI flag disablekeepalives",
}
"""Legacy concepts that require an explicit canonical migration decision."""


def _load_contract() -> dict[str, JsonValue]:
    """Load the repository's DCR parity contract.

    Returns:
        Parsed top-level parity contract object.
    """
    return _as_object(cast("JsonValue", json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))))


def _as_object(value: JsonValue) -> dict[str, JsonValue]:
    """Narrow a JSON value to an object.

    Args:
        value: JSON value expected to contain an object.

    Returns:
        The narrowed JSON object.
    """
    assert isinstance(value, dict)
    return value


def _as_list(value: JsonValue) -> list[JsonValue]:
    """Narrow a JSON value to an array.

    Args:
        value: JSON value expected to contain an array.

    Returns:
        The narrowed JSON array.
    """
    assert isinstance(value, list)
    return value


def _as_string(value: JsonValue) -> str:
    """Narrow a JSON value to a string.

    Args:
        value: JSON value expected to contain a string.

    Returns:
        The narrowed string.
    """
    assert isinstance(value, str)
    return value


@pytest.mark.unit
def test_dcr_parity_inventory_is_pinned_and_traceable() -> None:
    """Pin all legacy-correlated DCR scenario, case, and step identifiers."""
    contract = _load_contract()
    baseline = _as_object(contract["baseline"])
    inventory = _as_object(contract["inventory"])
    scenarios = [_as_object(value) for value in _as_list(contract["scenarios"])]
    step_definitions = _as_object(contract["stepDefinitions"])
    reference_ids = {_as_string(_as_object(value)["id"]) for value in _as_list(contract["references"])}

    assert baseline["repository"] == "OpenBankingUK/conformance-dcr"
    assert baseline["release"] == "v1.4.0"
    assert baseline["commit"] == "cc00a0065494e8e180c915621b9996bc2259ec8d"
    assert inventory["scenarioCount"] == 10
    assert inventory["caseCount"] == 34
    assert inventory["stepCount"] == 79
    assert inventory["missingScenarioIds"] == ["DCR-006"]

    scenario_ids = [_as_string(scenario["id"]) for scenario in scenarios]
    assert scenario_ids == list(EXPECTED_SCENARIO_COUNTS)
    assert inventory["scenarioIds"] == scenario_ids

    case_ids: set[str] = set()
    step_ids: set[str] = set()
    prerequisite_ids: set[str] = set()
    total_cases = 0
    total_steps = 0
    for scenario in scenarios:
        scenario_id = _as_string(scenario["id"])
        cases = [_as_object(value) for value in _as_list(scenario["cases"])]
        assert scenario["sourceSymbol"]
        assert set(map(_as_string, _as_list(scenario["normativeReferences"]))) <= reference_ids
        assert scenario["requiredSelectedMethods"] == EXPECTED_SCENARIO_METHODS[scenario_id]
        assert (len(cases), sum(len(_as_list(case["steps"])) for case in cases)) == EXPECTED_SCENARIO_COUNTS[
            scenario_id
        ]
        assert [
            tuple(cast("int", status) for status in _as_list(case["expectedHttpStatuses"])) for case in cases
        ] == EXPECTED_SCENARIO_STATUSES[scenario_id]
        total_cases += len(cases)
        for case_number, case in enumerate(cases, start=1):
            case_id = _as_string(case["id"])
            assert case_id == f"{scenario_id}-C{case_number:02d}"
            assert case_id not in case_ids
            case_ids.add(case_id)
            assert case["legacyName"]
            assert isinstance(case["stateConsumes"], list)
            assert isinstance(case["stateProduces"], list)
            prerequisite_ids.update(map(_as_string, _as_list(case["prerequisiteCaseIds"])))
            steps = [_as_object(value) for value in _as_list(case["steps"])]
            total_steps += len(steps)
            for step_number, step in enumerate(steps, start=1):
                step_id = _as_string(step["id"])
                assert step_id == f"{case_id}-S{step_number:02d}"
                assert step_id not in step_ids
                step_ids.add(step_id)
                definition_id = _as_string(step["definition"])
                assert definition_id in step_definitions
                assert _as_object(step_definitions[definition_id])["legacyBuilderCall"]

    assert total_cases == 34
    assert total_steps == 79
    assert prerequisite_ids <= case_ids


@pytest.mark.unit
def test_dcr_canonical_plan_contract_has_direct_locked_endpoint_scope() -> None:
    """Keep DCR plans resource-group-free with mandatory POST and generated token setup."""
    contract = _load_contract()
    canonical = _as_object(contract["canonicalPlanContract"])
    specification = _as_object(canonical["specification"])
    scope_rules = _as_object(canonical["scopeRules"])
    example = _as_object(canonical["example"])
    example_specification = _as_object(example["specification"])
    endpoints = [_as_object(value) for value in _as_list(example["endpoints"])]
    runtime = _as_object(contract["runtimeContract"])
    token_request = _as_object(runtime["tokenRequest"])

    assert canonical["schemaVersion"] == "1.0"
    assert specification == {
        "id": "dynamic-client-registration",
        "displayName": "Dynamic Client Registration",
        "family": "OBL_DCR",
        "version": "3.4",
    }
    assert example_specification["family"] == "OBL_DCR"
    assert example_specification["version"] == "3.4"
    assert "resourceGroups" not in example
    assert scope_rules["resourceGroups"] == "forbidden"
    assert [_as_string(endpoint["method"]) for endpoint in endpoints] == ["POST", "GET", "PUT", "DELETE"]
    assert endpoints[0]["required"] is True
    assert endpoints[0]["locked"] is True
    assert all(endpoint["required"] is False and endpoint["locked"] is False for endpoint in endpoints[1:])
    assert token_request["generatedDependency"] is True
    assert scope_rules["tokenOperation"] == "generated-dependency-not-selectable"


@pytest.mark.unit
def test_dcr_configuration_and_defect_decisions_cannot_drift() -> None:
    """Guard reusable configuration mappings and approved legacy corrections."""
    contract = _load_contract()
    mapping = _as_object(contract["configurationMapping"])
    fields = [_as_object(value) for value in _as_list(mapping["fields"])]
    deviations = [_as_object(value) for value in _as_list(contract["deviations"])]
    runtime = _as_object(contract["runtimeContract"])
    token = _as_object(runtime["tokenRequest"])
    state = _as_object(runtime["statePolicy"])
    evidence = _as_object(runtime["evidencePolicy"])
    response_validation = _as_object(runtime["responseValidation34"])
    registration_step = _as_object(_as_object(contract["stepDefinitions"])["validate-registration-endpoint"])
    dcr_011 = next(
        scenario for scenario in map(_as_object, _as_list(contract["scenarios"])) if scenario["id"] == "DCR-011"
    )
    dcr_011_steps = _as_list(_as_object(_as_list(dcr_011["cases"])[0])["steps"])
    final_dcr_011_step = _as_object(dcr_011_steps[-1])

    assert mapping["directLegacyImportSupported"] is False
    assert {_as_string(field["legacy"]) for field in fields} == EXPECTED_LEGACY_CONFIG_FIELDS
    assert {field["scope"] for field in fields} >= {
        "reusable-global",
        "shared-endpoint-selection",
        "dcr-specific",
    }
    assert [_as_string(deviation["id"]) for deviation in deviations] == [
        f"DCR-DEV-{number:03d}" for number in range(1, 10)
    ]
    assert {_as_string(deviation["classification"]) for deviation in deviations} <= {
        "preserved",
        "corrected",
        "strengthened",
        "deliberately-unsupported",
    }
    assert token["executableAuthMethods"] == [
        "tls_client_auth",
        "private_key_jwt",
        "client_secret_jwt",
        "client_secret_basic",
    ]
    assert token["unsupportedAuthMethods"] == ["client_secret_post"]
    assert _as_string(state["prerequisiteFailure"]).startswith("Skip every dependent")
    assert set(map(_as_string, _as_list(evidence["alwaysMasked"]))) >= {
        "signedRegistrationJose",
        "clientSecret",
        "registrationAccessToken",
        "clientCredentialsAccessToken",
        "Authorization",
        "tokenResponse",
    }
    assert "hostname must never weaken validation" in _as_string(response_validation["rule"])
    assert "absolute HTTPS URL" in _as_string(registration_step["behavior"])
    assert final_dcr_011_step["definition"] == "validate-registration-error"
    assert final_dcr_011_step["deviation"] == "DCR-DEV-005"
