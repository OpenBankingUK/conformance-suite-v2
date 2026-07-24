"""Unit tests for the CBPII legacy-FCS catalogue."""

from __future__ import annotations

import pytest

from conformance.catalogue import ImplementedEndpoint, TestPlanSpec, compile_test_plan
from conformance.catalogues.cbpii import CBPII_CATALOGUE_KEY, CBPII_CATALOGUE_VERSION, CBPII_FCS_CATALOGUE
from conformance.json_types import JsonValue


def _runtime_inputs() -> dict[str, JsonValue]:
    """Build the runtime inputs needed by the CBPII catalogue fixtures."""
    return {
        "resourceBaseUrl": "https://rs.example.com",
        "accessToken": "token-value",
        "debtorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
        "debtorAccountIdentification": "12345678901",
        "debtorAccountName": "Jane Doe",
        "fundsConfirmationConsentRequestRef": "fixtures/ob-funds-confirmation-consent.json",
        "fundsConfirmationRequestRef": "fixtures/ob-funds-confirmation.json",
        "uniqueCbpiiReference": "cbpii-reference-001",
        "invalidFundsConfirmationConsentId": "invalid-consent-id",
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
        "cbpii-consent-create-invalid-account-data",
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
    assert traces["accessToken"].provided is True
    assert traces["accessToken"].sensitive is True
    assert traces["accessToken"].value is None
    assert traces["fundsConfirmationConsentId"].required is False
    assert traces["fundsConfirmationConsentId"].provided is False
    assert traces["invalidFundsConfirmationConsentId"].provided is True


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
        "cbpii-consent-create-invalid-account-data",
    ]
    assert compiled_without_optional.traceability.generated_test_case_ids == (
        "cbpii-consent-create-core",
        "cbpii-consent-create-invalid-account-data",
    )
    assert compiled_without_optional.traceability.applicability_decisions[2].reason == (
        "required capability not selected: cbpii.funds-confirmation-consents.expiration-date-time-formats"
    )

    assert [case.test_case_id for case in compiled_with_optional.test_cases] == [
        "cbpii-consent-create-core",
        "cbpii-consent-create-invalid-account-data",
        "cbpii-consent-create-expiration-formats",
    ]
    assert [capability.capability_id for capability in compiled_with_optional.traceability.selected_capabilities] == [
        "cbpii.funds-confirmation-consents.create",
        "cbpii.funds-confirmation-consents.expiration-date-time-formats",
    ]
    assert compiled_with_optional.test_cases[2].compliance_scope == CBPII_FCS_CATALOGUE.test_cases[2].compliance_scope
    optional_scope = compiled_with_optional.test_cases[2].compliance_scope
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
