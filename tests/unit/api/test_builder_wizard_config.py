"""Builder wizard runtime, business, and visibility configuration derived from scope."""

from __future__ import annotations

import pytest
from django.contrib.sessions.backends.signed_cookies import SessionStore

from conformance.api.builder_draft_store import SessionBuilderDraftStore
from conformance.api.builder_wizard import (
    BusinessConfigForm,
    ConfigVisibility,
    ExecutionConfigForm,
    business_config_form_initial,
    catalogue_scope_hierarchy,
    config_visibility_for_draft,
    merge_participant_business_config,
    participant_plan_from_draft,
    plan_document_from_draft,
    runtime_input_prompts_for_draft,
)
from conformance.catalogue import PlanDocumentBoundary
from conformance.json_types import JsonObject, JsonValue

pytestmark = pytest.mark.unit

DISCOVERY_CONFIG = {"discoveryUrl": "https://example.com/.well-known/openid-configuration"}
"""Minimal discovery config needed to build canonical draft documents."""


def test_grouped_config_form_builds_runtime_inputs_for_selected_scope() -> None:
    """The config form stores catalogue runtime inputs under v2 config inputs."""
    session = SessionStore()
    store = SessionBuilderDraftStore(session)
    draft = store.create().with_catalogue_boundary(
        scheme="open-banking-uk",
        specification="read-write",
        version="4.0.1",
    )
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
    hierarchy = catalogue_scope_hierarchy(
        boundary,
        selected_resource_group_ids=("account-and-transaction",),
    )
    endpoint = next(
        endpoint
        for group in hierarchy.resource_groups
        for endpoint in group.endpoints
        if endpoint.path == "/open-banking/v4.0/aisp/accounts"
    )
    draft = draft.with_scope_selection(
        resource_group_ids=("account-and-transaction",),
        endpoint_ids=(endpoint.id,),
        endpoint_capability_ids={},
    ).with_config(
        config=DISCOVERY_CONFIG,
    )

    prompts = runtime_input_prompts_for_draft(draft)
    form = ExecutionConfigForm(
        data={
            "discovery_url": "https://example.com/.well-known/openid-configuration",
            "runtime_input__resourceBaseUrl": "https://resource.example.com",
        },
        runtime_prompts=prompts,
    )

    assert form.is_valid(), form.errors.as_json()
    assert form.config is not None
    assert form.config["inputs"] == {
        "resourceBaseUrl": {"value": "https://resource.example.com"},
    }

    document = plan_document_from_draft(draft.with_config(config=form.config))
    assert document.runtime_inputs["resourceBaseUrl"] == "https://resource.example.com"
    assert "accessToken" not in document.runtime_inputs


def test_runtime_prompt_labels_follow_selected_endpoint_scope() -> None:
    """PIS-only runtime prompts do not inherit AIS-specific labels."""
    draft = (
        SessionBuilderDraftStore(SessionStore())
        .create()
        .with_catalogue_boundary(
            scheme="open-banking-uk",
            specification="read-write",
            version="4.0.1",
        )
    )
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
    hierarchy = catalogue_scope_hierarchy(
        boundary,
        selected_resource_group_ids=("payment-initiation",),
    )
    endpoint = next(
        endpoint
        for group in hierarchy.resource_groups
        for endpoint in group.endpoints
        if endpoint.path == "/open-banking/v4.0/pisp/domestic-payments"
    )
    draft = draft.with_scope_selection(
        resource_group_ids=("payment-initiation",),
        endpoint_ids=(endpoint.id,),
        endpoint_capability_ids={},
    ).with_config(
        config=DISCOVERY_CONFIG,
    )

    prompts = runtime_input_prompts_for_draft(draft)
    labels_by_id = {prompt.input_id: prompt.label for prompt in prompts}
    groups_by_id = {prompt.input_id: prompt.group for prompt in prompts}

    assert labels_by_id["resourceBaseUrl"] == "Resource server base URL"
    assert "xFapiCustomerIpAddress" not in labels_by_id
    assert "Request metadata and headers" not in groups_by_id.values()
    assert "AIS resource server base URL" not in labels_by_id.values()


