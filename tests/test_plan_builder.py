"""Unit tests for participant plan-builder form helpers."""

from __future__ import annotations

import json
from typing import cast

import pytest

from conformance.api.plan_builder import (
    PlanBuilderForm,
    PlanPreview,
    _infer_shape_warning,
)
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

AIS_FCS_LEGACY_BENCHMARK_CONFIG: dict[str, JsonValue] = {
    **VALID_CONFIG,
    "testSuite": {
        "standard": "ob-read-write",
        "specVersion": "v4.0",
        "profile": "fapi1-advanced",
        "suite": "ais-fcs-legacy-benchmark",
    },
    "oauth": {
        "clientId": "test-client-id",
        "redirectUri": "https://conformance.example.com/callback",
        "resourceBaseUrl": "https://resource.example.com",
    },
}

PIS_FCS_LEGACY_BENCHMARK_CONFIG: dict[str, JsonValue] = {
    **VALID_CONFIG,
    "testSuite": {
        "standard": "ob-read-write",
        "specVersion": "v4.0",
        "api": "pis",
        "profile": "fapi1-advanced",
        "suite": "pis-fcs-legacy-benchmark",
    },
    "oauth": {
        "clientId": "test-client-id",
        "redirectUri": "https://conformance.example.com/callback",
        "resourceBaseUrl": "https://resource.example.com",
    },
    "fapiSigning": {
        "certificatePathRoot": "./certs",
        "signingCertificatePath": "dummy-signing.crt",
        "signingPrivateKeyPath": "dummy-signing.key",  # pragma: allowlist secret
        "kid": "test-signing-kid",
        "clientAssertionIssuer": "test-client-id",
        "clientAssertionSubject": "test-client-id",
        "tokenEndpointAuthMethod": "private_key_jwt",
    },
    "openBanking": {
        "financialId": "test-financial-id",
    },
    "testData": {
        "values": {
            "scheduledPaymentDateTime": "2026-07-17T10:00:00+00:00",
            "frequency": "EvryDay",
            "firstPaymentDateTime": "2026-07-17T10:00:00+00:00",
            "finalPaymentDateTime": "2026-08-17T10:00:00+00:00",
        },
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
def test_preview_includes_run_plan_hash_and_json_export() -> None:
    """Plan previews expose a hashed Run Plan snapshot and JSON export payload."""
    manifest = _v1_manifest([_http_step("mandatory", mandatory=True), _http_step("optional", optional=True)])
    preview = _validated_preview(_bound_form(manifest, selection_mode="select", selected_step_ids=["mandatory"]))

    assert preview.run_plan.suite.manifest_hash.startswith("sha256:")
    assert preview.run_plan.suite.manifest_hash != "sha256:"
    assert preview.run_plan_json
    assert '"schemaVersion": "1"' in preview.run_plan_json
    assert preview.legacy_test_values_warning is False


@pytest.mark.unit
def test_preview_marks_legacy_test_values_warning_when_config_contains_test_values() -> None:
    """Legacy config.testValues input is flagged for Run Plan migration messaging."""
    manifest = _v1_manifest([_http_step("mandatory", mandatory=True)])
    form = PlanBuilderForm(
        data={
            "config_json": json.dumps(
                {
                    **VALID_CONFIG,
                    "testValues": {
                        "profile": "sandbox",
                        "overrides": {"paymentId": "pid-123"},
                    },
                }
            ),
            "manifest_json": json.dumps(manifest),
            "selection_mode": "deselect",
            "selected_step_ids": [],
            "deselect_step_ids": [],
        }
    )
    preview = _validated_preview(form)

    assert preview.legacy_test_values_warning is True
    assert preview.run_plan.test_values.profile == "sandbox"
    assert preview.run_plan.test_values.custom_values["paymentId"] == "pid-123"


@pytest.mark.unit
def test_select_mode_with_mandatory_only_ids_deselects_non_mandatory_rows() -> None:
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
        selected_step_ids=["mandatory"],
    )

    preview = _validated_preview(form)

    assert preview.selected_plan.selected_step_ids() == ["mandatory"]
    assert [row.selected_after_form for row in preview.rows] == [True, False, False]
    assert preview.certification_eligible_by_selection is True


@pytest.mark.unit
def test_select_mode_with_empty_selection_deselects_all_rows() -> None:
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
        selected_step_ids=[],
    )

    preview = _validated_preview(form)

    assert preview.selected_plan.selected_step_ids() == []
    assert [row.selected_after_form for row in preview.rows] == [False, False, False]
    assert preview.selected_plan.deselected_mandatory_step_ids() == ["mandatory"]
    assert preview.certification_eligible_by_selection is False


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


