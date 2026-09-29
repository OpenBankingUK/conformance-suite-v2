"""Compiled account-information and CBPII plans lowered into executable manifests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from conformance.context import RuntimeConfig
from conformance.manifest import (
    GeneratedRequestObject,
    ManifestStep,
    PsuAuthorizationStep,
)

pytestmark = pytest.mark.unit


def test_compiled_ais_manifest_maps_present_fapi_header_assertions(tmp_path: Path) -> None:
    """AIS catalogue conversion accepts present-rule FAPI header assertions.

    Args:
        tmp_path: Pytest temporary directory used as the runtime-input base.
    """
    from conformance.catalogue import ImplementedEndpoint, TestPlanSpec, compile_test_plan
    from conformance.catalogues.ais import (
        AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE,
        AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE_KEY,
    )
    from conformance.executor import _compiled_plan_to_manifest
    from conformance.manifest import HeaderAssertion

    runtime_inputs = {
        "resourceBaseUrl": "https://resource.example.com",
        "consentedAccountId": "account-123",
    }
    spec = TestPlanSpec(
        schema_version="v1",
        catalogue_key=AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE_KEY,
        security_profile="fapi1-advanced",
        implemented_endpoints=(
            ImplementedEndpoint(
                method="GET",
                path="/open-banking/v4.0/aisp/accounts/{AccountId}",
                resource_group="Accounts",
            ),
        ),
        runtime_inputs=runtime_inputs,
    )
    compiled_plan = compile_test_plan(AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE, spec)

    manifest = _compiled_plan_to_manifest(
        compiled_plan,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=tmp_path,
        runtime_config=RuntimeConfig(discovery_url="https://auth.example.com/.well-known/openid-configuration"),
    )

    account_step = next(step for step in manifest.steps if step.id == "ais-at-account-by-id-200-request")
    assert isinstance(account_step, ManifestStep)
    header_assertion = next(
        assertion
        for assertion in account_step.assertions
        if isinstance(assertion, HeaderAssertion) and assertion.name == "x-fapi-interaction-id"
    )
    assert account_step.request.url == "https://resource.example.com/open-banking/v4.0/aisp/accounts/account-123"
    assert header_assertion.rule == "present"


def test_compiled_ais_manifest_maps_legacy_one_of_status_assertions(tmp_path: Path) -> None:
    """AIS legacy one-of failures accept either permitted status.

    Args:
        tmp_path: Pytest temporary directory used as the runtime-input base.
    """
    from conformance.catalogue import ImplementedEndpoint, TestPlanSpec, compile_test_plan
    from conformance.catalogues.ais import (
        AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE,
        AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE_KEY,
    )
    from conformance.executor import _compiled_plan_to_manifest
    from conformance.manifest import HttpStatusAssertion

    runtime_inputs = {
        "resourceBaseUrl": "https://resource.example.com",
        "consentedAccountId": "account-123",
    }
    spec = TestPlanSpec(
        schema_version="v1",
        catalogue_key=AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE_KEY,
        security_profile="fapi1-advanced",
        implemented_endpoints=(
            ImplementedEndpoint(
                method="GET",
                path="/open-banking/v4.0/aisp/accounts/{AccountId}/balances",
                resource_group="Balances",
            ),
        ),
        runtime_inputs=runtime_inputs,
    )
    compiled_plan = compile_test_plan(AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE, spec)

    manifest = _compiled_plan_to_manifest(
        compiled_plan,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=tmp_path,
        runtime_config=RuntimeConfig(discovery_url="https://auth.example.com/.well-known/openid-configuration"),
    )

    balance_step = next(step for step in manifest.steps if step.id == "ais-at-legacy-balance-bal-101600-request")
    assert isinstance(balance_step, ManifestStep)
    status_assertion = next(
        assertion for assertion in balance_step.assertions if isinstance(assertion, HttpStatusAssertion)
    )
    assert status_assertion.expected is None
    assert status_assertion.expected_one_of == (400, 403)


def test_compiled_ais_manifest_builds_authorised_account_access_setup(tmp_path: Path) -> None:
    """AIS setup uses consent, PSU authorisation, and token endpoint exchange.

    Args:
        tmp_path: Pytest temporary directory used as the runtime-input base.
    """
    from conformance.catalogue import ImplementedEndpoint, TestPlanSpec, compile_test_plan
    from conformance.catalogues.ais import (
        AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE,
        AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE_KEY,
    )
    from conformance.executor import _compiled_plan_to_manifest
    from conformance.manifest import (
        DetachedJwsPolicy,
        FormBody,
        JsonBody,
        ManifestStep,
    )

    runtime_inputs = {
        "resourceBaseUrl": "https://resource.example.com",
        "consentedAccountId": "account-123",
    }
    spec = TestPlanSpec(
        schema_version="v1",
        catalogue_key=AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE_KEY,
        security_profile="fapi1-advanced",
        implemented_endpoints=(
            ImplementedEndpoint(
                method="GET",
                path="/open-banking/v4.0/aisp/accounts/{AccountId}",
                resource_group="Accounts",
            ),
            ImplementedEndpoint(
                method="GET",
                path="/open-banking/v4.0/aisp/accounts/{AccountId}/balances",
                resource_group="Balances",
            ),
        ),
        runtime_inputs=runtime_inputs,
    )
    compiled_plan = compile_test_plan(AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE, spec)

    manifest = _compiled_plan_to_manifest(
        compiled_plan,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=tmp_path,
        runtime_config=RuntimeConfig(discovery_url="https://auth.example.com/.well-known/openid-configuration"),
    )

    step_ids = [step.id for step in manifest.steps]
    client_token_index = step_ids.index("setup-token-ais-client-credentials")
    basic_consent_index = step_ids.index("ais-at-setup-basic-consent-request")
    basic_psu_index = step_ids.index("setup-ais-basic-consent-authorisation")
    detail_consent_index = step_ids.index("ais-at-setup-detail-consent-request")
    detail_psu_index = step_ids.index("setup-ais-detail-consent-authorisation")
    basic_token_index = step_ids.index("ais-at-setup-basic-token-request")
    detail_token_index = step_ids.index("ais-at-setup-detail-token-request")
    account_resource_index = step_ids.index("ais-at-account-by-id-200-request")
    detail_resource_index = step_ids.index("ais-at-account-by-id-detail-200-request")
    assert (
        client_token_index
        < basic_consent_index
        < basic_psu_index
        < detail_consent_index
        < detail_psu_index
        < basic_token_index
        < detail_token_index
        < account_resource_index
        < detail_resource_index
    )

    client_token_step = manifest.steps[client_token_index]
    assert isinstance(client_token_step, ManifestStep)
    assert client_token_step.phase == "setup"
    assert client_token_step.request.url == "${config.oauth.tokenEndpoint}"
    assert client_token_step.produces_token_id == "ais-client-credentials"  # noqa: S105 - semantic token id
    assert isinstance(client_token_step.request.body, FormBody)
    assert client_token_step.request.body.fields["grant_type"] == "client_credentials"
    assert client_token_step.request.body.fields["scope"] == "accounts"

    basic_consent_step = manifest.steps[basic_consent_index]
    assert isinstance(basic_consent_step, ManifestStep)
    assert basic_consent_step.phase == "setup"
    assert basic_consent_step.required_token_id == "ais-client-credentials"  # noqa: S105 - semantic token id
    assert basic_consent_step.request.detached_jws == DetachedJwsPolicy(source="fapi-signing")
    assert isinstance(basic_consent_step.request.body, JsonBody)
    assert basic_consent_step.request.body.value == {
        "Data": {
            "Permissions": [
                "ReadAccountsBasic",
                "ReadBalances",
                "ReadBeneficiariesBasic",
                "ReadDirectDebits",
                "ReadOffers",
                "ReadParty",
                "ReadPartyPSU",
                "ReadProducts",
                "ReadScheduledPaymentsBasic",
                "ReadStandingOrdersBasic",
                "ReadStatementsBasic",
                "ReadTransactionsBasic",
                "ReadTransactionsCredits",
                "ReadTransactionsDebits",
            ],
        },
        "Risk": {},
    }

    detail_consent_step = manifest.steps[detail_consent_index]
    assert isinstance(detail_consent_step, ManifestStep)
    assert isinstance(detail_consent_step.request.body, JsonBody)
    assert detail_consent_step.request.body.value == {
        "Data": {
            "Permissions": [
                "ReadAccountsDetail",
                "ReadBalances",
                "ReadBeneficiariesDetail",
                "ReadDirectDebits",
                "ReadOffers",
                "ReadPAN",
                "ReadParty",
                "ReadPartyPSU",
                "ReadProducts",
                "ReadScheduledPaymentsDetail",
                "ReadStandingOrdersDetail",
                "ReadStatementsDetail",
                "ReadTransactionsCredits",
                "ReadTransactionsDebits",
                "ReadTransactionsDetail",
            ],
        },
        "Risk": {},
    }

    basic_psu_step = manifest.steps[basic_psu_index]
    assert isinstance(basic_psu_step, PsuAuthorizationStep)
    assert basic_psu_step.phase == "setup"
    assert basic_psu_step.scope == "openid accounts"
    assert isinstance(basic_psu_step.request_object, GeneratedRequestObject)
    assert basic_psu_step.request_object.openbanking_intent_id == (
        "${steps.ais-at-setup-basic-consent-request.response.body.Data.ConsentId}"
    )

    detail_psu_step = manifest.steps[detail_psu_index]
    assert isinstance(detail_psu_step, PsuAuthorizationStep)
    assert isinstance(detail_psu_step.request_object, GeneratedRequestObject)
    assert detail_psu_step.request_object.openbanking_intent_id == (
        "${steps.ais-at-setup-detail-consent-request.response.body.Data.ConsentId}"
    )

    basic_token_step = manifest.steps[basic_token_index]
    assert isinstance(basic_token_step, ManifestStep)
    assert basic_token_step.phase == "setup"
    assert basic_token_step.request.url == "${config.oauth.tokenEndpoint}"
    assert basic_token_step.produces_token_id == "ais-account-access-basic"  # noqa: S105 - semantic token id
    assert basic_token_step.token_endpoint_auth_policy is not None
    assert isinstance(basic_token_step.request.body, FormBody)
    assert basic_token_step.request.body.fields["grant_type"] == "authorization_code"
    assert basic_token_step.request.body.fields["code"] == (
        "${steps.setup-ais-basic-consent-authorisation.response.body.code}"
    )

    detail_token_step = manifest.steps[detail_token_index]
    assert isinstance(detail_token_step, ManifestStep)
    assert detail_token_step.produces_token_id == "ais-account-access-detail"  # noqa: S105 - semantic token id

    account_resource_step = manifest.steps[account_resource_index]
    assert isinstance(account_resource_step, ManifestStep)
    assert account_resource_step.required_token_id == "ais-account-access-basic"  # noqa: S105 - semantic token id
    detail_resource_step = manifest.steps[detail_resource_index]
    assert isinstance(detail_resource_step, ManifestStep)
    assert detail_resource_step.required_token_id == "ais-account-access-detail"  # noqa: S105 - semantic token id


def test_compiled_cbpii_manifest_uses_configured_debtor_account(tmp_path: Path) -> None:
    """CBPII consent body uses participant debtor-account config."""
    from conformance.catalogue import ImplementedEndpoint, TestPlanSpec, compile_test_plan
    from conformance.catalogues.cbpii import CBPII_CATALOGUE_KEY, CBPII_FCS_CATALOGUE
    from conformance.executor import _compiled_plan_to_manifest
    from conformance.manifest import JsonBody

    runtime_inputs = {
        "resourceBaseUrl": "https://resource.example.com",
        "debtorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
        "debtorAccountIdentification": "12345678901234",
        "debtorAccountName": "Model Bank Account",
    }
    spec = TestPlanSpec(
        schema_version="v1",
        catalogue_key=CBPII_CATALOGUE_KEY,
        security_profile="fapi1-advanced",
        implemented_endpoints=(
            ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/cbpii/funds-confirmation-consents",
                resource_group="Funds Confirmation",
            ),
        ),
        runtime_inputs=runtime_inputs,
    )
    compiled_plan = compile_test_plan(CBPII_FCS_CATALOGUE, spec)

    manifest = _compiled_plan_to_manifest(
        compiled_plan,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=tmp_path,
        runtime_config=RuntimeConfig(discovery_url="https://auth.example.com/.well-known/openid-configuration"),
    )

    cbpii_step = next(step for step in manifest.steps if step.id == "cbpii-consent-create-core-request")
    assert isinstance(cbpii_step, ManifestStep)
    body = cbpii_step.request.body
    assert isinstance(body, JsonBody)
    assert isinstance(body.value, dict)
    data = body.value["Data"]
    assert isinstance(data, dict)
    assert data["DebtorAccount"] == {
        "SchemeName": "UK.OBIE.SortCodeAccountNumber",
        "Identification": "12345678901234",
        "Name": "Model Bank Account",
    }
    expiration = data["ExpirationDateTime"]
    assert isinstance(expiration, str)
    parsed_expiration = datetime.fromisoformat(expiration.replace("Z", "+00:00"))
    assert parsed_expiration.time() == datetime.min.time()
    assert parsed_expiration.date() == (datetime.now(UTC) + timedelta(days=1)).date()


def test_compiled_cbpii_manifest_adds_access_token_setup_step(tmp_path: Path) -> None:
    """CBPII catalogue runs acquire the semantic funds-confirmation token at runtime.

    Args:
        tmp_path: Pytest temporary directory used as the runtime-input base.
    """
    from conformance.catalogue import ImplementedEndpoint, TestPlanSpec, compile_test_plan
    from conformance.catalogues.cbpii import CBPII_CATALOGUE_KEY, CBPII_FCS_CATALOGUE
    from conformance.executor import _compiled_plan_to_manifest
    from conformance.manifest import FormBody

    runtime_inputs = {
        "resourceBaseUrl": "https://resource.example.com",
        "debtorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
        "debtorAccountIdentification": "12345678901234",
        "debtorAccountName": "Model Bank Account",
    }
    spec = TestPlanSpec(
        schema_version="v1",
        catalogue_key=CBPII_CATALOGUE_KEY,
        security_profile="fapi1-advanced",
        implemented_endpoints=(
            ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/cbpii/funds-confirmation-consents",
                resource_group="Funds Confirmation",
            ),
        ),
        runtime_inputs=runtime_inputs,
    )
    compiled_plan = compile_test_plan(CBPII_FCS_CATALOGUE, spec)

    manifest = _compiled_plan_to_manifest(
        compiled_plan,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=tmp_path,
        runtime_config=RuntimeConfig(discovery_url="https://auth.example.com/.well-known/openid-configuration"),
    )

    token_step = manifest.steps[0]
    assert isinstance(token_step, ManifestStep)
    assert token_step.id == "setup-token-cbpii-client-credentials"
    assert token_step.phase == "setup"
    assert token_step.produces_token_id == "cbpii-client-credentials"  # noqa: S105 - semantic token id fixture
    assert token_step.token_endpoint_auth_policy is not None
    assert token_step.request.url == "${config.oauth.tokenEndpoint}"
    assert isinstance(token_step.request.body, FormBody)
    assert token_step.request.body.fields == {
        "grant_type": "client_credentials",
        "scope": "fundsconfirmations",
        "client_id": "${config.oauth.clientId}",
    }
    cbpii_step = next(step for step in manifest.steps if step.id == "cbpii-consent-create-core-request")
    assert isinstance(cbpii_step, ManifestStep)
    assert cbpii_step.request.headers is not None
    assert cbpii_step.required_token_id == "cbpii-client-credentials"  # noqa: S105 - semantic token id fixture
    assert cbpii_step.request.headers["Authorization"] == "Bearer ${tokens.cbpii-client-credentials.access_token}"


def test_compiled_cbpii_manifest_uses_v4_status_codes(tmp_path: Path) -> None:
    """CBPII v4 status assertions use OB status codes, not long-form labels.

    Args:
        tmp_path: Pytest temporary directory used as the runtime-input base.
    """
    from conformance.catalogue import ImplementedEndpoint, TestPlanSpec, compile_test_plan
    from conformance.catalogues.cbpii import CBPII_CATALOGUE_KEY, CBPII_FCS_CATALOGUE
    from conformance.executor import _compiled_plan_to_manifest
    from conformance.manifest import JsonFieldAssertion

    runtime_inputs = {
        "resourceBaseUrl": "https://resource.example.com",
        "debtorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
        "debtorAccountIdentification": "12345678901234",
        "debtorAccountName": "Model Bank Account",
    }
    spec = TestPlanSpec(
        schema_version="v1",
        catalogue_key=CBPII_CATALOGUE_KEY,
        security_profile="fapi1-advanced",
        implemented_endpoints=(
            ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/cbpii/funds-confirmation-consents",
                resource_group="Funds Confirmation",
            ),
            ImplementedEndpoint(
                method="GET",
                path="/open-banking/v4.0/cbpii/funds-confirmation-consents/{consentId}",
                resource_group="Funds Confirmation",
            ),
        ),
        runtime_inputs=runtime_inputs,
    )
    compiled_plan = compile_test_plan(CBPII_FCS_CATALOGUE, spec)

    manifest = _compiled_plan_to_manifest(
        compiled_plan,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=tmp_path,
        runtime_config=None,
    )

    create_step = next(step for step in manifest.steps if step.id == "cbpii-consent-create-core-request")
    get_step = next(step for step in manifest.steps if step.id == "cbpii-consent-get-authorised-request")
    assert isinstance(create_step, ManifestStep)
    assert isinstance(get_step, ManifestStep)
    create_status = next(
        assertion
        for assertion in create_step.assertions
        if isinstance(assertion, JsonFieldAssertion) and assertion.path == "Data.Status"
    )
    get_status = next(
        assertion
        for assertion in get_step.assertions
        if isinstance(assertion, JsonFieldAssertion) and assertion.path == "Data.Status"
    )
    assert create_status.value == "AWAU"
    assert get_status.value == "AUTH"


def test_compiled_cbpii_manifest_adds_authorisation_code_setup(tmp_path: Path) -> None:
    """CBPII funds confirmations use a PSU-authorised token bound to ConsentId.

    Args:
        tmp_path: Pytest temporary directory used as the runtime-input base.
    """
    from conformance.catalogue import ImplementedEndpoint, TestPlanSpec, compile_test_plan
    from conformance.catalogues.cbpii import CBPII_CATALOGUE_KEY, CBPII_FCS_CATALOGUE
    from conformance.executor import _compiled_plan_to_manifest
    from conformance.manifest import (
        FormBody,
        JsonBody,
        ManifestStep,
    )

    runtime_inputs = {
        "resourceBaseUrl": "https://resource.example.com",
        "debtorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
        "debtorAccountIdentification": "12345678901234",
        "debtorAccountName": "Model Bank Account",
    }
    spec = TestPlanSpec(
        schema_version="v1",
        catalogue_key=CBPII_CATALOGUE_KEY,
        security_profile="fapi1-advanced",
        implemented_endpoints=(
            ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/cbpii/funds-confirmation-consents",
                resource_group="Funds Confirmation",
            ),
            ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/cbpii/funds-confirmations",
                resource_group="Funds Confirmation",
            ),
        ),
        runtime_inputs=runtime_inputs,
    )
    compiled_plan = compile_test_plan(CBPII_FCS_CATALOGUE, spec)

    manifest = _compiled_plan_to_manifest(
        compiled_plan,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=tmp_path,
        runtime_config=None,
    )

    step_ids = [step.id for step in manifest.steps]
    create_index = step_ids.index("cbpii-consent-create-core-request")
    psu_step = manifest.steps[create_index + 1]
    token_step = manifest.steps[create_index + 2]
    assert isinstance(psu_step, PsuAuthorizationStep)
    assert psu_step.id == "setup-cbpii-consent-authorisation"
    assert psu_step.scope == "openid fundsconfirmations"
    assert isinstance(psu_step.request_object, GeneratedRequestObject)
    assert psu_step.request_object.audience == "${config.oauth.issuer}"
    assert psu_step.request_object.openbanking_intent_id == (
        "${steps.cbpii-consent-create-core-request.response.body.Data.ConsentId}"
    )

    assert isinstance(token_step, ManifestStep)
    assert token_step.id == "setup-token-cbpii-funds-confirmation"
    assert token_step.produces_token_id == "cbpii-funds-confirmation"  # noqa: S105 - semantic token id fixture
    assert isinstance(token_step.request.body, FormBody)
    assert token_step.request.body.fields["grant_type"] == "authorization_code"
    assert token_step.request.body.fields["code"] == ("${steps.setup-cbpii-consent-authorisation.response.body.code}")

    funds_confirmation_step = next(
        step for step in manifest.steps if step.id == "cbpii-funds-confirmation-create-request"
    )
    assert isinstance(funds_confirmation_step, ManifestStep)
    assert funds_confirmation_step.required_token_id == "cbpii-funds-confirmation"  # noqa: S105 - semantic token id fixture
    assert isinstance(funds_confirmation_step.request.body, JsonBody)
    funds_confirmation_body = funds_confirmation_step.request.body.value
    assert isinstance(funds_confirmation_body, dict)
    funds_confirmation_data = funds_confirmation_body["Data"]
    assert isinstance(funds_confirmation_data, dict)
    cbpii_reference = funds_confirmation_data["Reference"]
    assert isinstance(cbpii_reference, str)
    assert len(cbpii_reference) == 32


def test_compiled_cbpii_manifest_preserves_captured_consent_id_url(tmp_path: Path) -> None:
    """CBPII dependent resource URLs keep captured consent-id placeholders.

    Args:
        tmp_path: Pytest temporary directory used as the runtime-input base.
    """
    from conformance.catalogue import ImplementedEndpoint, TestPlanSpec, compile_test_plan
    from conformance.catalogues.cbpii import CBPII_CATALOGUE_KEY, CBPII_FCS_CATALOGUE
    from conformance.executor import _compiled_plan_to_manifest

    runtime_inputs = {
        "resourceBaseUrl": "https://resource.example.com",
        "debtorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
        "debtorAccountIdentification": "12345678901234",
        "debtorAccountName": "Model Bank Account",
    }
    spec = TestPlanSpec(
        schema_version="v1",
        catalogue_key=CBPII_CATALOGUE_KEY,
        security_profile="fapi1-advanced",
        implemented_endpoints=(
            ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/cbpii/funds-confirmation-consents",
                resource_group="Funds Confirmation",
            ),
            ImplementedEndpoint(
                method="DELETE",
                path="/open-banking/v4.0/cbpii/funds-confirmation-consents/{consentId}",
                resource_group="Funds Confirmation",
            ),
        ),
        runtime_inputs=runtime_inputs,
    )
    compiled_plan = compile_test_plan(CBPII_FCS_CATALOGUE, spec)

    manifest = _compiled_plan_to_manifest(
        compiled_plan,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=tmp_path,
        runtime_config=None,
    )

    urls_by_step_id = {step.id: step.request.url for step in manifest.steps if isinstance(step, ManifestStep)}
    assert urls_by_step_id["cbpii-consent-delete-request"] == (
        "https://resource.example.com/open-banking/v4.0/cbpii/funds-confirmation-consents/"
        "${steps.cbpii-consent-create-core-request.response.body.Data.ConsentId}"
    )
