"""Unit tests for browser wizard draft storage and catalogue boundary forms."""

from __future__ import annotations

from hashlib import sha256

import pytest
from django.contrib.sessions.backends.signed_cookies import SessionStore

from conformance.api.builder_draft_store import SessionBuilderDraftStore
from conformance.api.builder_wizard import (
    BusinessConfigForm,
    CatalogueBoundaryForm,
    ConfigVisibility,
    DiscoveryConfigForm,
    ExecutionConfigForm,
    ScopeSelectionForm,
    SecurityConfigForm,
    catalogue_boundary_continue_blocker,
    catalogue_boundary_options,
    catalogue_scope_hierarchy,
    config_visibility_for_draft,
    endpoint_capability_value,
    merge_discovery_config,
    plan_document_from_draft,
    runtime_input_prompts_for_draft,
)
from conformance.catalogue import PlanDocumentBoundary
from conformance.json_types import JsonValue

DISCOVERY_CONFIG = {"discoveryUrl": "https://example.com/.well-known/openid-configuration"}
"""Minimal discovery config needed to build canonical draft documents."""


@pytest.mark.unit
def test_discovery_config_form_only_stores_discovery_url() -> None:
    """Discovery form ignores stale timeout submissions from older builders."""
    form = DiscoveryConfigForm(
        data={
            "discovery_url": "https://example.com/.well-known/openid-configuration",
            "timeout_seconds": "60",
        },
    )

    assert form.is_valid(), form.errors.as_json()
    expected_config = {"discoveryUrl": "https://example.com/.well-known/openid-configuration"}
    assert form.config == expected_config
    stale_config: dict[str, JsonValue] = {"discoveryUrl": "https://old.example.com", "timeoutSeconds": 60}
    assert merge_discovery_config(stale_config, form.config) == expected_config


@pytest.mark.unit
def test_discovery_config_form_allows_blank_for_manual_security_entry() -> None:
    """Discovery form can clear discovery config and continue to manual entry."""
    form = DiscoveryConfigForm(data={"discovery_url": ""})

    assert form.is_valid(), form.errors.as_json()
    assert form.config == {}
    stale_config: dict[str, JsonValue] = {"discoveryUrl": "https://old.example.com"}
    assert merge_discovery_config(stale_config, form.config) == {}


@pytest.mark.unit
def test_security_config_form_allows_run_dependent_fields_to_be_blank() -> None:
    """Security form defers run-dependent requiredness until scope is selected."""
    form = SecurityConfigForm(data={})

    assert form.is_valid(), form.errors.as_json()
    assert form.config == {}


@pytest.mark.unit
def test_security_config_form_requires_complete_conditional_groups() -> None:
    """Conditional security groups must be supplied all together."""
    form = SecurityConfigForm(
        data={
            "oauth_client_id": "client-123",
            "oauth_redirect_uri": "https://client.example.com/callback",
            "resource_server_base_url": "https://resource.example.com",
            "signing_kid": "kid-123",
            "tls_client_certificate_path": "/certs/client.pem",
        }
    )

    assert form.is_valid() is False
    assert "Complete every FAPI signing field" in form.errors["signing_certificate_path"][0]
    assert "mTLS client certificate and private key" in form.errors["tls_client_private_key_path"][0]


@pytest.mark.unit
def test_session_builder_draft_store_persists_catalogue_boundary() -> None:
    """Session-backed drafts retain the saved scheme/specification/version step."""
    session = SessionStore()
    store = SessionBuilderDraftStore(session)
    draft = store.create()

    updated = draft.with_catalogue_boundary(
        scheme="open-banking-uk",
        specification="read-write",
        version="4.0.1",
    )
    store.save(updated)

    loaded = store.get(draft.draft_id)
    assert loaded is not None
    assert loaded.scheme == "open-banking-uk"
    assert loaded.specification == "read-write"
    assert loaded.version == "4.0.1"
    assert loaded.security_profile == "fapi1-advanced"


