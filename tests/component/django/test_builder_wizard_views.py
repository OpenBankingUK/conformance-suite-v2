"""Component tests for the guided builder wizard routes and rendered pages."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from html.parser import HTMLParser
from typing import Any
from unittest.mock import Mock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from conformance.api.builder_draft_store import SessionBuilderDraftStore
from conformance.api.builder_wizard import EndpointOption, catalogue_scope_hierarchy, endpoint_capability_value
from conformance.api.run_store import run_store
from conformance.catalogue import PlanDocumentBoundary
from conformance.http import JsonHttpResponse
from conformance.ozone_client import DiscoveryDocument
from tests.support.run_config import RUN_READY_SECURITY_ENVIRONMENT

pytestmark = pytest.mark.component


@pytest.fixture(autouse=True)
def _reset_global_stores() -> Iterator[None]:
    """Reset process-local singleton stores around each UI test.

    Yields:
        Control back to pytest while the test executes.
    """
    run_store.reset()
    yield
    run_store.reset()


def _draft_id_from_builder_redirect(location: str) -> str:
    """Extract the builder draft id from a wizard redirect response.

    Args:
        location: Redirect target from the new-builder route.

    Returns:
        Draft id segment from the redirect target.
    """
    return location.split("/")[2]


class _FormFieldCollector(HTMLParser):
    """Collect submittable field values from a rendered wizard page.

    Attributes:
        values: Field name to rendered value, as a browser would submit them.
    """

    def __init__(self) -> None:
        """Initialise the collector with no captured fields."""
        super().__init__(convert_charrefs=True)
        self.values: dict[str, str] = {}
        self._textarea_name: str | None = None
        self._select_name: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Capture input values, select options and open textarea elements.

        Args:
            tag: Element name.
            attrs: Element attributes.
        """
        attributes = dict(attrs)
        if tag == "option" and self._select_name is not None:
            if self._select_name not in self.values or "selected" in attributes:
                self.values[self._select_name] = attributes.get("value") or ""
            return
        name = attributes.get("name")
        if name is None:
            return
        if tag == "select":
            self._select_name = name
        elif tag == "input" and attributes.get("type") not in {"checkbox", "radio", "submit"}:
            self.values[name] = attributes.get("value") or ""
        elif tag == "textarea":
            self._textarea_name = name
            self.values[name] = ""

    def handle_data(self, data: str) -> None:
        """Capture the body of an open textarea element.

        Args:
            data: Character data inside the current element.
        """
        if self._textarea_name is not None:
            self.values[self._textarea_name] += data

    def handle_endtag(self, tag: str) -> None:
        """Close the current textarea or select element.

        Args:
            tag: Element name.
        """
        if tag == "textarea":
            self._textarea_name = None
        elif tag == "select":
            self._select_name = None


def _valid_import_plan() -> dict[str, Any]:
    """Return a minimal valid Read/Write plan for browser import tests.

    Returns:
        Canonical schemaVersion 1.0 test-plan JSON object.
    """
    return {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "4.0.1",
            "openApiDocumentUpdate": "Update-1",
            "profile": "FAPI1_ADVANCED",
        },
        "executionMode": "development",
        "securityEnvironment": {
            **RUN_READY_SECURITY_ENVIRONMENT,
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "resourceBaseUrl": "https://resource.example.com",
        },
        "resourceGroups": [
            {
                "id": "AIS",
                "label": "Accounts",
                "endpoints": [{"method": "GET", "path": "/open-banking/v4.0/aisp/accounts"}],
            }
        ],
        "businessTestData": {},
        "metadata": {"aspspName": "Example Bank"},
    }


