import dataclasses
import json
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest
from django.test import Client

from conformance.api.auth_session_store import auth_session_store
from conformance.api.run_lifecycle import BrowserParticipantActionLogger, _attach_plan_evidence
from conformance.api.run_store import MAX_TERMINAL_RECORDS, RunConflictError, RunPlanStep, RunStore, run_store
from conformance.approved_releases import APPROVED_RELEASE_POLICY_SCHEMA_VERSION
from conformance.catalogue import (
    CatalogueAssertion,
    CatalogueKey,
    CatalogueRequestStep,
    CatalogueTestCase,
    EndpointRef,
    ImplementedEndpoint,
    RuntimeInputRequirement,
    SecurityProfileApplicability,
    TestCaseApplicability,
    TestCatalogue,
    TestPlanSpec,
    compile_test_plan,
)
from conformance.catalogues.ais import AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE, AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE_KEY
from conformance.execution_log import BufferedExecutionLogger
from conformance.json_types import JsonObject
from conformance.results import mark_development_result_evidence

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

    def test_mark_running_ignores_unknown_run_id(self) -> None:
        """Missing runs are ignored to keep lifecycle transitions idempotent."""
        store = RunStore()

        store.mark_running("missing-run-id")

        assert store.get_run("missing-run-id") is None

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

    def test_mark_completed_ignores_unknown_run_id(self) -> None:
        """Terminal completion on a missing run is a no-op."""
        store = RunStore()

        store.mark_completed("missing-run-id", result={"status": "passed"})

        assert store.get_run("missing-run-id") is None

    def test_mark_failed_stores_error(self) -> None:
        store = RunStore()
        record = store.create_run()
        store.mark_running(record.run_id)
        store.mark_failed(record.run_id, error="timeout")
        updated = store.get_run(record.run_id)
        assert updated is not None
        assert updated.status == "failed"
        assert updated.error == "timeout"

    def test_mark_failed_ignores_unknown_run_id(self) -> None:
        """Terminal failure on a missing run is a no-op."""
        store = RunStore()

        store.mark_failed("missing-run-id", error="boom")

        assert store.get_run("missing-run-id") is None

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

    def test_create_run_persists_planned_steps_snapshot(self) -> None:
        store = RunStore()
        record = store.create_run(
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
                    step_id="token-exchange",
                    name="Token exchange",
                    kind="http",
                    group="execution",
                    phase="execution",
                    mandatory=False,
                    optional=True,
                    order=1,
                ),
            )
        )

        snapshot = store.get_run(record.run_id)
        assert snapshot is not None
        assert [step.step_id for step in snapshot.planned_steps] == ["discovery", "token-exchange"]
        assert [step.order for step in snapshot.planned_steps] == [0, 1]

    def test_planned_steps_snapshot_is_immutable_and_detached(self) -> None:
        store = RunStore()
        record = store.create_run(
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

        snapshot = store.get_run(record.run_id)
        assert snapshot is not None
        with pytest.raises(dataclasses.FrozenInstanceError):
            snapshot.planned_steps[0].name = "Mutated"  # type: ignore[misc]  # intentional: asserts frozen dataclass raises

        assert record.run_id in store._runs
        store._runs[record.run_id].planned_steps = ()
        assert [step.step_id for step in snapshot.planned_steps] == ["discovery"]

    def test_to_status_json_does_not_expose_planned_steps(self) -> None:
        store = RunStore()
        record = store.create_run(
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

        status_json = record.to_status_json()

        assert "plannedSteps" not in status_json
        assert "planned_steps" not in status_json
        assert "discovery" not in json.dumps(status_json)

    def test_plan_snapshot_and_validation_are_attached_to_result_evidence(self) -> None:
        """Launch-time plan evidence is copied into completed result JSON."""
        store = RunStore()
        record = store.create_run(
            plan_snapshot={"schemaVersion": "1.0", "businessTestData": {}},
            validation_result={"schemaVersion": "1.0", "executionMode": "development", "valid": True, "issues": []},
        )
        result: JsonObject = {
            "metadata": {"reportVersion": "test"},
            "certificationEligibility": {"eligible": True},
        }

        _attach_plan_evidence(result, record)

        assert result["testPlanSnapshot"] == {"schemaVersion": "1.0", "businessTestData": {}}
        test_plan_validation = result["testPlanValidation"]
        assert isinstance(test_plan_validation, dict)
        assert test_plan_validation["valid"] is True
        metadata = result["metadata"]
        assert isinstance(metadata, dict)
        assert metadata["executionMode"] == "development"
        certification_eligibility = result["certificationEligibility"]
        assert isinstance(certification_eligibility, dict)
        assert certification_eligibility["eligible"] is False
        assert certification_eligibility["reason"] == "Development-mode run is not certification evidence"
        reasons = certification_eligibility["reasons"]
        assert isinstance(reasons, list)
        assert "Development-mode run is not certification evidence" in reasons

    def test_mark_development_result_evidence_updates_certification_block(self) -> None:
        """Shared helper marks development-mode results as non-certifying."""
        result: JsonObject = {"metadata": {}, "certificationEligibility": {"eligible": True}}

        mark_development_result_evidence(
            {"schemaVersion": "1.0", "executionMode": "development", "valid": True, "issues": []},
            result,
        )

        eligibility = result["certificationEligibility"]
        assert isinstance(eligibility, dict)
        assert eligibility["eligible"] is False
        assert eligibility["reason"] == "Development-mode run is not certification evidence"
        assert result["metadata"] == {"executionMode": "development"}

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
        assert "psu" in snapshot.participant_actions
        assert snapshot.participant_actions["psu"].status == "pending"
        assert snapshot.participant_action is not None
        assert snapshot.participant_action.url == self.RAW_PSU_AUTHORIZATION_URL

    def test_participant_actions_support_multiple_pending_entries(self) -> None:
        """Runs can hold multiple pending browser actions at the same time."""
        store = RunStore()
        record = store.create_run()
        first_url = "https://auth.example.com/authorize?state=first"
        second_url = "https://auth.example.com/authorize?state=second"

        store.set_participant_action(record.run_id, step_id="psu-first", url=first_url)
        store.set_participant_action(record.run_id, step_id="psu-second", url=second_url)

        actions = store.get_participant_actions(record.run_id)
        assert len(actions) == 2
        assert {action.step_id for action in actions} == {"psu-first", "psu-second"}
        assert {action.status for action in actions} == {"pending"}

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
        """Matching step-completion hooks mark action state as completed."""
        store = RunStore()
        record = store.create_run()
        first_url = "https://auth.example.com/authorize?state=first"
        second_url = "https://auth.example.com/authorize?state=second"
        store.set_participant_action(record.run_id, step_id="psu-first", url=first_url)
        store.set_participant_action(record.run_id, step_id="psu-second", url=second_url)

        store.clear_participant_action(record.run_id, step_id="token")
        assert store.get_participant_action(record.run_id) is not None

        store.clear_participant_action(record.run_id, step_id="psu-first")
        actions = {action.step_id: action for action in store.get_participant_actions(record.run_id)}
        assert actions["psu-first"].status == "completed"
        assert actions["psu-second"].status == "pending"
        assert store.get_participant_action(record.run_id) is not None
        snapshot = store.get_run(record.run_id)
        assert snapshot is not None
        assert snapshot.participant_actions["psu-first"].status == "completed"

        store.clear_participant_action(record.run_id, step_id="psu-second")
        assert store.get_participant_action(record.run_id) is None

    def test_clear_participant_action_without_step_id_clears_active_action(self) -> None:
        """Run-level cleanup hooks can clear the active browser action."""
        store = RunStore()
        record = store.create_run()
        store.set_participant_action(record.run_id, step_id="psu", url=self.RAW_PSU_AUTHORIZATION_URL)

        store.clear_participant_action(record.run_id)

        assert store.get_participant_action(record.run_id) is None
        assert store.get_participant_actions(record.run_id) == []

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
        """Only the matching step completion marks the action completed."""
        store = RunStore()
        record = store.create_run()
        wrapped = BufferedExecutionLogger(run_id=record.run_id, developer_mode=False)
        logger = BrowserParticipantActionLogger(wrapped, run_id=record.run_id, store=store)
        logger.emit("psu-authorization-url", step_id="psu", payload={"url": self.RAW_PSU_AUTHORIZATION_URL})

        logger.emit("step-completed", step_id="token", payload={"status": "passed"})
        assert store.get_participant_action(record.run_id) is not None

        logger.emit("step-completed", step_id="psu", payload={"status": "passed"})
        assert store.get_participant_action(record.run_id) is None
        action = store.get_participant_actions(record.run_id)[0]
        assert action.status == "completed"

    def test_callback_received_clears_action(self) -> None:
        """Callback capture clears the active browser action."""
        store = RunStore()
        record = store.create_run()
        wrapped = BufferedExecutionLogger(run_id=record.run_id, developer_mode=False)
        logger = BrowserParticipantActionLogger(wrapped, run_id=record.run_id, store=store)
        logger.emit("psu-authorization-url", step_id="psu", payload={"url": self.RAW_PSU_AUTHORIZATION_URL})

        logger.emit("auth-callback-received", payload={"state": "state", "code": "auth-code"})

        assert store.get_participant_action(record.run_id) is None
        assert store.get_participant_actions(record.run_id) == []

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

VALID_TEST_PLAN = {
    "schemaVersion": "1.0",
    "specification": {"family": "OBL_READ_WRITE", "version": "4.0.1", "profile": "FAPI1_ADVANCED"},
    "securityEnvironment": {
        "discoveryUrl": "https://example.com/.well-known/openid-configuration",
        "resourceBaseUrl": "https://resource.example.com",
    },
    "resourceGroups": [
        {
            "id": "AIS",
            "endpoints": [{"method": "GET", "path": "/open-banking/v4.0/aisp/accounts"}],
        }
    ],
    "businessTestData": {},
    "metadata": {},
}


def _file_reference_catalogue() -> TestCatalogue:
    """Build a minimal catalogue with a selected file-reference runtime input.

    Returns:
        Catalogue fixture whose selected case would read a local file if the API
        accepted the submitted ``file_reference`` input.
    """
    endpoint = EndpointRef(method="POST", path="/open-banking/v4.0/files/request")
    return TestCatalogue(
        key=CatalogueKey(standard="open-banking", version="v4.0", api="files"),
        catalogue_version="test.1",
        test_cases=(
            CatalogueTestCase(
                test_case_id="file-request",
                name="File request",
                role="resource",
                compliance_scope=("legacy-fcs-script:test#file",),
                applicability=TestCaseApplicability(
                    security_profiles=SecurityProfileApplicability(profiles=("all",)),
                    endpoint_refs=(endpoint,),
                ),
                mandatory=True,
                runtime_input_requirements=(
                    RuntimeInputRequirement("resourceBaseUrl", "url", "Resource base URL"),
                    RuntimeInputRequirement("requestBodyRef", "file_reference", "Request body file"),
                ),
                request_steps=(
                    CatalogueRequestStep(
                        step_id="file-request",
                        name="POST file request",
                        method="POST",
                        path="/open-banking/v4.0/files/request",
                        runtime_input_refs=("resourceBaseUrl", "requestBodyRef"),
                    ),
                ),
                assertions=(CatalogueAssertion("status-201", "http_status", "HTTP 201", {"expected": 201}),),
            ),
        ),
    )


def _file_reference_plan_spec_json() -> dict[str, object]:
    """Return API plan-spec JSON that selects a file-reference runtime input.

    Returns:
        v1 plan-spec JSON targeting :func:`_file_reference_catalogue`.
    """
    return {
        "schemaVersion": "v1",
        "catalogue": {"standard": "open-banking", "version": "v4.0", "api": "files"},
        "securityProfile": "fapi1-advanced",
        "implementedEndpoints": [
            {
                "method": "POST",
                "path": "/open-banking/v4.0/files/request",
                "resourceGroup": "Files",
            }
        ],
        "runtimeInputs": {
            "resourceBaseUrl": "https://resource.example.com",
            "requestBodyRef": "local-request.json",
        },
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
def _reset_global_stores() -> Iterator[None]:
    """Reset process-local singleton stores around each test.

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

    def test_rejects_missing_test_plan(self) -> None:
        client = Client()
        response = client.post("/api/runs/", data=json.dumps({}), content_type="application/json")
        assert response.status_code == 400
        assert "schemaVersion 1.0 test plan" in response.json()["error"]

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

    @patch("conformance.api.run_lifecycle._execute_run")
    def test_start_run_derives_default_plan_and_persists_selected_steps(self, mock_execute: Mock) -> None:
        """Lifecycle start derives default plans and snapshots selected steps."""
        from conformance.api.run_lifecycle import start_run
        from conformance.manifest import parse_manifest
        from conformance.model_bank_config import ModelBankConfig

        config = ModelBankConfig(
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=Path("results.json"),
        )
        manifest = parse_manifest(
            {
                "schemaVersion": "v1",
                "name": "plan snapshot",
                "steps": [
                    {
                        "id": "mandatory-http",
                        "name": "Mandatory HTTP",
                        "mandatory": True,
                        "request": {"method": "GET", "url": "https://example.com/mandatory"},
                        "assertions": [{"type": "http_status", "expected": 200}],
                    },
                    {
                        "id": "optional-http",
                        "name": "Optional HTTP",
                        "optional": True,
                        "request": {"method": "GET", "url": "https://example.com/optional"},
                        "assertions": [{"type": "http_status", "expected": 200}],
                    },
                    {
                        "kind": "psu-authorization",
                        "id": "psu-step",
                        "name": "PSU authorization",
                        "mode": "manual",
                        "authorizationEndpoint": "https://auth.example.com/authorize",
                        "clientId": "client-123",
                        "redirectUri": "https://conformance.example.com/callback",
                    },
                ],
            }
        )

        response = start_run(config=config, manifest=manifest, plan=None)
        run_id = response["id"]
        assert isinstance(run_id, str)
        record = run_store.get_run(run_id)

        assert record is not None
        assert [step.step_id for step in record.planned_steps] == ["mandatory-http", "psu-step"]
        assert [step.order for step in record.planned_steps] == [0, 1]
        assert [step.kind for step in record.planned_steps] == ["http", "psu-authorization"]

        _wait_for_value(
            lambda: True if mock_execute.call_args is not None else None,
            timeout_seconds=1.0,
        )
        assert mock_execute.call_args is not None
        threaded_plan = mock_execute.call_args.args[6]
        assert threaded_plan is not None
        assert threaded_plan.selected_step_ids() == ["mandatory-http", "psu-step"]

    @patch("conformance.api.run_lifecycle._execute_run")
    def test_start_run_persists_selected_only_when_plan_deselects_steps(self, mock_execute: Mock) -> None:
        """Run snapshots include selected steps only, excluding deselections."""
        from conformance.api.run_lifecycle import start_run
        from conformance.manifest import parse_manifest
        from conformance.model_bank_config import ModelBankConfig
        from conformance.test_plan import TestPlan

        config = ModelBankConfig(
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=Path("results.json"),
        )
        manifest = parse_manifest(
            {
                "schemaVersion": "v1",
                "name": "selected only",
                "steps": [
                    {
                        "id": "keep-me",
                        "name": "Keep me",
                        "request": {"method": "GET", "url": "https://example.com/keep"},
                        "assertions": [{"type": "http_status", "expected": 200}],
                    },
                    {
                        "id": "drop-me",
                        "name": "Drop me",
                        "request": {"method": "GET", "url": "https://example.com/drop"},
                        "assertions": [{"type": "http_status", "expected": 200}],
                    },
                ],
            }
        )
        plan = TestPlan.default_plan_from_manifest(manifest).with_deselection(["drop-me"])

        response = start_run(config=config, manifest=manifest, plan=plan)
        run_id = response["id"]
        assert isinstance(run_id, str)
        record = run_store.get_run(run_id)

        assert record is not None
        assert [step.step_id for step in record.planned_steps] == ["keep-me"]

        _wait_for_value(
            lambda: True if mock_execute.call_args is not None else None,
            timeout_seconds=1.0,
        )
        assert mock_execute.call_args is not None
        threaded_plan = mock_execute.call_args.args[6]
        assert threaded_plan is not None
        assert threaded_plan.selected_step_ids() == ["keep-me"]

    @patch("conformance.api.run_lifecycle._execute_run")
    def test_start_run_marks_non_mandatory_compiled_steps_optional(
        self,
        mock_execute: Mock,
        tmp_path: Path,
    ) -> None:
        """Compiled-plan snapshots mark non-mandatory catalogue cases as optional."""
        from conformance.api.run_lifecycle import start_run
        from conformance.model_bank_config import ModelBankConfig

        catalogue = TestCatalogue(
            key=CatalogueKey(standard="open-banking", version="v4.0", api="ais"),
            catalogue_version="test.1",
            test_cases=(
                CatalogueTestCase(
                    test_case_id="mandatory-case",
                    name="Mandatory case",
                    role="resource",
                    compliance_scope=("legacy-fcs-script:test#mandatory",),
                    applicability=TestCaseApplicability(
                        security_profiles=SecurityProfileApplicability(profiles=("all",)),
                    ),
                    mandatory=True,
                    request_steps=(
                        CatalogueRequestStep(
                            step_id="mandatory-step",
                            name="Mandatory step",
                            method="GET",
                            path="/open-banking/v4.0/aisp/accounts",
                        ),
                    ),
                    assertions=(CatalogueAssertion("status-200", "http_status", "HTTP 200", {"expected": 200}),),
                ),
                CatalogueTestCase(
                    test_case_id="optional-case",
                    name="Optional case",
                    role="resource",
                    compliance_scope=("legacy-fcs-script:test#optional",),
                    applicability=TestCaseApplicability(
                        security_profiles=SecurityProfileApplicability(profiles=("all",)),
                    ),
                    mandatory=False,
                    request_steps=(
                        CatalogueRequestStep(
                            step_id="optional-step",
                            name="Optional step",
                            method="GET",
                            path="/open-banking/v4.0/aisp/accounts",
                        ),
                    ),
                    assertions=(CatalogueAssertion("status-200", "http_status", "HTTP 200", {"expected": 200}),),
                ),
            ),
        )
        compiled_plan = compile_test_plan(
            catalogue,
            TestPlanSpec(
                schema_version="v1",
                catalogue_key=catalogue.key,
                security_profile="fapi1-advanced",
                implemented_endpoints=(),
                runtime_inputs={},
            ),
        )
        config = ModelBankConfig(
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=tmp_path / "results.json",
        )

        response = start_run(
            config=config,
            compiled_plan=compiled_plan,
            runtime_inputs={},
            runtime_input_base_dir=tmp_path,
        )
        run_id = response["id"]
        assert isinstance(run_id, str)
        record = run_store.get_run(run_id)

        assert record is not None
        assert [(step.step_id, step.mandatory, step.optional) for step in record.planned_steps] == [
            ("mandatory-step", True, False),
            ("optional-step", False, True),
        ]
        _wait_for_value(
            lambda: True if mock_execute.call_args is not None else None,
            timeout_seconds=1.0,
        )

    @patch("conformance.api.run_lifecycle._execute_run")
    def test_start_run_snapshots_expanded_ais_permission_setup_steps(
        self,
        mock_execute: Mock,
        tmp_path: Path,
    ) -> None:
        """AIS run snapshots use executable basic/detail setup steps."""
        from conformance.api.run_lifecycle import start_run
        from conformance.model_bank_config import ModelBankConfig

        compiled_plan = compile_test_plan(
            AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE,
            TestPlanSpec(
                schema_version="v1",
                catalogue_key=AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE_KEY,
                security_profile="fapi1-advanced",
                implemented_endpoints=(
                    ImplementedEndpoint(
                        method="GET",
                        path="/open-banking/v4.0/aisp/accounts/{AccountId}",
                        resource_group="Accounts",
                    ),
                    ImplementedEndpoint(
                        method="GET",
                        path="/open-banking/v4.0/aisp/accounts/{AccountId}/balances",
                        resource_group="Balances",
                    ),
                ),
                runtime_inputs={
                    "resourceBaseUrl": "https://resource.example.com",
                    "consentedAccountId": "account-123",
                },
            ),
        )
        config = ModelBankConfig(
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=tmp_path / "results.json",
        )

        response = start_run(
            config=config,
            compiled_plan=compiled_plan,
            runtime_inputs={},
            runtime_input_base_dir=tmp_path,
        )
        run_id = response["id"]
        assert isinstance(run_id, str)
        record = run_store.get_run(run_id)

        assert record is not None
        planned_step_ids = [step.step_id for step in record.planned_steps]
        assert "ais-at-setup-consent-request" not in planned_step_ids
        assert "ais-at-setup-token-request" not in planned_step_ids
        assert planned_step_ids[:8] == [
            "setup-token-ais-client-credentials",
            "ais-at-setup-discovery-request",
            "ais-at-setup-basic-consent-request",
            "setup-ais-basic-consent-authorisation",
            "ais-at-setup-detail-consent-request",
            "setup-ais-detail-consent-authorisation",
            "ais-at-setup-basic-token-request",
            "ais-at-setup-detail-token-request",
        ]
        assert [
            step.kind
            for step in record.planned_steps
            if step.step_id
            in {
                "ais-at-setup-basic-consent-request",
                "setup-ais-basic-consent-authorisation",
                "ais-at-setup-detail-consent-request",
                "setup-ais-detail-consent-authorisation",
                "ais-at-setup-basic-token-request",
                "ais-at-setup-detail-token-request",
            }
        ] == ["http", "psu-authorization", "http", "psu-authorization", "http", "http"]
        _wait_for_value(
            lambda: True if mock_execute.call_args is not None else None,
            timeout_seconds=1.0,
        )

    @patch("conformance.api.run_lifecycle._execute_run")
    def test_creates_run_and_returns_201(self, mock_execute: Mock) -> None:
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
        _wait_for_value(
            lambda: True if mock_execute.call_args is not None else None,
            timeout_seconds=1.0,
        )
        assert mock_execute.call_args is not None
        assert mock_execute.call_args.args[0] == data["id"]
        assert mock_execute.call_args.args[2] is not None
        assert "accessToken" not in mock_execute.call_args.args[3]
        assert mock_execute.call_args.args[5:] == (None, None)
        assert mock_execute.call_args.kwargs == {"browser_psu_prompts": False}

    @patch("conformance.api.run_lifecycle._execute_run")
    def test_creates_run_from_canonical_json_test_plan(self, mock_execute: Mock) -> None:
        """REST API accepts the PRD schemaVersion 1.0 test-plan body directly."""
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

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "pending"
        record = run_store.get_run(data["id"])
        assert record is not None
        assert record.plan_snapshot is not None
        assert record.plan_snapshot["schemaVersion"] == "1.0"
        business_data = record.plan_snapshot["businessTestData"]
        assert isinstance(business_data, dict)
        assert "inputs" not in business_data
        assert "secret-access-token" not in json.dumps(record.plan_snapshot)
        assert record.validation_result is not None
        assert record.validation_result["valid"] is True
        _wait_for_value(
            lambda: True if mock_execute.call_args is not None else None,
            timeout_seconds=1.0,
        )

    @patch("conformance.api.run_lifecycle._execute_run")
    def test_rejects_canonical_plan_spec_with_separate_config(self, mock_execute: Mock) -> None:
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
        mock_execute.assert_not_called()

    @patch("conformance.api.run_lifecycle._execute_run")
    def test_creates_run_from_nested_canonical_test_plan(self, mock_execute: Mock) -> None:
        """REST API accepts canonical test plans under the testPlan key."""
        client = Client()
        body = {"testPlan": VALID_TEST_PLAN}

        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")

        assert response.status_code == 201
        assert mock_execute.call_args is not None

    def test_rejects_removed_deselect_field(self) -> None:
        """``deselectStepIds`` is no longer a public API field."""
        client = Client()
        body = {"config": VALID_CONFIG, "deselectStepIds": ["a"]}
        response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert response.status_code == 400
        assert "Legacy run request field(s) are no longer supported" in response.json()["error"]

    @patch("conformance.api.run_lifecycle._execute_run")
    def test_rejects_second_concurrent_run(self, mock_execute: object) -> None:
        client = Client()
        body = VALID_TEST_PLAN
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

    @patch("conformance.api.run_lifecycle._execute_run")
    def test_returns_run_status(self, mock_execute: object) -> None:
        client = Client()
        body = VALID_TEST_PLAN
        create_resp = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        run_id = create_resp.json()["id"]
        response = client.get(f"/api/runs/{run_id}/")
        assert response.status_code == 200
        assert response.json()["id"] == run_id
        assert response.json()["status"] == "pending"

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


@pytest.mark.integration
class TestGetRunResultEndpoint:
    def test_returns_404_for_unknown_id(self) -> None:
        client = Client()
        response = client.get("/api/runs/nonexistent/result/")
        assert response.status_code == 404

    @patch("conformance.api.run_lifecycle._execute_run")
    def test_returns_409_when_run_not_complete(self, mock_execute: object) -> None:
        client = Client()
        body = VALID_TEST_PLAN
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
        body = VALID_TEST_PLAN
        with patch("conformance.api.run_lifecycle._execute_run"):
            response = client.post("/api/runs/", data=json.dumps(body), content_type="application/json")
        assert response.status_code == 201

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


# ─── Run-log endpoint ─────────────────────────────────────────────────────


@pytest.mark.integration
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

    def test_appends_local_timestamp_display_fields_when_time_zone_is_requested(self) -> None:
        client = Client()
        record = run_store.create_run()

        response = client.post(f"/api/runs/{record.run_id}/auth-sessions/?timeZone=Europe/London")

        assert response.status_code == 201
        body = response.json()
        assert body["displayTimeZone"] == "Europe/London"
        created_at_local = datetime.fromisoformat(body["createdAt"]).astimezone(ZoneInfo("Europe/London"))
        assert body["createdAtLocal"] == created_at_local.isoformat()

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

    def test_appends_local_timestamp_display_fields_when_time_zone_is_requested(self) -> None:
        client = Client()
        record = run_store.create_run()
        registered = client.post(f"/api/runs/{record.run_id}/auth-sessions/").json()
        auth_session_store.capture_code(registered["state"], "auth-code-xyz")

        response = client.get(
            f"/api/runs/{record.run_id}/auth-sessions/{registered['state']}/?timeZone=Europe/London",
        )

        assert response.status_code == 200
        body = response.json()
        assert body["displayTimeZone"] == "Europe/London"
        created_at_local = datetime.fromisoformat(body["createdAt"]).astimezone(ZoneInfo("Europe/London"))
        captured_at_local = datetime.fromisoformat(body["capturedAt"]).astimezone(ZoneInfo("Europe/London"))
        assert body["createdAtLocal"] == created_at_local.isoformat()
        assert body["capturedAtLocal"] == captured_at_local.isoformat()

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
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=Path("results.json"),
        )
        fake_result = SmokeCheckResult(
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

    def test_reset_run_before_terminal_transition_does_not_raise_and_discards_sessions(self) -> None:
        """Lifecycle cleanup tolerates run-store resets that remove the run."""
        from datetime import UTC, datetime
        from pathlib import Path

        from conformance.api.auth_session_store import auth_session_store
        from conformance.api.run_lifecycle import _execute_run
        from conformance.model_bank_config import ModelBankConfig
        from conformance.results import SmokeCheckResult

        record = run_store.create_run()
        auth_session_store.register(record.run_id)
        assert len(auth_session_store.for_run(record.run_id)) == 1

        config = ModelBankConfig(
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=Path("results.json"),
        )
        fake_result = SmokeCheckResult(
            status="passed",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            steps=(),
        )

        def reset_store_then_return_result(*args: object, **kwargs: object) -> SmokeCheckResult:
            """Simulate fixture teardown/reset racing with lifecycle terminalization."""
            run_store.reset()
            return fake_result

        with patch(
            "conformance.api.run_lifecycle.run_model_bank_smoke_check",
            side_effect=reset_store_then_return_result,
        ):
            _execute_run(record.run_id, config, manifest=None, plan=None)

        assert run_store.get_run(record.run_id) is None
        assert auth_session_store.for_run(record.run_id) == []

    def test_completed_run_writes_configured_artifacts(self, tmp_path: Path) -> None:
        """Successful API/browser lifecycle runs persist configured artifacts.

        Args:
            tmp_path: Pytest temporary directory used for result and log files.
        """
        from datetime import UTC, datetime

        from conformance.api.run_lifecycle import _execute_run
        from conformance.model_bank_config import ModelBankConfig
        from conformance.results import SmokeCheckResult

        record = run_store.create_run()
        result_path = tmp_path / "out" / "result.json"
        log_path = tmp_path / "out" / "execution-log.ndjson"
        config = ModelBankConfig(
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=result_path,
            execution_log_path=log_path,
        )
        fake_result = SmokeCheckResult(
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

        updated = run_store.get_run(record.run_id)
        assert updated is not None
        assert updated.status == "completed"
        assert json.loads(result_path.read_text(encoding="utf-8"))["status"] == "passed"
        assert log_path.exists()

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
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=Path("results.json"),
        )
        fake_result = SmokeCheckResult(
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
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=Path("results.json"),
        )
        fake_result = SmokeCheckResult(
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
        assert mock_run_manifest.call_args.kwargs["approved_release_policy"] is approved_release_policy
