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
  unguessability (≥32 bytes of entropy enforced by
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
resolved sessions, and orphaned sessions whose parent run has gone away.
A uniform response avoids leaking which states the store knows about.
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

    _emit_callback_event(session.run_id, state=state, code=code, error=error)

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


def _emit_callback_event(
    run_id: str,
    *,
    state: str,
    code: str | None,
    error: str | None,
) -> None:
    """Append an ``auth-callback-received`` event to the parent run's log.

    The event payload is built from the inbound redirect parameters and
    handed unmodified to :meth:`BufferedExecutionLogger.emit`; the logger
    masks the ``code`` field via
    :data:`conformance.masking.SENSITIVE_JSON_KEYS` so the raw
    authorization code never lands in NDJSON. If the parent run record has
    been pruned or never carried a logger, the emission is silently
    skipped — the captured session itself is still retrievable via the
    loopback API.

    Args:
        run_id: Identifier of the parent run that owns the auth session.
        state: The opaque state token from the redirect query.
        code: The raw authorization code, if the redirect carried one.
            Passed through unmodified so the logger can mask it.
        error: The ASPSP-supplied error code, if the redirect carried one.
    """
    record = run_store.get_run(run_id)
    if record is None or record.execution_logger is None:
        return
    payload: dict[str, JsonValue] = {"state": state}
    if code is not None:
        payload["code"] = code
    if error is not None:
        payload["error"] = error
    record.execution_logger.emit(
        "auth-callback-received",
        payload=payload,
    )