@pytest.mark.unit
def test_session_builder_draft_store_persists_scope_selection() -> None:
    """Session-backed drafts retain selected resource groups, endpoints, and features."""
    session = SessionStore()
    store = SessionBuilderDraftStore(session)
    draft = store.create()

    updated = draft.with_scope_selection(
        resource_group_ids=("account-and-transaction",),
        endpoint_ids=("endpoint-abc",),
        endpoint_capability_ids={"endpoint-abc": ("ais.accounts.optional",)},
    )
    store.save(updated)

    loaded = store.get(draft.draft_id)
    assert loaded is not None
    assert loaded.resource_group_ids == ("account-and-transaction",)
    assert loaded.endpoint_ids == ("endpoint-abc",)
    assert loaded.endpoint_capability_ids == {"endpoint-abc": ("ais.accounts.optional",)}


@pytest.mark.unit
def test_session_builder_draft_store_persists_grouped_config() -> None:
    """Session-backed drafts retain grouped execution config."""
    session = SessionStore()
    store = SessionBuilderDraftStore(session)
    draft = store.create()

    updated = draft.with_config(
        config={
            "environment": "test-env",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "inputs": {"accessToken": {"value": "secret-access-token"}},
        }
    )
    store.save(updated)

    loaded = store.get(draft.draft_id)
    assert loaded is not None
    assert "environment" not in loaded.config
    assert loaded.config["inputs"] == {"accessToken": {"value": "secret-access-token"}}


@pytest.mark.unit
def test_catalogue_boundary_form_accepts_compile_ready_v2_boundary() -> None:
    """The first wizard step accepts the Read/Write boundary backed by bundled catalogues."""
    form = CatalogueBoundaryForm(
        data={
            "scheme": "open-banking-uk",
            "specification": "read-write",
            "version": "4.0.1",
        }
    )

    assert form.is_valid(), form.errors.as_json()
    assert form.selected_resource_group_ids == ()
    assert PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1") in catalogue_boundary_options()
    assert PlanDocumentBoundary("open-banking-uk", "dynamic-client-registration", "3.4") in catalogue_boundary_options()


@pytest.mark.unit
def test_catalogue_boundary_form_defers_resource_group_selection() -> None:
    """The first wizard step defers high-level resource-group selection."""
    form = CatalogueBoundaryForm(
        data={
            "scheme": "open-banking-uk",
            "specification": "read-write",
            "version": "4.0.1",
        }
    )

    assert form.is_valid(), form.errors.as_json()
    assert form.selected_resource_group_ids == ()


@pytest.mark.unit
def test_catalogue_boundary_form_allows_selector_only_dcr_without_resource_groups() -> None:
    """DCR v3.4 can be selected without Read/Write resource groups."""
    boundary = PlanDocumentBoundary("open-banking-uk", "dynamic-client-registration", "3.4")
    form = CatalogueBoundaryForm(
        data={
            "scheme": "open-banking-uk",
            "specification": "dynamic-client-registration",
            "version": "3.4",
            "resource_groups": ["account-and-transaction"],
        }
    )

    assert form.is_valid(), form.errors.as_json()
    assert form.selected_resource_group_ids == ()
    assert catalogue_scope_hierarchy(boundary).resource_groups == ()
    assert catalogue_boundary_continue_blocker(boundary) is not None


@pytest.mark.unit
def test_catalogue_boundary_form_rejects_unsupported_boundary_combination() -> None:
    """The first wizard step rejects scheme/specification/version combinations outside the catalogue."""
    form = CatalogueBoundaryForm(
        data={
            "scheme": "open-banking-uk",
            "specification": "read-write",
            "version": "4.0.1",
            "resource_groups": ["account-and-transaction"],
        },
        boundaries=(
            PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.0"),
            PlanDocumentBoundary("open-banking-uk", "dcr", "4.0.1"),
        ),
    )

    assert form.is_valid() is False
    assert "Choose a supported scheme" in form.non_field_errors()[0]


