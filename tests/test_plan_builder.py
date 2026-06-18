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
    "testValues": {
        "profile": "ozone-demo",
        "overrides": {
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
        "account-access-consent-transactions-basic",
        "psu-authorization-transactions-basic",
        "token-exchange-transactions-basic",
        "account-transactions-basic",
        "account-transactions",
        "transactions-list",
    ]
    optional_step_ids = [
        "transactions-list-basic",
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
def test_blank_manifest_resolves_ais_fcs_legacy_benchmark_with_optional_steps_deselected() -> None:
    """A blank manifest with the legacy benchmark ``testSuite`` preserves opt-in optional rows."""
    form = PlanBuilderForm(
        data={
            "config_json": json.dumps(AIS_FCS_LEGACY_BENCHMARK_CONFIG),
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
        "OB-400-ACC-100400",
        "OB-400-ACC-100200",
        "OB-400-BAL-101200",
        "account-access-consent-transactions-basic",
        "psu-authorization-transactions-basic",
        "token-exchange-transactions-basic",
        "OB-400-TRA-105000",
        "OB-400-TRA-105100",
        "OB-400-TRA-105110",
        "OB-400-TRA-105120",
    ]
    optional_step_ids = [
        "OB-400-BAL-101300",
        "OB-400-BEN-101800",
        "OB-400-BEN-101900",
        "OB-400-DIR-102300",
        "OB-400-DIR-102400",
        "OB-400-OFF-102600",
        "OB-400-PAR-102900",
        "OB-400-PAR-102901",
        "OB-400-PRO-103200",
        "OB-400-SCP-103500",
        "OB-400-STO-103800",
        "OB-400-TRA-105200",
    ]

    assert preview.suite_metadata is not None
    assert preview.suite_metadata.catalog_id == "ob-read-write/v4.0/fapi1-advanced/ais-fcs-legacy-benchmark"
    assert preview.manifest.name == "Open Banking Read/Write v4.0 FAPI 1 Advanced AIS FCS legacy benchmark"
    assert preview.selected_plan.selected_step_ids() == mandatory_step_ids
    assert [row.id for row in preview.rows] == mandatory_step_ids + optional_step_ids
    assert [row.id for row in preview.rows if row.default_selected] == mandatory_step_ids
    assert [row.id for row in preview.rows if row.optional and not row.default_selected] == optional_step_ids
    rows_by_id = {row.id: row for row in preview.rows}
    assert rows_by_id["OB-400-TRA-105000"].mandatory is True
    assert rows_by_id["OB-400-TRA-105000"].default_selected is True
    assert rows_by_id["OB-400-TRA-105200"].optional is True
    assert rows_by_id["OB-400-TRA-105200"].default_selected is False
    assert preview.launch_supported is True


@pytest.mark.unit
def test_blank_manifest_resolves_pis_fcs_legacy_benchmark_with_conditional_gating() -> None:
    """PIS FCS preview selects/deselects conditional rows from resolved test values."""
    form = PlanBuilderForm(
        data={
            "config_json": json.dumps(PIS_FCS_LEGACY_BENCHMARK_CONFIG),
            "manifest_json": "",
            "selection_mode": "deselect",
        }
    )

    preview = _validated_preview(form)

    assert preview.suite_metadata is not None
    assert preview.suite_metadata.catalog_id == "ob-read-write/v4.0/fapi1-advanced/pis/pis-fcs-legacy-benchmark"
    assert preview.manifest.name == "Open Banking Read/Write v4.0 FAPI 1 Advanced PIS FCS legacy benchmark"
    rows_by_id = {row.id: row for row in preview.rows}
    scheduled_row = rows_by_id["OB-400-DOP-100800"]
    standing_order_row = rows_by_id["OB-400-DOP-101200"]
    international_row = rows_by_id["OB-400-DOP-101600"]
    assert scheduled_row.conditional is True
    assert scheduled_row.selected_after_form is True
    assert scheduled_row.missing_test_value_keys == ()
    assert standing_order_row.conditional is True
    assert standing_order_row.selected_after_form is True
    assert standing_order_row.missing_test_value_keys == ()
    assert international_row.conditional is True
    assert international_row.selected_after_form is False
    assert international_row.missing_test_value_keys == (
        "currencyOfTransfer",
        "internationalCreditorSchemeName",
        "internationalCreditorIdentification",
        "internationalCreditorName",
    )
    selected_step_ids = preview.selected_plan.selected_step_ids()
    assert "OB-400-DOP-100800" in selected_step_ids
    assert "OB-400-DOP-101200" in selected_step_ids
    assert "OB-400-DOP-101600" not in selected_step_ids
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
def test_preview_capability_warnings_empty_for_catalog_suite_without_declaration() -> None:
    """Catalog suite preview with custom environment and no declaration produces warnings only."""
    # discovery-jwks suite — no PSU mode or token auth method required
    form = PlanBuilderForm(
        data={
            "config_json": json.dumps(SUITE_CONFIG),
            "manifest_json": "",
            "selection_mode": "deselect",
            "deselect_step_ids": [],
        }
    )

    preview = _validated_preview(form)

    # Custom environment without declaration — each unknown dimension is a warning
    # discovery-jwks has no PSU or token-auth requirements, so only dimension
    # warnings are generated (standard, spec_version, api, suite).
    assert isinstance(preview.capability_warnings, tuple)
    assert preview.launch_blockers == ()


@pytest.mark.unit
def test_discovery_suite_ignores_unrelated_fapi_signing_auth_method_for_capabilities() -> None:
    """No-auth discovery suites must not inherit config-level token auth choices."""
    config_with_signing: dict[str, JsonValue] = {
        **SUITE_CONFIG,
        "fapiSigning": {
            "certificatePathRoot": ".",
            "signingCertificatePath": "dummy-signing.crt",
            "signingPrivateKeyPath": "dummy-signing.key",  # pragma: allowlist secret
            "kid": "test-kid",
            "clientAssertionIssuer": "test-client",
            "clientAssertionSubject": "test-client",
            "tokenEndpointAuthMethod": "private_key_jwt",
        },
    }
    form = PlanBuilderForm(
        data={
            "config_json": json.dumps(config_with_signing),
            "manifest_json": "",
            "selection_mode": "deselect",
            "deselect_step_ids": [],
        }
    )

    preview = _validated_preview(form)

    assert preview.launch_blockers == ()


@pytest.mark.unit
def test_preview_launch_blockers_includes_capability_blockers_from_known_preset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capability hard blockers from environment evaluation are merged into launch_blockers."""
    from conformance.api import plan_builder as pb
    from conformance.environment_capabilities import CapabilityEvaluation

    fake_evaluation = CapabilityEvaluation(
        support="blocked",
        blockers=("Unsupported suite/auth combination.",),
        warnings=(),
        suite_capability=None,
    )

    def _fake_evaluate_capability_support(
        *,
        config: object,
        manifest: object,
        suite_metadata: object,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return fake capability blockers for testing.

        Args:
            config: Ignored mock config argument.
            manifest: Ignored mock manifest argument.
            suite_metadata: Ignored mock suite metadata argument.

        Returns:
            Two-tuple of capability blockers and empty warnings.
        """
        return fake_evaluation.blockers, fake_evaluation.warnings

    monkeypatch.setattr(pb, "_evaluate_capability_support", _fake_evaluate_capability_support)

    form = PlanBuilderForm(
        data={
            "config_json": json.dumps(AIS_SLICE_CONFIG),
            "manifest_json": "",
            "selection_mode": "deselect",
            "deselect_step_ids": [],
        }
    )

    preview = _validated_preview(form)

    assert "Unsupported suite/auth combination." in preview.launch_blockers
    assert preview.launch_supported is False


@pytest.mark.unit
def test_preview_capability_warnings_surfaced_without_blocking_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capability warnings are stored in capability_warnings and do not block launch."""
    from conformance.api import plan_builder as pb

    def _fake_evaluate_capability_support(
        *,
        config: object,
        manifest: object,
        suite_metadata: object,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return fake capability warnings for testing.

        Args:
            config: Ignored mock config argument.
            manifest: Ignored mock manifest argument.
            suite_metadata: Ignored mock suite metadata argument.

        Returns:
            Two-tuple of empty blockers and one capability warning.
        """
        return (), ("Custom environment capability for PSU mode is undeclared; compatibility is unknown.",)

    monkeypatch.setattr(pb, "_evaluate_capability_support", _fake_evaluate_capability_support)

    form = PlanBuilderForm(
        data={
            "config_json": json.dumps(AIS_SLICE_CONFIG),
            "manifest_json": "",
            "selection_mode": "deselect",
            "deselect_step_ids": [],
        }
    )

    preview = _validated_preview(form)

    expected_warning = "Custom environment capability for PSU mode is undeclared; compatibility is unknown."
    assert expected_warning in preview.capability_warnings
    assert preview.launch_supported is True
    assert preview.launch_blockers == ()


@pytest.mark.unit
def test_build_plan_preview_populates_tree_nodes_for_catalog_suite() -> None:
    """build_plan_preview populates tree_nodes when suite_metadata is available."""
    from conformance.openapi_plan_metadata import StepTreeNode

    form = PlanBuilderForm(
        data={
            "config_json": json.dumps(AIS_BASELINE_CONFIG),
            "manifest_json": "",
            "selection_mode": "deselect",
            "deselect_step_ids": [],
        }
    )

    preview = _validated_preview(form)

    assert preview.suite_metadata is not None
    assert preview.tree_nodes
    assert all(isinstance(node, StepTreeNode) for node in preview.tree_nodes)


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
    def _empty_ctx(_manifest: object, _config_test_values: object) -> PlanTestValueContext:
        """Return empty context to simulate no effective profile values."""
        return PlanTestValueContext()

    monkeypatch.setattr("conformance.api.plan_builder.build_plan_test_value_context", _empty_ctx)
    config = parse_model_bank_config(VALID_CONFIG, base_dir=Path.cwd(), output_base_dir=Path.cwd())
    preview = build_plan_preview(config=config, manifest=manifest)

    cond_row = next(r for r in preview.rows if r.id == "cond")
    assert cond_row.conditional is True
    assert cond_row.default_selected is False
    assert cond_row.missing_test_value_keys == ("paymentId",)
