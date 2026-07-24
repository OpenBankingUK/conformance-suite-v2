"""Unit tests for the CBPII legacy-FCS catalogue."""

from __future__ import annotations

import pytest

from conformance.catalogue import ImplementedEndpoint, TestPlanSpec, compile_test_plan
from conformance.catalogues.cbpii import CBPII_CATALOGUE_KEY, CBPII_CATALOGUE_VERSION, CBPII_FCS_CATALOGUE
from conformance.json_types import JsonValue


def _runtime_inputs() -> dict[str, JsonValue]:
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


def _spec(*, endpoints: tuple[ImplementedEndpoint, ...]) -> TestPlanSpec:
    return TestPlanSpec(
        schema_version="v1",
        catalogue_key=CBPII_CATALOGUE_KEY,
        security_profile="fapi1-advanced",
        implemented_endpoints=endpoints,
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
        "cbpii-consent-create-expiration-formats",
    ]


@pytest.mark.unit
def test_compile_includes_consent_dependency_for_funds_confirmation_endpoint() -> None:
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
def test_cbpii_compliance_scope_traces_legacy_31_and_40_manifests() -> None:
    flattened_scope = [scope for test_case in CBPII_FCS_CATALOGUE.test_cases for scope in test_case.compliance_scope]

    assert any("manifests/ob_3.1_cbpii_fca.json" in scope for scope in flattened_scope)
    assert any("manifests/ob_4.0_cbpii_fca.json" in scope for scope in flattened_scope)
    assert any("#OB-301-CBPII-000003" in scope for scope in flattened_scope)
    assert any("#OB-400-CBPII-000003" in scope for scope in flattened_scope)

    for test_case in CBPII_FCS_CATALOGUE.test_cases:
        assert any("ob_3.1_cbpii_fca.json" in scope for scope in test_case.compliance_scope)
        assert any("ob_4.0_cbpii_fca.json" in scope for scope in test_case.compliance_scope)
