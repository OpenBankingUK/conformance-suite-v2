"""Integration tests for participant-facing browser UI views."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import cast
from unittest.mock import Mock, patch

import pytest
from django.test import Client

from conformance.api.builder_wizard import catalogue_scope_hierarchy, endpoint_capability_value
from conformance.api.plan_builder import CatalogueEndpointOption, PlanBuilderForm, guided_flow_context
from conformance.api.run_store import RunConflictError, RunPlanStep, run_store
from conformance.catalogue import PlanDocumentBoundary
from conformance.http import JsonHttpResponse
from conformance.json_types import JsonValue
from conformance.ozone_client import DiscoveryDocument

VALID_CONFIG: dict[str, JsonValue] = {
    "environment": "test-env",
    "discoveryUrl": "https://example.com/.well-known/openid-configuration",
}
"""Minimal config used by browser UI tests."""


@pytest.fixture(autouse=True)
def _reset_global_stores() -> Iterator[None]:
    """Reset process-local singleton stores around each UI test.

    Yields:
        Control back to pytest while the test executes.
    """
    run_store.reset()
    yield
    run_store.reset()


def _endpoint_id_for(*, api: str, path: str) -> str:
    """Return the rendered endpoint option id for a catalogue path.

    Args:
        api: API family for the option.
        path: Standards endpoint path.

    Returns:
        Stable browser form id for the endpoint.

    Raises:
        AssertionError: If the endpoint is not rendered by the guided context.
    """
    context = guided_flow_context(PlanBuilderForm())
    for option in cast(tuple[CatalogueEndpointOption, ...], context["guided_endpoint_options"]):
        if option.api == api and option.path == path:
            return option.id
    raise AssertionError(f"Endpoint option not found for {api} {path}")


def _ais_accounts_endpoint_id() -> str:
    """Return the AIS accounts-list endpoint id.

    Returns:
        Endpoint id for ``GET /open-banking/v4.0/aisp/accounts``.
    """
    return _endpoint_id_for(api="ais", path="/open-banking/v4.0/aisp/accounts")


def _plan_form_data(
    *,
    endpoint_ids: list[str] | None = None,
    capability_values: list[str] | None = None,
    runtime_inputs: dict[str, str] | None = None,
) -> dict[str, object]:
    """Build form data for plan preview and launch requests.

    Args:
        endpoint_ids: Implemented endpoint ids submitted by checkboxes.
        capability_values: Endpoint capability checkbox values submitted by
            endpoint cards.
        runtime_inputs: Runtime input id/value strings to submit.

    Returns:
        Form-encoded payload dictionary accepted by Django's test client.
    """
    data: dict[str, object] = {
        "config_json": json.dumps(VALID_CONFIG),
        "plan_spec_json": "",
        "guided_standard": "open-banking",
        "guided_spec_version": "v4.0",
        "guided_api": "ais",
        "guided_security_profile": "fapi1-advanced",
        "implemented_endpoint_ids": endpoint_ids or [],
        "implemented_endpoint_capabilities": capability_values or [],
    }
    for input_id, value in (runtime_inputs or {}).items():
        data[f"runtime_input__{input_id}"] = value
    return data


def _valid_plan_form_data() -> dict[str, object]:
    """Build a valid AIS endpoint-selected form payload.

    Returns:
        Form data that compiles the AIS accounts-list catalogue plan.
    """
    return _plan_form_data(
        endpoint_ids=[_ais_accounts_endpoint_id()],
        runtime_inputs={
            "resourceBaseUrl": "https://resource.example.com",
            "accessToken": "secret-access-token",
        },
    )


def _run_id_from_redirect(location: str) -> str:
    """Extract the run id from a Django redirect response.

    Args:
        location: Redirect target from a Django test response.

    Returns:
        Run id segment from the redirect target.
    """
    return location.rstrip("/").rsplit("/", maxsplit=1)[-1]


def _draft_id_from_builder_redirect(location: str) -> str:
    """Extract the builder draft id from a wizard redirect response.

    Args:
        location: Redirect target from the new-builder route.

    Returns:
        Draft id segment from the redirect target.
    """
    return location.rstrip("/").rsplit("/", maxsplit=2)[-2]


@pytest.mark.integration
@pytest.mark.django_db
class TestBuilderWizardUi:
    """Browser coverage for the new multi-page builder entry and first step."""

    def test_new_builder_redirects_to_scheme_specification_version_step(self) -> None:
        """POST /builder/new/ creates a session draft and renders wizard step one."""
        client = Client()

        response = client.post("/builder/new/")

        assert response.status_code == 302
        location = response["Location"]
        assert location.startswith("/builder/")
        assert location.endswith("/catalogue/")

        step_response = client.get(location)
        assert step_response.status_code == 200
        content = step_response.content.decode("utf-8")
        assert "Choose test plan catalogue" in content
        assert "Scheme, specification, version, and resource groups" in content
        assert "Open Banking UK" in content
        assert "Read/Write" in content
        assert "Dynamic Client Registration (DCR)" in content
        assert "4.0.1" in content
        assert "3.4" in content
        assert "account-and-transaction" in content
        assert "payment-initiation" in content
        assert "cvrp" not in content.lower()
        assert "Implemented endpoints" not in content
        assert "Plan spec JSON" not in content

    def test_catalogue_boundary_post_saves_draft_values(self) -> None:
        """The first wizard step saves selected catalogue values and moves to scope."""
        client = Client()
        create_response = client.post("/builder/new/")
        location = create_response["Location"]
        draft_id = _draft_id_from_builder_redirect(location)

        response = client.post(
            location,
            data={
                "scheme": "open-banking-uk",
                "specification": "read-write",
                "version": "4.0.1",
                "resource_groups": ["account-and-transaction", "payment-initiation"],
            },
        )

        assert response.status_code == 302
        assert response["Location"] == f"/builder/{draft_id}/scope/"

        saved_response = client.get(response["Location"])
        assert saved_response.status_code == 200
        content = saved_response.content.decode("utf-8")
        assert "Select test plan endpoints" in content
        assert "Endpoints and scoped features" in content
        assert "hx-post" in content
        assert "data-scope-form" in content
        assert "fetch(" in content
        assert "account-and-transaction" in content
        assert "payment-initiation" in content
        assert "cvrp" not in content.lower()
        assert "GET /aisp/transactions" in content
        assert "GET /pisp/domestic-payments" in content
        assert "GET /open-banking/v4.0/aisp/transactions" not in content
        assert "GET /open-banking/v4.0/pisp/domestic-payments" not in content
        assert "Select all endpoints" in content
        assert "Deselect all endpoints" in content
        assert "data-endpoint-bulk-action" in content
        assert "Operation <code>" not in content

    def test_catalogue_boundary_post_requires_resource_group(self) -> None:
        """The first wizard step does not continue until at least one resource group is selected."""
        client = Client()
        create_response = client.post("/builder/new/")

        response = client.post(
            create_response["Location"],
            data={
                "scheme": "open-banking-uk",
                "specification": "read-write",
                "version": "4.0.1",
            },
        )

        assert response.status_code == 400
        content = response.content.decode("utf-8")
        assert "Select at least one resource group." in content

    def test_catalogue_resource_groups_fragment_updates_for_selected_boundary(self) -> None:
        """The catalogue page resource groups are rendered from the selected boundary."""
        client = Client()
        create_response = client.post("/builder/new/")
        draft_id = _draft_id_from_builder_redirect(create_response["Location"])

        read_write_response = client.post(
            f"/builder/{draft_id}/catalogue/resource-groups/",
            data={
                "scheme": "open-banking-uk",
                "specification": "read-write",
                "version": "4.0.1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        dcr_response = client.post(
            f"/builder/{draft_id}/catalogue/resource-groups/",
            data={
                "scheme": "open-banking-uk",
                "specification": "dynamic-client-registration",
                "version": "3.4",
                "resource_groups": ["account-and-transaction"],
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        assert read_write_response.status_code == 200
        read_write_content = read_write_response.content.decode("utf-8")
        assert 'value="account-and-transaction"' in read_write_content
        assert "Payment Initiation" in read_write_content

        assert dcr_response.status_code == 200
        dcr_content = dcr_response.content.decode("utf-8")
        assert "Dynamic Client Registration v3.4 is shown as a selector-only example." in dcr_content
        assert 'name="resource_groups"' not in dcr_content
        assert "Account and Transaction" not in dcr_content

    def test_selector_only_dcr_boundary_blocks_continuation(self) -> None:
        """DCR can demonstrate boundary-specific groups without continuing to launch flow."""
        client = Client()
        create_response = client.post("/builder/new/")
        draft_id = _draft_id_from_builder_redirect(create_response["Location"])

        response = client.post(
            create_response["Location"],
            data={
                "scheme": "open-banking-uk",
                "specification": "dynamic-client-registration",
                "version": "3.4",
                "resource_groups": ["account-and-transaction"],
            },
        )

        assert response.status_code == 400
        content = response.content.decode("utf-8")
        assert "Dynamic Client Registration v3.4 is shown as a selector-only example." in content
        assert "Select at least one resource group." not in content
        assert "Account and Transaction" not in content

        scope_response = client.get(f"/builder/{draft_id}/scope/")
        assert scope_response.status_code == 302
        assert scope_response["Location"] == f"/builder/{draft_id}/catalogue/"

    def test_scope_step_filters_endpoints_and_features_via_dynamic_fragment(self) -> None:
        """The dynamic scope fragment reveals endpoints and features under selected parents."""
        client = Client()
        create_response = client.post("/builder/new/")
        catalogue_response = client.post(
            create_response["Location"],
            data={
                "scheme": "open-banking-uk",
                "specification": "read-write",
                "version": "4.0.1",
                "resource_groups": ["account-and-transaction"],
            },
        )
        scope_location = catalogue_response["Location"]
        draft_id = _draft_id_from_builder_redirect(scope_location)
        boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
        hierarchy = catalogue_scope_hierarchy(
            boundary,
            selected_resource_group_ids=("account-and-transaction",),
        )
        endpoint = next(
            endpoint
            for group in hierarchy.resource_groups
            for endpoint in group.endpoints
            if endpoint.path == "/open-banking/v4.0/aisp/transactions"
        )

        response = client.post(
            f"/builder/{draft_id}/scope/options/",
            data={
                "resource_groups": ["account-and-transaction"],
                "endpoints": [endpoint.id],
                "endpoint_capabilities": [
                    endpoint_capability_value(
                        endpoint_id=endpoint.id,
                        capability_id="ais.transactions.date-range-filtering",
                    )
                ],
            },
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "GET /open-banking/v4.0/aisp/transactions" in content
        assert "ais.transactions.date-range-filtering" in content
        assert "GET /open-banking/v4.0/pisp/domestic-payments" not in content

    def test_scope_step_dynamic_fragment_prunes_stale_children(self) -> None:
        """Dynamic refresh drops endpoint selections after their resource group is cleared."""
        client = Client()
        create_response = client.post("/builder/new/")
        catalogue_response = client.post(
            create_response["Location"],
            data={
                "scheme": "open-banking-uk",
                "specification": "read-write",
                "version": "4.0.1",
                "resource_groups": ["account-and-transaction"],
            },
        )
        scope_location = catalogue_response["Location"]
        draft_id = _draft_id_from_builder_redirect(scope_location)
        boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
        hierarchy = catalogue_scope_hierarchy(
            boundary,
            selected_resource_group_ids=("account-and-transaction",),
        )
        endpoint = next(
            endpoint
            for group in hierarchy.resource_groups
            for endpoint in group.endpoints
            if endpoint.path == "/open-banking/v4.0/aisp/transactions"
        )

        response = client.post(
            f"/builder/{draft_id}/scope/options/",
            data={
                "resource_groups": [],
                "endpoints": [endpoint.id],
                "endpoint_capabilities": [
                    endpoint_capability_value(
                        endpoint_id=endpoint.id,
                        capability_id="ais.transactions.date-range-filtering",
                    )
                ],
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "No resource groups are selected." in content
        assert "ais.transactions.date-range-filtering" not in content

    def test_scope_step_saves_selected_resource_endpoint_and_feature(self) -> None:
        """POST /builder/<draft>/scope/ stores selected scope values and continues."""
        client = Client()
        create_response = client.post("/builder/new/")
        catalogue_response = client.post(
            create_response["Location"],
            data={
                "scheme": "open-banking-uk",
                "specification": "read-write",
                "version": "4.0.1",
                "resource_groups": ["account-and-transaction"],
            },
        )
        scope_location = catalogue_response["Location"]
        boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
        hierarchy = catalogue_scope_hierarchy(
            boundary,
            selected_resource_group_ids=("account-and-transaction",),
        )
        endpoint = next(
            endpoint
            for group in hierarchy.resource_groups
            for endpoint in group.endpoints
            if endpoint.path == "/open-banking/v4.0/aisp/transactions"
        )

        response = client.post(
            scope_location,
            data={
                "resource_groups": ["account-and-transaction"],
                "endpoints": [endpoint.id],
                "endpoint_capabilities": [
                    endpoint_capability_value(
                        endpoint_id=endpoint.id,
                        capability_id="ais.transactions.date-range-filtering",
                    )
                ],
            },
        )

        assert response.status_code == 302
        assert response["Location"] == f"/builder/{_draft_id_from_builder_redirect(scope_location)}/config/"
        saved_response = client.get(response["Location"])
        assert saved_response.status_code == 200
        content = saved_response.content.decode("utf-8")
        assert "Business configuration" in content
        assert "Resource server targets" not in content
        assert "accessToken" not in content
        assert "Consented account identifier" in content
        assert "Advanced AIS resource IDs JSON" in content
        assert "Domestic creditor account JSON" not in content
        assert "CBPII debtor account JSON" not in content

    def test_config_step_renders_payment_defaults_for_payment_scope(self) -> None:
        """The config page shows payment defaults only for selected PIS endpoints."""
        client = Client()
        create_response = client.post("/builder/new/")
        catalogue_response = client.post(
            create_response["Location"],
            data={
                "scheme": "open-banking-uk",
                "specification": "read-write",
                "version": "4.0.1",
                "resource_groups": ["payment-initiation"],
            },
        )
        scope_location = catalogue_response["Location"]
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
        scope_response = client.post(
            scope_location,
            data={"resource_groups": ["payment-initiation"], "endpoints": [endpoint.id]},
        )

        response = client.get(scope_response["Location"])

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Domestic creditor account JSON" in content
        assert "AIS resource IDs JSON" not in content
        assert "CBPII debtor account JSON" not in content

    def test_catalogue_resource_group_change_prunes_stale_endpoint_scope(self) -> None:
        """Changing page-one resource groups removes endpoints outside the new scope."""
        client = Client()
        create_response = client.post("/builder/new/")
        catalogue_response = client.post(
            create_response["Location"],
            data={
                "scheme": "open-banking-uk",
                "specification": "read-write",
                "version": "4.0.1",
                "resource_groups": ["account-and-transaction"],
            },
        )
        scope_location = catalogue_response["Location"]
        draft_id = _draft_id_from_builder_redirect(scope_location)
        boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
        ais_hierarchy = catalogue_scope_hierarchy(
            boundary,
            selected_resource_group_ids=("account-and-transaction",),
        )
        ais_endpoint = next(
            endpoint
            for group in ais_hierarchy.resource_groups
            for endpoint in group.endpoints
            if endpoint.path == "/open-banking/v4.0/aisp/accounts"
        )
        first_scope_response = client.post(
            scope_location,
            data={"resource_groups": ["account-and-transaction"], "endpoints": [ais_endpoint.id]},
        )
        assert first_scope_response.status_code == 302

        changed_catalogue_response = client.post(
            f"/builder/{draft_id}/catalogue/",
            data={
                "scheme": "open-banking-uk",
                "specification": "read-write",
                "version": "4.0.1",
                "resource_groups": ["payment-initiation"],
            },
        )

        assert changed_catalogue_response.status_code == 302
        changed_scope_response = client.get(changed_catalogue_response["Location"])
        assert changed_scope_response.status_code == 200
        content = changed_scope_response.content.decode("utf-8")
        assert "payment-initiation" in content
        assert "account-and-transaction" not in content
        assert "GET /open-banking/v4.0/aisp/accounts" not in content
        assert "GET /pisp/domestic-payments" in content

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_config_steps_save_grouped_values_and_render_review(self, mock_fetch_discovery: Mock) -> None:
        """Staged config saves grouped values and renders masked review output."""
        mock_fetch_discovery.return_value = {
            "issuer": "https://example.com",
            "authorization_endpoint": "https://example.com/authorize",
            "token_endpoint": "https://example.com/token",
            "jwks_uri": "https://example.com/jwks",
        }
        client = Client()
        create_response = client.post("/builder/new/")
        catalogue_response = client.post(
            create_response["Location"],
            data={
                "scheme": "open-banking-uk",
                "specification": "read-write",
                "version": "4.0.1",
                "resource_groups": ["account-and-transaction"],
            },
        )
        scope_location = catalogue_response["Location"]
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
        scope_response = client.post(
            scope_location,
            data={"resource_groups": ["account-and-transaction"], "endpoints": [endpoint.id]},
        )

        business_response = client.post(
            scope_response["Location"],
            data={
                "ais_consented_account_id": "account-123",
                "pis_creditor_account_scheme_name": "UK.OBIE.SortCodeAccountNumber",
                "cbpii_debtor_account_json": (
                    '{"schemeName": "UK.OBIE.SortCodeAccountNumber", '
                    '"identification": "12345678901234", "name": "Model Bank Account"}'
                ),
                "conditional_properties_json": '[{"id": "standing-order.number-of-payments"}]',
            },
        )

        assert business_response.status_code == 302
        assert business_response["Location"].endswith("/config/discovery/")
        discovery_form_response = client.get(business_response["Location"])
        discovery_form_content = discovery_form_response.content.decode("utf-8")
        assert "Environment" not in discovery_form_content
        assert "Follow-up mode" not in discovery_form_content
        discovery_response = client.post(
            business_response["Location"],
            data={
                "discovery_url": "https://example.com/.well-known/openid-configuration",
            },
        )
        assert discovery_response.status_code == 302
        assert discovery_response["Location"].endswith("/config/security/")
        security_response = client.post(
            discovery_response["Location"],
            data={"resource_server_base_url": "https://resource.example.com"},
        )
        assert security_response.status_code == 302
        assert security_response["Location"].endswith("/config/runtime/")
        runtime_response = client.post(
            security_response["Location"],
            data={"runtime_input__accessToken": "secret-access-token"},
        )

        assert runtime_response.status_code == 302
        assert runtime_response["Location"].endswith("/review/")
        review_response = client.get(runtime_response["Location"])
        assert review_response.status_code == 200
        content = review_response.content.decode("utf-8")
        assert "Review generated test plan" in content
        assert "Generated tests" in content
        assert "Safe export preview" in content
        assert "secret-access-token" not in content
        assert "accessToken" in content
        assert "&quot;value&quot;: &quot;&quot;" in content
        draft_id = _draft_id_from_builder_redirect(runtime_response["Location"])
        safe_export = client.get(f"/builder/{draft_id}/export.json")
        assert safe_export.json()["schemaVersion"] == "1.0"
        assert safe_export.json()["securityEnvironment"]["resourceBaseUrl"] == "https://resource.example.com"
        assert safe_export.json()["businessTestData"]["ais"]["accountIds"][0] == ""
        assert "pis" not in safe_export.json()["businessTestData"]
        assert "cbpii" not in safe_export.json()["businessTestData"]
        assert "conditionalProperties" not in safe_export.json()["businessTestData"]

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_discovery_metadata_prefills_security_and_exports_accepted_values(
        self,
        mock_fetch_discovery: Mock,
    ) -> None:
        """Accepted discovery-derived values are saved into plan JSON exports."""
        mock_fetch_discovery.return_value = {
            "issuer": "https://auth.example.com",
            "authorization_endpoint": "https://auth.example.com/authorize",
            "token_endpoint": "https://auth.example.com/token",
            "jwks_uri": "https://auth.example.com/jwks",
            "token_endpoint_auth_methods_supported": ["private_key_jwt", "tls_client_auth"],
            "response_types_supported": ["code id_token"],
            "acr_values_supported": ["urn:openbanking:psd2:sca"],
            "request_object_signing_alg_values_supported": ["PS256"],
        }
        client = Client()
        create_response = client.post("/builder/new/")
        catalogue_response = client.post(
            create_response["Location"],
            data={
                "scheme": "open-banking-uk",
                "specification": "read-write",
                "version": "4.0.1",
                "resource_groups": ["account-and-transaction"],
            },
        )
        boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
        hierarchy = catalogue_scope_hierarchy(boundary, selected_resource_group_ids=("account-and-transaction",))
        endpoint = next(
            endpoint
            for group in hierarchy.resource_groups
            for endpoint in group.endpoints
            if endpoint.path == "/open-banking/v4.0/aisp/accounts"
        )
        scope_response = client.post(
            catalogue_response["Location"],
            data={"resource_groups": ["account-and-transaction"], "endpoints": [endpoint.id]},
        )
        business_response = client.post(scope_response["Location"], data={"ais_consented_account_id": "account-123"})
        discovery_response = client.post(
            business_response["Location"],
            data={
                "discovery_url": "https://auth.example.com/.well-known/openid-configuration",
            },
        )

        security_response = client.get(discovery_response["Location"])

        assert security_response.status_code == 200
        content = security_response.content.decode("utf-8")
        assert "Token endpoint auth methods supported" in content
        assert "private_key_jwt, tls_client_auth" in content
        assert "JWKS URI" in content
        assert "https://auth.example.com/jwks" in content
        assert "JWKS check" not in content
        assert "JWKS key count" not in content
        assert 'value="https://auth.example.com/token"' in content
        assert "Inferred from discovery" not in content
        assert "inferred from discovery" in content

        security_save = client.post(
            discovery_response["Location"],
            data={
                "oauth_client_id": "client-123",
                "oauth_redirect_uri": "https://client.example.com/callback",
                "oauth_authorization_endpoint": "https://auth.example.com/authorize",
                "oauth_issuer": "https://auth.example.com",
                "oauth_token_endpoint": "https://auth.example.com/token",
                "oauth_response_type": "code id_token",
                "oauth_acr_values_supported": '["urn:openbanking:psd2:sca"]',
                "oauth_request_object_signing_alg": "PS256",
                "resource_server_base_url": "https://resource.example.com",
            },
        )
        runtime_response = client.post(security_save["Location"], data={"runtime_input__accessToken": "token-value"})
        draft_id = _draft_id_from_builder_redirect(runtime_response["Location"])

        exported = client.get(f"/builder/{draft_id}/export.json").json()["securityEnvironment"]

        assert exported["issuer"] == "https://auth.example.com"
        assert exported["authorizationEndpoint"] == "https://auth.example.com/authorize"
        assert exported["tokenEndpoint"] == "https://auth.example.com/token"
        assert exported["signingAlgorithm"] == "PS256"
        assert exported["resourceBaseUrl"] == "https://resource.example.com"
        assert "token_endpoint_auth_methods_supported" not in json.dumps(exported)

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_discovery_fetch_failure_allows_manual_security_config(self, mock_fetch_discovery: Mock) -> None:
        """Discovery fetch failures surface as warnings but do not block config."""
        mock_fetch_discovery.return_value = {
            "fetchError": "Connection refused",
            "sourceUrl": "https://auth.example.com/.well-known/openid-configuration",
        }
        client = Client()
        create_response = client.post("/builder/new/")
        catalogue_response = client.post(
            create_response["Location"],
            data={
                "scheme": "open-banking-uk",
                "specification": "read-write",
                "version": "4.0.1",
                "resource_groups": ["payment-initiation"],
            },
        )
        boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
        hierarchy = catalogue_scope_hierarchy(boundary, selected_resource_group_ids=("payment-initiation",))
        endpoint = next(
            endpoint
            for group in hierarchy.resource_groups
            for endpoint in group.endpoints
            if endpoint.path == "/open-banking/v4.0/pisp/domestic-payments"
        )
        scope_response = client.post(
            catalogue_response["Location"],
            data={"resource_groups": ["payment-initiation"], "endpoints": [endpoint.id]},
        )
        business_response = client.post(
            scope_response["Location"],
            data={
                "pis_creditor_account_scheme_name": "UK.OBIE.SortCodeAccountNumber",
                "pis_creditor_account_identification": "12345678901234",
                "pis_creditor_account_name": "Model Bank Payee",
            },
        )
        discovery_response = client.post(
            business_response["Location"],
            data={
                "discovery_url": "https://auth.example.com/.well-known/openid-configuration",
            },
        )

        security_response = client.get(discovery_response["Location"])

        assert security_response.status_code == 200
        content = security_response.content.decode("utf-8")
        assert "Discovery metadata is unavailable" in content
        assert "Continue manually" in content
        assert "Resource server base URL" in content

    def test_discovery_step_prefills_metadata_without_fetching_jwks(self) -> None:
        """The discovery wizard fetches OpenID metadata only, even with a blank timeout."""
        client = Client()
        create_response = client.post("/builder/new/")
        catalogue_response = client.post(
            create_response["Location"],
            data={
                "scheme": "open-banking-uk",
                "specification": "read-write",
                "version": "4.0.1",
                "resource_groups": ["payment-initiation"],
            },
        )
        boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
        hierarchy = catalogue_scope_hierarchy(boundary, selected_resource_group_ids=("payment-initiation",))
        endpoint = next(
            endpoint
            for group in hierarchy.resource_groups
            for endpoint in group.endpoints
            if endpoint.path == "/open-banking/v4.0/pisp/domestic-payments"
        )
        scope_response = client.post(
            catalogue_response["Location"],
            data={"resource_groups": ["payment-initiation"], "endpoints": [endpoint.id]},
        )
        business_response = client.post(scope_response["Location"], data={})

        with (
            patch("conformance.api.ui_views.build_json_http_client") as mock_build_http_client,
            patch("conformance.api.ui_views.OzoneModelBankClient") as mock_client_type,
        ):
            http_client = Mock(name="http_client")
            mock_build_http_client.return_value = http_client
            model_bank_client = Mock(name="model_bank_client")
            mock_client_type.return_value = model_bank_client
            model_bank_client.fetch_discovery_document.return_value = (
                DiscoveryDocument(
                    issuer="https://auth.example.com",
                    jwks_uri="https://auth.example.com/jwks",
                    raw={
                        "issuer": "https://auth.example.com",
                        "authorization_endpoint": "https://auth.example.com/authorize",
                        "token_endpoint": "https://auth.example.com/token",
                        "jwks_uri": "https://auth.example.com/jwks",
                        "token_endpoint_auth_methods_supported": ["private_key_jwt"],
                    },
                ),
                JsonHttpResponse(
                    url="https://auth.example.com/.well-known/openid-configuration",
                    status_code=200,
                    body={},
                ),
            )

            discovery_response = client.post(
                business_response["Location"],
                data={
                    "discovery_url": "https://auth.example.com/.well-known/openid-configuration",
                },
            )

        assert discovery_response.status_code == 302
        mock_build_http_client.assert_called_once_with(timeout_seconds=10.0)
        mock_client_type.assert_called_once_with(http_client)
        model_bank_client.fetch_discovery_document.assert_called_once_with(
            "https://auth.example.com/.well-known/openid-configuration"
        )
        model_bank_client.fetch_jwks.assert_not_called()
        model_bank_client.close.assert_called_once_with()

        security_response = client.get(discovery_response["Location"])
        assert security_response.status_code == 200
        content = security_response.content.decode("utf-8")
        assert "Token endpoint auth methods supported" in content
        assert "private_key_jwt" in content
        assert "JWKS URI" in content
        assert "https://auth.example.com/jwks" in content
        assert "JWKS check" not in content

    @patch("conformance.api.ui_views.start_run")
    def test_import_review_export_and_launch_uses_canonical_test_plan(self, mock_start_run: Mock) -> None:
        """Imported JSON-first plans open in review, export safely, and launch compiled state."""
        mock_start_run.return_value = {"id": "run-123", "status": "pending", "createdAt": "2026-06-03T12:00:00+00:00"}
        client = Client()
        plan_document = {
            "schemaVersion": "1.0",
            "specification": {
                "family": "OBL_READ_WRITE",
                "version": "4.0.1",
                "profile": "FAPI1_ADVANCED",
            },
            "executionMode": "development",
            "securityEnvironment": {
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "resourceBaseUrl": "https://resource.example.com",
            },
            "resourceGroups": [
                {
                    "id": "AIS",
                    "label": "Accounts",
                    "endpoints": [
                        {
                            "method": "GET",
                            "path": "/open-banking/v4.0/aisp/accounts",
                        }
                    ],
                }
            ],
            "businessTestData": {
                "inputs": {
                    "accessToken": {"value": "secret-access-token"},
                },
            },
            "metadata": {"aspspName": "Example Bank"},
        }

        import_response = client.post("/builder/import/", data={"plan_json": json.dumps(plan_document)})

        assert import_response.status_code == 302
        review_location = import_response["Location"]
        review_response = client.get(review_location)
        assert review_response.status_code == 200
        review_content = review_response.content.decode("utf-8")
        assert "Ready to launch" in review_content
        assert "secret-access-token" not in review_content
        legacy_secret_export_href = f'href="{review_location.replace("/review/", "/export.json")}?include_secrets=1"'
        assert legacy_secret_export_href not in review_content
        assert 'name="include_secrets" value="1"' in review_content

        draft_id = _draft_id_from_builder_redirect(review_location)
        safe_export = client.get(f"/builder/{draft_id}/export.json")
        rejected_get_secret_export = client.get(f"/builder/{draft_id}/export.json?include_secrets=1")
        secret_export = client.post(f"/builder/{draft_id}/export.json", data={"include_secrets": "1"})
        assert safe_export.status_code == 200
        assert rejected_get_secret_export.status_code == 405
        assert secret_export.status_code == 200
        assert safe_export["Cache-Control"] == "no-store"
        assert secret_export["Cache-Control"] == "no-store"
        assert "environment" not in safe_export.json()
        assert "environment" not in secret_export.json()
        assert safe_export.json()["resourceGroups"][0]["id"] == "AIS"
        assert safe_export.json()["businessTestData"]["inputs"]["accessToken"]["value"] == ""
        assert "secret-access-token" not in safe_export.content.decode("utf-8")
        assert secret_export.json()["businessTestData"]["inputs"]["accessToken"]["value"] == "secret-access-token"

        launch_response = client.post(f"/builder/{draft_id}/launch/")

        assert launch_response.status_code == 302
        assert launch_response["Location"] == "/runs/run-123/"
        compiled_plan = mock_start_run.call_args.kwargs["compiled_plan"]
        runtime_inputs = mock_start_run.call_args.kwargs["runtime_inputs"]
        assert "ais-at-accounts-list-200" in compiled_plan.traceability.generated_test_case_ids
        assert runtime_inputs["accessToken"] == "secret-access-token"
        assert mock_start_run.call_args.kwargs["browser_psu_prompts"] is True
        validation_result = mock_start_run.call_args.kwargs["validation_result"]
        assert isinstance(validation_result, dict)
        assert validation_result["executionMode"] == "development"
        plan_snapshot = mock_start_run.call_args.kwargs["plan_snapshot"]
        assert isinstance(plan_snapshot, dict)
        metadata = plan_snapshot["metadata"]
        assert isinstance(metadata, dict)
        assert metadata["aspspName"] == "Example Bank"


@pytest.mark.integration
class TestPlanBuilderUi:
    """Browser coverage for the participant plan-builder views."""

    def test_plan_builder_get_renders_endpoint_selection_form(self) -> None:
        """GET /plan/ renders the endpoint-selected plan-builder form."""
        response = Client().get("/plan/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Test plan builder" in content
        assert "Catalogue endpoint selection" in content
        assert "Implemented endpoints" in content
        assert "implemented_endpoint_capabilities" in content
        assert "Required" in content
        assert "Optional" in content
        assert "ais.transactions.date-range-filtering" in content
        assert "/open-banking/v4.0/aisp/accounts" in content
        assert "Plan spec JSON" in content
        assert "Advanced JSON import/export" in content
        assert "Bundled suite" not in content
        assert "Manifest JSON" not in content
        assert "Model bank example" not in content
        assert "selected_step_ids" not in content
        assert "deselect_step_ids" not in content
        assert "hx-post" not in content

    def test_preview_post_renders_rich_read_only_generated_plan(self) -> None:
        """POST /plan/preview/ renders read-only generated catalogue rows."""
        response = Client().post("/plan/preview/", data=_valid_plan_form_data())

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Open Banking v4.0 AIS" in content
        assert "Generated tests" in content
        assert "Implemented endpoints" in content
        assert "Selected capabilities" in content
        assert "Certification plan eligible" in content
        assert "Generated plan preview" in content
        assert "Secret-safe plan spec export" in content
        assert "Selected endpoint" in content
        assert "Runtime/auth" in content
        assert "Audit details" in content
        assert "ais-at-accounts-list-200" in content
        assert "selected_step_ids" not in content
        assert '"accessToken"' not in content.split("Secret-safe plan spec export", maxsplit=1)[1]

    def test_preview_post_returns_400_for_missing_runtime_input(self) -> None:
        """Missing selected-endpoint runtime data renders validation errors with HTTP 400."""
        response = Client().post(
            "/plan/preview/",
            data=_plan_form_data(endpoint_ids=[_ais_accounts_endpoint_id()]),
        )

        assert response.status_code == 400
        content = response.content.decode("utf-8")
        assert "Required runtime input" in content
        assert "AIS resource server base URL" in content
        assert "AIS access token" in content

    def test_preview_post_warns_for_imported_assertion_overrides(self) -> None:
        """Imported assertion overrides render as prominent non-certifying warnings."""
        raw_plan_spec: dict[str, JsonValue] = {
            "schemaVersion": "v1",
            "catalogue": {"standard": "open-banking", "version": "v4.0", "api": "ais"},
            "securityProfile": "fapi1-advanced",
            "implementedEndpoints": [
                {
                    "method": "GET",
                    "path": "/open-banking/v4.0/aisp/accounts",
                    "resourceGroup": "Accounts",
                }
            ],
            "runtimeInputs": {
                "resourceBaseUrl": "https://resource.example.com",
                "accessToken": "secret-access-token",
            },
            "assertionOverrides": [
                {
                    "testCaseId": "ais-at-accounts-list-200",
                    "assertionId": "status-200",
                    "reason": "participant diagnostic import",
                }
            ],
        }

        response = Client().post(
            "/plan/preview/",
            data={
                "config_json": json.dumps(VALID_CONFIG),
                "plan_spec_json": json.dumps(raw_plan_spec),
                "guided_standard": "open-banking",
                "guided_spec_version": "v4.0",
                "guided_api": "ais",
                "implemented_endpoint_ids": [],
                "implemented_endpoint_capabilities": [],
            },
        )

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Certification plan ineligible" in content
        assert "Non-certifying imported override" in content
        assert "participant diagnostic import" in content

    @patch("conformance.api.ui_views.start_run")
    def test_launch_post_starts_compiled_catalogue_run(self, mock_start_run: Mock) -> None:
        """Launch validates the form and hands compiled catalogue state to lifecycle code."""
        mock_start_run.return_value = {"id": "run-123", "status": "pending", "createdAt": "2026-06-03T12:00:00+00:00"}

        response = Client().post("/plan/launch/", data=_valid_plan_form_data())

        assert response.status_code == 302
        assert response["Location"] == "/runs/run-123/"
        assert mock_start_run.call_count == 1
        assert mock_start_run.call_args.kwargs["browser_psu_prompts"] is True
        compiled_plan = mock_start_run.call_args.kwargs["compiled_plan"]
        runtime_inputs = mock_start_run.call_args.kwargs["runtime_inputs"]
        assert "ais-at-accounts-list-200" in compiled_plan.traceability.generated_test_case_ids
        assert runtime_inputs["resourceBaseUrl"] == "https://resource.example.com"
        assert runtime_inputs["accessToken"] == "secret-access-token"
        assert "manifest" not in mock_start_run.call_args.kwargs
        assert "plan" not in mock_start_run.call_args.kwargs

    @patch("conformance.api.ui_views.start_run")
    def test_launch_post_renders_conflict_when_run_is_active(self, mock_start_run: Mock) -> None:
        """Active-run conflicts render HTTP 409 with a detail-page link."""
        mock_start_run.side_effect = RunConflictError("active-run")

        response = Client().post("/plan/launch/", data=_valid_plan_form_data())

        assert response.status_code == 409
        content = response.content.decode("utf-8")
        assert "A run is already active" in content
        assert "/runs/active-run/" in content

    def test_post_views_are_csrf_protected(self) -> None:
        """Browser POST routes require the Django CSRF token when enforcement is enabled."""
        client = Client(enforce_csrf_checks=True)

        rejected = client.post("/plan/preview/", data=_valid_plan_form_data())

        assert rejected.status_code == 403

        get_response = client.get("/plan/")
        csrf_token = get_response.cookies["csrftoken"].value
        accepted = client.post(
            "/plan/preview/",
            data={**_valid_plan_form_data(), "csrfmiddlewaretoken": csrf_token},
        )
        assert accepted.status_code == 200

    @patch("conformance.api.ui_views.start_run")
    def test_launch_post_is_csrf_protected(self, mock_start_run: Mock) -> None:
        """Launch POST rejects missing CSRF tokens and accepts token-backed submissions."""
        mock_start_run.return_value = {"id": "run-123", "status": "pending", "createdAt": "2026-06-03T12:00:00+00:00"}
        client = Client(enforce_csrf_checks=True)

        rejected = client.post("/plan/launch/", data=_valid_plan_form_data())

        assert rejected.status_code == 403
        mock_start_run.assert_not_called()

        get_response = client.get("/plan/")
        csrf_token = get_response.cookies["csrftoken"].value
        accepted = client.post(
            "/plan/launch/",
            data={**_valid_plan_form_data(), "csrfmiddlewaretoken": csrf_token},
        )

        assert accepted.status_code == 302
        assert accepted["Location"] == "/runs/run-123/"
        assert mock_start_run.call_count == 1


@pytest.mark.integration
class TestRunDetailUi:
    """Browser coverage for run detail and partial views."""

    def test_run_detail_renders_compiled_step_snapshot_and_result(self) -> None:
        """Run detail renders compiled-plan step snapshots and completed result evidence."""
        record = run_store.create_run(
            planned_steps=(
                RunPlanStep(
                    step_id="ais-at-accounts-list-200-request",
                    name="List AIS accounts",
                    kind="http",
                    group="ais-at-accounts-list-200",
                    phase="execution",
                    mandatory=True,
                    optional=False,
                    order=0,
                ),
            )
        )
        run_store.mark_running(record.run_id)
        run_store.mark_completed(
            record.run_id,
            result={
                "status": "passed",
                "summary": {"total": 1, "passed": 1, "failed": 0, "warn": 0, "skipped": 0},
                "plan": {"selectedSteps": 1, "deselectedSteps": 0, "mandatorySelected": 1, "mandatoryDeselected": 0},
                "catalogue": {
                    "standard": "open-banking",
                    "version": "v4.0",
                    "api": "ais",
                    "catalogueVersion": "2026.07.legacy-fcs-ais-at.1",
                    "generatedTestCaseIds": ["ais-at-accounts-list-200"],
                    "selectedEndpoints": [
                        {
                            "method": "GET",
                            "path": "/open-banking/v4.0/aisp/accounts",
                            "resourceGroup": "Accounts",
                        }
                    ],
                    "selectedCapabilities": [
                        {
                            "method": "GET",
                            "path": "/open-banking/v4.0/aisp/accounts",
                            "capabilityId": "ais.accounts.list.core",
                            "label": "AIS accounts list baseline coverage",
                            "required": True,
                        }
                    ],
                    "applicabilityDecisions": [],
                    "runtimeInputSnapshot": [],
                    "nonCertifyingReasons": [],
                },
                "certificationEligibility": {"eligible": True},
                "steps": [
                    {
                        "name": "ais-at-accounts-list-200-request",
                        "status": "passed",
                        "message": "OK",
                        "details": {
                            "request": {
                                "method": "GET",
                                "url": "https://resource.example.com/open-banking/v4.0/aisp/accounts",
                            },
                            "response": {"statusCode": 200},
                            "assertions": [{"status": "passed", "message": "HTTP 200"}],
                        },
                    }
                ],
            },
        )

        response = Client().get(f"/runs/{record.run_id}/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert f"Run {record.run_id}" in content
        assert "List AIS accounts" in content
        assert "ais-at-accounts-list-200-request" in content
        assert "passed" in content
        assert "Certification" in content
        assert "Catalogue traceability" in content
        assert "2026.07.legacy-fcs-ais-at.1" in content
        assert "Selected capabilities" in content

    def test_run_detail_returns_404_for_unknown_run(self) -> None:
        """Unknown run detail pages return 404."""
        response = Client().get("/runs/missing/")

        assert response.status_code == 404

    def test_run_result_download_returns_completed_result_json(self) -> None:
        """Completed result downloads return the masked JSON result."""
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_completed(record.run_id, result={"status": "passed", "steps": []})

        response = Client().get(f"/runs/{record.run_id}/result.json")

        assert response.status_code == 200
        assert response.json() == {"status": "passed", "steps": []}

    def test_run_id_can_be_extracted_from_redirect_location(self) -> None:
        """Redirect helper returns the last URL path segment."""
        assert _run_id_from_redirect("/runs/run-123/") == "run-123"
