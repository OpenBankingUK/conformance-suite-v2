"""Unit tests for the legacy FCS-derived PIS payment catalogue."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest

from conformance.catalogue import (
    CatalogueKey,
    ImplementedEndpoint,
    TestPlanSpec,
    compile_test_plan,
)
from conformance.json_types import JsonValue


def _load_pis_catalogue_module() -> ModuleType:
    """Load the PIS catalogue module without importing unrelated catalogues.

    Returns:
        The imported PIS catalogue module object.
    """
    module_path = Path(__file__).resolve().parents[1] / "conformance" / "catalogues" / "pis.py"
    spec = spec_from_file_location("conformance.catalogues.pis", module_path)
    assert spec is not None
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


PIS_CATALOGUE_MODULE = _load_pis_catalogue_module()
"""Directly loaded PIS catalogue module used to avoid unrelated package imports."""

PIS_PAYMENT_CATALOGUE = PIS_CATALOGUE_MODULE.PIS_PAYMENT_CATALOGUE
"""Bundled PIS payment catalogue under test."""

PIS_PAYMENT_CATALOGUE_KEY = PIS_CATALOGUE_MODULE.PIS_PAYMENT_CATALOGUE_KEY
"""Canonical catalogue key for the bundled PIS payment catalogue."""


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
    capability_ids = [capability.capability_id for capability in PIS_PAYMENT_CATALOGUE.capabilities]

    assert len(test_case_ids) == len(set(test_case_ids))
    assert len(capability_ids) == len(set(capability_ids))
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
            runtime_inputs={"resourceBaseUrl": "https://rs.example.com"},
        ),
    )

    assert [case.test_case_id for case in plan.test_cases] == [
        "pis-v4-domestic-payment-consent-create",
        "pis-v4-domestic-payment-consent-read-authorised",
        "pis-v4-domestic-payment-create",
    ]
    assert plan.traceability.generated_test_case_ids == (
        "pis-v4-domestic-payment-consent-create",
        "pis-v4-domestic-payment-consent-read-authorised",
        "pis-v4-domestic-payment-create",
    )
    assert [capability.capability_id for capability in plan.traceability.selected_capabilities] == [
        "pis.domestic-payment-submission",
    ]
    assert plan.traceability.selected_capabilities[0].required is True


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
            runtime_inputs={"resourceBaseUrl": "https://rs.example.com"},
        ),
    )

    input_ids = [entry.input_id for entry in plan.traceability.runtime_input_snapshot]
    assert input_ids == [
        "resourceBaseUrl",
        "accessTokenRef",
        "domesticPaymentConsentId",
    ]
    snapshot = {entry.input_id: entry for entry in plan.traceability.runtime_input_snapshot}
    assert snapshot["resourceBaseUrl"].provided is True
    assert snapshot["accessTokenRef"].provided is False
    assert snapshot["accessTokenRef"].sensitive is True
    assert snapshot["accessTokenRef"].value is None
    assert snapshot["domesticPaymentConsentId"].provided is False
    assert "idempotencyKey" not in snapshot
    assert "xFapiCustomerIpAddress" not in snapshot


@pytest.mark.unit
def test_compile_excludes_optional_pis_case_when_capability_is_omitted() -> None:
    plan = compile_test_plan(
        PIS_PAYMENT_CATALOGUE,
        _spec(
            endpoint=ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/pisp/domestic-payment-consents",
                resource_group="DomesticPayments",
            ),
            runtime_inputs={"resourceBaseUrl": "https://rs.example.com"},
        ),
    )

    assert [case.test_case_id for case in plan.test_cases] == ["pis-v4-domestic-payment-consent-create"]
    assert plan.traceability.generated_test_case_ids == ("pis-v4-domestic-payment-consent-create",)
    assert plan.traceability.selected_capabilities == ()


@pytest.mark.unit
def test_compile_includes_optional_pis_case_when_capability_is_declared() -> None:
    plan = compile_test_plan(
        PIS_PAYMENT_CATALOGUE,
        _spec(
            endpoint=ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/pisp/domestic-payment-consents",
                resource_group="DomesticPayments",
                capability_ids=("pis.domestic-payment-consent.reject-invalid-detached-jws",),
            ),
            runtime_inputs={"resourceBaseUrl": "https://rs.example.com"},
        ),
    )

    assert [case.test_case_id for case in plan.test_cases] == [
        "pis-v4-domestic-payment-consent-create",
        "pis-v4-domestic-payment-consent-reject-invalid-signature",
    ]
    assert plan.traceability.generated_test_case_ids == (
        "pis-v4-domestic-payment-consent-create",
        "pis-v4-domestic-payment-consent-reject-invalid-signature",
    )
    assert [capability.capability_id for capability in plan.traceability.selected_capabilities] == [
        "pis.domestic-payment-consent.reject-invalid-detached-jws",
    ]
    assert plan.traceability.selected_capabilities[0].required is False


@pytest.mark.unit
def test_pis_payment_catalogue_compliance_scope_traces_to_legacy_manifests() -> None:
    for test_case in PIS_PAYMENT_CATALOGUE.test_cases:
        scope = test_case.compliance_scope

        assert any("manifests/ob_3.1_payment_fca.json" in item for item in scope)
        assert any("manifests/ob_4.0_payment_fca.json" in item for item in scope)
        assert any("OB-301-DOP-" in item or "OB-313-DOP-" in item or "OB-316-DOP-" in item for item in scope)
        assert any("OB-400-DOP-" in item or "OB-316-DOP-" in item for item in scope)
