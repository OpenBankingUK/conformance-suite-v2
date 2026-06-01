"""Structured execution log emitted alongside the JSON result file.

Implements PRD participant story #25 and OBL engineer story #7 — an
OWASP A09 ("Security Logging & Monitoring Failures") compliant record of
*everything the engine did* during a run, so failures can be diagnosed
without re-running and so OBL can audit a submission.

Format: newline-delimited JSON (NDJSON). One JSON object per line. Chosen
over a single JSON array so the file is streamable, tail-able, and
partial-read safe — a truncated file is still parseable up to the last
complete line.

Masking: every event flows through :mod:`conformance.masking` before being
buffered. Callers do not need to pre-mask. Set the
``CONFORMANCE_DEVELOPER_MODE`` environment variable to ``true`` to disable
masking for local debugging only — this is never to be enabled in release
builds (enforced operationally, not in code, since the env-var check is the
release artefact's only protection).

Event taxonomy (``type`` field):
    run-started, run-completed,
    step-started, step-completed,
    request-sent, response-received,
    assertion-evaluated,
    placeholder-error, application-error.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from conformance.json_types import JsonObject, JsonValue
from conformance.masking import mask_headers, mask_json_value

logger = logging.getLogger(__name__)

EventType = Literal[
    "run-started",
    "run-completed",
    "step-started",
    "step-completed",
    "request-sent",
    "response-received",
    "assertion-evaluated",
    "placeholder-error",
    "application-error",
]
"""Closed set of event types emitted by the engine.

Closed by design: arbitrary string types would let callers silently drift
the schema. New event types must be added here and to the masking dispatch
in :func:`BufferedExecutionLogger._mask_payload`.
"""

_DEVELOPER_MODE_ENV_VAR = "CONFORMANCE_DEVELOPER_MODE"
"""Environment variable that disables masking when set to ``true``.

