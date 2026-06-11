"""In-memory run store for Phase 1 single-container deployment.

Phase 1 supports one run at a time. This module holds the run state in
memory — no database persistence is required. When the container restarts,
all run state is lost (fire-and-forget per the PRD).
"""

from __future__ import annotations

import dataclasses
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from conformance.execution_log import BufferedExecutionLogger
from conformance.json_types import JsonObject

RunStatus = Literal["pending", "running", "completed", "failed"]
"""Lifecycle states for a conformance run."""

ParticipantActionType = Literal["psu-authorization-url"]
"""Browser participant action kinds supported by the run store."""

ParticipantActionStatus = Literal["pending", "completed"]
"""Lifecycle states for browser participant actions."""

_TERMINAL_STATUSES: tuple[RunStatus, ...] = ("completed", "failed")
"""Lifecycle states beyond which a run will not transition again."""

MAX_TERMINAL_RECORDS = 10
"""Cap on completed/failed records retained in memory.

Phase 1 fire-and-forget deployments typically run one container per run,
but long-lived dev containers or repeated invocations against a persistent
process would otherwise grow ``RunStore._runs`` without bound. When a new
run is created, the oldest terminal records beyond this cap are dropped.
Active (pending/running) records are never pruned.
"""


@dataclass
class ParticipantAction:
    """In-memory action required from the browser participant.

    Attributes:
        type: Discriminator for the pending participant action shape.
        step_id: Manifest step that produced the participant action.
        url: Raw PSU authorisation URL to render while awaiting consent.
        status: Current action lifecycle state.
        created_at: UTC timestamp when the action became pending.
        expires_at: Optional UTC deadline when waiting should time out.
        completed_at: UTC timestamp when the action completed, or None.
    """

    type: ParticipantActionType
    step_id: str
    url: str
    status: ParticipantActionStatus
    created_at: datetime
    expires_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True)
class RunPlanStep:
    """Immutable snapshot of one selected plan step at run launch.

    Attributes:
        step_id: Manifest step identifier.
        name: Human-readable step name.
        kind: Manifest step kind.
        group: Optional logical grouping label for the step.
        phase: Optional high-level phase label for the step.
        mandatory: Whether the step is mandatory in the manifest.
        optional: Whether the step is optional in the manifest.
        order: Launch-time selected ordering index.
    """

    step_id: str
    name: str
    kind: str
    group: str | None
    phase: str | None
    mandatory: bool
    optional: bool
    order: int


@dataclass
class RunRecord:
    """Mutable state for a single conformance run.

    Attributes:
        run_id: Unique identifier for this run.
        status: Current lifecycle state.
        created_at: UTC timestamp when the run was queued.
        started_at: UTC timestamp when execution began, or None.
        finished_at: UTC timestamp when execution ended, or None.
        result: Structured JSON result object, populated on completion.
        error: Human-readable error message if the run failed internally.
        version: Monotonic change counter used by long-poll waiters.
        participant_actions: In-memory browser actions keyed by ``step_id``.
            Values include pending/completed state and are deliberately
            omitted from status JSON, results, and logs because they can
            contain raw PSU authorisation request objects.
        participant_action: Backward-compatible alias for the oldest pending
            participant action, or ``None`` when no action is pending.
        execution_logger: Per-run structured execution log buffer. The
            engine appends events here during the run; the API exposes
            the buffer's bytes via the run-log endpoint. ``None`` only
            for legacy fixtures that don't exercise the engine path.
        planned_steps: Launch-time immutable snapshot of selected plan
            steps used by UI progress rendering. Empty for runs started
            without a manifest plan snapshot.
    """

    run_id: str
    status: RunStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: JsonObject | None = None
    error: str | None = None
    version: int = 0
    participant_actions: dict[str, ParticipantAction] = field(default_factory=dict)
    participant_action: ParticipantAction | None = None
    execution_logger: BufferedExecutionLogger | None = None
    planned_steps: tuple[RunPlanStep, ...] = ()

    def to_status_json(self) -> JsonObject:
        """Serialise the run record into the public status JSON shape.

        Returns:
            JSON object suitable for the GET /api/runs/<id>/ response.
        """
        obj: JsonObject = {
            "id": self.run_id,
            "status": self.status,
            "createdAt": self.created_at.isoformat(),
        }
        if self.started_at is not None:
            obj["startedAt"] = self.started_at.isoformat()
        if self.finished_at is not None:
            obj["finishedAt"] = self.finished_at.isoformat()
        if self.error is not None:
            obj["error"] = self.error
        return obj


