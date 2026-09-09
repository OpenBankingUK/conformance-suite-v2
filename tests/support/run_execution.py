"""A deterministic stand-in for the background run-execution worker."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class RunExecutionLaunch:
    """Arguments captured from one background run-execution launch."""

    args: tuple[object, ...]
    kwargs: Mapping[str, object]


class StubbedRunExecution:
    """Deterministic stand-in for the background run-execution worker.

    ``start_run`` hands execution to a daemon thread it does not retain, so a
    test cannot otherwise join the worker it started. This records the worker
    thread and the arguments it received, lets a test wait for the launch
    instead of polling the run store, and is joined during teardown so no
    worker outlives the test that owns it.
    """

    def __init__(self) -> None:
        """Create a stub that has not yet been launched."""
        self._lock = threading.Lock()
        self._launched = threading.Event()
        self._launches: list[RunExecutionLaunch] = []
        self._workers: list[threading.Thread] = []

    def __call__(self, *args: object, **kwargs: object) -> None:
        """Record one background launch in place of executing the run.

        Args:
            *args: Positional arguments the lifecycle passed to the worker.
            **kwargs: Keyword arguments the lifecycle passed to the worker.
        """
        with self._lock:
            self._launches.append(RunExecutionLaunch(args=args, kwargs=dict(kwargs)))
            self._workers.append(threading.current_thread())
        self._launched.set()

    @property
    def launches(self) -> tuple[RunExecutionLaunch, ...]:
        """Return every recorded launch in order.

        Returns:
            Immutable snapshot of the launches observed so far.
        """
        with self._lock:
            return tuple(self._launches)

    def wait_for_launch(self, *, timeout_seconds: float = 5.0) -> RunExecutionLaunch:
        """Block until the launched worker reaches the stub.

        Args:
            timeout_seconds: Maximum wall-clock time to wait.

        Returns:
            The first recorded launch.

        Raises:
            AssertionError: If no worker reached the stub in time.
        """
        if not self._launched.wait(timeout_seconds):
            raise AssertionError(f"No run execution was launched within {timeout_seconds}s")
        return self.launches[0]

    def assert_not_launched(self) -> None:
        """Assert the request under test never started a background worker.

        Raises:
            AssertionError: If any launch was recorded.
        """
        assert self.launches == (), "Run execution was launched unexpectedly"

    def join(self, *, timeout_seconds: float = 5.0) -> None:
        """Join every worker thread recorded by this stub.

        Args:
            timeout_seconds: Maximum wall-clock time to wait per worker.

        Raises:
            AssertionError: If a worker is still running afterwards.
        """
        current = threading.current_thread()
        with self._lock:
            workers = tuple(self._workers)
        for worker in workers:
            if worker is current:
                continue
            worker.join(timeout=timeout_seconds)
            assert not worker.is_alive(), f"Run execution worker {worker.name!r} outlived its test"
