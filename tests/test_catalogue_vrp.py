"""Unit tests for legacy VRP/cVRP catalogue coverage definitions."""

from __future__ import annotations

import pytest

from conformance.catalogue import CatalogueKey, ImplementedEndpoint, TestPlanSpec, compile_test_plan
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
            runtime_inputs={
                "resourceBaseUrl": "https://resource.example.com",
                "accessToken": "opaque-access-token",
            },
        ),
    )

    assert [test_case.test_case_id for test_case in compiled.test_cases] == [
        "vrp-consent-create-awaiting-authorisation",
        "vrp-payment-create-initial",
        "vrp-payment-create-repeated",
        "vrp-payment-get-repeated",
        "vrp-payment-get-details",
    ]


@pytest.mark.unit
def test_compile_selects_cvrp_cases_and_surfaces_runtime_requirements() -> None:
    compiled = compile_test_plan(
        CVRP_LEGACY_FCS_CATALOGUE,
        _spec(
            catalogue_key=CVRP_LEGACY_FCS_CATALOGUE.key,
            endpoints=(
                ImplementedEndpoint(
                    method="POST",
                    path="/domestic-vrp-consents/{consentId}/funds-confirmation",
                    resource_group="DomesticVRP",
                ),
            ),
            runtime_inputs={
                "resourceBaseUrl": "https://resource.example.com",
                "accessToken": "opaque-access-token",
            },
        ),
    )

    assert [test_case.test_case_id for test_case in compiled.test_cases] == [
        "cvrp-consent-create-awaiting-authorisation",
        "cvrp-consent-funds-confirmation",
    ]

    runtime_inputs_by_id = {item.input_id: item for item in compiled.traceability.runtime_input_snapshot}
    assert runtime_inputs_by_id["resourceBaseUrl"].value == "https://resource.example.com"
    assert runtime_inputs_by_id["accessToken"].provided is True
    assert runtime_inputs_by_id["accessToken"].value is None
    assert runtime_inputs_by_id["domesticVrpConsentId"].required is False
    assert runtime_inputs_by_id["domesticVrpConsentId"].provided is False


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
