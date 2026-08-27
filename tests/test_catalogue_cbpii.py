"""Unit tests for the CBPII legacy-FCS catalogue."""

from __future__ import annotations

import pytest

from conformance.catalogue import CatalogueError, ImplementedEndpoint, TestPlanSpec, compile_test_plan
from conformance.catalogues.cbpii import CBPII_CATALOGUE_KEY, CBPII_CATALOGUE_VERSION, CBPII_FCS_CATALOGUE
from conformance.json_types import JsonValue


def _runtime_inputs() -> dict[str, JsonValue]:
    """Build the runtime inputs needed by the CBPII catalogue fixtures."""
    return {
        "resourceBaseUrl": "https://rs.example.com",
        "debtorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
        "debtorAccountIdentification": "12345678901234",
        "debtorAccountName": "Model Bank Account",
    }


def _spec(
    *,
    endpoints: tuple[ImplementedEndpoint, ...],
    capability_ids: tuple[str, ...] = (),
) -> TestPlanSpec:
    """Build a CBPII plan spec for the supplied implemented endpoints.

    Args:
        endpoints: Implemented endpoint declarations to compile.
        capability_ids: Optional endpoint capabilities to apply to each endpoint.

    Returns:
        Test-plan spec for the CBPII catalogue fixture.
    """
    return TestPlanSpec(
        schema_version="v1",
        catalogue_key=CBPII_CATALOGUE_KEY,
        security_profile="fapi1-advanced",
        implemented_endpoints=tuple(
            ImplementedEndpoint(
                method=endpoint.method,
                path=endpoint.path,
                resource_group=endpoint.resource_group,
                operation_id=endpoint.operation_id,
                capability_ids=capability_ids if endpoint.capability_ids == () else endpoint.capability_ids,
            )
            for endpoint in endpoints
        ),
        runtime_inputs=_runtime_inputs(),
    )


@pytest.mark.unit
def test_cbpii_catalogue_key_and_version() -> None:
    assert CBPII_FCS_CATALOGUE.key == CBPII_CATALOGUE_KEY
    assert CBPII_CATALOGUE_KEY.standard == "open-banking"
    assert CBPII_CATALOGUE_KEY.version == "v4.0"
    assert CBPII_CATALOGUE_KEY.api == "cbpii"
    assert CBPII_FCS_CATALOGUE.catalogue_version == CBPII_CATALOGUE_VERSION


@pytest.mark.unit
def test_cbpii_catalogue_ids_are_unique() -> None:
    ids = [test_case.test_case_id for test_case in CBPII_FCS_CATALOGUE.test_cases]
    assert len(ids) == len(set(ids))


@pytest.mark.unit
def test_compile_selects_cbpii_cases_for_post_consents_endpoint() -> None:
    """Select only baseline CBPII consent-creation cases for endpoint-only plans."""
    spec = _spec(
        endpoints=(
            ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/cbpii/funds-confirmation-consents",
                resource_group="Funds Confirmation",
            ),
        ),
    )

    compiled = compile_test_plan(CBPII_FCS_CATALOGUE, spec)

    assert [case.test_case_id for case in compiled.test_cases] == [
        "cbpii-consent-create-core",
        "cbpii-consent-create-invalid-account-name",
        "cbpii-consent-create-invalid-account-identification",
        "cbpii-consent-create-invalid-scheme-name",
    ]
    assert [capability.capability_id for capability in compiled.traceability.selected_capabilities] == [
        "cbpii.funds-confirmation-consents.create",
    ]


@pytest.mark.unit
def test_compile_includes_consent_dependency_for_funds_confirmation_endpoint() -> None:
    """Keep the consent prerequisite selected for the funds-confirmation flow."""
    spec = _spec(
        endpoints=(
            ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/cbpii/funds-confirmations",
                resource_group="Funds Confirmation",
            ),
        ),
    )

    compiled = compile_test_plan(CBPII_FCS_CATALOGUE, spec)

    assert [case.test_case_id for case in compiled.test_cases] == [
        "cbpii-consent-create-core",
        "cbpii-funds-confirmation-create",
    ]
    decisions = {decision.test_case_id: decision for decision in compiled.traceability.applicability_decisions}
    assert decisions["cbpii-consent-create-core"].dependency_of == ("cbpii-funds-confirmation-create",)


