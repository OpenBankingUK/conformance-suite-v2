"""Component tests for the participant-facing builder shell."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import Mock, patch

import pytest
from django.test import Client

from conformance.api.run_store import run_store

pytestmark = pytest.mark.component


@pytest.fixture(autouse=True)
def _reset_global_stores() -> Iterator[None]:
    run_store.reset()
    yield
    run_store.reset()


def _draft_id_from_builder_redirect(location: str) -> str:
    return location.rstrip("/").rsplit("/", maxsplit=2)[-2]


@pytest.mark.django_db
class TestBuilderWizardUi:
    """Browser coverage that is independent of one catalogue family."""

    def test_new_builder_starts_with_specification_only(self) -> None:
        client = Client()

        response = client.post("/builder/new/")

        assert response.status_code == 302
        assert response["Location"].endswith("/catalogue/")
        step_response = client.get(response["Location"])
        content = step_response.content.decode()
        assert step_response.status_code == 200
        assert "Choose specification" in content
        assert "Open Banking UK" in content
        assert "Read/Write" in content
        assert "4.0.1" in content
        assert 'name="capabilities"' not in content

    def test_catalogue_boundary_continues_to_discovery(self) -> None:
        client = Client()
        created = client.post("/builder/new/")
        draft_id = _draft_id_from_builder_redirect(created["Location"])

        response = client.post(
            created["Location"],
            data={
                "scheme": "open-banking-uk",
                "specification": "read-write",
                "version": "4.0.1",
            },
        )

        assert response.status_code == 302
        assert response["Location"] == f"/builder/{draft_id}/config/discovery/"

    @patch("conformance.api.ui_views._fetch_discovery_metadata")
    def test_discovery_fetch_failure_allows_manual_security_config(
        self,
        mock_fetch_discovery: Mock,
    ) -> None:
        mock_fetch_discovery.return_value = {"fetchError": "Discovery returned HTTP 503"}
        client = Client()
        created = client.post("/builder/new/")
        selected = client.post(
            created["Location"],
            data={
                "scheme": "open-banking-uk",
                "specification": "read-write",
                "version": "4.0.1",
            },
        )

        response = client.post(
            selected["Location"],
            data={"discovery_url": "https://as.example.com/.well-known/openid-configuration"},
        )

        assert response.status_code == 302
        security = client.get(response["Location"])
        assert "Discovery returned HTTP 503" in security.content.decode()

    def test_scope_page_uses_trusted_catalogue_capabilities(self) -> None:
        client = Client()
        created = client.post("/builder/new/")
        draft_id = _draft_id_from_builder_redirect(created["Location"])
        client.post(
            created["Location"],
            data={
                "scheme": "open-banking-uk",
                "specification": "read-write",
                "version": "4.0.1",
            },
        )

        response = client.get(f"/builder/{draft_id}/scope/")

        content = response.content.decode()
        assert response.status_code == 200
        assert "Requirements scope and capabilities" in content
        assert "Payment Initiation" in content
        assert "Variable Recurring Payments" in content
        assert 'name="endpoints"' not in content

    def test_removed_single_page_builder_routes_return_404(self) -> None:
        client = Client()

        assert client.get("/builder/").status_code == 404
        assert client.get("/builder/specifications/").status_code == 404