@pytest.mark.unit
def test_catalogue_scope_hierarchy_reveals_endpoints_and_features_under_selected_parents() -> None:
    """The scope hierarchy only reveals endpoints and features under selected parents."""
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")

    group_hierarchy = catalogue_scope_hierarchy(
        boundary,
        selected_resource_group_ids=("account-and-transaction",),
    )
    ais_group = next(group for group in group_hierarchy.resource_groups if group.id == "account-and-transaction")
    pis_group = next(group for group in group_hierarchy.resource_groups if group.id == "payment-initiation")
    transaction_endpoint = next(
        endpoint for endpoint in ais_group.endpoints if endpoint.path == "/open-banking/v4.0/aisp/transactions"
    )

    assert [group.id for group in group_hierarchy.resource_groups] == [
        "account-and-transaction",
        "payment-initiation",
        "confirmation-of-funds",
        "variable-recurring-payments",
    ]
    assert ais_group.selected is True
    assert ais_group.label == "Account and Transaction"
    assert pis_group.endpoints == ()
    assert transaction_endpoint.display_path == "/aisp/transactions"
    assert transaction_endpoint.operation_id.startswith("ais-get-open-banking-v4.0-aisp-transactions")
    assert transaction_endpoint.features == ()

    selected_hierarchy = catalogue_scope_hierarchy(
        boundary,
        selected_resource_group_ids=("account-and-transaction",),
        selected_endpoint_ids=(transaction_endpoint.id,),
        selected_capability_values=(
            endpoint_capability_value(
                endpoint_id=transaction_endpoint.id,
                capability_id="ais.transactions.date-range-filtering",
            ),
        ),
    )

    selected_endpoint = next(
        endpoint
        for group in selected_hierarchy.resource_groups
        for endpoint in group.endpoints
        if endpoint.id == transaction_endpoint.id
    )
    assert [feature.capability_id for feature in selected_endpoint.features] == [
        "ais.transactions.list.core",
        "ais.transactions.date-range-filtering",
    ]
    assert selected_endpoint.features[1].selected is True


@pytest.mark.unit
def test_catalogue_scope_hierarchy_maps_legacy_resource_group_ids_to_high_level_groups() -> None:
    """Earlier path-derived resource-group ids reopen under the API-family group."""
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")

    hierarchy = catalogue_scope_hierarchy(boundary, selected_resource_group_ids=("pis.domestic-payments",))
    selected_group = next(group for group in hierarchy.resource_groups if group.selected)

    assert selected_group.id == "payment-initiation"
    assert selected_group.label == "Payment Initiation"
    assert any(endpoint.path == "/open-banking/v4.0/pisp/domestic-payments" for endpoint in selected_group.endpoints)


@pytest.mark.unit
def test_catalogue_scope_hierarchy_excludes_cvrp_from_open_banking_boundary() -> None:
    """The Open Banking Read/Write wizard exposes VRP but not cVRP."""
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
    group_hierarchy = catalogue_scope_hierarchy(boundary)
    group_ids = {group.id for group in group_hierarchy.resource_groups}
    assert "variable-recurring-payments" in group_ids
    assert "cvrp.domestic-vrp-consents" not in group_ids
    assert "vrp.funds-confirmation" not in group_ids
    assert not any(group.api == "cvrp" for group in group_hierarchy.resource_groups)

    selected_hierarchy = catalogue_scope_hierarchy(
        boundary,
        selected_resource_group_ids=("variable-recurring-payments",),
    )
    vrp_group = next(group for group in selected_hierarchy.resource_groups if group.id == "variable-recurring-payments")
    vrp_endpoint = next(
        endpoint
        for endpoint in vrp_group.endpoints
        if endpoint.method == "POST" and endpoint.path == "/domestic-vrp-consents"
    )

    draft = (
        SessionBuilderDraftStore(SessionStore())
        .create()
        .with_catalogue_boundary(scheme="open-banking-uk", specification="read-write", version="4.0.1")
        .with_scope_selection(
            resource_group_ids=("variable-recurring-payments",),
            endpoint_ids=(vrp_endpoint.id,),
            endpoint_capability_ids={},
        )
        .with_config(config=DISCOVERY_CONFIG)
    )
    document = plan_document_from_draft(draft)
    selected_endpoints = [
        (group.resource_group_id, endpoint.method, endpoint.path)
        for group in document.resource_groups
        for endpoint in group.endpoints
    ]
    assert selected_endpoints == [("variable-recurring-payments", "POST", "/domestic-vrp-consents")]

    legacy_endpoint_id = f"endpoint-{sha256(b'POST /domestic-vrp-consents').hexdigest()[:12]}"
    legacy_draft = (
        SessionBuilderDraftStore(SessionStore())
        .create()
        .with_catalogue_boundary(scheme="open-banking-uk", specification="read-write", version="4.0.1")
        .with_scope_selection(
            resource_group_ids=("vrp.domestic-vrp-consents",),
            endpoint_ids=(legacy_endpoint_id,),
            endpoint_capability_ids={},
        )
        .with_config(config=DISCOVERY_CONFIG)
    )
    legacy_document = plan_document_from_draft(legacy_draft)
    legacy_selected_endpoints = [
        (group.resource_group_id, endpoint.method, endpoint.path)
        for group in legacy_document.resource_groups
        for endpoint in group.endpoints
    ]
    assert legacy_selected_endpoints == [("variable-recurring-payments", "POST", "/domestic-vrp-consents")]


