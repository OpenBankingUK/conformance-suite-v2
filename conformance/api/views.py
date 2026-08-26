"""Django views for the conformance run REST API.

Implements the Phase 1 local REST API (PRD: OBL Engineering Story #5):
unauthenticated, designed for local Docker deployment. Defence in depth:
endpoints reject non-loopback ``REMOTE_ADDR`` by default so a misconfigured
Docker port publish (e.g. ``-p 0.0.0.0:8443``) does not expose the API.
Localhost binding remains the primary control; this guard is a backstop.
Set ``CONFORMANCE_API_ALLOW_NON_LOCAL=true`` to opt out (e.g. for an
authenticated reverse proxy). Supports starting a run, polling run status,
and retrieving the report.
"""

from __future__ import annotations

import functools
import ipaddress
import json
import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from conformance.api.auth_session_store import (
    AuthSessionLimitError,
    DuplicateAuthSessionError,
    InvalidAuthSessionStateError,
    auth_session_store,
)
from conformance.api.run_lifecycle import start_run
from conformance.api.run_store import RunConflictError, run_store
from conformance.catalogue import CompiledTestPlan
from conformance.json_types import JsonObject, JsonValue
from conformance.test_plan_validation import TestPlanValidationError, prepare_test_plan_for_run

logger = logging.getLogger(__name__)


def _requested_display_time_zone(request: HttpRequest) -> str | None:
    """Return an explicit display timezone requested by the caller.

    Args:
        request: The inbound HTTP request.

    Returns:
        Trimmed IANA timezone name from ``timeZone`` query parameter or
        ``X-Time-Zone`` header, or ``None`` when not supplied.
    """
    raw_query_time_zone = request.GET.get("timeZone")
    if raw_query_time_zone is not None:
        query_time_zone = raw_query_time_zone.strip()
        if query_time_zone:
            return query_time_zone

    raw_header_time_zone = request.headers.get("X-Time-Zone")
    if raw_header_time_zone is None:
        return None
    header_time_zone = raw_header_time_zone.strip()
    return header_time_zone or None


def _resolve_display_time_zone(request: HttpRequest) -> tuple[str, ZoneInfo] | None:
    """Resolve and validate the caller-selected display timezone.

    Args:
        request: The inbound HTTP request.

    Returns:
        Tuple of ``(time zone name, ZoneInfo)`` when provided, otherwise
        ``None``.

    Raises:
        ValueError: If the caller supplied an unknown IANA timezone.
    """
    time_zone_name = _requested_display_time_zone(request)
    if time_zone_name is None:
        return None
    try:
        return time_zone_name, ZoneInfo(time_zone_name)
    except ZoneInfoNotFoundError as error:
        raise ValueError("timeZone must be a valid IANA timezone") from error


def _to_local_iso_timestamp(timestamp: str, *, time_zone: ZoneInfo) -> str | None:
    """Convert an ISO 8601 timestamp string to a timezone-local ISO string.

    Args:
        timestamp: Canonical ISO 8601 timestamp string.
        time_zone: Caller-selected display timezone.

    Returns:
        Localized ISO 8601 timestamp string, or ``None`` if ``timestamp`` is
        not parseable or is timezone-naive.
    """
    normalized_timestamp = timestamp.replace("Z", "+00:00")
    try:
        parsed_timestamp = datetime.fromisoformat(normalized_timestamp)
    except ValueError:
        return None
    if parsed_timestamp.tzinfo is None:
        return None
    return parsed_timestamp.astimezone(time_zone).isoformat()


def _append_local_timestamp_fields(
    payload: JsonObject,
    *,
    canonical_fields: tuple[str, ...],
    time_zone_name: str,
    time_zone: ZoneInfo,
) -> None:
    """Append additive display-only local timestamp fields to a payload.

    Args:
        payload: JSON response payload to mutate.
        canonical_fields: Canonical timestamp field names to localize.
        time_zone_name: Requested IANA timezone string.
        time_zone: Parsed timezone object.
    """
    payload["displayTimeZone"] = time_zone_name
    for field_name in canonical_fields:
        raw_value = payload.get(field_name)
        if not isinstance(raw_value, str):
            continue
        localized_value = _to_local_iso_timestamp(raw_value, time_zone=time_zone)
        if localized_value is None:
            continue
        payload[f"{field_name}Local"] = localized_value


