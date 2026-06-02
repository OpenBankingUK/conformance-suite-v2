"""Unit tests for the PSU authorization auth-session store.

Covers registration, one-shot code/error capture, lookup scoping by run,
per-run cap, terminal cleanup, and thread-safety under concurrent
register/capture from multiple threads (callback view + run driver).
"""

from __future__ import annotations

import threading

import pytest

from conformance.api.auth_session_store import (
    MAX_SESSIONS_PER_RUN,
    MIN_CALLER_SUPPLIED_STATE_LENGTH,
    AuthSessionAlreadyResolvedError,
    AuthSessionLimitError,
    AuthSessionStore,
    DuplicateAuthSessionError,
    InvalidAuthSessionStateError,
    UnknownAuthSessionError,
)


@pytest.mark.unit
class TestAuthSessionStoreRegister:
    def test_register_generates_opaque_state(self) -> None:
        store = AuthSessionStore()
        session = store.register("run-1")
        assert session.status == "awaiting"
        assert session.run_id == "run-1"
        assert session.created_at is not None
        # token_urlsafe(32) yields ≥43 chars; assert the unguessability bar.
        assert len(session.state) >= MIN_CALLER_SUPPLIED_STATE_LENGTH

    def test_register_generates_unique_states(self) -> None:
        store = AuthSessionStore()
        # Spread across many run_ids to avoid the per-run cap; the property
        # under test is state uniqueness, not per-run capacity.
        states = {store.register(f"run-{i}").state for i in range(50)}
        assert len(states) == 50

    def test_register_accepts_caller_supplied_state(self) -> None:
        store = AuthSessionStore()
        supplied = "x" * MIN_CALLER_SUPPLIED_STATE_LENGTH
        session = store.register("run-1", state=supplied)
        assert session.state == supplied

    def test_register_rejects_short_caller_supplied_state(self) -> None:
        store = AuthSessionStore()
        short = "x" * (MIN_CALLER_SUPPLIED_STATE_LENGTH - 1)
        with pytest.raises(InvalidAuthSessionStateError):
            store.register("run-1", state=short)

    def test_register_rejects_duplicate_state(self) -> None:
        store = AuthSessionStore()
        supplied = "x" * MIN_CALLER_SUPPLIED_STATE_LENGTH
        store.register("run-1", state=supplied)
        with pytest.raises(DuplicateAuthSessionError):
            store.register("run-1", state=supplied)

    def test_register_rejects_duplicate_state_across_runs(self) -> None:
        # State space is global — re-registering across runs is still a collision.
        store = AuthSessionStore()
        supplied = "x" * MIN_CALLER_SUPPLIED_STATE_LENGTH
        store.register("run-1", state=supplied)
        with pytest.raises(DuplicateAuthSessionError):
            store.register("run-2", state=supplied)

    def test_register_enforces_per_run_cap(self) -> None:
        store = AuthSessionStore()
        for _ in range(MAX_SESSIONS_PER_RUN):
            store.register("run-1")
        with pytest.raises(AuthSessionLimitError):
            store.register("run-1")

    def test_per_run_cap_is_scoped_to_run(self) -> None:
        store = AuthSessionStore()
        for _ in range(MAX_SESSIONS_PER_RUN):
            store.register("run-1")
        # Another run is unaffected.
        other = store.register("run-2")
        assert other.run_id == "run-2"


@pytest.mark.unit
class TestAuthSessionStoreCapture:
    def test_capture_code_transitions_to_captured(self) -> None:
        store = AuthSessionStore()
        session = store.register("run-1")
        updated = store.capture_code(session.state, code="auth-code-xyz")
        assert updated.status == "captured"
        assert updated.code == "auth-code-xyz"
        assert updated.captured_at is not None

    def test_capture_error_transitions_to_error(self) -> None:
        store = AuthSessionStore()
        session = store.register("run-1")
        updated = store.capture_error(
            session.state,
            error="access_denied",
            description="user cancelled",
        )
        assert updated.status == "error"
        assert updated.error == "access_denied"
        assert updated.error_description == "user cancelled"
        assert updated.captured_at is not None

    def test_capture_code_rejects_unknown_state(self) -> None:
        store = AuthSessionStore()
        with pytest.raises(UnknownAuthSessionError):
            store.capture_code("nope", code="x")

    def test_capture_error_rejects_unknown_state(self) -> None:
        store = AuthSessionStore()
        with pytest.raises(UnknownAuthSessionError):
            store.capture_error("nope", error="access_denied")

    def test_capture_code_is_one_shot(self) -> None:
        store = AuthSessionStore()
        session = store.register("run-1")
        store.capture_code(session.state, code="first")
        with pytest.raises(AuthSessionAlreadyResolvedError):
            store.capture_code(session.state, code="second")

    def test_capture_error_after_code_rejected(self) -> None:
        store = AuthSessionStore()
        session = store.register("run-1")
        store.capture_code(session.state, code="first")
        with pytest.raises(AuthSessionAlreadyResolvedError):
            store.capture_error(session.state, error="access_denied")

    def test_capture_code_after_error_rejected(self) -> None:
        store = AuthSessionStore()
        session = store.register("run-1")
        store.capture_error(session.state, error="access_denied")
        with pytest.raises(AuthSessionAlreadyResolvedError):
            store.capture_code(session.state, code="late")


