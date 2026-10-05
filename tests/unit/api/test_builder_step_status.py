"""Builder step issues and step-bar progress computed from saved draft data."""

from __future__ import annotations

from dataclasses import replace

import pytest

from conformance.api.builder_draft_store import BuilderDraft
from conformance.api.builder_step_status import builder_step_issues, builder_step_progress

pytestmark = pytest.mark.unit

_DISCOVERY_URL = "https://aspsp.example.com/.well-known/openid-configuration"


def _read_write_draft() -> BuilderDraft:
    return BuilderDraft.create().with_catalogue_boundary(
        scheme="open-banking-uk", specification="read-write", version="4.0.1"
    )


def _dcr_draft() -> BuilderDraft:
    return BuilderDraft.create().with_catalogue_boundary(
        scheme="open-banking-uk", specification="dynamic-client-registration", version="3.4"
    )


def test_only_the_specification_is_reported_until_one_is_selected() -> None:
    draft = BuilderDraft.create()

    assert builder_step_issues(draft) == {"catalogue": ("Choose a supported specification.",)}
    assert set(builder_step_progress(draft).values()) == {"not_started"}


def test_selected_specification_is_complete_and_other_steps_not_started() -> None:
    progress = builder_step_progress(_read_write_draft())

    assert progress == {
        "catalogue": "complete",
        "discovery": "not_started",
        "security": "not_started",
        "scope": "not_started",
        "config": "not_started",
    }


def test_empty_scope_is_reported_as_an_issue() -> None:
    assert builder_step_issues(_read_write_draft())["scope"] == ("Select at least one resource group.",)
    assert builder_step_issues(_dcr_draft())["scope"] == ("Select at least one endpoint.",)


def test_valid_saved_value_marks_the_step_complete() -> None:
    draft = _read_write_draft()
    draft = draft.with_config(config={**draft.config, "discoveryUrl": _DISCOVERY_URL})

    assert builder_step_issues(draft)["discovery"] == ()
    assert builder_step_progress(draft)["discovery"] == "complete"


def test_retained_invalid_value_marks_the_step_for_attention() -> None:
    draft = _read_write_draft().with_invalid_field_values("discovery", {"discovery_url": "http://insecure/"})

    issues = builder_step_issues(draft)["discovery"]

    assert issues == ("Discovery URL: discoveryUrl must be an HTTPS URL",)
    assert builder_step_progress(draft)["discovery"] == "attention"


def test_missing_required_values_are_issues_without_starting_the_step() -> None:
    draft = _dcr_draft()

    issues = builder_step_issues(draft)

    assert "Discovery URL: This field is required." in issues["discovery"]
    assert any("Signing private key" in issue for issue in issues["security"])
    assert builder_step_progress(draft)["security"] == "not_started"


def test_partially_entered_step_needs_attention() -> None:
    draft = _dcr_draft()
    draft = draft.with_config(config={**draft.config, "discoveryUrl": _DISCOVERY_URL})
    draft = replace(draft, dynamic_client_registration={"registrationAudience": "aspsp123"})

    assert builder_step_progress(draft)["discovery"] == "complete"
    assert builder_step_progress(draft)["security"] == "attention"


def test_precomputed_issues_are_used_for_progress() -> None:
    draft = _read_write_draft()

    progress = builder_step_progress(draft, issues={"catalogue": ("forced",)})

    assert progress["catalogue"] == "attention"
