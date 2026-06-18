from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

import conformance.suite_catalog as suite_catalog
from conformance.json_types import JsonValue
from conformance.manifest import (
    FormBody,
    GeneratedRequestObject,
    HeaderAssertion,
    HttpStatusAssertion,
    JsonBody,
    JsonFieldAssertion,
    ManifestStep,
    ObErrorCodeAssertion,
    PsuAuthorizationStep,
    ResponseSchemaAssertion,
    ResponseSignatureAssertion,
    TokenEndpointAuthPolicy,
)
from conformance.model_bank_config import SuiteApiFamily, SuiteName, SuiteSelection, SuiteSpecVersion
from conformance.suite_catalog import SuiteCatalogError, resolve_suite

REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_FCS_MANIFEST_PATH = (
    REPO_ROOT / "conformance" / "standards" / "ob_read_write" / "v4_0" / "legacy-ob_4.0_accounts_transactions_fca.json"
)
LEGACY_FCS_PARITY_TARGET_IDS = ("OB-400-TRA-105000", "OB-400-TRA-105200")
"""Legacy v4.0 FCS scripts currently targeted by the bundled parity guard."""
LEGACY_TRANSACTIONS_BASIC_DETAIL_FIELDS = (
    "TransactionInformation",
    "Balance",
    "MerchantDetails",
    "CreditorAgent",
    "CreditorAccount",
    "DebtorAgent",
    "DebtorAccount",
    "UltimateCreditor",
    "UltimateDebtor",
)
"""Detail fields that ReadTransactionsBasic responses must omit on all items."""
PIS_LEGACY_BENCHMARK_INVENTORY_PATH = (
    REPO_ROOT / "docs" / "requirements" / "suite-coverage" / "v4-pis-prior-fcs-inventory.json"
)
"""Generated parity inventory for the v4 PIS legacy benchmark coverage mapping."""
MANDATORY_PIS_LEGACY_IDS = (
    "OB-400-DOP-100100",
    "OB-400-DOP-100110",
    "OB-400-DOP-100300",
    "OB-316-DOP-100310",
    "OB-400-DOP-100400",
    "OB-400-DOP-100500",
    "OB-400-DOP-100600",
    "OB-400-DOP-100700",
)
"""Legacy PIS ids that must remain mandatory in the bundled v4 benchmark."""
ALL_PIS_LEGACY_IDS = MANDATORY_PIS_LEGACY_IDS + (
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
"""All legacy PIS ids that must be represented in the v4 benchmark manifest."""
PIS_V4_PAYMENT_OPENAPI_DOCUMENT = "ob-read-write-v4.0-payment-initiation-openapi"
"""Bundled v4.0 payment-initiation OpenAPI document id used by PIS schema assertions."""
PIS_LEGACY_SCHEMA_REFS_BY_ID = {
    "OB-400-DOP-100100": "#/components/schemas/OBWriteDomesticConsentResponse5",
    "OB-400-DOP-100300": "#/components/schemas/OBWriteDomesticConsentResponse5",
    "OB-316-DOP-100310": "#/components/schemas/OBErrorResponse1",
    "OB-400-DOP-100400": "#/components/schemas/OBWriteDomesticConsentResponse5",
    "OB-400-DOP-100500": "#/components/schemas/OBWriteFundsConfirmationResponse1",
    "OB-400-DOP-100600": "#/components/schemas/OBWriteDomesticResponse5",
    "OB-400-DOP-100700": "#/components/schemas/OBWriteDomesticResponse5",
    "OB-400-DOP-100800": "#/components/schemas/OBWriteDomesticScheduledConsentResponse5",
    "OB-400-DOP-100810": "#/components/schemas/OBWriteDomesticScheduledConsentResponse5",
    "OB-400-DOP-100820": "#/components/schemas/OBWriteDomesticScheduledConsentResponse5",
    "OB-400-DOP-100900": "#/components/schemas/OBWriteDomesticScheduledConsentResponse5",
    "OB-400-DOP-101000": "#/components/schemas/OBWriteDomesticScheduledConsentResponse5",
    "OB-400-DOP-101100": "#/components/schemas/OBWriteDomesticScheduledConsentResponse5",
    "OB-400-DOP-101101": "#/components/schemas/OBWriteDomesticScheduledResponse5",
    "OB-400-DOP-101200": "#/components/schemas/OBWriteDomesticStandingOrderConsentResponse6",
    "OB-400-DOP-101300": "#/components/schemas/OBWriteDomesticStandingOrderConsentResponse6",
    "OB-400-DOP-101400": "#/components/schemas/OBErrorResponse1",
    "OB-400-DOP-101401": "#/components/schemas/OBWriteDomesticStandingOrderResponse6",
    "OB-400-DOP-101500": "#/components/schemas/OBWriteDomesticStandingOrderResponse6",
    "OB-400-DOP-101503": "#/components/schemas/OBErrorResponse1",
    "OB-400-DOP-101600": "#/components/schemas/OBWriteInternationalConsentResponse6",
    "OB-400-DOP-101700": "#/components/schemas/OBWriteInternationalConsentResponse6",
    "OB-400-DOP-101800": "#/components/schemas/OBWriteInternationalResponse5",
    "OB-400-DOP-101900": "#/components/schemas/OBWriteInternationalResponse5",
    "OB-400-DOP-102000": "#/components/schemas/OBWriteInternationalScheduledConsentResponse6",
    "OB-400-DOP-102100": "#/components/schemas/OBWriteInternationalScheduledConsentResponse6",
    "OB-400-DOP-102200": "#/components/schemas/OBWriteInternationalScheduledResponse6",
    "OB-400-DOP-102300": "#/components/schemas/OBWriteInternationalScheduledResponse6",
}
"""Legacy PIS rows with schemaCheck=true mapped to bundled v4.0 PIS schema refs."""
PIS_LEGACY_VALIDATE_SIGNATURE_IDS = (
    "OB-400-DOP-100100",
    "OB-400-DOP-100300",
    "OB-400-DOP-100400",
    "OB-400-DOP-100500",
    "OB-400-DOP-100600",
    "OB-400-DOP-100700",
    "OB-400-DOP-100900",
    "OB-400-DOP-101100",
    "OB-400-DOP-101101",
    "OB-400-DOP-101200",
    "OB-400-DOP-101300",
    "OB-400-DOP-101400",
    "OB-400-DOP-101401",
    "OB-400-DOP-101500",
    "OB-400-DOP-101503",
    "OB-400-DOP-101700",
    "OB-400-DOP-101900",
    "OB-400-DOP-102100",
    "OB-400-DOP-102200",
    "OB-400-DOP-102300",
)
"""Legacy PIS rows whose validateSignature flag maps to response_signature assertions."""


def _legacy_fcs_script_index() -> dict[str, dict[str, JsonValue]]:
    """Load the legacy v4.0 AIS FCS manifest scripts keyed by test id.

    Returns:
        Legacy script declarations keyed by their Open Banking test id.
    """
    raw_manifest = cast(dict[str, JsonValue], json.loads(LEGACY_FCS_MANIFEST_PATH.read_text(encoding="utf-8")))
    scripts = cast(list[JsonValue], raw_manifest["scripts"])
    indexed_scripts: dict[str, dict[str, JsonValue]] = {}
    for raw_script in scripts:
        script = cast(dict[str, JsonValue], raw_script)
        indexed_scripts[cast(str, script["id"])] = script
    return indexed_scripts


def _selection(
    spec_version: SuiteSpecVersion = "v4.0",
    suite_name: str = "discovery-jwks",
    api: SuiteApiFamily = "ais",
) -> SuiteSelection:
    """Build a suite-selection value for bundled catalog tests.

    Args:
        spec_version: Catalog spec version to resolve.
        suite_name: Bundled suite identifier under test.
        api: API family to include in the normalized suite-selection key.

    Returns:
        Suite selection object matching the requested catalog entry.
    """
    return SuiteSelection(
        standard="ob-read-write",
        spec_version=spec_version,
        profile="fapi1-advanced",
        suite=cast(SuiteName, suite_name),
        api=api,
    )


def _has_all_items_field_assertion(step: ManifestStep, *, path: str, field: str) -> bool:
    """Return whether a step asserts that every array item contains a field.

    Args:
        step: Manifest step whose assertions should be inspected.
        path: JSON path targeted by the array-field assertion.
        field: Field name expected on every array item.

    Returns:
        ``True`` when the step contains the matching ``all_items_have_field``
        assertion, otherwise ``False``.
    """
    return any(
        isinstance(assertion, JsonFieldAssertion)
        and assertion.path == path
        and assertion.rule == "all_items_have_field"
        and assertion.field == field
        for assertion in step.assertions
    )


def _has_response_schema_assertion(step: ManifestStep, *, document: str, schema_ref: str) -> bool:
    """Return whether a step has a schema-backed assertion for a given schema ref.

    Args:
        step: Manifest step whose assertions should be inspected.
        document: Allowlisted bundled document identifier expected on the
            assertion.
        schema_ref: JSON Pointer expected on the assertion.

    Returns:
        ``True`` when the step contains a matching ``response_schema``
        assertion, otherwise ``False``.
    """
    return any(
        isinstance(assertion, ResponseSchemaAssertion)
        and assertion.source == "bundled_openapi"
        and assertion.document == document
        and assertion.schema_ref == schema_ref
        for assertion in step.assertions
    )


@pytest.mark.unit
@pytest.mark.parametrize("spec_version", ["v3.1.11", "v4.0", "v4.0.1"])
def test_resolve_suite_returns_bundled_manifest_for_supported_versions(spec_version: SuiteSpecVersion) -> None:
    resolved = resolve_suite(_selection(spec_version))

    assert resolved.metadata.standard == "ob-read-write"
    assert resolved.metadata.spec_version == spec_version
    assert resolved.metadata.profile == "fapi1-advanced"
    assert resolved.metadata.api == "ais"
    assert resolved.metadata.suite == "discovery-jwks"
    assert resolved.metadata.catalog_id == f"ob-read-write/{spec_version}/fapi1-advanced/discovery-jwks"
    assert "smoke" in resolved.metadata.label
    assert "not full Read/Write API certification coverage" in resolved.metadata.description

    manifest = resolved.manifest
    assert manifest.schema_version == "v1"
    assert manifest.name == resolved.metadata.label
    assert [step.id for step in manifest.steps] == ["openid-discovery", "jwks-fetch"]
    assert [step.mandatory for step in manifest.steps] == [True, True]

    discovery_step = cast(ManifestStep, manifest.steps[0])
    jwks_step = cast(ManifestStep, manifest.steps[1])
    assert discovery_step.request.url == "${config.discoveryUrl}"
    assert jwks_step.request.url == "${steps.openid-discovery.response.body.jwks_uri}"


@pytest.mark.unit
def test_list_supported_suites_is_deterministic() -> None:
    first = suite_catalog.list_supported_suites()
    second = suite_catalog.list_supported_suites()

    assert first == second
    assert len(first) == 25
    assert {(metadata.spec_version, metadata.api, metadata.suite) for metadata in first} >= {
        ("v3.1.11", "ais", "discovery-jwks"),
        ("v3.1.11", "ais", "psu-auth-starter"),
        ("v4.0", "ais", "ais-certification-baseline"),
        ("v4.0", "ais", "ais-fcs-legacy-benchmark"),
        ("v4.0", "ais", "ais-certification-slice"),
        ("v4.0", "pis", "discovery-jwks"),
        ("v4.0", "cbpii", "psu-auth-starter"),
        ("v4.0.1", "ais", "ais-certification-baseline"),
        ("v4.0.1", "ais", "ais-certification-slice"),
        ("v4.0.1", "vrp", "discovery-jwks"),
        ("v4.0.1", "pis", "psu-auth-starter"),
        ("v4.0", "pis", "pis-domestic-payment-starter"),
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("spec_version", "suite_name", "api", "expected_bundle_ids", "expected_step_ids"),
    [
        ("v4.0.1", "discovery-jwks", "ais", ("discovery-no-auth",), set()),
        ("v4.0.1", "psu-auth-starter", "pis", ("starter-manual",), set()),
        (
            "v4.0",
            "pis-domestic-payment-starter",
            "pis",
            ("domestic-payment-consent-token", "domestic-payment-flow"),
            {
                "domestic-payment-consent",
                "domestic-payment-consent-read-back",
                "funds-confirmation",
                "domestic-payment-submit",
                "domestic-payment-read-back",
            },
        ),
        (
            "v4.0.1",
            "ais-certification-slice",
            "ais",
            ("ais-protected-resource",),
            {"accounts-list", "account-balances", "account-transactions"},
        ),
        (
            "v4.0.1",
            "ais-certification-baseline",
            "ais",
            ("ais-protected-resource",),
            {"accounts-list", "account-detail", "transactions-list", "statements-list"},
        ),
        (
            "v4.0",
            "ais-certification-baseline",
            "ais",
            ("ais-protected-resource", "ais-transactions-basic"),
            {"account-transactions-basic", "transactions-list-basic"},
        ),
        (
            "v4.0",
            "ais-fcs-legacy-benchmark",
            "ais",
            ("ais-protected-resource", "ais-transactions-basic"),
            {"OB-400-TRA-105200"},
        ),
    ],
)
def test_bundled_suites_expose_explicit_auth_inventory(
    spec_version: SuiteSpecVersion,
    suite_name: str,
    api: SuiteApiFamily,
    expected_bundle_ids: tuple[str, ...],
    expected_step_ids: set[str],
) -> None:
    """Bundled suites should parse explicit auth inventories for each category."""
    resolved = resolve_suite(_selection(spec_version, suite_name=suite_name, api=api))
    inventory = resolved.manifest.auth_inventory

    assert inventory is not None
    assert tuple(bundle.id for bundle in inventory.bundles) == expected_bundle_ids
    assert expected_step_ids.issubset({req.step_id for req in inventory.step_requirements})


@pytest.mark.unit
@pytest.mark.parametrize("spec_version", ["v3.1.11", "v4.0", "v4.0.1"])
def test_resolve_psu_auth_starter_returns_bundled_manifest_for_supported_versions(
    spec_version: SuiteSpecVersion,
) -> None:
    resolved = resolve_suite(_selection(spec_version, suite_name="psu-auth-starter"))

    assert resolved.metadata.standard == "ob-read-write"
    assert resolved.metadata.spec_version == spec_version
    assert resolved.metadata.profile == "fapi1-advanced"
    assert resolved.metadata.api == "ais"
    assert resolved.metadata.suite == "psu-auth-starter"
    assert resolved.metadata.catalog_id == f"ob-read-write/{spec_version}/fapi1-advanced/psu-auth-starter"
    assert "PSU auth starter" in resolved.metadata.label
    assert "not full Read/Write API certification coverage" in resolved.metadata.description

    manifest = resolved.manifest
    assert manifest.schema_version == "v1"
    assert manifest.certification_coverage == "partial"
    assert manifest.name == resolved.metadata.label
    assert [step.id for step in manifest.steps] == ["openid-discovery", "jwks-fetch", "psu-authorization"]
    assert [step.mandatory for step in manifest.steps] == [True, True, True]

    discovery_step = cast(ManifestStep, manifest.steps[0])
    jwks_step = cast(ManifestStep, manifest.steps[1])
    psu_step = cast(PsuAuthorizationStep, manifest.steps[2])

    assert discovery_step.request.url == "${config.discoveryUrl}"
    assert jwks_step.request.url == "${steps.openid-discovery.response.body.jwks_uri}"
    assert psu_step.mode == "manual"
    assert psu_step.authorization_endpoint == "${steps.openid-discovery.response.body.authorization_endpoint}"
    assert psu_step.client_id == "${config.oauth.clientId}"
    assert psu_step.redirect_uri == "${config.oauth.redirectUri}"
    assert psu_step.scope == "openid accounts"
    assert psu_step.request_object == GeneratedRequestObject(
        source="fapi-signing",
        audience="${steps.openid-discovery.response.body.issuer}",
        openbanking_intent_id="${config.oauth.openBankingIntentId}",
    )

    discovery_assertions = discovery_step.assertions
    jwks_assertions = jwks_step.assertions
    assert any(
        isinstance(assertion, HeaderAssertion)
        and assertion.name == "content-type"
        and assertion.rule == "contains"
        and assertion.value == "application/json"
        for assertion in discovery_assertions
    )
    assert any(
        isinstance(assertion, JsonFieldAssertion)
        and assertion.path == "response_types_supported"
        and assertion.rule == "non_empty_array"
        for assertion in discovery_assertions
    )
    assert any(
        isinstance(assertion, JsonFieldAssertion)
        and assertion.path == "keys"
        and assertion.rule == "all_items_have_field"
        and assertion.field == "kty"
        for assertion in jwks_assertions
    )


@pytest.mark.unit
@pytest.mark.parametrize("spec_version", ["v3.1.11", "v4.0", "v4.0.1"])
def test_psu_auth_starter_manifest_is_partial_coverage(spec_version: SuiteSpecVersion) -> None:
    resolved = resolve_suite(_selection(spec_version, suite_name="psu-auth-starter"))

    assert resolved.manifest.certification_coverage == "partial"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("api", "scope"),
    [
        ("pis", "openid payments"),
        ("cbpii", "openid fundsconfirmations"),
        ("vrp", "openid payments"),
    ],
)
@pytest.mark.parametrize("spec_version", ["v4.0", "v4.0.1"])
def test_resolve_non_ais_psu_auth_starter_uses_api_specific_scope(
    api: SuiteApiFamily,
    scope: str,
    spec_version: SuiteSpecVersion,
) -> None:
    resolved = resolve_suite(_selection(spec_version, suite_name="psu-auth-starter", api=api))

    assert resolved.metadata.api == api
    if spec_version == "v4.0":
        assert resolved.metadata.catalog_id == f"ob-read-write/{spec_version}/fapi1-advanced/{api}/psu-auth-starter"

    manifest = resolved.manifest
    assert manifest.schema_version == "v1"
    assert manifest.certification_coverage == "partial"
    assert [step.id for step in manifest.steps] == ["openid-discovery", "jwks-fetch", "psu-authorization"]
    psu_step = cast(PsuAuthorizationStep, manifest.steps[2])
    assert psu_step.scope == scope


