"""Unit tests for the structured execution log module."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from conformance.execution_log import (
    BufferedExecutionLogger,
    ExecutionEvent,
    NullExecutionLogger,
    is_developer_mode_enabled,
    new_run_id,
    warn_if_developer_mode,
)


@pytest.mark.unit
def test_new_run_id_returns_unique_hex_uuid() -> None:
    a = new_run_id()
    b = new_run_id()
    assert a != b
    assert len(a) == 32
    int(a, 16)  # parseable as hex


@pytest.mark.unit
def test_null_logger_emit_is_a_no_op() -> None:
    logger = NullExecutionLogger()
    logger.emit("run-started")
    logger.emit("step-completed", step_id="x", payload={"status": "passed"})


@pytest.mark.unit
def test_buffered_logger_records_event_in_emission_order() -> None:
    logger = BufferedExecutionLogger(run_id="r1", developer_mode=False)

    logger.emit("run-started", payload={"environment": "test"})
    logger.emit("step-started", step_id="discovery", payload={"url": "https://x"})
    logger.emit("step-completed", step_id="discovery", payload={"status": "passed"})

    events = logger.events()
    assert [event.type for event in events] == ["run-started", "step-started", "step-completed"]
    assert events[0].run_id == "r1"
    assert events[0].step_id is None
    assert events[1].step_id == "discovery"


@pytest.mark.unit
def test_buffered_logger_masks_sensitive_json_keys_by_default() -> None:
    logger = BufferedExecutionLogger(run_id="r", developer_mode=False)
    logger.emit(
        "request-sent",
        step_id="token",
        payload={
            "body": {
                "client_secret": "super-secret",  # pragma: allowlist secret
                "grant_type": "client_credentials",
            }
        },
    )
    payload = logger.events()[0].payload
    body = payload["body"]
    assert isinstance(body, dict)
    assert body["client_secret"] == "***"  # noqa: S105 — masked sentinel, not a real secret
    assert body["grant_type"] == "client_credentials"


@pytest.mark.unit
def test_buffered_logger_masks_sensitive_headers_by_default() -> None:
    logger = BufferedExecutionLogger(run_id="r", developer_mode=False)
    logger.emit(
        "request-sent",
        step_id="discovery",
        payload={"headers": {"Authorization": "Bearer secret", "X-Request-Id": "abc"}},
    )
    payload = logger.events()[0].payload
    headers = payload["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "***"
    assert headers["X-Request-Id"] == "abc"


@pytest.mark.unit
def test_buffered_logger_developer_mode_skips_masking() -> None:
    logger = BufferedExecutionLogger(run_id="r", developer_mode=True)
    logger.emit(
        "request-sent",
        payload={
            "headers": {"Authorization": "Bearer secret"},
            "body": {"client_secret": "super-secret"},  # pragma: allowlist secret
        },
    )
    payload = logger.events()[0].payload
    headers = payload["headers"]
    body = payload["body"]
    assert isinstance(headers, dict)
    assert isinstance(body, dict)
    assert headers["Authorization"] == "Bearer secret"  # noqa: S105 — developer mode bypasses masking
    assert body["client_secret"] == "super-secret"  # noqa: S105 — developer mode bypasses masking  # pragma: allowlist secret


@pytest.mark.unit
def test_event_to_json_object_includes_optional_fields_only_when_present() -> None:
    event = ExecutionEvent(
        timestamp="2026-06-01T00:00:00.000000Z",
        run_id="r",
        type="run-started",
        step_id=None,
        payload={},
    )
    obj = event.to_json_object()
    assert obj == {"timestamp": "2026-06-01T00:00:00.000000Z", "runId": "r", "type": "run-started"}


@pytest.mark.unit
def test_event_to_json_object_includes_step_id_and_payload_when_present() -> None:
    event = ExecutionEvent(
        timestamp="2026-06-01T00:00:00.000000Z",
        run_id="r",
        type="step-completed",
        step_id="discovery",
        payload={"status": "passed"},
    )
    obj = event.to_json_object()
    assert obj["stepId"] == "discovery"
    assert obj["payload"] == {"status": "passed"}


@pytest.mark.unit
def test_flush_to_path_writes_ndjson_one_object_per_line(tmp_path: Path) -> None:
    logger = BufferedExecutionLogger(run_id="r", developer_mode=False)
    logger.emit("run-started", payload={"environment": "env"})
    logger.emit("run-completed", payload={"status": "passed"})

    out_path = tmp_path / "nested" / "log.ndjson"
    logger.flush_to_path(out_path)

    raw = out_path.read_text(encoding="utf-8")
    lines = raw.rstrip("\n").split("\n")
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["type"] == "run-started"
    assert parsed[1]["type"] == "run-completed"


@pytest.mark.unit
def test_flush_to_path_is_atomic_via_rename(tmp_path: Path) -> None:
    logger = BufferedExecutionLogger(run_id="r", developer_mode=False)
    logger.emit("run-started")
    out_path = tmp_path / "log.ndjson"
    logger.flush_to_path(out_path)

    # No temp files left behind in the destination directory.
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


@pytest.mark.unit
def test_to_ndjson_bytes_returns_utf8_with_trailing_newline() -> None:
    logger = BufferedExecutionLogger(run_id="r", developer_mode=False)
    logger.emit("run-started")
    data = logger.to_ndjson_bytes()
    assert data.endswith(b"\n")
    decoded = data.decode("utf-8")
    obj = json.loads(decoded.strip())
    assert obj["type"] == "run-started"


@pytest.mark.unit
def test_to_ndjson_bytes_empty_buffer_returns_empty_bytes() -> None:
    logger = BufferedExecutionLogger(run_id="r", developer_mode=False)
    assert logger.to_ndjson_bytes() == b""


@pytest.mark.unit
def test_timestamp_is_rfc3339_with_z_suffix() -> None:
    logger = BufferedExecutionLogger(run_id="r", developer_mode=False)
    logger.emit("run-started")
    timestamp = logger.events()[0].timestamp
    assert timestamp.endswith("Z")
    assert "T" in timestamp


@pytest.mark.unit
def test_is_developer_mode_enabled_true_only_for_literal_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFORMANCE_DEVELOPER_MODE", "true")
    assert is_developer_mode_enabled() is True
    monkeypatch.setenv("CONFORMANCE_DEVELOPER_MODE", "TRUE")
    assert is_developer_mode_enabled() is True
    monkeypatch.setenv("CONFORMANCE_DEVELOPER_MODE", "1")
    assert is_developer_mode_enabled() is False
    monkeypatch.setenv("CONFORMANCE_DEVELOPER_MODE", "yes")
    assert is_developer_mode_enabled() is False
    monkeypatch.delenv("CONFORMANCE_DEVELOPER_MODE", raising=False)
    assert is_developer_mode_enabled() is False


@pytest.mark.unit
def test_warn_if_developer_mode_logs_warning_when_enabled(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("CONFORMANCE_DEVELOPER_MODE", "true")
    with caplog.at_level("WARNING", logger="conformance.execution_log"):
        warn_if_developer_mode()
    assert any("CONFORMANCE_DEVELOPER_MODE" in record.message for record in caplog.records)


@pytest.mark.unit
def test_warn_if_developer_mode_silent_when_disabled(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("CONFORMANCE_DEVELOPER_MODE", raising=False)
    with caplog.at_level("WARNING", logger="conformance.execution_log"):
        warn_if_developer_mode()
    assert caplog.records == []


@pytest.mark.unit
def test_buffered_logger_developer_mode_defaults_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFORMANCE_DEVELOPER_MODE", "true")
    logger = BufferedExecutionLogger(run_id="r")
    assert logger.developer_mode is True
    monkeypatch.delenv("CONFORMANCE_DEVELOPER_MODE", raising=False)
    other = BufferedExecutionLogger(run_id="r")
    assert other.developer_mode is False


@pytest.mark.unit
def test_concurrent_emit_and_snapshot() -> None:
    """Regression: BufferedExecutionLogger must be thread-safe.

    8 emitter threads each emit 100 events while a reader thread takes 50
    ``to_ndjson_bytes()`` snapshots.  Every snapshot must parse as valid
    NDJSON and, after all threads join, the final snapshot must contain
    exactly 800 events.
    """
    n_emitters = 8
    events_per_emitter = 100
    n_snapshots = 50

    execution_logger = BufferedExecutionLogger(run_id="concurrent-test", developer_mode=False)
    errors: list[Exception] = []

    def emitter() -> None:
        for _ in range(events_per_emitter):
            execution_logger.emit("step-completed", payload={"status": "passed"})

    def reader() -> None:
        for _ in range(n_snapshots):
            data = execution_logger.to_ndjson_bytes()
            if not data:
                continue
            try:
                for line in data.decode("utf-8").rstrip("\n").split("\n"):
                    json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                errors.append(exc)

    emitter_threads = [threading.Thread(target=emitter) for _ in range(n_emitters)]
    reader_thread = threading.Thread(target=reader)

    reader_thread.start()
    for t in emitter_threads:
        t.start()
    for t in emitter_threads:
        t.join()
    reader_thread.join()

    assert errors == [], f"Snapshot parse errors: {errors}"
    final_events = execution_logger.events()
    assert len(final_events) == n_emitters * events_per_emitter
