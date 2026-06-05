"""Unit tests for participant plan-builder form helpers."""

from __future__ import annotations

import json
from typing import cast

import pytest

from conformance.api.plan_builder import PlanBuilderForm, PlanPreview
from conformance.json_types import JsonValue

VALID_CONFIG: dict[str, JsonValue] = {
    "environment": "test-env",
    "discoveryUrl": "https://example.com/.well-known/openid-configuration",
}

SUITE_CONFIG: dict[str, JsonValue] = {
    **VALID_CONFIG,
    "testSuite": {
        "standard": "ob-read-write",
        "specVersion": "v4.0",
        "profile": "fapi1-advanced",
        "suite": "discovery-jwks",
    },
}

PSU_AUTH_STARTER_CONFIG: dict[str, JsonValue] = {
    **VALID_CONFIG,
    "testSuite": {
        "standard": "ob-read-write",
        "specVersion": "v4.0",
        "profile": "fapi1-advanced",
        "suite": "psu-auth-starter",
    },
    "oauth": {
        "clientId": "test-client-id",
        "redirectUri": "https://conformance.example.com/callback",
    },
}

AIS_SLICE_CONFIG: dict[str, JsonValue] = {
    **VALID_CONFIG,
    "testSuite": {
        "standard": "ob-read-write",
        "specVersion": "v4.0",
        "profile": "fapi1-advanced",
        "suite": "ais-certification-slice",
    },
    "oauth": {
        "clientId": "test-client-id",
        "redirectUri": "https://conformance.example.com/callback",
        "resourceBaseUrl": "https://resource.example.com",
    },
}


def _http_step(
    step_id: str,
    *,
    mandatory: bool = False,
    optional: bool = False,
    phase: str = "execution",
    group: str = "default",
) -> dict[str, JsonValue]:
    """Build a minimal v1 HTTP step for plan-builder tests.

    Args:
        step_id: Stable manifest step id.
        mandatory: Whether the step is certification mandatory.
        optional: Whether the step is opt-in optional.
        phase: Scheduling phase declared by the manifest step.
        group: Execution group declared by the manifest step.

    Returns:
        A JSON object representing a valid v1 HTTP step.
    """
    step: dict[str, JsonValue] = {
        "id": step_id,
        "name": f"Step {step_id}",
        "request": {"method": "GET", "url": f"https://example.com/{step_id}"},
        "assertions": [{"type": "http_status", "expected": 200}],
    }
    if mandatory:
        step["mandatory"] = True
    if optional:
        step["optional"] = True
    step["phase"] = phase
    step["group"] = group
    return step


def _v1_manifest(steps: list[dict[str, JsonValue]]) -> dict[str, JsonValue]:
    """Build a v1 manifest for plan-builder tests.

    Args:
        steps: Step JSON objects to include in the manifest.

    Returns:
        A JSON object representing a v1 manifest.
    """
    return {"schemaVersion": "v1", "name": "Plan builder manifest", "steps": cast(list[JsonValue], steps)}


def _manual_psu_step(step_id: str) -> dict[str, JsonValue]:
    """Build a manual PSU authorisation step for plan-builder tests.

    Args:
        step_id: Stable manifest step id.

    Returns:
        A JSON object representing a valid manual PSU authorisation step.
    """
    return {
        "kind": "psu-authorization",
        "id": step_id,
        "name": "Manual PSU authorisation",
        "mode": "manual",
        "authorizationEndpoint": "https://auth.example.com/authorize",
        "clientId": "client-123",
        "redirectUri": "https://conformance.example.com/callback",
        "phase": "setup",
        "group": "consent",
        "mandatory": True,
    }


def _bound_form(
    manifest: dict[str, JsonValue],
    *,
    selection_mode: str = "deselect",
    selected_step_ids: list[str] | None = None,
    deselect_step_ids: list[str] | None = None,
) -> PlanBuilderForm:
    """Bind a plan-builder form with valid config and the given manifest.

    Args:
        manifest: Manifest JSON object to submit.
        selection_mode: Selection mode submitted by the browser form.
        selected_step_ids: Step ids submitted as selected when using select mode.
        deselect_step_ids: Step ids submitted as deselected when using deselect mode.

    Returns:
        A bound ``PlanBuilderForm`` ready for validation.
    """
    return PlanBuilderForm(
        data={
            "config_json": json.dumps(VALID_CONFIG),
            "manifest_json": json.dumps(manifest),
            "selection_mode": selection_mode,
            "selected_step_ids": selected_step_ids or [],
            "deselect_step_ids": deselect_step_ids or [],
        }
    )


def _validated_preview(form: PlanBuilderForm) -> PlanPreview:
    """Validate a form and return its typed preview.

    Args:
        form: Bound plan-builder form to validate.

    Returns:
        The form's typed plan preview.
    """
    assert form.is_valid(), form.errors.as_json()
    assert form.preview is not None
    return form.preview


