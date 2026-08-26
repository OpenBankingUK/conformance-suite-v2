"""Unit tests for legacy VRP/cVRP catalogue coverage definitions."""

from __future__ import annotations

import pytest

from conformance.catalogue import CatalogueKey, ImplementedEndpoint, TestCatalogue, TestPlanSpec, compile_test_plan
from conformance.catalogues.vrp import CVRP_LEGACY_FCS_CATALOGUE, VRP_LEGACY_FCS_CATALOGUE
from conformance.json_types import JsonValue


def _spec(
    *,
    catalogue_key: CatalogueKey,
    endpoints: tuple[ImplementedEndpoint, ...],
    runtime_inputs: dict[str, JsonValue] | None = None,
) -> TestPlanSpec:
    return TestPlanSpec(
        schema_version="v1",
        catalogue_key=catalogue_key,
        security_profile="fapi1-advanced",
        implemented_endpoints=endpoints,
        runtime_inputs={} if runtime_inputs is None else runtime_inputs,
    )


@pytest.mark.unit
def test_vrp_and_cvrp_catalogues_have_expected_keys_and_version() -> None:
    assert VRP_LEGACY_FCS_CATALOGUE.key == CatalogueKey(standard="open-banking", version="v4.0", api="vrp")
    assert CVRP_LEGACY_FCS_CATALOGUE.key == CatalogueKey(standard="open-banking", version="v4.0", api="cvrp")
    assert VRP_LEGACY_FCS_CATALOGUE.catalogue_version == "2026.07.legacy-fcs-vrp-cvrp.1"
    assert CVRP_LEGACY_FCS_CATALOGUE.catalogue_version == "2026.07.legacy-fcs-vrp-cvrp.1"


@pytest.mark.unit
def test_vrp_and_cvrp_catalogues_do_not_duplicate_test_case_ids() -> None:
    vrp_ids = [test_case.test_case_id for test_case in VRP_LEGACY_FCS_CATALOGUE.test_cases]
    cvrp_ids = [test_case.test_case_id for test_case in CVRP_LEGACY_FCS_CATALOGUE.test_cases]

    assert len(vrp_ids) == len(set(vrp_ids))
    assert len(cvrp_ids) == len(set(cvrp_ids))


@pytest.mark.unit
def test_compile_selects_vrp_cases_for_endpoint_and_includes_dependencies() -> None:
    compiled = compile_test_plan(
        VRP_LEGACY_FCS_CATALOGUE,
        _spec(
            catalogue_key=VRP_LEGACY_FCS_CATALOGUE.key,
            endpoints=(
                ImplementedEndpoint(
                    method="GET",
                    path="/domestic-vrps/{vrpId}/payment-details",
                    resource_group="DomesticVRP",
                ),
            ),
            runtime_inputs={"resourceBaseUrl": "https://resource.example.com"},
        ),
    )

    assert [test_case.test_case_id for test_case in compiled.test_cases] == [
        "vrp-consent-create-awaiting-authorisation",
        "vrp-payment-create-initial",
        "vrp-payment-create-repeated",
        "vrp-payment-get-repeated",
        "vrp-payment-get-details",
    ]
    selected_capabilities = [
        (capability.capability_id, capability.required) for capability in compiled.traceability.selected_capabilities
    ]
    assert selected_capabilities == [
        ("vrp.core", True),
    ]


@pytest.mark.unit
def test_compile_selects_cvrp_cases_and_surfaces_required_capabilities() -> None:
    compiled = compile_test_plan(
        CVRP_LEGACY_FCS_CATALOGUE,
        _spec(
            catalogue_key=CVRP_LEGACY_FCS_CATALOGUE.key,
            endpoints=(
                ImplementedEndpoint(
                    method="POST",
                    path="/domestic-vrp-consents",
                    resource_group="DomesticVRP",
                ),
            ),
            runtime_inputs={"resourceBaseUrl": "https://resource.example.com"},
        ),
    )

    assert [test_case.test_case_id for test_case in compiled.test_cases] == [
        "cvrp-consent-create-awaiting-authorisation",
    ]
    selected_capabilities = [
        (capability.capability_id, capability.required) for capability in compiled.traceability.selected_capabilities
    ]
    assert selected_capabilities == [
        ("cvrp.core", True),
    ]

    runtime_inputs_by_id = {item.input_id: item for item in compiled.traceability.runtime_input_snapshot}
    assert runtime_inputs_by_id["resourceBaseUrl"].value == "https://resource.example.com"
    assert runtime_inputs_by_id["accessToken"].provided is False
    assert runtime_inputs_by_id["accessToken"].value is None