def _is_loopback_address(remote_addr: str) -> bool:
    """Return True if ``remote_addr`` is an IPv4/IPv6 loopback address.

    Args:
        remote_addr: The value of ``request.META['REMOTE_ADDR']``.

    Returns:
        True for ``127.0.0.0/8`` and ``::1``; False for any other address
        (including malformed input).
    """
    try:
        addr = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    return addr.is_loopback


def _require_loopback[**P](
    view_func: Callable[P, HttpResponse],
) -> Callable[P, HttpResponse]:
    """Reject non-loopback requests with HTTP 403 unless opt-out is set.

    Defence-in-depth backstop for the Phase 1 PRD assumption that the API
    is reachable only from ``127.0.0.1``. The guard is bypassed when the
    ``API_ALLOW_NON_LOCAL`` Django setting is truthy (driven by the
    ``CONFORMANCE_API_ALLOW_NON_LOCAL`` environment variable), which lets
    operators front the API with an authenticated reverse proxy.

    The decorator inspects ``request.META['REMOTE_ADDR']`` directly and does
    not honour ``X-Forwarded-For`` — trusting forwarded headers without a
    vetted proxy chain would itself be a security bug.

    Args:
        view_func: The Django view to wrap.

    Returns:
        A wrapper that returns 403 for non-loopback callers and otherwise
        delegates to ``view_func``.
    """

    @functools.wraps(view_func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> HttpResponse:
        """Run the loopback check, then delegate to the wrapped view.

        Args:
            *args: Positional arguments forwarded to the view. The first
                must be the ``HttpRequest`` (Django view contract).
            **kwargs: Keyword arguments forwarded to the view.

        Returns:
            A 403 ``JsonResponse`` for non-loopback callers when the guard
            is enabled, otherwise the wrapped view's own response. The
            wrapped view may itself be decorated with ``@require_GET`` /
            ``@require_POST``, which can return ``HttpResponseNotAllowed``;
            hence the widened ``HttpResponse`` return type.
        """
        request = args[0]
        assert isinstance(request, HttpRequest)  # noqa: S101 — Django view contract
        if not getattr(settings, "API_ALLOW_NON_LOCAL", False):
            remote_addr = request.META.get("REMOTE_ADDR", "")
            if not _is_loopback_address(remote_addr):
                logger.warning(
                    "Rejected non-loopback API request from %s to %s",
                    remote_addr or "<unknown>",
                    request.path,
                )
                return JsonResponse(
                    {
                        "error": (
                            "API access restricted to loopback addresses. "
                            "Set CONFORMANCE_API_ALLOW_NON_LOCAL=true to disable."
                        )
                    },
                    status=403,
                )
        return view_func(*args, **kwargs)

    return wrapper


@_require_loopback
@csrf_exempt
@require_POST
def create_run(request: HttpRequest) -> JsonResponse:
    """Start a new conformance run from a JSON request body.

    The request body must be a canonical ``schemaVersion: "1.0"`` test plan, or
    a JSON object with a canonical test plan under the ``testPlan`` key. The plan
    is compiled against the bundled catalogues before the asynchronous run
    starts, so participants receive immediate feedback for unsupported
    combinations, unknown endpoints, missing runtime inputs, or invalid setup.
    The run executes asynchronously in a background thread; the response
    returns immediately with the run ID and status.

    CSRF is exempt because this is an unauthenticated API designed for
    programmatic/CI access (PRD Phase 1). No browser session is involved.
    Localhost access is primarily controlled by Docker port publishing to
    127.0.0.1; the ``_require_loopback`` decorator provides an
    application-level defence-in-depth guard that can be disabled via
    ``CONFORMANCE_API_ALLOW_NON_LOCAL`` for trusted-network deployments.

    Args:
        request: The incoming HTTP POST request with JSON body.

    Returns:
        201 with run status JSON on success, 400 on invalid input,
        409 if a run is already active.
    """
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError, UnicodeDecodeError:
        # UnicodeDecodeError covers request bodies whose bytes are not valid
        # UTF-8; json.loads decodes bytes as UTF-8 internally and raises this
        # before JSONDecodeError gets a chance. Both are caller-input errors
        # at the parse boundary and warrant the same 400 response.
        return JsonResponse({"error": "Request body must be valid JSON"}, status=400)

    if not isinstance(body, dict):
        return JsonResponse({"error": "Request body must be a JSON object"}, status=400)

    canonical_response = _create_run_from_canonical_test_plan(body)
    if canonical_response is not None:
        return canonical_response

    legacy_fields = sorted(set(body) & {"config", "planSpec", "manifest", "deselectStepIds"})
    if legacy_fields:
        return JsonResponse(
            {
                "error": (
                    "Legacy run request field(s) are no longer supported: "
                    f"{', '.join(legacy_fields)}. Submit a canonical schemaVersion 1.0 test plan as the "
                    'request body or under "testPlan".'
                )
            },
            status=400,
        )
    return JsonResponse(
        {"error": 'Request body must be a schemaVersion 1.0 test plan or contain a "testPlan" object'},
        status=400,
    )


def _create_run_from_canonical_test_plan(body: dict[str, JsonValue]) -> JsonResponse | None:
    """Create a run from a canonical JSON-first test-plan request body.

    Args:
        body: Decoded JSON request body.

    Returns:
        JSON response when the body is a canonical test plan request, otherwise
        ``None`` when the body is not a canonical test-plan request.
    """
    raw_test_plan = body.get("testPlan")
    if raw_test_plan is not None:
        unknown_keys = sorted(set(body) - {"testPlan"})
        if unknown_keys:
            return JsonResponse({"error": f"Unknown request field(s): {', '.join(unknown_keys)}"}, status=400)
        if not isinstance(raw_test_plan, dict):
            return JsonResponse({"error": '"testPlan" key must be a JSON object'}, status=400)
        return _start_canonical_test_plan(raw_test_plan)
    if body.get("schemaVersion") == "1.0":
        return _start_canonical_test_plan(body)
    return None


def _start_canonical_test_plan(raw_test_plan: dict[str, JsonValue]) -> JsonResponse:
    """Validate and launch a canonical JSON-first test plan.

    Args:
        raw_test_plan: Decoded canonical test-plan JSON object.

    Returns:
        Run status JSON on success or a validation/conflict error response.
    """
    try:
        prepared = prepare_test_plan_for_run(raw_test_plan, base_dir=Path.cwd())
        api_file_reference_error = _api_file_reference_error(prepared.compiled_plan, prepared.runtime_inputs)
        if api_file_reference_error is not None:
            return api_file_reference_error
    except TestPlanValidationError as error:
        return JsonResponse(
            {
                "error": f"Test plan validation failed: {error}",
                "validation": error.result.to_json_object(),
            },
            status=400,
        )

    try:
        response_body = start_run(
            config=prepared.config,
            compiled_plan=prepared.compiled_plan,
            runtime_inputs=prepared.runtime_inputs,
            runtime_input_base_dir=Path.cwd(),
            plan_snapshot=prepared.snapshot,
            validation_result=prepared.validation.to_json_object(),
        )
    except RunConflictError as error:
        return JsonResponse(
            {"error": "A run is already active", "activeRunId": error.active_run_id},
            status=409,
        )

    return JsonResponse(response_body, status=201)


def _api_file_reference_error(
    compiled_plan: CompiledTestPlan,
    runtime_inputs: Mapping[str, JsonValue],
) -> JsonResponse | None:
    """Return a 400 response when an API plan would read server-local files.

    Args:
        compiled_plan: Compiled catalogue plan used to identify selected runtime
            input types.
        runtime_inputs: Participant-supplied runtime inputs keyed by input id.

    Returns:
        Error response when any selected ``file_reference`` input is supplied,
        otherwise ``None``.
    """
    file_reference_input_ids = sorted(
        trace.input_id
        for trace in compiled_plan.traceability.runtime_input_snapshot
        if trace.input_type == "file_reference"
        and trace.input_id in runtime_inputs
        and runtime_inputs[trace.input_id] is not None
    )
    if not file_reference_input_ids:
        return None
    joined_input_ids = ", ".join(file_reference_input_ids)
    return JsonResponse(
        {
            "error": (
                "Test plan validation failed: file_reference runtime inputs are not accepted by the REST API: "
                f"{joined_input_ids}"
            )
        },
        status=400,
    )


@_require_loopback
@require_GET
def get_run_status(request: HttpRequest, run_id: str) -> JsonResponse:
    """Return the current status of a conformance run.

    Args:
        request: The incoming HTTP GET request.
        run_id: The unique run identifier from the URL path.

    Returns:
        200 with run status JSON, or 404 if the run ID is unknown.
    """
    try:
        display_time_zone = _resolve_display_time_zone(request)
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)

    record = run_store.get_run(run_id)
    if record is None:
        return JsonResponse({"error": "Run not found"}, status=404)

    body: JsonObject = record.to_status_json()
    if display_time_zone is not None:
        time_zone_name, time_zone = display_time_zone
        _append_local_timestamp_fields(
            body,
            canonical_fields=("createdAt", "startedAt", "finishedAt"),
            time_zone_name=time_zone_name,
            time_zone=time_zone,
        )
    return JsonResponse(body)