@pytest.mark.unit
def test_resolve_pis_domestic_payment_starter_returns_bundled_manifest() -> None:
    """The bundled PIS domestic-payment starter should resolve to the new manifest."""
    resolved = resolve_suite(_selection("v4.0", suite_name="pis-domestic-payment-starter", api="pis"))

    assert resolved.metadata.standard == "ob-read-write"
    assert resolved.metadata.spec_version == "v4.0"
    assert resolved.metadata.profile == "fapi1-advanced"
    assert resolved.metadata.api == "pis"
    assert resolved.metadata.suite == "pis-domestic-payment-starter"
    assert resolved.metadata.catalog_id == "ob-read-write/v4.0/fapi1-advanced/pis/pis-domestic-payment-starter"
    assert resolved.metadata.manifest_resource == "ob-read-write-v4.0-fapi1-advanced-pis-domestic-payment-starter.json"
    assert "sandbox/model-bank-only" in resolved.metadata.description
    assert "partial certification coverage" in resolved.metadata.description

    manifest = resolved.manifest
    assert manifest.schema_version == "v1"
    assert manifest.certification_coverage == "partial"
    assert manifest.name == resolved.metadata.label
    assert [step.id for step in manifest.steps] == [
        "openid-discovery",
        "jwks-fetch",
        "client-credentials-token",
        "domestic-payment-consent",
        "psu-authorization",
        "token-exchange",
        "domestic-payment-consent-read-back",
        "funds-confirmation",
        "domestic-payment-submit",
        "domestic-payment-read-back",
        "domestic-payment-consent-missing-detached-jws-header",
        "psu-authorization-signing-negative",
    ]
    assert [step.mandatory for step in manifest.steps] == [True] * 10 + [False, False]
    assert [step.optional for step in manifest.steps] == [False] * 10 + [True, True]

    test_value_profiles = manifest.test_value_profiles
    assert test_value_profiles is not None
    assert test_value_profiles.default_profile_id == "ozone-demo"
    assert [profile.id for profile in test_value_profiles.profiles] == ["ozone-demo", "synthetic-fallback"]
    assert test_value_profiles.profiles[0].generated_keys == {
        "instructionIdentification": "per-run-compact-uuid",
        "endToEndIdentification": "per-run-compact-uuid",
        "consentIdempotencyKey": "per-run-uuid",
        "negativeConsentIdempotencyKey": "per-run-uuid",
        "submitIdempotencyKey": "per-run-uuid",
    }

    consent_token_step = cast(ManifestStep, manifest.steps[2])
    consent_step = cast(ManifestStep, manifest.steps[3])
    psu_step = cast(PsuAuthorizationStep, manifest.steps[4])
    token_exchange_step = cast(ManifestStep, manifest.steps[5])
    consent_read_back_step = cast(ManifestStep, manifest.steps[6])
    funds_confirmation_step = cast(ManifestStep, manifest.steps[7])
    payment_submit_step = cast(ManifestStep, manifest.steps[8])
    payment_read_back_step = cast(ManifestStep, manifest.steps[9])
    negative_consent_step = cast(ManifestStep, manifest.steps[10])
    negative_psu_step = cast(PsuAuthorizationStep, manifest.steps[11])

    assert consent_step.request.detached_jws is not None
    assert consent_step.request.url == (
        "${config.oauth.resourceBaseUrl}/open-banking/v4.0/pisp/domestic-payment-consents"
    )
    assert consent_step.request.headers is not None
    assert payment_submit_step.request.headers is not None
    assert negative_consent_step.request.headers is not None
    assert consent_step.request.headers.get("x-idempotency-key") == "${testValues.consentIdempotencyKey}"
    assert payment_submit_step.request.headers.get("x-idempotency-key") == "${testValues.submitIdempotencyKey}"
    assert (
        negative_consent_step.request.headers.get("x-idempotency-key") == "${testValues.negativeConsentIdempotencyKey}"
    )
    assert negative_consent_step.request.url == (
        "${config.oauth.resourceBaseUrl}/open-banking/v4.0/pisp/domestic-payment-consents"
    )
    assert psu_step.request_object == GeneratedRequestObject(
        source="fapi-signing",
        audience="${steps.openid-discovery.response.body.issuer}",
        openbanking_intent_id="${steps.domestic-payment-consent.response.body.Data.ConsentId}",
    )
    assert consent_token_step.token_endpoint_auth_policy == TokenEndpointAuthPolicy(source="fapi-signing")
    assert token_exchange_step.token_endpoint_auth_policy == TokenEndpointAuthPolicy(source="fapi-signing")
    assert consent_read_back_step.request.url == (
        "${config.oauth.resourceBaseUrl}/open-banking/v4.0/pisp/domestic-payment-consents/"
        "${steps.domestic-payment-consent.response.body.Data.ConsentId}"
    )
    assert consent_read_back_step.request.headers == {
        "Accept": "application/json",
        "Authorization": "Bearer ${steps.client-credentials-token.response.body.access_token}",
    }
    assert funds_confirmation_step.request.url == (
        "${config.oauth.resourceBaseUrl}/open-banking/v4.0/pisp/domestic-payment-consents/"
        "${steps.domestic-payment-consent.response.body.Data.ConsentId}/funds-confirmation"
    )
    assert funds_confirmation_step.request.headers == {
        "Accept": "application/json",
        "Authorization": "Bearer ${steps.token-exchange.response.body.access_token}",
    }, "funds-confirmation must use the PSU-authorized token-exchange token (domestic-payment-flow bundle)"
    assert payment_submit_step.request.url == (
        "${config.oauth.resourceBaseUrl}/open-banking/v4.0/pisp/domestic-payments"
    )
    assert payment_read_back_step.request.url == (
        "${config.oauth.resourceBaseUrl}/open-banking/v4.0/pisp/domestic-payments/"
        "${steps.domestic-payment-submit.response.body.Data.DomesticPaymentId}"
    )
    assert payment_read_back_step.request.headers == {
        "Accept": "application/json",
        "Authorization": "Bearer ${steps.client-credentials-token.response.body.access_token}",
    }
    assert negative_consent_step.signing_negative_case == "omit-detached-jws-header"
    assert negative_consent_step.request.detached_jws is not None
    assert negative_psu_step.signing_negative_case == "omit-request-object-signature-claim"

    # Consent body: RemittanceInformation.Unstructured must be an array (OB PIS schema requirement)
    assert consent_step.request.body is not None
    assert isinstance(consent_step.request.body, JsonBody)
    consent_body = cast(dict[str, Any], consent_step.request.body.value)
    consent_initiation = cast(dict[str, Any], cast(dict[str, Any], consent_body["Data"])["Initiation"])
    consent_remittance = consent_initiation["RemittanceInformation"]
    assert isinstance(consent_remittance["Unstructured"], list), (
        "RemittanceInformation.Unstructured must be an array in the consent body"
    )

    # Submit body: same array shape
    assert payment_submit_step.request.body is not None
    assert isinstance(payment_submit_step.request.body, JsonBody)
    submit_body = cast(dict[str, Any], payment_submit_step.request.body.value)
    submit_initiation = cast(dict[str, Any], cast(dict[str, Any], submit_body["Data"])["Initiation"])
    submit_remittance = submit_initiation["RemittanceInformation"]
    assert isinstance(submit_remittance["Unstructured"], list), (
        "RemittanceInformation.Unstructured must be an array in the submit body"
    )