def test_grouped_config_form_preserves_legacy_fcs_functional_defaults() -> None:
    """The v2 config form keeps default-backed FCS values in structured sections."""
    form = ExecutionConfigForm(
        data={
            "discovery_url": "https://auth.example.com/.well-known/openid-configuration",
            "oauth_client_id": "client-123",
            "oauth_redirect_uri": "https://client.example.com/callback",
            "oauth_issuer": "https://auth.example.com",
            "oauth_token_endpoint": "https://auth.example.com/token",
            "oauth_response_type": "code id_token",
            "oauth_request_object_signing_alg": "PS256",
            "resource_server_base_url": "https://resource.example.com",
            "ais_resource_ids_json": '{"accountIds": [{"accountId": "account-123"}]}',
            "ais_transaction_from_date": "2026-01-01T00:00:00Z",
            "ais_transaction_to_date": "2026-01-31T23:59:59Z",
            "pis_creditor_account_json": '{"schemeName": "UK.OBIE.SortCodeAccountNumber"}',
            "pis_international_creditor_account_json": '{"schemeName": "UK.OBIE.IBAN"}',
            "pis_instructed_amount_json": '{"amount": "10.00", "currency": "GBP"}',
            "pis_currency_of_transfer": "GBP",
            "pis_requested_execution_date_time": "2026-02-01T00:00:00Z",
            "pis_first_payment_date_time": "2026-02-02T00:00:00Z",
            "pis_standing_order_frequency_json": '{"type": "Evry", "pointInTime": "01"}',
            "cbpii_debtor_account_json": (
                '{"schemeName": "UK.OBIE.SortCodeAccountNumber", '
                '"identification": "12345678901234", "name": "Model Bank Account"}'
            ),
            "conditional_properties_json": '[{"id": "standing-order.number-of-payments"}]',
        },
        runtime_prompts=(),
    )

    assert form.is_valid(), form.errors.as_json()
    assert form.config is not None
    assert form.config["oauth"] == {
        "clientId": "client-123",
        "redirectUri": "https://client.example.com/callback",
        "issuer": "https://auth.example.com",
        "tokenEndpoint": "https://auth.example.com/token",
        "responseType": "code id_token",
        "requestObjectSigningAlg": "PS256",
    }
    assert form.config["resourceServer"] == {"baseUrl": "https://resource.example.com"}
    assert "clientCredentials" not in form.config
    assert "openBanking" not in form.config
    pis = form.config["pis"]
    assert isinstance(pis, dict)
    assert pis["standingOrderFrequency"] == {"type": "Evry", "pointInTime": "01"}
    cbpii = form.config["cbpii"]
    assert isinstance(cbpii, dict)
    debtor_account = cbpii["debtorAccount"]
    assert isinstance(debtor_account, dict)
    assert debtor_account["identification"] == "12345678901234"
    assert form.config["conditionalProperties"] == [{"id": "standing-order.number-of-payments"}]


def test_grouped_config_form_omits_resource_server_for_unchecked_customer_ip_toggle() -> None:
    """Unchecked customer-IP toggle alone does not create a resourceServer config."""
    form = ExecutionConfigForm(data={})

    assert form.is_valid(), form.errors.as_json()
    assert form.config is not None
    assert "resourceServer" not in form.config


def test_structured_config_values_remove_duplicate_runtime_prompts() -> None:
    """Runtime prompts do not duplicate values already supplied by grouped config."""
    draft = (
        SessionBuilderDraftStore(SessionStore())
        .create()
        .with_catalogue_boundary(
            scheme="open-banking-uk",
            specification="read-write",
            version="4.0.1",
        )
    )
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
    hierarchy = catalogue_scope_hierarchy(
        boundary,
        selected_resource_group_ids=("account-and-transaction",),
    )
    endpoint = next(
        endpoint
        for group in hierarchy.resource_groups
        for endpoint in group.endpoints
        if endpoint.path == "/open-banking/v4.0/aisp/accounts/{AccountId}"
    )
    draft = draft.with_scope_selection(
        resource_group_ids=("account-and-transaction",),
        endpoint_ids=(endpoint.id,),
        endpoint_capability_ids={},
    ).with_config(
        config={
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "resourceServer": {"baseUrl": "https://resource.example.com"},
            "ais": {"resourceIds": {"accountIds": [{"accountId": "account-123"}]}},
        }
    )

    document = plan_document_from_draft(draft)
    prompts = runtime_input_prompts_for_draft(draft)

    assert document.runtime_inputs["resourceBaseUrl"] == "https://resource.example.com"
    assert document.runtime_inputs["consentedAccountId"] == "account-123"
    assert "resourceBaseUrl" not in {prompt.input_id for prompt in prompts}
    assert "consentedAccountId" not in {prompt.input_id for prompt in prompts}
    assert "accessToken" not in {prompt.input_id for prompt in prompts}


