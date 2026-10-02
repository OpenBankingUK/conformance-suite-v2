"""Lenient browser test-plan import recovery into builder drafts."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.datastructures import MultiValueDict

from conformance.api.builder_draft_store import BuilderDraft, PlanImportIssue, SessionBuilderDraftStore
from conformance.api.builder_wizard import merge_plan_json_overlay, plan_json_from_draft
from conformance.api.plan_import_recovery import (
    PLAN_IMPORT_MAX_BYTES,
    PlanImportError,
    PlanImportForm,
    parse_plan_import_text,
    reconcile_draft_after_builder_save,
    recover_draft_from_plan_json,
)
from conformance.test_plan_validation import redact_sensitive_plan_json, validate_test_plan_for_load

pytestmark = pytest.mark.unit


def _rw_plan() -> dict[str, Any]:
    """Return a valid Read/Write schemaVersion 1.0 test plan."""
    return {
        "schemaVersion": "1.0",
        "specification": {"family": "OBL_READ_WRITE", "version": "4.0.1", "profile": "FAPI1_ADVANCED"},
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
        "metadata": {"aspspName": "Example Bank"},
    }


def _dcr_plan() -> dict[str, Any]:
    """Return a valid DCR schemaVersion 1.0 test plan."""
    return {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_DCR",
            "scheme": "open-banking-uk",
            "name": "dynamic-client-registration",
            "version": "3.4",
        },
        "executionMode": "development",
        "securityEnvironment": {
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "clientAuthMethod": "private_key_jwt",
        },
        "endpoints": [{"method": "POST", "path": "/register", "required": True, "locked": True}],
        "dynamicClientRegistration": {
            "softwareStatementAssertion": "ssa.jwt.value",
            "registrationAudience": "0015800001041RHAAY",
        },
        "metadata": {"aspspName": "Example DCR"},
    }


def _recover(plan: dict[str, Any]) -> BuilderDraft:
    """Recover a draft from a raw plan into a fresh builder draft."""
    return recover_draft_from_plan_json(plan, draft=BuilderDraft.create())


def _issue(draft: BuilderDraft, path: str) -> PlanImportIssue:
    """Return the single import issue recorded for a path."""
    matches = [issue for issue in draft.import_issues if issue.path == path]
    assert len(matches) == 1, draft.import_issues
    return matches[0]


@pytest.mark.parametrize("plan_factory", [_rw_plan, _dcr_plan])
def test_valid_plan_recovers_without_issues_and_composes_valid_json(plan_factory: Any) -> None:
    """A schema-valid plan loads fully into the builder with no overlay and no warnings."""
    plan = plan_factory()
    draft = _recover(plan)

    assert draft.import_issues == ()
    assert dict(draft.unrepresented_plan_fields) == {}
    composed_issues = validate_test_plan_for_load(plan_json_from_draft(draft)).issues
    assert [issue.message for issue in composed_issues] == [
        issue.message for issue in validate_test_plan_for_load(plan).issues
    ]


def test_invalid_and_unknown_fields_are_preserved_not_defaulted() -> None:
    """Invalid values stay in the composed JSON so normal validation still fails."""
    plan = _rw_plan()
    plan["schemaVersion"] = "2.0"
    plan["executionMode"] = "fast"
    plan["securityEnvironment"]["discoveryUrl"] = "http://insecure"
    plan["securityEnvironment"]["bogus"] = 1
    plan["extra"] = True

    draft = _recover(plan)
    composed = plan_json_from_draft(draft)

    assert _issue(draft, "schemaVersion").kind == "notice"
    assert _issue(draft, "executionMode").kind == "invalid"
    assert _issue(draft, "securityEnvironment.discoveryUrl").message == (
        "securityEnvironment.discoveryUrl must be an HTTPS URL."
    )
    assert _issue(draft, "securityEnvironment.bogus").kind == "unknown"
    assert _issue(draft, "extra").kind == "unknown"
    assert composed["schemaVersion"] == "1.0"
    assert composed["executionMode"] == "fast"
    assert composed["extra"] is True
    assert composed["securityEnvironment"] == {
        "discoveryUrl": "http://insecure",
        "resourceBaseUrl": "https://resource.example.com",
        "bogus": 1,
    }
    assert draft.resource_group_ids == ("account-and-transaction",)
    assert not validate_test_plan_for_load(composed).valid


def test_scope_items_are_recovered_individually() -> None:
    """One unresolvable scope item does not discard the rest of the scope."""
    plan = _rw_plan()
    plan["resourceGroups"].append({"id": "AIS", "endpoints": [{"method": "GET", "path": "/nope"}]})

    draft = _recover(plan)

    assert draft.resource_group_ids == ("account-and-transaction",)
    issue = _issue(draft, "resourceGroups[1]")
    assert issue.kind == "invalid"
    assert issue.message.startswith("resourceGroups[1] could not be loaded:")
    assert draft.unrepresented_plan_fields["resourceGroups"] == [
        {"id": "AIS", "endpoints": [{"method": "GET", "path": "/nope"}]}
    ]


def test_scope_is_skipped_without_a_specification() -> None:
    """Scope needs a specification; it is kept verbatim until one is chosen."""
    plan = _rw_plan()
    plan["specification"] = {"family": "OBL_READ_WRITE", "version": "9.9"}

    draft = _recover(plan)

    assert draft.specification is None
    assert _issue(draft, "specification").kind == "invalid"
    assert _issue(draft, "resourceGroups").kind == "skipped"
    composed = plan_json_from_draft(draft)
    assert composed["specification"] == {"family": "OBL_READ_WRITE", "version": "9.9"}
    assert composed["resourceGroups"] == plan["resourceGroups"]


def test_empty_object_reports_missing_sections() -> None:
    """An empty object imports as an empty draft with missing-field warnings."""
    draft = _recover({})

    assert _issue(draft, "specification").kind == "missing"
    assert _issue(draft, "securityEnvironment").kind == "missing"
    assert any("Nothing usable was imported" in issue.message for issue in draft.import_issues)


def test_family_specific_sections_are_rejected_for_the_other_family() -> None:
    """DCR-only sections in a Read/Write plan are kept as invalid, not silently dropped."""
    plan = _rw_plan()
    plan["dynamicClientRegistration"] = {"registrationAudience": "0015800001041RHAAY"}
    plan["endpoints"] = [{"method": "GET", "path": "/x"}]

    draft = _recover(plan)

    assert _issue(draft, "dynamicClientRegistration").kind == "invalid"
    assert _issue(draft, "endpoints").kind == "invalid"
    assert "dynamicClientRegistration" in draft.unrepresented_plan_fields


def test_dcr_missing_registration_fields_are_reported() -> None:
    """Missing DCR registration inputs are flagged as missing."""
    plan = _dcr_plan()
    plan["dynamicClientRegistration"] = {}

    draft = _recover(plan)

    assert any(issue.kind == "missing" for issue in draft.import_issues)
    assert not validate_test_plan_for_load(plan_json_from_draft(draft)).valid


def test_composed_json_round_trips_through_recovery() -> None:
    """Re-recovering composed JSON reproduces the same draft content and overlay."""
    plan = _rw_plan()
    plan["extra"] = {"kept": True}
    first = _recover(plan)

    second = _recover(copy.deepcopy(plan_json_from_draft(first)))

    assert plan_json_from_draft(second) == plan_json_from_draft(first)
    assert second.resource_group_ids == first.resource_group_ids
    assert second.endpoint_ids == first.endpoint_ids


def test_merge_plan_json_overlay_merges_objects_and_appends_lists() -> None:
    """Overlay objects merge, lists append, and scalars win."""
    merged = merge_plan_json_overlay(
        {"a": {"x": 1, "y": 2}, "list": [1], "scalar": "builder"},
        {"a": {"y": 3, "z": 4}, "list": [2], "scalar": "overlay", "new": True},
    )

    assert merged == {"a": {"x": 1, "y": 3, "z": 4}, "list": [1, 2], "scalar": "overlay", "new": True}


def test_reconcile_discovery_save_drops_owned_overlay_and_issue() -> None:
    """Saving discovery takes ownership of an imported invalid discoveryUrl."""
    plan = _rw_plan()
    plan["securityEnvironment"]["discoveryUrl"] = "http://insecure"
    plan["extra"] = True
    previous = _recover(plan)
    updated = previous.with_config(
        config={**previous.config, "discoveryUrl": "https://example.com/.well-known/openid-configuration"}
    ).with_plan_context(
        security_environment={
            **previous.security_environment,
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
        },
        business_test_data=previous.business_test_data,
        metadata=previous.metadata,
        execution_mode=previous.execution_mode,
    )

    reconciled = reconcile_draft_after_builder_save(previous, updated, step="discovery")

    assert "securityEnvironment" not in reconciled.unrepresented_plan_fields
    assert reconciled.unrepresented_plan_fields["extra"] is True
    assert [issue.path for issue in reconciled.import_issues] == ["extra"]


def test_reconcile_scope_save_drops_scope_overlay() -> None:
    """Saving scope replaces any imported scope items the builder could not load."""
    plan = _rw_plan()
    plan["resourceGroups"].append("NOT_A_GROUP")
    previous = _recover(plan)

    reconciled = reconcile_draft_after_builder_save(previous, previous, step="scope")

    assert "resourceGroups" not in reconciled.unrepresented_plan_fields
    assert all(not issue.path.startswith("resourceGroups") for issue in reconciled.import_issues)


def test_reconcile_catalogue_save_loads_pending_scope() -> None:
    """Choosing a specification later loads scope that was skipped at import."""
    plan = _rw_plan()
    plan["specification"] = {"family": "OBL_READ_WRITE", "version": "9.9"}
    previous = _recover(plan)
    updated = previous.with_catalogue_boundary(
        scheme="open-banking-uk",
        specification="read-write",
        version="4.0.1",
    )

    reconciled = reconcile_draft_after_builder_save(previous, updated, step="catalogue")

    assert reconciled.resource_group_ids == ("account-and-transaction",)
    assert "specification" not in reconciled.unrepresented_plan_fields
    assert "resourceGroups" not in reconciled.unrepresented_plan_fields
    assert all(issue.path not in {"specification", "resourceGroups"} for issue in reconciled.import_issues)


def test_import_state_round_trips_through_session_and_tolerates_legacy_sessions() -> None:
    """Overlay and issues persist in the session; older sessions decode with defaults."""
    plan = _rw_plan()
    plan["extra"] = {"kept": True}
    draft = _recover(plan)
    session = SessionStore()
    store = SessionBuilderDraftStore(session)

    store.save(draft)
    loaded = store.get(draft.draft_id)
    legacy_raw = dict(draft.to_session_object())
    legacy_raw.pop("unrepresentedPlanFields")
    legacy_raw.pop("importIssues")
    legacy = BuilderDraft.from_session_object(legacy_raw)

    assert loaded is not None
    assert loaded.unrepresented_plan_fields == draft.unrepresented_plan_fields
    assert loaded.import_issues == draft.import_issues
    assert legacy is not None
    assert dict(legacy.unrepresented_plan_fields) == {}
    assert legacy.import_issues == ()


def test_plan_import_issue_session_decoding_rejects_malformed_values() -> None:
    """Malformed stored issues are ignored rather than crashing the draft."""
    assert PlanImportIssue.from_session_object("nope") is None
    assert PlanImportIssue.from_session_object({"path": "x", "ref": ["x"], "kind": "bad", "message": "m"}) is None


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("   ", "Paste plan JSON"),
        ("{", "Plan JSON must be valid JSON"),
        ('"text"', "Plan JSON must be a JSON object."),
        (" " * (PLAN_IMPORT_MAX_BYTES + 1), "Paste plan JSON"),
        ('{"a": "' + "x" * PLAN_IMPORT_MAX_BYTES + '"}', "1 MB or smaller"),
    ],
)
def test_parse_plan_import_text_rejects_unrecoverable_input(text: str, message: str) -> None:
    """Only input with nothing to recover is rejected."""
    with pytest.raises(PlanImportError, match=message):
        parse_plan_import_text(text)


def test_plan_import_form_prefers_uploaded_file_and_limits_size() -> None:
    """An uploaded file wins over pasted text, and oversized uploads are rejected."""
    upload = SimpleUploadedFile("plan.json", b'\xef\xbb\xbf{"metadata": {}}', content_type="application/json")
    form = PlanImportForm(data={"plan_json": "[]"}, files=MultiValueDict({"plan_file": [upload]}))

    assert form.is_valid()
    assert form.raw_plan == {"metadata": {}}

    oversized = SimpleUploadedFile("plan.json", b" " * (PLAN_IMPORT_MAX_BYTES + 1))
    rejected = PlanImportForm(data={}, files=MultiValueDict({"plan_file": [oversized]}))

    assert not rejected.is_valid()
    assert rejected.import_error() == "Plan JSON must be 1 MB or smaller."


def test_business_data_hidden_by_scope_is_kept_for_validation() -> None:
    """Business sections for unselected APIs are not silently dropped from the composed plan."""
    plan = _rw_plan()
    plan["businessTestData"]["pis"] = 42

    draft = _recover(plan)
    composed: dict[str, Any] = plan_json_from_draft(draft)

    assert _issue(draft, "businessTestData.pis").kind == "skipped"
    assert composed["businessTestData"]["pis"] == 42
    assert not validate_test_plan_for_load(composed).valid


def test_scope_save_takes_over_business_data_once_in_scope() -> None:
    """Hidden business data leaves the overlay once the builder emits it."""
    plan = _rw_plan()
    plan["businessTestData"]["ais"] = {"accountIds": ["account-1"]}
    plan["resourceGroups"] = ["CBPII"]
    previous = _recover(plan)
    assert "businessTestData" in previous.unrepresented_plan_fields

    ais_scope = _recover(_rw_plan())
    updated = previous.with_scope_selection(
        resource_group_ids=ais_scope.resource_group_ids,
        endpoint_ids=ais_scope.endpoint_ids,
        endpoint_capability_ids=ais_scope.endpoint_capability_ids,
    )
    reconciled = reconcile_draft_after_builder_save(previous, updated, step="scope")

    assert "businessTestData" not in reconciled.unrepresented_plan_fields


def test_explicit_null_specification_profile_is_not_defaulted() -> None:
    """A schema-invalid specification is kept rather than replaced with the derived profile."""
    plan = _rw_plan()
    plan["specification"]["profile"] = None

    draft = _recover(plan)

    assert draft.specification is None
    assert _issue(draft, "specification").kind == "invalid"
    composed: dict[str, Any] = plan_json_from_draft(draft)
    assert composed["specification"]["profile"] is None


def test_discovery_save_keeps_unrelated_security_overlay() -> None:
    """Saving discovery only takes ownership of discovery fields."""
    plan = _rw_plan()
    plan["securityEnvironment"]["mtls"] = {"enabled": "invalid", "certificatePath": "cert.pem"}
    previous = _recover(plan)

    reconciled = reconcile_draft_after_builder_save(previous, previous, step="discovery")

    assert reconciled.unrepresented_plan_fields == previous.unrepresented_plan_fields
    assert _issue(reconciled, "securityEnvironment.mtls.enabled").kind == "invalid"


def test_redact_sensitive_plan_json_blanks_any_value_under_sensitive_keys() -> None:
    """Unvalidated overlay secrets are blanked whatever their JSON type."""
    redacted = redact_sensitive_plan_json(
        {
            "securityEnvironment": {
                "signingPrivateKeyPem": {"contents": "SECRET"},
                "accessToken": ["SECRET"],
                "discoveryUrl": "https://example.com",
            },
            "inputs": {"accessToken": {"value": "SECRET", "sensitive": True}},
        }
    )

    assert redacted == {
        "securityEnvironment": {"signingPrivateKeyPem": "", "accessToken": "", "discoveryUrl": "https://example.com"},
        "inputs": {"accessToken": {"value": "", "sensitive": True}},
    }
