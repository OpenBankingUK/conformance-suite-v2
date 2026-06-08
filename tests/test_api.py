import json
import time
from collections.abc import Callable
from unittest.mock import Mock, patch

import pytest
from django.test import Client

from conformance.api.auth_session_store import auth_session_store
from conformance.api.run_lifecycle import BrowserParticipantActionLogger
from conformance.api.run_store import MAX_TERMINAL_RECORDS, RunConflictError, RunStore, run_store
from conformance.approved_releases import APPROVED_RELEASE_POLICY_SCHEMA_VERSION
from conformance.execution_log import BufferedExecutionLogger

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

    RAW_PSU_AUTHORIZATION_URL = (
        "https://auth.example.com/authorize?client_id=client-123&request=raw-jws-value&state=browser-psu-state"
    )

    def test_participant_action_exposes_raw_psu_url_on_run_snapshot(self) -> None:
        """Pending browser PSU actions are readable from in-memory run state."""
        store = RunStore()
        record = store.create_run()

        store.set_participant_action(record.run_id, step_id="psu", url=self.RAW_PSU_AUTHORIZATION_URL)

        action = store.get_participant_action(record.run_id)
        assert action is not None
        assert action.type == "psu-authorization-url"
        assert action.step_id == "psu"
        assert action.url == self.RAW_PSU_AUTHORIZATION_URL
        assert action.created_at is not None

        snapshot = store.get_run(record.run_id)
        assert snapshot is not None
        assert snapshot.participant_action is not None
        assert snapshot.participant_action.url == self.RAW_PSU_AUTHORIZATION_URL

    def test_participant_action_snapshot_is_not_live_mutable_state(self) -> None:
        """Run snapshots detach participant actions from the live store."""
        store = RunStore()
        record = store.create_run()
        store.set_participant_action(record.run_id, step_id="psu", url=self.RAW_PSU_AUTHORIZATION_URL)

        first_snapshot = store.get_run(record.run_id)
        second_snapshot = store.get_run(record.run_id)

        assert first_snapshot is not None
        assert second_snapshot is not None
        assert first_snapshot.participant_action is not None
        assert second_snapshot.participant_action is not None
        assert first_snapshot.participant_action is not second_snapshot.participant_action

    def test_participant_action_is_not_persisted_to_status_result_or_log(self) -> None:
        """Raw browser PSU URLs stay out of durable/public run artifacts."""
        store = RunStore()
        record = store.create_run()
        store.set_participant_action(record.run_id, step_id="psu", url=self.RAW_PSU_AUTHORIZATION_URL)

        snapshot = store.get_run(record.run_id)
        assert snapshot is not None
        assert self.RAW_PSU_AUTHORIZATION_URL not in json.dumps(snapshot.to_status_json())

        log_bytes = store.get_run_log_bytes(record.run_id)
        assert log_bytes is not None
        assert self.RAW_PSU_AUTHORIZATION_URL.encode("utf-8") not in log_bytes

        store.mark_running(record.run_id)
        store.mark_completed(record.run_id, result={"status": "passed"})

        completed = store.get_run(record.run_id)
        assert completed is not None
        assert completed.result is not None
        assert self.RAW_PSU_AUTHORIZATION_URL not in json.dumps(completed.result)

    def test_clear_participant_action_removes_matching_pending_action(self) -> None:
        """Callback and matching step-completion hooks can clear the active action."""
        store = RunStore()
        record = store.create_run()
        store.set_participant_action(record.run_id, step_id="psu", url=self.RAW_PSU_AUTHORIZATION_URL)

        store.clear_participant_action(record.run_id, step_id="token")
        assert store.get_participant_action(record.run_id) is not None

        store.clear_participant_action(record.run_id, step_id="psu")
        assert store.get_participant_action(record.run_id) is None
        snapshot = store.get_run(record.run_id)
        assert snapshot is not None
        assert snapshot.participant_action is None

    def test_clear_participant_action_without_step_id_clears_active_action(self) -> None:
        """Run-level cleanup hooks can clear the active browser action."""
        store = RunStore()
        record = store.create_run()
        store.set_participant_action(record.run_id, step_id="psu", url=self.RAW_PSU_AUTHORIZATION_URL)

        store.clear_participant_action(record.run_id)

        assert store.get_participant_action(record.run_id) is None

    def test_terminal_transitions_clear_participant_action(self) -> None:
        """Completed and failed runs must not retain raw browser PSU URLs."""
        store = RunStore()
        completed = store.create_run()
        store.set_participant_action(completed.run_id, step_id="psu", url=self.RAW_PSU_AUTHORIZATION_URL)
        store.mark_running(completed.run_id)
        store.mark_completed(completed.run_id, result={"status": "passed"})

        failed = store.create_run()
        store.set_participant_action(failed.run_id, step_id="psu", url=self.RAW_PSU_AUTHORIZATION_URL)
        store.mark_running(failed.run_id)
        store.mark_failed(failed.run_id, error="boom")

        assert store.get_participant_action(completed.run_id) is None
        assert store.get_participant_action(failed.run_id) is None