def test_config_visibility_uses_selected_endpoint_apis() -> None:
    """The grouped config page visibility follows selected endpoint API families."""
    draft = (
        SessionBuilderDraftStore(SessionStore())
        .create()
        .with_catalogue_boundary(
            scheme="open-banking-uk",
            specification="read-write",
            version="4.0.1",
        )
    )
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
    hierarchy = catalogue_scope_hierarchy(
        boundary,
        selected_resource_group_ids=("account-and-transaction", "payment-initiation", "confirmation-of-funds"),
    )
    ais_endpoint = next(
        endpoint
        for group in hierarchy.resource_groups
        for endpoint in group.endpoints
        if endpoint.path == "/open-banking/v4.0/aisp/accounts"
    )
    draft = draft.with_scope_selection(
        resource_group_ids=("account-and-transaction", "payment-initiation", "confirmation-of-funds"),
        endpoint_ids=(ais_endpoint.id,),
        endpoint_capability_ids={},
    ).with_config(
        config=DISCOVERY_CONFIG,
    )

    visibility = config_visibility_for_draft(draft)

    assert visibility.selected_api_ids == frozenset({"ais"})
    assert visibility.show_ais is True
    assert visibility.show_pis is False
    assert visibility.show_cbpii is False
    assert visibility.show_business_defaults is True
    assert visibility.ais_account_id_required is False


def test_config_visibility_requires_ais_account_id_for_account_scoped_endpoints() -> None:
    """AIS account-scoped endpoint selections mark the account id as required."""
    draft = (
        SessionBuilderDraftStore(SessionStore())
        .create()
        .with_catalogue_boundary(
            scheme="open-banking-uk",
            specification="read-write",
            version="4.0.1",
        )
    )
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
    hierarchy = catalogue_scope_hierarchy(
        boundary,
        selected_resource_group_ids=("account-and-transaction",),
    )
    balances_endpoint = next(
        endpoint
        for group in hierarchy.resource_groups
        for endpoint in group.endpoints
        if endpoint.path == "/open-banking/v4.0/aisp/accounts/{AccountId}/balances"
    )
    draft = draft.with_scope_selection(
        resource_group_ids=("account-and-transaction",),
        endpoint_ids=(balances_endpoint.id,),
        endpoint_capability_ids={},
    ).with_config(
        config=DISCOVERY_CONFIG,
    )

    visibility = config_visibility_for_draft(draft)
    missing_form = BusinessConfigForm(data={}, config_visibility=visibility)
    advanced_form = BusinessConfigForm(
        data={"ais_resource_ids_json": '{"accountIds": [{"accountId": "account-123"}]}'},
        config_visibility=visibility,
    )

    assert visibility.ais_account_id_required is True
    assert missing_form.is_valid() is False
    assert "required for selected account-scoped AIS endpoints" in missing_form.errors["ais_consented_account_id"][0]
    assert advanced_form.is_valid(), advanced_form.errors.as_json()
    assert advanced_form.config == {"ais": {"resourceIds": {"accountIds": [{"accountId": "account-123"}]}}}


def test_config_visibility_restores_cbpii_business_defaults() -> None:
    """CBPII endpoint selections show the Confirmation of Funds business fields."""
    draft = (
        SessionBuilderDraftStore(SessionStore())
        .create()
        .with_catalogue_boundary(
            scheme="open-banking-uk",
            specification="read-write",
            version="4.0.1",
        )
    )
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
    hierarchy = catalogue_scope_hierarchy(
        boundary,
        selected_resource_group_ids=("confirmation-of-funds",),
    )
    cbpii_endpoint = next(
        endpoint
        for group in hierarchy.resource_groups
        for endpoint in group.endpoints
        if endpoint.path == "/open-banking/v4.0/cbpii/funds-confirmation-consents"
    )
    draft = draft.with_scope_selection(
        resource_group_ids=("confirmation-of-funds",),
        endpoint_ids=(cbpii_endpoint.id,),
        endpoint_capability_ids={},
    ).with_config(config=DISCOVERY_CONFIG)

    visibility = config_visibility_for_draft(draft)

    assert visibility.selected_api_ids == frozenset({"cbpii"})
    assert visibility.show_ais is False
    assert visibility.show_pis is False
    assert visibility.show_cbpii is True
    assert visibility.show_business_defaults is True