def _rendered_form_data(html: str, **overrides: str) -> dict[str, str]:
    """Replay a rendered wizard page as a browser form submission.

    Collapsed advanced JSON textareas are pre-filled and are submitted by the
    browser whether or not the participant expands them, which is what makes the
    friendly-field precedence rule observable end to end.

    Args:
        html: Rendered page markup.
        overrides: Field values the participant edited before submitting.

    Returns:
        Form data equivalent to submitting the rendered page with those edits.
    """
    collector = _FormFieldCollector()
    collector.feed(html)
    data = {name: value for name, value in collector.values.items() if name != "csrfmiddlewaretoken"}
    data.update(overrides)
    return data


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
    """Create a Read/Write draft and return its scope URL.

    Scope now follows the specification directly; connection and security
    settings are entered afterwards, once scope decides what is required.

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
    return str(catalogue_response["Location"])


def _save_security(client: Client, draft_id: str, **overrides: str) -> str:
    """Save the connection and security step and return where it continues.

    Args:
        client: Django test client that owns the builder session.
        draft_id: Builder draft id.
        overrides: Field values to override in the default valid submission.

    Returns:
        Business data location the step continues to.
    """
    response = client.post(f"/builder/{draft_id}/config/security/", data=_valid_security_form_data(**overrides))
    assert response.status_code == 302
    return str(response["Location"])


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


def _assert_status_badge(content: str, label: str, status: str, text: str) -> None:
    """Assert that a field label carries a scope-aware requirement badge.

    Args:
        content: Rendered HTML response body.
        label: Field label text expected before the badge.
        status: Badge CSS status class.
        text: Badge text.
    """
    assert f'{label} <span class="requirement-badge {status}">{text}</span>' in content, (label, text)


@pytest.mark.django_db
class TestBuilderWizardUi:
    """Browser coverage for the canonical multi-page builder flow."""

    @staticmethod
    def _selected_openapi_document_updates(content: str) -> list[tuple[str, str]]:
        select = re.search(r'<select[^>]*name="openapi_document_update".*?</select>', content, re.DOTALL)
        assert select is not None
        return re.findall(
            r'<option value="([^"]+)"[^>]*data-version="([^"]+)"[^>]*\sselected>', select.group(0), re.DOTALL
        )

    def test_new_builder_preselects_latest_openapi_document_update(self) -> None:
        """A fresh draft renders the latest published update as the selected option."""
        client = Client()
        location = client.post("/builder/new/")["Location"]

        content = client.get(location).content.decode("utf-8")

        assert self._selected_openapi_document_updates(content) == [("Update-1", "4.0.1")]

    def test_specification_selects_use_shared_dropdown_style(self) -> None:
        """Every specification dropdown uses the shared builder select styling."""
        client = Client()
        location = client.post("/builder/new/")["Location"]

        content = client.get(location).content.decode("utf-8")

        selects = re.findall(r"<select[^>]*>", content)
        assert len(selects) == 4
        assert all('class="builder-select"' in select for select in selects)
        assert "select.builder-select::picker(select)" in content
        assert "@supports (appearance: base-select)" in content
        # Cascade filtering hides options via [hidden]; the picker's option display rule must not override it.
        assert "select.builder-select option[hidden]" in content

    def test_imported_plan_keeps_its_saved_openapi_document_update_selected(self) -> None:
        """A saved non-latest update stays selected instead of the latest default."""
        client = Client()
        plan = _valid_import_plan()
        plan["specification"]["openApiDocumentUpdate"] = "Baseline"
        draft_id = _draft_id_from_builder_redirect(
            client.post("/builder/import/", data={"plan_json": json.dumps(plan)})["Location"]
        )

        content = client.get(f"/builder/{draft_id}/catalogue/").content.decode("utf-8")

        assert self._selected_openapi_document_updates(content) == [("Baseline", "4.0.1")]

    def test_new_builder_starts_with_specification_only(self) -> None:
        """POST /builder/new/ renders specification-only step one."""
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
        assert 'aria-label="Beta release notice"' in content
        assert (
            '<span class="builder-step-pill" aria-current="step"><span class="builder-step-number">1</span>Specification</span>'
            in content
        )
        assert 'name="security_profile"' not in content
        assert "FAPI 2" not in content
        assert "Open Banking UK" in content
        assert "Read/Write" in content
        assert "4.0.1" in content
        assert 'name="resource_groups"' not in content
        assert "Implemented endpoints" not in content
        assert "Plan spec JSON" not in content

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_builder_pages_show_required_and_optional_field_badges(self, mock_fetch_discovery: Mock) -> None:
        """Builder pages label user-entered fields by what the selected scope needs to run."""
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
        draft_id = _draft_id_from_builder_redirect(saved_catalogue_response["Location"])
        scope_response = client.get(saved_catalogue_response["Location"])
        assert scope_response.status_code == 200
        assert "Endpoint labels follow the specification" in scope_response.content.decode("utf-8")

        unscoped_security = client.get(f"/builder/{draft_id}/config/security/").content.decode("utf-8")
        _assert_status_badge(unscoped_security, "Client ID", "depends_on_scope", "Depends on scope")
        _assert_status_badge(unscoped_security, "Discovery URL", "depends_on_scope", "Depends on scope")
        assert "Select a scope first to see which values the selected tests need to run" in unscoped_security

        endpoint = _scope_endpoint(
            selected_resource_group_id="account-and-transaction",
            path="/open-banking/v4.0/aisp/transactions",
        )
        saved_scope_response = client.post(
            saved_catalogue_response["Location"],
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
        assert saved_scope_response["Location"] == f"/builder/{draft_id}/config/security/"
        security_content = client.get(saved_scope_response["Location"]).content.decode("utf-8")
        for label in (
            "Discovery URL",
            "Client ID",
            "Redirect URI",
            "Authorization endpoint",
            "Token endpoint",
            "Token endpoint auth method",
            "Resource server base URL",
            "Signing key ID",
            "Signing certificate",
        ):
            _assert_status_badge(security_content, label, "required", "Required to run")
        _assert_status_badge(security_content, "mTLS client certificate", "optional", "Optional")
        _assert_status_badge(security_content, "CA bundle", "optional", "Optional")
        assert "Required to run because the selected tests request OAuth 2.0 access tokens" in security_content
        assert "Type: HTTPS URL" in security_content
        assert "Required to run only when the token endpoint auth method is tls_client_auth" in security_content

        saved_security_response = client.post(saved_scope_response["Location"], data={})
        assert saved_security_response["Location"] == f"/builder/{draft_id}/config/"
        business_response = client.get(saved_security_response["Location"])
        assert business_response.status_code == 200
        business_content = business_response.content.decode("utf-8")
        _assert_requirement_badge(business_content, "Consented account identifier", "Required")
        assert '<div class="field-heading">' in business_content
        assert "Advanced AIS resource IDs JSON" in business_content

        saved_business_response = client.post(
            saved_security_response["Location"], data={"ais_consented_account_id": "account-123"}
        )
        assert saved_business_response.status_code == 302
        assert saved_business_response["Location"].endswith("/review/")
        review = client.get(saved_business_response["Location"]).content.decode("utf-8")
        assert 'data-builder-step="security" data-builder-step-state="attention"' in review
        assert "Client ID: required to run because" in review
        assert client.get(f"/builder/{draft_id}/config/runtime/").status_code == 404

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_security_badges_require_mtls_only_for_tls_client_auth(self, mock_fetch_discovery: Mock) -> None:
        """mTLS paths become required to run once the token endpoint auth method is tls_client_auth."""
        mock_fetch_discovery.return_value = {}
        client = Client()
        scope_location = _scope_location_after_security(client)
        endpoint = _scope_endpoint(
            selected_resource_group_id="account-and-transaction",
            path="/open-banking/v4.0/aisp/accounts",
        )
        security_location = client.post(
            scope_location,
            data={"resource_groups": ["account-and-transaction"], "endpoints": [endpoint.id]},
        )["Location"]

        client.post(security_location, data={"signing_token_endpoint_auth_method": "tls_client_auth"})
        content = client.get(security_location).content.decode("utf-8")

        _assert_status_badge(content, "mTLS client certificate", "required", "Required to run")
        _assert_status_badge(content, "mTLS client private key", "required", "Required to run")

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_builder_business_page_marks_ais_account_id_required_for_account_scope(
        self,
        mock_fetch_discovery: Mock,
    ) -> None:
        """AIS account-scoped endpoint selections show the account id as required."""
        mock_fetch_discovery.return_value = {}
        client = Client()
        scope_location = _scope_location_after_security(client)
        draft_id = _draft_id_from_builder_redirect(scope_location)
        endpoint = _scope_endpoint(
            selected_resource_group_id="account-and-transaction",
            path="/open-banking/v4.0/aisp/accounts/{AccountId}/balances",
        )
        client.post(
            scope_location,
            data={"resource_groups": ["account-and-transaction"], "endpoints": [endpoint.id]},
        )

        business_response = client.get(f"/builder/{draft_id}/config/")
        business_content = business_response.content.decode("utf-8")

        assert business_response.status_code == 200
        assert '<div class="field-heading">' in business_content
        _assert_requirement_badge(business_content, "Consented account identifier", "Required")
        _assert_requirement_badge(business_content, "Transaction from date", "Optional")
        _assert_requirement_badge(business_content, "Transaction to date", "Optional")

    def test_catalogue_boundary_post_continues_to_scope(self) -> None:
        """The first wizard step saves the specification and moves to scope."""
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
        assert response["Location"] == f"/builder/{draft_id}/scope/"
        content = client.get(response["Location"]).content.decode("utf-8")
        assert '<span class="builder-step-number">2</span>Scope</span>' in content
        assert re.findall(r'data-builder-step="([a-z]+)"', content) == [
            "catalogue",
            "scope",
            "security",
            "config",
            "review",
        ]
        assert "Continue to connection &amp; security" in content

    def test_dcr_boundary_continues_to_direct_endpoint_scope(self) -> None:
        """DCR continues to locked POST and optional management endpoints."""
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

        assert response.status_code == 302
        assert response["Location"] == f"/builder/{draft_id}/scope/"
        scope_response = client.get(response["Location"])
        assert scope_response.status_code == 200
        content = scope_response.content.decode("utf-8")
        assert "POST /register" in content
        assert "Required and locked" in content
        assert "POST /token" not in content

    def test_every_step_url_opens_once_a_specification_is_selected(self) -> None:
        """Only the specification gates navigation; later steps open in any order."""
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

        for open_path in ("scope/", "config/security/", "config/", "review/"):
            response = client.get(f"/builder/{draft_id}/{open_path}")
            assert response.status_code == 200, open_path

    def test_step_urls_redirect_to_specification_until_one_is_selected(self) -> None:
        """Without a specification every later step sends the user back to choose one."""
        client = Client()
        create_response = client.post("/builder/new/")
        draft_id = _draft_id_from_builder_redirect(create_response["Location"])

        for locked_path in ("scope/", "config/discovery/", "config/security/", "config/", "review/"):
            response = client.get(f"/builder/{draft_id}/{locked_path}")
            assert response.status_code == 302
            assert response["Location"] == f"/builder/{draft_id}/catalogue/"

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_security_step_saves_discovery_and_continues_to_business_data(self, mock_fetch_discovery: Mock) -> None:
        """The merged page saves the discovery URL, fills empty OAuth endpoints and moves to business data."""
        mock_fetch_discovery.return_value = {
            "issuer": "https://example.com",
            "authorization_endpoint": "https://example.com/authorize",
            "token_endpoint": "https://example.com/token",
            "jwks_uri": "https://example.com/jwks",
        }
        client = Client()
        scope_location = _scope_location_after_security(client)
        draft_id = _draft_id_from_builder_redirect(scope_location)

        security_response = client.post(
            f"/builder/{draft_id}/config/security/",
            data=_valid_security_form_data(
                discovery_url="https://example.com/.well-known/openid-configuration",
                oauth_issuer="https://typed.example.com",
            ),
        )

        assert security_response.status_code == 302
        assert security_response["Location"] == f"/builder/{draft_id}/config/"
        mock_fetch_discovery.assert_called_once()
        draft = SessionBuilderDraftStore(client.session).get(draft_id)
        assert draft is not None
        assert draft.config["discoveryUrl"] == "https://example.com/.well-known/openid-configuration"
        oauth = draft.config["oauth"]
        assert isinstance(oauth, dict)
        assert oauth["tokenEndpoint"] == "https://example.com/token"
        assert oauth["authorizationEndpoint"] == "https://example.com/authorize"
        assert oauth["issuer"] == "https://typed.example.com"

        client.post(
            f"/builder/{draft_id}/config/security/",
            data=_valid_security_form_data(discovery_url="https://example.com/.well-known/openid-configuration"),
        )
        mock_fetch_discovery.assert_called_once()

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
    def test_scope_fragment_uses_spec_labels_and_locks_mandatory_endpoints(self, mock_fetch_discovery: Mock) -> None:
        """The UI exposes specification status, not catalogue coverage labels."""
        mock_fetch_discovery.return_value = {}
        client = Client()
        scope_location = _scope_location_after_security(client)
        response = client.post(
            scope_location + "options/",
            data={"resource_groups": ["account-and-transaction"]},
        )
        assert response.status_code == 200
        content = response.content.decode("utf-8")
        mandatory = _scope_endpoint(
            selected_resource_group_id="account-and-transaction",
            path="/open-banking/v4.0/aisp/accounts",
        )
        optional = _scope_endpoint(
            selected_resource_group_id="account-and-transaction",
            path="/open-banking/v4.0/aisp/transactions",
        )
        assert f'name="endpoints" value="{mandatory.id}" data-requirement-kind="M"' in content
        checkbox = re.search(
            rf'<input type="checkbox"\s+name="locked_endpoint"\s+value="{mandatory.id}"([^>]*)>',
            content,
        )
        assert checkbox is not None
        assert "checked" in checkbox[1]
        assert 'disabled aria-disabled="true"' in checkbox[1]
        assert f'value="{optional.id}"' in content
        assert "Mandatory" in content
        assert ">Optional" in content
        assert ">Conditional" in content
        assert re.search(r'<span class="chip">Baseline', content) is None
        assert "Not classified in endpoint tables" in content
        assert "Specification endpoint table" in content
        assert "Deselect conditional and optional endpoints" in content

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_scope_fragment_defaults_new_group_to_all_endpoints_and_features(self, mock_fetch_discovery: Mock) -> None:
        """Ticking a resource group selects every endpoint and optional feature in it."""
        mock_fetch_discovery.return_value = {}
        client = Client()
        scope_location = _scope_location_after_security(client)
        optional = _scope_endpoint(
            selected_resource_group_id="account-and-transaction",
            path="/open-banking/v4.0/aisp/transactions",
        )

        response = client.post(
            scope_location + "options/",
            data={"resource_groups": ["account-and-transaction"], "expand_resource_group": ["account-and-transaction"]},
        )

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        endpoint_box = re.search(
            rf'<input type="checkbox"\s+name="endpoints"\s+value="{optional.id}"([^>]*)>',
            content,
        )
        assert endpoint_box is not None
        assert "checked" in endpoint_box[1]
        feature_value = endpoint_capability_value(
            endpoint_id=optional.id,
            capability_id="ais.transactions.date-range-filtering",
        )
        feature_box = re.search(rf'value="{re.escape(feature_value)}"([^>]*)>', content)
        assert feature_box is not None
        assert "checked" in feature_box[1]

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_scope_fragment_without_expansion_keeps_optional_endpoints_unselected(
        self, mock_fetch_discovery: Mock
    ) -> None:
        """Refreshes for other changes do not re-add endpoints the participant removed."""
        mock_fetch_discovery.return_value = {}
        client = Client()
        scope_location = _scope_location_after_security(client)
        optional = _scope_endpoint(
            selected_resource_group_id="account-and-transaction",
            path="/open-banking/v4.0/aisp/transactions",
        )

        content = client.post(
            scope_location + "options/",
            data={"resource_groups": ["account-and-transaction"], "expand_resource_group": ["not-a-group"]},
        ).content.decode("utf-8")

        endpoint_box = re.search(
            rf'<input type="checkbox"\s+name="endpoints"\s+value="{optional.id}"([^>]*)>',
            content,
        )
        assert endpoint_box is not None
        assert "checked" not in endpoint_box[1]

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_scope_post_exports_mandatory_endpoints_even_when_omitted(self, mock_fetch_discovery: Mock) -> None:
        """Server-side required selections persist through canonical JSON export."""
        mock_fetch_discovery.return_value = {}
        client = Client()
        scope_location = _scope_location_after_security(client)
        draft_id = _draft_id_from_builder_redirect(scope_location)
        scope = client.post(scope_location, data={"resource_groups": ["account-and-transaction"]})
        assert scope.status_code == 302
        config = client.post(_save_security(client, draft_id), data={"ais_consented_account_id": "account-123"})
        assert config.status_code == 302
        export = client.get(f"/builder/{draft_id}/export.json")
        assert export.status_code == 200
        plan = export.json()
        assert len(plan["resourceGroups"]) == 1
        assert plan["resourceGroups"][0]["id"] == "AIS"
        assert {(e["method"], e["path"]) for e in plan["resourceGroups"][0]["endpoints"]} == {
            ("GET", "/open-banking/v4.0/aisp/accounts"),
            ("GET", "/open-banking/v4.0/aisp/accounts/{AccountId}"),
            ("GET", "/open-banking/v4.0/aisp/accounts/{AccountId}/balances"),
            ("GET", "/open-banking/v4.0/aisp/accounts/{AccountId}/transactions"),
        }

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

        draft_id = _draft_id_from_builder_redirect(scope_location)
        assert response.status_code == 302
        assert response["Location"] == f"/builder/{draft_id}/config/security/"
        saved_response = client.get(f"/builder/{draft_id}/config/")
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
        business_location = _save_security(client, _draft_id_from_builder_redirect(scope_location))
        content_response = client.get(business_location)
        assert content_response.status_code == 200
        content = content_response.content.decode("utf-8")
        assert "Confirmation of Funds" in content
        assert "Debtor account scheme" in content
        assert "Debtor account identification" in content
        assert "Debtor account name" in content
        _assert_requirement_badge(content, "Debtor account scheme", "Required")
        _assert_requirement_badge(content, "Debtor account name", "Required")
        assert "No business data inputs required" not in content

        saved_response = client.post(
            business_location,
            data={
                "cbpii_debtor_account_scheme_name": "UK.OBIE.SortCodeAccountNumber",
                "cbpii_debtor_account_identification": "12345678901234",
                "cbpii_debtor_account_name": "Model Bank Account",
            },
        )
        draft_id = _draft_id_from_builder_redirect(saved_response["Location"])
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
        business_location = response["Location"].replace("/config/security/", "/config/")
        content_response = client.get(business_location)
        assert content_response.status_code == 200
        content = content_response.content.decode("utf-8")
        assert "Payment Initiation" in content
        assert "Domestic creditor account scheme" in content
        assert "Instructed amount" in content
        assert "No business data inputs required" not in content

        empty_response = client.post(business_location, data={})
        assert empty_response.status_code == 302
        invalid_content = client.get(business_location.replace("/config/", "/review/")).content.decode("utf-8")
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
        assert scope_response.status_code == 302
        business_location = _save_security(client, _draft_id_from_builder_redirect(scope_location))
        business_response = client.post(business_location, data={"ais_consented_account_id": "account-123"})

        assert business_response.status_code == 302
        assert business_response["Location"].endswith("/review/")
        review_response = client.get(business_response["Location"])
        assert review_response.status_code == 200
        content = review_response.content.decode("utf-8")
        assert "Review generated test plan" in content
        assert 'form="review-plan-form"' in content
        assert "Save plan JSON" not in content
        assert "Masked test plan summary" not in content
        assert "accessToken" not in content
        assert "fixture-account-id" not in content
        draft_id = _draft_id_from_builder_redirect(business_response["Location"])
        # Downloads and launch leave the boosted wizard via full navigation.
        assert '<main hx-boost="true">' in content
        assert f'action="/builder/{draft_id}/review/json/" hx-boost="false"' in content
        assert f'formaction="/builder/{draft_id}/export.json"' in content
        assert f'formaction="/builder/{draft_id}/launch/"' in content

        safe_export = client.get(f"/builder/{draft_id}/export.json")

        assert safe_export.status_code == 200
        exported = safe_export.json()
        assert exported["schemaVersion"] == "1.0"
        assert exported["specification"] == {
            "family": "OBL_READ_WRITE",
            "openApiDocumentUpdate": "Update-1",
            "profile": "FAPI1_ADVANCED",
            "version": "4.0.1",
        }
        assert exported["resourceGroups"][0]["id"] == "AIS"
        assert exported["securityEnvironment"]["resourceBaseUrl"] == "https://resource.example.com"
        assert exported["businessTestData"]["ais"]["accountIds"] == ["account-123"]
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
        scope_location = _scope_location_after_security(client)
        draft_id = _draft_id_from_builder_redirect(scope_location)
        endpoint = _scope_endpoint(
            selected_resource_group_id="account-and-transaction",
            path="/open-banking/v4.0/aisp/accounts",
        )
        client.post(scope_location, data={"resource_groups": ["account-and-transaction"], "endpoints": [endpoint.id]})
        security_location = f"/builder/{draft_id}/config/security/"
        client.post(
            security_location,
            data={"discovery_url": "https://auth.example.com/.well-known/openid-configuration", "next": "security"},
        )

        security_response = client.get(security_location)

        assert security_response.status_code == 200
        content = security_response.content.decode("utf-8")
        assert "Token endpoint auth methods supported" in content
        assert "private_key_jwt, tls_client_auth" in content
        assert "JWKS URI" in content
        assert "https://auth.example.com/jwks" in content
        assert 'value="https://auth.example.com/token"' in content

        security_save = client.post(
            security_location,
            data={
                "discovery_url": "https://auth.example.com/.well-known/openid-configuration",
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
        client.post(security_save["Location"], data={"ais_consented_account_id": "account-123"})
        mock_fetch_discovery.assert_called_once()

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
        draft_id = _draft_id_from_builder_redirect(catalogue_response["Location"])
        security_location = f"/builder/{draft_id}/config/security/"
        saved = client.post(
            security_location,
            data={"discovery_url": "https://auth.example.com/.well-known/openid-configuration"},
        )
        assert saved.status_code == 302

        security_response = client.get(security_location)

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
        draft_id = _draft_id_from_builder_redirect(catalogue_response["Location"])
        security_location = f"/builder/{draft_id}/config/security/"
        form_response = client.get(security_location)
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
                security_location,
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

        security_response = client.get(security_location)
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
                "openApiDocumentUpdate": "Update-1",
                "profile": "FAPI1_ADVANCED",
            },
            "executionMode": "development",
            "securityEnvironment": {
                **RUN_READY_SECURITY_ENVIRONMENT,
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
                "openApiDocumentUpdate": "Update-1",
                "profile": "FAPI1_ADVANCED",
            },
            "executionMode": "development",
            "securityEnvironment": {
                **RUN_READY_SECURITY_ENVIRONMENT,
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

        assert scope_response["Location"] == f"/builder/{draft_id}/config/security/"
        business_page = client.get(f"/builder/{draft_id}/config/")
        business_response = client.post(
            f"/builder/{draft_id}/config/",
            data={
                "ais_consented_account_id": "account-123",
                "ais_transaction_from_date": "2026-01-01T00:00:00Z",
                "ais_transaction_to_date": "2026-01-31T23:59:59Z",
            },
        )
        exported = client.get(f"/builder/{draft_id}/export.json").json()
        launch_response = client.post(f"/builder/{draft_id}/launch/")

        assert business_page.status_code == 200
        assert "Consented account identifier" in business_page.content.decode("utf-8")
        assert business_response.status_code == 302
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

    @patch("conformance.api.ui_views.start_run")
    def test_imported_business_value_edited_in_builder_reaches_the_launched_plan(self, mock_start_run: Mock) -> None:
        """Editing an imported CBPII debtor account in the builder changes the run.

        The advanced JSON textarea is pre-filled from the imported plan and is
        resubmitted by the browser even while collapsed. It previously overrode
        the edited friendly field, so a participant could certify against the
        value they believed they had corrected.
        """
        mock_start_run.return_value = {"id": "run-456", "status": "pending", "createdAt": "2026-06-03T12:00:00+00:00"}
        client = Client()
        plan_document = {
            "schemaVersion": "1.0",
            "specification": {
                "family": "OBL_READ_WRITE",
                "version": "4.0.1",
                "openApiDocumentUpdate": "Update-1",
                "profile": "FAPI1_ADVANCED",
            },
            "executionMode": "development",
            "securityEnvironment": {
                **RUN_READY_SECURITY_ENVIRONMENT,
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "resourceBaseUrl": "https://resource.example.com",
            },
            "resourceGroups": ["CBPII"],
            "businessTestData": {
                "cbpii": {
                    "debtorAccount": {
                        "schemeName": "UK.OBIE.SortCodeAccountNumber",
                        "identification": "10000109010102",
                        "name": "Model Bank Account",
                        "secondaryIdentification": "ROLL-1",
                    }
                }
            },
            "metadata": {"aspspName": "Example Bank"},
        }

        import_response = client.post("/builder/import/", data={"plan_json": json.dumps(plan_document)})
        draft_id = _draft_id_from_builder_redirect(import_response["Location"])
        business_url = f"/builder/{draft_id}/config/"
        business_page = client.get(business_url)
        business_response = client.post(
            business_url,
            data=_rendered_form_data(
                business_page.content.decode("utf-8"),
                cbpii_debtor_account_identification="100002",
            ),
        )
        exported = json.loads(
            client.post(f"/builder/{draft_id}/export.json", data={"include_secrets": "1"}).content.decode("utf-8")
        )
        launch_response = client.post(f"/builder/{draft_id}/launch/")

        assert business_page.status_code == 200
        assert "10000109010102" in business_page.content.decode("utf-8")
        assert business_response.status_code == 302
        assert exported["businessTestData"]["cbpii"]["debtorAccount"] == {
            "schemeName": "UK.OBIE.SortCodeAccountNumber",
            "identification": "100002",
            "name": "Model Bank Account",
            "secondaryIdentification": "ROLL-1",
        }
        assert launch_response.status_code == 302
        assert mock_start_run.call_args.kwargs["runtime_inputs"]["debtorAccountIdentification"] == "100002"

    @patch("conformance.api.ui_views.start_run")
    def test_import_loads_legacy_v2_documents_as_blocked_drafts(self, mock_start_run: Mock) -> None:
        """Legacy documents open as drafts with warnings and cannot launch."""
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
        draft_id = _draft_id_from_builder_redirect(response["Location"])
        specification = client.get(response["Location"])
        launch = client.post(f"/builder/{draft_id}/launch/")

        assert response.status_code == 302
        assert response["Location"] == f"/builder/{draft_id}/catalogue/"
        content = specification.content.decode("utf-8")
        assert "data-start-new-plan" in content
        assert "Import warnings" in content
        assert "schemaVersion &quot;v2&quot; is not supported" in content
        assert "scheme is not a recognised test-plan field." in content
        assert "specification must be a JSON object" in content
        assert specification["Cache-Control"] == "no-store"
        assert launch.status_code == 400
        mock_start_run.assert_not_called()

    @patch("conformance.api.ui_views.start_run")
    def test_import_loads_profile_not_declared_by_specification_version_as_blocked_draft(
        self,
        mock_start_run: Mock,
    ) -> None:
        """An unsupported profile is reported and blocks launch instead of failing import."""
        client = Client()
        plan_document = _valid_import_plan()
        plan_document["specification"] = {
            "family": "OBL_READ_WRITE",
            "version": "4.0.1",
            "openApiDocumentUpdate": "Update-1",
            "profile": "FAPI2",
        }

        response = client.post("/builder/import/", data={"plan_json": json.dumps(plan_document)})
        draft_id = _draft_id_from_builder_redirect(response["Location"])
        content = client.get(response["Location"]).content.decode("utf-8")
        launch = client.post(f"/builder/{draft_id}/launch/")

        assert response.status_code == 302
        assert response["Location"] == f"/builder/{draft_id}/catalogue/"
        assert "profile must be one of: FAPI1_ADVANCED" in content
        assert "resourceGroups could not be loaded because the specification is missing or invalid" in content
        assert launch.status_code == 400
        mock_start_run.assert_not_called()

    @pytest.mark.parametrize(
        ("plan_json", "message"),
        [
            ("", "Paste plan JSON or choose a .json file to import."),
            ("{not json", "Plan JSON must be valid JSON"),
            ("[]", "Plan JSON must be a JSON object."),
        ],
    )
    def test_import_rejects_input_with_nothing_to_recover(self, plan_json: str, message: str) -> None:
        """Only empty text, invalid JSON, or a non-object root are rejected."""
        response = Client().post("/builder/import/", data={"plan_json": plan_json})

        assert response.status_code == 400
        assert message in response.content.decode("utf-8")

    def test_import_page_offers_json_file_upload(self) -> None:
        """The import page accepts a .json file as multipart form data."""
        content = Client().get("/builder/import/").content.decode("utf-8")

        assert 'enctype="multipart/form-data"' in content
        assert 'name="plan_file" type="file" accept=".json,application/json"' in content

    @patch("conformance.api.ui_views.start_run")
    def test_import_accepts_uploaded_plan_file(self, mock_start_run: Mock) -> None:
        """A valid uploaded plan opens in review ready to launch."""
        mock_start_run.return_value = {"id": "run-123", "status": "pending", "createdAt": "2026-06-03T12:00:00+00:00"}
        client = Client()
        upload = SimpleUploadedFile(
            "plan.json",
            json.dumps(_valid_import_plan()).encode("utf-8"),
            content_type="application/json",
        )

        response = client.post("/builder/import/", data={"plan_json": "", "plan_file": upload})
        draft_id = _draft_id_from_builder_redirect(response["Location"])
        content = client.get(f"/builder/{draft_id}/review/").content.decode("utf-8")
        launch = client.post(f"/builder/{draft_id}/launch/")

        assert response.status_code == 302
        assert "Import warnings" not in content
        assert "Ready to launch from this reviewed plan." in content
        assert launch.status_code == 302

    def test_import_rejects_non_utf8_uploaded_file(self) -> None:
        """Uploaded files must be UTF-8 JSON."""
        upload = SimpleUploadedFile("plan.json", b"\xff\xfe\x00", content_type="application/json")

        response = Client().post("/builder/import/", data={"plan_file": upload})

        assert response.status_code == 400
        assert "Plan file must be UTF-8 encoded JSON." in response.content.decode("utf-8")

    @patch("conformance.api.ui_views.start_run")
    def test_partial_import_warns_blocks_launch_and_is_fixed_in_review_json_editor(
        self,
        mock_start_run: Mock,
    ) -> None:
        """Invalid and unknown fields are kept, block launch, and can be fixed as JSON."""
        mock_start_run.return_value = {"id": "run-123", "status": "pending", "createdAt": "2026-06-03T12:00:00+00:00"}
        client = Client()
        plan_document = _valid_import_plan()
        plan_document["securityEnvironment"]["discoveryUrl"] = "http://insecure.example.com"
        plan_document["unexpected"] = True
        plan_document["resourceGroups"].append("NOT_A_GROUP")

        response = client.post("/builder/import/", data={"plan_json": json.dumps(plan_document)})
        draft_id = _draft_id_from_builder_redirect(response["Location"])
        review = client.get(f"/builder/{draft_id}/review/")
        content = review.content.decode("utf-8")
        blocked_launch = client.post(f"/builder/{draft_id}/launch/")
        blocked_export = client.get(f"/builder/{draft_id}/export.json").json()

        assert response.status_code == 302
        assert "securityEnvironment.discoveryUrl must be an HTTPS URL." in content
        assert "unexpected is not a recognised test-plan field." in content
        assert "resourceGroups[1] could not be loaded" in content
        assert "http://insecure.example.com" in content
        assert blocked_launch.status_code == 400
        mock_start_run.assert_not_called()
        assert blocked_export["unexpected"] is True
        assert blocked_export["securityEnvironment"]["discoveryUrl"] == "http://insecure.example.com"
        assert [group["id"] if isinstance(group, dict) else group for group in blocked_export["resourceGroups"]] == [
            "AIS",
            "NOT_A_GROUP",
        ]

        launch = client.post(f"/builder/{draft_id}/launch/", data={"plan_json": json.dumps(_valid_import_plan())})
        fixed_content = client.get(f"/builder/{draft_id}/review/").content.decode("utf-8")

        assert launch.status_code == 302
        mock_start_run.assert_called_once()
        assert "Import warnings" not in fixed_content
        assert "Ready to launch from this reviewed plan." in fixed_content

    @patch("conformance.api.ui_views.start_run")
    def test_launch_runs_the_plan_json_box_without_a_separate_save(self, mock_start_run: Mock) -> None:
        """An unsaved invalid edit in the review box blocks launch, as the box is the plan that runs."""
        client = Client()
        response = client.post("/builder/import/", data={"plan_json": json.dumps(_valid_import_plan())})
        draft_id = _draft_id_from_builder_redirect(response["Location"])
        edited = _valid_import_plan()
        edited["executionMode"] = "not-a-mode"

        launch = client.post(f"/builder/{draft_id}/launch/", data={"plan_json": json.dumps(edited)})

        assert launch.status_code == 400
        mock_start_run.assert_not_called()
        content = launch.content.decode("utf-8")
        assert "Resolve review blockers before launch." in content
        assert "not-a-mode" in content

    def test_export_uses_the_plan_json_box(self) -> None:
        """Export from the review form applies the box's JSON before exporting it."""
        client = Client()
        response = client.post("/builder/import/", data={"plan_json": json.dumps(_valid_import_plan())})
        draft_id = _draft_id_from_builder_redirect(response["Location"])
        edited = _valid_import_plan()
        edited["unexpected"] = "from-the-box"

        exported = client.post(f"/builder/{draft_id}/export.json", data={"plan_json": json.dumps(edited)})

        assert exported.status_code == 200
        assert exported.json()["unexpected"] == "from-the-box"

    def test_edit_step_buttons_apply_the_plan_json_box_first(self) -> None:
        """Builder-step navigation keeps box edits and only redirects to allowed internal steps."""
        client = Client()
        response = client.post("/builder/import/", data={"plan_json": json.dumps(_valid_import_plan())})
        draft_id = _draft_id_from_builder_redirect(response["Location"])
        edited = _valid_import_plan()
        edited["unexpected"] = True

        to_scope = client.post(
            f"/builder/{draft_id}/review/json/",
            data={"plan_json": json.dumps(edited), "next": "scope"},
        )
        hostile = client.post(
            f"/builder/{draft_id}/review/json/",
            data={"plan_json": json.dumps(edited), "next": "https://evil.example"},
        )
        review = client.get(f"/builder/{draft_id}/review/").content.decode("utf-8")

        assert to_scope.status_code == 302
        assert to_scope["Location"] == f"/builder/{draft_id}/scope/"
        assert hostile["Location"] == f"/builder/{draft_id}/review/"
        assert "unexpected is not a recognised test-plan field." in review

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    @patch("conformance.api.ui_views.start_run")
    def test_partial_import_is_fixed_by_saving_the_builder_step(
        self,
        mock_start_run: Mock,
        mock_fetch_discovery: Mock,
    ) -> None:
        """Saving the owning builder step clears the imported invalid value and its warning."""
        mock_start_run.return_value = {"id": "run-123", "status": "pending", "createdAt": "2026-06-03T12:00:00+00:00"}
        mock_fetch_discovery.return_value = {}
        client = Client()
        plan_document = _valid_import_plan()
        plan_document["securityEnvironment"]["discoveryUrl"] = "http://insecure.example.com"
        response = client.post("/builder/import/", data={"plan_json": json.dumps(plan_document)})
        draft_id = _draft_id_from_builder_redirect(response["Location"])

        security_page = client.get(f"/builder/{draft_id}/config/security/").content.decode("utf-8")
        discovery = client.post(
            f"/builder/{draft_id}/config/security/",
            data=_rendered_form_data(
                security_page, discovery_url="https://example.com/.well-known/openid-configuration"
            ),
        )
        content = client.get(f"/builder/{draft_id}/review/").content.decode("utf-8")
        launch = client.post(f"/builder/{draft_id}/launch/")

        assert discovery.status_code == 302
        assert "http://insecure.example.com" not in content
        assert "must be an HTTPS URL" not in content
        assert launch.status_code == 302

    def test_builder_save_keeps_unrepresented_imported_fields(self) -> None:
        """Unknown imported keys survive unrelated builder saves and keep launch blocked."""
        client = Client()
        plan_document = _valid_import_plan()
        plan_document["unexpected"] = {"kept": True}
        response = client.post("/builder/import/", data={"plan_json": json.dumps(plan_document)})
        draft_id = _draft_id_from_builder_redirect(response["Location"])

        endpoint = _scope_endpoint(
            selected_resource_group_id="account-and-transaction",
            path="/open-banking/v4.0/aisp/accounts",
        )
        scope = client.post(
            f"/builder/{draft_id}/scope/",
            data={"resource_groups": ["account-and-transaction"], "endpoints": [endpoint.id]},
        )
        config = client.post(
            f"/builder/{draft_id}/config/",
            data={"ais_consented_account_id": "account-123"},
        )
        assert config.status_code == 302
        review = client.get(f"/builder/{draft_id}/review/")
        exported = client.get(f"/builder/{draft_id}/export.json").json()

        assert scope.status_code == 302
        content = review.content.decode("utf-8")
        assert "unexpected is not a recognised test-plan field." in content
        assert "Resolve review blockers before launch." in content
        assert exported["unexpected"] == {"kept": True}

    def test_review_json_editor_rejects_non_object_text(self) -> None:
        """Invalid box JSON is shown back with an error, nothing launches, and the draft is unchanged."""
        client = Client()
        response = client.post("/builder/import/", data={"plan_json": json.dumps(_valid_import_plan())})
        draft_id = _draft_id_from_builder_redirect(response["Location"])

        rejected = client.post(f"/builder/{draft_id}/launch/", data={"plan_json": "{broken"})
        review = client.get(f"/builder/{draft_id}/review/")

        assert rejected.status_code == 400
        assert rejected["Cache-Control"] == "no-store"
        assert "Plan JSON must be valid JSON" in rejected.content.decode("utf-8")
        assert "{broken" in rejected.content.decode("utf-8")
        assert "Ready to launch from this reviewed plan." in review.content.decode("utf-8")

    def test_review_json_editor_returns_404_for_unknown_draft(self) -> None:
        """The review JSON editor is scoped to drafts in this browser session."""
        response = Client().post("/builder/missing/review/json/", data={"plan_json": "{}"})

        assert response.status_code == 404

    @patch("conformance.api.ui_views.start_run")
    def test_safe_export_redacts_imported_secret_fields(self, mock_start_run: Mock) -> None:
        """Unrepresented imported secrets are blanked in safe export and kept in secret export."""
        client = Client()
        plan_document = _valid_import_plan()
        plan_document["unexpected"] = {"accessToken": "imported-secret-token"}
        response = client.post("/builder/import/", data={"plan_json": json.dumps(plan_document)})
        draft_id = _draft_id_from_builder_redirect(response["Location"])

        safe = client.get(f"/builder/{draft_id}/export.json").json()
        with_secrets = client.post(f"/builder/{draft_id}/export.json", data={"include_secrets": "1"}).json()

        assert safe["unexpected"]["accessToken"] != "imported-secret-token"
        assert with_secrets["unexpected"]["accessToken"] == "imported-secret-token"
        mock_start_run.assert_not_called()

    def test_removed_single_page_builder_routes_return_404(self) -> None:
        """The legacy /plan/ builder routes are no longer mounted."""
        client = Client()

        assert client.get("/plan/").status_code == 404
        assert client.post("/plan/preview/", data={}).status_code == 404
        assert client.post("/plan/launch/", data={}).status_code == 404
