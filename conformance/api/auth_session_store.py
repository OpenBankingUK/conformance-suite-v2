"""In-memory auth-session store for PSU authorization callback coordination.

Phase 1 supports the manual PSU authorization flow described in the PRD:
the conformance suite registers an expected ``state`` token before redirecting
the participant's browser to the ASPSP, and later correlates the inbound
``/callback/?state=...&code=...`` redirect back to that registration so the
captured authorization code can be retrieved by the run driver.

The store is process-local and ephemeral — sibling to
``conformance.api.run_store.RunStore`` — and intentionally has no persistence:
auth sessions live only for the duration of their parent run. Replay
protection is provided by one-shot capture semantics rather than persistent
nonce tracking.

This module is a Phase 1 skeleton: types and method signatures are defined
with full docstrings; behaviour is implemented in a follow-up TDD step.
"""

from __future__ import annotations

import dataclasses
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

AuthSessionStatus = Literal["awaiting", "captured", "error"]
"""Lifecycle states for a single PSU authorization session.

``awaiting`` is the initial state after :meth:`AuthSessionStore.register`.
``captured`` is the terminal success state after the ASPSP redirect delivers
an authorization code. ``error`` is the terminal failure state when the
ASPSP redirect carries an ``error`` parameter instead.
"""

MAX_SESSIONS_PER_RUN = 8
"""Cap on auth sessions retained per run.

Bounds the in-memory footprint of a long-lived run that exercises many
authorization endpoints (for example a multi-account journey). Registration
beyond this cap is rejected via :class:`AuthSessionLimitError`. The cap is
deliberately small for Phase 1 and is revisitable as real flows land.
"""

MIN_CALLER_SUPPLIED_STATE_LENGTH = 32
"""Minimum length for a caller-supplied ``state`` token.

Callers MAY supply their own state to :meth:`AuthSessionStore.register` —
for example to thread an externally-generated correlator through the flow —
but a shorter value would weaken the unguessability property the callback
endpoint relies on for security. Shorter values are rejected with
:class:`InvalidAuthSessionStateError`.
"""


@dataclass
class AuthSession:
    """Mutable state for a single PSU authorization session.

    Attributes:
        state: Opaque, unguessable token correlating the ASPSP redirect
            back to this registration. Either server-generated (via
            ``secrets.token_urlsafe``) or caller-supplied above the minimum
            entropy bar.
        run_id: Identifier of the parent conformance run that owns this
            session. Used to scope lookups and lifecycle cleanup.
        status: Current lifecycle state.
        created_at: UTC timestamp when the session was registered.
        captured_at: UTC timestamp when the callback arrived, or ``None``
            while ``status`` is ``awaiting``.
        code: Authorization code captured from the ASPSP redirect, present
            only when ``status`` is ``captured``. Treated as sensitive —
            execution-log emission MUST mask this field.
        error: ASPSP-supplied error code (per RFC 6749 §4.1.2.1) when
            ``status`` is ``error``; otherwise ``None``.
        error_description: ASPSP-supplied human-readable error description
            when ``status`` is ``error``; otherwise ``None``.
    """

    state: str
    run_id: str
    status: AuthSessionStatus
    created_at: datetime
    captured_at: datetime | None = None
    code: str | None = None
    error: str | None = None
    error_description: str | None = None