@pytest.mark.unit
def test_valid_v1_preview_builds_step_rows_and_allows_optional_opt_in() -> None:
    manifest = _v1_manifest(
        [
            _http_step("mandatory", mandatory=True),
            _http_step("standard"),
            _http_step("optional", optional=True),
        ]
    )
    form = _bound_form(
        manifest,
        selection_mode="select",
        selected_step_ids=["mandatory", "standard", "optional"],
    )

    preview = _validated_preview(form)

    assert preview.config.environment == "test-env"
    assert preview.manifest.name == "Plan builder manifest"
    assert preview.launch_supported is True
    assert [(row.id, row.name, row.kind, row.phase, row.group) for row in preview.rows] == [
        ("mandatory", "Step mandatory", "http", "execution", "default"),
        ("standard", "Step standard", "http", "execution", "default"),
        ("optional", "Step optional", "http", "execution", "default"),
    ]
    optional_row = preview.rows[2]
    assert optional_row.default_selected is False
    assert optional_row.selected_after_form is True


@pytest.mark.unit
def test_blank_manifest_resolves_config_selected_suite() -> None:
    """A blank manifest textarea can preview the suite selected by config."""
    form = PlanBuilderForm(
        data={
            "config_json": json.dumps(SUITE_CONFIG),
            "manifest_json": "",
            "selection_mode": "select",
            "selected_step_ids": ["openid-discovery"],
        }
    )

    preview = _validated_preview(form)

    assert preview.suite_metadata is not None
    assert preview.suite_metadata.catalog_id == "ob-read-write/v4.0/fapi1-advanced/discovery-jwks"
    assert preview.manifest.name == "Open Banking Read/Write v4.0 FAPI 1 Advanced discovery/JWKS smoke suite"
    assert preview.selected_plan.selected_step_ids() == ["openid-discovery"]


@pytest.mark.unit
def test_blank_manifest_resolves_psu_auth_starter_suite() -> None:
    """A blank manifest with a ``psu-auth-starter`` testSuite resolves the PSU auth starter catalog entry."""
    form = PlanBuilderForm(
        data={
            "config_json": json.dumps(PSU_AUTH_STARTER_CONFIG),
            "manifest_json": "",
            "selection_mode": "select",
            "selected_step_ids": ["openid-discovery", "jwks-fetch", "psu-authorization"],
        }
    )

    preview = _validated_preview(form)

    assert preview.suite_metadata is not None
    assert preview.suite_metadata.catalog_id == "ob-read-write/v4.0/fapi1-advanced/psu-auth-starter"
    assert preview.manifest.name == "Open Banking Read/Write v4.0 FAPI 1 Advanced PSU auth starter suite"
    assert preview.selected_plan.selected_step_ids() == ["openid-discovery", "jwks-fetch", "psu-authorization"]


@pytest.mark.unit
def test_blank_manifest_resolves_ais_slice_suite() -> None:
    """A blank manifest with an AIS ``testSuite`` resolves the AIS catalog entry."""
    form = PlanBuilderForm(
        data={
            "config_json": json.dumps(AIS_SLICE_CONFIG),
            "manifest_json": "",
            "selection_mode": "select",
            "selected_step_ids": [
                "openid-discovery",
                "jwks-fetch",
                "psu-authorization",
                "token-exchange",
                "account-access-consent",
                "accounts-list",
            ],
        }
    )

    preview = _validated_preview(form)

    assert preview.suite_metadata is not None
    assert preview.suite_metadata.catalog_id == "ob-read-write/v4.0/fapi1-advanced/ais-certification-slice"
    assert preview.manifest.name == "Open Banking Read/Write v4.0 FAPI 1 Advanced AIS certification slice"
    assert preview.selected_plan.selected_step_ids() == [
        "openid-discovery",
        "jwks-fetch",
        "psu-authorization",
        "token-exchange",
        "account-access-consent",
        "accounts-list",
    ]
    assert preview.launch_supported is True


@pytest.mark.unit
def test_explicit_manifest_overrides_config_selected_suite() -> None:
    """Pasted manifest JSON remains the plan-builder authoring override."""
    explicit_manifest = _v1_manifest([_http_step("explicit")])
    form = PlanBuilderForm(
        data={
            "config_json": json.dumps(SUITE_CONFIG),
            "manifest_json": json.dumps(explicit_manifest),
            "selection_mode": "select",
            "selected_step_ids": ["explicit"],
        }
    )

    preview = _validated_preview(form)

    assert preview.suite_metadata is None
    assert preview.manifest.name == "Plan builder manifest"
    assert preview.selected_plan.selected_step_ids() == ["explicit"]