@_require_loopback
@require_GET
def get_run_result(request: HttpRequest, run_id: str) -> JsonResponse:
    """Return the structured result of a completed conformance run.

    Args:
        request: The incoming HTTP GET request.
        run_id: The unique run identifier from the URL path.

    Returns:
        200 with the full result JSON on success, 404 if the run ID is
        unknown, 409 if the run has not yet completed, or 500 if the run
        failed internally.
    """
    try:
        display_time_zone = _resolve_display_time_zone(request)
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)

    record = run_store.get_run(run_id)
    if record is None:
        return JsonResponse({"error": "Run not found"}, status=404)
    if record.status in ("pending", "running"):
        return JsonResponse(
            {"error": "Run has not completed yet", "status": record.status},
            status=409,
        )
    if record.status == "failed":
        return JsonResponse({"error": "Run failed internally"}, status=500)

    result_body = record.result
    body: JsonObject = dict(result_body) if isinstance(result_body, dict) else {}
    if display_time_zone is not None:
        time_zone_name, time_zone = display_time_zone
        _append_local_timestamp_fields(
            body,
            canonical_fields=("startedAt", "finishedAt"),
            time_zone_name=time_zone_name,
            time_zone=time_zone,
        )
    return JsonResponse(body)


@_require_loopback
@require_GET
def get_run_log(request: HttpRequest, run_id: str) -> HttpResponse:
    """Return the structured JSON execution log for a run.

    Returns the log snapshot taken at request time. For runs that are
    still in-flight the response contains a partial JSON array; the client
    can re-poll to receive newer events. Masking
    is applied at append time inside the engine, so callers receive the
    same masked event payloads that the CLI writes to disk.

    Args:
        request: The incoming HTTP GET request.
        run_id: The unique run identifier from the URL path.

    Returns:
        200 with ``application/json`` body on success, or 404 if the
        run ID is unknown, or 500 if the run exists but its execution log is
        unavailable.
    """
    record = run_store.get_run(run_id)
    if record is None:
        return JsonResponse({"error": "Run not found"}, status=404)
    # Use the execution_logger reference from the single lookup rather than
    # calling get_run_log_bytes() separately.  A second lookup risks a 500
    # instead of the correct 404 when the run is pruned between the two calls.
    if record.execution_logger is None:
        return JsonResponse({"error": "Execution log unavailable for this run"}, status=500)
    return HttpResponse(record.execution_logger.to_json_bytes(), content_type="application/json")


