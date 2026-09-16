"""Component tests for the loopback REST run lifecycle endpoints."""

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from django.test import Client

from conformance.api.run_store import run_store
from conformance.configuration_contracts import PreparedExecutionManifest
from tests.support.paths import REPO_ROOT
from tests.support.run_execution import StubbedRunExecution

pytestmark = pytest.mark.component


VALID_CONFIG = {
    "environment": "test-env",
    "discoveryUrl": "https://example.com/.well-known/openid-configuration",
}

VALID_TEST_PLAN = json.loads(
    (
        REPO_ROOT
        / "tests"
        / "fixtures"
        / "configuration_contracts"
        / "pis"
        / "v4_0_1"
        / "participant-plan.surface.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.usefixtures("api_singleton_stores")
class TestCreateRunEndpoint:
    def test_rejects_non_json_body(self) -> None:
        client = Client()
        response = client.post("/api/runs/", data="not json", content_type="application/json")
        assert response.status_code == 400
        assert "valid JSON" in response.json()["error"]

    def test_rejects_invalid_utf8_body(self) -> None:
        """Malformed UTF-8 bytes must yield 400, not a 500 from UnicodeDecodeError."""
        client = Client()
        response = client.post("/api/runs/", data=b"\xff\xfe\x00", content_type="application/json")
        assert response.status_code == 400
        assert "valid JSON" in response.json()["error"]

    def test_rejects_non_object_body(self) -> None:
        client = Client()
        response = client.post("/api/runs/", data=json.dumps([1, 2, 3]), content_type="application/json")
        assert response.status_code == 400
        assert "JSON object" in response.json()["error"]

    def test_rejects_missing_test_plan(self) -> None:
        client = Client()
        response = client.post("/api/runs/", data=json.dumps({}), content_type="application/json")
        assert response.status_code == 400
        assert "participant-plan 1.0" in response.json()["error"]

    def test_rejects_legacy_config_plan_spec_shape(self) -> None:
        client = Client()
        body = {"config": {"environment": "test"}, "planSpec": {"schemaVersion": "v1"}}
        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert response.status_code == 400
        assert "Legacy run request field(s) are no longer supported" in response.json()["error"]

    def test_rejects_removed_manifest_field(self) -> None:
        client = Client()
        body = {"config": VALID_CONFIG, "manifest": {"schemaVersion": "v99"}}
        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert response.status_code == 400
        assert "Legacy run request field(s) are no longer supported" in response.json()["error"]

    def test_creates_run_and_returns_201(self, stubbed_run_execution: StubbedRunExecution) -> None:
        client = Client()
        body = VALID_TEST_PLAN
        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "pending"
        assert "id" in data
        assert "createdAt" in data
        record = run_store.get_run(data["id"])
        assert record is not None
        assert record.status == "pending"
        assert record.planned_steps
        launch = stubbed_run_execution.wait_for_launch()
        assert launch.args[0] == data["id"]
        prepared = launch.args[2]
        assert isinstance(prepared, PreparedExecutionManifest)
        assert prepared.manifest is not None
        assert launch.kwargs == {"browser_psu_prompts": False}

    def test_rejects_legacy_canonical_test_plan(self, stubbed_run_execution: StubbedRunExecution) -> None:
        client = Client()
        body = {
            "schemaVersion": "1.0",
            "specification": {
                "family": "OBL_READ_WRITE",
                "version": "4.0.1",
                "profile": "FAPI1_ADVANCED",
            },
            "securityEnvironment": {
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "resourceBaseUrl": "https://resource.example.com",
            },
            "resourceGroups": [
                {
                    "id": "AIS",
                    "endpoints": [{"method": "GET", "path": "/open-banking/v4.0/aisp/accounts"}],
                }
            ],
            "businessTestData": {},
            "metadata": {"aspspName": "Example Bank"},
        }

        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")

        assert response.status_code == 400
        assert "participant-plan 1.0" in response.json()["error"]
        stubbed_run_execution.assert_not_launched()

    def test_rejects_canonical_plan_spec_with_separate_config(self, stubbed_run_execution: StubbedRunExecution) -> None:
        """Legacy config/planSpec requests are no longer accepted."""
        client = Client()
        body = {
            "config": VALID_CONFIG,
            "planSpec": {
                "schemaVersion": "1.0",
                "specification": {"family": "OBL_READ_WRITE", "version": "4.0.1"},
                "securityEnvironment": {
                    "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                },
                "resourceGroups": ["AIS"],
                "businessTestData": {},
                "metadata": {},
            },
        }

        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")

        assert response.status_code == 400
        assert "Legacy run request field(s) are no longer supported" in response.json()["error"]
        stubbed_run_execution.assert_not_launched()

    def test_creates_run_from_nested_participant_plan(self, stubbed_run_execution: StubbedRunExecution) -> None:
        """REST API accepts participant plans under the testPlan key."""
        client = Client()
        body = {"testPlan": VALID_TEST_PLAN}

        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")

        assert response.status_code == 201
        stubbed_run_execution.wait_for_launch()

    def test_rejects_removed_deselect_field(self) -> None:
        """``deselectStepIds`` is no longer a public API field."""
        client = Client()
        body = {"config": VALID_CONFIG, "deselectStepIds": ["a"]}
        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert response.status_code == 400
        assert "Legacy run request field(s) are no longer supported" in response.json()["error"]

    def test_rejects_second_concurrent_run(self, stubbed_run_execution: StubbedRunExecution) -> None:
        client = Client()
        body = VALID_TEST_PLAN
        first = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert first.status_code == 201
        second = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert second.status_code == 409
        assert "already active" in second.json()["error"]
        stubbed_run_execution.wait_for_launch()

    def test_get_method_not_allowed(self) -> None:
        client = Client()
        response = client.get("/api/runs/")
        assert response.status_code == 405


@pytest.mark.usefixtures("api_singleton_stores")
class TestGetRunStatusEndpoint:
    def test_returns_404_for_unknown_id(self) -> None:
        client = Client()
        response = client.get("/api/runs/nonexistent/")
        assert response.status_code == 404

    def test_returns_run_status(self, stubbed_run_execution: StubbedRunExecution) -> None:
        client = Client()
        body = VALID_TEST_PLAN
        create_resp = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        run_id = create_resp.json()["id"]
        response = client.get(f"/api/runs/{run_id}/")
        assert response.status_code == 200
        assert response.json()["id"] == run_id
        assert response.json()["status"] == "pending"
        stubbed_run_execution.wait_for_launch()

    def test_keeps_canonical_timestamp_fields_without_display_time_zone(self) -> None:
        client = Client()
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_completed(record.run_id, result={"status": "passed"})
        updated = run_store.get_run(record.run_id)
        assert updated is not None
        assert updated.started_at is not None
        assert updated.finished_at is not None

        response = client.get(f"/api/runs/{record.run_id}/")

        assert response.status_code == 200
        body = response.json()
        assert body["createdAt"] == record.created_at.isoformat()
        assert body["startedAt"] == updated.started_at.isoformat()
        assert body["finishedAt"] == updated.finished_at.isoformat()
        assert "displayTimeZone" not in body
        assert "createdAtLocal" not in body
        assert "startedAtLocal" not in body
        assert "finishedAtLocal" not in body

    def test_appends_local_timestamp_display_fields_when_time_zone_is_requested(self) -> None:
        client = Client()
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_completed(record.run_id, result={"status": "passed"})
        updated = run_store.get_run(record.run_id)
        assert updated is not None
        assert updated.started_at is not None
        assert updated.finished_at is not None

        response = client.get(f"/api/runs/{record.run_id}/?timeZone=Europe/London")

        assert response.status_code == 200
        body = response.json()
        assert body["displayTimeZone"] == "Europe/London"
        assert body["createdAt"] == record.created_at.isoformat()
        assert body["startedAt"] == updated.started_at.isoformat()
        assert body["finishedAt"] == updated.finished_at.isoformat()
        created_at_local = datetime.fromisoformat(body["createdAt"]).astimezone(ZoneInfo("Europe/London"))
        started_at_local = datetime.fromisoformat(body["startedAt"]).astimezone(ZoneInfo("Europe/London"))
        finished_at_local = datetime.fromisoformat(body["finishedAt"]).astimezone(ZoneInfo("Europe/London"))
        assert body["createdAtLocal"] == created_at_local.isoformat()
        assert body["startedAtLocal"] == started_at_local.isoformat()
        assert body["finishedAtLocal"] == finished_at_local.isoformat()

    def test_rejects_unknown_time_zone_on_status_endpoint(self) -> None:
        client = Client()
        record = run_store.create_run()

        response = client.get(f"/api/runs/{record.run_id}/?timeZone=Mars%2FOlympus")

        assert response.status_code == 400
        assert "timeZone" in response.json()["error"]

    def test_post_method_not_allowed(self) -> None:
        client = Client()
        response = client.post("/api/runs/some-id/", data="{}", content_type="application/json")
        assert response.status_code == 405


@pytest.mark.usefixtures("api_singleton_stores")
class TestGetRunResultEndpoint:
    def test_returns_404_for_unknown_id(self) -> None:
        client = Client()
        response = client.get("/api/runs/nonexistent/result/")
        assert response.status_code == 404

    def test_returns_409_when_run_not_complete(self, stubbed_run_execution: StubbedRunExecution) -> None:
        client = Client()
        body = VALID_TEST_PLAN
        create_resp = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        run_id = create_resp.json()["id"]
        response = client.get(f"/api/runs/{run_id}/result/")
        assert response.status_code == 409
        assert "not completed" in response.json()["error"]
        stubbed_run_execution.wait_for_launch()

    def test_returns_result_when_completed(self) -> None:
        client = Client()
        # Drive a run through the public API to a completed terminal state
        # rather than poking RunStore internals.
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_completed(record.run_id, result={"environment": "test", "status": "passed"})

        response = client.get(f"/api/runs/{record.run_id}/result/")
        assert response.status_code == 200
        assert response.json() == {"environment": "test", "status": "passed"}

    def test_keeps_canonical_timestamp_fields_without_display_time_zone(self) -> None:
        client = Client()
        started_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        finished_at = datetime(2026, 1, 2, 4, 5, 6, tzinfo=UTC)
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_completed(
            record.run_id,
            result={
                "environment": "test",
                "status": "passed",
                "startedAt": started_at.isoformat(),
                "finishedAt": finished_at.isoformat(),
            },
        )

        response = client.get(f"/api/runs/{record.run_id}/result/")

        assert response.status_code == 200
        body = response.json()
        assert body["startedAt"] == started_at.isoformat()
        assert body["finishedAt"] == finished_at.isoformat()
        assert "displayTimeZone" not in body
        assert "startedAtLocal" not in body
        assert "finishedAtLocal" not in body

    def test_appends_local_timestamp_display_fields_when_time_zone_is_requested(self) -> None:
        client = Client()
        started_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        finished_at = datetime(2026, 1, 2, 4, 5, 6, tzinfo=UTC)
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_completed(
            record.run_id,
            result={
                "environment": "test",
                "status": "passed",
                "startedAt": started_at.isoformat(),
                "finishedAt": finished_at.isoformat(),
            },
        )

        response = client.get(f"/api/runs/{record.run_id}/result/?timeZone=Europe/London")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "passed"
        assert body["startedAt"] == started_at.isoformat()
        assert body["finishedAt"] == finished_at.isoformat()
        assert body["displayTimeZone"] == "Europe/London"
        assert body["startedAtLocal"] == started_at.astimezone(ZoneInfo("Europe/London")).isoformat()
        assert body["finishedAtLocal"] == finished_at.astimezone(ZoneInfo("Europe/London")).isoformat()

    def test_returns_500_when_run_failed(self) -> None:
        client = Client()
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_failed(record.run_id, error="Internal engine error")

        response = client.get(f"/api/runs/{record.run_id}/result/")
        assert response.status_code == 500
        assert "failed internally" in response.json()["error"]
        assert "detail" not in response.json()


@pytest.mark.usefixtures("api_singleton_stores")
class TestGetRunLogEndpoint:
    """``GET /api/runs/<id>/log/`` exposes the structured execution log."""

    def test_returns_404_for_unknown_id(self) -> None:
        """Unknown run IDs yield 404 with no log content."""
        client = Client()
        response = client.get("/api/runs/nonexistent/log/")
        assert response.status_code == 404

    def test_returns_json_for_known_run(self) -> None:
        """The endpoint streams ``application/json`` with one event object per array item."""
        client = Client()
        record = run_store.create_run()
        # Emit a couple of events into the live buffer attached to the run.
        assert record.execution_logger is not None
        record.execution_logger.emit("run-started")
        record.execution_logger.emit("run-completed", payload={"summary": {"total": 0}})

        response = client.get(f"/api/runs/{record.run_id}/log/")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"
        parsed = json.loads(response.content.decode("utf-8"))
        assert [event["type"] for event in parsed] == ["run-started", "run-completed"]
        assert all(event["runId"] == record.run_id for event in parsed)

    def test_returns_partial_log_for_in_progress_run(self) -> None:
        """An in-flight run returns the events buffered so far (decision in plan)."""
        client = Client()
        record = run_store.create_run()
        assert record.execution_logger is not None
        record.execution_logger.emit("run-started")
        # Do NOT mark the run completed; the log should still be readable.

        response = client.get(f"/api/runs/{record.run_id}/log/")
        assert response.status_code == 200
        body = json.loads(response.content.decode("utf-8"))
        assert len(body) == 1

    def test_non_loopback_request_is_rejected_with_403(self) -> None:
        """The loopback guard applies to the log endpoint too."""
        client = Client(REMOTE_ADDR="10.0.0.5")
        response = client.get("/api/runs/some-id/log/")
        assert response.status_code == 403

    def test_returns_500_when_run_exists_but_logger_unattached(self) -> None:
        """Run record present but no execution logger yields 500, not 404."""
        from datetime import UTC, datetime

        from conformance.api.run_store import RunRecord

        record = RunRecord(
            run_id="no-logger",
            status="running",
            created_at=datetime.now(UTC),
            execution_logger=None,
        )
        run_store._runs["no-logger"] = record  # noqa: SLF001 — direct injection for invariant-violation test

        client = Client()
        response = client.get("/api/runs/no-logger/log/")
        assert response.status_code == 500
        assert response.json()["error"] == "Execution log unavailable for this run"

    def test_does_not_call_get_run_log_bytes_so_eviction_race_returns_200(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """View uses execution_logger from the first lookup, not a second store call.

        If ``get_run_log_bytes`` were called it would return ``None`` here,
        causing the view to return 500.  The fix reads from
        ``record.execution_logger`` directly, so the view must return 200
        even though ``get_run_log_bytes`` is patched to simulate eviction.

        Args:
            monkeypatch: pytest monkeypatch fixture.
        """
        monkeypatch.setattr(run_store, "get_run_log_bytes", lambda _run_id: None)
        client = Client()
        record = run_store.create_run()
        assert record.execution_logger is not None
        record.execution_logger.emit("run-started")

        response = client.get(f"/api/runs/{record.run_id}/log/")

        assert response.status_code == 200
        assert json.loads(response.content.decode("utf-8"))[0]["type"] == "run-started"


@pytest.mark.usefixtures("api_singleton_stores")
class TestLoopbackGuard:
    def test_loopback_request_is_allowed_by_default(self, stubbed_run_execution: StubbedRunExecution) -> None:
        # Django test client uses REMOTE_ADDR=127.0.0.1 by default.
        client = Client()
        body = VALID_TEST_PLAN
        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert response.status_code == 201
        stubbed_run_execution.wait_for_launch()

    def test_non_loopback_request_is_rejected_with_403(self) -> None:
        client = Client(REMOTE_ADDR="10.0.0.5")
        body = VALID_TEST_PLAN
        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert response.status_code == 403
        assert "loopback" in response.json()["error"].lower()

    def test_non_loopback_request_is_rejected_on_status_endpoint(self) -> None:
        client = Client(REMOTE_ADDR="192.168.1.10")
        response = client.get("/api/runs/some-id/")
        assert response.status_code == 403

    def test_non_loopback_request_is_rejected_on_result_endpoint(self) -> None:
        client = Client(REMOTE_ADDR="2001:db8::1")
        response = client.get("/api/runs/some-id/result/")
        assert response.status_code == 403

    def test_ipv6_loopback_is_allowed(self) -> None:
        client = Client(REMOTE_ADDR="::1")
        response = client.get("/api/runs/nonexistent/")
        assert response.status_code == 404  # passes guard, fails lookup

    def test_malformed_remote_addr_is_rejected(self) -> None:
        client = Client(REMOTE_ADDR="not-an-ip")
        response = client.get("/api/runs/anything/")
        assert response.status_code == 403

    def test_opt_out_setting_allows_non_loopback(self) -> None:
        from django.test import override_settings

        with override_settings(API_ALLOW_NON_LOCAL=True):
            client = Client(REMOTE_ADDR="10.0.0.5")
            response = client.get("/api/runs/missing/")
            assert response.status_code == 404  # guard bypassed, lookup misses

    def test_non_loopback_method_mismatch_returns_403_not_405(self) -> None:
        # Regression: loopback guard must run before method dispatch, so a
        # non-loopback caller using the wrong HTTP method gets 403 (guard
        # rejection), not 405 (method-not-allowed), avoiding endpoint/method
        # disclosure to non-loopback clients.
        client = Client(REMOTE_ADDR="10.0.0.5")
        # GET on the POST-only create endpoint.
        response = client.get("/api/runs/")
        assert response.status_code == 403
        # POST on the GET-only status endpoint.
        response = client.post("/api/runs/some-id/")
        assert response.status_code == 403