@pytest.mark.unit
def test_resolve_v4_ais_certification_baseline_returns_bundled_manifest() -> None:
    resolved = resolve_suite(_selection("v4.0", suite_name="ais-certification-baseline"))

    assert resolved.metadata.standard == "ob-read-write"
    assert resolved.metadata.spec_version == "v4.0"
    assert resolved.metadata.profile == "fapi1-advanced"
    assert resolved.metadata.api == "ais"
    assert resolved.metadata.suite == "ais-certification-baseline"
    assert resolved.metadata.catalog_id == "ob-read-write/v4.0/fapi1-advanced/ais-certification-baseline"
    assert resolved.metadata.manifest_resource == "ob-read-write-v4.0-fapi1-advanced-ais-certification-baseline.json"
    assert "partial coverage" in resolved.metadata.description

    manifest = resolved.manifest
    assert manifest.schema_version == "v1"
    assert manifest.certification_coverage == "partial"
    assert manifest.name == resolved.metadata.label
    mandatory_step_ids = [
        "openid-discovery",
        "jwks-fetch",
        "client-credentials-token",
        "account-access-consent",
        "psu-authorization",
        "token-exchange",
        "accounts-list",
        "account-detail",
        "account-balances",
        "account-access-consent-transactions-basic",
        "psu-authorization-transactions-basic",
        "token-exchange-transactions-basic",
        "account-transactions-basic",
        "account-transactions",
        "transactions-list",
    ]
    optional_step_ids = [
        "transactions-list-basic",
        "balances-list",
        "account-beneficiaries",
        "beneficiaries-list",
        "account-direct-debits",
        "direct-debits-list",
        "account-offers",
        "offers-list",
        "account-party",
        "account-parties",
        "party-list",
        "account-product",
        "products-list",
        "account-scheduled-payments",
        "scheduled-payments-list",
        "account-standing-orders",
        "standing-orders-list",
        "statements-list",
    ]
    assert [step.id for step in manifest.steps] == mandatory_step_ids + optional_step_ids
    assert [step.id for step in manifest.steps if step.mandatory] == mandatory_step_ids
    assert [step.id for step in manifest.steps if step.optional] == optional_step_ids

    consent_token_step = cast(ManifestStep, manifest.steps[2])
    consent_step = cast(ManifestStep, manifest.steps[3])
    psu_step = cast(PsuAuthorizationStep, manifest.steps[4])
    token_exchange_step = cast(ManifestStep, manifest.steps[5])
    accounts_step = cast(ManifestStep, manifest.steps[6])
    account_detail_step = cast(ManifestStep, manifest.steps[7])
    balances_step = cast(ManifestStep, manifest.steps[8])
    basic_consent_step = cast(ManifestStep, manifest.steps[9])
    basic_psu_step = cast(PsuAuthorizationStep, manifest.steps[10])
    basic_token_exchange_step = cast(ManifestStep, manifest.steps[11])
    transactions_basic_step = cast(ManifestStep, manifest.steps[12])
    transactions_step = cast(ManifestStep, manifest.steps[13])
    transactions_list_step = cast(ManifestStep, manifest.steps[14])
    transactions_list_basic_step = cast(ManifestStep, manifest.steps[15])

    assert psu_step.request_object == GeneratedRequestObject(
        source="fapi-signing",
        audience="${steps.openid-discovery.response.body.issuer}",
        openbanking_intent_id="${steps.account-access-consent.response.body.Data.ConsentId}",
    )
    assert consent_token_step.request.url == "${steps.openid-discovery.response.body.token_endpoint}"
    assert consent_token_step.token_endpoint_auth_policy == TokenEndpointAuthPolicy(source="fapi-signing")
    assert isinstance(consent_token_step.request.body, FormBody)
    assert dict(consent_token_step.request.body.fields) == {
        "grant_type": "client_credentials",
        "client_id": "${config.oauth.clientId}",
        "scope": "accounts",
    }
    assert token_exchange_step.request.url == "${steps.openid-discovery.response.body.token_endpoint}"
    assert token_exchange_step.token_endpoint_auth_policy == TokenEndpointAuthPolicy(source="fapi-signing")
    assert isinstance(token_exchange_step.request.body, FormBody)
    assert dict(token_exchange_step.request.body.fields) == {
        "grant_type": "authorization_code",
        "code": "${steps.psu-authorization.response.body.code}",
        "redirect_uri": "${config.oauth.redirectUri}",
        "client_id": "${config.oauth.clientId}",
    }
    assert consent_step.request.url == "${config.oauth.resourceBaseUrl}/open-banking/v4.0/aisp/account-access-consents"
    assert consent_step.request.headers == {
        "Accept": "application/json",
        "Authorization": "Bearer ${steps.client-credentials-token.response.body.access_token}",
    }
    consent_body = consent_step.request.body
    assert isinstance(consent_body, JsonBody)
    assert isinstance(consent_body.value, dict)
    consent_data = consent_body.value["Data"]
    assert isinstance(consent_data, dict)
    permissions = consent_data["Permissions"]
    assert isinstance(permissions, list)
    assert "ReadTransactionsDetail" in permissions
    basic_consent_body = basic_consent_step.request.body
    assert isinstance(basic_consent_body, JsonBody)
    assert isinstance(basic_consent_body.value, dict)
    basic_consent_data = basic_consent_body.value["Data"]
    assert isinstance(basic_consent_data, dict)
    basic_permissions = basic_consent_data["Permissions"]
    assert isinstance(basic_permissions, list)
    assert basic_permissions == [
        "ReadAccountsBasic",
        "ReadTransactionsBasic",
        "ReadTransactionsDebits",
        "ReadTransactionsCredits",
    ]
    assert "ReadTransactionsDetail" not in basic_permissions
    assert basic_psu_step.request_object == GeneratedRequestObject(
        source="fapi-signing",
        audience="${steps.openid-discovery.response.body.issuer}",
        openbanking_intent_id="${steps.account-access-consent-transactions-basic.response.body.Data.ConsentId}",
    )
    assert isinstance(basic_token_exchange_step.request.body, FormBody)
    assert dict(basic_token_exchange_step.request.body.fields)["code"] == (
        "${steps.psu-authorization-transactions-basic.response.body.code}"
    )
    assert accounts_step.request.url == "${config.oauth.resourceBaseUrl}/open-banking/v4.0/aisp/accounts"
    assert account_detail_step.request.url == (
        "${config.oauth.resourceBaseUrl}/open-banking/v4.0/aisp/accounts/"
        "${steps.accounts-list.response.body.Data.Account.0.AccountId}"
    )
    assert balances_step.request.url == (
        "${config.oauth.resourceBaseUrl}/open-banking/v4.0/aisp/accounts/"
        "${steps.accounts-list.response.body.Data.Account.0.AccountId}/balances"
    )
    assert balances_step.request.headers == {
        "Accept": "application/json",
        "Authorization": "Bearer ${steps.token-exchange.response.body.access_token}",
    }
    assert transactions_step.request.url == (
        "${config.oauth.resourceBaseUrl}/open-banking/v4.0/aisp/accounts/"
        "${steps.accounts-list.response.body.Data.Account.0.AccountId}/transactions"
    )
    assert transactions_step.request.headers == {
        "Accept": "application/json",
        "Authorization": "Bearer ${steps.token-exchange.response.body.access_token}",
    }
    assert transactions_list_step.request.url == "${config.oauth.resourceBaseUrl}/open-banking/v4.0/aisp/transactions"
    assert transactions_basic_step.request.headers == {
        "Accept": "application/json",
        "Authorization": "Bearer ${steps.token-exchange-transactions-basic.response.body.access_token}",
    }
    assert transactions_list_basic_step.request.headers == {
        "Accept": "application/json",
        "Authorization": "Bearer ${steps.token-exchange-transactions-basic.response.body.access_token}",
    }

    consent_assertions = consent_step.assertions
    accounts_assertions = accounts_step.assertions
    account_detail_assertions = account_detail_step.assertions
    balances_assertions = balances_step.assertions
    transactions_basic_assertions = transactions_basic_step.assertions
    transactions_assertions = transactions_step.assertions
    transactions_list_assertions = transactions_list_step.assertions
    transactions_list_basic_assertions = transactions_list_basic_step.assertions
    assert any(
        isinstance(assertion, JsonFieldAssertion) and assertion.path == "Data.ConsentId" and assertion.rule == "string"
        for assertion in consent_assertions
    )
    assert any(
        isinstance(assertion, JsonFieldAssertion) and assertion.path == "Data.Permissions" and assertion.rule == "array"
        for assertion in consent_assertions
    )
    assert any(
        isinstance(assertion, HeaderAssertion)
        and assertion.name == "x-fapi-interaction-id"
        and assertion.rule == "present"
        for assertion in consent_assertions
    )
    assert any(
        isinstance(assertion, JsonFieldAssertion) and assertion.path == "Data.Account" and assertion.rule == "array"
        for assertion in accounts_assertions
    )
    assert _has_all_items_field_assertion(accounts_step, path="Data.Account", field="AccountId")
    assert not _has_all_items_field_assertion(accounts_step, path="Data.Account", field="Status")
    assert any(
        isinstance(assertion, JsonFieldAssertion) and assertion.path == "Data.Account" and assertion.rule == "array"
        for assertion in account_detail_assertions
    )
    assert _has_all_items_field_assertion(account_detail_step, path="Data.Account", field="AccountId")
    assert not _has_all_items_field_assertion(account_detail_step, path="Data.Account", field="Status")
    assert any(
        isinstance(assertion, JsonFieldAssertion) and assertion.path == "Data.Balance" and assertion.rule == "array"
        for assertion in balances_assertions
    )
    assert any(
        isinstance(assertion, JsonFieldAssertion) and assertion.path == "Data.Transaction" and assertion.rule == "array"
        for assertion in transactions_assertions
    )
    assert _has_all_items_field_assertion(transactions_step, path="Data.Transaction", field="Status")
    assert _has_all_items_field_assertion(transactions_step, path="Data.Transaction", field="BookingDateTime")
    assert any(
        isinstance(assertion, JsonFieldAssertion) and assertion.path == "Data.Transaction" and assertion.rule == "array"
        for assertion in transactions_list_assertions
    )
    assert _has_all_items_field_assertion(transactions_list_step, path="Data.Transaction", field="Status")
    assert _has_all_items_field_assertion(transactions_list_step, path="Data.Transaction", field="BookingDateTime")
    for transaction_basic_assertions in (transactions_basic_assertions, transactions_list_basic_assertions):
        assert any(
            isinstance(assertion, JsonFieldAssertion)
            and assertion.path == "Data.Transaction"
            and assertion.rule == "array"
            for assertion in transaction_basic_assertions
        )
        all_items_absent_assertions = [
            assertion
            for assertion in transaction_basic_assertions
            if isinstance(assertion, JsonFieldAssertion)
            and assertion.path == "Data.Transaction"
            and assertion.rule == "all_items_absent_field"
        ]
        assert len(all_items_absent_assertions) == len(LEGACY_TRANSACTIONS_BASIC_DETAIL_FIELDS)
        assert {assertion.field for assertion in all_items_absent_assertions} == set(
            LEGACY_TRANSACTIONS_BASIC_DETAIL_FIELDS
        )