def test_config_visibility_classifies_v311_pisp_vrp_paths_as_vrp() -> None:
    """Show only VRP business fields for v3.1 VRP resources under ``pisp``."""
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "3.1.11")
    hierarchy = catalogue_scope_hierarchy(
        boundary,
        selected_resource_group_ids=("variable-recurring-payments",),
    )
    vrp_endpoint = next(
        endpoint
        for group in hierarchy.resource_groups
        for endpoint in group.endpoints
        if endpoint.method == "POST" and endpoint.path == "/open-banking/v3.1/pisp/domestic-vrp-consents"
    )
    draft = (
        SessionBuilderDraftStore(SessionStore())
        .create()
        .with_catalogue_boundary(
            scheme="open-banking-uk",
            specification="read-write",
            version="3.1.11",
        )
        .with_scope_selection(
            resource_group_ids=("variable-recurring-payments",),
            endpoint_ids=(vrp_endpoint.id,),
            endpoint_capability_ids={},
        )
        .with_config(config=DISCOVERY_CONFIG)
    )

    visibility = config_visibility_for_draft(draft)
    form = BusinessConfigForm(data={}, config_visibility=visibility)

    assert visibility.selected_api_ids == frozenset({"vrp"})
    assert visibility.show_ais is False
    assert visibility.show_pis is False
    assert visibility.show_cbpii is False
    assert visibility.show_vrp is True
    assert visibility.show_business_defaults is True
    assert form.is_valid() is False
    assert "vrp_creditor_account_scheme_name" in form.errors
    assert "pis_creditor_account_scheme_name" not in form.errors


def test_config_visibility_restores_pis_business_defaults() -> None:
    """PIS endpoint selections show payment business defaults."""
    draft = (
        SessionBuilderDraftStore(SessionStore())
        .create()
        .with_catalogue_boundary(
            scheme="open-banking-uk",
            specification="read-write",
            version="4.0.1",
        )
    )
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
    hierarchy = catalogue_scope_hierarchy(
        boundary,
        selected_resource_group_ids=("payment-initiation",),
    )
    pis_endpoint = next(
        endpoint
        for group in hierarchy.resource_groups
        for endpoint in group.endpoints
        if endpoint.path == "/open-banking/v4.0/pisp/domestic-payments"
    )
    draft = draft.with_scope_selection(
        resource_group_ids=("payment-initiation",),
        endpoint_ids=(pis_endpoint.id,),
        endpoint_capability_ids={},
    ).with_config(config=DISCOVERY_CONFIG)

    visibility = config_visibility_for_draft(draft)

    assert visibility.selected_api_ids == frozenset({"pis"})
    assert visibility.show_ais is False
    assert visibility.show_pis is True
    assert visibility.show_cbpii is False
    assert visibility.show_business_defaults is True
    assert visibility.pis_domestic_creditor_account_required is True
    assert visibility.pis_instructed_amount_required is True
    assert visibility.pis_international_creditor_account_required is False
    assert visibility.pis_requested_execution_date_time_required is False


def test_config_visibility_marks_all_pis_business_inputs_for_all_pis_endpoints() -> None:
    """Selecting all PIS endpoints marks every PIS product-family input required."""
    draft = (
        SessionBuilderDraftStore(SessionStore())
        .create()
        .with_catalogue_boundary(
            scheme="open-banking-uk",
            specification="read-write",
            version="4.0.1",
        )
    )
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
    hierarchy = catalogue_scope_hierarchy(
        boundary,
        selected_resource_group_ids=("payment-initiation",),
    )
    endpoint_ids = tuple(endpoint.id for group in hierarchy.resource_groups for endpoint in group.endpoints)
    draft = draft.with_scope_selection(
        resource_group_ids=("payment-initiation",),
        endpoint_ids=endpoint_ids,
        endpoint_capability_ids={},
    ).with_config(config=DISCOVERY_CONFIG)

    visibility = config_visibility_for_draft(draft)

    assert visibility.show_pis is True
    assert visibility.pis_domestic_creditor_account_required is True
    assert visibility.pis_international_creditor_account_required is True
    assert visibility.pis_instructed_amount_required is True
    assert visibility.pis_currency_of_transfer_required is True
    assert visibility.pis_requested_execution_date_time_required is True
    assert visibility.pis_first_payment_date_time_required is True
    assert visibility.pis_standing_order_frequency_required is True


