"""Component tests for the participant-facing builder shell."""

from __future__ import annotations

import re
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
        assert "Test scope and capabilities" in content
        assert "Payment Initiation" in content
        assert "Variable Recurring Payments" in content
        assert 'name="endpoints"' not in content

    def test_saved_scope_and_capabilities_remain_checked_when_reopened(self) -> None:
        """A scope saved through the UI remains selected on a later GET."""
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
        scope_url = f"/builder/{draft_id}/scope/"

        saved = client.post(
            scope_url,
            data={
                "test_scope": "pis",
                "capabilities": ["pis.v401.capability.domestic-standing-order"],
            },
        )
        reopened = client.get(scope_url)
        content = reopened.content.decode()

        assert saved.status_code == 302
        assert reopened.status_code == 200
        assert re.search(r'name="test_scope"\s+value="pis"\s+checked', content)
        assert re.search(
            r'name="capabilities"\s+value="pis\.v401\.capability\.domestic-standing-order"\s+checked',
            content,
        )
        assert not re.search(r'name="test_scope"\s+value="cbpii"\s+checked', content)

    def test_saved_business_values_remain_editable_when_reopened(self) -> None:
        """Friendly Business fields remain authoritative across repeated saves."""
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
        client.post(
            f"/builder/{draft_id}/scope/",
            data={
                "test_scope": "cbpii",
                "capabilities": ["cbpii.v401.capability.confirmation-of-funds"],
            },
        )
        business_url = f"/builder/{draft_id}/config/"
        first_values = {
            "cbpii_debtor_account_scheme_name": "first-scheme",
            "cbpii_debtor_account_identification": "first-identification",
            "cbpii_debtor_account_name": "First debtor",
        }

        first_save = client.post(business_url, data=first_values)
        reopened = client.get(business_url)

        assert first_save.status_code == 302
        assert reopened.status_code == 200
        reopened_content = reopened.content.decode()
        for value in first_values.values():
            assert f'value="{value}"' in reopened_content
        assert re.search(
            r'name="cbpii_debtor_account_json"[^>]*>\s*</textarea>',
            reopened_content,
        )

        second_values = {
            "cbpii_debtor_account_scheme_name": "second-scheme",
            "cbpii_debtor_account_identification": "second-identification",
            "cbpii_debtor_account_name": "Second debtor",
        }
        second_save = client.post(business_url, data=second_values)
        reopened_again = client.get(business_url)

        assert second_save.status_code == 302
        assert reopened_again.status_code == 200
        final_content = reopened_again.content.decode()
        for value in second_values.values():
            assert f'value="{value}"' in final_content
        for value in first_values.values():
            assert f'value="{value}"' not in final_content

    def test_removed_single_page_builder_routes_return_404(self) -> None:
        client = Client()

        assert client.get("/builder/").status_code == 404
        assert client.get("/builder/specifications/").status_code == 404
