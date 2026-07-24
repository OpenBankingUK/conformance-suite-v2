"""Integration tests for participant-facing browser UI views."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import cast
from unittest.mock import Mock, patch

import pytest
from django.test import Client

from conformance.api.plan_builder import CatalogueEndpointOption, PlanBuilderForm, guided_flow_context
from conformance.api.run_store import RunConflictError, RunPlanStep, run_store
from conformance.json_types import JsonValue

VALID_CONFIG: dict[str, JsonValue] = {
    "environment": "test-env",
    "discoveryUrl": "https://example.com/.well-known/openid-configuration",
}
"""Minimal config used by browser UI tests."""


@pytest.fixture(autouse=True)
def _reset_global_stores() -> Iterator[None]:
    """Reset process-local singleton stores around each UI test.

    Yields:
        Control back to pytest while the test executes.
    """
    run_store.reset()
    yield
    run_store.reset()


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


def _plan_form_data(
    *,
    endpoint_ids: list[str] | None = None,
    runtime_inputs: dict[str, str] | None = None,
) -> dict[str, object]:
    """Build form data for plan preview and launch requests.

    Args:
        endpoint_ids: Implemented endpoint ids submitted by checkboxes.
        runtime_inputs: Runtime input id/value strings to submit.

    Returns:
        Form-encoded payload dictionary accepted by Django's test client.
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
    return data


def _valid_plan_form_data() -> dict[str, object]:
    """Build a valid AIS endpoint-selected form payload.

    Returns:
        Form data that compiles the AIS accounts-list catalogue plan.
    """
    return _plan_form_data(
        endpoint_ids=[_ais_accounts_endpoint_id()],
        runtime_inputs={
            "resourceBaseUrl": "https://resource.example.com",
            "accessToken": "secret-access-token",
        },
    )


def _run_id_from_redirect(location: str) -> str:
    """Extract the run id from a Django redirect response.

    Args:
        location: Redirect target from a Django test response.

    Returns:
        Run id segment from the redirect target.
    """
    return location.rstrip("/").rsplit("/", maxsplit=1)[-1]