@pytest.mark.parametrize("version", ["3.1.11", "4.0.1"])
@pytest.mark.parametrize("scope", ["ais", "pis", "cbpii", "vrp"])
def test_business_config_initial_hydrates_canonical_participant_inputs(
    scope: str,
    version: str,
) -> None:
    """Canonical participant inputs hydrate every Business page scope."""
    version_id = {"3.1.11": "v311", "4.0.1": "v401"}[version]
    values_by_scope: dict[str, dict[str, JsonValue]] = {
        "ais": {
            "consentedAccountId": "account-123",
            f"ais.{version_id}.input.from-booking-date-time": "2026-01-01T00:00:00Z",
            f"ais.{version_id}.input.to-booking-date-time": "2026-01-31T00:00:00Z",
        },
        "pis": {
            f"pis.{version_id}.input.creditor-account-scheme-name": "UK.OBIE.SortCodeAccountNumber",
            f"pis.{version_id}.input.creditor-account-identification": "08080021325698",
            f"pis.{version_id}.input.creditor-account-name": "Merchant",
            f"pis.{version_id}.input.instructed-amount": "10.00",
            f"pis.{version_id}.input.instructed-currency": "GBP",
        },
        "cbpii": {
            f"cbpii.{version_id}.input.debtor-account-scheme": "UK.OBIE.SortCodeAccountNumber",
            f"cbpii.{version_id}.input.debtor-account-identification": "08080021325698",
            f"cbpii.{version_id}.input.debtor-account-name": "Debtor",
        },
        "vrp": {
            f"vrp.{version_id}.input.creditor-account-scheme-name": "UK.OBIE.SortCodeAccountNumber",
            f"vrp.{version_id}.input.creditor-account-identification": "70000170000005",
            f"vrp.{version_id}.input.creditor-account-name": "VRP creditor",
            f"vrp.{version_id}.input.instructed-amount": "1.00",
            f"vrp.{version_id}.input.currency": "GBP",
            f"vrp.{version_id}.input.valid-from-date-time": "2026-09-15T00:00:00Z",
            f"vrp.{version_id}.input.valid-to-date-time": "2026-10-15T00:00:00Z",
        },
    }
    expected_by_scope: dict[str, dict[str, str]] = {
        "ais": {
            "ais_consented_account_id": "account-123",
            "ais_transaction_from_date": "2026-01-01T00:00:00Z",
            "ais_transaction_to_date": "2026-01-31T00:00:00Z",
        },
        "pis": {
            "pis_creditor_account_scheme_name": "UK.OBIE.SortCodeAccountNumber",
            "pis_creditor_account_identification": "08080021325698",
            "pis_creditor_account_name": "Merchant",
            "pis_instructed_amount_amount": "10.00",
            "pis_instructed_amount_currency": "GBP",
        },
        "cbpii": {
            "cbpii_debtor_account_scheme_name": "UK.OBIE.SortCodeAccountNumber",
            "cbpii_debtor_account_identification": "08080021325698",
            "cbpii_debtor_account_name": "Debtor",
        },
        "vrp": {
            "vrp_creditor_account_scheme_name": "UK.OBIE.SortCodeAccountNumber",
            "vrp_creditor_account_identification": "70000170000005",
            "vrp_creditor_account_name": "VRP creditor",
            "vrp_instructed_amount_amount": "1.00",
            "vrp_instructed_amount_currency": "GBP",
            "vrp_valid_from_date_time": "2026-09-15T00:00:00Z",
            "vrp_valid_to_date_time": "2026-10-15T00:00:00Z",
        },
    }
    config: JsonObject = {"inputs": {input_id: {"value": value} for input_id, value in values_by_scope[scope].items()}}

    initial = business_config_form_initial(
        config,
        test_scope=scope,
        specification_version=version,
    )

    for field_name, expected_value in expected_by_scope[scope].items():
        assert initial[field_name] == expected_value


@pytest.mark.parametrize(
    ("version", "frequency", "expected_field", "expected_value"),
    [
        ("3.1.11", "IntrvlWkDay:01:03", "pis_standing_order_frequency_v311", "IntrvlWkDay:01:03"),
        (
            "4.0.1",
            {"frequencyType": "WEEK", "pointInTime": "03"},
            "pis_standing_order_frequency_type",
            "WEEK",
        ),
    ],
)
def test_business_config_initial_hydrates_versioned_pis_frequency(
    version: str,
    frequency: JsonValue,
    expected_field: str,
    expected_value: str,
) -> None:
    """PIS standing-order frequency retains its version-specific form."""
    version_id = {"3.1.11": "v311", "4.0.1": "v401"}[version]

    config: JsonObject = {
        "inputs": {
            f"pis.{version_id}.input.standing-order-frequency": {
                "value": frequency,
            }
        }
    }
    initial = business_config_form_initial(
        config,
        test_scope="pis",
        specification_version=version,
    )

    assert initial[expected_field] == expected_value
    if version == "4.0.1":
        assert initial["pis_standing_order_frequency_point_in_time"] == "03"
        assert initial["pis_standing_order_frequency_json"] is None