@pytest.mark.unit
def test_v4_ais_certification_baseline_mandatory_resource_steps_have_schema_assertions() -> None:
    """Schema-backed assertions are present on mandatory AIS resource steps and coverage stays partial.

    Verifies that the five mandatory v4.0 AIS resource steps each carry exactly
    one ``response_schema`` assertion referencing the bundled v4.0 OpenAPI
    document, and that the suite's ``certificationCoverage`` has not been
    promoted to ``complete``.
    """
    resolved = resolve_suite(_selection("v4.0", suite_name="ais-certification-baseline"))
    manifest = resolved.manifest

    assert manifest.certification_coverage == "partial"

    steps_by_id = {step.id: cast(ManifestStep, step) for step in manifest.steps}
    _doc = "ob-read-write-v4.0-account-info-openapi"

    assert _has_response_schema_assertion(
        steps_by_id["accounts-list"],
        document=_doc,
        schema_ref="#/components/schemas/OBReadAccount6",
    ), "accounts-list is missing OBReadAccount6 response_schema assertion"
    assert _has_response_schema_assertion(
        steps_by_id["account-detail"],
        document=_doc,
        schema_ref="#/components/schemas/OBReadAccount6",
    ), "account-detail is missing OBReadAccount6 response_schema assertion"
    assert _has_response_schema_assertion(
        steps_by_id["account-balances"],
        document=_doc,
        schema_ref="#/components/schemas/OBReadBalance1",
    ), "account-balances is missing OBReadBalance1 response_schema assertion"
    assert _has_response_schema_assertion(
        steps_by_id["account-transactions"],
        document=_doc,
        schema_ref="#/components/schemas/OBReadTransaction6",
    ), "account-transactions is missing OBReadTransaction6 response_schema assertion"
    assert _has_response_schema_assertion(
        steps_by_id["transactions-list"],
        document=_doc,
        schema_ref="#/components/schemas/OBReadTransaction6",
    ), "transactions-list is missing OBReadTransaction6 response_schema assertion"


@pytest.mark.unit
def test_v4_0_1_ais_certification_baseline_uses_versioned_schema_source() -> None:
    """v4.0.1 baseline uses its own bundled OpenAPI source and stays partial."""
    resolved = resolve_suite(_selection("v4.0.1", suite_name="ais-certification-baseline"))
    manifest = resolved.manifest

    assert manifest.certification_coverage == "partial"

    steps_by_id = {step.id: cast(ManifestStep, step) for step in manifest.steps}
    document = "ob-read-write-v4.0.1-account-info-openapi"
    schema_expectations = [
        ("accounts-list", "#/components/schemas/OBReadAccount6"),
        ("account-detail", "#/components/schemas/OBReadAccount6"),
        ("account-balances", "#/components/schemas/OBReadBalance1"),
        ("account-transactions", "#/components/schemas/OBReadTransaction6"),
        ("transactions-list", "#/components/schemas/OBReadTransaction6"),
    ]

    for step_id, schema_ref in schema_expectations:
        assert _has_response_schema_assertion(
            steps_by_id[step_id],
            document=document,
            schema_ref=schema_ref,
        ), f"{step_id} is missing {schema_ref} response_schema assertion for {document}"


@pytest.mark.unit
@pytest.mark.parametrize("spec_version", ["v4.0", "v4.0.1"])
def test_resolve_ais_certification_baseline_returns_bundled_manifest_for_supported_versions(
    spec_version: SuiteSpecVersion,
) -> None:
    resolved = resolve_suite(_selection(spec_version, suite_name="ais-certification-baseline"))

    assert resolved.metadata.standard == "ob-read-write"
    assert resolved.metadata.spec_version == spec_version
    assert resolved.metadata.profile == "fapi1-advanced"
    assert resolved.metadata.api == "ais"
    assert resolved.metadata.suite == "ais-certification-baseline"
    assert resolved.metadata.manifest_resource == (
        f"ob-read-write-{spec_version}-fapi1-advanced-ais-certification-baseline.json"
    )
    assert resolved.manifest.schema_version == "v1"
    assert resolved.manifest.certification_coverage == "partial"


