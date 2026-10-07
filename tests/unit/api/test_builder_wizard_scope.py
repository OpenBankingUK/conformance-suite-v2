"""Builder wizard forms, draft persistence, and catalogue scope selection."""

from __future__ import annotations

from hashlib import sha256

import pytest
from django.contrib.sessions.backends.signed_cookies import SessionStore

from conformance.api.builder_draft_store import SessionBuilderDraftStore
from conformance.api.builder_wizard import (
    CatalogueBoundaryForm,
    DiscoveryConfigForm,
    ScopeSelectionForm,
    SecurityConfigForm,
    catalogue_boundary_continue_blocker,
    catalogue_boundary_options,
    catalogue_scope_hierarchy,
    endpoint_capability_value,
    merge_discovery_config,
    plan_document_from_draft,
    resource_groups_without_endpoints,
    scope_selection_defaults,
)
from conformance.catalogue import PlanDocumentBoundary
from conformance.json_types import JsonValue

pytestmark = pytest.mark.unit

DISCOVERY_CONFIG = {"discoveryUrl": "https://example.com/.well-known/openid-configuration"}
"""Minimal discovery config needed to build canonical draft documents."""


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


def test_discovery_config_form_allows_blank_for_manual_security_entry() -> None:
    """Discovery form can clear discovery config and continue to manual entry."""
    form = DiscoveryConfigForm(data={"discovery_url": ""})

    assert form.is_valid(), form.errors.as_json()
    assert form.config == {}
    stale_config: dict[str, JsonValue] = {"discoveryUrl": "https://old.example.com"}
    assert merge_discovery_config(stale_config, form.config) == {}


def test_security_config_form_allows_run_dependent_fields_to_be_blank() -> None:
    """Security form defers run-dependent requiredness until scope is selected."""
    form = SecurityConfigForm(data={})

    assert form.is_valid(), form.errors.as_json()
    assert form.config == {}


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


def test_session_builder_draft_store_persists_catalogue_boundary() -> None:
    """Session-backed Read/Write drafts derive and retain FAPI 1 Advanced."""
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


def test_session_builder_draft_store_derives_dcr_neutral_profile() -> None:
    """Session-backed DCR drafts derive the internal profile-neutral value."""
    session = SessionStore()
    store = SessionBuilderDraftStore(session)
    draft = store.create().with_catalogue_boundary(
        scheme="open-banking-uk",
        specification="read-write",
        version="4.0.1",
    )

    updated = draft.with_catalogue_boundary(
        scheme="open-banking-uk",
        specification="dynamic-client-registration",
        version="3.4",
    )
    store.save(updated)

    loaded = store.get(draft.draft_id)
    assert loaded is not None
    assert loaded.security_profile == "all"


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
    assert "security_profile" not in form.fields
    assert form.selected_resource_group_ids == ()
    assert PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1") in catalogue_boundary_options()
    assert PlanDocumentBoundary("open-banking-uk", "dynamic-client-registration", "3.4") in catalogue_boundary_options()


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


def test_catalogue_boundary_form_allows_dcr_without_resource_groups() -> None:
    """DCR v3.4 can continue directly to endpoint selection."""
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
    assert len(catalogue_scope_hierarchy(boundary).direct_endpoints) == 4
    assert catalogue_boundary_continue_blocker(boundary) is None


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


def test_catalogue_scope_hierarchy_maps_legacy_resource_group_ids_to_high_level_groups() -> None:
    """Earlier path-derived resource-group ids reopen under the API-family group."""
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")

    hierarchy = catalogue_scope_hierarchy(boundary, selected_resource_group_ids=("pis.domestic-payments",))
    selected_group = next(group for group in hierarchy.resource_groups if group.selected)

    assert selected_group.id == "payment-initiation"
    assert selected_group.label == "Payment Initiation"
    assert any(endpoint.path == "/open-banking/v4.0/pisp/domestic-payments" for endpoint in selected_group.endpoints)


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


@pytest.mark.parametrize("version", ["4.0.1", "4.0.0", "3.1.11"])
def test_scope_form_restores_spec_mandatory_endpoints_but_not_optional_ones(version: str) -> None:
    """A crafted scope submission cannot deselect mandatory endpoints."""
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", version)
    form = ScopeSelectionForm(data={"resource_groups": ["account-and-transaction"]}, boundary=boundary)
    assert form.is_valid(), form.errors.as_json()
    endpoints = next(g.endpoints for g in form.hierarchy.resource_groups if g.selected)
    selected = {e.display_path for e in endpoints if e.id in form.selected_endpoint_ids}
    assert selected == {
        "/aisp/accounts",
        "/aisp/accounts/{AccountId}",
        "/aisp/accounts/{AccountId}/balances",
        "/aisp/accounts/{AccountId}/transactions",
    }
    assert all(e.required for e in endpoints if e.id in form.selected_endpoint_ids)
    assert all(g.endpoints == () for g in form.hierarchy.resource_groups if not g.selected)