@pytest.mark.unit
class TestBrowserParticipantActionLogger:
    """Unit coverage for the API-layer browser action logger decorator."""

    RAW_PSU_AUTHORIZATION_URL = TestRunStore.RAW_PSU_AUTHORIZATION_URL

    def test_stores_raw_psu_url_in_run_state_and_forwards_masked_event(self) -> None:
        """Raw PSU URLs are in-memory only while logs keep masking semantics."""
        store = RunStore()
        record = store.create_run()
        wrapped = BufferedExecutionLogger(run_id=record.run_id, developer_mode=False)
        logger = BrowserParticipantActionLogger(wrapped, run_id=record.run_id, store=store)

        logger.emit(
            "psu-authorization-url",
            step_id="psu",
            payload={
                "url": self.RAW_PSU_AUTHORIZATION_URL,
                "client_id": "client-123",
                "request_object": "raw-jws-value",
            },
        )

        action = store.get_participant_action(record.run_id)
        assert action is not None
        assert action.step_id == "psu"
        assert action.url == self.RAW_PSU_AUTHORIZATION_URL
        events = wrapped.events()
        assert [event.type for event in events] == ["psu-authorization-url"]
        assert self.RAW_PSU_AUTHORIZATION_URL not in wrapped.to_ndjson_bytes().decode("utf-8")

    def test_malformed_psu_url_event_is_forwarded_without_storing_action(self) -> None:
        """Malformed PSU URL events do not create browser actions."""
        store = RunStore()
        record = store.create_run()
        wrapped = BufferedExecutionLogger(run_id=record.run_id, developer_mode=False)
        logger = BrowserParticipantActionLogger(wrapped, run_id=record.run_id, store=store)

        logger.emit("psu-authorization-url", payload={"url": self.RAW_PSU_AUTHORIZATION_URL})

        assert store.get_participant_action(record.run_id) is None
        assert [event.type for event in wrapped.events()] == ["psu-authorization-url"]

    def test_matching_step_completion_clears_action(self) -> None:
        """Only the step that raised the browser action clears it on completion."""
        store = RunStore()
        record = store.create_run()
        wrapped = BufferedExecutionLogger(run_id=record.run_id, developer_mode=False)
        logger = BrowserParticipantActionLogger(wrapped, run_id=record.run_id, store=store)
        logger.emit("psu-authorization-url", step_id="psu", payload={"url": self.RAW_PSU_AUTHORIZATION_URL})

        logger.emit("step-completed", step_id="token", payload={"status": "passed"})
        assert store.get_participant_action(record.run_id) is not None

        logger.emit("step-completed", step_id="psu", payload={"status": "passed"})
        assert store.get_participant_action(record.run_id) is None

    def test_callback_received_clears_action(self) -> None:
        """Callback capture clears the active browser action."""
        store = RunStore()
        record = store.create_run()
        wrapped = BufferedExecutionLogger(run_id=record.run_id, developer_mode=False)
        logger = BrowserParticipantActionLogger(wrapped, run_id=record.run_id, store=store)
        logger.emit("psu-authorization-url", step_id="psu", payload={"url": self.RAW_PSU_AUTHORIZATION_URL})

        logger.emit("auth-callback-received", payload={"state": "state", "code": "auth-code"})

        assert store.get_participant_action(record.run_id) is None

    def test_terminal_events_clear_action(self) -> None:
        """Run-level terminal events clear any active browser action."""
        store = RunStore()
        completed = store.create_run()
        completed_logger = BrowserParticipantActionLogger(
            BufferedExecutionLogger(run_id=completed.run_id, developer_mode=False),
            run_id=completed.run_id,
            store=store,
        )
        completed_logger.emit("psu-authorization-url", step_id="psu", payload={"url": self.RAW_PSU_AUTHORIZATION_URL})
        completed_logger.emit("run-completed", payload={"status": "passed"})
        assert store.get_participant_action(completed.run_id) is None

        store.mark_running(completed.run_id)
        store.mark_completed(completed.run_id, result={})
        failed = store.create_run()
        failed_logger = BrowserParticipantActionLogger(
            BufferedExecutionLogger(run_id=failed.run_id, developer_mode=False),
            run_id=failed.run_id,
            store=store,
        )
        failed_logger.emit("psu-authorization-url", step_id="psu", payload={"url": self.RAW_PSU_AUTHORIZATION_URL})
        failed_logger.emit("application-error", payload={"message": "boom"})
        assert store.get_participant_action(failed.run_id) is None


# ─── API endpoint integration tests ─────────────────────────────────────────

VALID_CONFIG = {
    "environment": "test-env",
    "discoveryUrl": "https://example.com/.well-known/openid-configuration",
}

SUITE_CONFIG = {
    **VALID_CONFIG,
    "testSuite": {
        "standard": "ob-read-write",
        "specVersion": "v4.0",
        "profile": "fapi1-advanced",
        "suite": "discovery-jwks",
    },
}

PSU_AUTH_STARTER_CONFIG = {
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
    },
}