@pytest.mark.unit
def test_resolve_v4_ais_fcs_legacy_benchmark_returns_bundled_manifest() -> None:
    resolved = resolve_suite(_selection("v4.0", suite_name="ais-fcs-legacy-benchmark"))

    assert resolved.metadata.standard == "ob-read-write"
    assert resolved.metadata.spec_version == "v4.0"
    assert resolved.metadata.profile == "fapi1-advanced"
    assert resolved.metadata.api == "ais"
    assert resolved.metadata.suite == "ais-fcs-legacy-benchmark"
    assert resolved.metadata.catalog_id == "ob-read-write/v4.0/fapi1-advanced/ais-fcs-legacy-benchmark"
    assert resolved.metadata.manifest_resource == "ob-read-write-v4.0-fapi1-advanced-ais-fcs-legacy-benchmark.json"
    assert "Legacy FCS benchmark parity" in resolved.metadata.description
    assert "partial coverage" in resolved.metadata.description

    manifest = resolved.manifest
    mandatory_step_ids = [
        "openid-discovery",
        "jwks-fetch",
        "client-credentials-token",
        "account-access-consent",
        "psu-authorization",
        "token-exchange",
        "OB-400-ACC-100400",
        "OB-400-ACC-100200",
        "OB-400-BAL-101200",
        "account-access-consent-transactions-basic",
        "psu-authorization-transactions-basic",
        "token-exchange-transactions-basic",
        "OB-400-TRA-105000",
        "OB-400-TRA-105100",
        "OB-400-TRA-105110",
        "OB-400-TRA-105120",
    ]
    optional_step_ids = [
        "OB-400-BAL-101300",
        "OB-400-BEN-101800",
        "OB-400-BEN-101900",
        "OB-400-DIR-102300",
        "OB-400-DIR-102400",
        "OB-400-OFF-102600",
        "OB-400-PAR-102900",
        "OB-400-PAR-102901",
        "OB-400-PRO-103200",
        "OB-400-SCP-103500",
        "OB-400-STO-103800",
        "OB-400-TRA-105200",
    ]
    assert manifest.schema_version == "v1"
    assert manifest.certification_coverage == "partial"
    assert manifest.name == resolved.metadata.label
    assert [step.id for step in manifest.steps] == mandatory_step_ids + optional_step_ids
    assert [step.id for step in manifest.steps if step.mandatory] == mandatory_step_ids
    assert [step.id for step in manifest.steps if step.optional] == optional_step_ids

    consent_token_step = cast(ManifestStep, manifest.steps[2])
    consent_step = cast(ManifestStep, manifest.steps[3])
    psu_step = cast(PsuAuthorizationStep, manifest.steps[4])
    accounts_step = cast(ManifestStep, manifest.steps[6])
    basic_consent_step = cast(ManifestStep, manifest.steps[9])
    basic_psu_step = cast(PsuAuthorizationStep, manifest.steps[10])
    basic_token_exchange_step = cast(ManifestStep, manifest.steps[11])
    transactions_basic_step = cast(ManifestStep, manifest.steps[12])
    transactions_filter_step = cast(ManifestStep, manifest.steps[14])
    bulk_transactions_basic_step = cast(ManifestStep, manifest.steps[-1])

    assert consent_token_step.token_endpoint_auth_policy == TokenEndpointAuthPolicy(source="fapi-signing")
    assert psu_step.request_object == GeneratedRequestObject(
        source="fapi-signing",
        audience="${steps.openid-discovery.response.body.issuer}",
        openbanking_intent_id="${steps.account-access-consent.response.body.Data.ConsentId}",
    )
    assert consent_step.request.url == "${config.oauth.resourceBaseUrl}/open-banking/v4.0/aisp/account-access-consents"
    consent_body = consent_step.request.body
    assert isinstance(consent_body, JsonBody)
    assert isinstance(consent_body.value, dict)
    consent_data = consent_body.value["Data"]
    assert isinstance(consent_data, dict)
    permissions = consent_data["Permissions"]
    assert isinstance(permissions, list)
    assert "ReadStatementsDetail" in permissions
    assert "ReadTransactionsDetail" in permissions
    assert accounts_step.request.url == "${config.oauth.resourceBaseUrl}/open-banking/v4.0/aisp/accounts"
    basic_consent_body = basic_consent_step.request.body
    assert isinstance(basic_consent_body, JsonBody)
    assert isinstance(basic_consent_body.value, dict)
    basic_consent_data = basic_consent_body.value["Data"]
    assert isinstance(basic_consent_data, dict)
    basic_permissions = basic_consent_data["Permissions"]
    assert isinstance(basic_permissions, list)
    assert basic_permissions == [
        "ReadAccountsBasic",
        "ReadTransactionsBasic",
        "ReadTransactionsDebits",
        "ReadTransactionsCredits",
    ]
    assert "ReadTransactionsDetail" not in basic_permissions
    assert basic_psu_step.request_object == GeneratedRequestObject(
        source="fapi-signing",
        audience="${steps.openid-discovery.response.body.issuer}",
        openbanking_intent_id="${steps.account-access-consent-transactions-basic.response.body.Data.ConsentId}",
    )
    assert isinstance(basic_token_exchange_step.request.body, FormBody)
    assert dict(basic_token_exchange_step.request.body.fields)["code"] == (
        "${steps.psu-authorization-transactions-basic.response.body.code}"
    )
    assert transactions_basic_step.request.headers == {
        "Accept": "application/json",
        "Authorization": "Bearer ${steps.token-exchange-transactions-basic.response.body.access_token}",
    }
    assert transactions_filter_step.request.url == (
        "${config.oauth.resourceBaseUrl}/open-banking/v4.0/aisp/accounts/"
        "${steps.OB-400-ACC-100400.response.body.Data.Account.0.AccountId}/transactions"
        "?fromBookingDateTime=2025-04-23T15%3A47%3A00&toBookingDateTime=2025-04-23T15%3A48%3A00"
    )
    assert bulk_transactions_basic_step.request.headers == {
        "Accept": "application/json",
        "Authorization": "Bearer ${steps.token-exchange-transactions-basic.response.body.access_token}",
    }
    assert any(
        isinstance(assertion, HeaderAssertion)
        and assertion.name == "x-fapi-interaction-id"
        and assertion.rule == "present"
        for assertion in accounts_step.assertions
    )


@pytest.mark.unit
def test_resolve_v4_pis_fcs_legacy_benchmark_returns_bundled_manifest() -> None:
    """The bundled PIS FCS legacy benchmark should resolve to the correct manifest."""
    resolved = resolve_suite(_selection("v4.0", suite_name="pis-fcs-legacy-benchmark", api="pis"))

    assert resolved.metadata.standard == "ob-read-write"
    assert resolved.metadata.spec_version == "v4.0"
    assert resolved.metadata.profile == "fapi1-advanced"
    assert resolved.metadata.api == "pis"
    assert resolved.metadata.suite == "pis-fcs-legacy-benchmark"
    assert resolved.metadata.catalog_id == "ob-read-write/v4.0/fapi1-advanced/pis/pis-fcs-legacy-benchmark"
    assert resolved.metadata.manifest_resource == "ob-read-write-v4.0-fapi1-advanced-pis-fcs-legacy-benchmark.json"
    assert "Legacy FCS benchmark" in resolved.metadata.description or "legacy" in resolved.metadata.description.lower()
    manifest = resolved.manifest
    assert manifest.schema_version == "v1"
    assert manifest.certification_coverage == "partial"
    assert manifest.name == resolved.metadata.label


@pytest.mark.unit
def test_v4_pis_fcs_legacy_benchmark_uses_distinct_generated_identifiers_per_payment_flow() -> None:
    """Independent payment-consent flows should use distinct generated test-value identifiers."""
    manifest = resolve_suite(_selection("v4.0", suite_name="pis-fcs-legacy-benchmark", api="pis")).manifest
    test_value_profiles = manifest.test_value_profiles

    assert test_value_profiles is not None
    ozone_profile = next(profile for profile in test_value_profiles.profiles if profile.id == "ozone-demo")
    generated_keys = ozone_profile.generated_keys

    assert generated_keys["domesticConsentInstructionIdentification"] == "per-run-compact-uuid"
    assert generated_keys["domesticDebtorInstructionIdentification"] == "per-run-compact-uuid"
    assert generated_keys["scheduledConsentInstructionIdentification"] == "per-run-compact-uuid"
    assert generated_keys["standingOrderInstructionIdentification"] == "per-run-compact-uuid"
    assert generated_keys["internationalInstructionIdentification"] == "per-run-compact-uuid"
    assert generated_keys["internationalScheduledInstructionIdentification"] == "per-run-compact-uuid"

    steps_by_id = {step.id: cast(ManifestStep, step) for step in manifest.steps}
    domestic_debtor_body = cast(JsonBody, steps_by_id["OB-400-DOP-100300"].request.body).value
    domestic_submit_body = cast(JsonBody, steps_by_id["OB-400-DOP-100600"].request.body).value
    scheduled_consent_body = cast(JsonBody, steps_by_id["OB-400-DOP-100800"].request.body).value
    international_consent_body = cast(JsonBody, steps_by_id["OB-400-DOP-101600"].request.body).value

    domestic_debtor_data = cast(dict[str, JsonValue], cast(dict[str, JsonValue], domestic_debtor_body)["Data"])
    domestic_submit_data = cast(dict[str, JsonValue], cast(dict[str, JsonValue], domestic_submit_body)["Data"])
    scheduled_consent_data = cast(dict[str, JsonValue], cast(dict[str, JsonValue], scheduled_consent_body)["Data"])
    international_consent_data = cast(
        dict[str, JsonValue], cast(dict[str, JsonValue], international_consent_body)["Data"]
    )

    domestic_debtor_initiation = cast(dict[str, JsonValue], domestic_debtor_data["Initiation"])
    domestic_submit_initiation = cast(dict[str, JsonValue], domestic_submit_data["Initiation"])
    scheduled_consent_initiation = cast(dict[str, JsonValue], scheduled_consent_data["Initiation"])
    international_consent_initiation = cast(dict[str, JsonValue], international_consent_data["Initiation"])

    assert (
        domestic_debtor_initiation["InstructionIdentification"]
        == "${testValues.domesticDebtorInstructionIdentification}"
    )
    assert (
        domestic_submit_initiation["InstructionIdentification"]
        == "${testValues.domesticConsentInstructionIdentification}"
    )
    assert (
        scheduled_consent_initiation["InstructionIdentification"]
        == "${testValues.scheduledConsentInstructionIdentification}"
    )
    assert (
        international_consent_initiation["InstructionIdentification"]
        == "${testValues.internationalInstructionIdentification}"
    )
    assert (
        domestic_debtor_initiation["InstructionIdentification"]
        != scheduled_consent_initiation["InstructionIdentification"]
    )
    assert (
        domestic_debtor_initiation["InstructionIdentification"]
        != international_consent_initiation["InstructionIdentification"]
    )


@pytest.mark.unit
def test_v4_pis_fcs_legacy_benchmark_has_all_29_legacy_step_ids() -> None:
    """The v4 PIS legacy benchmark should include all 29 legacy Open Banking ids."""
    manifest = resolve_suite(_selection("v4.0", suite_name="pis-fcs-legacy-benchmark", api="pis")).manifest
    steps_by_id = {step.id: step for step in manifest.steps}
    step_ids = set(steps_by_id)

    assert set(ALL_PIS_LEGACY_IDS) <= step_ids

    for step_id in MANDATORY_PIS_LEGACY_IDS:
        assert steps_by_id[step_id].mandatory is True

    for step_id in ALL_PIS_LEGACY_IDS:
        if step_id not in MANDATORY_PIS_LEGACY_IDS:
            assert steps_by_id[step_id].optional is True


