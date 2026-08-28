"""Integration tests for participant-facing browser UI views."""

from __future__ import annotations

import json
from collections.abc import Iterator
from unittest.mock import Mock, patch

import pytest
from django.test import Client

from conformance.api.builder_wizard import EndpointOption, catalogue_scope_hierarchy, endpoint_capability_value
from conformance.api.run_store import RunPlanStep, run_store
from conformance.catalogue import PlanDocumentBoundary
from conformance.http import JsonHttpResponse
from conformance.ozone_client import DiscoveryDocument


@pytest.fixture(autouse=True)
def _reset_global_stores() -> Iterator[None]:
    """Reset process-local singleton stores around each UI test.

    Yields:
        Control back to pytest while the test executes.
    """
    run_store.reset()
    yield
    run_store.reset()


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


def _valid_security_form_data(**overrides: str) -> dict[str, str]:
    """Build valid guided security-step form data.

    Args:
        overrides: Field values to override in the default valid submission.

    Returns:
        Form data containing the security fields required to continue to scope.
    """
    data = {
        "oauth_client_id": "client-123",
        "oauth_redirect_uri": "https://client.example.com/callback",
        "resource_server_base_url": "https://resource.example.com",
    }
    data.update(overrides)
    return data


def _scope_endpoint(*, selected_resource_group_id: str, path: str) -> EndpointOption:
    """Return a rendered scope endpoint for a resource group and path.

    Args:
        selected_resource_group_id: Resource-group id to reveal in the scope hierarchy.
        path: Standards endpoint path to find.

    Returns:
        Matching endpoint option.

    Raises:
        AssertionError: If the endpoint is not available in the selected group.
    """
    hierarchy = catalogue_scope_hierarchy(
        PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1"),
        selected_resource_group_ids=(selected_resource_group_id,),
    )
    for group in hierarchy.resource_groups:
        for endpoint in group.endpoints:
            if endpoint.path == path:
                return endpoint
    raise AssertionError(f"Endpoint option not found for {path}")


def _scope_location_after_security(client: Client) -> str:
    """Create a draft and return its scope URL after security settings are saved.

    Args:
        client: Django test client that owns the builder session.

    Returns:
        Scope route location for the draft.
    """
    create_response = client.post("/builder/new/")
    catalogue_response = client.post(
        create_response["Location"],
        data={
            "scheme": "open-banking-uk",
            "specification": "read-write",
            "version": "4.0.1",
        },
    )
    discovery_response = client.post(
        catalogue_response["Location"],
        data={"discovery_url": "https://example.com/.well-known/openid-configuration"},
    )
    security_response = client.post(
        discovery_response["Location"],
        data=_valid_security_form_data(),
    )
    return str(security_response["Location"])


def _assert_requirement_badge(content: str, label: str, badge: str) -> None:
    """Assert that a rendered field label includes a requirement badge.

    Args:
        content: Rendered HTML response body.
        label: Field label text expected before the badge.
        badge: Requirement badge text expected after the label.
    """
    expected = f'{label} <span class="requirement-badge {badge.lower()}">{badge}</span>'
    field_header_label = f">{label}</label>"
    badge_markup = f'class="requirement-badge {badge.lower()}">{badge}</span>'
    assert expected in content or (field_header_label in content and badge_markup in content)