@pytest.mark.integration
class TestPlanBuilderUi:
    """Browser coverage for the participant plan-builder views."""

    def test_plan_builder_get_renders_endpoint_selection_form(self) -> None:
        """GET /plan/ renders the endpoint-selected plan-builder form."""
        response = Client().get("/plan/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Test plan builder" in content
        assert "Catalogue endpoint selection" in content
        assert "Implemented endpoints" in content
        assert "/open-banking/v4.0/aisp/accounts" in content
        assert "Plan spec JSON" in content
        assert "Bundled suite" not in content
        assert "Manifest JSON" not in content
        assert "Model bank example" not in content
        assert "hx-post" not in content

    def test_preview_post_renders_generated_counts_and_hidden_audit_details(self) -> None:
        """POST /plan/preview/ renders generated catalogue counts and collapsed audit details."""
        response = Client().post("/plan/preview/", data=_valid_plan_form_data())

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Open Banking v4.0 AIS" in content
        assert "Generated tests" in content
        assert "Implemented endpoints" in content
        assert "Certification plan eligible" in content
        assert "<details class=\"audit\">" in content
        assert "Audit generated tests and traceability" in content
        assert "ais-at-accounts-list-200" in content
        assert '"accessToken"' not in content.split("Generated plan spec", maxsplit=1)[1]

    def test_preview_post_returns_400_for_missing_runtime_input(self) -> None:
        """Missing selected-endpoint runtime data renders validation errors with HTTP 400."""
        response = Client().post(
            "/plan/preview/",
            data=_plan_form_data(endpoint_ids=[_ais_accounts_endpoint_id()]),
        )

        assert response.status_code == 400
        content = response.content.decode("utf-8")
        assert "Required runtime input" in content
        assert "AIS resource server base URL" in content
        assert "AIS access token" in content

    @patch("conformance.api.ui_views.start_run")
    def test_launch_post_starts_compiled_catalogue_run(self, mock_start_run: Mock) -> None:
        """Launch validates the form and hands compiled catalogue state to lifecycle code."""
        mock_start_run.return_value = {"id": "run-123", "status": "pending", "createdAt": "2026-06-03T12:00:00+00:00"}

        response = Client().post("/plan/launch/", data=_valid_plan_form_data())

        assert response.status_code == 302
        assert response["Location"] == "/runs/run-123/"
        assert mock_start_run.call_count == 1
        assert mock_start_run.call_args.kwargs["browser_psu_prompts"] is True
        compiled_plan = mock_start_run.call_args.kwargs["compiled_plan"]
        runtime_inputs = mock_start_run.call_args.kwargs["runtime_inputs"]
        assert "ais-at-accounts-list-200" in compiled_plan.traceability.generated_test_case_ids
        assert runtime_inputs["resourceBaseUrl"] == "https://resource.example.com"
        assert runtime_inputs["accessToken"] == "secret-access-token"
        assert "manifest" not in mock_start_run.call_args.kwargs
        assert "plan" not in mock_start_run.call_args.kwargs

    @patch("conformance.api.ui_views.start_run")
    def test_launch_post_renders_conflict_when_run_is_active(self, mock_start_run: Mock) -> None:
        """Active-run conflicts render HTTP 409 with a detail-page link."""
        mock_start_run.side_effect = RunConflictError("active-run")

        response = Client().post("/plan/launch/", data=_valid_plan_form_data())

        assert response.status_code == 409
        content = response.content.decode("utf-8")
        assert "A run is already active" in content
        assert "/runs/active-run/" in content

    def test_post_views_are_csrf_protected(self) -> None:
        """Browser POST routes require the Django CSRF token when enforcement is enabled."""
        client = Client(enforce_csrf_checks=True)

        rejected = client.post("/plan/preview/", data=_valid_plan_form_data())

        assert rejected.status_code == 403

        get_response = client.get("/plan/")
        csrf_token = get_response.cookies["csrftoken"].value
        accepted = client.post(
            "/plan/preview/",
            data={**_valid_plan_form_data(), "csrfmiddlewaretoken": csrf_token},
        )
        assert accepted.status_code == 200

    @patch("conformance.api.ui_views.start_run")
    def test_launch_post_is_csrf_protected(self, mock_start_run: Mock) -> None:
        """Launch POST rejects missing CSRF tokens and accepts token-backed submissions."""
        mock_start_run.return_value = {"id": "run-123", "status": "pending", "createdAt": "2026-06-03T12:00:00+00:00"}
        client = Client(enforce_csrf_checks=True)

        rejected = client.post("/plan/launch/", data=_valid_plan_form_data())

        assert rejected.status_code == 403
        mock_start_run.assert_not_called()

        get_response = client.get("/plan/")
        csrf_token = get_response.cookies["csrftoken"].value
        accepted = client.post(
            "/plan/launch/",
            data={**_valid_plan_form_data(), "csrfmiddlewaretoken": csrf_token},
        )

        assert accepted.status_code == 302
        assert accepted["Location"] == "/runs/run-123/"
        assert mock_start_run.call_count == 1


@pytest.mark.integration
class TestRunDetailUi:
    """Browser coverage for run detail and partial views."""

    def test_run_detail_renders_compiled_step_snapshot_and_result(self) -> None:
        """Run detail renders compiled-plan step snapshots and completed result evidence."""
        record = run_store.create_run(
            planned_steps=(
                RunPlanStep(
                    step_id="ais-at-accounts-list-200-request",
                    name="List AIS accounts",
                    kind="http",
                    group="ais-at-accounts-list-200",
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
                "status": "passed",
                "summary": {"total": 1, "passed": 1, "failed": 0, "warn": 0, "skipped": 0},
                "plan": {"selectedSteps": 1, "deselectedSteps": 0, "mandatorySelected": 1, "mandatoryDeselected": 0},
                "certificationEligibility": {"eligible": True},
                "steps": [
                    {
                        "name": "ais-at-accounts-list-200-request",
                        "status": "passed",
                        "message": "OK",
                        "details": {
                            "request": {
                                "method": "GET",
                                "url": "https://resource.example.com/open-banking/v4.0/aisp/accounts",
                            },
                            "response": {"statusCode": 200},
                            "assertions": [{"status": "passed", "message": "HTTP 200"}],
                        },
                    }
                ],
            },
        )

        response = Client().get(f"/runs/{record.run_id}/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert f"Run {record.run_id}" in content
        assert "List AIS accounts" in content
        assert "ais-at-accounts-list-200-request" in content
        assert "passed" in content
        assert "Certification" in content

    def test_run_detail_returns_404_for_unknown_run(self) -> None:
        """Unknown run detail pages return 404."""
        response = Client().get("/runs/missing/")

        assert response.status_code == 404

    def test_run_result_download_returns_completed_result_json(self) -> None:
        """Completed result downloads return the masked JSON result."""
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_completed(record.run_id, result={"status": "passed", "steps": []})

        response = Client().get(f"/runs/{record.run_id}/result.json")

        assert response.status_code == 200
        assert response.json() == {"status": "passed", "steps": []}

    def test_run_id_can_be_extracted_from_redirect_location(self) -> None:
        """Redirect helper returns the last URL path segment."""
        assert _run_id_from_redirect("/runs/run-123/") == "run-123"
