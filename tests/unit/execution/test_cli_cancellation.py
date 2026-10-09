"""Cancellation boundaries restore handlers and preserve existing evidence."""

import signal
import threading
from pathlib import Path

import pytest

from conformance import cli
from conformance.cli_cancellation import CliCancelled, run_cancellable
from conformance.execution_log import BufferedExecutionLogger

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_boundary_restores_handlers_and_returns_signal_exit_code(
    sig: signal.Signals, capsys: pytest.CaptureFixture[str]
) -> None:
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    unwound: list[bool] = []

    def operation() -> int:
        try:
            raise CliCancelled(sig)
        finally:
            unwound.append(True)

    assert run_cancellable(lambda: run_cancellable(operation)) == 128 + sig
    assert unwound == [True]
    assert {sig: signal.getsignal(sig) for sig in previous} == previous
    captured = capsys.readouterr()
    assert captured.err.count("Cancelled") == 1
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_boundary_restores_handlers_after_non_cancellation_error() -> None:
    previous = signal.getsignal(signal.SIGTERM)

    def operation() -> int:
        raise ValueError("not cancellation")

    with pytest.raises(ValueError, match="not cancellation"):
        run_cancellable(operation)
    assert signal.getsignal(signal.SIGTERM) == previous


def test_keyboard_interrupt_is_reported_without_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    def operation() -> int:
        raise KeyboardInterrupt

    assert run_cancellable(operation) == 130
    assert "Cancelled (SIGINT)" in capsys.readouterr().err


def test_repeated_signals_do_not_interrupt_cleanup() -> None:
    cleaned: list[bool] = []

    def operation() -> int:
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        try:
            handler(signal.SIGTERM, None)
        finally:
            handler(signal.SIGTERM, None)
            cleaned.append(True)
        return 0

    assert run_cancellable(operation) == 143
    assert cleaned == [True]


def test_direct_cli_call_handles_cancellation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def interrupt_read(_self: Path, **_kwargs: object) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(Path, "read_text", interrupt_read)
    assert cli.run(["--test-plan", str(tmp_path / "plan.json")]) == 130
    captured = capsys.readouterr()
    assert captured.err.count("Cancelled") == 1
    assert "Conformance run" not in captured.out
    assert list(tmp_path.iterdir()) == []


def test_worker_thread_does_not_install_signal_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_signal(*_args: object) -> None:
        pytest.fail("A worker thread cannot install signal handlers")

    monkeypatch.setattr(signal, "signal", unexpected_signal)
    results: list[int] = []
    worker = threading.Thread(target=lambda: results.append(run_cancellable(lambda: 7)))
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert results == [7]


def test_result_publish_cancellation_preserves_previous_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "result.json"
    target.write_text("previous complete result", encoding="utf-8")

    def interrupt_replace(_self: Path, _target: Path) -> Path:
        raise CliCancelled(signal.SIGTERM)

    monkeypatch.setattr(Path, "replace", interrupt_replace)
    with pytest.raises(CliCancelled):
        cli._write_result(target, "new complete result")
    assert target.read_text() == "previous complete result"
    assert list(tmp_path.iterdir()) == [target]


def test_log_serialization_cancellation_cleans_staging_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "log.ndjson"
    target.write_text("previous log", encoding="utf-8")
    logger = BufferedExecutionLogger(run_id="r")
    logger.emit("run-started")

    def interrupt_serialization(*_args: object, **_kwargs: object) -> str:
        raise CliCancelled(signal.SIGINT)

    monkeypatch.setattr("conformance.execution_log.json.dumps", interrupt_serialization)
    with pytest.raises(CliCancelled):
        logger.flush_to_path(target)
    assert target.read_text() == "previous log"
    assert list(tmp_path.iterdir()) == [target]
