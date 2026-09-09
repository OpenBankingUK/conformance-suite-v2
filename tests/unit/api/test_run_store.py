"""Unit tests for run-store state and the browser participant-action logger."""

import dataclasses
import json

import pytest

from conformance.api.run_lifecycle import BrowserParticipantActionLogger, _attach_plan_evidence
from conformance.api.run_store import MAX_TERMINAL_RECORDS, RunConflictError, RunPlanStep, RunStore
from conformance.execution_log import BufferedExecutionLogger
from conformance.json_types import JsonObject
from conformance.results import mark_development_result_evidence

pytestmark = pytest.mark.unit


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
            {
                "schemaVersion": "1.0",
                "executionMode": "development",
                "valid": True,
                "issues": [],
            },
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


# ─── Bounded run-store history ──────────────────────────────────────────────


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
