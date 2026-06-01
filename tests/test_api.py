import json
from unittest.mock import patch

import pytest
from django.test import Client

from conformance.api.run_store import MAX_TERMINAL_RECORDS, RunConflictError, RunStore, run_store

# ─── RunStore unit tests ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestRunStore:
    def test_create_run_returns_pending_record(self) -> None:
        store = RunStore()
        record = store.create_run()
        assert record.status == "pending"
        assert record.run_id
        assert record.created_at is not None

    def test_create_run_rejects_second_active_run(self) -> None:
        store = RunStore()
        store.create_run()
        with pytest.raises(RunConflictError):
            store.create_run()

    def test_create_run_allows_new_after_completion(self) -> None:
        store = RunStore()
        first = store.create_run()
        store.mark_running(first.run_id)
        store.mark_completed(first.run_id, result={"status": "passed"})
        second = store.create_run()
        assert second.run_id != first.run_id

    def test_create_run_allows_new_after_failure(self) -> None:
        store = RunStore()
        first = store.create_run()
        store.mark_running(first.run_id)
        store.mark_failed(first.run_id, error="boom")
        second = store.create_run()
        assert second.run_id != first.run_id

    def test_get_run_returns_none_for_unknown_id(self) -> None:
        store = RunStore()
        assert store.get_run("nonexistent") is None

    def test_mark_running_sets_started_at(self) -> None:
        store = RunStore()
        record = store.create_run()
        store.mark_running(record.run_id)
        updated = store.get_run(record.run_id)
        assert updated is not None
        assert updated.status == "running"
        assert updated.started_at is not None

    def test_mark_completed_stores_result(self) -> None:
        store = RunStore()
        record = store.create_run()
        store.mark_running(record.run_id)
        store.mark_completed(record.run_id, result={"environment": "test"})
        updated = store.get_run(record.run_id)
        assert updated is not None
        assert updated.status == "completed"
        assert updated.result == {"environment": "test"}
        assert updated.finished_at is not None

    def test_mark_failed_stores_error(self) -> None:
        store = RunStore()
        record = store.create_run()
        store.mark_running(record.run_id)
        store.mark_failed(record.run_id, error="timeout")
        updated = store.get_run(record.run_id)
        assert updated is not None
        assert updated.status == "failed"
        assert updated.error == "timeout"

    def test_to_status_json_minimal(self) -> None:
        store = RunStore()
        record = store.create_run()
        status_json = record.to_status_json()
        assert status_json["id"] == record.run_id
        assert status_json["status"] == "pending"
        assert "createdAt" in status_json
        assert "startedAt" not in status_json
        assert "finishedAt" not in status_json

    def test_to_status_json_completed(self) -> None:
        store = RunStore()
        record = store.create_run()
        store.mark_running(record.run_id)
        store.mark_completed(record.run_id, result={"status": "passed"})
        status_json = record.to_status_json()
        assert status_json["status"] == "completed"
        assert "startedAt" in status_json
        assert "finishedAt" in status_json

    def test_get_run_returns_snapshot_not_live_reference(self) -> None:
        store = RunStore()
        record = store.create_run()
        snapshot = store.get_run(record.run_id)
        assert snapshot is not None
        store.mark_running(record.run_id)
        # Snapshot captured before mark_running — must still read "pending"
        assert snapshot.status == "pending"


# ─── API endpoint integration tests ─────────────────────────────────────────

VALID_CONFIG = {
    "environment": "test-env",
    "discoveryUrl": "https://example.com/.well-known/openid-configuration",
}

VALID_MANIFEST = {
    "schemaVersion": "v0",
    "name": "Test manifest",
    "tests": [
        {
            "id": "test-1",
            "name": "Test endpoint",
            "request": {
                "method": "GET",
                "url": "https://example.com/test",
            },
            "assertions": [
                {"type": "http_status", "expected": 200},
            ],
        }
    ],
}


@pytest.fixture(autouse=True)
def _reset_run_store() -> None:
    """Reset the global run store between tests to avoid cross-contamination."""
    run_store.reset()


@pytest.mark.integration
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

    def test_rejects_missing_config(self) -> None:
        client = Client()
        response = client.post("/api/runs/", data=json.dumps({}), content_type="application/json")
        assert response.status_code == 400
        assert "config" in response.json()["error"]

    def test_rejects_invalid_config(self) -> None:
        client = Client()
        body = {"config": {"environment": "test"}}  # missing discoveryUrl
        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert response.status_code == 400
        assert "Config validation failed" in response.json()["error"]

    def test_rejects_invalid_manifest(self) -> None:
        client = Client()
        body = {"config": VALID_CONFIG, "manifest": {"schemaVersion": "v99"}}
        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert response.status_code == 400
        assert "Manifest validation failed" in response.json()["error"]

    @patch("conformance.api.views._execute_run")
    def test_creates_run_and_returns_201(self, mock_execute: object) -> None:
        client = Client()
        body = {"config": VALID_CONFIG}
        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "pending"
        assert "id" in data
        assert "createdAt" in data

    @patch("conformance.api.views._execute_run")
    def test_creates_run_with_manifest_and_returns_201(self, mock_execute: object) -> None:
        client = Client()
        body = {"config": VALID_CONFIG, "manifest": VALID_MANIFEST}
        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "pending"
        assert "id" in data

    @patch("conformance.api.views._execute_run")
    def test_rejects_second_concurrent_run(self, mock_execute: object) -> None:
        client = Client()
        body = {"config": VALID_CONFIG}
        first = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert first.status_code == 201
        second = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert second.status_code == 409
        assert "already active" in second.json()["error"]

    def test_get_method_not_allowed(self) -> None:
        client = Client()
        response = client.get("/api/runs/")
        assert response.status_code == 405


