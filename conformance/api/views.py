"""Django views for the conformance run REST API.

Implements the Phase 1 local REST API (PRD: OBL Engineering Story #5):
unauthenticated, designed for local Docker deployment. Defence in depth:
endpoints reject non-loopback ``REMOTE_ADDR`` by default so a misconfigured
Docker port publish (e.g. ``-p 0.0.0.0:8000``) does not expose the API.
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
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from conformance.api.run_store import RunConflictError, run_store
from conformance.execution_log import NullExecutionLogger, warn_if_developer_mode
from conformance.executor import run_manifest
from conformance.http import build_json_http_client
from conformance.manifest import ManifestError, load_manifest_from_object
from conformance.model_bank_config import ConfigError, ModelBankConfig, parse_model_bank_config
from conformance.runner import run_model_bank_smoke_check

if TYPE_CHECKING:
    from conformance.manifest import Manifest

logger = logging.getLogger(__name__)


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

    The request body must be a JSON object with a required ``config`` key
    (model-bank config object) and an optional ``manifest`` key (v0/v1
    manifest object). The run executes asynchronously in a background
    thread; the response returns immediately with the run ID and status.

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

    raw_config = body.get("config")
    if raw_config is None or not isinstance(raw_config, dict):
        return JsonResponse({"error": '"config" key is required and must be a JSON object'}, status=400)

    raw_manifest = body.get("manifest")
    if raw_manifest is not None and not isinstance(raw_manifest, dict):
        return JsonResponse({"error": '"manifest" must be a JSON object if provided'}, status=400)

    # Validate config eagerly so the caller gets immediate feedback.
    # base_dir anchors relative TLS certificate paths in the request body to
    # the Django process CWD (set by the Docker entrypoint). Path traversal
    # is rejected by parse_model_bank_config itself.
    try:
        config = parse_model_bank_config(raw_config, base_dir=Path.cwd())
    except ConfigError as error:
        return JsonResponse({"error": f"Config validation failed: {error}"}, status=400)

    # Validate manifest eagerly if provided.
    manifest = None
    if raw_manifest is not None:
        try:
            manifest = load_manifest_from_object(raw_manifest)
        except ManifestError as error:
            return JsonResponse({"error": f"Manifest validation failed: {error}"}, status=400)

    try:
        record = run_store.create_run()
    except RunConflictError as error:
        return JsonResponse(
            {"error": "A run is already active", "activeRunId": error.active_run_id},
            status=409,
        )

    # Execute in background thread (Phase 1: single-threaded engine, one run
    # at a time, but we don't block the HTTP response).
    warn_if_developer_mode()
    thread = threading.Thread(
        target=_execute_run,
        args=(record.run_id, config, manifest),
        daemon=True,
    )
    # Snapshot the pending state before starting the thread to avoid a
    # race where the background thread calls mark_running() before the
    # response is serialised.
    response_body = record.to_status_json()
    thread.start()

    return JsonResponse(response_body, status=201)


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
    record = run_store.get_run(run_id)
    if record is None:
        return JsonResponse({"error": "Run not found"}, status=404)
    return JsonResponse(record.to_status_json())


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
    return JsonResponse(record.result)


@_require_loopback
@require_GET
def get_run_log(request: HttpRequest, run_id: str) -> HttpResponse:
    """Return the structured NDJSON execution log for a run.

    Returns the log snapshot taken at request time. For runs that are
    still in-flight the response contains a partial log (one JSON object
    per line) — the client can re-poll to receive newer events. Masking
    is applied at append time inside the engine, so callers receive the
    same bytes that the CLI writes to disk.

    Args:
        request: The incoming HTTP GET request.
        run_id: The unique run identifier from the URL path.

    Returns:
        200 with ``application/x-ndjson`` body on success, or 404 if the
        run ID is unknown, or 500 if the run exists but its execution log is
        unavailable.
    """
    record = run_store.get_run(run_id)
    if record is None:
        return JsonResponse({"error": "Run not found"}, status=404)
    log_bytes = run_store.get_run_log_bytes(run_id)
    if log_bytes is None:
        return JsonResponse({"error": "Execution log unavailable for this run"}, status=500)
    return HttpResponse(log_bytes, content_type="application/x-ndjson")


def _execute_run(run_id: str, config: ModelBankConfig, manifest: Manifest | None) -> None:
    """Execute a conformance run in a background thread.

    Transitions the run record through running → completed/failed.

    Args:
        run_id: The run identifier to update in the store.
        config: Validated model-bank configuration.
        manifest: Parsed manifest object, or None for a smoke-check run.
    """
    run_store.mark_running(run_id)
    try:
        run_record = run_store.get_run(run_id)
        # ``get_run`` returns a shallow copy whose ``execution_logger``
        # reference points at the same live buffer the API exposes; either
        # accessing the live record or the copy yields the same logger.
        logger_sink = (run_record.execution_logger if run_record is not None else None) or NullExecutionLogger()
        if manifest is None:
            result = run_model_bank_smoke_check(config, execution_logger=logger_sink)
        else:
            http_client = build_json_http_client(
                timeout_seconds=config.timeout_seconds,
                ca_bundle_path=config.tls.ca_bundle_path,
                client_certificate_path=config.tls.client_certificate_path,
                client_private_key_path=config.tls.client_private_key_path,
            )
            try:
                result = run_manifest(
                    manifest,
                    environment=config.environment,
                    client=http_client,
                    execution_logger=logger_sink,
                )
            finally:
                http_client.close()

        run_store.mark_completed(run_id, result=result.to_json_object())
    except Exception:
        logger.exception("Run %s failed with an internal error", run_id)
        run_store.mark_failed(run_id, error="An internal error occurred")