@pytest.mark.unit
def test_blank_manifest_without_config_suite_returns_form_error() -> None:
    """A blank manifest still needs ``config.testSuite`` to be previewable."""
    form = PlanBuilderForm(
        data={
            "config_json": json.dumps(VALID_CONFIG),
            "manifest_json": "",
        }
    )

    assert form.is_valid() is False
    assert "required unless config.testSuite" in form.errors["manifest_json"][0]


@pytest.mark.unit
def test_config_suite_resolution_error_returns_form_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Catalog resolution failures are shown as validation errors.

    Args:
        monkeypatch: Pytest fixture used to replace suite resolution.
    """
    from conformance.suite_catalog import SuiteCatalogError

    def fail_resolve_suite(_selection: object) -> object:
        """Raise a catalog error for form validation.

        Args:
            _selection: Suite selection supplied by the validated config.

        Raises:
            SuiteCatalogError: Always raised to exercise form error handling.
        """
        raise SuiteCatalogError("missing suite")

    monkeypatch.setattr("conformance.api.plan_builder.resolve_suite", fail_resolve_suite)
    form = PlanBuilderForm(
        data={
            "config_json": json.dumps(SUITE_CONFIG),
            "manifest_json": "",
        }
    )

    assert form.is_valid() is False
    assert "Suite resolution failed" in form.non_field_errors()[0]


@pytest.mark.unit
def test_optional_steps_are_deselected_by_default() -> None:
    form = _bound_form(_v1_manifest([_http_step("mandatory", mandatory=True), _http_step("optional", optional=True)]))

    preview = _validated_preview(form)

    assert preview.selected_plan.selected_step_ids() == ["mandatory"]
    optional_row = preview.rows[1]
    assert optional_row.optional is True
    assert optional_row.default_selected is False
    assert optional_row.selected_after_form is False


@pytest.mark.unit
def test_mandatory_deselection_sets_certification_impact_flags() -> None:
    form = _bound_form(
        _v1_manifest([_http_step("mandatory", mandatory=True), _http_step("standard")]),
        deselect_step_ids=["mandatory"],
    )

    preview = _validated_preview(form)

    mandatory_row = preview.rows[0]
    assert mandatory_row.mandatory is True
    assert mandatory_row.certification_required is True
    assert mandatory_row.deselection_impacts_certification is True
    assert mandatory_row.certification_blocked_by_deselection is True
    assert preview.certification_eligible_by_selection is False
    assert preview.selected_plan.deselected_mandatory_step_ids() == ["mandatory"]


@pytest.mark.unit
def test_invalid_config_json_returns_form_error() -> None:
    form = PlanBuilderForm(
        data={
            "config_json": '{"environment":',
            "manifest_json": json.dumps(_v1_manifest([_http_step("standard")])),
        }
    )

    assert form.is_valid() is False
    assert "Config JSON must be valid JSON" in form.errors["config_json"][0]


@pytest.mark.unit
def test_invalid_manifest_returns_form_error() -> None:
    form = _bound_form({"schemaVersion": "v1", "name": "Broken", "steps": []})

    assert form.is_valid() is False
    assert "Manifest validation failed" in form.errors["manifest_json"][0]


@pytest.mark.unit
def test_v0_manifest_is_rejected_for_selectable_plan_builder() -> None:
    form = _bound_form(
        {
            "schemaVersion": "v0",
            "name": "Legacy manifest",
            "tests": [
                {
                    "id": "legacy",
                    "name": "Legacy",
                    "request": {"method": "GET", "url": "https://example.com/legacy"},
                    "assertions": [{"type": "http_status", "expected": 200}],
                }
            ],
        }
    )

    assert form.is_valid() is False
    assert "supports v1 manifests only" in form.errors["manifest_json"][0]


@pytest.mark.unit
def test_manual_psu_step_previews_and_allows_browser_launch() -> None:
    form = _bound_form(_v1_manifest([_manual_psu_step("psu"), _http_step("token")]))

    preview = _validated_preview(form)

    assert preview.rows[0].kind == "psu-authorization"
    assert preview.rows[0].phase == "setup"
    assert preview.rows[0].group == "consent"
    assert preview.rows[0].selected_after_form is True
    assert preview.launch_supported is True
    assert preview.launch_blockers == ()


@pytest.mark.unit
def test_ais_suite_preview_marks_mandatory_selection_and_launch_support() -> None:
    """AIS suite previews remain browser-launchable and flag deselected mandatory steps."""
    form = PlanBuilderForm(
        data={
            "config_json": json.dumps(AIS_SLICE_CONFIG),
            "manifest_json": "",
            "selection_mode": "deselect",
            "deselect_step_ids": ["accounts-list"],
        }
    )

    preview = _validated_preview(form)

    assert preview.launch_supported is True
    assert preview.launch_blockers == ()
    assert preview.certification_eligible_by_selection is False
    assert preview.selected_plan.deselected_mandatory_step_ids() == ["accounts-list"]