@pytest.mark.integration
class TestGetRunStatusEndpoint:
    def test_returns_404_for_unknown_id(self) -> None:
        client = Client()
        response = client.get("/api/runs/nonexistent/")
        assert response.status_code == 404

    @patch("conformance.api.views._execute_run")
    def test_returns_run_status(self, mock_execute: object) -> None:
        client = Client()
        body = {"config": VALID_CONFIG}
        create_resp = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        run_id = create_resp.json()["id"]
        response = client.get(f"/api/runs/{run_id}/")
        assert response.status_code == 200
        assert response.json()["id"] == run_id
        assert response.json()["status"] == "pending"

    def test_post_method_not_allowed(self) -> None:
        client = Client()
        response = client.post("/api/runs/some-id/", data="{}", content_type="application/json")
        assert response.status_code == 405


@pytest.mark.integration
class TestGetRunResultEndpoint:
    def test_returns_404_for_unknown_id(self) -> None:
        client = Client()
        response = client.get("/api/runs/nonexistent/result/")
        assert response.status_code == 404

    @patch("conformance.api.views._execute_run")
    def test_returns_409_when_run_not_complete(self, mock_execute: object) -> None:
        client = Client()
        body = {"config": VALID_CONFIG}
        create_resp = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        run_id = create_resp.json()["id"]
        response = client.get(f"/api/runs/{run_id}/result/")
        assert response.status_code == 409
        assert "not completed" in response.json()["error"]

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

    def test_returns_500_when_run_failed(self) -> None:
        client = Client()
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_failed(record.run_id, error="Internal engine error")

        response = client.get(f"/api/runs/{record.run_id}/result/")
        assert response.status_code == 500
        assert "failed internally" in response.json()["error"]
        assert "detail" not in response.json()


# ─── Bounded run-store history ──────────────────────────────────────────────


@pytest.mark.unit
class TestRunStoreBoundedHistory:
    def test_terminal_records_capped_at_maximum(self) -> None:
        store = RunStore()
        # Create MAX + 5 fully-completed runs so each create_run triggers prune.
        for i in range(MAX_TERMINAL_RECORDS + 5):
            record = store.create_run()
            store.mark_running(record.run_id)
            store.mark_completed(record.run_id, result={"i": i})
        # Plus one more pending run to confirm active is preserved.
        active = store.create_run()
        assert len(store._runs) == MAX_TERMINAL_RECORDS + 1
        assert active.run_id in store._runs

    def test_pending_or_running_records_are_never_pruned(self) -> None:
        store = RunStore()
        # Fill with terminal records.
        for _ in range(MAX_TERMINAL_RECORDS + 3):
            record = store.create_run()
            store.mark_running(record.run_id)
            store.mark_completed(record.run_id, result={})
        active = store.create_run()  # pending; prune should retain it
        store.mark_running(active.run_id)
        # Force a prune-eligible event by completing then creating again.
        store.mark_completed(active.run_id, result={})
        new_active = store.create_run()
        assert new_active.run_id in store._runs
        assert store.get_run(new_active.run_id) is not None

    def test_oldest_terminal_records_evicted_first(self) -> None:
        store = RunStore()
        first_ids = []
        for _ in range(MAX_TERMINAL_RECORDS):
            record = store.create_run()
            store.mark_running(record.run_id)
            store.mark_completed(record.run_id, result={})
            first_ids.append(record.run_id)
        # Add one more terminal record; the very first one should be evicted.
        extra = store.create_run()
        store.mark_running(extra.run_id)
        store.mark_completed(extra.run_id, result={})
        # Trigger prune via a new create_run.
        store.create_run()
        assert first_ids[0] not in store._runs
        assert first_ids[-1] in store._runs
        assert extra.run_id in store._runs


# ─── Loopback guard ─────────────────────────────────────────────────────────


@pytest.mark.integration
class TestLoopbackGuard:
    def test_loopback_request_is_allowed_by_default(self) -> None:
        # Django test client uses REMOTE_ADDR=127.0.0.1 by default.
        client = Client()
        body = {"config": VALID_CONFIG}
        with patch("conformance.api.views._execute_run"):
            response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert response.status_code == 201

    def test_non_loopback_request_is_rejected_with_403(self) -> None:
        client = Client(REMOTE_ADDR="10.0.0.5")
        body = {"config": VALID_CONFIG}
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


# ─── Run-log endpoint ─────────────────────────────────────────────────────


@pytest.mark.integration
class TestGetRunLogEndpoint:
    """``GET /api/runs/<id>/log/`` exposes the structured execution log."""

    def test_returns_404_for_unknown_id(self) -> None:
        """Unknown run IDs yield 404 with no log content."""
        client = Client()
        response = client.get("/api/runs/nonexistent/log/")
        assert response.status_code == 404

    def test_returns_ndjson_for_known_run(self) -> None:
        """The endpoint streams ``application/x-ndjson`` with one JSON object per line."""
        client = Client()
        record = run_store.create_run()
        # Emit a couple of events into the live buffer attached to the run.
        assert record.execution_logger is not None
        record.execution_logger.emit("run-started")
        record.execution_logger.emit("run-completed", payload={"summary": {"total": 0}})

        response = client.get(f"/api/runs/{record.run_id}/log/")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/x-ndjson"
        lines = response.content.decode("utf-8").rstrip("\n").split("\n")
        parsed = [json.loads(line) for line in lines]
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
        body = response.content.decode("utf-8").rstrip("\n")
        assert len(body.split("\n")) == 1

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
