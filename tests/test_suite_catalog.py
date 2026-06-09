from __future__ import annotations

from typing import cast

import pytest

import conformance.suite_catalog as suite_catalog
from conformance.manifest import (
    FormBody,
    GeneratedRequestObject,
    HeaderAssertion,
    JsonBody,
    JsonFieldAssertion,
    ManifestStep,
    PsuAuthorizationStep,
    TokenEndpointAuthPolicy,
)
from conformance.model_bank_config import SuiteName, SuiteSelection, SuiteSpecVersion
from conformance.suite_catalog import SuiteCatalogError, resolve_suite


def _selection(spec_version: SuiteSpecVersion = "v4.0", suite_name: str = "discovery-jwks") -> SuiteSelection:
    """Build a suite-selection value for bundled catalog tests.

    Args:
        spec_version: Catalog spec version to resolve.
        suite_name: Bundled suite identifier under test.

    Returns:
        Suite selection object matching the requested catalog entry.
    """
    return SuiteSelection(
        standard="ob-read-write",
        spec_version=spec_version,
        profile="fapi1-advanced",
        suite=cast(SuiteName, suite_name),
    )


@pytest.mark.unit
@pytest.mark.parametrize("spec_version", ["v3.1.11", "v4.0"])
def test_resolve_suite_returns_bundled_manifest_for_supported_versions(spec_version: SuiteSpecVersion) -> None:
    resolved = resolve_suite(_selection(spec_version))

    assert resolved.metadata.standard == "ob-read-write"
    assert resolved.metadata.spec_version == spec_version
    assert resolved.metadata.profile == "fapi1-advanced"
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
    assert [metadata.catalog_id for metadata in first] == sorted(metadata.catalog_id for metadata in first)
    assert [metadata.spec_version for metadata in first] == ["v3.1.11", "v3.1.11", "v4.0", "v4.0", "v4.0", "v4.0"]
    assert [metadata.suite for metadata in first] == [
        "discovery-jwks",
        "psu-auth-starter",
        "ais-certification-baseline",
        "ais-certification-slice",
        "discovery-jwks",
        "psu-auth-starter",
    ]


@pytest.mark.unit
@pytest.mark.parametrize("spec_version", ["v3.1.11", "v4.0"])
def test_resolve_psu_auth_starter_returns_bundled_manifest_for_supported_versions(
    spec_version: SuiteSpecVersion,
) -> None:
    resolved = resolve_suite(_selection(spec_version, suite_name="psu-auth-starter"))

    assert resolved.metadata.standard == "ob-read-write"
    assert resolved.metadata.spec_version == spec_version
    assert resolved.metadata.profile == "fapi1-advanced"
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
@pytest.mark.parametrize("spec_version", ["v3.1.11", "v4.0"])
def test_psu_auth_starter_manifest_is_partial_coverage(spec_version: SuiteSpecVersion) -> None:
    resolved = resolve_suite(_selection(spec_version, suite_name="psu-auth-starter"))

    assert resolved.manifest.certification_coverage == "partial"


