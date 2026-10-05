"""Component tests for open builder navigation, save-on-leave, and step status."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any
from unittest.mock import Mock, patch

import pytest
from django.test import Client

from conformance.api.builder_draft_store import BuilderDraft, SessionBuilderDraftStore

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
    """Return a fresh Read/Write draft with only a specification selected."""
    draft_id = _new_draft_id(client)
    client.post(f"/builder/{draft_id}/catalogue/", data=_read_write_boundary())
    return draft_id


def _step_state(content: str, step_id: str) -> str:
    match = re.search(rf'data-builder-step="{step_id}" data-builder-step-state="([a-z_]+)"', content)
    assert match is not None, step_id
    return match.group(1)


class TestStepBar:
    """The step bar renders on every builder page and reports saved-data status."""

    def test_imported_plan_review_offers_every_step(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)

        content = client.get(f"/builder/{draft_id}/review/").content.decode("utf-8")

        assert 'aria-label="Builder steps"' in content
        assert '<span class="builder-step-number">6</span>Review</span>' in content
        for step_id in ("catalogue", "discovery", "security", "scope", "config"):
            assert f'name="next" value="{step_id}" form="review-plan-form" formnovalidate' in content
        assert _step_state(content, "catalogue") == "complete"
        assert _step_state(content, "discovery") == "complete"
        assert _step_state(content, "scope") == "complete"
        assert "Edit specification" not in content

    def test_new_draft_locks_steps_until_a_specification_is_selected(self) -> None:
        client = Client()
        draft_id = _new_draft_id(client)

        content = client.get(f"/builder/{draft_id}/catalogue/").content.decode("utf-8")

        assert 'id="builder-step-form"' in content
        assert content.count('data-builder-step-state="locked"') == 5
        assert content.count('class="builder-step-pill" aria-disabled="true"') == 5
        assert content.count('role="tooltip"') == 5
        assert content.count("Select a specification first to unlock this step.") == 5
        assert 'aria-describedby="builder-step-locked-tip-discovery"' in content
        assert 'id="builder-step-locked-tip-discovery"' in content
        assert 'name="next" value="discovery"' not in content

    def test_selecting_a_specification_opens_every_step(self) -> None:
        client = Client()
        draft_id = _draft_at_scope(client)

        content = client.get(f"/builder/{draft_id}/scope/").content.decode("utf-8")

        assert 'data-builder-step-state="locked"' not in content
        assert 'role="tooltip"' not in content
        assert _step_state(content, "catalogue") == "complete"
        assert _step_state(content, "discovery") == "not_started"
        assert _step_state(content, "config") == "not_started"
        for step_id in ("catalogue", "discovery", "security", "config", "review"):
            assert f'name="next" value="{step_id}"' in content

    def test_leaving_scope_empty_flags_it_and_leaves_unvisited_steps_untouched(self) -> None:
        client = Client()
        draft_id = _draft_at_scope(client)

        client.post(f"/builder/{draft_id}/scope/", data={"next": "back"})
        content = client.get(f"/builder/{draft_id}/catalogue/").content.decode("utf-8")

        assert _step_state(content, "scope") == "attention"
        assert _step_state(content, "config") == "not_started"
        assert _step_state(content, "discovery") == "not_started"

    def test_leaving_business_data_without_scope_does_not_mark_it_complete(self) -> None:
        client = Client()
        draft_id = _draft_at_scope(client)

        client.post(f"/builder/{draft_id}/config/", data={"next": "back"})
        content = client.get(f"/builder/{draft_id}/scope/").content.decode("utf-8")

        assert _step_state(content, "config") == "not_started"

    def test_leaving_required_business_data_empty_flags_it(self) -> None:
        client = Client()
        plan = _import_plan()
        plan["resourceGroups"][0]["endpoints"] = [
            {"method": "GET", "path": "/open-banking/v4.0/aisp/accounts/{AccountId}"}
        ]
        draft_id = str(client.post("/builder/import/", data={"plan_json": json.dumps(plan)})["Location"]).split("/")[2]
        session = client.session
        store = SessionBuilderDraftStore(session)
        draft = store.get(draft_id)
        assert draft is not None
        store.save(replace(draft, saved_steps=()))
        session.save()
        before = client.get(f"/builder/{draft_id}/review/").content.decode("utf-8")
        assert _step_state(before, "config") == "not_started"

        client.post(f"/builder/{draft_id}/config/", data={"next": "review"})
        review = client.get(f"/builder/{draft_id}/review/").content.decode("utf-8")

        assert _step_state(review, "config") == "attention"

    def test_imported_plan_without_scope_flags_scope(self) -> None:
        client = Client()
        plan = {key: value for key, value in _import_plan().items() if key != "resourceGroups"}
        response = client.post("/builder/import/", data={"plan_json": json.dumps(plan)})
        draft_id = str(response["Location"]).split("/")[2]

        review = client.get(f"/builder/{draft_id}/review/").content.decode("utf-8")

        assert _step_state(review, "scope") == "attention"


class TestStepBarLayout:
    """Every builder page shares one layout so the step bar never moves."""

    def test_review_is_set_apart_by_a_divider(self) -> None:
        client = Client()
        draft_id = _draft_at_scope(client)

        content = client.get(f"/builder/{draft_id}/scope/").content.decode("utf-8")

        assert content.count("builder-step-divider") == 2  # one CSS rule, one divider
        assert re.search(
            r'<li class="builder-step-divider" aria-hidden="true"></li>\s*<li[^>]*data-builder-step="review"', content
        )

    @pytest.mark.parametrize("step", ["catalogue", "config/discovery", "config/security", "scope", "config", "review"])
    def test_every_step_shares_one_page_width(self, step: str) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)

        with patch("conformance.api.ui_views._fetch_discovery_metadata", return_value=None):
            content = client.get(f"/builder/{draft_id}/{step}/").content.decode("utf-8")

        assert "width: min(1180px, calc(100% - 32px));" in content
        assert 'class="button secondary builder-menu-link"' in content

    def test_draft_id_is_shown_on_review_only(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)

        scope = client.get(f"/builder/{draft_id}/scope/").content.decode("utf-8")
        review = client.get(f"/builder/{draft_id}/review/").content.decode("utf-8")

        assert f'<span class="step-id">{draft_id}</span>' not in scope
        assert f'<span class="step-id">{draft_id}</span>' in review
        assert "Next you will" not in scope

    def test_review_shows_a_compile_error_once(self) -> None:
        client = Client()
        plan = _import_plan()
        del plan["securityEnvironment"]["resourceBaseUrl"]
        response = client.post("/builder/import/", data={"plan_json": json.dumps(plan)})
        draft_id = str(response["Location"]).split("/")[2]

        review = client.get(f"/builder/{draft_id}/review/").content.decode("utf-8")

        assert review.count("Required runtime input &#x27;resourceBaseUrl&#x27; is missing") == 1

    def test_empty_scope_review_shows_the_scope_issue_instead_of_the_raw_validation_error(self) -> None:
        client = Client()
        draft_id = _draft_at_scope(client)

        review = client.get(f"/builder/{draft_id}/review/").content.decode("utf-8")
        launch = client.post(f"/builder/{draft_id}/launch/")

        assert 'data-step-issues="scope"' in review
        assert "testPlan.resourceGroups must contain" not in review
        assert launch.status_code == 400


class TestStepJumps:
    """Step-bar jumps always save the page first, then leave it."""

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

    def test_invalid_value_is_kept_and_flagged_when_jumping_away(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)

        response = client.post(
            f"/builder/{draft_id}/config/discovery/",
            data={"discovery_url": "http://insecure.example.com/", "next": "review"},
        )

        assert response.status_code == 302
        assert response["Location"] == f"/builder/{draft_id}/review/"
        draft = _draft(client, draft_id)
        assert draft.config["discoveryUrl"] == _DISCOVERY_URL
        assert draft.invalid_field_values["discovery"] == {"discovery_url": "http://insecure.example.com/"}

        review = client.get(response["Location"]).content.decode("utf-8")
        assert _step_state(review, "discovery") == "attention"
        assert 'data-step-issues="discovery"' in review
        assert 'name="launch_confirmation"' not in review or "disabled" in review

        revisited = client.get(f"/builder/{draft_id}/config/discovery/").content.decode("utf-8")
        assert 'value="http://insecure.example.com/"' in revisited
        assert "errorlist" in revisited

    def test_correcting_an_invalid_value_clears_the_flag(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)
        client.post(f"/builder/{draft_id}/config/discovery/", data={"discovery_url": "http://insecure.example.com/"})

        with patch("conformance.api.ui_views._fetch_discovery_metadata", return_value={}):
            client.post(f"/builder/{draft_id}/config/discovery/", data={"discovery_url": _DISCOVERY_URL})

        assert not _draft(client, draft_id).invalid_field_values.get("discovery")

    def test_partial_business_data_is_saved_when_jumping_back(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)

        response = client.post(
            f"/builder/{draft_id}/config/", data={"ais_consented_account_id": "acc-1", "next": "scope"}
        )

        assert response.status_code == 302
        assert response["Location"] == f"/builder/{draft_id}/scope/"
        assert "acc-1" in json.dumps(_draft(client, draft_id).config)

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
        ["https://evil.example/", "//evil.example/", "builder-review", "/builder/x/review/", "unknown"],
    )
    def test_untrusted_next_falls_back_to_the_following_step(self, next_value: str) -> None:
        client = Client()
        draft_id = _new_draft_id(client)

        response = client.post(f"/builder/{draft_id}/catalogue/", data=_read_write_boundary(next=next_value))

        assert response.status_code == 302
        assert response["Location"] == f"/builder/{draft_id}/config/discovery/"

    def test_new_plan_can_jump_straight_to_review_once_specification_is_chosen(self) -> None:
        client = Client()
        draft_id = _new_draft_id(client)

        response = client.post(f"/builder/{draft_id}/catalogue/", data=_read_write_boundary(next="review"))

        assert response["Location"] == f"/builder/{draft_id}/review/"

    def test_review_rejects_untrusted_next(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)

        response = client.post(f"/builder/{draft_id}/review/json/", data={"next": "https://evil.example/"})

        assert response["Location"] == f"/builder/{draft_id}/review/"


class TestSpecificationChange:
    """Changing the specification warns before it affects entered data."""

    def test_change_that_drops_scope_asks_for_confirmation_first(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)
        before = _draft(client, draft_id)
        dcr = {
            "scheme": "open-banking-uk",
            "specification": "dynamic-client-registration",
            "version": "3.4",
            "next": "review",
        }

        response = client.post(f"/builder/{draft_id}/catalogue/", data=dcr)

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "data-specification-change" in content
        assert "Confirm specification change" in content
        assert _draft(client, draft_id) == before

        confirmed = client.post(
            f"/builder/{draft_id}/catalogue/",
            data={**dcr, "confirm_specification_change": "open-banking-uk/dynamic-client-registration/3.4"},
        )

        assert confirmed.status_code == 302
        assert confirmed["Location"] == f"/builder/{draft_id}/review/"
        assert _draft(client, draft_id).specification == "dynamic-client-registration"

    def test_reselecting_the_same_specification_needs_no_confirmation(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)

        response = client.post(
            f"/builder/{draft_id}/catalogue/",
            data=_read_write_boundary(openapi_document_update="Update-1", next="review"),
        )

        assert response.status_code == 302


class TestBack:
    """Back always saves what was entered, valid or not."""

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

    def test_invalid_back_keeps_the_value_without_a_discard_dialog(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)

        response = client.post(
            f"/builder/{draft_id}/config/discovery/",
            data={"discovery_url": "http://insecure.example.com/", "next": "back"},
        )

        assert response.status_code == 302
        assert response["Location"] == f"/builder/{draft_id}/catalogue/"
        assert _draft(client, draft_id).invalid_field_values["discovery"]
        page = client.get(response["Location"]).content.decode("utf-8")
        assert 'id="builder-back-dialog"' not in page
        assert _step_state(page, "discovery") == "attention"

    def test_empty_scope_saves_and_continues(self) -> None:
        client = Client()
        draft_id = _draft_at_scope(client)

        response = client.post(f"/builder/{draft_id}/scope/", data={})

        assert response.status_code == 302
        assert response["Location"] == f"/builder/{draft_id}/config/"
        assert _draft(client, draft_id).resource_group_ids == ()

    def test_clearing_imported_scope_flags_scope_at_review(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)

        client.post(f"/builder/{draft_id}/scope/", data={"next": "security"})

        assert _draft(client, draft_id).resource_group_ids == ()
        review = client.get(f"/builder/{draft_id}/review/").content.decode("utf-8")
        assert _step_state(review, "scope") == "attention"
        assert 'data-step-issues="scope"' in review
        assert "Select at least one resource group" in review


class TestScopeAndBusinessData:
    """Business data follows the saved scope, and is empty when there is none."""

    def test_business_data_without_scope_shows_an_empty_state(self) -> None:
        client = Client()
        draft_id = _draft_at_scope(client)

        response = client.get(f"/builder/{draft_id}/config/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "data-scope-missing" in content
        assert 'name="ais_consented_account_id"' not in content
        assert 'name="pis_creditor_account_scheme_name"' not in content
        assert "No business data inputs required" not in content

    def test_business_data_hides_every_field_when_scope_cannot_be_resolved(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)

        with patch("conformance.api.ui_views.business_config_form_for_draft", return_value=None):
            response = client.get(f"/builder/{draft_id}/config/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "data-scope-missing" in content
        assert 'name="ais_consented_account_id"' not in content
        assert 'name="vrp_creditor_account_name"' not in content

    def test_group_without_endpoints_is_flagged_at_review(self) -> None:
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

        review = client.get(f"/builder/{draft_id}/review/").content.decode("utf-8")

        assert _step_state(review, "scope") == "attention"
        assert "Select at least one" in review

    @patch("conformance.api.ui_views.start_run")
    def test_invalid_business_data_is_listed_at_review_and_blocks_launch(self, mock_start_run: Mock) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)
        client.post(f"/builder/{draft_id}/config/", data={"ais_resource_ids_json": "{not json", "next": "review"})

        review = client.get(f"/builder/{draft_id}/review/").content.decode("utf-8")
        launch = client.post(f"/builder/{draft_id}/launch/")

        assert 'data-step-issues="config"' in review
        assert 'name="next" value="config" form="review-plan-form"' in review
        assert launch.status_code == 400
        mock_start_run.assert_not_called()

    def test_business_data_shows_only_selected_group_fields(self) -> None:
        client = Client()
        draft_id = _imported_draft_id(client)

        content = client.get(f"/builder/{draft_id}/config/").content.decode("utf-8")

        assert 'name="ais_consented_account_id"' in content
        assert 'name="pis_creditor_account_scheme_name"' not in content
        assert 'name="cbpii_debtor_account_name"' not in content
        assert 'name="vrp_creditor_account_name"' not in content


class TestImportWithoutSpecification:
    """A plan with no usable specification opens on the specification step."""

    def test_later_steps_redirect_to_specification(self) -> None:
        client = Client()
        plan = _import_plan()
        del plan["specification"]
        response = client.post("/builder/import/", data={"plan_json": json.dumps(plan)})
        draft_id = str(response["Location"]).split("/")[2]

        assert response["Location"] == f"/builder/{draft_id}/catalogue/"
        for path in ("config/discovery/", "scope/", "config/", "review/"):
            assert client.get(f"/builder/{draft_id}/{path}")["Location"] == f"/builder/{draft_id}/catalogue/"
        page = client.get(response["Location"]).content.decode("utf-8")
        assert "data-start-new-plan" in page
        assert "Import warnings" in page

    def test_unusable_import_offers_to_start_a_new_plan(self) -> None:
        client = Client()

        response = client.post("/builder/import/", data={"plan_json": "not json"})

        assert "data-start-new-plan" in response.content.decode("utf-8")