Reserved for local engineering debugging. Never enable in release builds.
"""


def is_developer_mode_enabled() -> bool:
    """Return whether unmasked logging is enabled via the environment.

    Returns:
        True when ``CONFORMANCE_DEVELOPER_MODE`` is set to ``"true"``
        (case-insensitive). False for any other value or when unset.
    """
    return os.environ.get(_DEVELOPER_MODE_ENV_VAR, "").strip().lower() == "true"


def new_run_id() -> str:
    """Generate a fresh run identifier.

    A shared helper so the CLI and API agree on the run-id shape (UUID4 hex)
    without either having to depend on the other.

    Returns:
        A 32-character hex UUID4 string.
    """
    return uuid.uuid4().hex


@dataclass(frozen=True)
class ExecutionEvent:
    """A single structured event in the execution log.

    Attributes:
        timestamp: RFC 3339 UTC timestamp (``Z`` suffix, microsecond precision).
        run_id: Identifier of the run that produced this event.
        type: Event type from the closed :data:`EventType` taxonomy.
        step_id: Optional identifier of the manifest step the event belongs to.
        payload: Event-specific structured data. Sensitive values are masked
            at emit time unless developer mode is enabled.
    """

    timestamp: str
    run_id: str
    type: EventType
    step_id: str | None
    payload: JsonObject

    def to_json_object(self) -> JsonObject:
        """Convert the event into its NDJSON line shape.

        Returns:
            JSON object with stable key order suitable for one line of NDJSON.
        """
        obj: JsonObject = {
            "timestamp": self.timestamp,
            "runId": self.run_id,
            "type": self.type,
        }
        if self.step_id is not None:
            obj["stepId"] = self.step_id
        if self.payload:
            obj["payload"] = self.payload
        return obj


class ExecutionLogger:
    """Abstract interface for execution-log sinks.

    Two concrete implementations exist: :class:`NullExecutionLogger` (drops
    every event) and :class:`BufferedExecutionLogger` (in-memory buffer with
    an optional atomic NDJSON write).
    """

    def emit(
        self,
        event_type: EventType,
        *,
        step_id: str | None = None,
        payload: Mapping[str, JsonValue] | None = None,
    ) -> None:
        """Record an event.

        Args:
            event_type: Event type from the closed taxonomy.
            step_id: Optional manifest step identifier.
            payload: Optional event-specific data. Sensitive values are masked
                by the implementation; callers must not pre-mask.

        Raises:
            NotImplementedError: Always — subclasses must implement.
        """
        raise NotImplementedError


class NullExecutionLogger(ExecutionLogger):
    """Drop-everything logger used when no execution log is wanted.

    Default for callers that opt out, so executor/runner call sites can emit
    unconditionally without ``if logger is not None`` guards.
    """

    def emit(
        self,
        event_type: EventType,
        *,
        step_id: str | None = None,
        payload: Mapping[str, JsonValue] | None = None,
    ) -> None:
        """Discard the event.

        Args:
            event_type: Ignored.
            step_id: Ignored.
            payload: Ignored.
        """
        return


class BufferedExecutionLogger(ExecutionLogger):
    """In-memory execution-log buffer with an atomic NDJSON write.

    Events are accumulated in order via :meth:`emit` and can be serialised to
    a single NDJSON file via :meth:`flush_to_path` or to a list of JSON
    objects via :meth:`events`. The buffer is unbounded — callers are
    responsible for bounding run length (the PRD's per-run model).

    Thread-safety: :meth:`emit`, :meth:`events`, :meth:`to_ndjson_bytes`, and
    :meth:`flush_to_path` are safe to call concurrently from multiple threads.
    Events are appended atomically under a per-instance lock; snapshot reads
    are consistent copies that release the lock before serialisation or I/O.
    """

    def __init__(self, *, run_id: str, developer_mode: bool | None = None) -> None:
        """Initialise an empty buffer.

        Args:
            run_id: Identifier embedded in every event emitted by this logger.
            developer_mode: When True, payload values are written unmasked.
                Defaults to consulting :func:`is_developer_mode_enabled` so
                callers do not have to thread the flag through.
        """
        self._run_id = run_id
        self._developer_mode = is_developer_mode_enabled() if developer_mode is None else developer_mode
        self._events: list[ExecutionEvent] = []
        self._lock = threading.Lock()

    @property
    def run_id(self) -> str:
        """The run identifier embedded in every emitted event."""
        return self._run_id

    @property
    def developer_mode(self) -> bool:
        """Whether masking is bypassed for this logger instance."""
        return self._developer_mode

    def events(self) -> list[ExecutionEvent]:
        """Return a snapshot copy of the buffered events.

        Returns:
            New list of events in emission order. Mutating the returned list
            does not affect the buffer. Safe to call concurrently.
        """
        with self._lock:
            return list(self._events)

    def emit(
        self,
        event_type: EventType,
        *,
        step_id: str | None = None,
        payload: Mapping[str, JsonValue] | None = None,
    ) -> None:
        """Append an event to the in-memory buffer with masking applied.

        Args:
            event_type: Event type from the closed taxonomy.
            step_id: Optional manifest step identifier.
            payload: Optional event-specific data. Sensitive fields and
                headers are masked unless developer mode is on.
        """
        masked_payload = self._mask_payload(event_type, payload or {})
        event = ExecutionEvent(
            timestamp=_now_rfc3339(),
            run_id=self._run_id,
            type=event_type,
            step_id=step_id,
            payload=masked_payload,
        )
        with self._lock:
            self._events.append(event)

    def flush_to_path(self, path: Path) -> None:
        """Atomically write the buffered events to ``path`` as NDJSON.

        Writes to a sibling temporary file then ``rename()``s into place so a
        concurrent reader never sees a partial file. Creates parent
        directories as needed.

        Args:
            path: Destination NDJSON file path. Existing contents are
                overwritten.

        Raises:
            OSError: If the file or parent directory cannot be written.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        # NamedTemporaryFile + os.replace gives an atomic publish on POSIX
        # and Windows. delete=False because we hand off to os.replace.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            for event in self.events():
                tmp.write(json.dumps(event.to_json_object(), sort_keys=True))
                tmp.write("\n")
        tmp_path.replace(path)

    def to_ndjson_bytes(self) -> bytes:
        """Serialise the buffered events to NDJSON bytes.

        Used by the REST API endpoint that streams the log without ever
        touching disk.

        Returns:
            UTF-8 encoded NDJSON, one event per line, trailing newline.
        """
        lines = [json.dumps(event.to_json_object(), sort_keys=True) for event in self.events()]
        return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")

    def _mask_payload(self, event_type: EventType, payload: Mapping[str, JsonValue]) -> JsonObject:
        """Apply per-event-type masking to a raw payload.

        Args:
            event_type: Event type whose masking rules to apply.
            payload: Raw payload supplied by the caller. May contain sensitive
                credentials, tokens, or header values.

        Returns:
            New JSON object with sensitive values replaced by
            :data:`conformance.masking.MASKED_VALUE`, or a deep-copied
            unmasked payload when developer mode is enabled.
        """
        if self._developer_mode:
            # Deep-copy via mask_json_value's traversal would still mask
            # nested credential keys; in developer mode we want the raw
            # payload, but we still copy to detach the caller's references.
            return cast("JsonObject", json.loads(json.dumps(payload)))

        masked: JsonObject = {}
        for key, value in payload.items():
            if key == "headers" and isinstance(value, dict):
                # ``headers`` is mapped str→str by upstream callers; the
                # cast is safe because mask_headers only reads .items().
                str_headers = {str(name): str(header_value) for name, header_value in value.items()}
                masked[key] = dict(mask_headers(str_headers))
            else:
                masked[key] = mask_json_value(value)
        return masked


def warn_if_developer_mode() -> None:
    """Log a prominent WARN line when developer mode is active.

    Called once at process startup by the CLI. Safe to call repeatedly — the
    underlying logger handles deduplication via its handlers.
    """
    if is_developer_mode_enabled():
        logger.warning(
            "CONFORMANCE_DEVELOPER_MODE=true — execution log will contain UNMASKED credentials. "
            "Never enable this in release builds.",
        )


def _now_rfc3339() -> str:
    """Return the current UTC time as RFC 3339 with a ``Z`` suffix.

    Returns:
        ISO 8601 / RFC 3339 timestamp string, e.g. ``2026-06-01T12:34:56.789012Z``.
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
