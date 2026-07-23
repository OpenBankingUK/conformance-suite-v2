"""Integration tests for participant-facing browser UI views."""

from __future__ import annotations

import html
import json
import re
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client

from conformance.api.auth_session_store import auth_session_store
from conformance.api.run_store import RunConflictError, RunPlanStep, run_store
from conformance.api.ui_views import _run_context
from conformance.http import JsonHttpClientError, JsonHttpResponse
from conformance.json_types import JsonValue
from tests.test_executor import _executor_signing_config

VALID_CONFIG: dict[str, JsonValue] = {
    "environment": "test-env",
    "discoveryUrl": "https://example.com/.well-known/openid-configuration",
}

TARGET_CONFIG: dict[str, JsonValue] = {
    **VALID_CONFIG,
    "testTarget": {
        "standard": "obl",
        "specification": "read-write",
        "securityProfile": "fapi1-advanced",
        "specificationVersion": "v4.0.1",
        "resourceGroups": ["ais"],
    },
}

DCR_TARGET_CONFIG: dict[str, JsonValue] = {
    **VALID_CONFIG,
    "testTarget": {
        "standard": "obl",
        "specification": "dynamic-client-registration",
        "securityProfile": "fapi1-advanced",
        "specificationVersion": "3.3",
    },
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
        "openBankingIntentId": "consent-ui-123",
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


@pytest.fixture(autouse=True)
def _reset_global_stores() -> Iterator[None]:
    """Reset process-local singleton stores around each UI test.

    Yields:
        Control back to pytest while the test executes.
    """
    run_store.reset()
    auth_session_store.reset()
    yield
    _wait_for_active_run_to_settle(timeout_seconds=1.0)
    run_store.reset()
    auth_session_store.reset()


def _wait_for_active_run_to_settle(*, timeout_seconds: float) -> None:
    """Best-effort wait for active runs to leave pending/running states.

    Args:
        timeout_seconds: Maximum wall-clock time to wait before teardown
            proceeds with store reset.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with run_store._lock:
            active_run_id = run_store._active_run_id
            active_record = run_store._runs.get(active_run_id) if active_run_id is not None else None
            if active_record is None or active_record.status in {"completed", "failed"}:
                return
        time.sleep(0.01)


def _http_step(
    step_id: str,
    *,
    mandatory: bool = False,
    optional: bool = False,
    phase: str = "execution",
    group: str = "default",
) -> dict[str, JsonValue]:
    """Build a minimal v1 HTTP step for UI tests.

    Args:
        step_id: Stable manifest step id.
        mandatory: Whether the manifest marks the step as mandatory.
        optional: Whether the manifest marks the step as optional.
        phase: Scheduling phase declared by the manifest step.
        group: Execution group declared by the manifest step.

    Returns:
        JSON object representing a valid v1 HTTP manifest step.
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


def _manual_psu_step(step_id: str) -> dict[str, JsonValue]:
    """Build a manual PSU authorisation step for UI tests.

    Args:
        step_id: Stable manifest step id.

    Returns:
        JSON object representing a valid manual PSU step.
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


def _manual_psu_step_with_request_object(step_id: str, *, request_object: str) -> dict[str, JsonValue]:
    """Build a manual PSU step carrying a request object.

    Args:
        step_id: Stable manifest step id.
        request_object: Opaque request object value to include in the
            authorisation URL.

    Returns:
        JSON object representing a valid manual PSU step.
    """
    step = _manual_psu_step(step_id)
    step["requestObject"] = request_object
    step["timeoutSeconds"] = 2
    return step


def _v1_manifest(steps: list[dict[str, JsonValue]]) -> dict[str, JsonValue]:
    """Build a v1 manifest for UI tests.

    Args:
        steps: Manifest step JSON objects.

    Returns:
        JSON object representing a v1 manifest.
    """
    return {"schemaVersion": "v1", "name": "UI manifest", "steps": cast(list[JsonValue], steps)}


def _plan_form_data(
    manifest: dict[str, JsonValue],
    *,
    selected_step_ids: list[str] | None = None,
    selection_mode: str = "select",
) -> dict[str, object]:
    """Build form data for plan preview and launch requests.

    Args:
        manifest: Manifest JSON object to submit.
        selected_step_ids: Step ids submitted by checked row checkboxes.
        selection_mode: Selection mode submitted by the rendered form.

    Returns:
        Form-encoded payload dictionary accepted by Django's test client.
    """
    return {
        "config_json": json.dumps(VALID_CONFIG),
        "manifest_json": json.dumps(manifest),
        "selection_mode": selection_mode,
        "selected_step_ids": selected_step_ids or [],
    }


def _suite_plan_form_data(*, selected_step_ids: list[str] | None = None) -> dict[str, object]:
    """Build form data that resolves the manifest from config ``testSuite``.

    Args:
        selected_step_ids: Step ids submitted by checked row checkboxes.

    Returns:
        Form-encoded payload dictionary accepted by Django's test client.
    """
    return {
        "config_json": json.dumps(SUITE_CONFIG),
        "manifest_json": "",
        "selection_mode": "select",
        "selected_step_ids": selected_step_ids or [],
    }


def _psu_auth_starter_suite_form_data(*, selected_step_ids: list[str] | None = None) -> dict[str, object]:
    """Build form data that resolves the PSU auth starter suite from config ``testSuite``.

    Args:
        selected_step_ids: Step ids submitted by checked row checkboxes.

    Returns:
        Form-encoded payload dictionary for the ``psu-auth-starter`` catalog entry.
    """
    return {
        "config_json": json.dumps(PSU_AUTH_STARTER_CONFIG),
        "manifest_json": "",
        "selection_mode": "select",
        "selected_step_ids": selected_step_ids or [],
    }


def _ais_slice_suite_form_data(*, selected_step_ids: list[str] | None = None) -> dict[str, object]:
    """Build form data that resolves the AIS slice suite from config ``testSuite``.

    Args:
        selected_step_ids: Step ids submitted by checked row checkboxes.

    Returns:
        Form-encoded payload dictionary for the AIS catalog entry.
    """
    return {
        "config_json": json.dumps(AIS_SLICE_CONFIG),
        "manifest_json": "",
        "selection_mode": "select",
        "selected_step_ids": selected_step_ids or [],
    }


def _ais_baseline_suite_form_data(
    tmp_path: Path,
    *,
    selected_step_ids: list[str] | None = None,
) -> dict[str, object]:
    """Build form data that resolves the AIS baseline suite with FAPI signing config.

    Args:
        tmp_path: Temporary directory used to materialise signing PEM files.
        selected_step_ids: Step ids submitted by checked row checkboxes.

    Returns:
        Form-encoded payload dictionary for the AIS baseline catalog entry.
    """
    signing_config = _executor_signing_config(tmp_path)
    config = {
        **AIS_BASELINE_CONFIG,
        "fapiSigning": {
            "signingCertificatePath": str(signing_config.signing_certificate_path),
            "signingPrivateKeyPath": str(signing_config.signing_private_key_path),
            "kid": signing_config.key_id,
            "requestObjectIssuerOverride": signing_config.request_object_issuer,
            "privateKeyJwtIssuerOverride": signing_config.private_key_jwt_issuer,
            "privateKeyJwtSubjectOverride": signing_config.private_key_jwt_subject,
            "tokenEndpointAuthMethod": signing_config.token_endpoint_auth_method,
        },
    }
    return {
        "config_json": json.dumps(config),
        "manifest_json": "",
        "selection_mode": "select",
        "selected_step_ids": selected_step_ids or [],
    }


def _run_id_from_redirect(location: str) -> str:
    """Extract the run id from a Django redirect response.

    Args:
        location: Redirect target from a Django test response.

    Returns:
        Run id segment from the redirect target.
    """
    return location.rstrip("/").rsplit("/", maxsplit=1)[-1]


def _query_parameter(url: str, name: str) -> str:
    """Read a single query parameter from a URL.

    Args:
        url: Absolute URL containing a query string.
        name: Query parameter name to extract.

    Returns:
        The first query parameter value.
    """
    values = parse_qs(urlsplit(url).query)[name]
    return values[0]


def _registered_auth_state(run_id: str) -> str:
    """Read the single registered PSU auth-session state for a run.

    Args:
        run_id: Run whose pending PSU authorization state should be returned.

    Returns:
        The registered auth-session state value.
    """
    sessions = auth_session_store.for_run(run_id)
    assert len(sessions) == 1
    return sessions[0].state


def _wait_for_participant_action(run_id: str) -> str:
    """Wait until a browser PSU participant action is available.

    Args:
        run_id: Run whose action should become pending.

    Returns:
        Raw PSU authorisation URL from the participant action.
    """
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        action = run_store.get_participant_action(run_id)
        if action is not None:
            return action.url
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for participant action")


def _wait_for_terminal_run(run_id: str) -> None:
    """Wait until a run reaches a terminal state.

    Args:
        run_id: Run whose status should become completed or failed.
    """
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        record = run_store.get_run(run_id)
        if record is not None and record.status in {"completed", "failed"}:
            return
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for terminal run")


def _assert_time_elements_use_local_datetime_contract(content: str) -> None:
    """Assert all rendered ``<time>`` elements follow the local-time contract.

    Args:
        content: Rendered HTML fragment or full document.

    Raises:
        AssertionError: If no ``<time>`` tags are present or a rendered tag
            omits required local-time attributes.
    """
    time_tags = re.findall(r"<time\b[^>]*>", content)
    assert time_tags
    for tag in time_tags:
        assert "data-local-datetime" in tag
        assert 'datetime="' in tag
        assert 'data-utc-datetime="' in tag
        assert 'title="' in tag


def _fixed_utc_timestamp() -> datetime:
    """Return a deterministic UTC timestamp used by UI time-rendering tests.

    Returns:
        Fixed UTC instant used for local-time fallback assertions.
    """
    return datetime(2026, 6, 11, 9, 0, 37, tzinfo=UTC)


@pytest.mark.integration
class TestPlanBuilderUi:
    """Browser coverage for the participant plan-builder views."""

    def test_plan_builder_get_renders_input_form(self) -> None:
        """GET /plan/ renders the JSON input form."""
        response = Client().get("/plan/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Test plan builder" in content
        assert "Endpoint-first test-plan builder" in content
        assert "Field Values" in content
        assert "Derived RunPlanV2 JSON" not in content
        assert "Load or edit saved config JSON" in content
        assert "Deselect all" in content
        assert "Select mandatory" in content
        assert "Select all" in content
        assert "Advanced testData.values" in content
        assert "Save config JSON" in content
        assert "Preview discovery" in content
        assert "Launch run" in content
        assert "Show all" in content
        assert "Specification Version" in content
        assert "Config JSON" in content
        assert "Manifest JSON" not in content
        assert "RunPlanV2" not in content
        assert "testPlan" in content
        assert 'href="/health/"' in content
        assert "hx-post" not in content

    def test_plan_builder_get_exposes_staged_import_export_hooks(self) -> None:
        """GET /plan/ renders staged import, endpoint bulk, and export hooks."""
        response = Client().get("/plan/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        expected_hooks = [
            "data-staged-import-apply",
            'data-endpoint-bulk-action="deselect-all"',
            'data-endpoint-bulk-action="select-mandatory"',
            'data-endpoint-bulk-action="select-all"',
            "data-add-test-data-value",
            "data-staged-config-download",
            "data-discovery-preview",
            "data-staged-launch-run",
            "data-toggle-applicable-tests",
        ]
        for hook in expected_hooks:
            assert hook in content

    @patch("conformance.api.ui_views.get_json")
    @patch("conformance.api.ui_views.build_json_http_client")
    def test_discovery_preview_returns_curated_non_secret_metadata(
        self,
        mock_build_client: Mock,
        mock_get_json: Mock,
    ) -> None:
        """Discovery preview fetches server-side and returns only curated fields."""
        mock_client = Mock()
        mock_build_client.return_value = mock_client
        mock_get_json.return_value = JsonHttpResponse(
            url="https://auth.example.com/.well-known/openid-configuration",
            status_code=200,
            body={
                "issuer": "https://auth.example.com",
                "authorization_endpoint": "https://auth.example.com/authorize",
                "token_endpoint": "https://auth.example.com/token",
                "jwks_uri": "https://auth.example.com/jwks",
                "registration_endpoint": "https://auth.example.com/register",
                "grant_types_supported": ["authorization_code", "client_credentials", 42],
                "response_types_supported": ["code"],
                "token_endpoint_auth_methods_supported": ["tls_client_auth", "private_key_jwt"],
                "client_secret": "must-not-be-returned",  # pragma: allowlist secret
            },
        )

        response = Client().post(
            "/plan/discovery-preview/",
            data=json.dumps({"discoveryUrl": "https://auth.example.com/.well-known/openid-configuration"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()["discovery"]
        assert data == {
            "issuer": "https://auth.example.com",
            "authorizationEndpoint": "https://auth.example.com/authorize",
            "tokenEndpoint": "https://auth.example.com/token",
            "jwksUri": "https://auth.example.com/jwks",
            "registrationEndpoint": "https://auth.example.com/register",
            "grantTypesSupported": ["authorization_code", "client_credentials"],
            "responseTypesSupported": ["code"],
            "tokenEndpointAuthMethodsSupported": ["tls_client_auth", "private_key_jwt"],
        }
        mock_build_client.assert_called_once_with(timeout_seconds=5.0)
        mock_get_json.assert_called_once_with(mock_client, "https://auth.example.com/.well-known/openid-configuration")
        mock_client.close.assert_called_once_with()

    @patch("conformance.api.ui_views.get_json")
    def test_discovery_preview_rejects_non_https_url_before_fetch(self, mock_get_json: Mock) -> None:
        """Discovery preview requires HTTPS and does not fetch invalid URLs."""
        response = Client().post(
            "/plan/discovery-preview/",
            data=json.dumps({"discoveryUrl": "http://auth.example.com/.well-known/openid-configuration"}),
            content_type="application/json",
        )

        assert response.status_code == 400
        assert "HTTPS" in response.json()["error"]
        mock_get_json.assert_not_called()

    def test_staged_template_scopes_discovery_prefill_to_current_specification(self) -> None:
        """Discovery prefill only writes runtime fields relevant to the selected specification."""
        template_path = Path("conformance/api/templates/conformance/plan_builder.html")
        template = template_path.read_text(encoding="utf-8")

        assert "const isDcrTarget = state.specification === 'dynamic-client-registration';" in template
        assert "if (!isDcrTarget) prefillEmptyField('oauth.authorizationBaseUrl'" in template
        assert re.search(
            r"if \(isDcrTarget\) \{\s+prefillEmptyField\('dcr\.tokenEndpointAuthMethod', preferredMethod\);"
            r"\s+\} else \{\s+prefillEmptyField\('fapiSigning\.tokenEndpointAuthMethod', preferredMethod\);",
            template,
        )
        assert "validateDcrSectionMatchesTarget(importedConfig);" in template

    @patch("conformance.api.ui_views.get_json")
    @patch("conformance.api.ui_views.build_json_http_client")
    def test_discovery_preview_surfaces_fetch_errors(
        self,
        mock_build_client: Mock,
        mock_get_json: Mock,
    ) -> None:
        """Discovery preview returns an inline error when the server-side fetch fails."""
        mock_client = Mock()
        mock_build_client.return_value = mock_client
        mock_get_json.side_effect = JsonHttpClientError("Request failed")

        response = Client().post(
            "/plan/discovery-preview/",
            data=json.dumps({"discoveryUrl": "https://auth.example.com/.well-known/openid-configuration"}),
            content_type="application/json",
        )

        assert response.status_code == 502
        assert "Discovery fetch failed" in response.json()["error"]
        mock_client.close.assert_called_once_with()

    def test_preview_post_renders_step_selection_table(self) -> None:
        """POST /plan/preview/ renders selectable step rows in the tree UI."""
        manifest = _v1_manifest(
            [
                _http_step("mandatory", mandatory=True, phase="setup", group="bootstrap"),
                _http_step("optional", optional=True, phase="execution", group="accounts"),
            ]
        )

        response = Client().post("/plan/preview/", data=_plan_form_data(manifest, selected_step_ids=["mandatory"]))

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "UI manifest" in content
        assert "Step mandatory" in content
        assert "Step optional" in content
        assert "Mandatory" in content
        assert "Optional" in content
        assert "setup" in content
        assert "execution" in content
        assert "bootstrap" in content
        assert "accounts" in content
        assert "data-plan-tree" in content
        assert "data-plan-step-row" in content
        assert "hx-post" not in content

    def test_preview_post_renders_bulk_selection_controls_and_metadata(self) -> None:
        """Preview renders bulk button hooks and mandatory metadata for each step row."""
        manifest = _v1_manifest(
            [
                _http_step("mandatory", mandatory=True, phase="setup", group="bootstrap"),
                _http_step("optional", optional=True, phase="execution", group="accounts"),
            ]
        )

        response = Client().post("/plan/preview/", data=_plan_form_data(manifest, selected_step_ids=["mandatory"]))

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "select all" in content
        assert "select mandatory only" in content
        assert "deselect all" in content
        assert 'data-plan-bulk-action="select-all"' in content
        assert 'data-plan-bulk-action="select-mandatory-only"' in content
        assert 'data-plan-bulk-action="deselect-all"' in content
        assert 'data-plan-step-row data-step-id="mandatory" data-step-mandatory="true"' in content
        assert 'data-plan-step-row data-step-id="optional" data-step-mandatory="false"' in content
        assert 'data-plan-step-checkbox data-step-id="mandatory" data-step-mandatory="true"' in content
        assert 'data-plan-step-checkbox data-step-id="optional" data-step-mandatory="false"' in content

    def test_preview_post_renders_chevron_and_separated_controls(self) -> None:
        """Preview renders chevrons, node wrappers, and header controls.

        The plan tree nodes must not use ``<details>``/``<summary>`` elements;
        the page-level Advanced section may use them for the collapsible import
        panel, which is expected and intentional.
        """
        manifest = _v1_manifest(
            [
                _http_step("mandatory", mandatory=True, phase="setup", group="bootstrap"),
                _http_step("optional", optional=True, phase="execution", group="accounts"),
            ]
        )

        response = Client().post("/plan/preview/", data=_plan_form_data(manifest, selected_step_ids=["mandatory"]))

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "data-plan-tree-chevron" in content
        assert "data-plan-tree-node" in content
        assert "data-node-count" in content
        # The plan-tree nodes must NOT use <details>/<summary>; the page-level
        # Advanced / Import JSON collapsible section does use <details> intentionally.
        assert 'class="tv-step"' not in content
        assert "tree-group-header" in content
        assert "aria-expanded" in content
        assert "tree-header-content" in content
        assert "tree-group-name" in content
        assert "tree-step-name" in content
        # Root groups start expanded
        assert 'aria-expanded="true"' in content
        assert "tree-group-header--open" in content
        # Nested groups start collapsed
        assert 'aria-expanded="false"' in content
        # CSS [hidden] override ensures hidden attribute works over display:flex
        assert "[hidden]" in content
        # JS uses node-local targeting
        assert "closest('[data-plan-tree-node]')" in content

    def test_preview_post_tree_layout_has_left_aligned_header_content_wrapper(self) -> None:
        """Preview renders tree-header-content wrapper div for left-aligned chevron and label."""
        manifest = _v1_manifest(
            [
                _http_step("mandatory", mandatory=True, phase="setup", group="bootstrap"),
                _http_step("optional", optional=True, phase="execution", group="accounts"),
            ]
        )

        response = Client().post("/plan/preview/", data=_plan_form_data(manifest, selected_step_ids=["mandatory"]))

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "tree-header-content" in content
        assert "tree-group-name" in content
        assert "tree-counts" in content

    def test_preview_post_tree_nested_groups_render_closed_and_root_groups_open(self) -> None:
        """Nested tree groups start closed; root groups start open; JS uses node-local targeting."""
        manifest = _v1_manifest(
            [
                _http_step("step-a", mandatory=True, phase="setup", group="groupA"),
                _http_step("step-b", optional=True, phase="execution", group="groupB"),
            ]
        )

        response = Client().post("/plan/preview/", data=_plan_form_data(manifest, selected_step_ids=["step-a"]))

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        # Root groups are open
        assert 'aria-expanded="true"' in content
        assert "tree-group-header--open" in content
        # CSS ensures the hidden attribute hides display:flex containers
        assert "[hidden]" in content
        # JavaScript uses node-local targeting for robustness
        assert "closest('[data-plan-tree-node]')" in content

    def test_preview_post_test_value_test_cases_are_the_only_nested_collapsible_boxes(self) -> None:
        """Test Values renders static surfaces inside closed test case boxes."""
        step = _http_step("payment-consent", mandatory=True)
        step["request"] = {
            "method": "POST",
            "url": "https://example.com/payments",
            "body": {
                "encoding": "json",
                "value": {"Data": {"Initiation": {"Amount": "${testValues.amount}"}}},
            },
        }
        manifest = _v1_manifest([step])
        manifest["testValues"] = {
            "baseline": {"amount": "1.00"},
            "allowedCustomKeys": ["amount"],
        }

        response = Client().post(
            "/plan/preview/",
            data=_plan_form_data(manifest, selected_step_ids=["payment-consent"]),
        )

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert '<h3 id="test-values-heading">Test Values</h3>' in content
        assert "tv-section-collapsible" not in content
        assert '<details class="tv-step">' in content
        assert not re.search(r'<details class="tv-step"\s+open', content)
        assert '<details class="tv-surface"' not in content
        assert '<div class="tv-surface">' in content
        assert '<div class="tv-surface-heading">Body</div>' in content
        assert "Amount" in content

    @pytest.mark.parametrize(
        ("selected_step_ids", "expected_checked_ids", "expected_mandatory_off_count", "expected_eligible_message"),
        [
            (["mandatory", "default", "optional"], ["mandatory", "default", "optional"], 0, True),
            (["mandatory"], ["mandatory"], 0, True),
            ([], [], 1, False),
        ],
    )
    def test_preview_post_select_mode_reflects_bulk_selection_end_states(
        self,
        selected_step_ids: list[str],
        expected_checked_ids: list[str],
        expected_mandatory_off_count: int,
        expected_eligible_message: bool,
    ) -> None:
        """Preview reflects all, mandatory-only, and empty select-mode submissions."""
        manifest = _v1_manifest(
            [
                _http_step("mandatory", mandatory=True, phase="setup", group="bootstrap"),
                _http_step("default", phase="execution", group="accounts"),
                _http_step("optional", optional=True, phase="execution", group="accounts"),
            ]
        )

        response = Client().post(
            "/plan/preview/",
            data=_plan_form_data(manifest, selected_step_ids=selected_step_ids, selection_mode="select"),
        )

        assert response.status_code == 200
        content = response.content.decode("utf-8")

        for step_id in ["mandatory", "default", "optional"]:
            checked_fragment = f'value="{step_id}" checked'
            if step_id in expected_checked_ids:
                assert checked_fragment in content
            else:
                assert checked_fragment not in content

        assert f"data-plan-count-selected>{len(expected_checked_ids)}<" in content
        assert f"data-plan-count-mandatory-off>{expected_mandatory_off_count}<" in content

        if expected_eligible_message:
            assert (
                '<div class="message success" data-plan-certification-message>Certification selection eligible</div>'
            ) in content
        else:
            assert (
                '<div class="message warning" data-plan-certification-message>Certification selection ineligible</div>'
            ) in content

    def test_preview_post_returns_400_for_invalid_manifest(self) -> None:
        """Invalid manifest submissions render form errors with HTTP 400."""
        response = Client().post(
            "/plan/preview/",
            data=_plan_form_data({"schemaVersion": "v1", "name": "Broken", "steps": []}),
        )

        assert response.status_code == 400
        assert "Manifest validation failed" in response.content.decode("utf-8")

    @patch("conformance.api.ui_views.start_run")
    def test_launch_post_starts_run_and_redirects_to_detail(self, mock_start_run: Mock) -> None:
        """Launch validates the form and hands selected plan state to lifecycle code."""
        mock_start_run.return_value = {"id": "run-123", "status": "pending", "createdAt": "2026-06-03T12:00:00+00:00"}
        manifest = _v1_manifest([_http_step("mandatory", mandatory=True), _http_step("optional", optional=True)])

        response = Client().post("/plan/launch/", data=_plan_form_data(manifest, selected_step_ids=["mandatory"]))

        assert response.status_code == 302
        assert response["Location"] == "/runs/run-123/"
        assert mock_start_run.call_count == 1
        plan = mock_start_run.call_args.kwargs["plan"]
        assert mock_start_run.call_args.kwargs["browser_psu_prompts"] is True
        assert mock_start_run.call_args.kwargs["launch_test_data_values"] == {}
        assert plan.selected_step_ids() == ["mandatory"]

    @patch("conformance.api.ui_views.start_run")
    def test_staged_launch_post_routes_read_write_config_to_lifecycle(self, mock_start_run: Mock) -> None:
        """Staged launch posts enhanced config JSON through target-derived Read/Write routing."""
        mock_start_run.return_value = {"id": "staged-run", "status": "pending"}

        response = Client().post(
            "/plan/staged/launch/",
            data=json.dumps(TARGET_CONFIG),
            content_type="application/json",
        )

        assert response.status_code == 201
        assert response.json() == {
            "runId": "staged-run",
            "status": "pending",
            "redirectUrl": "/runs/staged-run/",
        }
        assert mock_start_run.call_count == 1
        assert mock_start_run.call_args.kwargs["browser_psu_prompts"] is True
        assert mock_start_run.call_args.kwargs["compiled_run"] is not None

    def test_staged_launch_post_blocks_test_plan_catalogue_hash_drift(self) -> None:
        """Staged browser launch refuses stale saved testPlan catalogue hashes."""
        config: dict[str, JsonValue] = {
            **VALID_CONFIG,
            "testPlan": {
                "target": {
                    "standard": "obl",
                    "specification": "read-write",
                    "securityProfile": "fapi1-advanced",
                    "specificationVersion": "v4.0.1",
                    "catalogueHash": "sha256:definitely-not-the-live-hash",
                },
                "resourceGroups": ["ais"],
            },
        }

        response = Client().post(
            "/plan/staged/launch/",
            data=json.dumps(config),
            content_type="application/json",
        )

        assert response.status_code == 400
        assert "Catalogue drift detected" in response.json()["error"]

    def test_staged_launch_post_rejects_dcr_section_for_read_write_target(self) -> None:
        """Staged launch reports DCR sections as invalid for Read/Write test plans."""
        config: dict[str, JsonValue] = {
            **TARGET_CONFIG,
            "dcr": {"tokenEndpointAuthMethod": "tls_client_auth"},
        }

        response = Client().post(
            "/plan/staged/launch/",
            data=json.dumps(config),
            content_type="application/json",
        )

        assert response.status_code == 400
        error = response.json()["error"]
        assert "top-level 'dcr' section" in error
        assert "read-write" in error

    @patch("conformance.api.ui_views.start_dcr_run")
    def test_staged_launch_post_routes_dcr_config_to_dcr_lifecycle(self, mock_start_dcr_run: Mock) -> None:
        """Staged launch dispatches DCR targets to the DCR run lifecycle."""
        mock_start_dcr_run.return_value = {"id": "dcr-run", "status": "pending"}

        response = Client().post(
            "/plan/staged/launch/",
            data=json.dumps(DCR_TARGET_CONFIG),
            content_type="application/json",
        )

        assert response.status_code == 201
        assert response.json()["redirectUrl"] == "/runs/dcr-run/"
        assert mock_start_dcr_run.call_count == 1
        assert mock_start_dcr_run.call_args.kwargs["compiled_run"] is not None

    def test_staged_launch_post_returns_inline_validation_errors(self) -> None:
        """Staged launch returns JSON validation errors for incomplete configs."""
        response = Client().post(
            "/plan/staged/launch/",
            data=json.dumps({"environment": "test-env"}),
            content_type="application/json",
        )

        assert response.status_code == 400
        assert "Config validation failed" in response.json()["error"]

    @patch("conformance.api.ui_views.start_run")
    def test_staged_launch_post_returns_active_run_conflict(self, mock_start_run: Mock) -> None:
        """Staged launch surfaces active-run conflicts as inline JSON."""
        mock_start_run.side_effect = RunConflictError("active-run")

        response = Client().post(
            "/plan/staged/launch/",
            data=json.dumps(TARGET_CONFIG),
            content_type="application/json",
        )

        assert response.status_code == 409
        assert response.json() == {
            "error": "A run is already active",
            "activeRunId": "active-run",
            "activeRunUrl": "/runs/active-run/",
        }

    @patch("conformance.api.ui_views.start_run")
    def test_launch_post_passes_preview_test_data_values_to_lifecycle(self, mock_start_run: Mock) -> None:
        """Launch forwards preview-normalised ``testData.values`` to ``start_run``."""
        mock_start_run.return_value = {"id": "run-123", "status": "pending", "createdAt": "2026-06-03T12:00:00+00:00"}
        manifest = _v1_manifest(
            [
                {
                    "id": "step-1",
                    "name": "Step 1",
                    "request": {
                        "method": "POST",
                        "url": "https://example.com/payments",
                        "body": {
                            "encoding": "json",
                            "value": {"creditor": "${testValues.creditorName}"},
                        },
                    },
                    "assertions": [{"type": "http_status", "expected": 200}],
                }
            ]
        )
        manifest["testValues"] = cast(
            "JsonValue",
            {
                "baseline": {"creditorName": "Baseline Creditor"},
                "allowedCustomKeys": ["creditorName"],
            },
        )
        form_data = _plan_form_data(manifest, selected_step_ids=["step-1"])
        form_data["custom_tv_creditorName"] = "Custom Creditor"
        form_data["exploratory_ack"] = "on"

        response = Client().post("/plan/launch/", data=form_data)

        assert response.status_code == 302
        assert response["Location"] == "/runs/run-123/"
        assert mock_start_run.call_count == 1
        assert mock_start_run.call_args.kwargs["launch_test_data_values"] == {
            "creditorName": "Custom Creditor",
        }

    @patch("conformance.api.ui_views.start_run")
    def test_launch_post_starts_manual_psu_manifest(self, mock_start_run: Mock) -> None:
        """Manual PSU manifests can be launched with browser PSU prompts enabled."""
        mock_start_run.return_value = {"id": "run-psu", "status": "pending", "createdAt": "2026-06-03T12:00:00+00:00"}
        manifest = _v1_manifest([_manual_psu_step("psu"), _http_step("token")])

        response = Client().post("/plan/launch/", data=_plan_form_data(manifest, selected_step_ids=["psu", "token"]))

        assert response.status_code == 302
        assert response["Location"] == "/runs/run-psu/"
        assert mock_start_run.call_count == 1
        plan = mock_start_run.call_args.kwargs["plan"]
        assert mock_start_run.call_args.kwargs["browser_psu_prompts"] is True
        assert plan.selected_step_ids() == ["psu", "token"]

    @patch("conformance.api.ui_views.start_run")
    def test_launch_post_renders_conflict_when_run_is_active(self, mock_start_run: Mock) -> None:
        """Active-run conflicts render HTTP 409 with a detail-page link."""
        mock_start_run.side_effect = RunConflictError("active-run")
        manifest = _v1_manifest([_http_step("standard")])

        response = Client().post("/plan/launch/", data=_plan_form_data(manifest, selected_step_ids=["standard"]))

        assert response.status_code == 409
        content = response.content.decode("utf-8")
        assert "A run is already active" in content
        assert "/runs/active-run/" in content

    def test_post_views_are_csrf_protected(self) -> None:
        """Browser POST routes require the Django CSRF token when enforcement is enabled."""
        client = Client(enforce_csrf_checks=True)
        manifest = _v1_manifest([_http_step("standard")])

        rejected = client.post("/plan/preview/", data=_plan_form_data(manifest, selected_step_ids=["standard"]))

        assert rejected.status_code == 403

        discovery_rejected = client.post(
            "/plan/discovery-preview/",
            data=json.dumps({"discoveryUrl": "https://auth.example.com/.well-known/openid-configuration"}),
            content_type="application/json",
        )
        assert discovery_rejected.status_code == 403

        staged_launch_rejected = client.post(
            "/plan/staged/launch/",
            data=json.dumps(TARGET_CONFIG),
            content_type="application/json",
        )
        assert staged_launch_rejected.status_code == 403

        get_response = client.get("/plan/")
        csrf_token = get_response.cookies["csrftoken"].value
        accepted = client.post(
            "/plan/preview/",
            data={**_plan_form_data(manifest, selected_step_ids=["standard"]), "csrfmiddlewaretoken": csrf_token},
        )
        assert accepted.status_code == 200

        with (
            patch("conformance.api.ui_views.get_json") as mock_get_json,
            patch(
                "conformance.api.ui_views.build_json_http_client",
            ) as mock_build_client,
        ):
            mock_client = Mock()
            mock_build_client.return_value = mock_client
            mock_get_json.return_value = JsonHttpResponse(
                url="https://auth.example.com/.well-known/openid-configuration",
                status_code=200,
                body={"issuer": "https://auth.example.com"},
            )
            discovery_accepted = client.post(
                "/plan/discovery-preview/",
                data=json.dumps(
                    {
                        "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                        "csrfmiddlewaretoken": csrf_token,
                    }
                ),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=csrf_token,
            )
        assert discovery_accepted.status_code == 200

        with patch("conformance.api.ui_views.start_run") as mock_staged_start_run:
            mock_staged_start_run.return_value = {"id": "csrf-run", "status": "pending"}
            staged_launch_accepted = client.post(
                "/plan/staged/launch/",
                data=json.dumps(TARGET_CONFIG),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=csrf_token,
            )
        assert staged_launch_accepted.status_code == 201

    @patch("conformance.api.ui_views.start_run")
    def test_launch_post_is_csrf_protected(self, mock_start_run: Mock) -> None:
        """Launch POST rejects missing CSRF tokens and accepts token-backed submissions."""
        mock_start_run.return_value = {"id": "run-123", "status": "pending", "createdAt": "2026-06-03T12:00:00+00:00"}
        client = Client(enforce_csrf_checks=True)
        manifest = _v1_manifest([_http_step("standard")])

        rejected = client.post("/plan/launch/", data=_plan_form_data(manifest, selected_step_ids=["standard"]))

        assert rejected.status_code == 403
        mock_start_run.assert_not_called()

        get_response = client.get("/plan/")
        csrf_token = get_response.cookies["csrftoken"].value
        accepted = client.post(
            "/plan/launch/",
            data={**_plan_form_data(manifest, selected_step_ids=["standard"]), "csrfmiddlewaretoken": csrf_token},
        )

        assert accepted.status_code == 302
        assert accepted["Location"] == "/runs/run-123/"
        assert mock_start_run.call_count == 1

    def test_launch_post_persists_only_selected_steps_on_run_snapshot(self) -> None:
        """Browser launch snapshots only selected steps for live progress rows."""
        client = Client()
        manifest = _v1_manifest(
            [
                _http_step("keep", mandatory=True),
                _http_step("drop", optional=True),
            ]
        )

        response = client.post(
            "/plan/launch/",
            data=_plan_form_data(manifest, selected_step_ids=["keep"]),
        )

        assert response.status_code == 302
        run_id = _run_id_from_redirect(response["Location"])
        record = run_store.get_run(run_id)
        assert record is not None
        assert [step.step_id for step in record.planned_steps] == ["keep"]


@pytest.mark.integration
class TestRunDetailUi:
    """Browser coverage for run detail and partial views."""

    def test_browser_launch_manual_psu_flow_renders_callback_and_clears_action(self) -> None:
        """Browser launch renders manual PSU action until callback completion."""
        client = Client()
        raw_request_object = "ui-request-object-raw-value"
        raw_auth_code = "ui-auth-code-123"
        manifest = _v1_manifest([_manual_psu_step_with_request_object("psu", request_object=raw_request_object)])

        launch_response = client.post("/plan/launch/", data=_plan_form_data(manifest, selected_step_ids=["psu"]))

        assert launch_response.status_code == 302
        run_id = _run_id_from_redirect(launch_response["Location"])
        authorisation_url = _wait_for_participant_action(run_id)
        state = _registered_auth_state(run_id)
        assert _query_parameter(authorisation_url, "request") == raw_request_object

        detail_response = client.get(f"/runs/{run_id}/")
        status_response = client.get(f"/runs/{run_id}/status/")

        assert detail_response.status_code == 200
        assert status_response.status_code == 200
        detail_content = html.unescape(detail_response.content.decode("utf-8"))
        status_content = html.unescape(status_response.content.decode("utf-8"))
        assert "Authorisation actions" in detail_content
        assert "Step psu is waiting for PSU authorisation." in status_content
        assert "Pending" in status_content
        assert f'href="{authorisation_url}"' in status_content
        assert "data-psu-authorisation-popup" in status_content
        assert 'document.addEventListener("click", (event) => {' in detail_content
        assert 'event.target.closest("a[data-psu-authorisation-popup]")' in detail_content
        assert "event.preventDefault();" in detail_content
        assert 'window.open(href, "_blank", popupFeatures.join(","))' in detail_content
        assert "if (!popupWindow) return;" not in detail_content

        callback_response = client.get("/callback/", {"state": state, "code": raw_auth_code})

        assert callback_response.status_code == 200
        _wait_for_terminal_run(run_id)
        assert run_store.get_participant_action(run_id) is None
        cleared_response = client.get(f"/runs/{run_id}/status/")
        assert cleared_response.status_code == 200
        cleared_content = cleared_response.content.decode("utf-8")
        assert "Authorisation actions" not in cleared_content
        assert "Open authorisation" not in cleared_content

        log_response = client.get(f"/runs/{run_id}/log.json")
        result_response = client.get(f"/runs/{run_id}/result.json")

        assert log_response.status_code == 200
        assert result_response.status_code == 200
        log_bytes = log_response.content
        result_json = json.dumps(result_response.json(), sort_keys=True)
        assert authorisation_url.encode("utf-8") not in log_bytes
        assert raw_request_object.encode("utf-8") not in log_bytes
        assert raw_auth_code.encode("utf-8") not in log_bytes
        assert authorisation_url not in result_json
        assert raw_request_object not in result_json
        assert raw_auth_code not in result_json

    def test_run_detail_returns_404_for_unknown_run(self) -> None:
        """Unknown run ids render a browser 404."""
        response = Client().get("/runs/missing/")

        assert response.status_code == 404
        assert "Run not found" in response.content.decode("utf-8")

    def test_run_detail_renders_pending_status_and_links(self) -> None:
        """Known pending runs render status, log, and result panels."""
        record = run_store.create_run()
        assert record.execution_logger is not None
        record.execution_logger.emit("run-started")

        response = Client().get(f"/runs/{record.run_id}/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert f"Run {record.run_id}" in content
        assert "pending" in content
        assert '<meta name="conformance-live-polling-enabled" content="true">' in content
        assert '<meta http-equiv="refresh" content="2">' in content
        assert f"/runs/{record.run_id}/log.json" in content
        assert "Result pending" in content
        assert 'event.target.closest("a[data-psu-authorisation-popup]")' in content
        assert "event.preventDefault();" in content
        assert 'window.open(href, "_blank", popupFeatures.join(","))' in content
        assert "event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey" in content
        assert "if (!popupWindow) return;" not in content

    def test_run_detail_renders_step_progress_panel_and_preserves_step_state(self) -> None:
        """Pending run detail pages include selected-step panel and state preservation script."""
        record = run_store.create_run(
            planned_steps=(
                RunPlanStep(
                    step_id="discovery",
                    name="Discovery",
                    kind="http",
                    group="setup",
                    phase="setup",
                    mandatory=True,
                    optional=False,
                    order=0,
                ),
            )
        )

        response = Client().get(f"/runs/{record.run_id}/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert 'id="run-steps"' in content
        assert "Step Progress and Results" in content
        assert f"/runs/{record.run_id}/steps/" in content
        assert 'data-step-id="discovery"' in content
        assert "sessionStorage.setItem(STEP_STATE_STORAGE_KEY" in content
        assert 'const terminalStatuses = new Set(["completed", "failed"]);' in content
        assert "sessionStorage.removeItem(STEP_STATE_STORAGE_KEY)" in content
        assert 'window.addEventListener("pagehide", captureStepStates)' in content

    def test_run_detail_includes_local_datetime_rendering_hooks(self) -> None:
        """Run detail pages localise timestamp elements after load and HTMX swaps."""
        record = run_store.create_run()

        response = Client().get(f"/runs/{record.run_id}/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        _assert_time_elements_use_local_datetime_contract(content)
        assert "function localTimeZoneShortName(date)" in content
        assert "function formatLocalDatetime(date)" in content
        assert "pad2(date.getDate())" in content
        assert "dd/mm" not in content.lower()
        assert 'timeZoneName: "short"' in content
        assert 'node.setAttribute("data-local-datetime-rendered", "true");' in content
        assert "function runStartupTask(task)" in content
        assert "runStartupTask(() => renderLocalDatetimes(document));" in content
        assert 'document.body.addEventListener("htmx:afterSwap", (event) => {' in content
        assert "runStartupTask(() => scheduleParticipantActionDeadlineRefresh());" in content
        assert "runStartupTask(() => waitForParticipantActionUpdate());" in content

    def test_run_detail_renders_bst_created_timestamp_fallback(self) -> None:
        """Run detail shows Created fallback text in Open Banking UK local time."""
        record = run_store.create_run()
        record.created_at = _fixed_utc_timestamp()

        response = Client().get(f"/runs/{record.run_id}/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Created" in content
        assert "11/06/2026 10:00:37 BST" in content
        _assert_time_elements_use_local_datetime_contract(content)

    def test_run_detail_does_not_refresh_terminal_runs(self) -> None:
        """Completed run detail pages should not keep refreshing."""
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_completed(record.run_id, result={"status": "passed"})

        response = Client().get(f"/runs/{record.run_id}/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert '<meta name="conformance-page-auto-refresh-enabled" content="false">' in content
        assert '<meta name="conformance-live-polling-enabled" content="false">' in content
        assert '<meta http-equiv="refresh" content="2">' not in content

    def test_run_detail_disables_live_polling_flag_while_psu_authorisation_is_pending(self) -> None:
        """Awaiting PSU authorisation should refresh the page but pause panel intervals."""
        record = run_store.create_run()
        run_store.set_participant_action(
            record.run_id,
            step_id="psu",
            url="https://auth.example.com/authorize?state=state-123",
        )

        response = Client().get(f"/runs/{record.run_id}/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert '<meta name="conformance-page-auto-refresh-enabled" content="true">' in content
        assert '<meta name="conformance-live-polling-enabled" content="false">' in content
        assert '<meta http-equiv="refresh" content="2">' in content
        assert 'hx-trigger="load"' in content
        assert 'hx-trigger="load, every 2s"' not in content

    def test_run_detail_renders_participant_action_deadline_meta_when_present(self) -> None:
        """Awaiting PSU actions with deadlines should expose a wake-up timestamp."""
        record = run_store.create_run()
        expires_at = datetime.now(UTC) + timedelta(seconds=30)
        run_store.set_participant_action(
            record.run_id,
            step_id="psu",
            url="https://auth.example.com/authorize?state=state-123",
            expires_at=expires_at,
        )

        response = Client().get(f"/runs/{record.run_id}/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert '<meta name="conformance-participant-action-deadline"' in content
        assert expires_at.isoformat() in content

    def test_run_detail_includes_participant_action_deadline_wake_up_hook(self) -> None:
        """Run detail page schedules one-shot refresh when polling is paused."""
        record = run_store.create_run()

        response = Client().get(f"/runs/{record.run_id}/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "function scheduleParticipantActionDeadlineRefresh" in content
        assert "conformance-participant-action-deadline" in content
        assert "refreshRunPanels();" in content

    def test_run_detail_includes_backend_wait_hook(self) -> None:
        """Run detail page should expose the backend wait endpoint hook."""
        record = run_store.create_run()

        response = Client().get(f"/runs/{record.run_id}/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "function waitForParticipantActionUpdate" in content
        assert "conformance-participant-action-wait-url" in content
        assert "conformance-run-version" in content
        assert 'data-run-version="' in content
        assert 'url.searchParams.set("since", runVersion)' in content

    def test_run_wait_returns_updated_snapshot_after_participant_action_clears(self) -> None:
        """The wait endpoint should wake when the pending PSU action clears."""
        record = run_store.create_run()
        run_store.set_participant_action(
            record.run_id,
            step_id="psu",
            url="https://auth.example.com/authorize?state=awaiting-wait",
        )

        def clear_action() -> None:
            time.sleep(0.05)
            run_store.clear_participant_action(record.run_id, step_id="psu")

        worker = threading.Thread(target=clear_action, daemon=True)
        worker.start()

        response = Client().get(f"/runs/{record.run_id}/wait/", {"timeout": "1.0"})

        worker.join(timeout=1.0)
        assert response.status_code == 200
        body = response.json()
        assert body["changed"] is True
        assert body["run"]["id"] == record.run_id
        assert body["run"]["status"] == "pending"

    def test_run_wait_wakes_after_callback_capture_clears_participant_action(self) -> None:
        """Callback capture should wake waiters by clearing pending PSU action state."""
        record = run_store.create_run()
        run_store.set_participant_action(
            record.run_id,
            step_id="psu",
            url="https://auth.example.com/authorize?state=awaiting-callback",
        )
        session = auth_session_store.register(record.run_id)

        def capture_callback() -> None:
            time.sleep(0.05)
            Client().get("/callback/", {"state": session.state, "code": "auth-code-xyz"})

        worker = threading.Thread(target=capture_callback, daemon=True)
        worker.start()

        response = Client().get(f"/runs/{record.run_id}/wait/", {"timeout": "1.0"})

        worker.join(timeout=1.0)
        assert response.status_code == 200
        body = response.json()
        assert body["changed"] is True
        assert body["run"]["id"] == record.run_id
        assert run_store.get_participant_action(record.run_id) is None

    def test_run_wait_returns_immediately_when_rendered_version_is_stale_after_callback(self) -> None:
        """The wait endpoint should not miss callbacks that completed before the wait request."""
        record = run_store.create_run()
        run_store.set_participant_action(
            record.run_id,
            step_id="psu",
            url="https://auth.example.com/authorize?state=awaiting-callback",
        )
        rendered_record = run_store.get_run(record.run_id)
        assert rendered_record is not None
        session = auth_session_store.register(record.run_id)
        client = Client()

        callback_response = client.get("/callback/", {"state": session.state, "code": "auth-code-xyz"})
        response = client.get(
            f"/runs/{record.run_id}/wait/",
            {"timeout": "0", "since": str(rendered_record.version)},
        )

        assert callback_response.status_code == 200
        assert response.status_code == 200
        body = response.json()
        assert body["changed"] is True
        assert body["run"]["id"] == record.run_id
        assert run_store.get_participant_action(record.run_id) is None

    def test_run_wait_returns_204_when_timeout_expires(self) -> None:
        """The wait endpoint should fall back to 204 when nothing changes."""
        record = run_store.create_run()

        response = Client().get(f"/runs/{record.run_id}/wait/", {"timeout": "0.01"})

        assert response.status_code == 204
        assert response.content == b""

    def test_run_detail_contains_psu_callback_message_listener(self) -> None:
        """Run detail page must include message listener that reacts to PSU callback notification."""
        record = run_store.create_run()

        response = Client().get(f"/runs/{record.run_id}/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "conformance:psu-callback" in content
        assert 'addEventListener("message"' in content
        assert "function isTrustedCallbackOrigin(origin)" in content
        assert "sender.origin === current.origin" in content
        assert "sender.protocol !== current.protocol || sender.port !== current.port" in content
        assert '["localhost", "127.0.0.1", "0.0.0.0", "::1"].includes(hostname)' in content
        assert "window.location.reload();" in content

    def test_run_detail_contains_refresh_run_panels_helper(self) -> None:
        """Run detail page must include refreshRunPanels helper function."""
        record = run_store.create_run()

        response = Client().get(f"/runs/{record.run_id}/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "function refreshRunPanels" in content
        assert "function fetchAndSwapRunPanel" in content
        assert 'fetch(panelUrl, { credentials: "same-origin" })' in content
        assert "panel.replaceWith(replacement);" in content
        assert "return Promise.all(refreshes).then" in content
        assert 'panel.getAttribute("hx-get")' in content
        assert 'htmx.trigger(el, "load")' not in content
        assert "htmxApi.ajax" not in content
        assert "window.location.reload" in content

    def test_status_partial_renders_current_timestamps(self) -> None:
        """The status partial renders the current run snapshot."""
        record = run_store.create_run()
        run_store.mark_running(record.run_id)

        response = Client().get(f"/runs/{record.run_id}/status/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "running" in content
        assert "Started" in content
        assert 'hx-trigger="load, every 2s"' in content
        _assert_time_elements_use_local_datetime_contract(content)

    def test_status_partial_disables_interval_polling_while_awaiting_psu_authorisation(self) -> None:
        """Awaiting PSU authorisation should disable status-panel interval polling."""
        record = run_store.create_run()
        run_store.set_participant_action(
            record.run_id,
            step_id="psu",
            url="https://auth.example.com/authorize?state=awaiting",
        )

        response = Client().get(f"/runs/{record.run_id}/status/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert 'hx-trigger="load"' in content
        assert "every 2s" not in content

    def test_status_partial_renders_bst_started_timestamp_fallback(self) -> None:
        """Status partial shows Started fallback text in Open Banking UK local time."""
        record = run_store.create_run()
        record.created_at = _fixed_utc_timestamp()
        run_store.mark_running(record.run_id)
        record.started_at = _fixed_utc_timestamp()

        response = Client().get(f"/runs/{record.run_id}/status/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Started" in content
        assert "11/06/2026 10:00:37 BST" in content
        _assert_time_elements_use_local_datetime_contract(content)

    def test_steps_partial_returns_404_for_unknown_run(self) -> None:
        """Unknown run ids return browser 404 from steps partial endpoint."""
        response = Client().get("/runs/missing/steps/")

        assert response.status_code == 404
        assert "Run not found" in response.content.decode("utf-8")

    def test_steps_partial_renders_rows_and_live_evidence_for_active_run(self) -> None:
        """Steps partial renders selected rows and expandable masked evidence."""
        record = run_store.create_run(
            planned_steps=(
                RunPlanStep(
                    step_id="discovery",
                    name="Discovery",
                    kind="http",
                    group="setup",
                    phase="setup",
                    mandatory=True,
                    optional=False,
                    order=0,
                ),
            )
        )
        assert record.execution_logger is not None
        record.execution_logger.emit("step-started", step_id="discovery")
        record.execution_logger.emit(
            "request-sent",
            step_id="discovery",
            payload={"method": "GET", "url": "https://example.com/.well-known/openid-configuration"},
        )

        response = Client().get(f"/runs/{record.run_id}/steps/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert 'id="run-steps"' in content
        assert 'data-step-id="discovery"' in content
        assert "open" in content
        assert "Discovery" in content
        assert "running" in content
        assert "Live evidence" in content
        assert "request-sent" in content
        assert "Request sent - GET - https://example.com/.well-known/openid-configuration" in content
        assert 'hx-trigger="load, every 2s"' in content
        _assert_time_elements_use_local_datetime_contract(content)

    def test_steps_partial_disables_interval_polling_when_run_is_terminal(self) -> None:
        """Terminal runs render steps partial without interval polling."""
        record = run_store.create_run(
            planned_steps=(
                RunPlanStep(
                    step_id="discovery",
                    name="Discovery",
                    kind="http",
                    group="setup",
                    phase="setup",
                    mandatory=True,
                    optional=False,
                    order=0,
                ),
            )
        )
        run_store.mark_running(record.run_id)
        run_store.mark_completed(
            record.run_id,
            result={"status": "passed", "steps": [{"name": "discovery", "status": "passed"}]},
        )

        response = Client().get(f"/runs/{record.run_id}/steps/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert 'id="run-steps"' in content
        assert 'data-step-id="discovery"' in content
        assert 'hx-trigger="load"' in content
        assert "every 2s" not in content
        # Terminal rows are not forced open by the server.
        assert 'data-step-id="discovery" open>' not in content

    def test_steps_partial_disables_interval_polling_while_awaiting_psu_authorisation(self) -> None:
        """Awaiting PSU authorisation should disable steps-panel interval polling."""
        record = run_store.create_run(
            planned_steps=(
                RunPlanStep(
                    step_id="psu-step",
                    name="PSU Step",
                    kind="psu-authorization",
                    group="consent",
                    phase="execution",
                    mandatory=True,
                    optional=False,
                    order=0,
                ),
            )
        )
        run_store.set_participant_action(
            record.run_id,
            step_id="psu-step",
            url="https://auth.example.com/authorize?state=awaiting-steps",
        )

        response = Client().get(f"/runs/{record.run_id}/steps/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert 'hx-trigger="load"' in content
        assert "every 2s" not in content

    def test_status_partial_renders_pending_psu_authorisation_action(self) -> None:
        """The status partial renders active manual PSU browser actions."""
        record = run_store.create_run()
        authorisation_url = "https://auth.example.com/authorize?state=state-123"
        run_store.set_participant_action(record.run_id, step_id="psu", url=authorisation_url)

        response = Client().get(f"/runs/{record.run_id}/status/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Authorisation actions" in content
        assert "Step psu is waiting for PSU authorisation." in content
        assert "Pending" in content
        assert "Open authorisation" in content
        assert f'href="{authorisation_url}"' in content
        assert "data-psu-authorisation-popup" in content

    def test_status_partial_renders_multiple_actions_with_completion_state(self) -> None:
        """The status partial renders one link per pending action and completion labels."""
        record = run_store.create_run()
        first_url = "https://auth.example.com/authorize?state=first"
        second_url = "https://auth.example.com/authorize?state=second"
        run_store.set_participant_action(record.run_id, step_id="psu-first", url=first_url)
        run_store.set_participant_action(record.run_id, step_id="psu-second", url=second_url)
        run_store.clear_participant_action(record.run_id, step_id="psu-first")

        response = Client().get(f"/runs/{record.run_id}/status/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Authorisation actions" in content
        assert "Step psu-first is waiting for PSU authorisation." in content
        assert "Step psu-second is waiting for PSU authorisation." in content
        assert "Completed" in content
        assert "Pending" in content
        assert f'href="{second_url}"' in content
        assert f'href="{first_url}"' not in content

    def test_status_partial_omits_psu_authorisation_action_when_absent(self) -> None:
        """The status partial hides manual PSU controls when no action is pending."""
        record = run_store.create_run()

        response = Client().get(f"/runs/{record.run_id}/status/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Authorisation actions" not in content
        assert "Open authorisation" not in content
        assert 'target="_blank"' not in content

    def test_log_partial_renders_masked_log_link_and_event_count(self) -> None:
        """The log partial links to the browser-accessible JSON endpoint."""
        record = run_store.create_run()
        assert record.execution_logger is not None
        record.execution_logger.emit("run-started")
        record.execution_logger.emit("run-completed", payload={"status": "passed"})

        response = Client().get(f"/runs/{record.run_id}/log/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert ">2<" in content
        assert f"/runs/{record.run_id}/log.json" in content
        assert "Masked log" in content
        assert 'hx-trigger="load, every 2s"' in content

    def test_log_partial_disables_interval_polling_while_awaiting_psu_authorisation(self) -> None:
        """Awaiting PSU authorisation should disable log-panel interval polling."""
        record = run_store.create_run()
        run_store.set_participant_action(
            record.run_id,
            step_id="psu",
            url="https://auth.example.com/authorize?state=awaiting-log",
        )

        response = Client().get(f"/runs/{record.run_id}/log/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert 'hx-trigger="load"' in content
        assert "every 2s" not in content

    def test_log_partial_counts_events_without_serialising_log(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Rendering the log partial should count buffered events directly.

        Args:
            monkeypatch: pytest fixture used to make log serialisation fail
                if the UI summary accidentally calls it.
        """
        record = run_store.create_run()
        assert record.execution_logger is not None
        record.execution_logger.emit("run-started")
        monkeypatch.setattr(
            record.execution_logger,
            "to_json_bytes",
            Mock(side_effect=AssertionError("log count must not serialise JSON")),
        )

        response = Client().get(f"/runs/{record.run_id}/log/")

        assert response.status_code == 200
        assert ">1<" in response.content.decode("utf-8")

    def test_log_download_is_available_to_non_loopback_browser_clients(self) -> None:
        """UI log downloads should not inherit the REST API loopback guard."""
        record = run_store.create_run()
        assert record.execution_logger is not None
        record.execution_logger.emit("run-started")

        client = Client(REMOTE_ADDR="10.0.0.5")
        api_response = client.get(f"/api/runs/{record.run_id}/log/")
        ui_response = client.get(f"/runs/{record.run_id}/log.json")

        assert api_response.status_code == 403
        assert ui_response.status_code == 200
        assert ui_response["Content-Type"] == "application/json"
        assert ui_response["Content-Disposition"] == f'attachment; filename="{record.run_id}-execution-log.json"'
        assert json.loads(ui_response.content.decode("utf-8"))[0]["type"] == "run-started"

    def test_result_partial_renders_completed_summary(self) -> None:
        """The result partial summarises completed structured results."""
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_completed(
            record.run_id,
            result={
                "status": "passed",
                "summary": {"total": 2, "passed": 2, "failed": 0, "warn": 0, "skipped": 0},
                "plan": {
                    "totalSteps": 2,
                    "selectedSteps": 2,
                    "deselectedSteps": 0,
                    "mandatorySelected": 1,
                    "mandatoryDeselected": 0,
                },
                "certificationEligibility": "eligible",
            },
        )

        response = Client().get(f"/runs/{record.run_id}/result/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "passed" in content
        assert "eligible" in content
        assert "Coverage selected" in content
        assert "Warn" in content
        assert "Skipped" in content
        assert "Issues" in content
        assert "Mandatory covered" in content
        assert f"/runs/{record.run_id}/result.json" in content
        assert 'hx-trigger="load"' in content
        assert "every 2s" not in content

    def test_result_partial_polls_while_run_is_active(self) -> None:
        """Active runs keep HTMX polling enabled for the result panel."""
        record = run_store.create_run()
        run_store.mark_running(record.run_id)

        response = Client().get(f"/runs/{record.run_id}/result/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Result pending" in content
        assert 'hx-trigger="load, every 2s"' in content

    def test_result_partial_disables_interval_polling_while_awaiting_psu_authorisation(self) -> None:
        """Awaiting PSU authorisation should disable result-panel interval polling."""
        record = run_store.create_run()
        run_store.set_participant_action(
            record.run_id,
            step_id="psu",
            url="https://auth.example.com/authorize?state=awaiting-result",
        )

        response = Client().get(f"/runs/{record.run_id}/result/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Result pending" in content
        assert 'hx-trigger="load"' in content
        assert "every 2s" not in content

    def test_result_partial_renders_structured_certification_eligibility(self) -> None:
        """The result partial renders the current eligibility object shape."""
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_completed(
            record.run_id,
            result={
                "status": "failed",
                "summary": {"total": 1, "passed": 0, "failed": 1, "warn": 0, "skipped": 0},
                "certificationEligibility": {
                    "eligible": False,
                    "mandatoryTotal": 1,
                    "mandatoryPassed": 0,
                    "mandatoryFailed": 1,
                    "mandatoryWarn": 0,
                    "mandatorySkipped": 0,
                    "mandatoryDeselected": 0,
                    "mandatoryDeselectedStepIds": [],
                    "reason": "1 mandatory step(s) failed",
                },
            },
        )

        response = Client().get(f"/runs/{record.run_id}/result/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "ineligible: 1 mandatory step(s) failed" in content

    def test_result_partial_renders_readiness_report_sections(self) -> None:
        """The result partial renders endpoint-first readiness sections when present."""
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_completed(
            record.run_id,
            result={
                "status": "passed",
                "summary": {"total": 2, "passed": 2, "failed": 0, "warn": 0, "skipped": 0},
                "readinessReport": {
                    "schemaVersion": "2",
                    "targetCoordinates": {
                        "standard": "obl",
                        "specification": "read-write",
                        "securityProfile": "fapi1-advanced",
                        "specificationVersion": "v4.0.1",
                        "catalogueHash": "sha256:catalogue",
                    },
                    "catalogueHash": "sha256:catalogue",
                    "selectedCoverageSummary": {
                        "selectedResourceGroups": ["ais"],
                        "selectedEndpointCount": 1,
                        "mandatoryEndpointCount": 2,
                        "omittedMandatoryEndpointCount": 1,
                        "coverageComplete": False,
                    },
                    "overallOutcome": "incomplete",
                    "resourceGroupSections": [
                        {
                            "resourceGroup": "ais",
                            "readinessOutcome": "incomplete",
                            "omittedMandatoryEndpoints": ["account-balances"],
                            "selectedTestCount": 2,
                            "passedCount": 2,
                            "failedCount": 0,
                            "skippedCount": 0,
                            "certificationStatus": "not-ready",
                            "certificationEligible": False,
                        }
                    ],
                    "runId": record.run_id,
                    "generatedAt": "2026-01-01T00:00:00+00:00",
                },
            },
        )

        response = Client().get(f"/runs/{record.run_id}/result/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Coverage readiness" in content
        assert "incomplete" in content
        assert "not-ready" in content
        assert "Incomplete coverage:" in content
        assert "account-balances" in content

    def test_result_partial_renders_custom_test_value_impact_panel(self) -> None:
        """Result partial renders persisted custom-test-value impact evidence."""
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_completed(
            record.run_id,
            result={
                "status": "passed",
                "summary": {"total": 1, "passed": 1, "failed": 0, "warn": 0, "skipped": 0},
                "customTestValueImpact": {
                    "profileId": "ozone-demo",
                    "source": "overridden",
                    "overrideKeys": ["remittanceInformation"],
                    "overriddenValues": {
                        "remittanceInformation": {
                            "defaultValue": "***",
                            "defaultValueDisplay": {
                                "preview": "base…info (len=20)",
                                "sha256": "sha256:base",
                            },
                            "customValue": "***",
                            "customValueDisplay": {
                                "preview": "cust…info (len=18)",
                                "sha256": "sha256:used",
                            },
                            "effectiveValue": "***",
                            "effectiveValueDisplay": {
                                "preview": "cust…info (len=18)",
                                "sha256": "sha256:used",
                            },
                        }
                    },
                    "summary": {
                        "overrideKeyCount": 1,
                        "executedReferenceCount": 1,
                        "referencedButNotRunCount": 1,
                        "executedStepCount": 1,
                        "referencedButNotRunStepCount": 1,
                    },
                    "executedReferences": [
                        {
                            "stepId": "mandatory-positive",
                            "key": "remittanceInformation",
                            "requestArea": "request-json-body",
                            "fieldPath": "request.body.Data.RemittanceInformation.Unstructured[0]",
                            "status": "passed",
                        }
                    ],
                    "referencedButNotRun": [
                        {
                            "stepId": "optional-negative",
                            "key": "remittanceInformation",
                            "requestArea": "request-json-body",
                            "fieldPath": "request.body.note",
                            "notRunReason": "deselected",
                        }
                    ],
                },
            },
        )

        response = Client().get(f"/runs/{record.run_id}/result/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Test Data Customisation" in content
        assert "Custom values used in this run" in content
        assert "Hash sha256:" not in content
        assert 'class="custom-values-warning"' in content
        assert "Referenced but not run" in content
        assert "optional-negative" in content
        assert "request.body.note" in content
        # Legacy labels must not appear
        assert "Override keys" not in content
        assert "Profile" not in content

    def test_result_partial_renders_baseline_delta_impact_panel(self) -> None:
        """Result partial renders Test Data Customisation with new baseline-delta impact shape."""
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_completed(
            record.run_id,
            result={
                "status": "passed",
                "summary": {"total": 1, "passed": 1, "failed": 0, "warn": 0, "skipped": 0},
                "customTestValueImpact": {
                    "source": "custom",
                    "baselineDeltaKeys": ["remittanceInformation"],
                    "valueDetails": [
                        {
                            "key": "remittanceInformation",
                            "usedValue": "***",
                            "usedValueDisplay": {
                                "preview": "cust…info (len=18)",
                                "sha256": "sha256:used",
                            },
                            "baselineValue": "***",
                            "baselineValueDisplay": {
                                "preview": "base…info (len=20)",
                                "sha256": "sha256:base",
                            },
                            "executedReferences": [
                                {
                                    "stepId": "consent-1",
                                    "requestArea": "request-json-body",
                                    "fieldPath": "request.body.Data.Initiation.CreditorAccount.Name",
                                }
                            ],
                        }
                    ],
                    "summary": {
                        "baselineDeltaKeyCount": 1,
                        "executedReferenceCount": 2,
                        "referencedButNotRunCount": 0,
                        "executedStepCount": 2,
                        "referencedButNotRunStepCount": 0,
                    },
                    "executedReferences": [
                        {
                            "stepId": "consent-1",
                            "key": "remittanceInformation",
                            "requestArea": "request-json-body",
                            "fieldPath": "request.body.Data.Initiation.CreditorAccount.Name",
                            "status": "passed",
                        },
                        {
                            "stepId": "consent-1",
                            "key": "remittanceInformation",
                            "requestArea": "request-json-body",
                            "fieldPath": "request.body.Data.Initiation.CreditorAccount.Name",
                            "status": "passed",
                        },
                    ],
                    "referencedButNotRun": [],
                },
            },
        )

        response = Client().get(f"/runs/{record.run_id}/result/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Test Data Customisation" in content
        assert "Custom value keys" in content
        assert "Exploratory run" in content
        assert "Custom values used in this run" in content
        assert "Hash sha256:" not in content
        assert "request paths" in content
        assert "CreditorAccount" in content
        # Legacy labels must not appear
        assert "Override keys" not in content
        assert "Profile" not in content

    def test_result_partial_baseline_run_shows_baseline_values_label(self) -> None:
        """Result partial shows 'Baseline values' run type when source is 'baseline' or 'default'."""
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_completed(
            record.run_id,
            result={
                "status": "passed",
                "summary": {"total": 1, "passed": 1, "failed": 0, "warn": 0, "skipped": 0},
                "customTestValueImpact": {
                    "profileId": "ozone-demo",
                    "source": "default",
                    "overrideKeys": ["remittanceInformation"],
                    "summary": {
                        "overrideKeyCount": 1,
                        "executedReferenceCount": 1,
                        "referencedButNotRunCount": 0,
                        "executedStepCount": 1,
                        "referencedButNotRunStepCount": 0,
                    },
                    "referencedButNotRun": [],
                },
            },
        )

        response = Client().get(f"/runs/{record.run_id}/result/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Test Data Customisation" in content
        assert "Baseline values" in content

        """Completed steps partial renders final per-step details and unified context."""
        record = run_store.create_run(
            planned_steps=(
                RunPlanStep(
                    step_id="discovery",
                    name="Discovery",
                    kind="http",
                    group="setup",
                    phase="setup",
                    mandatory=True,
                    optional=False,
                    order=0,
                ),
                RunPlanStep(
                    step_id="token-request",
                    name="Token request",
                    kind="http",
                    group="execution",
                    phase="execution",
                    mandatory=False,
                    optional=True,
                    order=1,
                ),
            )
        )
        run_store.mark_running(record.run_id)
        assert record.execution_logger is not None
        record.execution_logger.emit(
            "request-sent",
            step_id="discovery",
            payload={"method": "GET", "url": "https://example.com/.well-known/openid-configuration"},
        )
        record.execution_logger.emit(
            "response-received",
            step_id="discovery",
            payload={"statusCode": 200, "url": "https://example.com/.well-known/openid-configuration"},
        )
        record.execution_logger.emit(
            "request-sent",
            step_id="token-request",
            payload={"method": "POST", "url": "https://example.com/token"},
        )
        record.execution_logger.emit(
            "response-received",
            step_id="token-request",
            payload={"statusCode": 400, "url": "https://example.com/token"},
        )
        run_store.mark_completed(
            record.run_id,
            result={
                "status": "failed",
                "summary": {"total": 2, "passed": 1, "failed": 1, "warn": 0, "skipped": 0},
                "customTestValueImpact": {
                    "profileId": "ozone-demo",
                    "source": "overridden",
                    "overrideKeys": ["creditorName", "remittanceInformation"],
                    "valueDetails": [
                        {
                            "key": "creditorName",
                            "usedValue": "Alice",
                            "usedValueDisplay": {
                                "preview": "Alic…lice (len=5)",
                                "sha256": "sha256:alice",
                            },
                            "baselineValue": "Bob",
                            "baselineValueDisplay": {
                                "preview": "Bob (len=3)",
                                "sha256": "sha256:bob",
                            },
                            "executedReferences": [
                                {
                                    "stepId": "discovery",
                                    "requestArea": "request-json-body",
                                    "fieldPath": "request.body.Data.Creditor.Name",
                                }
                            ],
                        },
                        {
                            "key": "remittanceInformation",
                            "usedValue": "***",
                            "usedValueDisplay": {
                                "preview": "cust…info (len=18)",
                                "sha256": "sha256:used",
                            },
                            "baselineValue": "***",
                            "baselineValueDisplay": {
                                "preview": "base…info (len=20)",
                                "sha256": "sha256:base",
                            },
                            "executedReferences": [
                                {
                                    "stepId": "token-request",
                                    "requestArea": "request-json-body",
                                    "fieldPath": "request.body.remittanceInformation",
                                }
                            ],
                        },
                    ],
                    "summary": {
                        "overrideKeyCount": 2,
                        "executedReferenceCount": 2,
                        "referencedButNotRunCount": 0,
                        "executedStepCount": 2,
                        "referencedButNotRunStepCount": 0,
                    },
                    "executedReferences": [
                        {
                            "stepId": "discovery",
                            "stepName": "Discovery",
                            "status": "passed",
                            "key": "creditorName",
                            "requestArea": "request-json-body",
                            "fieldPath": "request.body.Data.Creditor.Name",
                        },
                        {
                            "stepId": "token-request",
                            "stepName": "Token request",
                            "status": "failed",
                            "key": "remittanceInformation",
                            "requestArea": "request-json-body",
                            "fieldPath": "request.body.remittanceInformation",
                        },
                    ],
                    "referencedButNotRun": [],
                },
                "steps": [
                    {
                        "name": "discovery",
                        "status": "passed",
                        "message": "Discovery endpoint reachable",
                        "url": "https://example.com/.well-known/openid-configuration",
                        "details": {
                            "assertions": [{"status": "passed", "message": "Status code matched"}],
                            "request": {"method": "GET", "url": "https://example.com/.well-known/openid-configuration"},
                            "response": {
                                "statusCode": 200,
                                "headers": {"content-type": "application/json"},
                                "body": {"issuer": "https://example.com", "note": "<script>alert(1)</script>"},
                            },
                        },
                    },
                    {
                        "name": "token-request",
                        "status": "failed",
                        "message": "Token step failed",
                        "url": "https://example.com/token",
                        "details": {
                            "assertions": [{"status": "failed", "message": "Expected 200 got 400"}],
                            "warning": "Deprecated token endpoint behaviour",
                            "request": {
                                "method": "POST",
                                "url": "https://example.com/token",
                                "headers": {"Authorization": "***"},
                                "body": {
                                    "grant_type": "authorization_code",
                                    "evidence_lines": [
                                        "line-1",
                                        "line-2",
                                        "line-3",
                                        "line-4",
                                        "line-5",
                                        "line-6",
                                        "line-7",
                                        "line-8",
                                        "line-9",
                                        "line-10",
                                        "line-11",
                                        "line-12",
                                    ],
                                },
                            },
                            "response": {
                                "statusCode": 400,
                                "headers": {"content-type": "application/json"},
                                "body": {"error": "invalid_grant"},
                            },
                            "rawError": {"reason": "Downstream rejected auth code"},
                        },
                    },
                ],
            },
        )

        response = Client().get(f"/runs/{record.run_id}/steps/")

        assert response.status_code == 200
        assert response.context is not None
        content = response.content.decode("utf-8")

        assert "Step Progress and Results" in content
        assert "Issues" in content
        assert "Request evidence" in content
        assert "Response evidence" in content
        assert "Raw details" in content
        assert "1 custom value reference(s)" in content
        assert "Custom values used by this step" in content
        assert "Hash sha256:" not in content
        assert 'class="custom-values-warning"' in content
        assert "Body" in content
        assert "remittanceInformation" in content
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in content
        assert "<script>alert(1)</script>" not in content
        assert 'data-copy-target="step-1-result-request-json"' in content
        assert 'data-copy-target="step-1-result-response-json"' in content
        assert 'data-copy-target="step-2-result-request-json"' in content
        assert 'data-copy-target="step-2-result-response-json"' in content
        assert 'data-copy-target="step-2-result-remaining-details-json"' in content
        assert 'id="step-2-result-request-json"' in content
        assert 'id="step-2-result-response-json"' in content
        assert 'id="step-2-result-remaining-details-json"' in content
        assert "Request sent - GET - https://example.com/.well-known/openid-configuration" in content
        assert "Response received (400) - https://example.com/token" in content
        assert "Request evidence is shown in the consolidated result evidence above." in content
        assert "Response evidence is shown in the consolidated result evidence above." in content
        assert 'data-copy-target="step-1-event-' not in content
        assert 'data-copy-target="step-2-event-' not in content
        assert 'class="payload-json payload-scroll evidence-preview"' not in content

        context_steps = cast("list[dict[str, object]]", response.context["step_progress"])
        assert len(context_steps) == 2

        passed_step = context_steps[0]
        assert passed_step["name"] == "Discovery"
        assert passed_step["status"] == "passed"
        assert passed_step["request_method"] == "GET"
        assert passed_step["request_url"] == "https://example.com/.well-known/openid-configuration"
        assert passed_step["response_status_code"] == 200
        assert cast("list[dict[str, str]]", passed_step["issues"]) == []
        assert '"method": "GET"' in cast("str", passed_step["request_json"])
        assert '"statusCode": 200' in cast("str", passed_step["response_json"])
        assert passed_step["remaining_details_json"] is None
        assert (
            cast("str", passed_step["request_json_preview"]).splitlines()
            == cast("str", passed_step["request_json"]).splitlines()[:9]
        )
        assert (
            cast("str", passed_step["response_json_preview"]).splitlines()
            == cast("str", passed_step["response_json"]).splitlines()[:9]
        )
        assert passed_step["remaining_details_json_preview"] is None
        assert passed_step["custom_test_value_impact_count"] == 1
        passed_value_entries = cast("list[dict[str, object]]", passed_step["custom_test_value_impact_values"])
        assert len(passed_value_entries) == 1
        assert passed_value_entries[0]["key"] == "creditorName"

        failed_step = context_steps[1]
        assert failed_step["name"] == "Token request"
        assert failed_step["status"] == "failed"
        assert failed_step["custom_test_value_impact_count"] == 1
        assert cast("list[dict[str, str]]", failed_step["custom_test_value_impact_references"]) == [
            {
                "key": "remittanceInformation",
                "request_area": "request-json-body",
                "field_path": "request.body.remittanceInformation",
                "status": "failed",
            }
        ]
        value_entries = cast("list[dict[str, object]]", failed_step["custom_test_value_impact_values"])
        assert len(value_entries) == 1
        assert value_entries[0]["key"] == "remittanceInformation"
        issues = cast("list[dict[str, str]]", failed_step["issues"])
        assert issues == [
            {"status": "failed", "message": "Expected 200 got 400"},
            {"status": "warn", "message": "Deprecated token endpoint behaviour"},
        ]
        assert '"rawError"' in cast("str", failed_step["remaining_details_json"])
        assert isinstance(failed_step["request_json_preview"], str)
        assert isinstance(failed_step["response_json_preview"], str)
        assert isinstance(failed_step["remaining_details_json_preview"], str)

        failed_request_json = cast("str", failed_step["request_json"])
        failed_request_preview = failed_step["request_json_preview"]  # isinstance check above narrows to str
        assert len(failed_request_preview.splitlines()) == 9
        assert failed_request_preview == "\n".join(failed_request_json.splitlines()[:9])
        assert '"line-12"' in failed_request_json
        assert '"line-12"' not in failed_request_preview

        assert response.context["result_issue_count"] == 2
        assert response.context["result_status_counts"] == {
            "passed": 1,
            "failed": 1,
            "warn": 0,
            "skipped": 0,
        }
        assert response.context["developer_mode"] is False

    def test_run_detail_uses_wrap_friendly_step_summary_layout_for_long_messages(self) -> None:
        """Run detail should preserve readable step titles when summary messages are long."""
        record = run_store.create_run(
            planned_steps=(
                RunPlanStep(
                    step_id="psu-manual",
                    name="PSU authorisation (manual)",
                    kind="psu",
                    group="default",
                    phase="execution",
                    mandatory=True,
                    optional=False,
                    order=0,
                ),
            )
        )
        run_store.mark_running(record.run_id)
        run_store.mark_completed(
            record.run_id,
            result={
                "status": "failed",
                "summary": {"total": 1, "failed": 1, "passed": 0, "warn": 0, "skipped": 0},
                "steps": [
                    {
                        "name": "psu-manual",
                        "status": "failed",
                        "message": (
                            "Placeholder resolution failed: Path segment 'Data' not found: "
                            "${steps.OB-400-DOP-100300.response.body.Data.ConsentId}"
                        ),
                    }
                ],
            },
        )

        response = Client().get(f"/runs/{record.run_id}/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "PSU authorisation (manual)" in content
        assert "Placeholder resolution failed: Path segment" in content
        assert 'class="muted result-step-summary-meta"' in content
        assert 'class="muted result-step-summary-message"' in content
        assert ".result-step-summary {" in content
        assert "flex-wrap: wrap;" in content
        assert ".result-step-title {" in content
        assert "min-width: min(100%, 18rem);" in content
        assert "overflow-wrap: break-word;" in content
        assert ".result-step-summary-message {" in content
        assert "flex: 1 1 100%;" in content

    def test_result_partial_omits_per_step_details_loop(self) -> None:
        """Result partial should keep only run-level outcome content."""
        record = run_store.create_run(
            planned_steps=(
                RunPlanStep(
                    step_id="discovery",
                    name="Discovery",
                    kind="http",
                    group="setup",
                    phase="setup",
                    mandatory=True,
                    optional=False,
                    order=0,
                ),
            )
        )
        run_store.mark_running(record.run_id)
        run_store.mark_completed(
            record.run_id,
            result={
                "status": "passed",
                "summary": {"total": 1, "passed": 1, "failed": 0, "warn": 0, "skipped": 0},
                "steps": [{"name": "discovery", "status": "passed", "message": "ok"}],
            },
        )

        response = Client().get(f"/runs/{record.run_id}/result/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Per-step details" not in content
        assert 'details class="result-step"' not in content
        assert 'data-step-id="discovery"' not in content

    def test_result_partial_context_sets_developer_mode_from_run_logger(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Result context surfaces the logger developer-mode flag for warnings."""
        monkeypatch.setenv("CONFORMANCE_DEVELOPER_MODE", "true")
        record = run_store.create_run()

        context = _run_context(record)

        assert context["developer_mode"] is True

    def test_result_partial_renders_developer_mode_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Completed results show a clear warning in developer-unmasked mode."""
        monkeypatch.setenv("CONFORMANCE_DEVELOPER_MODE", "true")
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_completed(record.run_id, result={"status": "passed", "summary": {"total": 0}})

        response = Client().get(f"/runs/{record.run_id}/result/")

        assert response.status_code == 200
        assert "Developer mode warning" in response.content.decode("utf-8")

    def test_result_download_is_available_to_non_loopback_browser_clients(self) -> None:
        """UI result downloads should not inherit the REST API loopback guard."""
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_completed(record.run_id, result={"status": "passed"})

        client = Client(REMOTE_ADDR="10.0.0.5")
        api_response = client.get(f"/api/runs/{record.run_id}/result/")
        ui_response = client.get(f"/runs/{record.run_id}/result.json")

        assert api_response.status_code == 403
        assert ui_response.status_code == 200
        assert ui_response["Content-Type"] == "application/json"
        assert ui_response["Content-Disposition"] == f'attachment; filename="{record.run_id}-result.json"'
        assert ui_response.json() == {"status": "passed"}

    def test_result_partial_renders_failed_run_message(self) -> None:
        """Failed runs render the stored terminal error message."""
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_failed(record.run_id, error="Internal engine error")

        response = Client().get(f"/runs/{record.run_id}/result/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Internal engine error" in content
        assert 'hx-trigger="load"' in content
        assert "every 2s" not in content

    def test_run_context_step_progress_defaults_selected_steps_to_pending(self) -> None:
        """Selected planned steps should render as pending before any events."""
        record = run_store.create_run(
            planned_steps=(
                RunPlanStep(
                    step_id="step-one",
                    name="Step one",
                    kind="http",
                    group="setup",
                    phase="setup",
                    mandatory=True,
                    optional=False,
                    order=0,
                ),
                RunPlanStep(
                    step_id="step-two",
                    name="Step two",
                    kind="http",
                    group="execution",
                    phase="execution",
                    mandatory=False,
                    optional=True,
                    order=1,
                ),
            )
        )
        snapshot = run_store.get_run(record.run_id)
        assert snapshot is not None

        context = _run_context(snapshot)

        progress_rows = cast("list[dict[str, object]]", context["step_progress"])
        assert len(progress_rows) == 2
        assert [row["step_id"] for row in progress_rows] == ["step-one", "step-two"]
        assert [row["status"] for row in progress_rows] == ["pending", "pending"]
        assert [row["evidence_events"] for row in progress_rows] == [[], []]
        assert context["step_progress_counts"] == {
            "total": 2,
            "pending": 2,
            "running_or_awaiting": 0,
            "passed": 0,
            "failed": 0,
            "warn": 0,
            "skipped": 0,
            "completed": 0,
        }

    def test_run_context_step_progress_maps_events_into_running_and_terminal_rows(self) -> None:
        """Step progress rows should reflect execution events and evidence summaries."""
        record = run_store.create_run(
            planned_steps=(
                RunPlanStep(
                    step_id="discovery",
                    name="Discovery",
                    kind="http",
                    group="setup",
                    phase="setup",
                    mandatory=True,
                    optional=False,
                    order=0,
                ),
            )
        )
        assert record.execution_logger is not None
        record.execution_logger.emit("step-started", step_id="discovery")
        record.execution_logger.emit(
            "request-sent",
            step_id="discovery",
            payload={"method": "GET", "url": "https://example.com/.well-known/openid-configuration"},
        )
        record.execution_logger.emit(
            "response-received",
            step_id="discovery",
            payload={"statusCode": 200, "url": "https://example.com/.well-known/openid-configuration"},
        )
        record.execution_logger.emit(
            "assertion-evaluated",
            step_id="discovery",
            payload={"status": "passed", "message": "Status code matched"},
        )
        record.execution_logger.emit(
            "step-completed",
            step_id="discovery",
            payload={"status": "passed", "message": "Discovery passed", "statusCode": 200},
        )

        snapshot = run_store.get_run(record.run_id)
        assert snapshot is not None
        context = _run_context(snapshot)

        progress_rows = cast("list[dict[str, object]]", context["step_progress"])
        assert len(progress_rows) == 1
        row = progress_rows[0]
        assert row["step_id"] == "discovery"
        assert row["status"] == "passed"
        assert row["message"] == "Discovery passed"
        assert row["status_code"] == 200
        assert isinstance(row["started_at"], str)
        evidence_events = cast("list[dict[str, object]]", row["evidence_events"])
        assert [event["type"] for event in evidence_events] == [
            "request-sent",
            "response-received",
            "assertion-evaluated",
            "step-completed",
        ]
        assert context["step_progress_counts"] == {
            "total": 1,
            "pending": 0,
            "running_or_awaiting": 0,
            "passed": 1,
            "failed": 0,
            "warn": 0,
            "skipped": 0,
            "completed": 1,
        }

    def test_run_context_step_progress_marks_pending_participant_action_as_awaiting(self) -> None:
        """Pending PSU participant actions should surface as awaiting progress rows."""
        record = run_store.create_run(
            planned_steps=(
                RunPlanStep(
                    step_id="psu",
                    name="Manual PSU",
                    kind="psu-authorization",
                    group="consent",
                    phase="execution",
                    mandatory=True,
                    optional=False,
                    order=0,
                ),
            )
        )
        assert record.execution_logger is not None
        record.execution_logger.emit("step-started", step_id="psu")
        run_store.set_participant_action(record.run_id, step_id="psu", url="https://auth.example.com/authorize?state=x")

        snapshot = run_store.get_run(record.run_id)
        assert snapshot is not None
        context = _run_context(snapshot)

        progress_rows = cast("list[dict[str, object]]", context["step_progress"])
        assert len(progress_rows) == 1
        row = progress_rows[0]
        assert row["status"] == "awaiting"
        assert row["awaiting_authorisation"] is True
        assert row["message"] == "Waiting for PSU authorisation callback"
        assert context["step_progress_counts"] == {
            "total": 1,
            "pending": 0,
            "running_or_awaiting": 1,
            "passed": 0,
            "failed": 0,
            "warn": 0,
            "skipped": 0,
            "completed": 0,
        }

    def test_run_context_step_progress_reconciles_status_with_final_result(self) -> None:
        """Final result step data should override live status and message fields."""
        record = run_store.create_run(
            planned_steps=(
                RunPlanStep(
                    step_id="token-exchange",
                    name="Token exchange",
                    kind="http",
                    group="execution",
                    phase="execution",
                    mandatory=True,
                    optional=False,
                    order=0,
                ),
            )
        )
        assert record.execution_logger is not None
        record.execution_logger.emit("step-started", step_id="token-exchange")
        record.execution_logger.emit(
            "step-completed",
            step_id="token-exchange",
            payload={"status": "passed", "message": "Interim status", "statusCode": 200},
        )
        run_store.mark_running(record.run_id)
        run_store.mark_completed(
            record.run_id,
            result={
                "status": "failed",
                "steps": [
                    {
                        "name": "token-exchange",
                        "status": "failed",
                        "message": "Token endpoint rejected authorisation code",
                        "details": {"response": {"statusCode": 400}},
                    }
                ],
            },
        )

        snapshot = run_store.get_run(record.run_id)
        assert snapshot is not None
        context = _run_context(snapshot)

        progress_rows = cast("list[dict[str, object]]", context["step_progress"])
        assert len(progress_rows) == 1
        row = progress_rows[0]
        assert row["status"] == "failed"
        assert row["message"] == "Token endpoint rejected authorisation code"
        assert row["status_code"] == 400
        assert context["step_progress_counts"] == {
            "total": 1,
            "pending": 0,
            "running_or_awaiting": 0,
            "passed": 0,
            "failed": 1,
            "warn": 0,
            "skipped": 0,
            "completed": 1,
        }
