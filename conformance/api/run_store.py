"""In-memory run store for Phase 1 single-container deployment.

Phase 1 supports one run at a time. This module holds the run state in
memory — no database persistence is required. When the container restarts,
all run state is lost (fire-and-forget per the PRD).
"""

from __future__ import annotations

import dataclasses
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from conformance.execution_log import BufferedExecutionLogger
from conformance.json_types import JsonObject

RunStatus = Literal["pending", "running", "completed", "failed"]
"""Lifecycle states for a conformance run."""

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
        execution_logger: Per-run structured execution log buffer. The
            engine appends events here during the run; the API exposes
            the buffer's bytes via the run-log endpoint. ``None`` only
            for legacy fixtures that don't exercise the engine path.
    """

    run_id: str
    status: RunStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: JsonObject | None = None
    error: str | None = None
    execution_logger: BufferedExecutionLogger | None = None

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
        self._runs: dict[str, RunRecord] = {}
        self._active_run_id: str | None = None

    def create_run(self) -> RunRecord:
        """Reserve a new run slot if no run is currently active.

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
            return dataclasses.replace(record) if record is not None else None

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
            record = self._runs[run_id]
            record.status = "running"
            record.started_at = datetime.now(UTC)

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
            record = self._runs[run_id]
            record.status = "completed"
            record.finished_at = datetime.now(UTC)
            record.result = result
            if self._active_run_id == run_id:
                self._active_run_id = None

    def mark_failed(self, run_id: str, *, error: str) -> None:
        """Transition a run to failed state with an error message.

        Clears ``_active_run_id`` so a subsequent ``create_run`` is allowed.

        Args:
            run_id: The unique run identifier.
            error: Human-readable error description.
        """
        with self._lock:
            record = self._runs[run_id]
            record.status = "failed"
            record.finished_at = datetime.now(UTC)
            record.error = error
            if self._active_run_id == run_id:
                self._active_run_id = None

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