@pytest.mark.unit
def test_compile_surfaces_cbpii_runtime_input_requirements() -> None:
    """Expose CBPII runtime inputs in traceability without leaking secrets."""
    spec = _spec(
        endpoints=(
            ImplementedEndpoint(
                method="DELETE",
                path="/open-banking/v4.0/cbpii/funds-confirmation-consents/{consentId}",
                resource_group="Funds Confirmation",
            ),
        ),
    )

    compiled = compile_test_plan(CBPII_FCS_CATALOGUE, spec)

    traces = {trace.input_id: trace for trace in compiled.traceability.runtime_input_snapshot}
    assert traces["accessToken"].provided is False
    assert traces["accessToken"].sensitive is True
    assert traces["accessToken"].value is None
    assert traces["debtorAccountSchemeName"].required is True
    assert traces["debtorAccountSchemeName"].provided is True
    assert traces["debtorAccountSchemeName"].value == "UK.OBIE.SortCodeAccountNumber"
    assert traces["debtorAccountIdentification"].required is True
    assert traces["debtorAccountIdentification"].provided is True
    assert traces["debtorAccountIdentification"].value is None
    assert traces["debtorAccountName"].required is True
    assert traces["debtorAccountName"].provided is True
    assert traces["debtorAccountName"].value == "Model Bank Account"
    assert traces["fundsConfirmationConsentId"].required is False
    assert traces["fundsConfirmationConsentId"].provided is False
    assert traces["invalidFundsConfirmationConsentId"].provided is False


@pytest.mark.unit
def test_compile_requires_cbpii_debtor_account_config() -> None:
    """CBPII consent creation requires participant/model-bank debtor account data."""
    spec = _spec(
        endpoints=(
            ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/cbpii/funds-confirmation-consents",
                resource_group="Funds Confirmation",
            ),
        ),
    )
    spec = TestPlanSpec(
        schema_version=spec.schema_version,
        catalogue_key=spec.catalogue_key,
        security_profile=spec.security_profile,
        implemented_endpoints=spec.implemented_endpoints,
        runtime_inputs={"resourceBaseUrl": "https://rs.example.com"},
    )

    with pytest.raises(CatalogueError, match="Required runtime input 'debtorAccountSchemeName' is missing"):
        compile_test_plan(CBPII_FCS_CATALOGUE, spec)


@pytest.mark.unit
def test_compile_requires_and_selects_cbpii_optional_expiration_capability() -> None:
    """Gate the optional expiration-format case behind its CBPII capability."""
    spec_without_optional = _spec(
        endpoints=(
            ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/cbpii/funds-confirmation-consents",
                resource_group="Funds Confirmation",
            ),
        ),
    )
    spec_with_optional = _spec(
        endpoints=(
            ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/cbpii/funds-confirmation-consents",
                resource_group="Funds Confirmation",
                capability_ids=("cbpii.funds-confirmation-consents.expiration-date-time-formats",),
            ),
        ),
    )

    compiled_without_optional = compile_test_plan(CBPII_FCS_CATALOGUE, spec_without_optional)
    compiled_with_optional = compile_test_plan(CBPII_FCS_CATALOGUE, spec_with_optional)

    assert [case.test_case_id for case in compiled_without_optional.test_cases] == [
        "cbpii-consent-create-core",
        "cbpii-consent-create-invalid-account-name",
        "cbpii-consent-create-invalid-account-identification",
        "cbpii-consent-create-invalid-scheme-name",
    ]
    assert compiled_without_optional.traceability.generated_test_case_ids == (
        "cbpii-consent-create-core",
        "cbpii-consent-create-invalid-account-name",
        "cbpii-consent-create-invalid-account-identification",
        "cbpii-consent-create-invalid-scheme-name",
    )
    optional_decisions = [
        decision
        for decision in compiled_without_optional.traceability.applicability_decisions
        if decision.test_case_id.startswith("cbpii-consent-create-expiration-")
    ]
    assert {decision.reason for decision in optional_decisions} == {
        "required capability not selected: cbpii.funds-confirmation-consents.expiration-date-time-formats"
    }

    assert [case.test_case_id for case in compiled_with_optional.test_cases] == [
        "cbpii-consent-create-core",
        "cbpii-consent-create-invalid-account-name",
        "cbpii-consent-create-invalid-account-identification",
        "cbpii-consent-create-invalid-scheme-name",
        "cbpii-consent-create-expiration-milliseconds-z",
        "cbpii-consent-create-expiration-milliseconds-offset",
        "cbpii-consent-create-expiration-seconds-z",
        "cbpii-consent-create-expiration-seconds-offset",
    ]
    assert [capability.capability_id for capability in compiled_with_optional.traceability.selected_capabilities] == [
        "cbpii.funds-confirmation-consents.create",
        "cbpii.funds-confirmation-consents.expiration-date-time-formats",
    ]
    compiled_by_id = {case.test_case_id: case for case in compiled_with_optional.test_cases}
    optional_scope = compiled_by_id["cbpii-consent-create-expiration-milliseconds-z"].compliance_scope
    assert any("legacy_manifest:manifests/ob_4.0_cbpii_fca.json" in scope for scope in optional_scope)