@_require_loopback
@csrf_exempt
@require_POST
def register_auth_session(request: HttpRequest, run_id: str) -> JsonResponse:
    """Register an expected PSU authorization session for a run.

    The run driver calls this before redirecting the participant's browser
    to the ASPSP, so the public ``/callback/`` endpoint can correlate the
    inbound ``state`` query parameter back to a known session. The
    response includes the opaque ``state`` token that the caller MUST use
    when constructing the authorization request URL.

    CSRF is exempt because this is an unauthenticated, loopback-guarded API
    designed for programmatic callers. No browser session is involved.

    Request body (JSON, optional): an object with an optional ``state``
    field. When supplied, the value must meet the minimum entropy bar
    enforced by :class:`AuthSessionStore`; when omitted, the store
    generates one via ``secrets.token_urlsafe``. An empty body is
    equivalent to ``{}``.

    On success an ``auth-session-registered`` event is appended to the
    parent run's execution log with payload ``{"state": ..., "status":
    "awaiting"}`` — the state value is non-sensitive (it leaves the
    process inside the authorization URL anyway) and proves the FCS
    expected this state, which complements the later
    ``auth-callback-received`` event.

    Args:
        request: The inbound ``POST`` request from the run driver.
        run_id: The unique run identifier from the URL path.

    Returns:
        201 with ``{state, status, createdAt}`` on success; 400 on
        malformed body, caller-supplied state below the entropy bar, or
        when the per-run session cap is exceeded
        (:class:`AuthSessionLimitError`); 404 if the run is unknown; 409
        if the run has already reached a terminal state or if the
        requested state is already registered. After a successful
        ``register`` the run record is re-fetched: if it has been pruned
        or transitioned to a terminal state in the interim (racing the
        run lifecycle's ``discard_for_run`` cleanup), the
        just-created session is rolled back via
        :meth:`AuthSessionStore.discard` and the corresponding 404/409
        is returned instead of 201.
    """
    try:
        display_time_zone = _resolve_display_time_zone(request)
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)

    raw_state: str | None = None
    # Parse whenever the caller sent a JSON-shaped payload, regardless of
    # whether they remembered ``Content-Type: application/json``. The
    # previous ``content_type == "application/json"`` gate silently dropped
    # bodies posted with the curl default of
    # ``application/x-www-form-urlencoded`` (e.g. ``curl -d
    # '{"state":...}'``) and returned 201 with a server-generated state —
    # masking the client bug.
    #
    # ``multipart/form-data`` is not a supported carrier for this
    # endpoint's optional JSON body. However, Django's test client and
    # HTML forms produce a non-empty multipart envelope even when the
    # caller intends a bodyless request. To avoid rejecting truly
    # bodyless calls, treat multipart as empty *only* when it contains no
    # parsed fields/files; multipart requests that carry any fields are
    # rejected with 400 so a caller-supplied ``state`` posted as form
    # fields is never silently ignored (reintroducing the silent-drop
    # bug). Mirrors ``create_run``'s parse-at-the-boundary behaviour.
    if request.content_type == "multipart/form-data":
        # Accessing ``request.POST``/``FILES`` consumes the request
        # stream, so reading ``request.body`` afterwards raises
        # ``RawPostDataException``. Handle multipart in its own branch
        # and never fall through to the JSON parser for this content
        # type.
        if request.POST or request.FILES:
            return JsonResponse({"error": "Request body must be a JSON object"}, status=400)
    elif request.body:
        # UnicodeDecodeError covers bytes that are not valid UTF-8 (json.loads
        # decodes internally and raises this before JSONDecodeError); both are
        # parse-boundary caller errors warranting the same 400.
        try:
            parsed_body = json.loads(request.body)
        except json.JSONDecodeError, UnicodeDecodeError:
            return JsonResponse({"error": "Request body must be valid JSON"}, status=400)
        if not isinstance(parsed_body, dict):
            return JsonResponse({"error": "Request body must be a JSON object"}, status=400)
        raw_state = parsed_body.get("state")
        if raw_state is not None and not isinstance(raw_state, str):
            return JsonResponse({"error": '"state" must be a string if provided'}, status=400)

    record = run_store.get_run(run_id)
    if record is None:
        return JsonResponse({"error": "Run not found"}, status=404)
    if record.status in ("completed", "failed"):
        return JsonResponse(
            {"error": "Run is no longer active", "status": record.status},
            status=409,
        )

    try:
        session = auth_session_store.register(run_id, state=raw_state)
    except InvalidAuthSessionStateError as error:
        return JsonResponse({"error": str(error)}, status=400)
    except DuplicateAuthSessionError as error:
        return JsonResponse({"error": str(error)}, status=409)
    except AuthSessionLimitError as error:
        return JsonResponse({"error": str(error)}, status=400)

    # Re-validate the run record after registration to close the race
    # against the run lifecycle's terminal-state cleanup: the run may have
    # transitioned to ``completed``/``failed`` (and ``discard_for_run``
    # may have already swept the run's sessions) while this request was
    # in-flight. Roll back the just-created session so it cannot outlive
    # its parent run's lifecycle guarantees.
    post_record = run_store.get_run(run_id)
    if post_record is None:
        auth_session_store.discard(run_id, session.state)
        return JsonResponse({"error": "Run not found"}, status=404)
    if post_record.status in ("completed", "failed"):
        auth_session_store.discard(run_id, session.state)
        return JsonResponse(
            {"error": "Run is no longer active", "status": post_record.status},
            status=409,
        )

    if post_record.execution_logger is not None:
        post_record.execution_logger.emit(
            "auth-session-registered",
            payload={"state": session.state, "status": session.status},
        )

    body: JsonObject = {
        "state": session.state,
        "status": session.status,
        "createdAt": session.created_at.isoformat(),
    }
    if display_time_zone is not None:
        time_zone_name, time_zone = display_time_zone
        _append_local_timestamp_fields(
            body,
            canonical_fields=("createdAt",),
            time_zone_name=time_zone_name,
            time_zone=time_zone,
        )
    return JsonResponse(body, status=201)