AIS_SLICE_CONFIG = {
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


def _wait_for_value[WaitValue](producer: Callable[[], WaitValue | None], *, timeout_seconds: float) -> WaitValue:
    """Poll until ``producer`` returns a non-None value or time expires.

    Args:
        producer: Callback that returns a value once the awaited condition is
            satisfied, or ``None`` while the caller should keep waiting.
        timeout_seconds: Maximum wall-clock duration to wait.

    Returns:
        The first non-None value returned by ``producer``.

    Raises:
        AssertionError: If the timeout expires before a value is produced.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        value = producer()
        if value is not None:
            return value
        time.sleep(0.01)
    raise AssertionError(f"Timed out after {timeout_seconds}s waiting for asynchronous API run")


@pytest.fixture(autouse=True)
def _reset_global_stores() -> None:
    """Reset process-local singleton stores between tests."""
    run_store.reset()
    auth_session_store.reset()


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

    @patch("conformance.api.run_lifecycle._execute_run")
    def test_creates_run_and_returns_201(self, mock_execute: Mock) -> None:
        client = Client()
        body = {"config": VALID_CONFIG}
        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "pending"
        assert "id" in data
        assert "createdAt" in data
        record = run_store.get_run(data["id"])
        assert record is not None
        assert record.status == "pending"
        _wait_for_value(
            lambda: True if mock_execute.call_args is not None else None,
            timeout_seconds=1.0,
        )
        assert mock_execute.call_args is not None
        assert mock_execute.call_args.args[0] == data["id"]
        assert mock_execute.call_args.args[2:] == (None, None, None)
        assert mock_execute.call_args.kwargs == {"browser_psu_prompts": False}

    @patch("conformance.api.run_lifecycle._execute_run")
    def test_creates_run_with_manifest_and_returns_201(self, mock_execute: object) -> None:
        client = Client()
        body = {"config": VALID_CONFIG, "manifest": VALID_MANIFEST}
        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "pending"
        assert "id" in data

    @patch("conformance.api.run_lifecycle._execute_run")
    def test_creates_run_with_config_resolved_suite(self, mock_execute: Mock) -> None:
        """A config ``testSuite`` supplies the manifest when inline manifest is absent.

        Args:
            mock_execute: Patched lifecycle worker used to inspect run inputs.
        """
        client = Client()
        response = client.post(
            "/api/runs/",
            data=json.dumps({"config": SUITE_CONFIG}),
            content_type="application/json",
        )

        assert response.status_code == 201
        data = response.json()
        _wait_for_value(
            lambda: True if mock_execute.call_args is not None else None,
            timeout_seconds=1.0,
        )
        assert mock_execute.call_args is not None
        manifest = mock_execute.call_args.args[2]
        plan = mock_execute.call_args.args[3]
        suite_metadata = mock_execute.call_args.args[4]
        assert manifest.name == "Open Banking Read/Write v4.0 FAPI 1 Advanced discovery/JWKS smoke suite"
        assert plan.selected_step_ids() == ["openid-discovery", "jwks-fetch"]
        assert suite_metadata.catalog_id == "ob-read-write/v4.0/fapi1-advanced/discovery-jwks"
        assert data["status"] == "pending"

    @patch("conformance.api.run_lifecycle._execute_run")
    def test_creates_run_with_psu_auth_starter_config_resolved_suite(self, mock_execute: Mock) -> None:
        """A ``psu-auth-starter`` testSuite config resolves the bundled PSU auth starter manifest.

        Args:
            mock_execute: Patched lifecycle worker used to inspect run inputs.
        """
        client = Client()
        response = client.post(
            "/api/runs/",
            data=json.dumps({"config": PSU_AUTH_STARTER_CONFIG}),
            content_type="application/json",
        )

        assert response.status_code == 201
        _wait_for_value(
            lambda: True if mock_execute.call_args is not None else None,
            timeout_seconds=1.0,
        )
        assert mock_execute.call_args is not None
        manifest = mock_execute.call_args.args[2]
        plan = mock_execute.call_args.args[3]
        suite_metadata = mock_execute.call_args.args[4]
        assert manifest.name == "Open Banking Read/Write v4.0 FAPI 1 Advanced PSU auth starter suite"
        assert plan.selected_step_ids() == ["openid-discovery", "jwks-fetch", "psu-authorization"]
        assert suite_metadata.catalog_id == "ob-read-write/v4.0/fapi1-advanced/psu-auth-starter"

    @patch("conformance.api.run_lifecycle._execute_run")
    def test_creates_run_with_ais_slice_config_resolved_suite(self, mock_execute: Mock) -> None:
        """A v4 AIS ``testSuite`` config resolves the bundled AIS slice manifest.

        Args:
            mock_execute: Patched lifecycle worker used to inspect run inputs.
        """
        client = Client()
        response = client.post(
            "/api/runs/",
            data=json.dumps({"config": AIS_SLICE_CONFIG}),
            content_type="application/json",
        )

        assert response.status_code == 201
        _wait_for_value(
            lambda: True if mock_execute.call_args is not None else None,
            timeout_seconds=1.0,
        )
        assert mock_execute.call_args is not None
        manifest = mock_execute.call_args.args[2]
        plan = mock_execute.call_args.args[3]
        suite_metadata = mock_execute.call_args.args[4]
        assert manifest.name == "Open Banking Read/Write v4.0 FAPI 1 Advanced AIS certification slice"
        assert plan.selected_step_ids() == [
            "openid-discovery",
            "jwks-fetch",
            "psu-authorization",
            "token-exchange",
            "account-access-consent",
            "accounts-list",
            "account-balances",
            "account-transactions",
        ]
        assert suite_metadata.catalog_id == "ob-read-write/v4.0/fapi1-advanced/ais-certification-slice"

    @patch("conformance.api.run_lifecycle._execute_run")
    def test_inline_manifest_overrides_config_resolved_suite(self, mock_execute: Mock) -> None:
        """Inline API manifests remain explicit overrides for authoring workflows.

        Args:
            mock_execute: Patched lifecycle worker used to inspect run inputs.
        """
        client = Client()
        inline_manifest = {
            "schemaVersion": "v1",
            "name": "inline override",
            "steps": [
                {
                    "id": "inline-step",
                    "name": "Inline step",
                    "request": {"method": "GET", "url": "https://example.com/inline"},
                    "assertions": [{"type": "http_status", "expected": 200}],
                }
            ],
        }

        response = client.post(
            "/api/runs/",
            data=json.dumps({"config": SUITE_CONFIG, "manifest": inline_manifest}),
            content_type="application/json",
        )

        assert response.status_code == 201
        _wait_for_value(
            lambda: True if mock_execute.call_args is not None else None,
            timeout_seconds=1.0,
        )
        assert mock_execute.call_args is not None
        manifest = mock_execute.call_args.args[2]
        suite_metadata = mock_execute.call_args.args[4]
        assert manifest.name == "inline override"
        assert [step.id for step in manifest.steps] == ["inline-step"]
        assert suite_metadata is None

    @patch("conformance.api.run_lifecycle._execute_run")
    def test_creates_run_with_manifest_and_valid_deselection(self, mock_execute: object) -> None:
        """A valid ``deselectStepIds`` against a v1 manifest is accepted."""
        client = Client()
        v1_manifest = {
            "schemaVersion": "v1",
            "name": "plan-api",
            "steps": [
                {
                    "id": "a",
                    "name": "A",
                    "request": {"method": "GET", "url": "https://example.com/a"},
                    "assertions": [{"type": "http_status", "expected": 200}],
                },
                {
                    "id": "b",
                    "name": "B",
                    "optional": True,
                    "request": {"method": "GET", "url": "https://example.com/b"},
                    "assertions": [{"type": "http_status", "expected": 200}],
                },
            ],
        }
        body = {"config": VALID_CONFIG, "manifest": v1_manifest, "deselectStepIds": ["a"]}
        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert response.status_code == 201

    @patch("conformance.api.run_lifecycle._execute_run")
    def test_creates_run_with_config_resolved_suite_and_valid_deselection(self, mock_execute: Mock) -> None:
        """``deselectStepIds`` is valid against a config-resolved suite manifest.

        Args:
            mock_execute: Patched lifecycle worker used to inspect run inputs.
        """
        client = Client()
        body = {"config": SUITE_CONFIG, "deselectStepIds": ["jwks-fetch"]}

        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")

        assert response.status_code == 201
        _wait_for_value(
            lambda: True if mock_execute.call_args is not None else None,
            timeout_seconds=1.0,
        )
        assert mock_execute.call_args is not None
        plan = mock_execute.call_args.args[3]
        assert plan.selected_step_ids() == ["openid-discovery"]

    @patch("conformance.api.run_lifecycle._execute_run")
    def test_rejects_deselect_unknown_step_id_for_ais_config_resolved_suite(self, mock_execute: Mock) -> None:
        """Unknown step ids are rejected for config-selected AIS runs.

        Args:
            mock_execute: Patched lifecycle worker that must not run.
        """
        client = Client()
        body = {"config": AIS_SLICE_CONFIG, "deselectStepIds": ["ghost-step"]}

        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")

        assert response.status_code == 400
        assert "Plan validation failed" in response.json()["error"]
        mock_execute.assert_not_called()

    @patch("conformance.api.run_lifecycle._execute_run")
    def test_rejects_deselect_unknown_step_id(self, mock_execute: object) -> None:
        """An unknown step id in ``deselectStepIds`` returns 400."""
        client = Client()
        body = {"config": VALID_CONFIG, "manifest": VALID_MANIFEST, "deselectStepIds": ["ghost"]}
        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert response.status_code == 400
        assert "Plan validation failed" in response.json()["error"]

    def test_rejects_deselect_without_manifest(self) -> None:
        """``deselectStepIds`` requires an inline or config-resolved manifest."""
        client = Client()
        body = {"config": VALID_CONFIG, "deselectStepIds": ["a"]}
        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert response.status_code == 400
        assert "deselectStepIds" in response.json()["error"]

    def test_rejects_invalid_config_suite_resolution(self) -> None:
        """Catalog resolution errors are surfaced as HTTP 400 before run start."""
        from conformance.suite_catalog import SuiteCatalogError

        client = Client()
        with patch("conformance.api.views.resolve_suite", side_effect=SuiteCatalogError("missing resource")):
            response = client.post(
                "/api/runs/",
                data=json.dumps({"config": SUITE_CONFIG}),
                content_type="application/json",
            )

        assert response.status_code == 400
        assert "Suite resolution failed" in response.json()["error"]

    def test_rejects_deselect_not_array_of_strings(self) -> None:
        """``deselectStepIds`` must be an array of strings; otherwise 400."""
        client = Client()
        body = {"config": VALID_CONFIG, "manifest": VALID_MANIFEST, "deselectStepIds": [1, 2]}
        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert response.status_code == 400
        assert "array of strings" in response.json()["error"]

    @patch("conformance.api.run_lifecycle._execute_run")
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
class TestPsuAuthorizationApiRun:
    """End-to-end API coverage for manual PSU authorisation manifest runs."""

    def test_manifest_run_passes_after_synthetic_callback(self) -> None:
        """A PSU manifest run can be completed through the public callback path."""
        client = Client()
        state = "api-psu-state-" + "x" * 32
        manifest = {
            "schemaVersion": "v1",
            "name": "api psu authorisation",
            "steps": [
                {
                    "kind": "psu-authorization",
                    "id": "psu",
                    "name": "PSU authorisation",
                    "mode": "manual",
                    "authorizationEndpoint": "https://auth.example.com/authorize",
                    "clientId": "client-123",
                    "redirectUri": "https://conformance.example.com/callback",
                    "state": state,
                    "timeoutSeconds": 3,
                    "mandatory": True,
                }
            ],
        }

        create_response = client.post(
            "/api/runs/",
            data=json.dumps({"config": VALID_CONFIG, "manifest": manifest}),
            content_type="application/json",
        )
        assert create_response.status_code == 201
        run_id = create_response.json()["id"]

        _wait_for_value(
            lambda: True if auth_session_store.get(run_id, state) is not None else None,
            timeout_seconds=2.0,
        )
        callback_response = client.get("/callback/", {"state": state, "code": "api-auth-code"})
        assert callback_response.status_code == 200

        def completed_result() -> dict[str, object] | None:
            """Return result JSON once the asynchronous run has completed.

            Returns:
                Result JSON dictionary when available, otherwise ``None``
                while the run is still pending/running.

            Raises:
                AssertionError: If the result endpoint reports an unexpected
                terminal or internal-error response.
            """
            result_response = client.get(f"/api/runs/{run_id}/result/")
            if result_response.status_code == 200:
                body = result_response.json()
                assert isinstance(body, dict)
                return body
            if result_response.status_code == 409:
                return None
            raise AssertionError(
                f"Unexpected result response {result_response.status_code}: {result_response.content!r}"
            )

        result = _wait_for_value(completed_result, timeout_seconds=4.0)
        assert result["status"] == "passed"
        assert result["summary"] == {"total": 1, "passed": 1, "failed": 0, "warn": 0, "skipped": 0}
        steps = result["steps"]
        assert isinstance(steps, list)
        assert steps[0]["name"] == "psu"
        assert steps[0]["status"] == "passed"

        log_response = client.get(f"/api/runs/{run_id}/log/")
        assert log_response.status_code == 200
        events = [json.loads(line) for line in log_response.content.decode("utf-8").splitlines()]
        assert "psu-authorization-url" in [event["type"] for event in events]


@pytest.mark.integration
class TestGetRunStatusEndpoint:
    def test_returns_404_for_unknown_id(self) -> None:
        client = Client()
        response = client.get("/api/runs/nonexistent/")
        assert response.status_code == 404

    @patch("conformance.api.run_lifecycle._execute_run")
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

    @patch("conformance.api.run_lifecycle._execute_run")
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

    def test_pruning_terminal_records_removes_participant_actions(self) -> None:
        """Pruned terminal run records must not leave raw browser PSU URLs behind."""
        store = RunStore()
        first_record = store.create_run()
        store.set_participant_action(
            first_record.run_id,
            step_id="psu",
            url=TestRunStore.RAW_PSU_AUTHORIZATION_URL,
        )
        store.mark_running(first_record.run_id)
        store.mark_completed(first_record.run_id, result={})

        for _ in range(MAX_TERMINAL_RECORDS):
            record = store.create_run()
            store.mark_running(record.run_id)
            store.mark_completed(record.run_id, result={})

        store.create_run()

        assert store.get_run(first_record.run_id) is None
        assert store.get_participant_action(first_record.run_id) is None


# ─── Loopback guard ─────────────────────────────────────────────────────────


@pytest.mark.integration
class TestLoopbackGuard:
    def test_loopback_request_is_allowed_by_default(self) -> None:
        # Django test client uses REMOTE_ADDR=127.0.0.1 by default.
        client = Client()
        body = {"config": VALID_CONFIG}
        with patch("conformance.api.run_lifecycle._execute_run"):
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
        lines = response.content.decode("utf-8").rstrip("\n").split("\n")
        assert json.loads(lines[0])["type"] == "run-started"


# ─── Auth-session API endpoints ─────────────────────────────────────────────


@pytest.mark.integration
class TestRegisterAuthSessionEndpoint:
    """``POST /api/runs/<id>/auth-sessions/`` registers a PSU auth session."""

    def test_returns_201_with_server_generated_state(self) -> None:
        """A bodyless request returns 201 with a server-generated state."""
        client = Client()
        record = run_store.create_run()
        response = client.post(f"/api/runs/{record.run_id}/auth-sessions/")
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "awaiting"
        assert "createdAt" in body
        assert len(body["state"]) >= 32

    def test_accepts_caller_supplied_state_above_entropy_bar(self) -> None:
        """A caller-supplied state of sufficient length is accepted."""
        client = Client()
        record = run_store.create_run()
        state = "x" * 32
        response = client.post(
            f"/api/runs/{record.run_id}/auth-sessions/",
            data=json.dumps({"state": state}),
            content_type="application/json",
        )
        assert response.status_code == 201
        assert response.json()["state"] == state

    def test_rejects_caller_supplied_state_below_entropy_bar(self) -> None:
        """A short caller-supplied state is rejected with 400."""
        client = Client()
        record = run_store.create_run()
        response = client.post(
            f"/api/runs/{record.run_id}/auth-sessions/",
            data=json.dumps({"state": "short"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_rejects_non_string_state(self) -> None:
        """A non-string ``state`` field is rejected with 400."""
        client = Client()
        record = run_store.create_run()
        response = client.post(
            f"/api/runs/{record.run_id}/auth-sessions/",
            data=json.dumps({"state": 123}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_rejects_invalid_json_body(self) -> None:
        """Malformed JSON in the body yields 400."""
        client = Client()
        record = run_store.create_run()
        response = client.post(
            f"/api/runs/{record.run_id}/auth-sessions/",
            data="not json",
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_rejects_non_json_body_without_content_type(self) -> None:
        """A non-empty body is parsed regardless of Content-Type.

        Prevents the silent-drop bug where a caller posting
        ``{"state": ...}`` without ``Content-Type: application/json``
        would have their state ignored and receive 201 with a
        server-generated state instead.
        """
        client = Client()
        record = run_store.create_run()
        response = client.post(
            f"/api/runs/{record.run_id}/auth-sessions/",
            data="not json",
            content_type="text/plain",
        )
        assert response.status_code == 400

    def test_rejects_non_object_body(self) -> None:
        """A non-object JSON body is rejected with 400."""
        client = Client()
        record = run_store.create_run()
        response = client.post(
            f"/api/runs/{record.run_id}/auth-sessions/",
            data=json.dumps([1, 2]),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_rejects_multipart_with_fields(self) -> None:
        """``multipart/form-data`` carrying fields is rejected with 400.

        Guards against the silent-drop bug where a caller posting
        ``state`` as a multipart form field would otherwise be ignored
        and receive 201 with a server-generated state. The empty
        multipart envelope produced by Django's test client when
        ``client.post(url)`` is called without ``data`` is still
        accepted as bodyless (covered by
        :meth:`test_returns_201_with_server_generated_state`).
        """
        client = Client()
        record = run_store.create_run()
        response = client.post(
            f"/api/runs/{record.run_id}/auth-sessions/",
            data={"state": "y" * 40},
        )
        assert response.status_code == 400

    def test_returns_404_for_unknown_run(self) -> None:
        """An unknown run id yields 404."""
        client = Client()
        response = client.post("/api/runs/missing/auth-sessions/")
        assert response.status_code == 404

    def test_returns_409_for_terminal_run(self) -> None:
        """Registration against a completed run is rejected with 409."""
        client = Client()
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_completed(record.run_id, result={"status": "passed"})
        response = client.post(f"/api/runs/{record.run_id}/auth-sessions/")
        assert response.status_code == 409

    def test_returns_409_for_duplicate_state(self) -> None:
        """Re-registering the same caller-supplied state yields 409."""
        client = Client()
        record = run_store.create_run()
        state = "y" * 40
        first = client.post(
            f"/api/runs/{record.run_id}/auth-sessions/",
            data=json.dumps({"state": state}),
            content_type="application/json",
        )
        assert first.status_code == 201
        second = client.post(
            f"/api/runs/{record.run_id}/auth-sessions/",
            data=json.dumps({"state": state}),
            content_type="application/json",
        )
        assert second.status_code == 409

    def test_returns_400_when_per_run_cap_exceeded(self) -> None:
        """Registering past the per-run cap yields 400."""
        from conformance.api.auth_session_store import MAX_SESSIONS_PER_RUN

        client = Client()
        record = run_store.create_run()
        for _ in range(MAX_SESSIONS_PER_RUN):
            ok = client.post(f"/api/runs/{record.run_id}/auth-sessions/")
            assert ok.status_code == 201
        over = client.post(f"/api/runs/{record.run_id}/auth-sessions/")
        assert over.status_code == 400

    def test_emits_auth_session_registered_event(self) -> None:
        """Successful registration appends an ``auth-session-registered`` event."""
        client = Client()
        record = run_store.create_run()
        response = client.post(f"/api/runs/{record.run_id}/auth-sessions/")
        assert response.status_code == 201
        live = run_store._runs[record.run_id]  # noqa: SLF001 — read live logger
        assert live.execution_logger is not None
        events = live.execution_logger.events()
        types = [event.type for event in events]
        assert "auth-session-registered" in types
        registered = next(event for event in events if event.type == "auth-session-registered")
        assert registered.payload["state"] == response.json()["state"]
        assert registered.payload["status"] == "awaiting"

    def test_non_loopback_request_is_rejected_with_403(self) -> None:
        """The loopback guard applies to the register endpoint."""
        client = Client(REMOTE_ADDR="10.0.0.5")
        response = client.post("/api/runs/some-id/auth-sessions/")
        assert response.status_code == 403

    def test_rolls_back_session_when_run_terminates_during_register(self) -> None:
        """Race fix: a run completing mid-register must not leak a session.

        Simulates the run lifecycle transitioning the run to
        ``completed`` (and sweeping its sessions) between
        ``auth_session_store.register`` and the post-register run-record
        revalidation. The view must roll back the just-created session
        and return 409 instead of 201, preventing the session from
        outliving its parent run.
        """
        from unittest.mock import patch

        from conformance.api.auth_session_store import auth_session_store

        client = Client()
        record = run_store.create_run()
        original_get_run = run_store.get_run
        call_count = {"n": 0}

        def get_run_with_terminal_race(run_id: str) -> object:
            """Return the live record on the pre-check, terminate on revalidation."""
            call_count["n"] += 1
            if call_count["n"] == 2:
                run_store.mark_running(run_id)
                run_store.mark_completed(run_id, result={"status": "passed"})
            return original_get_run(run_id)

        with patch.object(run_store, "get_run", side_effect=get_run_with_terminal_race):
            response = client.post(f"/api/runs/{record.run_id}/auth-sessions/")

        assert response.status_code == 409
        assert response.json()["status"] == "completed"
        # The just-created session must have been rolled back.
        assert auth_session_store.for_run(record.run_id) == []


@pytest.mark.integration
class TestGetAuthSessionEndpoint:
    """``GET /api/runs/<id>/auth-sessions/<state>/`` returns session state."""

    def test_returns_awaiting_session(self) -> None:
        """A freshly registered session is returned with status ``awaiting``."""
        client = Client()
        record = run_store.create_run()
        registered = client.post(f"/api/runs/{record.run_id}/auth-sessions/").json()
        response = client.get(
            f"/api/runs/{record.run_id}/auth-sessions/{registered['state']}/",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == registered["state"]
        assert body["status"] == "awaiting"
        assert "createdAt" in body
        assert "code" not in body
        assert "capturedAt" not in body

    def test_returns_captured_session_with_code(self) -> None:
        """After ``capture_code`` the response includes the code and capturedAt."""
        from conformance.api.auth_session_store import auth_session_store

        client = Client()
        record = run_store.create_run()
        registered = client.post(f"/api/runs/{record.run_id}/auth-sessions/").json()
        auth_session_store.capture_code(registered["state"], "auth-code-xyz")
        response = client.get(
            f"/api/runs/{record.run_id}/auth-sessions/{registered['state']}/",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "captured"
        assert body["code"] == "auth-code-xyz"
        assert "capturedAt" in body

    def test_returns_error_session(self) -> None:
        """After ``capture_error`` the response includes error fields."""
        from conformance.api.auth_session_store import auth_session_store

        client = Client()
        record = run_store.create_run()
        registered = client.post(f"/api/runs/{record.run_id}/auth-sessions/").json()
        auth_session_store.capture_error(
            registered["state"],
            error="access_denied",
            description="User declined consent",
        )
        response = client.get(
            f"/api/runs/{record.run_id}/auth-sessions/{registered['state']}/",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "error"
        assert body["error"] == "access_denied"
        assert body["errorDescription"] == "User declined consent"

    def test_returns_404_for_unknown_run(self) -> None:
        """Unknown run id yields 404."""
        client = Client()
        response = client.get("/api/runs/missing/auth-sessions/any-state/")
        assert response.status_code == 404

    def test_returns_404_for_unknown_state(self) -> None:
        """Unknown state under a known run yields 404."""
        client = Client()
        record = run_store.create_run()
        response = client.get(f"/api/runs/{record.run_id}/auth-sessions/bogus/")
        assert response.status_code == 404

    def test_returns_404_for_cross_run_lookup(self) -> None:
        """A state registered against another run is not visible.

        Exercises the ``(run_id, state)`` scoping guarantee directly:
        the lookup runs against an *existing* but unrelated run record,
        so a 404 here is attributable to run-scoped binding rather than
        a missing run.
        """
        client = Client()
        run_a = run_store.create_run()
        run_store.mark_running(run_a.run_id)
        run_store.mark_completed(run_a.run_id, result={"status": "passed"})
        run_b = run_store.create_run()
        registered = client.post(f"/api/runs/{run_b.run_id}/auth-sessions/").json()

        # Look up ``run_b``'s state under ``run_a``'s still-existing run id.
        response = client.get(
            f"/api/runs/{run_a.run_id}/auth-sessions/{registered['state']}/",
        )
        assert response.status_code == 404

    def test_non_loopback_request_is_rejected_with_403(self) -> None:
        """The loopback guard applies to the get endpoint."""
        client = Client(REMOTE_ADDR="10.0.0.5")
        response = client.get("/api/runs/some-id/auth-sessions/some-state/")
        assert response.status_code == 403


# ─── Run lifecycle ↔ auth-session-store integration ─────────────────────────


@pytest.mark.integration
class TestExecuteRunDiscardsAuthSessions:
    """The run lifecycle must drop auth sessions on terminal exit.

    Awaiting auth sessions registered against a run MUST NOT outlive that
    run. The hook lives in ``_execute_run``'s ``finally`` block so it
    covers both the happy path (``mark_completed``) and the failure path
    (``mark_failed``). These tests exercise the hook directly rather than
    relying on the full HTTP request lifecycle.
    """

    def test_completed_run_discards_awaiting_auth_sessions(self) -> None:
        """Sessions are discarded after a successful run completes."""
        from datetime import UTC, datetime
        from pathlib import Path

        from conformance.api.auth_session_store import auth_session_store
        from conformance.api.run_lifecycle import _execute_run
        from conformance.model_bank_config import ModelBankConfig
        from conformance.results import SmokeCheckResult

        record = run_store.create_run()
        auth_session_store.register(record.run_id)
        auth_session_store.register(record.run_id)
        assert len(auth_session_store.for_run(record.run_id)) == 2

        config = ModelBankConfig(
            environment="test-env",
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=Path("results.json"),
        )
        fake_result = SmokeCheckResult(
            environment="test-env",
            status="passed",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            steps=(),
        )
        with patch(
            "conformance.api.run_lifecycle.run_model_bank_smoke_check",
            return_value=fake_result,
        ):
            _execute_run(record.run_id, config, manifest=None, plan=None)

        assert auth_session_store.for_run(record.run_id) == []
        # The run itself transitioned to completed (sanity check).
        assert run_store.get_run(record.run_id) is not None
        assert run_store.get_run(record.run_id).status == "completed"  # type: ignore[union-attr]

    def test_failed_run_also_discards_awaiting_auth_sessions(self) -> None:
        """Sessions are discarded even when the run raises internally."""
        from pathlib import Path

        from conformance.api.auth_session_store import auth_session_store
        from conformance.api.run_lifecycle import _execute_run
        from conformance.model_bank_config import ModelBankConfig

        record = run_store.create_run()
        auth_session_store.register(record.run_id)
        assert len(auth_session_store.for_run(record.run_id)) == 1

        config = ModelBankConfig(
            environment="test-env",
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=Path("results.json"),
        )
        with patch(
            "conformance.api.run_lifecycle.run_model_bank_smoke_check",
            side_effect=RuntimeError("boom"),
        ):
            _execute_run(record.run_id, config, manifest=None, plan=None)

        assert auth_session_store.for_run(record.run_id) == []
        assert run_store.get_run(record.run_id).status == "failed"  # type: ignore[union-attr]

    def test_suite_resolution_failure_surfaces_catalog_error(self) -> None:
        """Catalog failures produce actionable run errors and still clean sessions."""
        from pathlib import Path

        from conformance.api.auth_session_store import auth_session_store
        from conformance.api.run_lifecycle import _execute_run
        from conformance.model_bank_config import ModelBankConfig, SuiteSelection
        from conformance.suite_catalog import SuiteCatalogError

        record = run_store.create_run()
        auth_session_store.register(record.run_id)
        config = ModelBankConfig(
            environment="test-env",
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=Path("results.json"),
            test_suite=SuiteSelection(
                standard="ob-read-write",
                spec_version="v4.0",
                profile="fapi1-advanced",
                suite="discovery-jwks",
            ),
        )

        with patch(
            "conformance.api.run_lifecycle.resolve_suite",
            side_effect=SuiteCatalogError("missing resource"),
        ):
            _execute_run(record.run_id, config, manifest=None, plan=None)

        updated = run_store.get_run(record.run_id)
        assert updated is not None
        assert updated.status == "failed"
        assert updated.error == "Suite resolution failed: missing resource"
        assert auth_session_store.for_run(record.run_id) == []

    def test_other_runs_auth_sessions_are_not_discarded(self) -> None:
        """The hook is run-scoped: sibling runs' sessions are untouched."""
        from datetime import UTC, datetime
        from pathlib import Path

        from conformance.api.auth_session_store import auth_session_store
        from conformance.api.run_lifecycle import _execute_run
        from conformance.model_bank_config import ModelBankConfig
        from conformance.results import SmokeCheckResult

        finishing = run_store.create_run()
        other_run_id = "other-run-id"
        auth_session_store.register(finishing.run_id)
        auth_session_store.register(other_run_id)

        config = ModelBankConfig(
            environment="test-env",
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=Path("results.json"),
        )
        fake_result = SmokeCheckResult(
            environment="test-env",
            status="passed",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            steps=(),
        )
        with patch(
            "conformance.api.run_lifecycle.run_model_bank_smoke_check",
            return_value=fake_result,
        ):
            _execute_run(finishing.run_id, config, manifest=None, plan=None)

        assert auth_session_store.for_run(finishing.run_id) == []
        assert len(auth_session_store.for_run(other_run_id)) == 1

    def test_browser_psu_prompt_flag_wraps_execution_logger(self) -> None:
        """Browser-launched runs mirror raw PSU URLs into transient run state."""
        from datetime import UTC, datetime
        from pathlib import Path

        from conformance.api.run_lifecycle import BrowserParticipantActionLogger, _execute_run
        from conformance.execution_log import ExecutionLogger
        from conformance.model_bank_config import ModelBankConfig
        from conformance.results import SmokeCheckResult

        record = run_store.create_run()
        raw_url = "https://auth.example.com/authorize?client_id=client-123&request=raw-jws-value&state=browser-state"
        config = ModelBankConfig(
            environment="test-env",
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=Path("results.json"),
        )
        fake_result = SmokeCheckResult(
            environment="test-env",
            status="passed",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            steps=(),
        )

        def emit_browser_action(
            config: ModelBankConfig,
            *,
            execution_logger: ExecutionLogger,
        ) -> SmokeCheckResult:
            """Assert the lifecycle provided the browser logger and emit a PSU URL.

            Args:
                config: Runtime model-bank configuration passed to the smoke check.
                execution_logger: Logger supplied by the lifecycle.

            Returns:
                The fake successful smoke-check result.
            """
            assert config.environment == "test-env"
            assert isinstance(execution_logger, BrowserParticipantActionLogger)
            execution_logger.emit("psu-authorization-url", step_id="psu", payload={"url": raw_url})
            return fake_result

        with patch(
            "conformance.api.run_lifecycle.run_model_bank_smoke_check",
            side_effect=emit_browser_action,
        ):
            _execute_run(record.run_id, config, manifest=None, plan=None, browser_psu_prompts=True)

        updated = run_store.get_run(record.run_id)
        assert updated is not None
        assert updated.status == "completed"
        log_bytes = run_store.get_run_log_bytes(record.run_id)
        assert log_bytes is not None
        assert raw_url.encode("utf-8") not in log_bytes

    def test_manifest_run_passes_runtime_config_to_executor(self) -> None:
        """Manifest runs receive safe config placeholder values from the lifecycle."""
        from datetime import UTC, datetime
        from pathlib import Path

        import httpx

        from conformance.api.run_lifecycle import _execute_run
        from conformance.approved_releases import ApprovedReleasePolicy
        from conformance.manifest import parse_manifest
        from conformance.model_bank_config import ModelBankConfig
        from conformance.results import SmokeCheckResult

        record = run_store.create_run()
        approved_release_policy = ApprovedReleasePolicy(
            schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
            approved_tool_versions=("1.2.3",),
        )
        config = ModelBankConfig(
            environment="test-env",
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=Path("results.json"),
            approved_release_policy=approved_release_policy,
        )
        manifest = parse_manifest(
            {
                "schemaVersion": "v1",
                "name": "runtime config",
                "steps": [
                    {
                        "id": "config-driven",
                        "name": "Config-driven request",
                        "request": {"method": "GET", "url": "${config.discoveryUrl}"},
                        "assertions": [{"type": "http_status", "expected": 200}],
                    }
                ],
            }
        )
        fake_result = SmokeCheckResult(
            environment="test-env",
            status="passed",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            steps=(),
        )
        with (
            httpx.Client() as fake_client,
            patch("conformance.api.run_lifecycle.build_json_http_client", return_value=fake_client),
            patch("conformance.api.run_lifecycle.run_manifest", return_value=fake_result) as mock_run_manifest,
        ):
            _execute_run(record.run_id, config, manifest=manifest, plan=None)

        assert mock_run_manifest.call_args is not None
        runtime_config = mock_run_manifest.call_args.kwargs["runtime_config"]
        assert runtime_config.discovery_url == "https://example.com/.well-known/openid-configuration"
        assert runtime_config.environment == "test-env"
        assert mock_run_manifest.call_args.kwargs["approved_release_policy"] is approved_release_policy