class AuthSessionStore:
    """Thread-safe in-memory store for PSU authorization sessions.

    Mirrors the locking and singleton pattern of
    :class:`conformance.api.run_store.RunStore`. All public methods are
    safe to call concurrently from request-handling threads and the
    callback view.
    """

    def __init__(self) -> None:
        """Initialise an empty auth-session store with a threading lock."""
        self._lock = threading.Lock()
        self._sessions: dict[str, AuthSession] = {}

    def register(self, run_id: str, *, state: str | None = None) -> AuthSession:
        """Register a new auth session for a run.

        Args:
            run_id: Identifier of the parent run that will own the session.
            state: Optional caller-supplied state token. When omitted the
                store generates one via ``secrets.token_urlsafe``. When
                supplied it must be at least
                :data:`MIN_CALLER_SUPPLIED_STATE_LENGTH` characters long.

        Returns:
            The newly created :class:`AuthSession` in ``awaiting`` state.

        Raises:
            InvalidAuthSessionStateError: If a caller-supplied ``state`` is
                shorter than :data:`MIN_CALLER_SUPPLIED_STATE_LENGTH`.
            DuplicateAuthSessionError: If ``state`` is already registered.
            AuthSessionLimitError: If the parent run already has
                :data:`MAX_SESSIONS_PER_RUN` sessions registered.
        """
        if state is not None and len(state) < MIN_CALLER_SUPPLIED_STATE_LENGTH:
            raise InvalidAuthSessionStateError(state)
        with self._lock:
            resolved_state = state if state is not None else secrets.token_urlsafe(32)
            if resolved_state in self._sessions:
                raise DuplicateAuthSessionError(resolved_state)
            run_count = sum(1 for s in self._sessions.values() if s.run_id == run_id)
            if run_count >= MAX_SESSIONS_PER_RUN:
                raise AuthSessionLimitError(run_id)
            session = AuthSession(
                state=resolved_state,
                run_id=run_id,
                status="awaiting",
                created_at=datetime.now(UTC),
            )
            self._sessions[resolved_state] = session
            return dataclasses.replace(session)

    def capture_code(self, state: str, code: str) -> AuthSession:
        """Record an authorization code captured from the ASPSP redirect.

        One-shot: the session must currently be in ``awaiting`` state.
        Re-capturing an already-terminal session is rejected to provide
        replay protection without persistent nonce tracking.

        Args:
            state: The opaque state token identifying the session.
            code: The authorization code value from the redirect query.

        Returns:
            The updated :class:`AuthSession` in ``captured`` state.

        Raises:
            UnknownAuthSessionError: If ``state`` is not registered.
            AuthSessionAlreadyResolvedError: If the session is already in
                a terminal state.
        """
        with self._lock:
            session = self._sessions.get(state)
            if session is None:
                raise UnknownAuthSessionError(state)
            if session.status != "awaiting":
                raise AuthSessionAlreadyResolvedError(state, session.status)
            session.status = "captured"
            session.code = code
            session.captured_at = datetime.now(UTC)
            return dataclasses.replace(session)

    def capture_error(
        self,
        state: str,
        error: str,
        description: str | None = None,
    ) -> AuthSession:
        """Record an error captured from the ASPSP redirect.

        One-shot: the session must currently be in ``awaiting`` state.

        Args:
            state: The opaque state token identifying the session.
            error: The ``error`` code from the redirect query (per
                RFC 6749 §4.1.2.1).
            description: Optional ``error_description`` from the redirect.

        Returns:
            The updated :class:`AuthSession` in ``error`` state.

        Raises:
            UnknownAuthSessionError: If ``state`` is not registered.
            AuthSessionAlreadyResolvedError: If the session is already in
                a terminal state.
        """
        with self._lock:
            session = self._sessions.get(state)
            if session is None:
                raise UnknownAuthSessionError(state)
            if session.status != "awaiting":
                raise AuthSessionAlreadyResolvedError(state, session.status)
            session.status = "error"
            session.error = error
            session.error_description = description
            session.captured_at = datetime.now(UTC)
            return dataclasses.replace(session)

    def get(self, run_id: str, state: str) -> AuthSession | None:
        """Look up an auth session by ``(run_id, state)``.

        The two-part key prevents a caller from probing sessions that
        belong to a different run even if they guess a state value.

        Args:
            run_id: Identifier of the parent run.
            state: The opaque state token identifying the session.

        Returns:
            The :class:`AuthSession`, or ``None`` if no session exists for
            this ``(run_id, state)`` pair.
        """
        with self._lock:
            session = self._sessions.get(state)
            if session is None or session.run_id != run_id:
                return None
            return dataclasses.replace(session)

    def for_run(self, run_id: str) -> list[AuthSession]:
        """List all auth sessions registered against a run.

        Args:
            run_id: Identifier of the parent run.

        Returns:
            A list of :class:`AuthSession` records owned by the run, in
            registration order. Empty if the run has no sessions (or is
            unknown — the store does not validate run IDs).
        """
        with self._lock:
            return [dataclasses.replace(session) for session in self._sessions.values() if session.run_id == run_id]

    def discard_for_run(self, run_id: str) -> int:
        """Drop all auth sessions belonging to a run.

        Called from the run-lifecycle hook when a run transitions to a
        terminal state, so awaiting sessions don't outlive their parent.

        Args:
            run_id: Identifier of the parent run whose sessions to drop.

        Returns:
            The number of sessions removed.
        """
        with self._lock:
            to_drop = [state for state, session in self._sessions.items() if session.run_id == run_id]
            for state in to_drop:
                del self._sessions[state]
            return len(to_drop)

    def reset(self) -> None:
        """Wipe all auth-session state. Intended for test fixtures only.

        Production code must not call this — it would discard in-flight
        sessions without coordinating with the run driver awaiting them.
        """
        with self._lock:
            self._sessions.clear()


