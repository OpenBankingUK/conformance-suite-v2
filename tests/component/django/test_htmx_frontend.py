"""Component tests for the HTMX-driven partial page updates.

Covers static serving of the vendored HTMX bundle, live run-page polling that
stops once a run is terminal, the HTMX scope refresh, boosted builder
navigation, and the discovery metadata preview fragment.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from unittest.mock import Mock, patch

import pytest
from django.test import Client

from conformance.api.builder_wizard import catalogue_scope_hierarchy
from conformance.api.run_store import RunRecord, run_store
from conformance.catalogue import PlanDocumentBoundary

pytestmark = pytest.mark.component

HTMX_PATH = "/static/conformance/vendor/htmx-2.0.11.min.js"
HTMX_SHA256 = "d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717"  # pragma: allowlist secret - vendored file checksum
HEAD_SUPPORT_PATH = "/static/conformance/vendor/htmx-ext-head-support-2.0.5.min.js"
HEAD_SUPPORT_SHA256 = "86e59ae1048bf02fb31a5842b4b559a1058b4c3ff190c258530ecb94f9fa6d56"  # pragma: allowlist secret - vendored file checksum
DISCOVERY_URL = "https://example.com/.well-known/openid-configuration"
PANEL_IDS = ("run-status", "run-steps", "run-log", "run-result")


@pytest.fixture(autouse=True)
def _reset_global_stores() -> Iterator[None]:
    """Reset the process-local run store around each test.

    Yields:
        Control back to pytest while the test executes.
    """
    run_store.reset()
    yield
    run_store.reset()


def _panel_open_tag(content: str, panel_id: str) -> str:
    """Return the opening ``<section>`` tag for a run page panel.

    Args:
        content: Rendered HTML.
        panel_id: ``id`` attribute of the panel section.

    Returns:
        The panel's opening tag text.
    """
    match = re.search(rf'<section class="panel" id="{panel_id}"[^>]*>', content)
    assert match is not None, panel_id
    return match.group(0)


def _completed_run() -> RunRecord:
    """Create a run that has reached the terminal ``completed`` status.

    Returns:
        The created run record.
    """
    record = run_store.create_run()
    run_store.mark_running(record.run_id)
    run_store.mark_completed(record.run_id, result={"status": "passed"})
    return record


def _discovery_location(client: Client) -> str:
    """Create a Read/Write draft and return the page that holds its discovery URL.

    Args:
        client: Django test client that owns the builder session.

    Returns:
        Connection and security step location for the new draft.
    """
    create_response = client.post("/builder/new/")
    client.post(
        create_response["Location"],
        data={"scheme": "open-banking-uk", "specification": "read-write", "version": "4.0.1"},
    )
    draft_id = str(create_response["Location"]).split("/")[2]
    return f"/builder/{draft_id}/config/security/"


class TestStaticAssets:
    """The vendored HTMX bundle is served by WhiteNoise and loaded on pages."""

    @pytest.mark.parametrize(
        ("path", "sha256"),
        [(HTMX_PATH, HTMX_SHA256), (HEAD_SUPPORT_PATH, HEAD_SUPPORT_SHA256)],
    )
    def test_vendored_asset_is_served_with_recorded_digest(self, path: str, sha256: str) -> None:
        """Static assets are served from the app origin and match the vendored digest."""
        response = Client().get(path)

        assert response.status_code == 200
        body = b"".join(response.streaming_content)  # type: ignore[attr-defined]  # WhiteNoise returns a streaming file response
        assert hashlib.sha256(body).hexdigest() == sha256

    def test_pages_load_htmx_and_configure_safe_history(self) -> None:
        """Pages load HTMX with history snapshots disabled and error bodies swapped."""
        record = run_store.create_run()
        client = Client()
        for url in ("/", f"/runs/{record.run_id}/", _discovery_location(client)):
            content = client.get(url).content.decode("utf-8")
            assert f'<script src="{HTMX_PATH}" defer></script>' in content, url
            assert f'<script src="{HEAD_SUPPORT_PATH}" defer></script>' in content, url
            assert '"historyCacheSize": 0' in content, url
            assert '{"code": "[45]..", "swap": true, "error": true}' in content, url
            assert 'event.detail.headers["X-CSRFToken"]' in content, url


class TestRunPageLiveUpdates:
    """Run panels poll while active and stop polling once the run is terminal."""

    def test_active_run_polls_panels_without_full_page_refresh(self) -> None:
        """An active run page polls each panel and only refreshes the page without JavaScript."""
        record = run_store.create_run()

        content = Client().get(f"/runs/{record.run_id}/").content.decode("utf-8")

        assert '<noscript><meta http-equiv="refresh" content="2"></noscript>' in content
        assert content.count('http-equiv="refresh"') == 1
        assert 'hx-trigger="every 2s"' in _panel_open_tag(content, "run-status")
        for panel_id in PANEL_IDS[1:]:
            assert 'hx-trigger="every 2s, run-finished from:body"' in _panel_open_tag(content, panel_id)

    def test_terminal_run_renders_static_panels(self) -> None:
        """A terminal run page neither refreshes nor polls (a ``load`` trigger would loop forever)."""
        record = _completed_run()

        content = Client().get(f"/runs/{record.run_id}/").content.decode("utf-8")

        assert 'http-equiv="refresh"' not in content
        for panel_id in PANEL_IDS:
            tag = _panel_open_tag(content, panel_id)
            assert "hx-trigger" not in tag
            assert "hx-get" not in tag

    @pytest.mark.parametrize(
        ("partial", "panel_id"),
        [("status", "run-status"), ("steps", "run-steps"), ("log", "run-log"), ("result", "run-result")],
    )
    def test_terminal_partials_stop_polling(self, partial: str, panel_id: str) -> None:
        """The final poll response replaces each panel with a non-polling version."""
        record = _completed_run()

        response = Client().get(f"/runs/{record.run_id}/{partial}/", HTTP_HX_REQUEST="true")

        assert response.status_code == 200
        assert "hx-trigger" not in _panel_open_tag(response.content.decode("utf-8"), panel_id)

    def test_status_partial_signals_run_finished_to_other_panels(self) -> None:
        """The terminal HTMX status poll emits ``run-finished`` so the other panels refresh once."""
        record = _completed_run()

        response = Client().get(f"/runs/{record.run_id}/status/", HTTP_HX_REQUEST="true")

        assert response["HX-Trigger"] == "run-finished"

    def test_status_partial_does_not_signal_while_running_or_outside_htmx(self) -> None:
        """``run-finished`` is only sent for terminal runs requested by HTMX."""
        completed = _completed_run()
        running = run_store.create_run()
        run_store.mark_running(running.run_id)
        client = Client()

        running_response = client.get(f"/runs/{running.run_id}/status/", HTTP_HX_REQUEST="true")
        plain_response = client.get(f"/runs/{completed.run_id}/status/")

        assert "HX-Trigger" not in running_response
        assert 'hx-trigger="every 2s"' in running_response.content.decode("utf-8")
        assert "HX-Trigger" not in plain_response


class TestBuilderPages:
    """Builder steps use HTMX for scope refresh, boosted navigation and discovery checks."""

    @patch("conformance.api.ui_views._fetch_discovery_metadata", return_value={})
    def test_scope_page_refreshes_options_with_htmx_only(self, _mock_fetch: Mock) -> None:
        """The scope tree refreshes through HTMX; the duplicate fetch implementation is gone."""
        client = Client()
        discovery_location = _discovery_location(client)
        draft_id = discovery_location.split("/")[2]
        security_location = client.post(discovery_location, data={"discovery_url": ""})["Location"]
        client.post(
            security_location,
            data={
                "oauth_client_id": "client-123",
                "oauth_redirect_uri": "https://client.example.com/callback",
                "resource_server_base_url": "https://resource.example.com",
            },
        )

        content = client.get(f"/builder/{draft_id}/scope/").content.decode("utf-8")

        assert '<main hx-boost="true">' in content
        assert f'hx-post="/builder/{draft_id}/scope/options/"' in content
        assert 'hx-trigger="change, scope-refresh"' in content
        assert 'hx-sync="this:replace"' in content
        assert 'htmx.trigger(refresher, "scope-refresh", { expandAll: selectEndpoints })' in content
        assert "expand_resource_group" in content
        assert "AbortController" not in content
        assert "fetch(" not in content

    def test_security_page_is_boosted_with_discovery_check_button(self) -> None:
        """The connection and security step is boosted and offers an inline discovery check."""
        client = Client()
        location = _discovery_location(client)
        draft_id = location.split("/")[2]

        content = client.get(location).content.decode("utf-8")

        assert '<main hx-boost="true">' in content
        assert f'hx-post="/builder/{draft_id}/config/discovery/preview/"' in content
        assert '<section id="discovery-preview" aria-live="polite">' in content
        assert ">Fetch and fill<" in content
        assert ">Fetching…<" in content
        assert "Empty OAuth fields below are filled from it; values you've already typed are kept." in content
        assert '<span id="discovery-tag-oauth_issuer" class="discovery-tag" hidden>From discovery</span>' in content
        assert ">Check<" not in content

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_discovery_preview_renders_metadata_without_saving(self, mock_fetch: Mock) -> None:
        """A valid URL is fetched via the existing helper and shown, but not stored on the draft."""
        mock_fetch.return_value = {
            "issuer": "https://issuer.example.com",
            "token_endpoint": "https://issuer.example.com/token",
            "sourceUrl": DISCOVERY_URL,
            "statusCode": 200,
        }
        client = Client()
        location = _discovery_location(client)
        draft_id = location.split("/")[2]

        response = client.post(
            f"/builder/{draft_id}/config/discovery/preview/",
            data={"discovery_url": DISCOVERY_URL},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "https://issuer.example.com/token" in content
        assert "Token endpoint" in content
        mock_fetch.assert_called_once_with({"discoveryUrl": DISCOVERY_URL})
        page = client.get(location).content.decode("utf-8")
        assert DISCOVERY_URL not in page

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_fetch_and_fill_fills_only_empty_oauth_fields(self, mock_fetch: Mock) -> None:
        """Fetch and fill fills empty OAuth inputs out of band, keeps typed values, and summarises both."""
        mock_fetch.return_value = {
            "issuer": "https://issuer.example.com",
            "token_endpoint": "https://issuer.example.com/token",
            "authorization_endpoint": "https://issuer.example.com/authorize",
            "sourceUrl": DISCOVERY_URL,
        }
        client = Client()
        draft_id = _discovery_location(client).split("/")[2]

        response = client.post(
            f"/builder/{draft_id}/config/discovery/preview/",
            data={
                "discovery_url": DISCOVERY_URL,
                "oauth_issuer": "https://typed.example.com",
                "oauth_token_endpoint": "",
            },
            HTTP_HX_REQUEST="true",
        )

        content = response.content.decode("utf-8")
        assert (
            '<input id="id_oauth_token_endpoint" name="oauth_token_endpoint" type="url" '
            'value="https://issuer.example.com/token"'
        ) in content
        assert 'id="id_oauth_authorization_endpoint"' in content
        assert 'id="id_oauth_issuer"' not in content
        assert content.count("<input ") == 2
        assert "Filled 2 empty fields from discovery: Authorization endpoint, Token endpoint." in content
        assert "Kept 1 value you entered." in content
        assert "Values are saved when you save or leave this page." in content
        assert "<summary>Show discovery document values</summary>" in content
        assert (
            '<span id="discovery-tag-oauth_token_endpoint" class="discovery-tag" hx-swap-oob="true">'
            "From discovery</span>"
        ) in content
        assert (
            '<span id="discovery-tag-oauth_issuer" class="discovery-tag" hidden hx-swap-oob="true">'
            "From discovery</span>"
        ) in content
        assert "These values you entered differ from discovery:" in content
        assert "https://typed.example.com" in content
        assert ">Replace with discovery values</button>" in content
        assert 'hx-vals=\'{"overwrite": "true"}\'' in content

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_fetch_and_fill_replaces_differing_values_only_when_asked(self, mock_fetch: Mock) -> None:
        """The overwrite action replaces values that differ from discovery and leaves matching ones alone."""
        mock_fetch.return_value = {
            "issuer": "https://issuer.example.com",
            "token_endpoint": "https://issuer.example.com/token",
            "sourceUrl": DISCOVERY_URL,
        }
        client = Client()
        draft_id = _discovery_location(client).split("/")[2]

        response = client.post(
            f"/builder/{draft_id}/config/discovery/preview/",
            data={
                "discovery_url": DISCOVERY_URL,
                "oauth_issuer": "https://typed.example.com",
                "oauth_token_endpoint": "https://issuer.example.com/token",
                "overwrite": "true",
            },
            HTTP_HX_REQUEST="true",
        )

        content = response.content.decode("utf-8")
        assert (
            '<input id="id_oauth_issuer" name="oauth_issuer" type="url" value="https://issuer.example.com"' in content
        )
        assert 'id="id_oauth_token_endpoint"' not in content
        assert "Replaced 1 value with discovery values: Issuer." in content
        assert "Kept 1 value you entered." in content
        assert "Replace with discovery values" not in content
        assert (
            '<span id="discovery-tag-oauth_issuer" class="discovery-tag" hx-swap-oob="true">From discovery</span>'
        ) in content

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_fetch_and_fill_reports_nothing_to_fill(self, mock_fetch: Mock) -> None:
        """When every discoverable field already matches, the summary says nothing needed filling."""
        mock_fetch.return_value = {"issuer": "https://issuer.example.com", "sourceUrl": DISCOVERY_URL}
        client = Client()
        draft_id = _discovery_location(client).split("/")[2]

        response = client.post(
            f"/builder/{draft_id}/config/discovery/preview/",
            data={"discovery_url": DISCOVERY_URL, "oauth_issuer": "https://issuer.example.com"},
            HTTP_HX_REQUEST="true",
        )

        content = response.content.decode("utf-8")
        assert "Nothing to fill — every discoverable field already has a value." in content
        assert "<input " not in content

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_dcr_discovery_preview_does_not_fill_fields(self, mock_fetch: Mock) -> None:
        """DCR offers a preview-only discovery action that never fills or tags fields."""
        mock_fetch.return_value = {"issuer": "https://issuer.example.com", "sourceUrl": DISCOVERY_URL}
        client = Client()
        created = client.post("/builder/new/")
        selected = client.post(
            created["Location"],
            data={"scheme": "open-banking-uk", "specification": "dynamic-client-registration", "version": "3.4"},
        )
        draft_id = str(created["Location"]).split("/")[2]
        boundary = PlanDocumentBoundary("open-banking-uk", "dynamic-client-registration", "3.4")
        get_endpoint = next(
            endpoint for endpoint in catalogue_scope_hierarchy(boundary).direct_endpoints if endpoint.method == "GET"
        )
        discovery_location = client.post(selected["Location"], data={"endpoints": [get_endpoint.id]})["Location"]

        page = client.get(discovery_location).content.decode("utf-8")
        response = client.post(
            f"/builder/{draft_id}/config/discovery/preview/",
            data={"discovery_url": DISCOVERY_URL},
            HTTP_HX_REQUEST="true",
        )

        assert ">Preview discovery</button>" in page
        assert "Check discovery URL" not in page
        content = response.content.decode("utf-8")
        assert "Preview only — nothing on this page was changed." in content
        assert '<details class="discovery-document" open>' in content
        assert "hx-swap-oob" not in content

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_discovery_preview_reports_fetch_error(self, mock_fetch: Mock) -> None:
        """Fetch failures are shown inline so the participant can correct the URL."""
        mock_fetch.return_value = {"fetchError": "Connection refused", "sourceUrl": DISCOVERY_URL}
        client = Client()
        draft_id = _discovery_location(client).split("/")[2]

        response = client.post(f"/builder/{draft_id}/config/discovery/preview/", data={"discovery_url": DISCOVERY_URL})

        assert response.status_code == 200
        assert "Couldn't fetch discovery from" in response.content.decode("utf-8")
        assert "Nothing was changed — enter the values manually." in response.content.decode("utf-8")
        assert "Connection refused" in response.content.decode("utf-8")

    @pytest.mark.parametrize("discovery_url", ["", "http://example.com/.well-known/openid-configuration"])
    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_discovery_preview_rejects_invalid_url_without_fetching(self, mock_fetch: Mock, discovery_url: str) -> None:
        """Blank or non-HTTPS URLs fail validation with ``400`` and are never fetched."""
        client = Client()
        draft_id = _discovery_location(client).split("/")[2]

        response = client.post(f"/builder/{draft_id}/config/discovery/preview/", data={"discovery_url": discovery_url})

        assert response.status_code == 400
        assert 'class="errorlist"' in response.content.decode("utf-8")
        mock_fetch.assert_not_called()

    def test_discovery_preview_returns_404_for_unknown_draft(self) -> None:
        """Drafts from another browser session cannot be previewed."""
        response = Client().post("/builder/missing/config/discovery/preview/", data={"discovery_url": DISCOVERY_URL})

        assert response.status_code == 404

    def test_discovery_preview_returns_404_before_boundary_selected(self) -> None:
        """A draft without a catalogue boundary cannot be previewed."""
        client = Client()
        draft_id = client.post("/builder/new/")["Location"].split("/")[2]

        response = client.post(f"/builder/{draft_id}/config/discovery/preview/", data={"discovery_url": DISCOVERY_URL})

        assert response.status_code == 404

    def test_discovery_preview_requires_post(self) -> None:
        """The preview endpoint only accepts POST."""
        client = Client()
        draft_id = _discovery_location(client).split("/")[2]

        assert client.get(f"/builder/{draft_id}/config/discovery/preview/").status_code == 405
