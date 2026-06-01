import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from django.test import Client

from conformance.api.run_store import RunConflictError, RunRecord, RunStore, run_store

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
    with run_store._lock:
        run_store._runs.clear()
        run_store._active_run_id = None


@pytest.mark.integration
class TestCreateRunEndpoint:
    def test_rejects_non_json_body(self) -> None:
        client = Client()
        response = client.post("/api/runs/", data="not json", content_type="application/json")
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
        # Manually insert a completed run
        record = RunRecord(
            run_id="test-completed",
            status="completed",
            created_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            result={"environment": "test", "status": "passed"},
        )
        run_store._runs["test-completed"] = record
        run_store._active_run_id = "test-completed"

        response = client.get("/api/runs/test-completed/result/")
        assert response.status_code == 200
        assert response.json() == {"environment": "test", "status": "passed"}

    def test_returns_500_when_run_failed(self) -> None:
        client = Client()
        record = RunRecord(
            run_id="test-failed",
            status="failed",
            created_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            error="Internal engine error",
        )
        run_store._runs["test-failed"] = record
        run_store._active_run_id = "test-failed"

        response = client.get("/api/runs/test-failed/result/")
        assert response.status_code == 500
        assert "failed internally" in response.json()["error"]
        assert "detail" not in response.json()