@pytest.mark.unit
class TestAuthSessionStoreLookup:
    def test_get_returns_session_for_matching_run(self) -> None:
        store = AuthSessionStore()
        session = store.register("run-1")
        looked_up = store.get("run-1", session.state)
        assert looked_up is not None
        assert looked_up.state == session.state

    def test_get_returns_none_for_mismatched_run(self) -> None:
        # Defence-in-depth: even if a caller guesses a valid state,
        # the run_id must match to retrieve the session.
        store = AuthSessionStore()
        session = store.register("run-1")
        assert store.get("run-2", session.state) is None

    def test_get_returns_none_for_unknown_state(self) -> None:
        store = AuthSessionStore()
        assert store.get("run-1", "no-such-state") is None

    def test_for_run_returns_only_matching_sessions(self) -> None:
        store = AuthSessionStore()
        a1 = store.register("run-1")
        a2 = store.register("run-1")
        store.register("run-2")
        sessions = store.for_run("run-1")
        states = {s.state for s in sessions}
        assert states == {a1.state, a2.state}

    def test_for_run_empty_for_unknown_run(self) -> None:
        store = AuthSessionStore()
        assert store.for_run("nobody") == []

    def test_get_returns_snapshot_not_live_reference(self) -> None:
        store = AuthSessionStore()
        session = store.register("run-1")
        snapshot = store.get("run-1", session.state)
        assert snapshot is not None
        store.capture_code(session.state, code="x")
        # Snapshot captured pre-capture must still read "awaiting".
        assert snapshot.status == "awaiting"
        assert snapshot.code is None


@pytest.mark.unit
class TestAuthSessionStoreLifecycle:
    def test_discard_for_run_drops_all_sessions(self) -> None:
        store = AuthSessionStore()
        store.register("run-1")
        store.register("run-1")
        removed = store.discard_for_run("run-1")
        assert removed == 2
        assert store.for_run("run-1") == []

    def test_discard_for_run_leaves_other_runs_alone(self) -> None:
        store = AuthSessionStore()
        store.register("run-1")
        keep = store.register("run-2")
        store.discard_for_run("run-1")
        assert [s.state for s in store.for_run("run-2")] == [keep.state]

    def test_discard_for_run_returns_zero_for_unknown_run(self) -> None:
        store = AuthSessionStore()
        assert store.discard_for_run("nobody") == 0

    def test_reset_wipes_all_state(self) -> None:
        store = AuthSessionStore()
        store.register("run-1")
        store.register("run-2")
        store.reset()
        assert store.for_run("run-1") == []
        assert store.for_run("run-2") == []


@pytest.mark.unit
class TestAuthSessionStoreConcurrency:
    def test_concurrent_register_yields_unique_states(self) -> None:
        store = AuthSessionStore()
        # Use distinct run_ids so the per-run cap does not interfere.
        results: list[str] = []
        lock = threading.Lock()

        def worker(idx: int) -> None:
            """Register one session and record its state."""
            session = store.register(f"run-{idx}")
            with lock:
                results.append(session.state)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(32)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(set(results)) == 32

    def test_concurrent_capture_only_one_wins(self) -> None:
        store = AuthSessionStore()
        session = store.register("run-1")
        successes: list[str] = []
        failures: list[Exception] = []
        lock = threading.Lock()
        barrier = threading.Barrier(8)

        def worker(idx: int) -> None:
            """Race to capture the same state; only one thread should win."""
            barrier.wait()
            try:
                store.capture_code(session.state, code=f"code-{idx}")
                with lock:
                    successes.append(f"code-{idx}")
            except AuthSessionAlreadyResolvedError as exc:
                with lock:
                    failures.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(successes) == 1
        assert len(failures) == 7