@pytest.mark.unit
def test_resolve_v4_ais_certification_baseline_returns_bundled_manifest() -> None:
    resolved = resolve_suite(_selection("v4.0", suite_name="ais-certification-baseline"))

    assert resolved.metadata.standard == "ob-read-write"
    assert resolved.metadata.spec_version == "v4.0"
    assert resolved.metadata.profile == "fapi1-advanced"
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
        "psu-authorization",
        "token-exchange",
        "account-access-consent",
        "accounts-list",
        "account-detail",
        "account-balances",
        "account-transactions",
        "transactions-list",
    ]
    optional_step_ids = [
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

    token_exchange_step = cast(ManifestStep, manifest.steps[3])
    consent_step = cast(ManifestStep, manifest.steps[4])
    accounts_step = cast(ManifestStep, manifest.steps[5])
    account_detail_step = cast(ManifestStep, manifest.steps[6])
    balances_step = cast(ManifestStep, manifest.steps[7])
    transactions_step = cast(ManifestStep, manifest.steps[8])
    transactions_list_step = cast(ManifestStep, manifest.steps[9])
    psu_step = cast(PsuAuthorizationStep, manifest.steps[2])

    assert psu_step.request_object == GeneratedRequestObject(
        source="fapi-signing",
        audience="${steps.openid-discovery.response.body.issuer}",
    )
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
        "Authorization": "Bearer ${steps.token-exchange.response.body.access_token}",
    }
    consent_body = consent_step.request.body
    assert isinstance(consent_body, JsonBody)
    assert isinstance(consent_body.value, dict)
    consent_data = consent_body.value["Data"]
    assert isinstance(consent_data, dict)
    permissions = consent_data["Permissions"]
    assert isinstance(permissions, list)
    assert "ReadTransactionsDetail" in permissions
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

    consent_assertions = consent_step.assertions
    accounts_assertions = accounts_step.assertions
    account_detail_assertions = account_detail_step.assertions
    balances_assertions = balances_step.assertions
    transactions_assertions = transactions_step.assertions
    transactions_list_assertions = transactions_list_step.assertions
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
    assert any(
        isinstance(assertion, JsonFieldAssertion) and assertion.path == "Data.Account" and assertion.rule == "array"
        for assertion in account_detail_assertions
    )
    assert any(
        isinstance(assertion, JsonFieldAssertion) and assertion.path == "Data.Balance" and assertion.rule == "array"
        for assertion in balances_assertions
    )
    assert any(
        isinstance(assertion, JsonFieldAssertion) and assertion.path == "Data.Transaction" and assertion.rule == "array"
        for assertion in transactions_assertions
    )
    assert any(
        isinstance(assertion, JsonFieldAssertion)
        and assertion.path == "Data.Transaction"
        and assertion.rule == "all_items_have_field"
        and assertion.field == "BookingDateTime"
        for assertion in transactions_assertions
    )
    assert any(
        isinstance(assertion, JsonFieldAssertion) and assertion.path == "Data.Transaction" and assertion.rule == "array"
        for assertion in transactions_list_assertions
    )
    assert any(
        isinstance(assertion, JsonFieldAssertion)
        and assertion.path == "Data.Transaction"
        and assertion.rule == "all_items_have_field"
        and assertion.field == "BookingDateTime"
        for assertion in transactions_list_assertions
    )


@pytest.mark.unit
def test_resolve_v4_ais_certification_slice_returns_bundled_manifest() -> None:
    resolved = resolve_suite(_selection("v4.0", suite_name="ais-certification-slice"))

    assert resolved.metadata.standard == "ob-read-write"
    assert resolved.metadata.spec_version == "v4.0"
    assert resolved.metadata.profile == "fapi1-advanced"
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
        "psu-authorization",
        "token-exchange",
        "account-access-consent",
        "accounts-list",
        "account-balances",
        "account-transactions",
    ]
    assert [step.mandatory for step in manifest.steps] == [True, True, True, True, True, True, True, True]

    token_exchange_step = cast(ManifestStep, manifest.steps[3])
    consent_step = cast(ManifestStep, manifest.steps[4])
    accounts_step = cast(ManifestStep, manifest.steps[5])
    balances_step = cast(ManifestStep, manifest.steps[6])
    transactions_step = cast(ManifestStep, manifest.steps[7])

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
        "Authorization": "Bearer ${steps.token-exchange.response.body.access_token}",
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
def test_resolve_suite_rejects_unsupported_catalog_key() -> None:
    unsupported = SuiteSelection(
        standard="ob-read-write",
        spec_version=cast("SuiteSpecVersion", "v9.9"),
        profile="fapi1-advanced",
        suite="discovery-jwks",
    )

    with pytest.raises(SuiteCatalogError, match="Unsupported suite selection: .*specVersion=v9.9"):
        resolve_suite(unsupported)


@pytest.mark.unit
def test_resolve_suite_reports_missing_bundled_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    key: suite_catalog.SuiteCatalogKey = ("ob-read-write", "v4.0", "fapi1-advanced", "discovery-jwks")
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
