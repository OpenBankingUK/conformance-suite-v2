"""Integration tests for participant-facing browser UI views."""

from __future__ import annotations

import json
from typing import cast
from unittest.mock import Mock, patch

import pytest
from django.test import Client

from conformance.api.auth_session_store import auth_session_store
from conformance.api.run_store import RunConflictError, run_store
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


@pytest.fixture(autouse=True)
def _reset_global_stores() -> None:
    """Reset process-local singleton stores between UI tests."""
    run_store.reset()
    auth_session_store.reset()


def _http_step(step_id: str, *, mandatory: bool = False, optional: bool = False) -> dict[str, JsonValue]:
    """Build a minimal v1 HTTP step for UI tests.

    Args:
        step_id: Stable manifest step id.
        mandatory: Whether the manifest marks the step as mandatory.
        optional: Whether the manifest marks the step as optional.

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
        "mandatory": True,
    }


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


@pytest.mark.integration
class TestPlanBuilderUi:
    """Browser coverage for the participant plan-builder views."""

    def test_plan_builder_get_renders_input_form(self) -> None:
        """GET /plan/ renders the JSON input form."""
        response = Client().get("/plan/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Test plan builder" in content
        assert "Config JSON" in content
        assert "Manifest JSON" in content
        assert 'href="/health/"' in content
        assert "hx-post" not in content

    def test_preview_post_renders_step_selection_table(self) -> None:
        """POST /plan/preview/ renders selectable v1 manifest rows."""
        manifest = _v1_manifest([_http_step("mandatory", mandatory=True), _http_step("optional", optional=True)])

        response = Client().post("/plan/preview/", data=_plan_form_data(manifest, selected_step_ids=["mandatory"]))

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "UI manifest" in content
        assert "Step mandatory" in content
        assert "Step optional" in content
        assert "Mandatory" in content
        assert "Optional" in content
        assert "hx-post" not in content

    def test_preview_post_resolves_config_only_suite(self) -> None:
        """POST /plan/preview/ can render a config-selected suite with blank manifest."""
        response = Client().post(
            "/plan/preview/",
            data=_suite_plan_form_data(selected_step_ids=["openid-discovery"]),
        )

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Open Banking Read/Write v4.0 FAPI 1 Advanced discovery/JWKS smoke suite" in content
        assert "openid-discovery" in content
        assert "jwks-fetch" in content
        assert "ob-read-write" in content

    def test_preview_post_explicit_manifest_overrides_config_suite(self) -> None:
        """Pasted manifests override config suite selection in the browser preview."""
        manifest = _v1_manifest([_http_step("explicit")])
        response = Client().post(
            "/plan/preview/",
            data={
                "config_json": json.dumps(SUITE_CONFIG),
                "manifest_json": json.dumps(manifest),
                "selection_mode": "select",
                "selected_step_ids": ["explicit"],
            },
        )

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "UI manifest" in content
        assert "explicit" in content
        assert "openid-discovery" not in content

    def test_preview_post_returns_400_for_invalid_manifest(self) -> None:
        """Invalid manifest submissions render form errors with HTTP 400."""
        response = Client().post(
            "/plan/preview/",
            data=_plan_form_data({"schemaVersion": "v1", "name": "Broken", "steps": []}),
        )

        assert response.status_code == 400
        assert "Manifest validation failed" in response.content.decode("utf-8")

    def test_preview_post_returns_400_for_invalid_suite_resolution(self) -> None:
        """Suite catalog errors render form errors with HTTP 400."""
        from conformance.suite_catalog import SuiteCatalogError

        with patch("conformance.api.plan_builder.resolve_suite", side_effect=SuiteCatalogError("missing suite")):
            response = Client().post(
                "/plan/preview/",
                data=_suite_plan_form_data(selected_step_ids=["openid-discovery"]),
            )

        assert response.status_code == 400
        assert "Suite resolution failed" in response.content.decode("utf-8")

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
        assert plan.selected_step_ids() == ["mandatory"]

    @patch("conformance.api.ui_views.start_run")
    def test_launch_post_starts_config_resolved_suite(self, mock_start_run: Mock) -> None:
        """Launch can start a config-only suite preview through shared lifecycle code.

        Args:
            mock_start_run: Patched lifecycle starter used to inspect launch inputs.
        """
        mock_start_run.return_value = {"id": "run-123", "status": "pending", "createdAt": "2026-06-03T12:00:00+00:00"}

        response = Client().post(
            "/plan/launch/",
            data=_suite_plan_form_data(selected_step_ids=["openid-discovery"]),
        )

        assert response.status_code == 302
        assert response["Location"] == "/runs/run-123/"
        assert mock_start_run.call_count == 1
        manifest = mock_start_run.call_args.kwargs["manifest"]
        plan = mock_start_run.call_args.kwargs["plan"]
        suite_metadata = mock_start_run.call_args.kwargs["suite_metadata"]
        assert mock_start_run.call_args.kwargs["browser_psu_prompts"] is True
        assert manifest.name == "Open Banking Read/Write v4.0 FAPI 1 Advanced discovery/JWKS smoke suite"
        assert plan.selected_step_ids() == ["openid-discovery"]
        assert suite_metadata.catalog_id == "ob-read-write/v4.0/fapi1-advanced/discovery-jwks"

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

        get_response = client.get("/plan/")
        csrf_token = get_response.cookies["csrftoken"].value
        accepted = client.post(
            "/plan/preview/",
            data={**_plan_form_data(manifest, selected_step_ids=["standard"]), "csrfmiddlewaretoken": csrf_token},
        )
        assert accepted.status_code == 200

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


@pytest.mark.integration
class TestRunDetailUi:
    """Browser coverage for run detail and partial views."""

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
        assert 'http-equiv="refresh" content="2"' in content
        assert f"/runs/{record.run_id}/log.ndjson" in content
        assert "Result pending" in content

    def test_run_detail_does_not_refresh_terminal_runs(self) -> None:
        """Completed run detail pages should not keep refreshing."""
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_completed(record.run_id, result={"status": "passed"})

        response = Client().get(f"/runs/{record.run_id}/")

        assert response.status_code == 200
        assert 'http-equiv="refresh"' not in response.content.decode("utf-8")

    def test_status_partial_renders_current_timestamps(self) -> None:
        """The status partial renders the current run snapshot."""
        record = run_store.create_run()
        run_store.mark_running(record.run_id)

        response = Client().get(f"/runs/{record.run_id}/status/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "running" in content
        assert "Started" in content

    def test_log_partial_renders_masked_log_link_and_event_count(self) -> None:
        """The log partial links to the browser-accessible NDJSON endpoint."""
        record = run_store.create_run()
        assert record.execution_logger is not None
        record.execution_logger.emit("run-started")
        record.execution_logger.emit("run-completed", payload={"status": "passed"})

        response = Client().get(f"/runs/{record.run_id}/log/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert ">2<" in content
        assert f"/runs/{record.run_id}/log.ndjson" in content
        assert "Masked log" in content

    def test_log_partial_counts_events_without_serialising_ndjson(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Rendering the log partial should count buffered events directly.

        Args:
            monkeypatch: pytest fixture used to make NDJSON serialisation fail
                if the UI summary accidentally calls it.
        """
        record = run_store.create_run()
        assert record.execution_logger is not None
        record.execution_logger.emit("run-started")
        monkeypatch.setattr(
            record.execution_logger,
            "to_ndjson_bytes",
            Mock(side_effect=AssertionError("log count must not serialise NDJSON")),
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
        ui_response = client.get(f"/runs/{record.run_id}/log.ndjson")

        assert api_response.status_code == 403
        assert ui_response.status_code == 200
        assert ui_response["Content-Type"] == "application/x-ndjson"
        assert ui_response["Content-Disposition"] == f'attachment; filename="{record.run_id}-execution-log.ndjson"'
        assert json.loads(ui_response.content.decode("utf-8").strip())["type"] == "run-started"

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
        assert "Plan selected" in content
        assert f"/runs/{record.run_id}/result.json" in content

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
        assert "Internal engine error" in response.content.decode("utf-8")