class RunStore:
    """Thread-safe in-memory store for conformance run records.

    Phase 1 enforces a single concurrent run. Attempting to start a second
    run while one is pending or running is rejected.
    """

    def __init__(self) -> None:
        """Initialise an empty run store with a threading lock."""
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._runs: dict[str, RunRecord] = {}
        self._active_run_id: str | None = None

    def create_run(self, *, planned_steps: tuple[RunPlanStep, ...] = ()) -> RunRecord:
        """Reserve a new run slot if no run is currently active.

        Args:
            planned_steps: Optional immutable launch snapshot of selected
                manifest plan steps.

        Returns:
            The newly created run record in ``pending`` state.

        Raises:
            RunConflictError: If a run is already pending or running.
        """
        with self._lock:
            if self._active_run_id is not None:
                raise RunConflictError(self._active_run_id)
            run_id = uuid.uuid4().hex
            record = RunRecord(
                run_id=run_id,
                status="pending",
                created_at=datetime.now(UTC),
                execution_logger=BufferedExecutionLogger(run_id=run_id),
                planned_steps=tuple(planned_steps),
            )
            self._runs[run_id] = record
            self._active_run_id = run_id
            self._prune_terminal_records_locked()
            return record

    def get_run(self, run_id: str) -> RunRecord | None:
        """Look up a run record by ID.

        Args:
            run_id: The unique run identifier.

        Returns:
            The run record, or None if not found.
        """
        with self._lock:
            record = self._runs.get(run_id)
            return self._snapshot_record_locked(record) if record is not None else None

    def get_participant_action(self, run_id: str) -> ParticipantAction | None:
        """Snapshot the oldest pending browser participant action for a run.

        Args:
            run_id: The unique run identifier.

        Returns:
            A detached participant action snapshot, or ``None`` if the run is
            unknown or no action is currently pending.
        """
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return None
            return self._snapshot_participant_action(self._first_pending_action_locked(record))

    def get_participant_actions(self, run_id: str) -> list[ParticipantAction]:
        """Snapshot all browser participant actions for a run.

        Args:
            run_id: The unique run identifier.

        Returns:
            Detached participant actions sorted by creation time then step ID,
            or an empty list when the run is unknown or has no actions.
        """
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return []
            snapshot_actions = self._snapshot_participant_actions(record.participant_actions)
            return sorted(
                snapshot_actions.values(),
                key=lambda item: (item.created_at, item.step_id),
            )

    def wait_for_run_change(
        self,
        run_id: str,
        *,
        timeout_seconds: float,
        since_version: int | None = None,
    ) -> RunRecord | None:
        """Block until a run changes or the timeout expires.

        Args:
            run_id: The unique run identifier.
            timeout_seconds: Maximum time to wait for a state change.
            since_version: Optional run version already rendered by the
                caller. When the live run is newer than this value, the
                current snapshot is returned immediately.

        Returns:
            A detached run snapshot after the next change, or ``None`` when
            the run is unknown or the timeout expires without a change.
        """
        with self._condition:
            record = self._runs.get(run_id)
            if record is None:
                return None
            if record.status in _TERMINAL_STATUSES:
                return self._snapshot_record_locked(record)

            initial_version = record.version
            if since_version is not None and since_version < record.version:
                return self._snapshot_record_locked(record)
            if since_version is not None and since_version <= record.version:
                initial_version = since_version
            if timeout_seconds <= 0:
                return None
            if not self._condition.wait_for(
                lambda: self._run_changed_since_locked(run_id, initial_version),
                timeout=timeout_seconds,
            ):
                return None

            updated_record = self._runs.get(run_id)
            return self._snapshot_record_locked(updated_record) if updated_record is not None else None

    def set_participant_action(
        self,
        run_id: str,
        *,
        step_id: str,
        url: str,
        expires_at: datetime | None = None,
    ) -> None:
        """Store a pending raw PSU authorisation URL for browser launch.

        The action is intentionally process-local only. It is exposed through
        run snapshots for the browser UI, but is not serialised into status
        JSON, result JSON, or execution logs.

        Args:
            run_id: The unique run identifier.
            step_id: Manifest step that emitted the PSU authorisation URL.
            url: Raw PSU authorisation URL to render for the participant.
            expires_at: Optional UTC deadline when the participant action is
                expected to resolve through timeout.
        """
        with self._lock:
            record = self._runs[run_id]
            record.participant_actions[step_id] = ParticipantAction(
                type="psu-authorization-url",
                step_id=step_id,
                url=url,
                status="pending",
                created_at=datetime.now(UTC),
                expires_at=expires_at,
            )
            self._sync_legacy_participant_action_locked(record)
            self._notify_run_changed_locked(record)

    def clear_participant_action(self, run_id: str, *, step_id: str | None = None) -> None:
        """Clear or complete browser participant actions.

        When ``step_id`` is provided, the matching action is marked
        ``completed`` so the browser UI can still render completion state.
        Run terminal cleanup passes no step ID so all action state is
        discarded.

        Args:
            run_id: The unique run identifier.
            step_id: Optional manifest step that must match the active action.
        """
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return
            if step_id is None:
                record.participant_actions.clear()
                record.participant_action = None
                self._notify_run_changed_locked(record)
                return
            action = record.participant_actions.get(step_id)
            if action is None:
                return
            action.status = "completed"
            action.completed_at = datetime.now(UTC)
            self._sync_legacy_participant_action_locked(record)
            self._notify_run_changed_locked(record)

    def get_run_log_bytes(self, run_id: str) -> bytes | None:
        """Snapshot the run's execution log as NDJSON bytes.

        Safe to call on in-progress runs; the returned snapshot reflects
        all events buffered up to this call.

        Args:
            run_id: The unique run identifier.

        Returns:
            The NDJSON-encoded log bytes, or ``None`` if the run ID is
            unknown or the record has no attached logger.
        """
        with self._lock:
            record = self._runs.get(run_id)
            if record is None or record.execution_logger is None:
                return None
            execution_logger = record.execution_logger
        return execution_logger.to_ndjson_bytes()

    def mark_running(self, run_id: str) -> None:
        """Transition a pending run to running state.

        Args:
            run_id: The unique run identifier.
        """
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return
            record.status = "running"
            record.started_at = datetime.now(UTC)
            self._notify_run_changed_locked(record)

    def mark_completed(self, run_id: str, *, result: JsonObject) -> None:
        """Transition a running run to completed state with its result.

        Clears ``_active_run_id`` so a subsequent ``create_run`` is allowed
        — the data structure's invariant is that ``_active_run_id`` names the
        currently active (pending/running) run, or is None.

        Args:
            run_id: The unique run identifier.
            result: The structured JSON result object from the engine.
        """
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return
            record.status = "completed"
            record.finished_at = datetime.now(UTC)
            record.result = result
            record.participant_actions.clear()
            record.participant_action = None
            if self._active_run_id == run_id:
                self._active_run_id = None
            self._notify_run_changed_locked(record)

    def mark_failed(self, run_id: str, *, error: str) -> None:
        """Transition a run to failed state with an error message.

        Clears ``_active_run_id`` so a subsequent ``create_run`` is allowed.

        Args:
            run_id: The unique run identifier.
            error: Human-readable error description.
        """
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return
            record.status = "failed"
            record.finished_at = datetime.now(UTC)
            record.error = error
            record.participant_actions.clear()
            record.participant_action = None
            if self._active_run_id == run_id:
                self._active_run_id = None
            self._notify_run_changed_locked(record)

    def reset(self) -> None:
        """Wipe all run state. Intended for test fixtures only.

        Production code must not call this — it discards in-flight runs
        without coordinating with their background threads.
        """
        with self._lock:
            self._runs.clear()
            self._active_run_id = None

    def _prune_terminal_records_locked(self) -> None:
        """Drop oldest terminal records beyond ``MAX_TERMINAL_RECORDS``.

        Bounds memory growth in long-lived processes that handle many
        runs over time. The caller MUST hold ``self._lock``. Only
        terminal (``completed`` / ``failed``) records are eligible for
        eviction; the currently active run (``pending`` / ``running``)
        is never dropped. Records without a ``finished_at`` are treated
        as the oldest — they should not exist in a terminal state, but
        evicting them first keeps the invariant safe.
        """
        terminal_ids = [run_id for run_id, record in self._runs.items() if record.status in _TERMINAL_STATUSES]
        if len(terminal_ids) <= MAX_TERMINAL_RECORDS:
            return
        # Sort oldest-first; missing finished_at sorts before any real timestamp.
        terminal_ids.sort(
            key=lambda run_id: self._runs[run_id].finished_at or datetime.min.replace(tzinfo=UTC),
        )
        evict_count = len(terminal_ids) - MAX_TERMINAL_RECORDS
        for run_id in terminal_ids[:evict_count]:
            del self._runs[run_id]

    def _snapshot_record_locked(self, record: RunRecord) -> RunRecord:
        """Create a detached run record snapshot.

        Args:
            record: Live run record stored under ``self._lock``.

        Returns:
            A shallow copy of the record with detached participant action
            snapshots and an immutable planned-step tuple.
        """
        snapshot_actions = self._snapshot_participant_actions(record.participant_actions)
        legacy_action = self._snapshot_participant_action(self._first_pending_action_locked(record))
        return dataclasses.replace(
            record,
            participant_actions=snapshot_actions,
            participant_action=legacy_action,
            planned_steps=tuple(record.planned_steps),
        )

    def _first_pending_action_locked(self, record: RunRecord) -> ParticipantAction | None:
        """Return the oldest pending action for backward-compatible callers.

        Args:
            record: Live run record stored under ``self._lock``.

        Returns:
            The oldest pending participant action, or ``None`` when none are
            pending.
        """
        pending_actions = [action for action in record.participant_actions.values() if action.status == "pending"]
        if not pending_actions:
            return None
        return min(pending_actions, key=lambda action: (action.created_at, action.step_id))

    def _sync_legacy_participant_action_locked(self, record: RunRecord) -> None:
        """Synchronise the legacy ``participant_action`` compatibility alias.

        Args:
            record: Live run record stored under ``self._lock``.
        """
        record.participant_action = self._first_pending_action_locked(record)

    def _notify_run_changed_locked(self, record: RunRecord) -> None:
        """Bump the run version and wake any waiters.

        Args:
            record: Live run record stored under ``self._lock``.
        """
        record.version += 1
        self._condition.notify_all()

    def _run_changed_since_locked(self, run_id: str, initial_version: int) -> bool:
        """Return whether a run has changed since the captured version.

        Args:
            run_id: The unique run identifier.
            initial_version: The version captured before waiting began.

        Returns:
            ``True`` when the run record no longer matches the captured
            version, or when the run has been removed.
        """
        record = self._runs.get(run_id)
        return record is None or record.version != initial_version

    def _snapshot_participant_actions(
        self,
        actions: dict[str, ParticipantAction],
    ) -> dict[str, ParticipantAction]:
        """Create detached snapshots for all participant actions.

        Args:
            actions: Live participant actions keyed by step id.

        Returns:
            Detached participant action snapshots keyed by step id.
        """
        return {step_id: dataclasses.replace(action) for step_id, action in actions.items()}

    def _snapshot_participant_action(self, action: ParticipantAction | None) -> ParticipantAction | None:
        """Create a detached participant action snapshot.

        Args:
            action: Live participant action stored on a run record.

        Returns:
            A copied participant action, or ``None`` when no action is pending.
        """
        return dataclasses.replace(action) if action is not None else None


class RunConflictError(Exception):
    """Raised when a new run is requested while one is already active.

    Attributes:
        active_run_id: The ID of the currently active run blocking the request.
    """

    active_run_id: str

    def __init__(self, active_run_id: str) -> None:
        """Initialise with the blocking run's ID.

        Args:
            active_run_id: The ID of the run that is currently active.
        """
        super().__init__(f"Run {active_run_id} is already active")
        self.active_run_id = active_run_id


# Module-level singleton for the Phase 1 single-process deployment.
run_store = RunStore()
"""Global run store instance shared across the Django process."""