@pytest.mark.unit
def test_v4_pis_fcs_legacy_benchmark_maps_legacy_schema_rows_to_pis_openapi() -> None:
    """Legacy schemaCheck rows should each assert a bundled v4.0 PIS response schema."""
    manifest = resolve_suite(_selection("v4.0", suite_name="pis-fcs-legacy-benchmark", api="pis")).manifest
    steps_by_id = {step.id: cast(ManifestStep, step) for step in manifest.steps}

    for step_id, schema_ref in PIS_LEGACY_SCHEMA_REFS_BY_ID.items():
        assert _has_response_schema_assertion(
            steps_by_id[step_id],
            document=PIS_V4_PAYMENT_OPENAPI_DOCUMENT,
            schema_ref=schema_ref,
        ), f"{step_id} is missing {schema_ref} response_schema assertion"


@pytest.mark.unit
def test_v4_pis_fcs_legacy_benchmark_maps_validate_signature_rows_to_response_signature() -> None:
    """Legacy validateSignature rows should verify response x-jws-signature using JWKS."""
    manifest = resolve_suite(_selection("v4.0", suite_name="pis-fcs-legacy-benchmark", api="pis")).manifest
    steps_by_id = {step.id: cast(ManifestStep, step) for step in manifest.steps}

    for step_id in PIS_LEGACY_VALIDATE_SIGNATURE_IDS:
        assert any(
            isinstance(assertion, ResponseSignatureAssertion)
            and assertion.jwks_step_id == "jwks-fetch"
            and assertion.header_name == "x-jws-signature"
            for assertion in steps_by_id[step_id].assertions
        ), f"{step_id} is missing response_signature assertion"


@pytest.mark.unit
def test_v4_pis_fcs_legacy_benchmark_mandatory_domestic_payment_steps_use_correct_urls() -> None:
    """Mandatory domestic-payment legacy steps should use expected methods, URLs, and payload shape."""
    manifest = resolve_suite(_selection("v4.0", suite_name="pis-fcs-legacy-benchmark", api="pis")).manifest
    steps_by_id = {step.id: cast(ManifestStep, step) for step in manifest.steps}

    consent_create_step = steps_by_id["OB-400-DOP-100100"]
    consent_debtor_step = steps_by_id["OB-400-DOP-100300"]
    consent_read_step = steps_by_id["OB-400-DOP-100400"]
    funds_step = steps_by_id["OB-400-DOP-100500"]
    payment_submit_step = steps_by_id["OB-400-DOP-100600"]
    payment_read_step = steps_by_id["OB-400-DOP-100700"]

    assert consent_create_step.request.method == "POST"
    assert "domestic-payment-consents" in consent_create_step.request.url
    assert consent_debtor_step.request.method == "POST"
    assert "domestic-payment-consents" in consent_debtor_step.request.url
    debtor_body = consent_debtor_step.request.body
    assert isinstance(debtor_body, JsonBody)
    debtor_body_value = cast(dict[str, JsonValue], debtor_body.value)
    debtor_data = cast(dict[str, JsonValue], debtor_body_value["Data"])
    debtor_initiation = cast(dict[str, JsonValue], debtor_data["Initiation"])
    assert debtor_initiation["DebtorAccount"] == {
        "SchemeName": "${testValues.thisSchemeName}",
        "Identification": "${testValues.thisIdentification}",
    }
    assert consent_read_step.request.method == "GET"
    assert "domestic-payment-consents" in consent_read_step.request.url
    assert "funds-confirmation" in funds_step.request.url
    assert payment_submit_step.request.method == "POST"
    assert "domestic-payments" in payment_submit_step.request.url
    submit_body = payment_submit_step.request.body
    assert isinstance(submit_body, JsonBody)
    submit_body_value = cast(dict[str, JsonValue], submit_body.value)
    submit_data = cast(dict[str, JsonValue], submit_body_value["Data"])
    submit_initiation = cast(dict[str, JsonValue], submit_data["Initiation"])
    assert submit_data["ConsentId"] == "${steps.OB-400-DOP-100100.response.body.Data.ConsentId}"
    assert "DebtorAccount" not in submit_initiation
    assert payment_read_step.request.method == "GET"
    assert "domestic-payments" in payment_read_step.request.url


@pytest.mark.unit
def test_v4_pis_fcs_legacy_benchmark_authorises_no_debtor_consent_for_model_bank() -> None:
    """Executable PSU chains should bind to no-debtor consent rows for model-bank compatibility."""
    manifest = resolve_suite(_selection("v4.0", suite_name="pis-fcs-legacy-benchmark", api="pis")).manifest
    steps_by_id = {step.id: step for step in manifest.steps}
    assert manifest.auth_inventory is not None

    domestic_psu_step = cast(PsuAuthorizationStep, steps_by_id["psu-authorization"])
    scheduled_psu_step = cast(PsuAuthorizationStep, steps_by_id["scheduled-payment-psu-authorization"])
    scheduled_submit_psu_step = cast(PsuAuthorizationStep, steps_by_id["scheduled-submit-psu-authorization"])
    standing_psu_step = cast(PsuAuthorizationStep, steps_by_id["standing-order-psu-authorization"])
    domestic_read_step = cast(ManifestStep, steps_by_id["OB-400-DOP-100400"])
    funds_step = cast(ManifestStep, steps_by_id["OB-400-DOP-100500"])
    scheduled_read_step = cast(ManifestStep, steps_by_id["OB-400-DOP-100900"])
    scheduled_submit_consent_step = cast(ManifestStep, steps_by_id["OB-400-DOP-101000"])
    scheduled_submit_step = cast(ManifestStep, steps_by_id["OB-400-DOP-101101"])

    assert domestic_psu_step.request_object == GeneratedRequestObject(
        source="fapi-signing",
        audience="${steps.openid-discovery.response.body.issuer}",
        openbanking_intent_id="${steps.OB-400-DOP-100100.response.body.Data.ConsentId}",
    )
    assert scheduled_psu_step.request_object == GeneratedRequestObject(
        source="fapi-signing",
        audience="${steps.openid-discovery.response.body.issuer}",
        openbanking_intent_id="${steps.OB-400-DOP-100800.response.body.Data.ConsentId}",
    )
    assert scheduled_submit_psu_step.request_object == GeneratedRequestObject(
        source="fapi-signing",
        audience="${steps.openid-discovery.response.body.issuer}",
        openbanking_intent_id="${steps.OB-400-DOP-101000.response.body.Data.ConsentId}",
    )
    assert standing_psu_step.request_object == GeneratedRequestObject(
        source="fapi-signing",
        audience="${steps.openid-discovery.response.body.issuer}",
        openbanking_intent_id="${steps.OB-400-DOP-101200.response.body.Data.ConsentId}",
    )
    assert "${steps.OB-400-DOP-100100.response.body.Data.ConsentId}" in domestic_read_step.request.url
    assert "${steps.OB-400-DOP-100100.response.body.Data.ConsentId}" in funds_step.request.url
    assert "${steps.OB-400-DOP-100800.response.body.Data.ConsentId}" in scheduled_read_step.request.url

    scheduled_submit_body = cast(JsonBody, scheduled_submit_consent_step.request.body)
    scheduled_submit_data = cast(dict[str, JsonValue], cast(dict[str, JsonValue], scheduled_submit_body.value)["Data"])
    assert scheduled_submit_data["Permission"] == "Create"
    scheduled_submit_headers = scheduled_submit_step.request.headers
    assert scheduled_submit_headers is not None
    assert (
        scheduled_submit_headers["Authorization"]
        == "Bearer ${steps.scheduled-submit-token-exchange.response.body.access_token}"
    )

    bundles_by_id = {bundle.id: bundle for bundle in manifest.auth_inventory.bundles}
    assert bundles_by_id["domestic-payment-flow"].consent_step_id == "OB-400-DOP-100100"
    assert bundles_by_id["scheduled-payment-flow"].consent_step_id == "OB-400-DOP-100800"
    assert bundles_by_id["scheduled-submit-flow"].consent_step_id == "OB-400-DOP-101000"
    assert bundles_by_id["standing-order-flow"].consent_step_id == "OB-400-DOP-101200"


@pytest.mark.unit
def test_v4_pis_fcs_legacy_benchmark_psu_backed_payloads_omit_debtor_account() -> None:
    """Consent and submission payloads on executable PSU chains should omit DebtorAccount."""
    manifest = resolve_suite(_selection("v4.0", suite_name="pis-fcs-legacy-benchmark", api="pis")).manifest
    steps_by_id = {step.id: step for step in manifest.steps if isinstance(step, ManifestStep)}
    assert manifest.auth_inventory is not None

    for step_id in (
        "OB-400-DOP-100100",
        "OB-400-DOP-100600",
        "OB-400-DOP-100800",
        "OB-400-DOP-101000",
        "OB-400-DOP-101101",
        "OB-400-DOP-101200",
        "OB-400-DOP-101400",
        "OB-400-DOP-101401",
        "OB-400-DOP-101503",
    ):
        body = cast(JsonBody, steps_by_id[step_id].request.body)
        data = cast(dict[str, JsonValue], cast(dict[str, JsonValue], body.value)["Data"])
        initiation = cast(dict[str, JsonValue], data["Initiation"])
        assert "DebtorAccount" not in initiation

    for bundle in manifest.auth_inventory.bundles:
        if bundle.psu_step_id is None or bundle.consent_step_id is None:
            continue
        consent_body = cast(JsonBody, steps_by_id[bundle.consent_step_id].request.body)
        consent_data = cast(dict[str, JsonValue], cast(dict[str, JsonValue], consent_body.value)["Data"])
        consent_initiation = cast(dict[str, JsonValue], consent_data["Initiation"])
        assert "DebtorAccount" not in consent_initiation


@pytest.mark.unit
def test_v4_pis_fcs_legacy_benchmark_negative_cases_restore_signature_parity() -> None:
    """Negative consent rows should preserve legacy signing mutations and OB error-code checks."""
    manifest = resolve_suite(_selection("v4.0", suite_name="pis-fcs-legacy-benchmark", api="pis")).manifest
    steps_by_id = {step.id: cast(ManifestStep, step) for step in manifest.steps}

    jwt_claim_negative = steps_by_id["OB-400-DOP-100110"]
    missing_header_negative = steps_by_id["OB-316-DOP-100310"]

    assert jwt_claim_negative.signing_negative_case == "omit-jwt-claim"
    assert any(
        isinstance(assertion, HttpStatusAssertion) and assertion.expected == 400
        for assertion in jwt_claim_negative.assertions
    )
    assert any(
        isinstance(assertion, ObErrorCodeAssertion)
        and assertion.codes
        == (
            "UK.OBIE.Signature.Invalid",
            "UK.OBIE.Signature.Missing",
            "UK.OBIE.Signature.Malformed",
        )
        for assertion in jwt_claim_negative.assertions
    )

    assert missing_header_negative.signing_negative_case == "omit-detached-jws-header"
    assert any(
        isinstance(assertion, HttpStatusAssertion) and assertion.expected == 400
        for assertion in missing_header_negative.assertions
    )
    assert any(
        isinstance(assertion, ObErrorCodeAssertion) and assertion.codes == ("UK.OBIE.Signature.Missing",)
        for assertion in missing_header_negative.assertions
    )


