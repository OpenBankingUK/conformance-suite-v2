"""Unit tests for legacy VRP/cVRP catalogue coverage definitions."""

from __future__ import annotations

import pytest

from conformance.catalogue import (
    CatalogueKey,
    CompiledTestPlan,
    ImplementedEndpoint,
    TestCatalogue,
    TestPlanSpec,
    compile_test_plan,
    compile_test_plan_document,
    parse_test_plan_document,
)
from conformance.catalogue_registry import supported_catalogues
from conformance.catalogues.vrp import CVRP_LEGACY_FCS_CATALOGUE, VRP_LEGACY_FCS_CATALOGUE
from conformance.json_types import JsonValue

VRP_RUNTIME_INPUT_DEFAULTS: dict[str, JsonValue] = {
    "vrpCreditorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
    "vrpCreditorAccountIdentification": "70000170000002",
    "vrpCreditorAccountName": "VRP creditor",
    "vrpInstructedAmountAmount": "1.00",
    "vrpInstructedAmountCurrency": "GBP",
    "vrpValidFromDateTime": "2026-08-27T00:00:00+00:00",
    "vrpValidToDateTime": "2026-09-27T00:00:00+00:00",
}
"""Default business data required by executable VRP/cVRP catalogue cases."""

LEGACY_VRP_V31_SCRIPT_IDS = (
    "OB-301-VRP-100100",
    "OB-301-VRP-100101",
    "OB-301-VRP-100600",
    "OB-301-VRP-100601",
    "OB-301-VRP-100610",
    "OB-301-VRP-100650",
    "OB-301-VRP-10670",
    "OB-301-VRP-100700",
    "OB-301-VRP-100701",
    "OB-301-VRP-101100",
    "OB-301-VRP-101200",
    "OB-301-VRP-102100",
    "OB-301-VRP-102150",
    "OB-301-VRP-102200",
)
"""Legacy v3.1 VRP manifest script IDs expected in VRP compliance scope."""

LEGACY_VRP_V40_SCRIPT_IDS = (
    "OB-400-VRP-100100",
    "OB-400-VRP-100600",
    "OB-400-VRP-100610",
    "OB-400-VRP-100650",
    "OB-400-VRP-10170",
    "OB-400-VRP-100700",
    "OB-400-VRP-101100",
    "OB-400-VRP-101200",
    "OB-400-VRP-102100",
    "OB-400-VRP-102150",
    "OB-400-VRP-102200",
)
"""Legacy v4.0 VRP manifest script IDs expected in VRP compliance scope."""


def _compiled_v4_vrp_plan() -> CompiledTestPlan:
    """Compile a full v4.0.1 VRP plan through the public Read/Write boundary.

    Returns:
        Compiled Read/Write plan containing v4 VRP executable cases.
    """
    document = parse_test_plan_document(
        {
            "schemaVersion": "1.0",
            "specification": {"family": "OBL_READ_WRITE", "version": "4.0.1", "profile": "FAPI1_ADVANCED"},
            "securityEnvironment": {"discoveryUrl": "https://auth.example.com/.well-known/openid-configuration"},
            "resourceGroups": [
                {
                    "id": "VRP",
                    "endpoints": [
                        {"method": "POST", "path": "/domestic-vrp-consents"},
                        {"method": "GET", "path": "/domestic-vrp-consents/{consentId}"},
                        {
                            "method": "POST",
                            "path": "/domestic-vrp-consents/{consentId}/funds-confirmation",
                            "capabilities": ["vrp.funds-confirmation"],
                        },
                        {"method": "DELETE", "path": "/domestic-vrp-consents/{consentId}"},
                        {"method": "POST", "path": "/domestic-vrps"},
                        {"method": "GET", "path": "/domestic-vrps/{vrpId}"},
                        {"method": "GET", "path": "/domestic-vrps/{vrpId}/payment-details"},
                    ],
                }
            ],
            "businessTestData": {
                "runtimeInputs": {"resourceBaseUrl": "https://resource.example.com"},
                "vrp": {
                    "creditorAccount": {
                        "schemeName": "UK.OBIE.SortCodeAccountNumber",
                        "identification": "70000170000002",
                        "name": "VRP creditor",
                    },
                    "instructedAmount": {"amount": "1.00", "currency": "GBP"},
                    "validFromDateTime": "2026-08-27T00:00:00+00:00",
                    "validToDateTime": "2026-09-27T00:00:00+00:00",
                },
            },
            "metadata": {},
        }
    )
    return compile_test_plan_document(document, supported_catalogues())


