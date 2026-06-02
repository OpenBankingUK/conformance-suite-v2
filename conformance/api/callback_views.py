"""Public PSU authorization callback endpoint.

Receives the ASPSP browser redirect at the project-root path ``/callback/``
and correlates the ``state`` query parameter to a previously-registered
:class:`conformance.api.auth_session_store.AuthSession`. On success the
captured authorization ``code`` (or ``error``) is recorded against the
session and the participant sees a minimal HTML landing page telling them
to return to the CLI/UI.

Security model (PRD Phase 1):

* The endpoint is **intentionally not loopback-guarded**: a browser redirect
  from the ASPSP must be able to reach it, and even when the FCS runs
  locally the browser navigation arrives via the host's network stack.
  Coupling the endpoint to ``REMOTE_ADDR`` would break reverse-proxy
  deployments without adding real security.
* The threat model relies on three independent properties: ``state``
  unguessability (server-generated states use
  ``secrets.token_urlsafe(32)`` — 32 bytes of entropy; caller-supplied
  states are required to be at least 32 characters long, enforced by
  :class:`AuthSessionStore`), one-shot consumption (a second hit with the
  same state is rejected by
  :class:`AuthSessionAlreadyResolvedError`), and run-scoped binding (the
  loopback-guarded read API requires the parent run id to retrieve the
  captured code).
* Unknown, expired, or already-resolved ``state`` values return a generic
  400 page that does **not** disclose which states exist — the response is
  identical regardless of which failure mode was hit.
* CSRF exemption is not required: only ``GET`` is allowed (enforced by
  :func:`django.views.decorators.http.require_GET`). If a future change
  adds ``POST``, the CSRF posture MUST be revisited.
"""

from __future__ import annotations

import logging

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from conformance.api.auth_session_store import (
    AuthSession,
    AuthSessionAlreadyResolvedError,
    UnknownAuthSessionError,
    auth_session_store,
)
from conformance.api.run_store import run_store
from conformance.json_types import JsonValue

logger = logging.getLogger(__name__)

_GENERIC_FAILURE_MESSAGE = "Invalid or expired callback."
"""Single error message used for every failure mode.

Returned identically for unknown ``state``, missing ``state``, already-
resolved sessions, and redirects that carry neither ``code`` nor
``error``. A uniform response avoids leaking which states the store
knows about. Note that an *orphaned* session whose parent run record has
been pruned still captures successfully and renders the 200 landing page
— only the execution-log emission is silently skipped (see
:func:`_emit_callback_event`).
"""


@require_GET
def callback_view(request: HttpRequest) -> HttpResponse:
    """Handle the ASPSP PSU authorization redirect.

    The ASPSP redirects the participant's browser to
    ``/callback/?state=<opaque>&code=<auth-code>`` on success, or
    ``/callback/?state=<opaque>&error=<code>&error_description=<text>`` on
    failure (per RFC 6749 §4.1.2 / §4.1.2.1). This view correlates the
    ``state`` value to a registered :class:`AuthSession`, records the
    outcome on the session, emits an ``auth-callback-received`` event into
    the parent run's execution log (with the raw ``code`` masked by
    :class:`BufferedExecutionLogger`), and renders a minimal HTML landing
    page for the participant.

    Args:
        request: The inbound ``GET`` request from the participant's browser.

    Returns:
        A 200 HTML response on successful capture (success or ASPSP-reported
        error variant of the same template), or a 400 HTML response with a
        generic message for every failure path. The response body never
        echoes the ``state`` value or any ASPSP-supplied free text.
    """
    state = request.GET.get("state", "")
    code = request.GET.get("code")
    error = request.GET.get("error")
    error_description = request.GET.get("error_description")

    if not state:
        logger.warning("PSU callback hit without a state parameter")
        return _render_failure(request)

    try:
        if error:
            session = auth_session_store.capture_error(
                state,
                error=error,
                description=error_description,
            )
        elif code:
            session = auth_session_store.capture_code(state, code)
        else:
            # Neither ``code`` nor ``error`` present — malformed redirect.
            # Treat as a generic failure without touching the store.
            logger.warning("PSU callback hit with neither code nor error")
            return _render_failure(request)
    except UnknownAuthSessionError, AuthSessionAlreadyResolvedError:
        # Both arms collapse to the same generic response so the endpoint
        # does not disclose whether a state was unknown vs. already used.
        logger.warning("PSU callback rejected: unknown or already-resolved state")
        return _render_failure(request)

    _emit_callback_event(session, state=state)

    return render(
        request,
        "conformance/callback.html",
        {
            "outcome": "error" if error else "success",
            "error_code": error,
        },
        status=200,
    )


def _render_failure(request: HttpRequest) -> HttpResponse:
    """Render the generic 400 failure page for any callback rejection.

    Args:
        request: The inbound HTTP request being rejected.

    Returns:
        A 400 HTML response carrying :data:`_GENERIC_FAILURE_MESSAGE`. No
        request-supplied values are interpolated into the response.
    """
    return render(
        request,
        "conformance/callback.html",
        {
            "outcome": "failure",
            "message": _GENERIC_FAILURE_MESSAGE,
        },
        status=400,
    )


def _emit_callback_event(session: AuthSession, *, state: str) -> None:
    """Append an ``auth-callback-received`` event to the parent run's log.

    The event payload is built from the **resolved** :class:`AuthSession`
    rather than from the raw redirect query parameters, so the emitted
    fields always match the stored outcome. This prevents a malformed or
    hostile redirect (for example one that carries both ``error`` and
    ``code``, or an ``error_description`` on a success redirect) from
    landing fields in the execution log that contradict the session's
    actual ``captured``/``error`` status — a concern that becomes
    security-relevant under a future developer-mode toggle that lifts the
    :data:`conformance.masking.SENSITIVE_JSON_KEYS` masking of ``code``.

    The payload is handed unmodified to
    :meth:`BufferedExecutionLogger.emit`; the logger masks the ``code``
    field via :data:`conformance.masking.SENSITIVE_JSON_KEYS` so the raw
    authorization code never lands in NDJSON. If the parent run record
    has been pruned or never carried a logger, the emission is silently
    skipped — the captured session itself is still retrievable via the
    loopback API.

    Args:
        session: The resolved auth session whose status drives the payload
            shape. ``captured`` sessions emit ``{state, code}``; ``error``
            sessions emit ``{state, error[, error_description]}``.
        state: The opaque state token from the redirect query. Equal to
            ``session.state`` by construction; passed explicitly so the
            caller controls the wire-format key.
    """
    record = run_store.get_run(session.run_id)
    if record is None or record.execution_logger is None:
        return
    payload: dict[str, JsonValue] = {"state": state}
    if session.status == "captured" and session.code is not None:
        payload["code"] = session.code
    elif session.status == "error" and session.error is not None:
        payload["error"] = session.error
        if session.error_description is not None:
            payload["error_description"] = session.error_description
    record.execution_logger.emit(
        "auth-callback-received",
        payload=payload,
    )