def test_business_config_initial_uses_json_only_for_unrepresentable_objects() -> None:
    """Advanced JSON cannot silently override lossless friendly controls."""
    friendly = business_config_form_initial(
        {
            "cbpii": {
                "debtorAccount": {
                    "schemeName": "UK.OBIE.SortCodeAccountNumber",
                    "identification": "08080021325698",
                    "name": "Debtor",
                }
            }
        }
    )
    advanced = business_config_form_initial(
        {
            "cbpii": {
                "debtorAccount": {
                    "schemeName": "UK.OBIE.SortCodeAccountNumber",
                    "identification": "08080021325698",
                    "name": "Debtor",
                    "secondaryIdentification": "secondary",
                }
            }
        }
    )

    assert friendly["cbpii_debtor_account_name"] == "Debtor"
    assert friendly["cbpii_debtor_account_json"] is None
    assert advanced["cbpii_debtor_account_name"] is None
    assert '"secondaryIdentification": "secondary"' in str(advanced["cbpii_debtor_account_json"])


def test_business_config_merge_replaces_owned_inputs_and_preserves_runtime_inputs() -> None:
    """Business saves replace debtor inputs without touching CBPII amount data."""
    original: JsonObject = {
        "inputs": {
            "cbpii.v401.input.debtor-account-scheme": {"value": "old-scheme"},
            "cbpii.v401.input.debtor-account-identification": {"value": "old-identification"},
            "cbpii.v401.input.debtor-account-name": {"value": "old-name"},
            "cbpii.v401.input.instructed-amount": {"value": "10.00"},
            "cbpii.v401.input.instructed-currency": {"value": "GBP"},
            "unrelated": {"value": "preserved"},
        }
    }
    section: JsonObject = {
        "cbpii": {
            "debtorAccount": {
                "schemeName": "new-scheme",
                "identification": "new-identification",
                "name": "new-name",
            }
        }
    }

    updated = merge_participant_business_config(
        original,
        section,
        test_scope="cbpii",
        specification_version="4.0.1",
    )

    assert updated["inputs"] == {
        "cbpii.v401.input.debtor-account-scheme": {"value": "new-scheme"},
        "cbpii.v401.input.debtor-account-identification": {"value": "new-identification"},
        "cbpii.v401.input.debtor-account-name": {"value": "new-name"},
        "cbpii.v401.input.instructed-amount": {"value": "10.00"},
        "cbpii.v401.input.instructed-currency": {"value": "GBP"},
        "unrelated": {"value": "preserved"},
    }


def test_business_config_merge_removes_cleared_optional_inputs() -> None:
    """Clearing optional AIS defaults removes stale canonical values."""
    updated = merge_participant_business_config(
        {
            "inputs": {
                "consentedAccountId": {"value": "account-123"},
                "ais.v401.input.from-booking-date-time": {"value": "2026-01-01T00:00:00Z"},
                "ais.v401.input.to-booking-date-time": {"value": "2026-01-31T00:00:00Z"},
                "unrelated": {"value": "preserved"},
            }
        },
        {},
        test_scope="ais",
        specification_version="4.0.1",
    )

    assert updated["inputs"] == {"unrelated": {"value": "preserved"}}


