"""PSU authorisation settings in builder drafts, forms, import, and review."""

from __future__ import annotations

import pytest
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.template.loader import render_to_string

from conformance.api.builder_draft_store import BuilderDraft, SessionBuilderDraftStore
from conformance.api.builder_wizard import (
    SecurityConfigForm,
    plan_document_from_draft,
    plan_document_to_export_json,
    plan_json_from_draft,
)
from conformance.api.plan_import_recovery import recover_draft_from_plan_json
from conformance.api.ui_views import _builder_review_context
from conformance.catalogue import compile_test_plan_document
from conformance.catalogue_registry import supported_catalogues
from conformance.json_types import JsonObject

pytestmark = pytest.mark.unit


def _rw_plan() -> JsonObject:
    """Return a minimal builder-resolvable Read/Write plan."""
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
        "metadata": {},
        "execution": {
            "psuAuthorization": {
                "mode": "auto-approve",
                "headers": {"X-Sandbox-Auto-Approve": "true"},
                "parameters": {"sandbox_auto_approve": True, "psu_id": "user1"},
            }
        },
    }


def test_draft_store_round_trips_psu_authorization_values() -> None:
    """Session persistence keeps mode, headers, and typed parameters."""
    store = SessionBuilderDraftStore(SessionStore())
    draft = store.create().with_psu_authorization_settings(
        mode="auto-approve",
        headers=(("X-Sandbox-Auto-Approve", "true"),),
        parameters=(("sandbox_auto_approve", True), ("psu_id", "user1")),
    )

    store.save(draft)

    loaded = store.get(draft.draft_id)
    assert loaded is not None
    assert loaded.psu_authorization_mode == "auto-approve"
    assert loaded.psu_authorization_headers == (("X-Sandbox-Auto-Approve", "true"),)
    assert loaded.psu_authorization_parameters == (("sandbox_auto_approve", True), ("psu_id", "user1"))


def test_security_form_rejects_duplicate_parameter_and_reserved_header_names() -> None:
    """Shared execution validation errors attach to the offending row fields."""
    duplicate = SecurityConfigForm(
        data={
            "psu_authorization_mode": "auto-approve",
            "psu_parameter_name_0": "sandbox",
            "psu_parameter_value_0": "one",
            "psu_parameter_name_1": "SANDBOX",
            "psu_parameter_value_1": "two",
        }
    )
    assert not duplicate.is_valid()
    assert "duplicates another parameter" in duplicate.errors["psu_parameter_name_1"][0]

    reserved = SecurityConfigForm(
        data={
            "psu_authorization_mode": "auto-approve",
            "psu_header_name_0": "Host",
            "psu_header_value_0": "example.com",
        }
    )
    assert not reserved.is_valid()
    assert "managed by the HTTP client" in reserved.errors["psu_header_name_0"][0]


def test_manual_mode_retains_but_does_not_apply_or_export_headers() -> None:
    """Manual mode retains header rows while emitting only active settings."""
    form = SecurityConfigForm(
        data={
            "psu_authorization_mode": "manual",
            "psu_header_name_0": "X-Sandbox-Auto-Approve",
            "psu_header_value_0": "true",
            "psu_parameter_name_0": "psu_id",
            "psu_parameter_value_0": "user1",
        }
    )
    assert form.is_valid(), form.errors.as_json()
    assert form.psu_authorization_headers == (("X-Sandbox-Auto-Approve", "true"),)
    assert form.psu_authorization_settings is not None
    assert form.psu_authorization_settings.headers == ()

    draft = BuilderDraft.create().with_psu_authorization_settings(
        mode=form.psu_authorization_settings.mode,
        headers=form.psu_authorization_headers,
        parameters=form.psu_authorization_settings.parameters,
    )
    execution = plan_json_from_draft(draft)["execution"]
    assert isinstance(execution, dict)
    psu = execution["psuAuthorization"]
    assert isinstance(psu, dict)
    assert psu == {"mode": "manual", "parameters": {"psu_id": "user1"}}


def test_import_draft_export_round_trip_and_compiled_plan_carry_settings() -> None:
    """Imported execution settings survive draft composition and compilation."""
    draft = recover_draft_from_plan_json(_rw_plan(), draft=BuilderDraft.create())

    exported = plan_json_from_draft(draft)
    assert exported["execution"] == _rw_plan()["execution"]
    document = plan_document_from_draft(draft)
    safe_export = plan_document_to_export_json(
        document,
        sensitive_runtime_input_ids=(),
        include_secrets=False,
    )
    assert safe_export["execution"] == _rw_plan()["execution"]
    assert document.execution_settings.psu_authorization.mode == "auto-approve"
    compiled = compile_test_plan_document(document, supported_catalogues())
    assert compiled.execution_settings.psu_authorization.headers == (("X-Sandbox-Auto-Approve", "true"),)
    assert compiled.execution_settings.psu_authorization.parameters == (
        ("sandbox_auto_approve", True),
        ("psu_id", "user1"),
    )


def test_review_summary_shows_names_without_parameter_or_header_values() -> None:
    """The review section lists setting names and never interpolates values."""
    draft = BuilderDraft.create().with_psu_authorization_settings(
        mode="auto-approve",
        headers=(("X-Sandbox-Auto-Approve", "private-header-value"),),
        parameters=(("psu_id", "private-parameter-value"),),
    )
    context = _builder_review_context(draft=draft)

    rendered = render_to_string("conformance/partials/builder_review_summary.html", context)

    assert "X-Sandbox-Auto-Approve" in rendered
    assert "psu_id" in rendered
    assert "private-header-value" not in rendered
    assert "private-parameter-value" not in rendered