@pytest.mark.unit
def test_cbpii_compliance_scope_traces_legacy_31_and_40_manifests() -> None:
    flattened_scope = [scope for test_case in CBPII_FCS_CATALOGUE.test_cases for scope in test_case.compliance_scope]

    assert any("manifests/ob_3.1_cbpii_fca.json" in scope for scope in flattened_scope)
    assert any("manifests/ob_4.0_cbpii_fca.json" in scope for scope in flattened_scope)
    assert any("#OB-301-CBPII-000003" in scope for scope in flattened_scope)
    assert any("#OB-400-CBPII-000003" in scope for scope in flattened_scope)

    for test_case in CBPII_FCS_CATALOGUE.test_cases:
        assert any("ob_3.1_cbpii_fca.json" in scope for scope in test_case.compliance_scope)
        assert any("ob_4.0_cbpii_fca.json" in scope for scope in test_case.compliance_scope)


@pytest.mark.unit
def test_cbpii_catalogue_covers_every_legacy_manifest_script() -> None:
    """Ensure CBPII keeps parity with the legacy 3.1.11, 4.0.0, and 4.0.1 scripts."""
    manifest_scopes = {
        scope
        for test_case in CBPII_FCS_CATALOGUE.test_cases
        for scope in test_case.compliance_scope
        if scope.startswith("legacy_manifest:")
    }

    expected_scopes = {
        "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000001",
        "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000002",
        "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000003",
        "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000004",
        "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000005",
        "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000006",
        "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000007",
        "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000008",
        "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000009(delete)",
        "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000009(expiration)",
        "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000010",
        "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000011",
        "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000012",
        "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-312-CBPII-000100",
        "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000001",
        "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000002",
        "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000003",
        "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000004",
        "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000005",
        "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000006",
        "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000007",
        "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000008",
        "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000009(delete)",
        "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000009(expiration)",
        "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000010",
        "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000011",
        "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000012",
        "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-312-CBPII-000100",
    }

    assert manifest_scopes == expected_scopes


@pytest.mark.unit
def test_compile_selects_every_cbpii_legacy_consent_creation_variant() -> None:
    """Compile each distinct legacy consent creation request for full CBPII parity."""
    spec = _spec(
        endpoints=(
            ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/cbpii/funds-confirmation-consents",
                resource_group="Funds Confirmation",
                capability_ids=("cbpii.funds-confirmation-consents.expiration-date-time-formats",),
            ),
        ),
    )

    compiled = compile_test_plan(CBPII_FCS_CATALOGUE, spec)

    assert [request_step.step_id for case in compiled.test_cases for request_step in case.request_steps] == [
        "cbpii-consent-create-core-request",
        "cbpii-consent-create-invalid-account-name-request",
        "cbpii-consent-create-invalid-account-identification-request",
        "cbpii-consent-create-invalid-scheme-name-request",
        "cbpii-consent-create-expiration-milliseconds-z-request",
        "cbpii-consent-create-expiration-milliseconds-offset-request",
        "cbpii-consent-create-expiration-seconds-z-request",
        "cbpii-consent-create-expiration-seconds-offset-request",
    ]


@pytest.mark.unit
def test_cbpii_v4_read_and_funds_flows_keep_legacy_schema_and_header_assertions() -> None:
    """CBPII v4 resource flows enforce legacy FAPI, content-type, and schema checks."""
    spec = _spec(
        endpoints=(
            ImplementedEndpoint(
                method="GET",
                path="/open-banking/v4.0/cbpii/funds-confirmation-consents/{consentId}",
                resource_group="Funds Confirmation",
            ),
            ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/cbpii/funds-confirmations",
                resource_group="Funds Confirmation",
            ),
        ),
    )

    compiled = compile_test_plan(CBPII_FCS_CATALOGUE, spec)
    cases = {case.test_case_id: case for case in compiled.test_cases}

    for case_id in ("cbpii-consent-get-authorised", "cbpii-funds-confirmation-create"):
        assertions = cases[case_id].assertions
        assert any(assertion.kind == "response_schema" for assertion in assertions)
        assert any(
            assertion.kind == "header" and assertion.rule.get("name") == "x-fapi-interaction-id"
            for assertion in assertions
        )
        assert any(
            assertion.kind == "header" and assertion.rule.get("name") == "content-type" for assertion in assertions
        )