@pytest.mark.unit
def test_v4_pis_fcs_legacy_benchmark_conditional_steps_are_optional_with_selection_metadata() -> None:
    """Representative conditional legacy steps should stay optional and selection-gated."""
    manifest = resolve_suite(_selection("v4.0", suite_name="pis-fcs-legacy-benchmark", api="pis")).manifest
    steps_by_id = {step.id: step for step in manifest.steps}

    for step_id in ("OB-400-DOP-100800", "OB-400-DOP-101200", "OB-400-DOP-101600"):
        step = steps_by_id[step_id]
        assert step.mandatory is False
        assert step.optional is True
        assert step.selection_metadata is not None
        assert step.selection_metadata.conditional is True
        assert step.selection_metadata.required_test_value_keys


@pytest.mark.unit
def test_v4_pis_fcs_legacy_benchmark_scheduled_consent_bodies_include_permission_create() -> None:
    """Scheduled-consent request bodies should include Data.Permission set to Create."""
    manifest = resolve_suite(_selection("v4.0", suite_name="pis-fcs-legacy-benchmark", api="pis")).manifest
    steps_by_id = {step.id: cast(ManifestStep, step) for step in manifest.steps}

    for step_id in (
        "OB-400-DOP-100800",
        "OB-400-DOP-100810",
        "OB-400-DOP-100820",
        "OB-400-DOP-101000",
        "OB-400-DOP-102000",
    ):
        body = cast(JsonBody, steps_by_id[step_id].request.body)
        data = cast(dict[str, JsonValue], cast(dict[str, JsonValue], body.value)["Data"])
        assert data["Permission"] == "Create"


@pytest.mark.unit
def test_v4_pis_fcs_legacy_benchmark_standing_order_selection_requires_final_payment_datetime() -> None:
    """Standing-order rows should require finalPaymentDateTime before selecting the flow."""
    manifest = resolve_suite(_selection("v4.0", suite_name="pis-fcs-legacy-benchmark", api="pis")).manifest
    steps_by_id = {step.id: step for step in manifest.steps}

    for step_id in (
        "OB-400-DOP-101200",
        "standing-order-psu-authorization",
        "standing-order-token-exchange",
        "OB-400-DOP-101300",
        "OB-400-DOP-101400",
        "OB-400-DOP-101401",
        "OB-400-DOP-101500",
        "OB-400-DOP-101503",
    ):
        step = steps_by_id[step_id]
        assert step.selection_metadata is not None
        assert "finalPaymentDateTime" in step.selection_metadata.required_test_value_keys


@pytest.mark.unit
def test_v4_pis_fcs_legacy_benchmark_standing_order_payload_shapes_match_v4() -> None:
    """Standing-order consent and submission rows should use v4 mandate-related payload shape."""
    manifest = resolve_suite(_selection("v4.0", suite_name="pis-fcs-legacy-benchmark", api="pis")).manifest
    steps_by_id = {step.id: cast(ManifestStep, step) for step in manifest.steps}

    standing_consent_step = steps_by_id["OB-400-DOP-101200"]
    standing_negative_submit_step = steps_by_id["OB-400-DOP-101400"]
    standing_submit_step = steps_by_id["OB-400-DOP-101401"]
    standing_known_model_bank_step = steps_by_id["OB-400-DOP-101503"]

    consent_body = cast(JsonBody, standing_consent_step.request.body)
    consent_data = cast(dict[str, JsonValue], cast(dict[str, JsonValue], consent_body.value)["Data"])
    assert consent_data["Permission"] == "Create"
    consent_initiation = cast(dict[str, JsonValue], consent_data["Initiation"])
    consent_mandate = cast(dict[str, JsonValue], consent_initiation["MandateRelatedInformation"])
    consent_frequency = cast(dict[str, JsonValue], consent_mandate["Frequency"])
    assert "InstructionIdentification" not in consent_initiation
    assert "EndToEndIdentification" not in consent_initiation
    assert consent_frequency["Type"] == "${testValues.frequency}"
    assert "CountPerPeriod" not in consent_frequency

    for step in (standing_negative_submit_step, standing_submit_step, standing_known_model_bank_step):
        body = cast(JsonBody, step.request.body)
        data = cast(dict[str, JsonValue], cast(dict[str, JsonValue], body.value)["Data"])
        initiation = cast(dict[str, JsonValue], data["Initiation"])
        mandate = cast(dict[str, JsonValue], initiation["MandateRelatedInformation"])
        frequency = cast(dict[str, JsonValue], mandate["Frequency"])
        assert "InstructionIdentification" not in initiation
        assert "EndToEndIdentification" not in initiation
        assert "Type" in frequency
        if step.id != "OB-400-DOP-101503":
            assert "CountPerPeriod" not in frequency

    known_model_bank_body = cast(JsonBody, standing_known_model_bank_step.request.body)
    known_model_bank_data = cast(dict[str, JsonValue], cast(dict[str, JsonValue], known_model_bank_body.value)["Data"])
    known_model_bank_initiation = cast(dict[str, JsonValue], known_model_bank_data["Initiation"])
    known_model_bank_mandate = cast(dict[str, JsonValue], known_model_bank_initiation["MandateRelatedInformation"])
    known_model_bank_frequency = cast(dict[str, JsonValue], known_model_bank_mandate["Frequency"])
    assert known_model_bank_data["Permission"] == "Create"
    assert "FinalPaymentDateTime" in known_model_bank_mandate
    assert known_model_bank_frequency["Type"] == "${testValues.frequency}"
    assert known_model_bank_frequency["CountPerPeriod"] == 1
    assert standing_known_model_bank_step.signing_negative_case is None


@pytest.mark.unit
def test_v4_pis_fcs_legacy_benchmark_detached_jws_on_write_steps() -> None:
    """Detached JWS should be set for write calls and absent for GET read-back calls."""
    manifest = resolve_suite(_selection("v4.0", suite_name="pis-fcs-legacy-benchmark", api="pis")).manifest
    steps_by_id = {step.id: cast(ManifestStep, step) for step in manifest.steps}

    for step_id in ("OB-400-DOP-100100", "OB-400-DOP-100300", "OB-400-DOP-100600"):
        assert steps_by_id[step_id].request.detached_jws is not None

    for step_id in ("OB-400-DOP-100400", "OB-400-DOP-100700"):
        assert steps_by_id[step_id].request.detached_jws is None


@pytest.mark.unit
def test_v4_pis_fcs_legacy_benchmark_inventory_covers_all_29_rows() -> None:
    """The generated PIS prior-FCS inventory should record all 29 rows as implemented defaults."""
    inventory = cast(dict[str, JsonValue], json.loads(PIS_LEGACY_BENCHMARK_INVENTORY_PATH.read_text(encoding="utf-8")))
    records = cast(list[dict[str, JsonValue]], inventory["records"])

    assert len(records) == 29
    assert cast(dict[str, JsonValue], inventory["summary"])["implementedInV4Benchmark"] == 29
    assert cast(dict[str, JsonValue], inventory["summary"])["notImplementedInV4Benchmark"] == 0
    assert all(cast(bool, row.get("selectedByDefault")) is True for row in records)


@pytest.mark.unit
def test_v4_ais_fcs_legacy_benchmark_matches_target_legacy_classification() -> None:
    """Guard the current legacy parity target subset without requiring every legacy script yet."""
    legacy_scripts = _legacy_fcs_script_index()
    manifest = resolve_suite(_selection("v4.0", suite_name="ais-fcs-legacy-benchmark")).manifest
    bundled_steps = {step.id: step for step in manifest.steps}

    for target_id in LEGACY_FCS_PARITY_TARGET_IDS:
        legacy_script = legacy_scripts[target_id]
        legacy_uri_implementation = legacy_script["uriImplementation"]
        assert legacy_uri_implementation in {"mandatory", "optional"}

        bundled_step = bundled_steps[target_id]
        assert bundled_step.mandatory is (legacy_uri_implementation == "mandatory")
        assert bundled_step.optional is (legacy_uri_implementation == "optional")


@pytest.mark.unit
def test_v4_ais_fcs_legacy_benchmark_uses_all_items_absent_field_for_transactions_basic_steps() -> None:
    manifest = resolve_suite(_selection("v4.0", suite_name="ais-fcs-legacy-benchmark")).manifest
    bundled_steps = {step.id: step for step in manifest.steps}
    expected_fields = set(LEGACY_TRANSACTIONS_BASIC_DETAIL_FIELDS)
    legacy_first_item_paths = {
        f"Data.Transaction.0.{detail_field}" for detail_field in LEGACY_TRANSACTIONS_BASIC_DETAIL_FIELDS
    }

    for target_id in LEGACY_FCS_PARITY_TARGET_IDS:
        target_step = bundled_steps[target_id]
        assert isinstance(target_step, ManifestStep)
        all_items_absent_assertions = [
            assertion
            for assertion in target_step.assertions
            if isinstance(assertion, JsonFieldAssertion)
            and assertion.path == "Data.Transaction"
            and assertion.rule == "all_items_absent_field"
        ]
        assert len(all_items_absent_assertions) == len(LEGACY_TRANSACTIONS_BASIC_DETAIL_FIELDS)
        assert {assertion.field for assertion in all_items_absent_assertions} == expected_fields
        assert not any(
            isinstance(assertion, JsonFieldAssertion)
            and assertion.rule == "absent"
            and assertion.path in legacy_first_item_paths
            for assertion in target_step.assertions
        )