def test_scope_form_locks_post_dependents_only_while_post_is_selected() -> None:
    """A conditional POST requires its GET, not all other conditional endpoints."""
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
    hierarchy = catalogue_scope_hierarchy(boundary, selected_resource_group_ids=("payment-initiation",))
    endpoints = next(g.endpoints for g in hierarchy.resource_groups if g.selected)
    post = next(e for e in endpoints if e.method == "POST" and e.path.endswith("/international-payments"))
    get = next(e for e in endpoints if e.method == "GET" and "/international-payments/" in e.path)
    form = ScopeSelectionForm(
        data={"resource_groups": ["payment-initiation"], "endpoints": [post.id]},
        boundary=boundary,
    )
    assert form.is_valid(), form.errors.as_json()
    assert {post.id, get.id} <= set(form.selected_endpoint_ids)
    assert next(e for g in form.hierarchy.resource_groups for e in g.endpoints if e.id == get.id).required
    without_post = ScopeSelectionForm(data={"resource_groups": ["payment-initiation"]}, boundary=boundary)
    assert without_post.is_valid(), without_post.errors.as_json()
    assert get.id not in without_post.selected_endpoint_ids
    assert post.id not in without_post.selected_endpoint_ids


def test_scope_form_does_not_lock_vrp_conditional_payment_get() -> None:
    """VRP GET is Conditional in its table, not mandatory if POST implemented."""
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
    hierarchy = catalogue_scope_hierarchy(boundary, selected_resource_group_ids=("variable-recurring-payments",))
    endpoints = next(g.endpoints for g in hierarchy.resource_groups if g.selected)
    post = next(e for e in endpoints if e.method == "POST" and e.path == "/domestic-vrps")
    get = next(e for e in endpoints if e.method == "GET" and e.path == "/domestic-vrps/{vrpId}")
    form = ScopeSelectionForm(
        data={"resource_groups": ["variable-recurring-payments"], "endpoints": [post.id]},
        boundary=boundary,
    )
    assert form.is_valid(), form.errors.as_json()
    assert post.id in form.selected_endpoint_ids
    assert get.id not in form.selected_endpoint_ids


def test_scope_selection_defaults_select_every_endpoint_and_optional_feature_in_new_group() -> None:
    """A newly ticked resource group defaults to all its endpoints and optional features."""
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
    hierarchy = catalogue_scope_hierarchy(boundary, selected_resource_group_ids=("account-and-transaction",))
    ais_group = next(group for group in hierarchy.resource_groups if group.id == "account-and-transaction")
    transactions = next(e for e in ais_group.endpoints if e.path == "/open-banking/v4.0/aisp/transactions")

    endpoint_ids, capability_values = scope_selection_defaults(
        boundary,
        selected_resource_group_ids=("account-and-transaction", "payment-initiation"),
        expand_resource_group_ids=("account-and-transaction",),
    )

    assert endpoint_ids == tuple(endpoint.id for endpoint in ais_group.endpoints)
    assert (
        endpoint_capability_value(endpoint_id=transactions.id, capability_id="ais.transactions.date-range-filtering")
        in capability_values
    )
    assert not any("ais.transactions.list.core" in value for value in capability_values)
    assert not any("/pisp/" in endpoint_id for endpoint_id in endpoint_ids)


def test_scope_selection_defaults_select_optional_features_for_new_endpoint() -> None:
    """A newly ticked endpoint defaults to its optional features only."""
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
    hierarchy = catalogue_scope_hierarchy(boundary, selected_resource_group_ids=("account-and-transaction",))
    ais_group = next(group for group in hierarchy.resource_groups if group.id == "account-and-transaction")
    transactions = next(e for e in ais_group.endpoints if e.path == "/open-banking/v4.0/aisp/transactions")

    endpoint_ids, capability_values = scope_selection_defaults(
        boundary,
        selected_resource_group_ids=("account-and-transaction",),
        expand_endpoint_ids=(transactions.id,),
    )

    assert endpoint_ids == (transactions.id,)
    assert capability_values == (
        endpoint_capability_value(endpoint_id=transactions.id, capability_id="ais.transactions.date-range-filtering"),
    )


def test_scope_selection_defaults_ignore_ids_outside_selected_scope() -> None:
    """Unknown or unselected group and endpoint ids add nothing."""
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")

    assert scope_selection_defaults(
        boundary,
        selected_resource_group_ids=("account-and-transaction",),
        expand_resource_group_ids=("payment-initiation", "not-a-group"),
        expand_endpoint_ids=("not-an-endpoint",),
    ) == ((), ())


def test_resource_groups_without_endpoints_reports_empty_selected_groups() -> None:
    """Selected groups without a selected endpoint are reported by label."""
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
    hierarchy = catalogue_scope_hierarchy(boundary, selected_resource_group_ids=("account-and-transaction",))
    ais_endpoint = next(group for group in hierarchy.resource_groups if group.selected).endpoints[0]

    assert resource_groups_without_endpoints(
        boundary,
        resource_group_ids=("account-and-transaction", "payment-initiation"),
        endpoint_ids=(ais_endpoint.id,),
    ) == ("Payment Initiation",)
    assert (
        resource_groups_without_endpoints(
            boundary,
            resource_group_ids=("account-and-transaction",),
            endpoint_ids=(ais_endpoint.id,),
        )
        == ()
    )
