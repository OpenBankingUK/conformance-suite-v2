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
        "openBankingIntentId": "consent-plan-123",
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

AIS_BASELINE_CONFIG: dict[str, JsonValue] = {
    **VALID_CONFIG,
    "testSuite": {
        "standard": "ob-read-write",
        "specVersion": "v4.0",
        "profile": "fapi1-advanced",
        "suite": "ais-certification-baseline",
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
                "client-credentials-token",
                "account-access-consent",
                "psu-authorization",
                "token-exchange",
                "accounts-list",
                "account-balances",
                "account-transactions",
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
        "client-credentials-token",
        "account-access-consent",
        "psu-authorization",
        "token-exchange",
        "accounts-list",
        "account-balances",
        "account-transactions",
    ]
    assert preview.launch_supported is True


@pytest.mark.unit
def test_guided_fields_can_build_config_selected_suite_without_config_json() -> None:
    """Structured guided fields can generate config for suite-backed previews."""
    form = PlanBuilderForm(
        data={
            "config_json": "",
            "manifest_json": "",
            "guided_environment": "guided-env",
            "guided_discovery_url": "https://example.com/.well-known/openid-configuration",
            "guided_spec_version": "v4.0.1",
            "guided_api": "ais",
            "guided_suite": "ais-certification-slice",
            "guided_client_id": "guided-client",
            "guided_redirect_uri": "https://conformance.example.com/callback",
            "guided_resource_base_url": "https://resource.example.com",
            "selection_mode": "select",
            "selected_step_ids": [
                "openid-discovery",
                "jwks-fetch",
                "client-credentials-token",
                "account-access-consent",
                "psu-authorization",
                "token-exchange",
                "accounts-list",
                "account-balances",
                "account-transactions",
            ],
        }
    )

    preview = _validated_preview(form)

    assert preview.config.environment == "guided-env"
    assert preview.config.discovery_url == "https://example.com/.well-known/openid-configuration"
    assert preview.config.test_suite is not None
    assert preview.config.test_suite.spec_version == "v4.0.1"
    assert preview.config.test_suite.api == "ais"
    assert preview.config.test_suite.suite == "ais-certification-slice"
    assert preview.suite_metadata is not None
    assert preview.suite_metadata.catalog_id == "ob-read-write/v4.0.1/fapi1-advanced/ais-certification-slice"
    assert form.generated_config_json is not None
    assert '"specVersion": "v4.0.1"' in form.generated_config_json


@pytest.mark.unit
def test_guided_model_bank_example_populates_environment_and_discovery_when_blank() -> None:
    """A guided model-bank example can supply environment and discovery values."""
    form = PlanBuilderForm(
        data={
            "config_json": "",
            "manifest_json": "",
            "guided_model_bank": "ozone-obie-preprod",
            "guided_spec_version": "v4.0",
            "guided_api": "ais",
            "guided_suite": "discovery-jwks",
            "selection_mode": "select",
            "selected_step_ids": ["openid-discovery"],
        }
    )

    preview = _validated_preview(form)

    assert preview.config.environment == "ozone-model-bank"
    assert preview.config.discovery_url == "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration"
    assert preview.config.test_suite is not None
    assert preview.config.test_suite.suite == "discovery-jwks"
    assert form.generated_config_json is not None
    assert '"environment": "ozone-model-bank"' in form.generated_config_json


@pytest.mark.unit
def test_guided_model_bank_example_allows_custom_environment_override() -> None:
    """Custom guided environment and discovery values take precedence over an example."""
    form = PlanBuilderForm(
        data={
            "config_json": "",
            "manifest_json": json.dumps(_v1_manifest([_http_step("guided-custom")])),
            "guided_model_bank": "ozone-obie-preprod",
            "guided_environment": "custom-env",
            "guided_discovery_url": "https://custom.example.com/.well-known/openid-configuration",
            "selection_mode": "select",
            "selected_step_ids": ["guided-custom"],
        }
    )

    preview = _validated_preview(form)

    assert preview.config.environment == "custom-env"
    assert preview.config.discovery_url == "https://custom.example.com/.well-known/openid-configuration"
    assert form.generated_config_json is not None
    assert '"environment": "custom-env"' in form.generated_config_json


@pytest.mark.unit
def test_guided_fields_can_build_config_for_explicit_manifest_without_suite_selection() -> None:
    """Guided environment and OAuth fields still work with an explicit manifest override."""
    form = PlanBuilderForm(
        data={
            "config_json": "",
            "manifest_json": json.dumps(_v1_manifest([_http_step("guided-explicit")])),
            "guided_environment": "guided-explicit-env",
            "guided_discovery_url": "https://example.com/.well-known/openid-configuration",
            "guided_client_id": "guided-client",
            "guided_redirect_uri": "https://conformance.example.com/callback",
            "selection_mode": "select",
            "selected_step_ids": ["guided-explicit"],
        }
    )

    preview = _validated_preview(form)

    assert preview.suite_metadata is None
    assert preview.manifest.name == "Plan builder manifest"
    assert preview.config.environment == "guided-explicit-env"
    assert preview.config.oauth is not None
    assert preview.config.oauth.client_id == "guided-client"


@pytest.mark.unit
def test_blank_config_without_json_or_guided_inputs_returns_form_error() -> None:
    """The plan builder still requires either pasted config JSON or guided inputs."""
    form = PlanBuilderForm(
        data={
            "config_json": "",
            "manifest_json": json.dumps(_v1_manifest([_http_step("standard")])),
            "selection_mode": "select",
            "selected_step_ids": ["standard"],
        }
    )

    assert form.is_valid() is False
    assert "guided inputs supply a config" in form.errors["config_json"][0]


@pytest.mark.unit
def test_blank_manifest_resolves_ais_baseline_suite_with_optional_steps_deselected() -> None:
    """A blank manifest with the AIS baseline ``testSuite`` preserves opt-in optional rows."""
    form = PlanBuilderForm(
        data={
            "config_json": json.dumps(AIS_BASELINE_CONFIG),
            "manifest_json": "",
            "selection_mode": "deselect",
        }
    )

    preview = _validated_preview(form)

    mandatory_step_ids = [
        "openid-discovery",
        "jwks-fetch",
        "client-credentials-token",
        "account-access-consent",
        "psu-authorization",
        "token-exchange",
        "accounts-list",
        "account-detail",
        "account-balances",
        "account-transactions",
        "transactions-list",
    ]
    optional_step_ids = [
        "balances-list",
        "account-beneficiaries",
        "beneficiaries-list",
        "account-direct-debits",
        "direct-debits-list",
        "account-offers",
        "offers-list",
        "account-party",
        "account-parties",
        "party-list",
        "account-product",
        "products-list",
        "account-scheduled-payments",
        "scheduled-payments-list",
        "account-standing-orders",
        "standing-orders-list",
        "statements-list",
    ]

    assert preview.suite_metadata is not None
    assert preview.suite_metadata.catalog_id == "ob-read-write/v4.0/fapi1-advanced/ais-certification-baseline"
    assert preview.manifest.name == "Open Banking Read/Write v4.0 FAPI 1 Advanced AIS certification baseline"
    assert preview.selected_plan.selected_step_ids() == mandatory_step_ids
    assert [row.id for row in preview.rows] == mandatory_step_ids + optional_step_ids
    assert [row.id for row in preview.rows if row.default_selected] == mandatory_step_ids
    assert [row.id for row in preview.rows if row.optional and not row.default_selected] == optional_step_ids
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


def _auth_inventory_manifest(*, permissions: list[str]) -> dict[str, JsonValue]:
    """Build a minimal v1 manifest with consent, PSU, token, and protected steps.

    Args:
        permissions: Consent permissions written into the consent-creation request.

    Returns:
        JSON object representing a valid v1 manifest for auth-inventory tests.
    """
    return {
        "schemaVersion": "v1",
        "name": "Auth inventory manifest",
        "steps": [
            {
                "id": "openid-discovery",
                "name": "OpenID discovery",
                "request": {
                    "method": "GET",
                    "url": "https://example.com/.well-known/openid-configuration",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "mandatory": True,
            },
            {
                "id": "client-credentials-token",
                "name": "Client credentials token",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/token",
                    "body": {
                        "encoding": "form",
                        "fields": {
                            "grant_type": "client_credentials",
                            "client_id": "client-123",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "mandatory": True,
            },
            {
                "id": "account-access-consent",
                "name": "Account access consent",
                "request": {
                    "method": "POST",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/account-access-consents",
                    "headers": {
                        "Authorization": "Bearer ${steps.client-credentials-token.response.body.access_token}",
                    },
                    "body": {
                        "Data": {
                            "Permissions": cast(list[JsonValue], permissions),
                        },
                        "Risk": {},
                    },
                },
                "assertions": [{"type": "http_status", "expected": 201}],
                "mandatory": True,
            },
            {
                "kind": "psu-authorization",
                "id": "psu-authorization",
                "name": "PSU authorisation",
                "mode": "manual",
                "authorizationEndpoint": "https://example.com/authorize",
                "clientId": "client-123",
                "redirectUri": "https://conformance.example.com/callback",
                "scope": "openid accounts",
                "requestObject": {
                    "source": "fapi-signing",
                    "openbankingIntentId": "${steps.account-access-consent.response.body.Data.ConsentId}",
                },
                "mandatory": True,
            },
            {
                "id": "token-exchange",
                "name": "Token exchange",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/token",
                    "body": {
                        "encoding": "form",
                        "fields": {
                            "grant_type": "authorization_code",
                            "code": "${steps.psu-authorization.response.body.code}",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "mandatory": True,
            },
            {
                "id": "accounts-list",
                "name": "Accounts list",
                "request": {
                    "method": "GET",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/accounts",
                    "headers": {
                        "Authorization": "Bearer ${steps.token-exchange.response.body.access_token}",
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "mandatory": True,
            },
            {
                "id": "account-balances",
                "name": "Account balances",
                "request": {
                    "method": "GET",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/accounts/123/balances",
                    "headers": {
                        "Authorization": "Bearer ${steps.token-exchange.response.body.access_token}",
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "mandatory": True,
            },
            {
                "id": "account-detail",
                "name": "Account detail",
                "request": {
                    "method": "GET",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/accounts/123",
                    "headers": {
                        "Authorization": "Bearer ${steps.token-exchange.response.body.access_token}",
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "mandatory": True,
            },
        ],
    }


@pytest.mark.unit
def test_preview_auth_inventory_reuses_bundle_for_identical_effective_requirements() -> None:
    """Selected protected steps sharing one effective requirement use one auth bundle."""
    form = _bound_form(
        _auth_inventory_manifest(permissions=["ReadBalances"]),
        selection_mode="select",
        selected_step_ids=[
            "openid-discovery",
            "client-credentials-token",
            "account-access-consent",
            "psu-authorization",
            "token-exchange",
            "accounts-list",
            "account-balances",
        ],
    )

    preview = _validated_preview(form)

    assert len(preview.auth_inventory) == 1
    bundle = preview.auth_inventory[0]
    assert bundle.token_step_id == "token-exchange"  # noqa: S105 - test manifest step id literal
    assert bundle.consent_step_id == "account-access-consent"
    assert bundle.required_scopes == ("accounts", "openid")
    assert bundle.required_ob_permissions == ("ReadBalances",)
    assert bundle.consuming_step_ids == ("accounts-list", "account-balances")
    assert preview.step_auth_requirements[0].step_id == "accounts-list"
    assert preview.step_auth_requirements[1].step_id == "account-balances"
    assert preview.step_auth_requirements[0].bundle_id == bundle.id
    assert preview.step_auth_requirements[1].bundle_id == bundle.id


@pytest.mark.unit
def test_preview_auth_inventory_splits_basic_and_detail_permission_bundles() -> None:
    """Basic and Detail consuming steps are split into separate effective bundles."""
    form = _bound_form(
        _auth_inventory_manifest(permissions=["ReadAccountsBasic", "ReadAccountsDetail"]),
        selection_mode="select",
        selected_step_ids=[
            "openid-discovery",
            "client-credentials-token",
            "account-access-consent",
            "psu-authorization",
            "token-exchange",
            "accounts-list",
            "account-detail",
        ],
    )

    preview = _validated_preview(form)

    assert len(preview.auth_inventory) == 2
    bundles_by_permission = {bundle.required_ob_permissions: bundle for bundle in preview.auth_inventory}
    assert ("ReadAccountsBasic",) in bundles_by_permission
    assert ("ReadAccountsDetail",) in bundles_by_permission
    basic_bundle = bundles_by_permission[("ReadAccountsBasic",)]
    detail_bundle = bundles_by_permission[("ReadAccountsDetail",)]
    assert basic_bundle.consuming_step_ids == ("accounts-list",)
    assert detail_bundle.consuming_step_ids == ("account-detail",)

    requirement_by_step = {requirement.step_id: requirement.bundle_id for requirement in preview.step_auth_requirements}
    assert requirement_by_step["accounts-list"] == basic_bundle.id
    assert requirement_by_step["account-detail"] == detail_bundle.id
    assert basic_bundle.id != detail_bundle.id