@pytest.mark.unit
@pytest.mark.parametrize(
    (
        "catalogue",
        "baseline_case_id",
        "optional_endpoint_path",
        "optional_case_id",
        "baseline_capability_id",
        "optional_capability_id",
    ),
    [
        (
            VRP_LEGACY_FCS_CATALOGUE,
            "vrp-consent-create-awaiting-authorisation",
            "/domestic-vrp-consents/{consentId}/funds-confirmation",
            "vrp-consent-funds-confirmation",
            "vrp.core",
            "vrp.funds-confirmation",
        ),
        (
            CVRP_LEGACY_FCS_CATALOGUE,
            "cvrp-consent-create-awaiting-authorisation",
            "/domestic-vrp-consents/{consentId}/funds-confirmation",
            "cvrp-consent-funds-confirmation",
            "cvrp.core",
            "cvrp.funds-confirmation",
        ),
    ],
)
def test_optional_funds_confirmation_capabilities_are_selected_only_when_declared(
    catalogue: TestCatalogue,
    baseline_case_id: str,
    optional_endpoint_path: str,
    optional_case_id: str,
    baseline_capability_id: str,
    optional_capability_id: str,
) -> None:
    """Optional VRP/cVRP funds-confirmation coverage stays out of endpoint-only plans.

    Args:
        catalogue: VRP or cVRP catalogue under test.
        baseline_case_id: Expected dependency case id for consent creation.
        optional_endpoint_path: Path for the implementation-dependent funds-confirmation endpoint.
        optional_case_id: Expected optional case id.
        baseline_capability_id: Required baseline capability id.
        optional_capability_id: Optional implementation-feature capability id.
    """
    implemented_endpoints = (
        ImplementedEndpoint(
            method="POST",
            path=optional_endpoint_path,
            resource_group="DomesticVRP",
        ),
    )

    compiled_without_optional = compile_test_plan(
        catalogue,
        _spec(
            catalogue_key=catalogue.key,
            endpoints=implemented_endpoints,
            runtime_inputs={"resourceBaseUrl": "https://resource.example.com"},
        ),
    )
    assert compiled_without_optional.test_cases == ()
    assert [
        (capability.capability_id, capability.required)
        for capability in compiled_without_optional.traceability.selected_capabilities
    ] == [(baseline_capability_id, True)]
    decisions_without_optional = {
        decision.test_case_id: decision for decision in compiled_without_optional.traceability.applicability_decisions
    }
    assert decisions_without_optional[optional_case_id].reason == (
        f"required capability not selected: {optional_capability_id}"
    )

    compiled_with_optional = compile_test_plan(
        catalogue,
        _spec(
            catalogue_key=catalogue.key,
            endpoints=(
                ImplementedEndpoint(
                    method="POST",
                    path=optional_endpoint_path,
                    resource_group="DomesticVRP",
                    capability_ids=(optional_capability_id,),
                ),
            ),
            runtime_inputs={"resourceBaseUrl": "https://resource.example.com"},
        ),
    )
    assert [test_case.test_case_id for test_case in compiled_with_optional.test_cases] == [
        baseline_case_id,
        optional_case_id,
    ]
    assert [
        (capability.capability_id, capability.required)
        for capability in compiled_with_optional.traceability.selected_capabilities
    ] == [
        (baseline_capability_id, True),
        (optional_capability_id, False),
    ]
    runtime_inputs_by_id = {item.input_id: item for item in compiled_with_optional.traceability.runtime_input_snapshot}
    assert runtime_inputs_by_id["domesticVrpConsentId"].provided is False
    assert runtime_inputs_by_id["domesticVrpConsentId"].value is None


@pytest.mark.unit
def test_vrp_and_cvrp_catalogues_keep_parity_safe_ids_and_provenance() -> None:
    """VRP and cVRP catalogue families keep matching suffixes and provenance roots."""

    vrp_ids = [test_case.test_case_id for test_case in VRP_LEGACY_FCS_CATALOGUE.test_cases]
    cvrp_ids = [test_case.test_case_id for test_case in CVRP_LEGACY_FCS_CATALOGUE.test_cases]

    assert [test_case_id.removeprefix("vrp-") for test_case_id in vrp_ids] == [
        test_case_id.removeprefix("cvrp-") for test_case_id in cvrp_ids
    ]
    vrp_31_source = "legacy-manifest:manifests/ob_3.1_variable_recurring_payments.json#"
    vrp_40_source = "legacy-manifest:manifests/ob_4.0_variable_recurring_payments.json#"
    cvrp_40_source = "legacy-manifest:manifests/cVRP_4.0_variable_recurring_payments.json#"
    assert all(
        any(scope.startswith(vrp_31_source) for scope in test_case.compliance_scope)
        and any(scope.startswith(vrp_40_source) for scope in test_case.compliance_scope)
        for test_case in VRP_LEGACY_FCS_CATALOGUE.test_cases
    )
    assert all(
        any(scope.startswith(cvrp_40_source) for scope in test_case.compliance_scope)
        for test_case in CVRP_LEGACY_FCS_CATALOGUE.test_cases
    )


@pytest.mark.unit
def test_catalogue_cases_keep_traceable_legacy_manifest_provenance() -> None:
    for test_case in VRP_LEGACY_FCS_CATALOGUE.test_cases:
        assert any(
            scope.startswith("legacy-manifest:manifests/ob_3.1_variable_recurring_payments.json#")
            for scope in test_case.compliance_scope
        )
        assert any(
            scope.startswith("legacy-manifest:manifests/ob_4.0_variable_recurring_payments.json#")
            for scope in test_case.compliance_scope
        )

    for test_case in CVRP_LEGACY_FCS_CATALOGUE.test_cases:
        assert any(
            scope.startswith("legacy-manifest:manifests/cVRP_4.0_variable_recurring_payments.json#")
            for scope in test_case.compliance_scope
        )
