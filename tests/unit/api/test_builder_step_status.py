"""Builder step issues and step-bar progress computed from saved draft data."""

from __future__ import annotations

from dataclasses import replace

import pytest

from conformance.api.builder_draft_store import BuilderDraft
from conformance.api.builder_step_status import builder_step_issues, builder_step_progress
from conformance.api.builder_wizard import catalogue_scope_hierarchy
from conformance.catalogue import PlanDocumentBoundary

pytestmark = pytest.mark.unit

_DISCOVERY_URL = "https://aspsp.example.com/.well-known/openid-configuration"


def _read_write_draft() -> BuilderDraft:
    return BuilderDraft.create().with_catalogue_boundary(
        scheme="open-banking-uk", specification="read-write", version="4.0.1"
    )


def _ais_scoped_draft() -> BuilderDraft:
    hierarchy = catalogue_scope_hierarchy(
        PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1"),
        selected_resource_group_ids=("account-and-transaction",),
    )
    endpoint = next(
        endpoint
        for group in hierarchy.resource_groups
        for endpoint in group.endpoints
        if endpoint.path == "/open-banking/v4.0/aisp/accounts"
    )
    return _read_write_draft().with_scope_selection(
        resource_group_ids=("account-and-transaction",),
        endpoint_ids=(endpoint.id,),
        endpoint_capability_ids={},
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
        "scope": "not_started",
        "security": "not_started",
        "config": "not_started",
    }


def test_empty_scope_is_reported_as_an_issue() -> None:
    assert builder_step_issues(_read_write_draft())["scope"] == ("Select at least one resource group.",)
    assert builder_step_issues(_dcr_draft())["scope"] == ("Select at least one endpoint.",)


def test_valid_saved_value_marks_the_step_complete() -> None:
    draft = _read_write_draft()
    draft = draft.with_config(config={**draft.config, "discoveryUrl": _DISCOVERY_URL})

    assert builder_step_issues(draft)["security"] == ()
    assert builder_step_progress(draft)["security"] == "complete"


def test_retained_invalid_discovery_value_marks_the_security_step_for_attention() -> None:
    draft = _read_write_draft().with_invalid_field_values("discovery", {"discovery_url": "http://insecure/"})

    issues = builder_step_issues(draft)["security"]

    assert "Discovery URL: discoveryUrl must be an HTTPS URL" in issues
    assert builder_step_progress(draft)["security"] == "attention"


def test_selected_scope_reports_connection_values_required_to_run() -> None:
    draft = _ais_scoped_draft()

    issues = builder_step_issues(draft)["security"]

    assert any(issue.startswith("Client ID: required to run because") for issue in issues)
    assert any(issue.startswith("Discovery URL: required to run because") for issue in issues)
    assert any(issue.startswith("Resource server base URL: required to run because") for issue in issues)
    assert not any("mTLS" in issue for issue in issues)
    assert builder_step_progress(draft)["security"] == "not_started"


def test_saved_security_step_missing_run_values_needs_attention() -> None:
    draft = _ais_scoped_draft().with_steps_saved("security")

    assert builder_step_progress(draft)["security"] == "attention"


def test_unscoped_security_step_reports_no_run_requirements() -> None:
    draft = _read_write_draft().with_steps_saved("security")

    assert builder_step_issues(draft)["security"] == ()


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