@pytest.mark.unit
def test_resolve_v4_ais_certification_slice_returns_bundled_manifest() -> None:
    resolved = resolve_suite(_selection("v4.0", suite_name="ais-certification-slice"))

    assert resolved.metadata.standard == "ob-read-write"
    assert resolved.metadata.spec_version == "v4.0"
    assert resolved.metadata.profile == "fapi1-advanced"
    assert resolved.metadata.api == "ais"
    assert resolved.metadata.suite == "ais-certification-slice"
    assert resolved.metadata.catalog_id == "ob-read-write/v4.0/fapi1-advanced/ais-certification-slice"
    assert resolved.metadata.manifest_resource == "ob-read-write-v4.0-fapi1-advanced-ais-certification-slice.json"
    assert "partial coverage" in resolved.metadata.description

    manifest = resolved.manifest
    assert manifest.schema_version == "v1"
    assert manifest.certification_coverage == "partial"
    assert manifest.name == resolved.metadata.label
    assert [step.id for step in manifest.steps] == [
        "openid-discovery",
        "jwks-fetch",
        "client-credentials-token",
        "account-access-consent",
        "psu-authorization",
        "token-exchange",
        "accounts-list",
        "account-balances",
        "account-transactions",
    ]
    assert [step.mandatory for step in manifest.steps] == [True, True, True, True, True, True, True, True, True]

    consent_token_step = cast(ManifestStep, manifest.steps[2])
    consent_step = cast(ManifestStep, manifest.steps[3])
    token_exchange_step = cast(ManifestStep, manifest.steps[5])
    accounts_step = cast(ManifestStep, manifest.steps[6])
    balances_step = cast(ManifestStep, manifest.steps[7])
    transactions_step = cast(ManifestStep, manifest.steps[8])

    assert consent_token_step.request.url == "${steps.openid-discovery.response.body.token_endpoint}"
    assert isinstance(consent_token_step.request.body, FormBody)
    assert dict(consent_token_step.request.body.fields) == {
        "grant_type": "client_credentials",
        "client_id": "${config.oauth.clientId}",
        "scope": "accounts",
    }
    assert token_exchange_step.request.url == "${steps.openid-discovery.response.body.token_endpoint}"
    assert isinstance(token_exchange_step.request.body, FormBody)
    assert dict(token_exchange_step.request.body.fields) == {
        "grant_type": "authorization_code",
        "code": "${steps.psu-authorization.response.body.code}",
        "redirect_uri": "${config.oauth.redirectUri}",
        "client_id": "${config.oauth.clientId}",
    }
    assert consent_step.request.url == "${config.oauth.resourceBaseUrl}/open-banking/v4.0/aisp/account-access-consents"
    assert consent_step.request.headers == {
        "Accept": "application/json",
        "Authorization": "Bearer ${steps.client-credentials-token.response.body.access_token}",
    }
    assert accounts_step.request.url == "${config.oauth.resourceBaseUrl}/open-banking/v4.0/aisp/accounts"
    assert balances_step.request.url == (
        "${config.oauth.resourceBaseUrl}/open-banking/v4.0/aisp/accounts/"
        "${steps.accounts-list.response.body.Data.Account.0.AccountId}/balances"
    )
    assert balances_step.request.headers == {
        "Accept": "application/json",
        "Authorization": "Bearer ${steps.token-exchange.response.body.access_token}",
    }
    assert transactions_step.request.url == (
        "${config.oauth.resourceBaseUrl}/open-banking/v4.0/aisp/accounts/"
        "${steps.accounts-list.response.body.Data.Account.0.AccountId}/transactions"
    )
    assert transactions_step.request.headers == {
        "Accept": "application/json",
        "Authorization": "Bearer ${steps.token-exchange.response.body.access_token}",
    }

    consent_assertions = consent_step.assertions
    accounts_assertions = accounts_step.assertions
    balances_assertions = balances_step.assertions
    transactions_assertions = transactions_step.assertions
    assert any(
        isinstance(assertion, JsonFieldAssertion) and assertion.path == "Data.ConsentId" and assertion.rule == "string"
        for assertion in consent_assertions
    )
    assert any(
        isinstance(assertion, JsonFieldAssertion) and assertion.path == "Data.Account" and assertion.rule == "array"
        for assertion in accounts_assertions
    )
    assert any(
        isinstance(assertion, JsonFieldAssertion) and assertion.path == "Data.Balance" and assertion.rule == "array"
        for assertion in balances_assertions
    )
    assert any(
        isinstance(assertion, JsonFieldAssertion) and assertion.path == "Data.Transaction" and assertion.rule == "array"
        for assertion in transactions_assertions
    )


@pytest.mark.unit
@pytest.mark.parametrize("spec_version", ["v4.0", "v4.0.1"])
def test_resolve_ais_certification_slice_returns_bundled_manifest_for_supported_versions(
    spec_version: SuiteSpecVersion,
) -> None:
    resolved = resolve_suite(_selection(spec_version, suite_name="ais-certification-slice"))

    assert resolved.metadata.standard == "ob-read-write"
    assert resolved.metadata.spec_version == spec_version
    assert resolved.metadata.profile == "fapi1-advanced"
    assert resolved.metadata.api == "ais"
    assert resolved.metadata.suite == "ais-certification-slice"
    assert resolved.metadata.manifest_resource == (
        f"ob-read-write-{spec_version}-fapi1-advanced-ais-certification-slice.json"
    )
    assert resolved.manifest.schema_version == "v1"
    assert resolved.manifest.certification_coverage == "partial"


@pytest.mark.unit
def test_resolve_suite_rejects_unsupported_catalog_key() -> None:
    unsupported = SuiteSelection(
        standard="ob-read-write",
        spec_version=cast("SuiteSpecVersion", "v9.9"),
        profile="fapi1-advanced",
        suite="discovery-jwks",
        api="ais",
    )

    with pytest.raises(SuiteCatalogError, match="Unsupported suite selection: .*specVersion=v9.9"):
        resolve_suite(unsupported)


@pytest.mark.unit
def test_resolve_suite_reports_missing_bundled_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    key: suite_catalog.SuiteCatalogKey = ("ob-read-write", "v4.0", "fapi1-advanced", "ais", "discovery-jwks")
    missing_entry = suite_catalog._CatalogEntry(
        key=key,
        resource_name="missing-discovery-jwks.json",
        label="Missing discovery suite",
        description="Missing resource test entry",
    )
    monkeypatch.setattr(suite_catalog, "_CATALOG_BY_KEY", {key: missing_entry})

    with pytest.raises(
        SuiteCatalogError, match="Bundled suite manifest resource not found: missing-discovery-jwks.json"
    ):
        resolve_suite(_selection())


@pytest.mark.unit
def test_resolve_suite_reports_invalid_bundled_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_read_resource_text(resource_name: str) -> str:
        return '{"schemaVersion": "v1",'

    monkeypatch.setattr(suite_catalog, "_read_resource_text", fake_read_resource_text)

    with pytest.raises(SuiteCatalogError, match="Invalid JSON in bundled suite manifest"):
        resolve_suite(_selection())


@pytest.mark.unit
def test_resolve_suite_reports_invalid_bundled_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_read_resource_text(resource_name: str) -> str:
        return '{"schemaVersion": "v1", "name": "Broken", "steps": []}'

    monkeypatch.setattr(suite_catalog, "_read_resource_text", fake_read_resource_text)

    with pytest.raises(SuiteCatalogError, match="Invalid bundled suite manifest .*steps must be a non-empty array"):
        resolve_suite(_selection())


# ---------------------------------------------------------------------------
# SuiteMetadata.to_suite_selection() tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("spec_version", "api", "suite_name"),
    [
        ("v4.0", "ais", "discovery-jwks"),
        ("v4.0", "ais", "psu-auth-starter"),
        ("v4.0.1", "ais", "ais-certification-baseline"),
        ("v4.0", "pis", "psu-auth-starter"),
        ("v4.0", "cbpii", "discovery-jwks"),
        ("v4.0.1", "vrp", "psu-auth-starter"),
        ("v3.1.11", "ais", "discovery-jwks"),
    ],
)
def test_suite_metadata_to_suite_selection_roundtrips_resolve_suite(
    spec_version: SuiteSpecVersion,
    api: SuiteApiFamily,
    suite_name: str,
) -> None:
    """to_suite_selection must produce a key that resolves back to the same catalog entry."""
    original = _selection(spec_version=spec_version, suite_name=suite_name, api=api)
    resolved = resolve_suite(original)

    rebuilt_selection = resolved.metadata.to_suite_selection()
    re_resolved = resolve_suite(rebuilt_selection)

    assert re_resolved.metadata.catalog_id == resolved.metadata.catalog_id
    assert re_resolved.metadata.standard == resolved.metadata.standard
    assert re_resolved.metadata.spec_version == resolved.metadata.spec_version
    assert re_resolved.metadata.api == resolved.metadata.api
    assert re_resolved.metadata.suite == resolved.metadata.suite


@pytest.mark.unit
def test_suite_metadata_to_suite_selection_fields_match_metadata() -> None:
    """The selection returned by to_suite_selection must reflect each metadata field."""
    resolved = resolve_suite(_selection(spec_version="v4.0", suite_name="ais-certification-baseline"))
    metadata = resolved.metadata

    selection = metadata.to_suite_selection()

    assert selection.standard == metadata.standard
    assert selection.spec_version == metadata.spec_version
    assert selection.profile == metadata.profile
    assert selection.api == metadata.api
    assert selection.suite == metadata.suite


@pytest.mark.unit
def test_suite_metadata_to_suite_selection_enables_capability_lookup() -> None:
    """Passing to_suite_selection() to resolve_suite_environment_capability must return a capability."""
    from conformance.environment_capabilities import resolve_suite_environment_capability

    resolved = resolve_suite(_selection(spec_version="v4.0", suite_name="ais-certification-baseline"))
    selection = resolved.metadata.to_suite_selection()

    capability = resolve_suite_environment_capability(selection)

    assert capability is not None
    assert capability.suite == "ais-certification-baseline"
    assert capability.spec_version == "v4.0"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("spec_version", "api", "suite_name"),
    [
        ("v4.0", "ais", "discovery-jwks"),
        ("v4.0.1", "ais", "ais-certification-slice"),
        ("v3.1.11", "ais", "psu-auth-starter"),
    ],
)
def test_suite_metadata_to_suite_selection_capability_lookup_various_suites(
    spec_version: SuiteSpecVersion,
    api: SuiteApiFamily,
    suite_name: str,
) -> None:
    """Selected bundled catalog entries resolve a non-None capability via to_suite_selection()."""
    from conformance.environment_capabilities import resolve_suite_environment_capability

    resolved = resolve_suite(_selection(spec_version=spec_version, suite_name=suite_name, api=api))
    capability = resolve_suite_environment_capability(resolved.metadata.to_suite_selection())

    assert capability is not None


@pytest.mark.unit
def test_all_bundled_suite_metadata_produce_valid_suite_selections() -> None:
    """Every catalog entry's to_suite_selection() must round-trip through resolve_suite."""
    all_metadata = suite_catalog.list_supported_suites()

    for metadata in all_metadata:
        selection = metadata.to_suite_selection()
        re_resolved = resolve_suite(selection)
        assert re_resolved.metadata.catalog_id == metadata.catalog_id