@pytest.mark.unit
def test_scope_selection_form_rejects_feature_for_unselected_endpoint() -> None:
    """The scope form rejects optional features outside selected endpoint context."""
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
    hierarchy = catalogue_scope_hierarchy(
        boundary,
        selected_resource_group_ids=("account-and-transaction",),
    )
    transaction_endpoint = next(
        endpoint
        for group in hierarchy.resource_groups
        for endpoint in group.endpoints
        if endpoint.path == "/open-banking/v4.0/aisp/transactions"
    )

    form = ScopeSelectionForm(
        data={
            "resource_groups": ["account-and-transaction"],
            "endpoint_capabilities": [
                endpoint_capability_value(
                    endpoint_id=transaction_endpoint.id,
                    capability_id="ais.transactions.date-range-filtering",
                )
            ],
        },
        boundary=boundary,
    )

    assert form.is_valid() is False


@pytest.mark.unit
def test_scope_selection_form_prunes_stale_children_for_dynamic_refresh() -> None:
    """Dynamic scope refreshes discard child inputs from deselected parents."""
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
    hierarchy = catalogue_scope_hierarchy(
        boundary,
        selected_resource_group_ids=("account-and-transaction",),
    )
    transaction_endpoint = next(
        endpoint
        for group in hierarchy.resource_groups
        for endpoint in group.endpoints
        if endpoint.path == "/open-banking/v4.0/aisp/transactions"
    )

    form = ScopeSelectionForm(
        data={
            "resource_groups": [],
            "endpoints": [transaction_endpoint.id],
            "endpoint_capabilities": [
                endpoint_capability_value(
                    endpoint_id=transaction_endpoint.id,
                    capability_id="ais.transactions.date-range-filtering",
                )
            ],
        },
        boundary=boundary,
        prune_unavailable_choices=True,
    )

    assert form.is_valid(), form.errors.as_json()
    assert form.selected_resource_group_ids == ()
    assert form.selected_endpoint_ids == ()
    assert form.selected_endpoint_capability_ids == {}


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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
            "pis_payment_frequency": "Monthly",
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
    assert pis["paymentFrequency"] == "Monthly"
    assert pis["standingOrderFrequency"] == {"type": "Evry", "pointInTime": "01"}
    cbpii = form.config["cbpii"]
    assert isinstance(cbpii, dict)
    debtor_account = cbpii["debtorAccount"]
    assert isinstance(debtor_account, dict)
    assert debtor_account["identification"] == "12345678901234"
    assert form.config["conditionalProperties"] == [{"id": "standing-order.number-of-payments"}]


@pytest.mark.unit
def test_grouped_config_form_omits_resource_server_for_unchecked_customer_ip_toggle() -> None:
    """Unchecked customer-IP toggle alone does not create a resourceServer config."""
    form = ExecutionConfigForm(data={})

    assert form.is_valid(), form.errors.as_json()
    assert form.config is not None
    assert "resourceServer" not in form.config


@pytest.mark.unit
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
    assert "consentedAccountId" not in document.runtime_inputs
    assert "resourceBaseUrl" not in {prompt.input_id for prompt in prompts}
    assert "consentedAccountId" not in {prompt.input_id for prompt in prompts}
    assert "accessToken" not in {prompt.input_id for prompt in prompts}


@pytest.mark.unit
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
    assert visibility.show_ais is False
    assert visibility.show_pis is False
    assert visibility.show_cbpii is False
    assert visibility.show_business_defaults is False


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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
    assert "ais" not in form.config
    assert "pis" not in form.config
    assert "cbpii" not in form.config
    assert "conditionalProperties" not in form.config