@pytest.mark.parametrize(
    ("scope", "capabilities", "config", "expected_input_count"),
    [
        (
            "ais",
            ("ais.v401.capability.accounts",),
            {
                "ais": {
                    "resourceIds": {"accountIds": [{"accountId": "account-123"}]},
                    "transactionFromDate": "2026-01-01T00:00:00Z",
                    "transactionToDate": "2026-01-31T00:00:00Z",
                }
            },
            2,
        ),
        (
            "vrp",
            (
                "vrp.v401.capability.domestic-vrp",
                "vrp.v401.capability.funds-confirmation",
                "vrp.v401.capability.consent-version-replacement",
                "vrp.v401.capability.consent-version-patch",
            ),
            {
                "vrp": {
                    "creditorAccount": {
                        "schemeName": "UK.OBIE.SortCodeAccountNumber",
                        "identification": "70000170000005",
                        "name": "VRP creditor",
                    },
                    "instructedAmount": {"amount": "1.00", "currency": "GBP"},
                    "validFromDateTime": "2026-09-15T00:00:00Z",
                    "validToDateTime": "2026-10-15T00:00:00Z",
                }
            },
            7,
        ),
    ],
)
def test_participant_plan_promotes_all_grouped_business_inputs(
    scope: str,
    capabilities: tuple[str, ...],
    config: dict[str, JsonValue],
    expected_input_count: int,
) -> None:
    """AIS and VRP grouped values export under canonical participant ids."""
    draft = (
        SessionBuilderDraftStore(SessionStore())
        .create()
        .with_catalogue_boundary(
            scheme="open-banking-uk",
            specification="read-write",
            version="4.0.1",
        )
        .with_scope_selection(
            resource_group_ids=(scope,),
            endpoint_ids=capabilities,
            endpoint_capability_ids={},
        )
        .with_config(config=config)
    )

    plan = participant_plan_from_draft(draft)

    assert len(plan.predefined_inputs) == expected_input_count
    assert plan.execution_configuration is not None
    compatibility_inputs = plan.execution_configuration.compatibility_runtime_inputs
    assert not any(input_id.startswith(("fromBooking", "toBooking", "vrp")) for input_id in compatibility_inputs)


def test_business_config_form_requires_cbpii_debtor_account_values() -> None:
    """CBPII business config serializes debtor account values into structured config."""
    form = BusinessConfigForm(
        data={
            "cbpii_debtor_account_scheme_name": "UK.OBIE.SortCodeAccountNumber",
            "cbpii_debtor_account_identification": "12345678901234",
            "cbpii_debtor_account_name": "Model Bank Account",
        },
        config_visibility=ConfigVisibility(
            selected_api_ids=frozenset({"cbpii"}),
            show_ais=False,
            show_pis=False,
            show_cbpii=True,
            show_vrp=False,
            show_business_defaults=True,
        ),
    )

    assert form.is_valid(), form.errors.as_json()
    assert form.config == {
        "cbpii": {
            "debtorAccount": {
                "schemeName": "UK.OBIE.SortCodeAccountNumber",
                "identification": "12345678901234",
                "name": "Model Bank Account",
            }
        }
    }


def test_business_config_form_requires_vrp_business_values() -> None:
    """VRP business config serializes creditor, amount, and validity defaults."""
    visibility = ConfigVisibility(
        selected_api_ids=frozenset({"vrp"}),
        show_ais=False,
        show_pis=False,
        show_cbpii=False,
        show_vrp=True,
        show_business_defaults=True,
    )
    missing_form = BusinessConfigForm(data={}, config_visibility=visibility)
    form = BusinessConfigForm(
        data={
            "vrp_creditor_account_scheme_name": "UK.OBIE.SortCodeAccountNumber",
            "vrp_creditor_account_identification": "70000170000002",
            "vrp_creditor_account_name": "VRP creditor",
            "vrp_instructed_amount_amount": "1.00",
            "vrp_instructed_amount_currency": "GBP",
            "vrp_valid_from_date_time": "2026-08-27T00:00:00+00:00",
            "vrp_valid_to_date_time": "2026-09-27T00:00:00+00:00",
        },
        config_visibility=visibility,
    )

    assert missing_form.is_valid() is False
    assert form.is_valid(), form.errors.as_json()
    assert form.config == {
        "vrp": {
            "validFromDateTime": "2026-08-27T00:00:00+00:00",
            "validToDateTime": "2026-09-27T00:00:00+00:00",
            "creditorAccount": {
                "schemeName": "UK.OBIE.SortCodeAccountNumber",
                "identification": "70000170000002",
                "name": "VRP creditor",
            },
            "instructedAmount": {"amount": "1.00", "currency": "GBP"},
        }
    }


