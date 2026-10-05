"""Component tests for builder step-bar navigation, Back validation, and locking."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import Mock, patch

import pytest
from django.test import Client

from conformance.api.builder_draft_store import BUILDER_STEP_IDS, BuilderDraft, SessionBuilderDraftStore
from conformance.catalogue import CatalogueError

pytestmark = pytest.mark.component

_DISCOVERY_URL = "https://example.com/.well-known/openid-configuration"


def _import_plan() -> dict[str, Any]:
    return {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "4.0.1",
            "openApiDocumentUpdate": "Update-1",
            "profile": "FAPI1_ADVANCED",
        },
        "executionMode": "development",
        "securityEnvironment": {"discoveryUrl": _DISCOVERY_URL, "resourceBaseUrl": "https://resource.example.com"},
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


def _imported_draft_id(client: Client) -> str:
    response = client.post("/builder/import/", data={"plan_json": json.dumps(_import_plan())})
    assert response.status_code == 302
    return str(response["Location"]).split("/")[2]


def _new_draft_id(client: Client) -> str:
    return str(client.post("/builder/new/")["Location"]).split("/")[2]


def _draft(client: Client, draft_id: str) -> BuilderDraft:
    draft = SessionBuilderDraftStore(client.session).get(draft_id)
    assert draft is not None
    return draft


def _read_write_boundary(**extra: str) -> dict[str, str]:
    return {"scheme": "open-banking-uk", "specification": "read-write", "version": "4.0.1", **extra}


def _draft_at_scope(client: Client) -> str:
    """Return a fresh Read/Write draft whose steps before scope are complete."""
    draft_id = _new_draft_id(client)
    client.post(f"/builder/{draft_id}/catalogue/", data=_read_write_boundary())
    session = client.session
    store = SessionBuilderDraftStore(session)
    draft = store.get(draft_id)
    assert draft is not None
    store.save(draft.with_completed_steps(("catalogue", "discovery", "security")))
    session.save()
    return draft_id


class TestStepBar:
    """The step bar renders on every builder page and submits the page form."""

    def test_imported_plan_review_offers_every_step(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)

        content = client.get(f"/builder/{draft_id}/review/").content.decode("utf-8")

        assert _draft(client, draft_id).completed_steps == BUILDER_STEP_IDS
        assert 'aria-label="Builder steps"' in content
        assert '<span class="builder-step-number">6</span>Review</span>' in content
        for step_id in ("catalogue", "discovery", "security", "scope", "config"):
            assert f'name="next" value="{step_id}" form="review-plan-form" formnovalidate' in content
        assert "Edit specification" not in content

    def test_new_draft_locks_steps_until_predecessors_are_saved(self) -> None:
        client = Client()
        draft_id = _new_draft_id(client)

        content = client.get(f"/builder/{draft_id}/catalogue/").content.decode("utf-8")

        assert 'id="builder-step-form"' in content
        assert content.count('data-builder-step-state="locked"') == 5
        assert 'name="next" value="discovery"' not in content


class TestStepJumps:
    """Step-bar jumps save the page first and only leave a valid page."""

    def test_editing_imported_specification_jumps_straight_back_to_review(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)

        to_catalogue = client.post(f"/builder/{draft_id}/review/json/", data={"next": "catalogue"})
        assert to_catalogue["Location"] == f"/builder/{draft_id}/catalogue/"
        catalogue = client.get(to_catalogue["Location"]).content.decode("utf-8")
        assert 'name="next" value="review" form="builder-step-form" formnovalidate' in catalogue

        response = client.post(
            f"/builder/{draft_id}/catalogue/",
            data=_read_write_boundary(openapi_document_update="Update-1", next="review"),
        )

        assert response.status_code == 302
        assert response["Location"] == f"/builder/{draft_id}/review/"
        assert _draft(client, draft_id).completed_steps == BUILDER_STEP_IDS

    @patch("conformance.api.ui_views._fetch_discovery_metadata", return_value={})
    def test_valid_jump_saves_the_current_page(self, _mock_fetch: Mock) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)
        new_url = "https://changed.example.com/.well-known/openid-configuration"

        response = client.post(
            f"/builder/{draft_id}/config/discovery/", data={"discovery_url": new_url, "next": "scope"}
        )

        assert response.status_code == 302
        assert response["Location"] == f"/builder/{draft_id}/scope/"
        assert _draft(client, draft_id).config["discoveryUrl"] == new_url

    def test_invalid_jump_stays_with_errors_and_saves_nothing(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)
        before = _draft(client, draft_id)

        response = client.post(
            f"/builder/{draft_id}/config/discovery/",
            data={"discovery_url": "http://insecure.example.com/", "next": "review"},
        )

        assert response.status_code == 400
        content = response.content.decode("utf-8")
        assert "errorlist" in content
        assert 'id="builder-back-dialog"' not in content
        assert _draft(client, draft_id) == before

    def test_invalid_jump_to_earlier_step_offers_to_discard_changes(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)
        before = _draft(client, draft_id)

        response = client.post(
            f"/builder/{draft_id}/config/", data={"ais_resource_ids_json": "{not json", "next": "scope"}
        )

        assert response.status_code == 400
        content = response.content.decode("utf-8")
        assert "errorlist" in content
        assert '<dialog class="builder-back-dialog" id="builder-back-dialog" open' in content
        assert "Discard your changes on this page and go to scope?" in content
        assert f'href="/builder/{draft_id}/scope/" data-back-discard>Discard &amp; go to scope</a>' in content
        assert _draft(client, draft_id) == before

        discarded = client.get(f"/builder/{draft_id}/scope/")
        assert discarded.status_code == 200
        assert _draft(client, draft_id) == before

    def test_invalid_review_json_jump_offers_to_discard_changes(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)
        before = _draft(client, draft_id)

        response = client.post(f"/builder/{draft_id}/review/json/", data={"plan_json": "not json", "next": "security"})

        assert response.status_code == 400
        content = response.content.decode("utf-8")
        assert f'href="/builder/{draft_id}/config/security/" data-back-discard>' in content
        assert _draft(client, draft_id) == before

    def test_unavailable_catalogue_is_not_saved(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)
        before = _draft(client, draft_id)

        with patch("conformance.api.ui_views.catalogue_boundary_continue_blocker", return_value="Not executable yet."):
            response = client.post(
                f"/builder/{draft_id}/catalogue/",
                data=_read_write_boundary(version="4.0.0", next="review"),
            )

        assert response.status_code == 400
        assert "Not executable yet." in response.content.decode("utf-8")
        assert _draft(client, draft_id) == before

    @pytest.mark.parametrize(
        "next_value",
        ["https://evil.example/", "//evil.example/", "builder-review", "/builder/x/review/", "unknown", "review"],
    )
    def test_untrusted_or_locked_next_falls_back_to_the_following_step(self, next_value: str) -> None:
        client = Client()
        draft_id = _new_draft_id(client)

        response = client.post(f"/builder/{draft_id}/catalogue/", data=_read_write_boundary(next=next_value))

        assert response.status_code == 302
        assert response["Location"] == f"/builder/{draft_id}/config/discovery/"

    def test_review_rejects_untrusted_next(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)

        response = client.post(f"/builder/{draft_id}/review/json/", data={"next": "https://evil.example/"})

        assert response["Location"] == f"/builder/{draft_id}/review/"

    def test_changing_specification_relocks_later_steps(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)

        response = client.post(
            f"/builder/{draft_id}/catalogue/",
            data={
                "scheme": "open-banking-uk",
                "specification": "dynamic-client-registration",
                "version": "3.4",
                "next": "review",
            },
        )

        assert response["Location"] == f"/builder/{draft_id}/scope/"
        assert _draft(client, draft_id).completed_steps == ("catalogue",)
        assert client.get(f"/builder/{draft_id}/review/")["Location"] == f"/builder/{draft_id}/scope/"


class TestBack:
    """Back saves valid edits and asks before discarding invalid ones."""

    @patch("conformance.api.ui_views._fetch_discovery_metadata", return_value={})
    def test_valid_back_saves_and_goes_to_previous_step(self, _mock_fetch: Mock) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)
        new_url = "https://changed.example.com/.well-known/openid-configuration"

        response = client.post(
            f"/builder/{draft_id}/config/discovery/", data={"discovery_url": new_url, "next": "back"}
        )

        assert response["Location"] == f"/builder/{draft_id}/catalogue/"
        assert _draft(client, draft_id).config["discoveryUrl"] == new_url

    def test_back_button_submits_the_page_form(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)

        content = client.get(f"/builder/{draft_id}/config/security/").content.decode("utf-8")

        assert 'name="next" value="back" formnovalidate data-back-button>Back to discovery</button>' in content
        assert 'id="builder-back-dialog"' not in content

    def test_invalid_back_offers_to_discard_changes(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)
        before = _draft(client, draft_id)

        response = client.post(
            f"/builder/{draft_id}/config/discovery/",
            data={"discovery_url": "http://insecure.example.com/", "next": "back"},
        )

        assert response.status_code == 400
        content = response.content.decode("utf-8")
        assert '<dialog class="builder-back-dialog" id="builder-back-dialog" open' in content
        assert "This page has errors. Discard your changes on this page and go to specification?" in content
        assert (
            f'href="/builder/{draft_id}/catalogue/" data-back-discard>Discard &amp; go to specification</a>' in content
        )
        assert '<form method="dialog">' in content
        assert "autofocus>Stay</button>" in content
        assert _draft(client, draft_id) == before

        discarded = client.get(f"/builder/{draft_id}/catalogue/")
        assert discarded.status_code == 200
        assert _draft(client, draft_id) == before
        assert _draft(client, draft_id).config["discoveryUrl"] == _DISCOVERY_URL

    @patch("conformance.api.ui_views._fetch_discovery_metadata", return_value={})
    def test_back_from_unvisited_valid_step_does_not_mark_it_complete(self, _mock_fetch: Mock) -> None:
        client = Client()
        draft_id = _new_draft_id(client)
        client.post(f"/builder/{draft_id}/catalogue/", data=_read_write_boundary())

        response = client.post(f"/builder/{draft_id}/config/discovery/", data={"discovery_url": "", "next": "back"})

        assert response["Location"] == f"/builder/{draft_id}/catalogue/"
        assert _draft(client, draft_id).completed_steps == ("catalogue",)
        content = client.get(f"/builder/{draft_id}/catalogue/").content.decode("utf-8")
        assert content.count('data-builder-step-state="locked"') == 4

    def test_empty_scope_back_saves_without_unlocking_business_data(self) -> None:
        client = Client()
        draft_id = _draft_at_scope(client)

        response = client.post(f"/builder/{draft_id}/scope/", data={"next": "back"})

        assert response["Location"] == f"/builder/{draft_id}/config/security/"
        draft = _draft(client, draft_id)
        assert "scope" not in draft.completed_steps
        assert client.get(f"/builder/{draft_id}/config/")["Location"] == f"/builder/{draft_id}/scope/"

    def test_clearing_a_completed_scope_relocks_business_data(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)

        client.post(f"/builder/{draft_id}/scope/", data={"next": "security"})

        draft = _draft(client, draft_id)
        assert draft.resource_group_ids == ()
        assert "scope" not in draft.completed_steps
        assert "config" not in draft.completed_steps

    def test_continue_with_empty_scope_shows_an_error(self) -> None:
        client = Client()
        draft_id = _draft_at_scope(client)

        response = client.post(f"/builder/{draft_id}/scope/", data={})

        assert response.status_code == 400
        assert "Select at least one resource group to continue." in response.content.decode("utf-8")
        assert "scope" not in _draft(client, draft_id).completed_steps


class TestScopeCompleteness:
    """A resource group without endpoints cannot be carried into Business data."""

    @patch("conformance.api.ui_views.resource_groups_without_endpoints", return_value=("Account and Transaction",))
    def test_continue_with_group_lacking_endpoints_shows_an_error(self, _mock_missing: Mock) -> None:
        client = Client()
        draft_id = _draft_at_scope(client)

        response = client.post(f"/builder/{draft_id}/scope/", data={"resource_groups": ["account-and-transaction"]})

        assert response.status_code == 400
        content = response.content.decode("utf-8")
        assert "Select at least one Account and Transaction endpoint to continue." in content
        assert "scope" not in _draft(client, draft_id).completed_steps

    @patch("conformance.api.ui_views.resource_groups_without_endpoints", return_value=("Account and Transaction",))
    def test_back_with_group_lacking_endpoints_saves_without_completing_scope(self, _mock_missing: Mock) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)

        response = client.post(
            f"/builder/{draft_id}/scope/",
            data={"resource_groups": ["account-and-transaction"], "next": "security"},
        )

        assert response.status_code == 302
        draft = _draft(client, draft_id)
        assert draft.resource_group_ids == ("account-and-transaction",)
        assert "scope" not in draft.completed_steps
        assert "config" not in draft.completed_steps

    def test_business_data_redirects_to_scope_when_a_group_has_no_endpoints(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)
        session = client.session
        store = SessionBuilderDraftStore(session)
        draft = store.get(draft_id)
        assert draft is not None
        store.save(
            draft.with_scope_selection(
                resource_group_ids=("account-and-transaction",),
                endpoint_ids=(),
                endpoint_capability_ids={},
            )
        )
        session.save()

        response = client.get(f"/builder/{draft_id}/config/")

        assert response.status_code == 302
        assert response["Location"] == f"/builder/{draft_id}/scope/"

    def test_business_data_hides_every_field_when_scope_cannot_be_resolved(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)

        with patch("conformance.api.ui_views.plan_document_from_draft", side_effect=CatalogueError("bad scope")):
            response = client.get(f"/builder/{draft_id}/config/")

        assert response.status_code == 400
        content = response.content.decode("utf-8")
        assert "Scope validation failed: bad scope" in content
        assert 'name="ais_consented_account_id"' not in content
        assert 'name="pis_creditor_account_scheme_name"' not in content
        assert 'name="vrp_creditor_account_name"' not in content

    def test_business_data_shows_only_selected_group_fields(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)

        content = client.get(f"/builder/{draft_id}/config/").content.decode("utf-8")

        assert 'name="ais_consented_account_id"' in content
        assert 'name="pis_creditor_account_scheme_name"' not in content
        assert 'name="cbpii_debtor_account_name"' not in content
        assert 'name="vrp_creditor_account_name"' not in content


class TestLocking:
    """Locked steps cannot be opened by URL."""

    def test_review_and_later_steps_redirect_to_first_incomplete_step(self) -> None:
        client = Client()
        draft_id = _new_draft_id(client)
        client.post(f"/builder/{draft_id}/catalogue/", data=_read_write_boundary())

        assert client.get(f"/builder/{draft_id}/review/")["Location"] == f"/builder/{draft_id}/config/discovery/"
        assert client.get(f"/builder/{draft_id}/config/security/")["Location"] == (
            f"/builder/{draft_id}/config/discovery/"
        )