@_require_loopback
@require_GET
def get_auth_session(request: HttpRequest, run_id: str, state: str) -> JsonResponse:
    """Return the current state of a registered PSU auth session.

    The run driver polls this endpoint after redirecting the browser to
    the ASPSP, waiting for the ``status`` to transition from ``awaiting``
    to ``captured`` (or ``error``). The captured authorization ``code``
    is included in the response only when ``status`` is ``captured`` —
    callers are expected to consume it immediately for the token
    exchange, and the run-scoped key prevents probing of sessions owned
    by other runs.

    Args:
        request: The inbound ``GET`` request from the run driver.
        run_id: The unique run identifier from the URL path.
        state: The opaque state token identifying the session.

    Returns:
        200 with ``{state, status, createdAt, capturedAt?, code?, error?,
        errorDescription?}`` on success; 404 if either the run or the
        session is unknown. The 404 response is identical for both
        failure modes so an unauthenticated caller cannot enumerate run
        IDs by probing.
    """
    try:
        display_time_zone = _resolve_display_time_zone(request)
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)

    if run_store.get_run(run_id) is None:
        return JsonResponse({"error": "Auth session not found"}, status=404)
    session = auth_session_store.get(run_id, state)
    if session is None:
        return JsonResponse({"error": "Auth session not found"}, status=404)

    body: JsonObject = {
        "state": session.state,
        "status": session.status,
        "createdAt": session.created_at.isoformat(),
    }
    if session.captured_at is not None:
        body["capturedAt"] = session.captured_at.isoformat()
    if session.code is not None:
        body["code"] = session.code
    if session.error is not None:
        body["error"] = session.error
    if session.error_description is not None:
        body["errorDescription"] = session.error_description

    if display_time_zone is not None:
        time_zone_name, time_zone = display_time_zone
        _append_local_timestamp_fields(
            body,
            canonical_fields=("createdAt", "capturedAt"),
            time_zone_name=time_zone_name,
            time_zone=time_zone,
        )
    return JsonResponse(body, status=200)
