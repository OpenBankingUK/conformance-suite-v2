"""Unit tests for catalogue-backed participant plan-builder form helpers."""

from __future__ import annotations

import json
from typing import cast

import pytest

from conformance.api.plan_builder import CatalogueEndpointOption, PlanBuilderForm, PlanPreview, guided_flow_context
from conformance.json_types import JsonValue

VALID_CONFIG: dict[str, JsonValue] = {
    "environment": "test-env",
    "discoveryUrl": "https://example.com/.well-known/openid-configuration",
}
"""Minimal runtime config accepted by the plan-builder form."""


def _endpoint_id_for(*, api: str, path: str) -> str:
    """Return the rendered endpoint option id for a catalogue path.

    Args:
        api: API family for the option.
        path: Standards endpoint path.

    Returns:
        Stable browser form id for the endpoint.

    Raises:
        AssertionError: If the endpoint is not rendered by the guided context.
    """
    context = guided_flow_context(PlanBuilderForm())
    for option in cast(tuple[CatalogueEndpointOption, ...], context["guided_endpoint_options"]):
        if option.api == api and option.path == path:
            return option.id
    raise AssertionError(f"Endpoint option not found for {api} {path}")


def _ais_accounts_endpoint_id() -> str:
    """Return the AIS accounts-list endpoint id.

    Returns:
        Endpoint id for ``GET /open-banking/v4.0/aisp/accounts``.
    """
    return _endpoint_id_for(api="ais", path="/open-banking/v4.0/aisp/accounts")


def _bound_guided_form(
    *,
    endpoint_ids: list[str] | None = None,
    runtime_inputs: dict[str, str] | None = None,
) -> PlanBuilderForm:
    """Build a guided AIS plan-builder form submission.

    Args:
        endpoint_ids: Implemented endpoint ids to submit.
        runtime_inputs: Runtime input id/value strings to submit.

    Returns:
        Bound ``PlanBuilderForm`` ready for validation.
    """
    data: dict[str, object] = {
        "config_json": json.dumps(VALID_CONFIG),
        "plan_spec_json": "",
        "guided_standard": "open-banking",
        "guided_spec_version": "v4.0",
        "guided_api": "ais",
        "guided_security_profile": "fapi1-advanced",
        "implemented_endpoint_ids": endpoint_ids or [],
    }
    for input_id, value in (runtime_inputs or {}).items():
        data[f"runtime_input__{input_id}"] = value
    return PlanBuilderForm(data=data)


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
def test_guided_endpoint_selection_compiles_catalogue_plan() -> None:
    """Endpoint selections generate a compiled catalogue plan rather than a manifest step plan."""
    form = _bound_guided_form(
        endpoint_ids=[_ais_accounts_endpoint_id()],
        runtime_inputs={
            "resourceBaseUrl": "https://resource.example.com",
            "accessToken": "secret-access-token",
        },
    )

    preview = _validated_preview(form)

    assert preview.config.environment == "test-env"
    assert preview.plan_spec.catalogue_key.api == "ais"
    assert [endpoint.path for endpoint in preview.plan_spec.implemented_endpoints] == [
        "/open-banking/v4.0/aisp/accounts"
    ]
    assert preview.compiled_plan.traceability.generated_test_case_ids == (
        "ais-at-setup-discovery",
        "ais-at-setup-consent",
        "ais-at-setup-token",
        "ais-at-accounts-list-200",
        "ais-at-accounts-list-401",
        "ais-at-invalid-base-endpoint-404",
    )
    assert preview.launch_supported is True
    assert preview.certification_eligible_by_selection is True


@pytest.mark.unit
def test_guided_endpoint_selection_exposes_runtime_prompts() -> None:
    """Runtime prompts are derived from the selected endpoints' catalogue requirements."""
    form = _bound_guided_form(endpoint_ids=[_ais_accounts_endpoint_id()])

    assert form.is_valid() is False
    assert "Required runtime input 'resourceBaseUrl' is missing" in form.non_field_errors()[0]
    assert [(prompt.input_id, prompt.label, prompt.required) for prompt in form.runtime_input_prompts] == [
        ("resourceBaseUrl", "AIS resource server base URL", True),
        ("accessToken", "AIS access token", True),
        ("invalidAccessToken", "Invalid AIS access token for unauthorized checks", False),
    ]


@pytest.mark.unit
def test_guided_form_without_endpoint_previews_non_launchable_empty_plan() -> None:
    """Blank endpoint selection previews successfully but blocks launch."""
    form = _bound_guided_form()

    preview = _validated_preview(form)

    assert preview.compiled_plan.test_cases == ()
    assert preview.launch_supported is False
    assert preview.launch_blockers == ("Select at least one implemented endpoint before launch.",)
    assert preview.generated_plan_spec_json


@pytest.mark.unit
def test_plan_spec_json_import_overrides_guided_endpoint_fields() -> None:
    """JSON mode can import a first-class plan spec without manifest or suite fields."""
    raw_plan_spec: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "catalogue": {"standard": "open-banking", "version": "v4.0", "api": "ais"},
        "securityProfile": "fapi1-advanced",
        "implementedEndpoints": [
            {
                "method": "GET",
                "path": "/open-banking/v4.0/aisp/accounts",
                "resourceGroup": "Accounts",
            }
        ],
        "runtimeInputs": {
            "resourceBaseUrl": "https://resource.example.com",
            "accessToken": "secret-access-token",
        },
    }
    form = PlanBuilderForm(
        data={
            "config_json": json.dumps(VALID_CONFIG),
            "plan_spec_json": json.dumps(raw_plan_spec),
            "guided_standard": "open-banking",
            "guided_spec_version": "v4.0",
            "guided_api": "ais",
            "implemented_endpoint_ids": [],
        }
    )

    preview = _validated_preview(form)

    assert [endpoint.path for endpoint in preview.plan_spec.implemented_endpoints] == [
        "/open-banking/v4.0/aisp/accounts"
    ]
    assert '"implementedEndpoints"' in preview.generated_plan_spec_json
    assert "secret-access-token" not in preview.generated_plan_spec_json
    assert "resource.example.com" in preview.generated_plan_spec_json


@pytest.mark.unit
def test_config_testsuite_is_rejected_by_plan_builder() -> None:
    """The browser plan builder no longer accepts public config.testSuite."""
    form = _bound_guided_form(
        endpoint_ids=[_ais_accounts_endpoint_id()],
        runtime_inputs={
            "resourceBaseUrl": "https://resource.example.com",
            "accessToken": "secret-access-token",
        },
    )
    raw_config = {
        **VALID_CONFIG,
        "testSuite": {
            "standard": "ob-read-write",
            "specVersion": "v4.0",
            "profile": "fapi1-advanced",
            "suite": "removed-suite",
        },
    }
    mutable_data = dict(form.data)
    mutable_data["config_json"] = json.dumps(raw_config)
    rejected = PlanBuilderForm(data=mutable_data)

    assert rejected.is_valid() is False
    assert "Unknown config field(s): testSuite" in rejected.errors["config_json"][0]