@pytest.mark.integration
@pytest.mark.django_db
class TestBuilderWizardUi:
    """Browser coverage for the canonical multi-page builder flow."""

    def test_new_builder_starts_with_specification_only(self) -> None:
        """POST /builder/new/ creates a draft and renders specification/profile step one."""
        client = Client()

        response = client.post("/builder/new/")

        assert response.status_code == 302
        location = response["Location"]
        assert location.startswith("/builder/")
        assert location.endswith("/catalogue/")

        step_response = client.get(location)
        assert step_response.status_code == 200
        content = step_response.content.decode("utf-8")
        assert "Choose specification" in content
        assert "Step 1: specification and profile" in content
        assert "Open Banking UK" in content
        assert "Read/Write" in content
        assert "4.0.1" in content
        assert 'name="resource_groups"' not in content
        assert "Implemented endpoints" not in content
        assert "Plan spec JSON" not in content

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_builder_pages_show_required_and_optional_field_badges(self, mock_fetch_discovery: Mock) -> None:
        """Builder pages label representative user-entered fields by requiredness."""
        mock_fetch_discovery.return_value = {}
        client = Client()
        create_response = client.post("/builder/new/")

        catalogue_response = client.get(create_response["Location"])
        assert catalogue_response.status_code == 200
        catalogue_content = catalogue_response.content.decode("utf-8")
        assert "requirement-badge" not in catalogue_content

        saved_catalogue_response = client.post(
            create_response["Location"],
            data={
                "scheme": "open-banking-uk",
                "specification": "read-write",
                "version": "4.0.1",
            },
        )
        discovery_response = client.get(saved_catalogue_response["Location"])
        assert discovery_response.status_code == 200
        discovery_content = discovery_response.content.decode("utf-8")
        assert "requirement-badge" not in discovery_content
        assert "leave it blank and fill values manually" in discovery_content

        saved_discovery_response = client.post(
            saved_catalogue_response["Location"],
            data={"discovery_url": ""},
        )
        mock_fetch_discovery.assert_not_called()
        security_response = client.get(saved_discovery_response["Location"])
        assert security_response.status_code == 200
        security_content = security_response.content.decode("utf-8")
        _assert_requirement_badge(security_content, "Client ID", "Conditional")
        _assert_requirement_badge(security_content, "Authorization endpoint", "Conditional")
        _assert_requirement_badge(security_content, "Resource server base URL", "Conditional")
        _assert_requirement_badge(security_content, "Signing key ID", "Conditional")
        assert "These fields are included only when they can affect execution" in security_content
        assert "Type: HTTPS URL" in security_content
        assert "FAPI signing fields are conditional" in security_content
        assert "mTLS client certificate and private key are conditional" in security_content

        saved_security_response = client.post(
            saved_discovery_response["Location"],
            data={},
        )
        scope_response = client.get(saved_security_response["Location"])
        assert scope_response.status_code == 200
        assert "baseline and optional labels describe generated conformance coverage" in scope_response.content.decode(
            "utf-8"
        )
        endpoint = _scope_endpoint(
            selected_resource_group_id="account-and-transaction",
            path="/open-banking/v4.0/aisp/transactions",
        )
        saved_scope_response = client.post(
            saved_security_response["Location"],
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
        business_response = client.get(saved_scope_response["Location"])
        assert business_response.status_code == 200
        business_content = business_response.content.decode("utf-8")
        _assert_requirement_badge(business_content, "Consented account identifier", "Optional")
        assert '<div class="field-heading">' in business_content
        assert "Advanced AIS resource IDs JSON" in business_content

        saved_business_response = client.post(saved_scope_response["Location"], data={})
        runtime_response = client.get(saved_business_response["Location"])
        assert runtime_response.status_code == 200
        runtime_content = runtime_response.content.decode("utf-8")
        _assert_requirement_badge(runtime_content, "Resource server base URL", "Required")
        assert "accessToken" not in runtime_content

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_builder_business_page_marks_ais_account_id_required_for_account_scope(
        self,
        mock_fetch_discovery: Mock,
    ) -> None:
        """AIS account-scoped endpoint selections show the account id as required."""
        mock_fetch_discovery.return_value = {}
        client = Client()
        create_response = client.post("/builder/new/")
        saved_catalogue_response = client.post(
            create_response["Location"],
            data={
                "scheme": "open-banking-uk",
                "specification": "read-write",
                "version": "4.0.1",
            },
        )
        saved_discovery_response = client.post(
            saved_catalogue_response["Location"],
            data={"discovery_url": ""},
        )
        saved_security_response = client.post(
            saved_discovery_response["Location"],
            data={},
        )
        endpoint = _scope_endpoint(
            selected_resource_group_id="account-and-transaction",
            path="/open-banking/v4.0/aisp/accounts/{AccountId}/balances",
        )
        saved_scope_response = client.post(
            saved_security_response["Location"],
            data={"resource_groups": ["account-and-transaction"], "endpoints": [endpoint.id]},
        )

        business_response = client.get(saved_scope_response["Location"])
        business_content = business_response.content.decode("utf-8")

        assert business_response.status_code == 200
        assert '<div class="field-heading">' in business_content
        _assert_requirement_badge(business_content, "Consented account identifier", "Required")
        _assert_requirement_badge(business_content, "Transaction from date", "Optional")
        _assert_requirement_badge(business_content, "Transaction to date", "Optional")

    def test_catalogue_boundary_post_continues_to_discovery(self) -> None:
        """The first wizard step saves the specification and moves to discovery."""
        client = Client()
        create_response = client.post("/builder/new/")
        draft_id = _draft_id_from_builder_redirect(create_response["Location"])

        response = client.post(
            create_response["Location"],
            data={
                "scheme": "open-banking-uk",
                "specification": "read-write",
                "version": "4.0.1",
            },
        )

        assert response.status_code == 302
        assert response["Location"] == f"/builder/{draft_id}/config/discovery/"
        discovery_response = client.get(response["Location"])
        assert discovery_response.status_code == 200
        content = discovery_response.content.decode("utf-8")
        assert "Step 2: security environment discovery" in content
        assert "Optionally enter the `.well-known/openid-configuration` URL" in content

    def test_selector_only_dcr_boundary_blocks_continuation(self) -> None:
        """DCR can be displayed but still cannot continue into the executable flow."""
        client = Client()
        create_response = client.post("/builder/new/")
        draft_id = _draft_id_from_builder_redirect(create_response["Location"])

        response = client.post(
            create_response["Location"],
            data={
                "scheme": "open-banking-uk",
                "specification": "dynamic-client-registration",
                "version": "3.4",
            },
        )

        assert response.status_code == 400
        content = response.content.decode("utf-8")
        assert "Dynamic Client Registration v3.4 is shown as a selector-only example." in content

        scope_response = client.get(f"/builder/{draft_id}/scope/")
        assert scope_response.status_code == 302
        assert scope_response["Location"] == f"/builder/{draft_id}/catalogue/"

    def test_scope_allows_security_environment_to_be_empty_before_resource_groups(self) -> None:
        """The scope step can be opened before optional security fields are filled."""
        client = Client()
        create_response = client.post("/builder/new/")
        draft_id = _draft_id_from_builder_redirect(create_response["Location"])
        client.post(
            create_response["Location"],
            data={
                "scheme": "open-banking-uk",
                "specification": "read-write",
                "version": "4.0.1",
            },
        )

        response = client.get(f"/builder/{draft_id}/scope/")

        assert response.status_code == 200
        assert "Select test plan endpoints" in response.content.decode("utf-8")

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_security_step_continues_to_resource_group_scope(self, mock_fetch_discovery: Mock) -> None:
        """Security details are collected before resource groups and endpoint selections."""
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
            },
        )
        discovery_response = client.post(
            catalogue_response["Location"],
            data={"discovery_url": "https://example.com/.well-known/openid-configuration"},
        )

        security_response = client.post(
            discovery_response["Location"],
            data=_valid_security_form_data(),
        )

        assert security_response.status_code == 302
        assert security_response["Location"].endswith("/scope/")
        scope_response = client.get(security_response["Location"])
        assert scope_response.status_code == 200
        content = scope_response.content.decode("utf-8")
        assert "Steps 4 and 5: resource groups, endpoints, and capabilities" in content
        assert "account-and-transaction" in content
        assert "payment-initiation" in content
        assert "GET /aisp/transactions" not in content

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_scope_step_filters_endpoints_and_features_via_dynamic_fragment(self, mock_fetch_discovery: Mock) -> None:
        """The dynamic scope fragment reveals endpoints and features under selected parents."""
        mock_fetch_discovery.return_value = {}
        client = Client()
        scope_location = _scope_location_after_security(client)
        draft_id = _draft_id_from_builder_redirect(scope_location)
        endpoint = _scope_endpoint(
            selected_resource_group_id="account-and-transaction",
            path="/open-banking/v4.0/aisp/transactions",
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

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_scope_step_saves_selected_resource_endpoint_and_feature(self, mock_fetch_discovery: Mock) -> None:
        """POST /builder/<draft>/scope/ stores selected scope values and continues."""
        mock_fetch_discovery.return_value = {}
        client = Client()
        scope_location = _scope_location_after_security(client)
        endpoint = _scope_endpoint(
            selected_resource_group_id="account-and-transaction",
            path="/open-banking/v4.0/aisp/transactions",
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
        assert "Business test data" in content
        assert "Resource server targets" not in content
        assert "accessToken" not in content
        assert "Consented account identifier" in content
        assert "Advanced AIS resource IDs JSON" in content

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_business_config_step_shows_cbpii_debtor_account_inputs(self, mock_fetch_discovery: Mock) -> None:
        """CBPII selections collect participant debtor-account business data."""
        mock_fetch_discovery.return_value = {}
        client = Client()
        scope_location = _scope_location_after_security(client)
        endpoint = _scope_endpoint(
            selected_resource_group_id="confirmation-of-funds",
            path="/open-banking/v4.0/cbpii/funds-confirmation-consents",
        )

        response = client.post(
            scope_location,
            data={
                "resource_groups": ["confirmation-of-funds"],
                "endpoints": [endpoint.id],
            },
        )

        assert response.status_code == 302
        content_response = client.get(response["Location"])
        assert content_response.status_code == 200
        content = content_response.content.decode("utf-8")
        assert "Confirmation of Funds" in content
        assert "Debtor account scheme" in content
        assert "Debtor account identification" in content
        assert "Debtor account name" in content
        assert "No business data inputs required" not in content

        saved_response = client.post(
            response["Location"],
            data={
                "cbpii_debtor_account_scheme_name": "UK.OBIE.SortCodeAccountNumber",
                "cbpii_debtor_account_identification": "12345678901234",
                "cbpii_debtor_account_name": "Model Bank Account",
            },
        )
        runtime_response = client.post(saved_response["Location"], data={})
        draft_id = _draft_id_from_builder_redirect(runtime_response["Location"])
        export_response = client.get(f"/builder/{draft_id}/export.json")

        assert export_response.status_code == 200
        exported = export_response.json()
        assert exported["businessTestData"]["cbpii"] == {
            "debtorAccount": {
                "schemeName": "UK.OBIE.SortCodeAccountNumber",
                "identification": "",
                "name": "Model Bank Account",
            }
        }
        assert "inputs" not in exported["businessTestData"]
        assert "accessToken" not in json.dumps(exported)

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_business_config_step_shows_pis_payment_inputs(self, mock_fetch_discovery: Mock) -> None:
        """PIS selections collect participant payment business data."""
        mock_fetch_discovery.return_value = {}
        client = Client()
        scope_location = _scope_location_after_security(client)
        endpoint = _scope_endpoint(
            selected_resource_group_id="payment-initiation",
            path="/open-banking/v4.0/pisp/domestic-payments",
        )

        response = client.post(
            scope_location,
            data={
                "resource_groups": ["payment-initiation"],
                "endpoints": [endpoint.id],
            },
        )

        assert response.status_code == 302
        content_response = client.get(response["Location"])
        assert content_response.status_code == 200
        content = content_response.content.decode("utf-8")
        assert "Payment Initiation" in content
        assert "Domestic creditor account scheme" in content
        assert "Instructed amount" in content
        assert "No business data inputs required" not in content

        invalid_response = client.post(response["Location"], data={})
        assert invalid_response.status_code == 400
        invalid_content = invalid_response.content.decode("utf-8")
        assert "Domestic creditor account is required for selected PIS endpoints." in invalid_content
        assert "Instructed amount is required for selected PIS endpoints." in invalid_content

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_full_guided_flow_exports_canonical_json_and_masks_secrets(self, mock_fetch_discovery: Mock) -> None:
        """The reordered wizard exports canonical JSON-first plans from review."""
        mock_fetch_discovery.return_value = {
            "issuer": "https://example.com",
            "authorization_endpoint": "https://example.com/authorize",
            "token_endpoint": "https://example.com/token",
            "jwks_uri": "https://example.com/jwks",
        }
        client = Client()
        scope_location = _scope_location_after_security(client)
        endpoint = _scope_endpoint(
            selected_resource_group_id="account-and-transaction",
            path="/open-banking/v4.0/aisp/accounts",
        )
        scope_response = client.post(
            scope_location,
            data={"resource_groups": ["account-and-transaction"], "endpoints": [endpoint.id]},
        )
        business_response = client.post(scope_response["Location"], data={})
        runtime_response = client.post(business_response["Location"], data={})

        assert runtime_response.status_code == 302
        assert runtime_response["Location"].endswith("/review/")
        review_response = client.get(runtime_response["Location"])
        assert review_response.status_code == 200
        content = review_response.content.decode("utf-8")
        assert "Review generated test plan" in content
        assert "Safe export preview" in content
        assert "accessToken" not in content
        assert "fixture-account-id" not in content
        draft_id = _draft_id_from_builder_redirect(runtime_response["Location"])

        safe_export = client.get(f"/builder/{draft_id}/export.json")

        assert safe_export.status_code == 200
        exported = safe_export.json()
        assert exported["schemaVersion"] == "1.0"
        assert exported["specification"] == {
            "family": "OBL_READ_WRITE",
            "profile": "FAPI1_ADVANCED",
            "version": "4.0.1",
        }
        assert exported["resourceGroups"][0]["id"] == "AIS"
        assert exported["securityEnvironment"]["resourceBaseUrl"] == "https://resource.example.com"
        assert "ais" not in exported["businessTestData"]
        assert "inputs" not in exported["businessTestData"]

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
            },
        )
        discovery_response = client.post(
            catalogue_response["Location"],
            data={"discovery_url": "https://auth.example.com/.well-known/openid-configuration"},
        )

        security_response = client.get(discovery_response["Location"])

        assert security_response.status_code == 200
        content = security_response.content.decode("utf-8")
        assert "Token endpoint auth methods supported" in content
        assert "private_key_jwt, tls_client_auth" in content
        assert "JWKS URI" in content
        assert "https://auth.example.com/jwks" in content
        assert 'value="https://auth.example.com/token"' in content

        security_save = client.post(
            discovery_response["Location"],
            data={
                "oauth_client_id": "client-123",
                "oauth_redirect_uri": "https://client.example.com/callback",
                "oauth_authorization_endpoint": "https://auth.example.com/authorize",
                "oauth_issuer": "https://auth.example.com",
                "oauth_token_endpoint": "https://auth.example.com/token",
                "oauth_response_type": "code id_token",
                "oauth_request_object_signing_alg": "PS256",
                "resource_server_base_url": "https://resource.example.com",
            },
        )
        endpoint = _scope_endpoint(
            selected_resource_group_id="account-and-transaction",
            path="/open-banking/v4.0/aisp/accounts",
        )
        scope_response = client.post(
            security_save["Location"],
            data={"resource_groups": ["account-and-transaction"], "endpoints": [endpoint.id]},
        )
        business_response = client.post(scope_response["Location"], data={"ais_consented_account_id": "account-123"})
        runtime_response = client.post(business_response["Location"], data={})
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
            },
        )
        discovery_response = client.post(
            catalogue_response["Location"],
            data={"discovery_url": "https://auth.example.com/.well-known/openid-configuration"},
        )

        security_response = client.get(discovery_response["Location"])

        assert security_response.status_code == 200
        content = security_response.content.decode("utf-8")
        assert "Discovery metadata is unavailable" in content
        assert "Continue manually" in content
        assert "Resource server base URL" in content

    def test_discovery_step_prefills_metadata_without_fetching_jwks(self) -> None:
        """The discovery wizard fetches OpenID metadata only with the fixed timeout."""
        client = Client()
        create_response = client.post("/builder/new/")
        catalogue_response = client.post(
            create_response["Location"],
            data={
                "scheme": "open-banking-uk",
                "specification": "read-write",
                "version": "4.0.1",
            },
        )
        form_response = client.get(catalogue_response["Location"])
        assert form_response.status_code == 200
        assert "Timeout seconds" not in form_response.content.decode("utf-8")

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
                catalogue_response["Location"],
                data={"discovery_url": "https://auth.example.com/.well-known/openid-configuration"},
            )

        assert discovery_response.status_code == 302
        mock_build_http_client.assert_called_once_with()
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
            "businessTestData": {},
            "metadata": {"aspspName": "Example Bank"},
        }

        import_response = client.post("/builder/import/", data={"plan_json": json.dumps(plan_document)})

        assert import_response.status_code == 302
        review_location = import_response["Location"]
        review_response = client.get(review_location)
        assert review_response.status_code == 200
        review_content = review_response.content.decode("utf-8")
        assert "Ready to launch" in review_content
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
        assert "inputs" not in safe_export.json()["businessTestData"]
        assert "inputs" not in secret_export.json()["businessTestData"]

        launch_response = client.post(f"/builder/{draft_id}/launch/")

        assert launch_response.status_code == 302
        assert launch_response["Location"] == "/runs/run-123/"
        compiled_plan = mock_start_run.call_args.kwargs["compiled_plan"]
        runtime_inputs = mock_start_run.call_args.kwargs["runtime_inputs"]
        assert "ais-at-accounts-list-200" in compiled_plan.traceability.generated_test_case_ids
        assert dict(runtime_inputs) == {
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "resourceBaseUrl": "https://resource.example.com",
        }
        assert mock_start_run.call_args.kwargs["browser_psu_prompts"] is True
        validation_result = mock_start_run.call_args.kwargs["validation_result"]
        assert isinstance(validation_result, dict)
        assert validation_result["executionMode"] == "development"
        plan_snapshot = mock_start_run.call_args.kwargs["plan_snapshot"]
        assert isinstance(plan_snapshot, dict)
        metadata = plan_snapshot["metadata"]
        assert isinstance(metadata, dict)
        assert metadata["aspspName"] == "Example Bank"

    @patch("conformance.api.ui_views.start_run")
    def test_imported_cbpii_plan_can_be_rescoped_to_ais_business_data(self, mock_start_run: Mock) -> None:
        """Imported CBPII business data is pruned after switching scope to AIS."""
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
            "resourceGroups": ["CBPII"],
            "businessTestData": {
                "cbpii": {
                    "debtorAccount": {
                        "schemeName": "UK.OBIE.SortCodeAccountNumber",
                        "identification": "12345678901234",
                        "name": "Model Bank Account",
                    }
                }
            },
            "metadata": {"aspspName": "Example Bank"},
        }
        endpoint = _scope_endpoint(
            selected_resource_group_id="account-and-transaction",
            path="/open-banking/v4.0/aisp/accounts/{AccountId}",
        )
        import_response = client.post("/builder/import/", data={"plan_json": json.dumps(plan_document)})
        draft_id = _draft_id_from_builder_redirect(import_response["Location"])
        scope_response = client.post(
            f"/builder/{draft_id}/scope/",
            data={"resource_groups": ["account-and-transaction"], "endpoints": [endpoint.id]},
        )

        business_page = client.get(scope_response["Location"])
        business_response = client.post(
            scope_response["Location"],
            data={
                "ais_consented_account_id": "account-123",
                "ais_transaction_from_date": "2026-01-01T00:00:00Z",
                "ais_transaction_to_date": "2026-01-31T23:59:59Z",
            },
        )
        runtime_response = client.post(business_response["Location"], data={})
        exported = client.get(f"/builder/{draft_id}/export.json").json()
        launch_response = client.post(f"/builder/{draft_id}/launch/")

        assert business_page.status_code == 200
        assert "Consented account identifier" in business_page.content.decode("utf-8")
        assert runtime_response.status_code == 302
        assert exported["resourceGroups"][0]["id"] == "AIS"
        assert "cbpii" not in exported["businessTestData"]
        assert exported["businessTestData"]["ais"] == {
            "accountIds": ["account-123"],
            "transactionFromDate": "2026-01-01T00:00:00Z",
            "transactionToDate": "2026-01-31T23:59:59Z",
        }
        assert launch_response.status_code == 302
        runtime_inputs = mock_start_run.call_args.kwargs["runtime_inputs"]
        assert runtime_inputs["consentedAccountId"] == "account-123"

    def test_import_rejects_legacy_v2_documents(self) -> None:
        """Browser import accepts only canonical schemaVersion 1.0 plans."""
        client = Client()
        plan_document = {
            "schemaVersion": "v2",
            "scheme": "open-banking-uk",
            "specification": "read-write",
            "version": "4.0.1",
            "securityProfile": "fapi1-advanced",
            "scope": {"resourceGroups": []},
            "config": {"discoveryUrl": "https://auth.example.com/.well-known/openid-configuration"},
        }

        response = client.post("/builder/import/", data={"plan_json": json.dumps(plan_document)})

        assert response.status_code == 400
        assert "canonical schemaVersion 1.0 test plan document" in response.content.decode("utf-8")

    def test_removed_single_page_builder_routes_return_404(self) -> None:
        """The legacy /plan/ builder routes are no longer mounted."""
        client = Client()

        assert client.get("/plan/").status_code == 404
        assert client.post("/plan/preview/", data={}).status_code == 404
        assert client.post("/plan/launch/", data={}).status_code == 404


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
        assert 'href="/">Home page</a>' in content
        assert "New plan" not in content

    def test_failed_run_detail_shows_home_page_action(self) -> None:
        """Failed terminal run detail pages return participants to the home page."""
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_failed(record.run_id, error="Participant callback timed out")

        response = Client().get(f"/runs/{record.run_id}/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Participant callback timed out" in content
        assert 'href="/">Home page</a>' in content
        assert "New plan" not in content

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
