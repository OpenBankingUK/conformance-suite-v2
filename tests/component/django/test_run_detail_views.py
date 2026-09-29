"""Component tests for the run detail page routes and rendered run state."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from django.test import Client

from conformance.api.run_store import RunPlanStep, run_store

pytestmark = pytest.mark.component


@pytest.fixture(autouse=True)
def _reset_global_stores() -> Iterator[None]:
    """Reset process-local singleton stores around each UI test.

    Yields:
        Control back to pytest while the test executes.
    """
    run_store.reset()
    yield
    run_store.reset()


def _run_id_from_redirect(location: str) -> str:
    """Extract the run id from a Django redirect response.

    Args:
        location: Redirect target from a Django test response.

    Returns:
        Run id segment from the redirect target.
    """
    return location.rstrip("/").rsplit("/", maxsplit=1)[-1]


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
                "catalogue": {
                    "standard": "open-banking",
                    "version": "v4.0",
                    "api": "ais",
                    "catalogueVersion": "2026.07.legacy-fcs-ais-at.1",
                    "generatedTestCaseIds": ["ais-at-accounts-list-200"],
                    "selectedEndpoints": [
                        {
                            "method": "GET",
                            "path": "/open-banking/v4.0/aisp/accounts",
                            "resourceGroup": "Accounts",
                        }
                    ],
                    "selectedCapabilities": [
                        {
                            "method": "GET",
                            "path": "/open-banking/v4.0/aisp/accounts",
                            "capabilityId": "ais.accounts.list.core",
                            "label": "AIS accounts list baseline coverage",
                            "required": True,
                        }
                    ],
                    "applicabilityDecisions": [],
                    "runtimeInputSnapshot": [],
                    "nonCertifyingReasons": [],
                },
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
        assert "Catalogue traceability" in content
        assert "2026.07.legacy-fcs-ais-at.1" in content
        assert "Selected capabilities" in content
        assert 'href="/">Home page</a>' in content
        assert "New plan" not in content

    def test_failed_run_detail_shows_home_page_action(self) -> None:
        """Failed terminal run detail pages return participants to the home page."""
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_failed(record.run_id, error="Participant callback timed out")

        response = Client().get(f"/runs/{record.run_id}/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Participant callback timed out" in content
        assert 'href="/">Home page</a>' in content
        assert "New plan" not in content

    def test_run_detail_sizes_psu_popup_within_available_screen(self) -> None:
        """PSU authorisation popups target 900 square pixels without exceeding the available screen."""
        record = run_store.create_run()

        response = Client().get(f"/runs/{record.run_id}/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "const popupTargetSize = 900;" in content
        assert "Math.min(popupTargetSize, availableWidth)" in content
        assert "Math.min(popupTargetSize, availableHeight)" in content
        assert "window.screen.availLeft" in content
        assert "window.screen.availTop" in content
        assert "availableLeft + availableWidth - popupWidth" in content
        assert "availableTop + availableHeight - popupHeight" in content
        assert "`width=${popupWidth}`" in content
        assert "`height=${popupHeight}`" in content
        assert "`left=${popupLeft}`" in content
        assert "`top=${popupTop}`" in content

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