def test_business_config_form_requires_selected_pis_values() -> None:
    """PIS validation requires selected product-family payment defaults."""
    visibility = ConfigVisibility(
        selected_api_ids=frozenset({"pis"}),
        show_ais=False,
        show_pis=True,
        show_cbpii=False,
        show_vrp=False,
        show_business_defaults=True,
        pis_domestic_creditor_account_required=True,
        pis_instructed_amount_required=True,
    )

    missing_form = BusinessConfigForm(data={}, config_visibility=visibility)
    json_fallback_form = BusinessConfigForm(
        data={
            "pis_creditor_account_json": (
                '{"schemeName": "UK.OBIE.SortCodeAccountNumber", '
                '"identification": "12345678901234", "name": "Model Bank Account"}'
            ),
            "pis_instructed_amount_json": '{"amount": "10.00", "currency": "GBP"}',
        },
        config_visibility=visibility,
    )

    assert missing_form.is_valid() is False
    assert "required for selected PIS endpoints" in missing_form.errors["pis_creditor_account_scheme_name"][0]
    assert "required for selected PIS endpoints" in missing_form.errors["pis_instructed_amount_amount"][0]
    assert json_fallback_form.is_valid(), json_fallback_form.errors.as_json()
    assert json_fallback_form.config == {
        "pis": {
            "creditorAccount": {
                "schemeName": "UK.OBIE.SortCodeAccountNumber",
                "identification": "12345678901234",
                "name": "Model Bank Account",
            },
            "instructedAmount": {"amount": "10.00", "currency": "GBP"},
        }
    }


def test_business_config_form_requires_only_selected_pis_family() -> None:
    """International PIS requiredness does not require domestic-only fields."""
    visibility = ConfigVisibility(
        selected_api_ids=frozenset({"pis"}),
        show_ais=False,
        show_pis=True,
        show_cbpii=False,
        show_vrp=False,
        show_business_defaults=True,
        pis_international_creditor_account_required=True,
        pis_instructed_amount_required=True,
        pis_currency_of_transfer_required=True,
    )
    form = BusinessConfigForm(
        data={
            "pis_international_creditor_account_scheme_name": "UK.OBIE.IBAN",
            "pis_international_creditor_account_identification": "GB29NWBK60161331926819",
            "pis_international_creditor_account_name": "International Creditor",
            "pis_instructed_amount_amount": "10.00",
            "pis_instructed_amount_currency": "GBP",
            "pis_currency_of_transfer": "GBP",
        },
        config_visibility=visibility,
    )

    assert form.is_valid(), form.errors.as_json()
    assert form.config == {
        "pis": {
            "currencyOfTransfer": "GBP",
            "internationalCreditorAccount": {
                "schemeName": "UK.OBIE.IBAN",
                "identification": "GB29NWBK60161331926819",
                "name": "International Creditor",
            },
            "instructedAmount": {"amount": "10.00", "currency": "GBP"},
        }
    }


def test_scoped_config_form_prunes_out_of_scope_business_defaults() -> None:
    """Grouped config serialization ignores stale values outside selected scope."""
    draft = (
        SessionBuilderDraftStore(SessionStore())
        .create()
        .with_catalogue_boundary(
            scheme="open-banking-uk",
            specification="read-write",
            version="4.0.1",
        )
    )
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
    hierarchy = catalogue_scope_hierarchy(
        boundary,
        selected_resource_group_ids=("account-and-transaction",),
    )
    ais_endpoint = next(
        endpoint
        for group in hierarchy.resource_groups
        for endpoint in group.endpoints
        if endpoint.path == "/open-banking/v4.0/aisp/accounts"
    )
    draft = draft.with_scope_selection(
        resource_group_ids=("account-and-transaction",),
        endpoint_ids=(ais_endpoint.id,),
        endpoint_capability_ids={},
    ).with_config(
        config=DISCOVERY_CONFIG,
    )

    form = ExecutionConfigForm(
        data={
            "discovery_url": "https://example.com/.well-known/openid-configuration",
            "ais_resource_ids_json": '{"accountIds": [{"accountId": "account-123"}]}',
            "pis_creditor_account_json": '{"schemeName": "UK.OBIE.SortCodeAccountNumber"}',
            "cbpii_debtor_account_json": (
                '{"schemeName": "UK.OBIE.SortCodeAccountNumber", '
                '"identification": "12345678901234", "name": "Model Bank Account"}'
            ),
            "conditional_properties_json": '[{"id": "standing-order.number-of-payments"}]',
        },
        runtime_prompts=(),
        config_visibility=config_visibility_for_draft(draft),
    )

    assert form.is_valid(), form.errors.as_json()
    assert form.config is not None
    assert form.config["ais"] == {"resourceIds": {"accountIds": [{"accountId": "account-123"}]}}
    assert "pis" not in form.config
    assert "cbpii" not in form.config
    assert "conditionalProperties" not in form.config