# ---------------------------------------------------------------------------
# Explicit authMetadata tests
# ---------------------------------------------------------------------------


def _explicit_auth_metadata_manifest(
    *,
    selected_step_ids_in_reqs: list[str] | None = None,
) -> dict[str, JsonValue]:
    """Build a v1 manifest with explicit ``authMetadata`` for plan-builder tests.

    The manifest contains a client-credentials token step, a consent step, PSU
    step, authorization-code token step, and two protected resource steps.  The
    ``authMetadata`` section declares one bundle covering both protected steps,
    using the declared step ids directly rather than heuristic name matching.

    Args:
        selected_step_ids_in_reqs: Optional override for the step ids listed in
            ``authMetadata.stepRequirements``.  Defaults to both resource steps.

    Returns:
        JSON object representing a v1 manifest with an ``authMetadata`` section.
    """
    reqs_step_ids: list[str] = selected_step_ids_in_reqs or ["resource-a", "resource-b"]
    step_requirements: list[JsonValue] = [{"stepId": step_id, "bundleId": "ais-bundle"} for step_id in reqs_step_ids]
    return {
        "schemaVersion": "v1",
        "name": "Explicit metadata manifest",
        "steps": cast(
            list[JsonValue],
            [
                {
                    "id": "cc-token",
                    "name": "Client credentials token",
                    "request": {
                        "method": "POST",
                        "url": "https://auth.example.com/token",
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
                    "id": "consent-step",
                    "name": "Create consent",
                    "request": {
                        "method": "POST",
                        "url": "https://resource.example.com/open-banking/v4.0/aisp/account-access-consents",
                        "headers": {
                            "Authorization": "Bearer ${steps.cc-token.response.body.access_token}",
                        },
                        "body": {"Data": {"Permissions": ["ReadAccountsBasic"]}, "Risk": {}},
                    },
                    "assertions": [{"type": "http_status", "expected": 201}],
                    "mandatory": True,
                },
                {
                    "kind": "psu-authorization",
                    "id": "psu-step",
                    "name": "PSU authorisation",
                    "mode": "manual",
                    "authorizationEndpoint": "https://auth.example.com/authorize",
                    "clientId": "client-123",
                    "redirectUri": "https://conformance.example.com/callback",
                    "scope": "openid accounts",
                    "requestObject": {
                        "source": "fapi-signing",
                        "openbankingIntentId": "${steps.consent-step.response.body.Data.ConsentId}",
                    },
                    "mandatory": True,
                },
                {
                    "id": "auth-token",
                    "name": "Authorization code token",
                    "request": {
                        "method": "POST",
                        "url": "https://auth.example.com/token",
                        "body": {
                            "encoding": "form",
                            "fields": {
                                "grant_type": "authorization_code",
                                "code": "${steps.psu-step.response.body.code}",
                            },
                        },
                    },
                    "assertions": [{"type": "http_status", "expected": 200}],
                    "mandatory": True,
                },
                {
                    "id": "resource-a",
                    "name": "Get resource A",
                    "request": {
                        "method": "GET",
                        "url": "https://resource.example.com/open-banking/v4.0/aisp/accounts",
                        "headers": {
                            "Authorization": "Bearer ${steps.auth-token.response.body.access_token}",
                        },
                    },
                    "assertions": [{"type": "http_status", "expected": 200}],
                    "mandatory": True,
                },
                {
                    "id": "resource-b",
                    "name": "Get resource B",
                    "request": {
                        "method": "GET",
                        "url": "https://resource.example.com/open-banking/v4.0/aisp/accounts/123",
                        "headers": {
                            "Authorization": "Bearer ${steps.auth-token.response.body.access_token}",
                        },
                    },
                    "assertions": [{"type": "http_status", "expected": 200}],
                    "mandatory": True,
                },
            ],
        ),
        "authMetadata": {
            "bundles": [
                {
                    "id": "ais-bundle",
                    "tokenStepId": "auth-token",
                    "consentStepId": "consent-step",
                    "psuStepId": "psu-step",
                    "requiredScopes": ["openid", "accounts"],
                    "requiredObPermissions": ["ReadAccountsBasic"],
                    "consumingStepIds": ["resource-a", "resource-b"],
                }
            ],
            "stepRequirements": step_requirements,
        },
    }


@pytest.mark.unit
def test_explicit_auth_metadata_uses_declared_bundle_id_not_heuristics() -> None:
    """Explicit authMetadata path uses declared bundle id without Basic/Detail heuristics."""
    form = _bound_form(
        _explicit_auth_metadata_manifest(),
        selection_mode="select",
        selected_step_ids=["cc-token", "consent-step", "psu-step", "auth-token", "resource-a", "resource-b"],
    )

    preview = _validated_preview(form)

    assert len(preview.auth_inventory) == 1
    bundle = preview.auth_inventory[0]
    assert bundle.id == "ais-bundle"
    assert bundle.token_step_id == "auth-token"  # noqa: S105 - manifest step id literal
    assert bundle.consent_step_id == "consent-step"
    assert bundle.required_scopes == ("openid", "accounts")
    assert bundle.required_ob_permissions == ("ReadAccountsBasic",)
    assert bundle.consuming_step_ids == ("resource-a", "resource-b")

    step_req_ids = {req.step_id for req in preview.step_auth_requirements}
    assert step_req_ids == {"resource-a", "resource-b"}
    for req in preview.step_auth_requirements:
        assert req.bundle_id == "ais-bundle"


@pytest.mark.unit
def test_explicit_auth_metadata_filters_deselected_steps_from_bundle() -> None:
    """When a consuming step is deselected, the bundle omits it and step requirements are filtered."""
    form = _bound_form(
        _explicit_auth_metadata_manifest(),
        selection_mode="deselect",
        deselect_step_ids=["resource-b"],
    )

    preview = _validated_preview(form)

    assert len(preview.auth_inventory) == 1
    bundle = preview.auth_inventory[0]
    assert bundle.id == "ais-bundle"
    assert bundle.consuming_step_ids == ("resource-a",)

    step_req_ids = {req.step_id for req in preview.step_auth_requirements}
    assert step_req_ids == {"resource-a"}
    assert "resource-b" not in step_req_ids


@pytest.mark.unit
def test_explicit_auth_metadata_excludes_bundle_when_all_consuming_steps_deselected() -> None:
    """A bundle with all consuming steps deselected is excluded from auth_inventory."""
    form = _bound_form(
        _explicit_auth_metadata_manifest(),
        selection_mode="deselect",
        deselect_step_ids=["resource-a", "resource-b"],
    )

    preview = _validated_preview(form)

    assert preview.auth_inventory == ()
    assert preview.step_auth_requirements == ()


@pytest.mark.unit
def test_fallback_heuristic_used_when_no_explicit_auth_metadata() -> None:
    """Manifests without authMetadata continue to use the heuristic inventory builder."""
    # Use _auth_inventory_manifest which has no authMetadata section
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

    # Heuristic path uses sha256-based bundle id, not a declared name
    assert len(preview.auth_inventory) == 1
    bundle = preview.auth_inventory[0]
    assert bundle.id.startswith("auth-token-exchange-")
    assert bundle.token_step_id == "token-exchange"  # noqa: S105 - manifest step id
    assert bundle.required_ob_permissions == ("ReadBalances",)


@pytest.mark.unit
def test_explicit_auth_metadata_no_name_heuristic_applied() -> None:
    """Explicit metadata produces no Basic/Detail split — heuristic is suppressed."""
    # Build a manifest with explicit authMetadata naming two steps with "list" and "detail"
    # in their names; in the heuristic path these would be split into different bundles.
    manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "No heuristic split manifest",
        "steps": cast(
            list[JsonValue],
            [
                {
                    "id": "token",
                    "name": "Token exchange",
                    "request": {
                        "method": "POST",
                        "url": "https://auth.example.com/token",
                        "body": {
                            "encoding": "form",
                            "fields": {"grant_type": "client_credentials"},
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
                        "url": "https://resource.example.com/accounts",
                        "headers": {
                            "Authorization": "Bearer ${steps.token.response.body.access_token}",
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
                        "url": "https://resource.example.com/accounts/123",
                        "headers": {
                            "Authorization": "Bearer ${steps.token.response.body.access_token}",
                        },
                    },
                    "assertions": [{"type": "http_status", "expected": 200}],
                    "mandatory": True,
                },
            ],
        ),
        "authMetadata": {
            "bundles": [
                {
                    "id": "single-bundle",
                    "tokenStepId": "token",
                    "requiredScopes": ["accounts"],
                    "consumingStepIds": ["accounts-list", "account-detail"],
                }
            ],
            "stepRequirements": [
                {"stepId": "accounts-list", "bundleId": "single-bundle"},
                {"stepId": "account-detail", "bundleId": "single-bundle"},
            ],
        },
    }

    form = _bound_form(
        manifest,
        selection_mode="select",
        selected_step_ids=["token", "accounts-list", "account-detail"],
    )
    preview = _validated_preview(form)

    # Explicit metadata: only one bundle despite list/detail names
    assert len(preview.auth_inventory) == 1
    assert preview.auth_inventory[0].id == "single-bundle"
    assert set(preview.auth_inventory[0].consuming_step_ids) == {"accounts-list", "account-detail"}


@pytest.mark.unit
def test_preview_has_empty_capability_warnings_for_explicit_manifest() -> None:
    """Explicit manifest (no suite_metadata) produces no capability warnings."""
    form = _bound_form(_v1_manifest([_http_step("step-a", mandatory=True)]))

    preview = _validated_preview(form)

    assert preview.capability_warnings == ()


@pytest.mark.unit
def test_build_plan_preview_tree_nodes_empty_without_suite_metadata() -> None:
    """build_plan_preview returns empty tree_nodes when suite_metadata is None."""
    from conformance.openapi_plan_metadata import StepTreeNode

    manifest = _v1_manifest([_http_step("single")])
    preview = _validated_preview(_bound_form(manifest))

    assert preview.suite_metadata is None
    assert isinstance(preview.tree_nodes, tuple)
    assert all(isinstance(node, StepTreeNode) for node in preview.tree_nodes)


# ---------------------------------------------------------------------------
# Conditional plan row tests
# ---------------------------------------------------------------------------


def _manifest_with_profiles_and_conditional_step(
    *,
    effective_values: dict[str, str] | None = None,
) -> dict[str, JsonValue]:
    """Build a v1 manifest with a test-value profile and a conditional step.

    Args:
        effective_values: Values to put in the default profile.  Defaults to
            ``{"paymentId": "pmnt-001"}``.

    Returns:
        A v1 manifest JSON object.
    """
    vals: dict[str, JsonValue] = dict(effective_values or {"paymentId": "pmnt-001"})
    return {
        "schemaVersion": "v1",
        "name": "Conditional manifest",
        "testValueProfiles": {
            "defaultProfileId": "sandbox",
            "profiles": [
                {"id": "sandbox", "label": "Sandbox", "values": vals},
            ],
            "allowedOverrideKeys": ["paymentId"],
            "nonSecretKeys": ["paymentId"],
        },
        "steps": cast(
            list[JsonValue],
            [
                {
                    "id": "unconditional",
                    "name": "Always runs",
                    "request": {"method": "GET", "url": "https://example.com/unconditional"},
                    "assertions": [{"type": "http_status", "expected": 200}],
                    "mandatory": True,
                },
                {
                    "id": "cond",
                    "name": "Conditional step",
                    "request": {
                        "method": "GET",
                        "url": "https://example.com/${testValues.paymentId}",
                    },
                    "assertions": [{"type": "http_status", "expected": 200}],
                    "selectionMetadata": {
                        "conditionId": "payment-supported",
                        "conditionLabel": "Payment feature supported",
                        "conditional": True,
                        "requiredTestValueKeys": ["paymentId"],
                    },
                },
            ],
        ),
    }


@pytest.mark.unit
def test_conditional_row_selected_when_default_profile_has_required_values() -> None:
    """Conditional step row is selected when the default profile resolves all required values."""
    manifest = _manifest_with_profiles_and_conditional_step()
    preview = _validated_preview(_bound_form(manifest))

    cond_row = next(r for r in preview.rows if r.id == "cond")
    assert cond_row.conditional is True
    assert cond_row.default_selected is True
    assert cond_row.selected_after_form is True
    assert cond_row.condition_id == "payment-supported"
    assert cond_row.condition_label == "Payment feature supported"
    assert cond_row.required_test_value_keys == ("paymentId",)
    assert cond_row.missing_test_value_keys == ()
    assert cond_row.test_value_profile_id == "sandbox"
    assert cond_row.test_value_profile_source == "default"
    assert cond_row.test_value_override_keys == ()


@pytest.mark.unit
def test_conditional_row_shows_overridden_source_when_participant_overrides_key() -> None:
    """Conditional row carries ``overridden`` profile source when participant supplies an override."""
    config_with_override: dict[str, JsonValue] = {
        **VALID_CONFIG,
        "testValues": {"overrides": {"paymentId": "my-payment-001"}},
    }
    manifest = _manifest_with_profiles_and_conditional_step()
    form = PlanBuilderForm(
        data={
            "config_json": json.dumps(config_with_override),
            "manifest_json": json.dumps(manifest),
            "selection_mode": "deselect",
            "deselect_step_ids": [],
        }
    )
    preview = _validated_preview(form)

    cond_row = next(r for r in preview.rows if r.id == "cond")
    assert cond_row.conditional is True
    assert cond_row.default_selected is True
    assert cond_row.test_value_profile_source == "overridden"
    assert "paymentId" in cond_row.test_value_override_keys
    assert cond_row.missing_test_value_keys == ()


@pytest.mark.unit
def test_conditional_row_deselected_when_manifest_has_no_config_profile() -> None:
    """Conditional step is deselected by default when no test_value_context is provided via config."""
    # Build the manifest but provide a config with no testValues section
    manifest = _manifest_with_profiles_and_conditional_step()
    # The config has no testValues, so build_plan_test_value_context uses the manifest default.
    # The default profile has paymentId, so the step is auto-selected.
    preview = _validated_preview(_bound_form(manifest))

    cond_row = next(r for r in preview.rows if r.id == "cond")
    # The default profile resolves paymentId → step should be selected
    assert cond_row.default_selected is True
    assert cond_row.missing_test_value_keys == ()


@pytest.mark.unit
def test_unconditional_rows_have_no_conditional_metadata() -> None:
    """Rows for steps without selectionMetadata carry zero-valued conditional fields."""
    manifest = _manifest_with_profiles_and_conditional_step()
    preview = _validated_preview(_bound_form(manifest))

    unconditional_row = next(r for r in preview.rows if r.id == "unconditional")
    assert unconditional_row.conditional is False
    assert unconditional_row.condition_id is None
    assert unconditional_row.condition_label is None
    assert unconditional_row.required_test_value_keys == ()
    assert unconditional_row.missing_test_value_keys == ()


@pytest.mark.unit
def test_backward_compatible_manifest_rows_have_no_conditional_metadata() -> None:
    """Rows for manifests without testValueProfiles have zero-valued conditional fields."""
    manifest = _v1_manifest([_http_step("a"), _http_step("b", mandatory=True)])
    preview = _validated_preview(_bound_form(manifest))

    for row in preview.rows:
        assert row.conditional is False
        assert row.test_value_profile_id is None
        assert row.test_value_profile_source is None
        assert row.test_value_override_keys == ()


@pytest.mark.unit
def test_conditional_row_deselected_via_no_context_has_missing_keys_populated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Populate conditional row missing keys when no effective profile values resolve.

    Args:
        monkeypatch: Fixture used to patch the plan-builder test-value context helper.
    """
    from pathlib import Path

    from conformance.api.plan_builder import build_plan_preview
    from conformance.manifest import parse_manifest
    from conformance.model_bank_config import parse_model_bank_config

    manifest_obj = _manifest_with_profiles_and_conditional_step()
    manifest = parse_manifest(manifest_obj)
    from conformance.test_plan import PlanTestValueContext

    # Config without a testValues section: build_plan_test_value_context resolves the
    # manifest default profile (sandbox), which has paymentId → step should be selected.
    # To simulate missing values we patch the imported helper symbol in plan_builder.
    def _empty_ctx(
        _manifest: object,
        _config_test_values: object,
        _config_test_data: object | None = None,
    ) -> PlanTestValueContext:
        """Return empty context to simulate no effective profile values."""
        del _config_test_data
        return PlanTestValueContext()

    monkeypatch.setattr("conformance.api.plan_builder.build_plan_test_value_context", _empty_ctx)
    config = parse_model_bank_config(VALID_CONFIG, base_dir=Path.cwd(), output_base_dir=Path.cwd())
    preview = build_plan_preview(config=config, manifest=manifest)

    cond_row = next(r for r in preview.rows if r.id == "cond")
    assert cond_row.conditional is True
    assert cond_row.default_selected is False
    assert cond_row.missing_test_value_keys == ("paymentId",)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("default_value", "override_value"),
    [
        ("2025-01-15", "2026-02-20"),
        ("2025-01-15T10:00:00Z", "2026-02-20T11:15:00+00:00"),
        ("123e4567-e89b-12d3-a456-426614174000", "123e4567-e89b-12d3-a456-426614174111"),
        ("https://example.com", "http://example.org/resource"),
        ("42", "7"),
        ("10.50", "1.25"),
        ("true", "false"),
    ],
)
def test_infer_shape_warning_returns_none_for_matching_shapes(default_value: str, override_value: str) -> None:
    """Shape warnings are suppressed when override values match default shapes.

    Args:
        default_value: Profile default value used to infer shape.
        override_value: Participant override value to validate.
    """
    assert _infer_shape_warning("sampleKey", default_value, override_value) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("default_value", "override_value"),
    [
        ("2025-01-15", "15-01-2025"),
        ("2025-01-15T10:00:00Z", "2025-01-15"),
        ("123e4567-e89b-12d3-a456-426614174000", "not-a-uuid"),
        ("https://example.com", "example.com"),
        ("42", "42.5"),
        ("10.50", "abc"),
        ("true", "yes"),
    ],
)
def test_infer_shape_warning_returns_advisory_for_mismatched_shapes(default_value: str, override_value: str) -> None:
    """Shape warnings are returned when override values diverge from default shape.

    Args:
        default_value: Profile default value used to infer shape.
        override_value: Participant override value to validate.
    """
    warning = _infer_shape_warning("sampleKey", default_value, override_value)
    assert warning is not None


@pytest.mark.unit
def test_build_plan_preview_populates_test_value_fields_with_profiles() -> None:
    """Preview includes field specs when manifest test-value profiles are declared."""
    manifest = _manifest_with_profiles_and_conditional_step(effective_values={"paymentId": "pmnt-001"})
    form = PlanBuilderForm(
        data={
            "config_json": json.dumps(VALID_CONFIG),
            "manifest_json": json.dumps(manifest),
            "selection_mode": "deselect",
            "test_value_profile": "sandbox",
            "custom_tv_paymentId": "pmnt-override",
        }
    )
    preview = _validated_preview(form)

    assert preview.test_value_fields
    assert preview.test_value_fields[0].key == "paymentId"
    assert preview.test_value_fields[0].default_value == "pmnt-001"
    assert preview.test_value_fields[0].current_value == "pmnt-override"
    assert preview.test_value_fields[0].is_overridden is True


@pytest.mark.unit
def test_build_plan_preview_populates_test_value_fields_with_test_data_schema() -> None:
    """Preview includes field specs for selected new-schema test-data keys."""
    manifest = _v1_manifest(
        [
            {
                **_http_step("selected", mandatory=True),
                "request": {"method": "GET", "url": "https://example.com/${testValues.paymentId}"},
            },
            {
                **_http_step("unselected", optional=True),
                "request": {"method": "GET", "url": "https://example.com/${testValues.unusedPaymentId}"},
            },
        ]
    )
    manifest["testValues"] = {
        "baseline": {
            "paymentId": "pmnt-001",
            "unusedPaymentId": "unused-001",
        },
        "allowedCustomKeys": ["paymentId", "unusedPaymentId"],
    }
    form = PlanBuilderForm(
        data={
            "config_json": json.dumps(VALID_CONFIG),
            "manifest_json": json.dumps(manifest),
            "selection_mode": "select",
            "selected_step_ids": ["selected"],
            "custom_tv_paymentId": "pmnt-override",
        }
    )
    preview = _validated_preview(form)

    assert [field.key for field in preview.test_value_fields] == ["paymentId"]
    assert preview.test_value_fields[0].default_value == "pmnt-001"
    assert preview.test_value_fields[0].current_value == "pmnt-override"
    assert preview.test_value_fields[0].is_overridden is True
    assert preview.run_plan.test_data.values["paymentId"] == "pmnt-override"
    assert preview.run_plan.test_data.values.get("unusedPaymentId") is None


@pytest.mark.unit
def test_build_plan_preview_test_value_fields_empty_without_test_value_metadata() -> None:
    """Preview omits test-value field specs when manifest has no test-value metadata."""
    manifest = _v1_manifest([_http_step("mandatory", mandatory=True)])
    preview = _validated_preview(_bound_form(manifest))

    assert preview.test_value_fields == ()


@pytest.mark.unit
def test_test_value_field_is_not_overridden_when_no_custom_value_present() -> None:
    """Preview field specs keep defaults when custom override keys are absent."""
    manifest = _manifest_with_profiles_and_conditional_step(effective_values={"paymentId": "pmnt-001"})
    preview = _validated_preview(_bound_form(manifest))

    assert preview.test_value_fields
    assert preview.test_value_fields[0].is_overridden is False
    assert preview.test_value_fields[0].shape_warning is None


def _manifest_with_body_test_values(
    step_id: str = "step-a",
    *,
    allowed_keys: list[str] | None = None,
    baseline: dict[str, str] | None = None,
    mandatory: bool = True,
) -> dict[str, JsonValue]:
    """Build a v1 manifest with a JSON-body step referencing test-value keys.

    Args:
        step_id: Step identifier for the HTTP step.
        allowed_keys: Keys listed in ``testValues.allowedCustomKeys``.
        baseline: Baseline values for ``testValues.baseline``.
        mandatory: Whether the step is certification mandatory.

    Returns:
        A JSON manifest object suitable for plan-builder testing.
    """
    manifest = _v1_manifest(
        [
            {
                **_http_step(step_id, mandatory=mandatory),
                "request": {
                    "method": "POST",
                    "url": "https://example.com/payments",
                    "body": {
                        "encoding": "json",
                        "value": {
                            "Data": {
                                "Initiation": {
                                    "Amount": "${testValues.amount}",
                                    "Currency": "${testValues.currency}",
                                    "CreditorAccount": {
                                        "Name": "${testValues.creditorName}",
                                    },
                                },
                            },
                            "Risk": {},
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 201}],
            },
        ]
    )
    manifest["testValues"] = cast(
        "JsonValue",
        {
            "baseline": baseline or {"amount": "1.00", "currency": "GBP", "creditorName": "Test Merchant"},
            "allowedCustomKeys": allowed_keys or ["amount", "currency", "creditorName"],
        },
    )
    return manifest


@pytest.mark.unit
def test_test_value_step_groups_empty_for_legacy_manifest() -> None:
    """New-schema step groups are empty for legacy profile-based manifests."""
    manifest = _manifest_with_profiles_and_conditional_step(effective_values={"paymentId": "pmnt-001"})
    preview = _validated_preview(_bound_form(manifest))

    assert preview.test_value_step_groups == ()


@pytest.mark.unit
def test_test_value_step_groups_empty_without_test_value_metadata() -> None:
    """New-schema step groups are empty when manifest has no testValues block."""
    manifest = _v1_manifest([_http_step("step-a", mandatory=True)])
    preview = _validated_preview(_bound_form(manifest))

    assert preview.test_value_step_groups == ()


@pytest.mark.unit
def test_test_value_step_groups_produced_for_new_schema_body_references() -> None:
    """New-schema manifests with body test-value refs produce one group per step."""
    manifest = _manifest_with_body_test_values()
    preview = _validated_preview(_bound_form(manifest))

    assert len(preview.test_value_step_groups) == 1
    group = preview.test_value_step_groups[0]
    assert group.step_id == "step-a"
    assert group.has_canonical_keys is True


@pytest.mark.unit
def test_test_value_step_groups_body_surface_is_ordered_first() -> None:
    """JSON-body surface is ordered first in step groups."""
    manifest = _manifest_with_body_test_values()
    preview = _validated_preview(_bound_form(manifest))

    group = preview.test_value_step_groups[0]
    body_surface = group.surfaces[0]
    assert body_surface.request_area == "request-json-body"
    assert body_surface.surface_label == "Body"


@pytest.mark.unit
def test_test_value_step_groups_body_rows_include_group_and_leaf_nodes() -> None:
    """Body surface rows include intermediate group nodes and leaf input nodes."""
    manifest = _manifest_with_body_test_values()
    preview = _validated_preview(_bound_form(manifest))

    group = preview.test_value_step_groups[0]
    body_surface = next(s for s in group.surfaces if s.request_area == "request-json-body")
    row_types = [(r.row_type, r.label) for r in body_surface.rows]

    assert ("group", "Data") in row_types
    assert ("group", "Initiation") in row_types
    leaf_labels = {r.label for r in body_surface.rows if r.row_type == "leaf"}
    assert leaf_labels == {"Amount", "Currency", "Name"}


@pytest.mark.unit
def test_test_value_step_groups_leaf_rows_carry_depth() -> None:
    """Leaf rows nested under group rows carry increasing depth values."""
    manifest = _manifest_with_body_test_values()
    preview = _validated_preview(_bound_form(manifest))

    group = preview.test_value_step_groups[0]
    body_surface = next(s for s in group.surfaces if s.request_area == "request-json-body")
    data_group = next(r for r in body_surface.rows if r.row_type == "group" and r.label == "Data")
    amount_leaf = next(r for r in body_surface.rows if r.row_type == "leaf" and r.label == "Amount")
    creditor_group = next(r for r in body_surface.rows if r.row_type == "group" and r.label == "CreditorAccount")
    name_leaf = next(r for r in body_surface.rows if r.row_type == "leaf" and r.label == "Name")

    assert data_group.depth == 0
    assert amount_leaf.depth > data_group.depth
    assert creditor_group.depth > data_group.depth
    assert name_leaf.depth > creditor_group.depth


@pytest.mark.unit
def test_test_value_step_groups_leaf_rows_carry_field_spec_data() -> None:
    """Canonical leaf rows carry baseline and override data for template rendering."""
    manifest = _manifest_with_body_test_values(
        baseline={"amount": "1.00", "currency": "GBP", "creditorName": "Test Merchant"},
    )
    form = PlanBuilderForm(
        data={
            "config_json": json.dumps(VALID_CONFIG),
            "manifest_json": json.dumps(manifest),
            "selection_mode": "deselect",
            "custom_tv_amount": "5.00",
        }
    )
    preview = _validated_preview(form)

    group = preview.test_value_step_groups[0]
    body_surface = next(s for s in group.surfaces if s.request_area == "request-json-body")
    amount_leaf = next(r for r in body_surface.rows if r.row_type == "leaf" and r.label == "Amount")

    assert amount_leaf.key == "amount"
    assert amount_leaf.default_value == "1.00"
    assert amount_leaf.current_value == "5.00"
    assert amount_leaf.is_overridden is True
    assert amount_leaf.is_canonical is True


@pytest.mark.unit
def test_test_value_step_groups_unselected_steps_are_excluded() -> None:
    """Steps not included in the selected plan produce no step group."""
    manifest = _v1_manifest(
        [
            {
                **_http_step("selected-step", mandatory=True),
                "request": {
                    "method": "POST",
                    "url": "https://example.com/",
                    "body": {
                        "encoding": "json",
                        "value": {"Amount": "${testValues.amount}"},
                    },
                },
                "assertions": [{"type": "http_status", "expected": 201}],
            },
            {
                **_http_step("optional-step", optional=True),
                "request": {
                    "method": "POST",
                    "url": "https://example.com/",
                    "body": {
                        "encoding": "json",
                        "value": {"Amount": "${testValues.amount}"},
                    },
                },
                "assertions": [{"type": "http_status", "expected": 201}],
            },
        ]
    )
    manifest["testValues"] = {
        "baseline": {"amount": "1.00"},
        "allowedCustomKeys": ["amount"],
    }
    form = PlanBuilderForm(
        data={
            "config_json": json.dumps(VALID_CONFIG),
            "manifest_json": json.dumps(manifest),
            "selection_mode": "select",
            "selected_step_ids": ["selected-step"],
        }
    )
    preview = _validated_preview(form)

    step_ids = [g.step_id for g in preview.test_value_step_groups]
    assert step_ids == ["selected-step"]


@pytest.mark.unit
def test_test_value_step_groups_duplicate_key_only_canonical_in_first_step() -> None:
    """When the same key appears in two steps, only the first step marks it canonical."""
    manifest = _v1_manifest(
        [
            {
                **_http_step("step-a", mandatory=True),
                "request": {
                    "method": "POST",
                    "url": "https://example.com/",
                    "body": {
                        "encoding": "json",
                        "value": {"Amount": "${testValues.amount}"},
                    },
                },
                "assertions": [{"type": "http_status", "expected": 201}],
            },
            {
                **_http_step("step-b", mandatory=True),
                "request": {
                    "method": "POST",
                    "url": "https://example.com/other",
                    "body": {
                        "encoding": "json",
                        "value": {"Amount": "${testValues.amount}"},
                    },
                },
                "assertions": [{"type": "http_status", "expected": 201}],
            },
        ]
    )
    manifest["testValues"] = {
        "baseline": {"amount": "1.00"},
        "allowedCustomKeys": ["amount"],
    }
    preview = _validated_preview(_bound_form(manifest))

    assert len(preview.test_value_step_groups) == 2
    group_a = preview.test_value_step_groups[0]
    group_b = preview.test_value_step_groups[1]
    assert group_a.step_id == "step-a"
    assert group_b.step_id == "step-b"

    leaf_a = next(r for s in group_a.surfaces for r in s.rows if r.row_type == "leaf")
    leaf_b = next(r for s in group_b.surfaces for r in s.rows if r.row_type == "leaf")
    assert leaf_a.is_canonical is True
    assert leaf_b.is_canonical is False
    assert group_a.has_canonical_keys is True
    assert group_b.has_canonical_keys is False


@pytest.mark.unit
def test_test_value_step_groups_header_surface_is_not_primary() -> None:
    """Header surfaces are not primary (collapsed by default)."""
    manifest = _v1_manifest(
        [
            {
                **_http_step("step-a", mandatory=True),
                "request": {
                    "method": "GET",
                    "url": "https://example.com/",
                    "headers": {"X-Custom": "${testValues.headerVal}"},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ]
    )
    manifest["testValues"] = {
        "baseline": {"headerVal": "abc"},
        "allowedCustomKeys": ["headerVal"],
    }
    preview = _validated_preview(_bound_form(manifest))

    group = preview.test_value_step_groups[0]
    header_surface = next((s for s in group.surfaces if s.request_area == "request-header"), None)
    assert header_surface is not None
    assert header_surface.surface_label == "Headers"


@pytest.mark.unit
def test_test_value_step_groups_leaf_row_default_value_without_override() -> None:
    """Canonical leaf rows with no override carry baseline as both values."""
    manifest = _manifest_with_body_test_values(
        baseline={"amount": "1.00", "currency": "GBP", "creditorName": "Test Merchant"},
    )
    preview = _validated_preview(_bound_form(manifest))

    group = preview.test_value_step_groups[0]
    body_surface = next(s for s in group.surfaces if s.request_area == "request-json-body")
    currency_leaf = next((r for r in body_surface.rows if r.row_type == "leaf" and r.key == "currency"), None)
    assert currency_leaf is not None
    assert currency_leaf.default_value == "GBP"
    assert currency_leaf.current_value == "GBP"
    assert currency_leaf.is_overridden is False
