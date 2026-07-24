"""Unit tests for the legacy FCS-derived PIS payment catalogue."""

from __future__ import annotations

import pytest

from conformance.catalogue import (
    CatalogueKey,
    ImplementedEndpoint,
    TestPlanSpec,
    compile_test_plan,
)
from conformance.catalogues.pis import PIS_PAYMENT_CATALOGUE, PIS_PAYMENT_CATALOGUE_KEY
from conformance.json_types import JsonValue


def _spec(
    *,
    endpoint: ImplementedEndpoint,
    runtime_inputs: dict[str, JsonValue],
) -> TestPlanSpec:
    return TestPlanSpec(
        schema_version="v1",
        catalogue_key=PIS_PAYMENT_CATALOGUE.key,
        security_profile="fapi1-advanced",
        implemented_endpoints=(endpoint,),
        runtime_inputs=runtime_inputs,
    )


@pytest.mark.unit
def test_pis_payment_catalogue_key_and_version() -> None:
    assert PIS_PAYMENT_CATALOGUE.key == CatalogueKey(standard="open-banking", version="v4.0", api="pis")
    assert PIS_PAYMENT_CATALOGUE.key == PIS_PAYMENT_CATALOGUE_KEY
    assert PIS_PAYMENT_CATALOGUE.catalogue_version == "2026.07.legacy-fcs-pis.1"


@pytest.mark.unit
def test_pis_payment_catalogue_ids_are_duplicate_free() -> None:
    test_case_ids = [test_case.test_case_id for test_case in PIS_PAYMENT_CATALOGUE.test_cases]

    assert len(test_case_ids) == len(set(test_case_ids))
    for test_case in PIS_PAYMENT_CATALOGUE.test_cases:
        assertion_ids = [assertion.assertion_id for assertion in test_case.assertions]
        assert len(assertion_ids) == len(set(assertion_ids))


@pytest.mark.unit
def test_compile_selects_domestic_payment_case_and_includes_dependencies() -> None:
    plan = compile_test_plan(
        PIS_PAYMENT_CATALOGUE,
        _spec(
            endpoint=ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/pisp/domestic-payments",
                resource_group="DomesticPayments",
            ),
            runtime_inputs={
                "resourceBaseUrl": "https://rs.example.com",
                "accessTokenRef": "token://payments/access",
                "idempotencyKey": "idem-123",
                "domesticPaymentConsentId": "consent-123",
            },
        ),
    )

    assert [case.test_case_id for case in plan.test_cases] == [
        "pis-v4-domestic-payment-consent-create",
        "pis-v4-domestic-payment-consent-read-authorised",
        "pis-v4-domestic-payment-create",
    ]


@pytest.mark.unit
def test_compile_surfaces_runtime_inputs_from_selected_pis_cases() -> None:
    plan = compile_test_plan(
        PIS_PAYMENT_CATALOGUE,
        _spec(
            endpoint=ImplementedEndpoint(
                method="GET",
                path="/open-banking/v4.0/pisp/domestic-payment-consents/{domesticPaymentConsentId}/funds-confirmation",
                resource_group="DomesticPayments",
            ),
            runtime_inputs={
                "resourceBaseUrl": "https://rs.example.com",
                "accessTokenRef": "token://payments/access",
                "idempotencyKey": "idem-123",
                "domesticPaymentConsentId": "consent-123",
            },
        ),
    )

    input_ids = [entry.input_id for entry in plan.traceability.runtime_input_snapshot]
    assert input_ids == ["resourceBaseUrl", "accessTokenRef", "idempotencyKey", "domesticPaymentConsentId"]
    assert plan.traceability.runtime_input_snapshot[1].sensitive is True
    assert plan.traceability.runtime_input_snapshot[1].value is None


@pytest.mark.unit
def test_pis_payment_catalogue_compliance_scope_traces_to_legacy_manifests() -> None:
    for test_case in PIS_PAYMENT_CATALOGUE.test_cases:
        scope = test_case.compliance_scope

        assert any("manifests/ob_3.1_payment_fca.json" in item for item in scope)
        assert any("manifests/ob_4.0_payment_fca.json" in item for item in scope)
        assert any("OB-301-DOP-" in item or "OB-313-DOP-" in item or "OB-316-DOP-" in item for item in scope)
        assert any("OB-400-DOP-" in item or "OB-316-DOP-" in item for item in scope)