class AuthSessionError(Exception):
    """Base class for auth-session store errors."""


class UnknownAuthSessionError(AuthSessionError):
    """Raised when a lookup or capture targets an unregistered ``state``.

    Attributes:
        state: The unknown state token that was looked up.
    """

    state: str

    def __init__(self, state: str) -> None:
        """Initialise with the unknown state token.

        Args:
            state: The state token that was not found in the store.
        """
        super().__init__(f"Unknown auth-session state: {state!r}")
        self.state = state


class DuplicateAuthSessionError(AuthSessionError):
    """Raised when registering a ``state`` that is already in the store.

    Attributes:
        state: The duplicate state token that was offered.
    """

    state: str

    def __init__(self, state: str) -> None:
        """Initialise with the conflicting state token.

        Args:
            state: The state token that collided with an existing entry.
        """
        super().__init__(f"Auth-session state already registered: {state!r}")
        self.state = state


class InvalidAuthSessionStateError(AuthSessionError):
    """Raised when a caller-supplied ``state`` fails the minimum-entropy check.

    Attributes:
        state: The rejected state token.
    """

    state: str

    def __init__(self, state: str) -> None:
        """Initialise with the rejected state token.

        Args:
            state: The state token that failed validation.
        """
        super().__init__(
            f"Caller-supplied state must be at least {MIN_CALLER_SUPPLIED_STATE_LENGTH} characters",
        )
        self.state = state


class AuthSessionLimitError(AuthSessionError):
    """Raised when a run already has :data:`MAX_SESSIONS_PER_RUN` sessions.

    Attributes:
        run_id: Identifier of the run that hit the cap.
    """

    run_id: str

    def __init__(self, run_id: str) -> None:
        """Initialise with the offending run ID.

        Args:
            run_id: Identifier of the run that exceeded the session cap.
        """
        super().__init__(
            f"Run {run_id} already has {MAX_SESSIONS_PER_RUN} auth sessions",
        )
        self.run_id = run_id


class AuthSessionAlreadyResolvedError(AuthSessionError):
    """Raised when a capture targets a session that is already terminal.

    Provides one-shot replay protection: once a session has captured a
    code or an error, further captures are rejected rather than silently
    overwriting state.

    Attributes:
        state: The state token of the already-resolved session.
        status: The terminal status the session is already in.
    """

    state: str
    status: AuthSessionStatus

    def __init__(self, state: str, status: AuthSessionStatus) -> None:
        """Initialise with the resolved session's state and status.

        Args:
            state: The state token of the session that was already resolved.
            status: The terminal status (``captured`` or ``error``).
        """
        super().__init__(
            f"Auth session {state!r} already resolved (status={status})",
        )
        self.state = state
        self.status = status


# Module-level singleton for the Phase 1 single-process deployment.
auth_session_store = AuthSessionStore()
"""Global auth-session store instance shared across the Django process."""
