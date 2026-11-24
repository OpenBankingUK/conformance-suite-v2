"""Process-signal cancellation scoped to CLI calls, not web/API execution."""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable
from contextvars import ContextVar
from types import FrameType

from conformance.cli_console import write_notice


class _Cancellation:
    def __init__(self) -> None:
        self.signum: int | None = None
        self.event = threading.Event()

    def check(self) -> None:
        if self.event.is_set() and self.signum is not None:
            raise CliCancelled(self.signum)


_STATE: ContextVar[_Cancellation | None] = ContextVar("cli_cancellation_state", default=None)


class CliCancelled(BaseException):
    """Unwind CLI resources without being converted into a conformance failure."""

    def __init__(self, signum: int) -> None:
        """Record the signal for its conventional process exit code."""
        super().__init__(signum)
        self.signum = signum


def current_cancellation_check() -> Callable[[], None] | None:
    """Capture this CLI call's cancellation state for execution worker threads."""
    state = _STATE.get()
    return state.check if state is not None else None


def run_cancellable(operation: Callable[[], int]) -> int:
    """Handle SIGINT/SIGTERM once, restore handlers and return 130/143.

    Nested CLI entry points reuse the outer boundary. Non-main-thread calls
    never install process-wide handlers. Repeated signals during cleanup are
    ignored until the original handlers are restored.
    """
    if _STATE.get() is not None:
        return operation()
    state = _Cancellation()
    token = _STATE.set(state)
    previous: dict[signal.Signals, Callable[[int, FrameType | None], object] | int | None] = {}
    cancelling = False

    def cancel(signum: int, _frame: FrameType | None) -> None:
        nonlocal cancelling
        if not cancelling:
            cancelling = True
            state.signum = signum
            state.event.set()
            raise CliCancelled(signum)

    try:
        try:
            if threading.current_thread() is threading.main_thread():
                for sig in (signal.SIGINT, signal.SIGTERM):
                    previous[sig] = signal.getsignal(sig)
                    signal.signal(sig, cancel)
            return operation()
        except CliCancelled as error:
            signum = error.signum
        except KeyboardInterrupt:
            cancelling = True
            signum = int(signal.SIGINT)
            state.signum = signum
            state.event.set()
        name = signal.Signals(signum).name
        write_notice(
            "[CLI]",
            f"Cancelled ({name}). No partial run results were saved; files from completed runs may remain.",
            tone="warning",
        )
        return 128 + signum
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        _STATE.reset(token)
