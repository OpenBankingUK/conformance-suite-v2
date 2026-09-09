"""Unit tests for the legacy FCS-derived PIS payment catalogue."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from types import ModuleType
from typing import cast

import pytest

from conformance.catalogue import (
    CatalogueKey,
    ImplementedEndpoint,
    TestPlanSpec,
    compile_test_plan,
)
from conformance.json_types import JsonObject, JsonValue
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.unit


def _load_pis_catalogue_module() -> ModuleType:
    """Load the PIS catalogue module without importing unrelated catalogues.

    Returns:
        The imported PIS catalogue module object.
    """
    module_path = REPO_ROOT / "conformance" / "catalogues" / "pis.py"
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

PIS_RUNTIME_INPUTS: dict[str, JsonValue] = {
    "resourceBaseUrl": "https://rs.example.com",
    "pisCreditorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
    "pisCreditorAccountIdentification": "70000170000002",
    "pisCreditorAccountName": "Test creditor",
    "pisInternationalCreditorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
    "pisInternationalCreditorAccountIdentification": "70000170000003",
    "pisInternationalCreditorAccountName": "International test creditor",
    "pisInstructedAmountAmount": "1.00",
    "pisInstructedAmountCurrency": "GBP",
    "pisCurrencyOfTransfer": "USD",
    "pisRequestedExecutionDateTime": "2026-12-01T00:00:00+00:00",
    "pisFirstPaymentDateTime": "2026-12-01T00:00:00+00:00",
    "pisStandingOrderFrequencyType": "WEEK",
    "pisStandingOrderFrequencyPointInTime": "03",
}
"""Complete non-sensitive PIS runtime input fixture used by compiler tests."""

LEGACY_PIS_V40_SCRIPT_IDS = (
    "OB-400-DOP-100100",
    "OB-400-DOP-100110",
    "OB-400-DOP-100300",
    "OB-316-DOP-100310",
    "OB-400-DOP-100400",
    "OB-400-DOP-100500",
    "OB-400-DOP-100600",
    "OB-400-DOP-100700",
    "OB-400-DOP-100800",
    "OB-400-DOP-100810",
    "OB-400-DOP-100820",
    "OB-400-DOP-100900",
    "OB-400-DOP-101000",
    "OB-400-DOP-101100",
    "OB-400-DOP-101101",
    "OB-400-DOP-101200",
    "OB-400-DOP-101300",
    "OB-400-DOP-101400",
    "OB-400-DOP-101401",
    "OB-400-DOP-101500",
    "OB-400-DOP-101503",
    "OB-400-DOP-101600",
    "OB-400-DOP-101700",
    "OB-400-DOP-101800",
    "OB-400-DOP-101900",
    "OB-400-DOP-102000",
    "OB-400-DOP-102100",
    "OB-400-DOP-102200",
    "OB-400-DOP-102300",
)
"""Legacy v4.0 payment manifest script IDs expected in PIS compliance scope."""


def _spec(
    *,
    endpoint: ImplementedEndpoint,
    runtime_inputs: dict[str, JsonValue],
    specification_version: str | None = None,
) -> TestPlanSpec:
    merged_runtime_inputs = {**PIS_RUNTIME_INPUTS, **runtime_inputs}
    return TestPlanSpec(
        schema_version="v1",
        catalogue_key=PIS_PAYMENT_CATALOGUE.key,
        security_profile="fapi1-advanced",
        implemented_endpoints=(endpoint,),
        runtime_inputs=merged_runtime_inputs,
        specification_version=specification_version,
    )


def _legacy_script_ids(manifest_name: str) -> tuple[str, ...]:
    """Return PIS compliance-scope script ids for a legacy manifest.

    Args:
        manifest_name: Legacy manifest filename to extract scope entries for.

    Returns:
        Legacy script ids claimed by the PIS catalogue for ``manifest_name``.
    """
    prefix = f"legacy-fcs-script:{manifest_name}#"
    return tuple(
        scope.removeprefix(prefix)
        for test_case in PIS_PAYMENT_CATALOGUE.test_cases
        for scope in test_case.compliance_scope
        if scope.startswith(prefix)
    )


def test_pis_payment_catalogue_key_and_version() -> None:
    assert PIS_PAYMENT_CATALOGUE.key == CatalogueKey(standard="open-banking", version="v4.0", api="pis")
    assert PIS_PAYMENT_CATALOGUE.key == PIS_PAYMENT_CATALOGUE_KEY
    assert PIS_PAYMENT_CATALOGUE.catalogue_version == "2026.09.legacy-fcs-pis.2"


def test_pis_payment_catalogue_ids_are_duplicate_free() -> None:
    test_case_ids = [test_case.test_case_id for test_case in PIS_PAYMENT_CATALOGUE.test_cases]
    capability_ids = [capability.capability_id for capability in PIS_PAYMENT_CATALOGUE.capabilities]

    assert len(test_case_ids) == len(set(test_case_ids))
    assert len(capability_ids) == len(set(capability_ids))
    for test_case in PIS_PAYMENT_CATALOGUE.test_cases:
        assertion_ids = [assertion.assertion_id for assertion in test_case.assertions]
        assert len(assertion_ids) == len(set(assertion_ids))


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


def test_pis_domestic_consent_status_assertions_use_v4_response_shape() -> None:
    """Domestic consent status assertions match v4 response field casing and values."""
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

    cases = {case.test_case_id: case for case in plan.test_cases}
    create_assertion = cases["pis-v4-domestic-payment-consent-create"].assertions[0]
    authorised_read_assertion = cases["pis-v4-domestic-payment-consent-read-authorised"].assertions[0]
    create_all = cast("list[JsonObject]", create_assertion.rule["allOf"])
    authorised_read_all = cast("list[JsonObject]", authorised_read_assertion.rule["allOf"])

    assert any(
        match.get("JSON") == "Data.Status" and match.get("Value") == "AWAU"
        for expectation in create_all
        for match in cast("list[JsonObject]", expectation.get("matches", []))
    )
    assert any(
        match.get("JSON") == "Data.Status" and match.get("Value") == "AUTH"
        for expectation in authorised_read_all
        for match in cast("list[JsonObject]", expectation.get("matches", []))
    )


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
        "pisCreditorAccountSchemeName",
        "pisCreditorAccountIdentification",
        "pisCreditorAccountName",
        "pisInstructedAmountAmount",
        "pisInstructedAmountCurrency",
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

    assert [case.test_case_id for case in plan.test_cases] == [
        "pis-v4-domestic-payment-consent-create-without-authorisation",
        "pis-v4-domestic-payment-consent-create",
    ]
    assert plan.traceability.generated_test_case_ids == (
        "pis-v4-domestic-payment-consent-create-without-authorisation",
        "pis-v4-domestic-payment-consent-create",
    )
    assert plan.traceability.selected_capabilities == ()


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
        "pis-v4-domestic-payment-consent-create-without-authorisation",
        "pis-v4-domestic-payment-consent-reject-missing-signature-claim",
        "pis-v4-domestic-payment-consent-create",
        "pis-v4-domestic-payment-consent-reject-invalid-signature",
    ]
    assert plan.traceability.generated_test_case_ids == (
        "pis-v4-domestic-payment-consent-create-without-authorisation",
        "pis-v4-domestic-payment-consent-reject-missing-signature-claim",
        "pis-v4-domestic-payment-consent-create",
        "pis-v4-domestic-payment-consent-reject-invalid-signature",
    )
    assert [capability.capability_id for capability in plan.traceability.selected_capabilities] == [
        "pis.domestic-payment-consent.reject-invalid-detached-jws",
    ]
    assert plan.traceability.selected_capabilities[0].required is False


def test_compile_includes_distinct_legacy_pis_consent_parity_variants() -> None:
    """Domestic consent selection executes each v4 legacy row separately."""
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

    cases = {case.test_case_id: case for case in plan.test_cases}
    assert (
        "legacy-fcs-script:ob_4.0_payment_fca.json#OB-400-DOP-100100"
        in cases["pis-v4-domestic-payment-consent-create-without-authorisation"].compliance_scope
    )
    assert (
        "legacy-fcs-script:ob_4.0_payment_fca.json#OB-400-DOP-100300"
        in cases["pis-v4-domestic-payment-consent-create"].compliance_scope
    )
    assert (
        "legacy-fcs-script:ob_4.0_payment_fca.json#OB-400-DOP-100110"
        in cases["pis-v4-domestic-payment-consent-reject-missing-signature-claim"].compliance_scope
    )
    missing_claim_step = cases["pis-v4-domestic-payment-consent-reject-missing-signature-claim"].request_steps[0]
    assert missing_claim_step.detached_jws_omit_claims == ("iss",)


def test_compile_includes_distinct_legacy_scheduled_datetime_variants() -> None:
    """Domestic scheduled consent selection exposes both legacy datetime variants."""
    plan = compile_test_plan(
        PIS_PAYMENT_CATALOGUE,
        _spec(
            endpoint=ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/pisp/domestic-scheduled-payment-consents",
                resource_group="DomesticScheduledPayments",
            ),
            runtime_inputs={"resourceBaseUrl": "https://rs.example.com"},
        ),
    )

    assert [case.test_case_id for case in plan.test_cases] == [
        "pis-v4-domestic-scheduled-payment-consent-create",
        "pis-v4-domestic-scheduled-payment-consent-create-with-offset-datetime",
        "pis-v4-domestic-scheduled-payment-consent-create-with-utc-datetime",
        "pis-v4-domestic-scheduled-payment-consent-create-and-authorise",
    ]
    cases = {case.test_case_id: case for case in plan.test_cases}
    assert (
        "legacy-fcs-script:ob_4.0_payment_fca.json#OB-400-DOP-100810"
        in cases["pis-v4-domestic-scheduled-payment-consent-create-with-offset-datetime"].compliance_scope
    )
    assert (
        "legacy-fcs-script:ob_4.0_payment_fca.json#OB-400-DOP-100820"
        in cases["pis-v4-domestic-scheduled-payment-consent-create-with-utc-datetime"].compliance_scope
    )
    assert (
        "legacy-fcs-script:ob_4.0_payment_fca.json#OB-400-DOP-101000"
        in cases["pis-v4-domestic-scheduled-payment-consent-create-and-authorise"].compliance_scope
    )


def test_compile_v4_excludes_v31_only_standing_order_read_variants() -> None:
    """v4 standing-order reads compile only the pinned v4 legacy row."""
    plan = compile_test_plan(
        PIS_PAYMENT_CATALOGUE,
        _spec(
            endpoint=ImplementedEndpoint(
                method="GET",
                path="/open-banking/v4.0/pisp/domestic-standing-orders/{domesticStandingOrderId}",
                resource_group="DomesticStandingOrders",
            ),
            runtime_inputs={"resourceBaseUrl": "https://rs.example.com"},
        ),
    )

    assert [case.test_case_id for case in plan.test_cases] == [
        "pis-v4-domestic-standing-order-consent-create",
        "pis-v4-domestic-standing-order-consent-read",
        "pis-v4-domestic-standing-order-create",
        "pis-v4-domestic-standing-order-read",
    ]


def test_compile_v4_filters_pis_v3_only_variants_and_keeps_schema_checks() -> None:
    """v4 PIS execution excludes v3-only legacy cases and emits schema checks."""
    plan = compile_test_plan(
        PIS_PAYMENT_CATALOGUE,
        _spec(
            endpoint=ImplementedEndpoint(
                method="GET",
                path="/open-banking/v4.0/pisp/domestic-standing-orders/{domesticStandingOrderId}",
                resource_group="DomesticStandingOrders",
            ),
            runtime_inputs={"resourceBaseUrl": "https://rs.example.com"},
            specification_version="4.0.1",
        ),
    )

    selected_ids = set(plan.traceability.generated_test_case_ids)
    read_case = next(case for case in plan.test_cases if case.test_case_id == "pis-v4-domestic-standing-order-read")

    assert "pis-v4-domestic-standing-order-read-with-number-and-final-date" not in selected_ids
    assert "pis-v4-domestic-standing-order-read-with-final-amount-only" not in selected_ids
    assert read_case.assertions[0].kind == "legacy_fcs"
    assert read_case.assertions[0].rule["schemaDocument"] == "ob-read-write-v4.0-payment-initiation-openapi"


def test_pis_v4_legacy_consent_scripts_map_to_consent_endpoints() -> None:
    """Strict v4 PIS parity keeps consent scripts on consent endpoints."""
    scheduled_consent_case = next(
        case
        for case in PIS_PAYMENT_CATALOGUE.test_cases
        if case.test_case_id == "pis-v4-domestic-scheduled-payment-consent-create-and-authorise"
    )
    standing_order_rejection_case = next(
        case
        for case in PIS_PAYMENT_CATALOGUE.test_cases
        if case.test_case_id == "pis-v4-domestic-standing-order-consent-reject-invalid-frequency"
    )

    assert "legacy-fcs-script:ob_4.0_payment_fca.json#OB-400-DOP-101000" in scheduled_consent_case.compliance_scope
    assert scheduled_consent_case.request_steps[0].path == (
        "/open-banking/v4.0/pisp/domestic-scheduled-payment-consents"
    )
    assert (
        "legacy-fcs-script:ob_4.0_payment_fca.json#OB-400-DOP-101503" in standing_order_rejection_case.compliance_scope
    )
    assert standing_order_rejection_case.request_steps[0].path == (
        "/open-banking/v4.0/pisp/domestic-standing-order-consents"
    )


def test_pis_payment_catalogue_compliance_scope_traces_to_legacy_manifests() -> None:
    """Every public v4 case carries only pinned v4 legacy provenance."""
    for test_case in PIS_PAYMENT_CATALOGUE.test_cases:
        scope = test_case.compliance_scope

        assert any(
            "OpenBankingUK/conformance-suite@1908789b52e26e0a79fc5a565e411f771006c3ce/"
            "manifests/ob_4.0_payment_fca.json" in item
            for item in scope
        )
        v40_script_scope = [item for item in scope if item.startswith("legacy-fcs-script:ob_4.0_payment_fca.json#")]
        assert len(v40_script_scope) == 1
        assert any("OB-400-DOP-" in item or "OB-316-DOP-" in item for item in v40_script_scope)
        assert all("ob_3.1_payment_fca.json" not in item for item in scope)


def test_pis_payment_catalogue_covers_all_legacy_manifest_scripts() -> None:
    """The public v4 catalogue has exactly one case for every v4 legacy row."""
    v40_script_ids = _legacy_script_ids("ob_4.0_payment_fca.json")

    assert len(v40_script_ids) == len(set(v40_script_ids))
    assert v40_script_ids == LEGACY_PIS_V40_SCRIPT_IDS
