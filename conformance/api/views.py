"""Django views for the conformance run REST API.

Implements the Phase 1 local REST API (PRD: OBL Engineering Story #5):
unauthenticated, designed for local Docker deployment. Localhost access
restriction is a deployment concern (Docker port publishing to 127.0.0.1),
not an application-level guarantee. Supports starting a run, polling run
status, and retrieving the report.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from conformance.api.run_store import RunConflictError, run_store
from conformance.executor import run_manifest
from conformance.http import build_json_http_client
from conformance.manifest import ManifestError, load_manifest_from_object
from conformance.model_bank_config import ConfigError, ModelBankConfig, parse_model_bank_config
from conformance.runner import run_model_bank_smoke_check

logger = logging.getLogger(__name__)


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
    Localhost access restriction is a deployment requirement (Docker port
    publishing to 127.0.0.1), not enforced at the application level.

    Args:
        request: The incoming HTTP POST request with JSON body.

    Returns:
        201 with run status JSON on success, 400 on invalid input,
        409 if a run is already active.
    """
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError, ValueError:
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


def _execute_run(run_id: str, config: ModelBankConfig, manifest: Any) -> None:
    """Execute a conformance run in a background thread.

    Transitions the run record through running → completed/failed.

    Args:
        run_id: The run identifier to update in the store.
        config: Validated model-bank configuration.
        manifest: Parsed manifest object, or None for a smoke-check run.
    """
    run_store.mark_running(run_id)
    try:
        if manifest is None:
            result = run_model_bank_smoke_check(config)
        else:
            http_client = build_json_http_client(
                timeout_seconds=config.timeout_seconds,
                ca_bundle_path=config.tls.ca_bundle_path,
                client_certificate_path=config.tls.client_certificate_path,
                client_private_key_path=config.tls.client_private_key_path,
            )
            try:
                result = run_manifest(manifest, environment=config.environment, client=http_client)
            finally:
                http_client.close()

        run_store.mark_completed(run_id, result=result.to_json_object())
    except Exception:
        logger.exception("Run %s failed with an internal error", run_id)
        run_store.mark_failed(run_id, error="An internal error occurred")