def _spec(
    *,
    catalogue_key: CatalogueKey,
    endpoints: tuple[ImplementedEndpoint, ...],
    runtime_inputs: dict[str, JsonValue] | None = None,
) -> TestPlanSpec:
    merged_runtime_inputs = dict(VRP_RUNTIME_INPUT_DEFAULTS)
    if runtime_inputs is not None:
        merged_runtime_inputs.update(runtime_inputs)
    return TestPlanSpec(
        schema_version="v1",
        catalogue_key=catalogue_key,
        security_profile="fapi1-advanced",
        implemented_endpoints=endpoints,
        runtime_inputs=merged_runtime_inputs,
    )


def _vrp_legacy_script_ids(manifest_path: str) -> tuple[str, ...]:
    """Return VRP compliance-scope script ids for a legacy manifest.

    Args:
        manifest_path: Legacy manifest path to extract scope entries for.

    Returns:
        Legacy script ids claimed by the VRP catalogue for ``manifest_path``.
    """
    prefix = f"legacy-manifest:{manifest_path}#"
    return tuple(
        scope.removeprefix(prefix)
        for test_case in VRP_LEGACY_FCS_CATALOGUE.test_cases
        for scope in test_case.compliance_scope
        if scope.startswith(prefix)
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
        "vrp-consent-create-awaiting-authorisation-v4",
        "vrp-payment-create-initial-v4",
        "vrp-payment-create-repeated-v4",
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
        "cvrp-consent-create-awaiting-authorisation-v4",
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
            "vrp-consent-create-awaiting-authorisation-v4",
            "/domestic-vrp-consents/{consentId}/funds-confirmation",
            "vrp-consent-funds-confirmation",
            "vrp.core",
            "vrp.funds-confirmation",
        ),
        (
            CVRP_LEGACY_FCS_CATALOGUE,
            "cvrp-consent-create-awaiting-authorisation-v4",
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

    assert len(vrp_ids) == len(set(vrp_ids))
    assert len(cvrp_ids) == len(set(cvrp_ids))
    vrp_31_source = "legacy-manifest:manifests/ob_3.1_variable_recurring_payments.json#"
    vrp_40_source = "legacy-manifest:manifests/ob_4.0_variable_recurring_payments.json#"
    cvrp_40_source = "legacy-manifest:manifests/cVRP_4.0_variable_recurring_payments.json#"
    assert any(
        scope.startswith(vrp_31_source)
        for test_case in VRP_LEGACY_FCS_CATALOGUE.test_cases
        for scope in test_case.compliance_scope
    )
    assert any(
        scope.startswith(vrp_40_source)
        for test_case in VRP_LEGACY_FCS_CATALOGUE.test_cases
        for scope in test_case.compliance_scope
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
            or scope.startswith("legacy-manifest:manifests/ob_4.0_variable_recurring_payments.json#")
            for scope in test_case.compliance_scope
        )

    for test_case in CVRP_LEGACY_FCS_CATALOGUE.test_cases:
        assert any(
            scope.startswith("legacy-manifest:manifests/cVRP_4.0_variable_recurring_payments.json#")
            for scope in test_case.compliance_scope
        )


@pytest.mark.unit
def test_vrp_catalogue_covers_all_legacy_manifest_scripts() -> None:
    v31_script_ids = _vrp_legacy_script_ids("manifests/ob_3.1_variable_recurring_payments.json")
    v40_script_ids = _vrp_legacy_script_ids("manifests/ob_4.0_variable_recurring_payments.json")

    assert len(v31_script_ids) == len(set(v31_script_ids))
    assert len(v40_script_ids) == len(set(v40_script_ids))
    assert set(v31_script_ids) == set(LEGACY_VRP_V31_SCRIPT_IDS)
    assert set(v40_script_ids) == set(LEGACY_VRP_V40_SCRIPT_IDS)


@pytest.mark.unit
def test_v4_vrp_compilation_maps_every_legacy_v4_script_without_v31_execution() -> None:
    """V4 Read/Write VRP execution maps old v4 scripts and excludes v3.1 variants."""
    compiled = _compiled_v4_vrp_plan()

    compiled_case_ids = [test_case.test_case_id for test_case in compiled.test_cases]
    compiled_v40_script_ids = [
        scope.rsplit("#", maxsplit=1)[1]
        for test_case in compiled.test_cases
        for scope in test_case.compliance_scope
        if scope.startswith("legacy-manifest:manifests/ob_4.0_variable_recurring_payments.json#")
    ]
    request_paths = [step.path for test_case in compiled.test_cases for step in test_case.request_steps]

    assert len(compiled_case_ids) == 11
    assert set(compiled_v40_script_ids) == set(LEGACY_VRP_V40_SCRIPT_IDS)
    assert all("-v31" not in case_id for case_id in compiled_case_ids)
    assert all("/v3.1/" not in path for path in request_paths)


@pytest.mark.unit
def test_v4_vrp_asserts_one_of_legacy_statuses_are_executable() -> None:
    """Legacy v4 VRP asserts_one_of entries compile into HTTP status assertions."""
    compiled = _compiled_v4_vrp_plan()
    cases_by_id = {test_case.test_case_id: test_case for test_case in compiled.test_cases}

    funds_confirmation_assertions = cases_by_id["vrp-consent-funds-confirmation"].assertions
    delete_after_delete_assertions = cases_by_id["vrp-consent-delete-after-delete"].assertions

    assert [assertion.rule for assertion in funds_confirmation_assertions if assertion.kind == "http_status"] == [
        {
            "expectedOneOf": [201],
            "legacyAssertionIds": ["OB3GLOAssertOn201"],
            "legacyAssertionSource": "manifests/assertions.json",
        }
    ]
    assert [assertion.rule for assertion in delete_after_delete_assertions if assertion.kind == "http_status"] == [
        {
            "expectedOneOf": [400, 204],
            "legacyAssertionIds": ["OB3GLOAssertOn400", "OB3GLOAssertOn204"],
            "legacyAssertionSource": "manifests/assertions.json",
        }
    ]


@pytest.mark.unit
def test_v4_vrp_json_response_schema_checks_are_executable() -> None:
    """Legacy v4 VRP schemaCheck entries compile into response-schema assertions."""
    compiled = _compiled_v4_vrp_plan()
    schema_cases = {
        test_case.test_case_id: [assertion for assertion in test_case.assertions if assertion.kind == "response_schema"]
        for test_case in compiled.test_cases
    }

    assert schema_cases["vrp-consent-create-awaiting-authorisation-v4"][0].rule == {
        "source": "bundled_openapi",
        "document": "ob-read-write-v4.0-vrp-openapi",
        "schemaRef": "#/components/schemas/OBDomesticVRPConsentResponse",
        "legacyAssertionIds": ["legacy-schema-check"],
    }
    assert schema_cases["vrp-consent-funds-confirmation"][0].rule == {
        "source": "bundled_openapi",
        "document": "ob-read-write-v4.0-vrp-openapi",
        "schemaRef": "#/components/schemas/OBVRPFundsConfirmationResponse",
        "legacyAssertionIds": ["legacy-schema-check"],
    }


@pytest.mark.unit
def test_vrp_catalogue_splits_legacy_v31_body_variant_scripts() -> None:
    """Legacy v3.1 pre/post-3.1.11 VRP scripts remain distinct cases."""
    script_to_case_id = {
        scope.rsplit("#", maxsplit=1)[1]: test_case.test_case_id
        for test_case in VRP_LEGACY_FCS_CATALOGUE.test_cases
        for scope in test_case.compliance_scope
        if scope.startswith("legacy-manifest:")
    }

    assert script_to_case_id["OB-301-VRP-100100"] == "vrp-consent-create-awaiting-authorisation-v31-pre-3111"
    assert script_to_case_id["OB-301-VRP-100101"] == "vrp-consent-create-awaiting-authorisation-v31-3111"
    assert script_to_case_id["OB-301-VRP-100600"] == "vrp-payment-create-initial-v31-pre-3111"
    assert script_to_case_id["OB-301-VRP-100601"] == "vrp-payment-create-initial-v31-3111"
    assert script_to_case_id["OB-301-VRP-100700"] == "vrp-payment-create-repeated-v31-pre-3111"
    assert script_to_case_id["OB-301-VRP-100701"] == "vrp-payment-create-repeated-v31-3111"


@pytest.mark.unit
def test_vrp_and_cvrp_legacy_script_ids_are_assigned_once() -> None:
    """Every legacy VRP/cVRP script id is owned by one catalogue case only."""
    for catalogue in (VRP_LEGACY_FCS_CATALOGUE, CVRP_LEGACY_FCS_CATALOGUE):
        script_ids = [
            scope.rsplit("#", maxsplit=1)[1]
            for test_case in catalogue.test_cases
            for scope in test_case.compliance_scope
            if scope.startswith("legacy-manifest:")
        ]

        assert len(script_ids) == len(set(script_ids))


@pytest.mark.unit
def test_canonical_vrp_business_data_supplies_runtime_inputs() -> None:
    """Canonical VRP business data maps into executable catalogue runtime inputs."""
    document = parse_test_plan_document(
        {
            "schemaVersion": "1.0",
            "specification": {"family": "OBL_READ_WRITE", "version": "4.0.1", "profile": "FAPI1_ADVANCED"},
            "securityEnvironment": {"discoveryUrl": "https://auth.example.com/.well-known/openid-configuration"},
            "resourceGroups": [
                {
                    "id": "VRP",
                    "endpoints": [{"method": "POST", "path": "/domestic-vrps"}],
                }
            ],
            "businessTestData": {
                "runtimeInputs": {"resourceBaseUrl": "https://resource.example.com"},
                "vrp": {
                    "creditorAccount": {
                        "schemeName": "UK.OBIE.SortCodeAccountNumber",
                        "identification": "70000170000002",
                        "name": "VRP creditor",
                    },
                    "instructedAmount": {"amount": "1.00", "currency": "GBP"},
                    "validFromDateTime": "2026-08-27T00:00:00+00:00",
                    "validToDateTime": "2026-09-27T00:00:00+00:00",
                },
            },
            "metadata": {},
        }
    )

    compiled = compile_test_plan_document(document, supported_catalogues())

    assert compiled.catalogue_key.api == "read-write"
    case_ids = [test_case.test_case_id for test_case in compiled.test_cases]
    assert case_ids == [
        "vrp-consent-create-awaiting-authorisation-v4",
        "vrp-payment-create-initial-v4",
        "vrp-payment-create-repeated-v4",
    ]
    assert all("-v31" not in case_id for case_id in case_ids)
    provided_input_ids = {item.input_id for item in compiled.traceability.runtime_input_snapshot if item.provided}
    assert {
        "resourceBaseUrl",
        "vrpCreditorAccountSchemeName",
        "vrpCreditorAccountIdentification",
        "vrpCreditorAccountName",
        "vrpInstructedAmountAmount",
        "vrpInstructedAmountCurrency",
        "vrpValidFromDateTime",
        "vrpValidToDateTime",
    }.issubset(provided_input_ids)
