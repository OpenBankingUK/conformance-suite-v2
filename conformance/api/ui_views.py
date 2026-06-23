"""Browser views for participant-facing plan builder and run monitoring."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import cast
from zoneinfo import ZoneInfo

from django.http import HttpRequest, HttpResponse, HttpResponseNotFound, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from conformance.api.plan_builder import PlanBuilderForm, PlanPreview, guided_flow_context
from conformance.api.run_lifecycle import start_run
from conformance.api.run_store import RunConflictError, RunRecord, run_store
from conformance.execution_log import ExecutionEvent
from conformance.json_types import JsonObject, JsonValue

_UI_DISPLAY_TIME_ZONE = ZoneInfo("Europe/London")
"""Open Banking UK browser fallback timezone for server-rendered timestamps."""

_RUN_WAIT_MAX_TIMEOUT_SECONDS = 30.0
"""Maximum server-side wait duration for the browser long-poll endpoint."""


@require_GET
def plan_builder(request: HttpRequest) -> HttpResponse:
    """Render the participant plan-builder input form.

    Args:
        request: The incoming browser GET request.

    Returns:
        HTML response containing JSON input fields and preview controls.
    """
    return render(request, "conformance/plan_builder.html", _plan_context(PlanBuilderForm()))


@require_POST
def plan_preview(request: HttpRequest) -> HttpResponse:
    """Validate submitted JSON and render the selectable plan preview.

    Args:
        request: The incoming browser POST request with form-encoded JSON
            fields and optional step selection values.

    Returns:
        HTML response with validation errors or the current step preview.
    """
    form = PlanBuilderForm(data=request.POST)
    status = 200 if form.is_valid() else 400
    return render(request, "conformance/plan_builder.html", _plan_context(form), status=status)


@require_POST
def plan_launch(request: HttpRequest) -> HttpResponse:
    """Validate the submitted plan and launch a browser-supported run.

    Args:
        request: The incoming browser POST request with config, manifest,
            and step selection values.

    Returns:
        A redirect to the run detail page when launch succeeds, or an HTML
        response describing validation, support, or active-run conflicts.
    """
    form = PlanBuilderForm(data=request.POST)
    if not form.is_valid() or form.preview is None:
        return render(request, "conformance/plan_builder.html", _plan_context(form), status=400)

    preview = form.preview
    if not preview.launch_supported:
        return render(
            request,
            "conformance/plan_builder.html",
            _plan_context(
                form,
                launch_error="This manifest can be previewed but cannot be launched from the browser UI yet.",
            ),
            status=400,
        )

    try:
        status_body = start_run(
            config=preview.config,
            manifest=preview.manifest,
            plan=preview.selected_plan,
            suite_metadata=preview.suite_metadata,
            browser_psu_prompts=True,
            launch_test_data_values=preview.run_plan.test_data.values,
        )
    except ValueError as error:
        return render(
            request,
            "conformance/plan_builder.html",
            _plan_context(form, launch_error=f"Launch validation failed: {error}"),
            status=400,
        )
    except RunConflictError as error:
        return render(
            request,
            "conformance/plan_builder.html",
            _plan_context(
                form,
                launch_error=f"A run is already active: {error.active_run_id}",
                active_run_id=error.active_run_id,
            ),
            status=409,
        )

    run_id = status_body["id"]
    assert isinstance(run_id, str)  # noqa: S101 - lifecycle response contract
    return redirect("ui-run-detail", run_id=run_id)


@require_GET
def run_detail(request: HttpRequest, run_id: str) -> HttpResponse:
    """Render the browser detail page for a run.

    Args:
        request: The incoming browser GET request.
        run_id: The unique run identifier from the URL path.

    Returns:
        HTML response with run status, log, and result panels, or 404 when
        the run is unknown.
    """
    record = run_store.get_run(run_id)
    if record is None:
        return HttpResponseNotFound("Run not found")
    return render(request, "conformance/run_detail.html", _run_context(record))


@require_GET
def run_status_partial(request: HttpRequest, run_id: str) -> HttpResponse:
    """Render the run status panel partial.

    Args:
        request: The incoming browser GET request.
        run_id: The unique run identifier from the URL path.

    Returns:
        HTML partial with the current run status, or 404 when the run is
        unknown.
    """
    record = run_store.get_run(run_id)
    if record is None:
        return HttpResponseNotFound("Run not found")
    return render(request, "conformance/partials/run_status.html", _run_context(record))


@require_GET
def run_steps_partial(request: HttpRequest, run_id: str) -> HttpResponse:
    """Render the selected-step progress panel partial.

    Args:
        request: The incoming browser GET request.
        run_id: The unique run identifier from the URL path.

    Returns:
        HTML partial with selected-step progress rows, or 404 when the run
        is unknown.
    """
    record = run_store.get_run(run_id)
    if record is None:
        return HttpResponseNotFound("Run not found")
    return render(request, "conformance/partials/run_steps.html", _run_context(record))


@require_GET
def run_log_partial(request: HttpRequest, run_id: str) -> HttpResponse:
    """Render the run log panel partial.

    Args:
        request: The incoming browser GET request.
        run_id: The unique run identifier from the URL path.

    Returns:
        HTML partial containing a masked log download link and event count,
        or 404 when the run is unknown.
    """
    record = run_store.get_run(run_id)
    if record is None:
        return HttpResponseNotFound("Run not found")
    return render(request, "conformance/partials/run_log.html", _run_context(record))


@require_GET
def run_result_partial(request: HttpRequest, run_id: str) -> HttpResponse:
    """Render the run result panel partial.

    Args:
        request: The incoming browser GET request.
        run_id: The unique run identifier from the URL path.

    Returns:
        HTML partial with result summary information and masked report link,
        or 404 when the run is unknown.
    """
    record = run_store.get_run(run_id)
    if record is None:
        return HttpResponseNotFound("Run not found")
    return render(request, "conformance/partials/run_result.html", _run_context(record))


@require_GET
def run_wait(request: HttpRequest, run_id: str) -> HttpResponse:
    """Block until a run changes or the wait timeout expires.

    Args:
        request: The incoming browser GET request.
        run_id: The unique run identifier from the URL path.

    Returns:
        JSON response with the updated run status after a state change, a
        204 response when the wait times out, or 404 when the run is unknown.
    """
    record = run_store.get_run(run_id)
    if record is None:
        return HttpResponseNotFound("Run not found")

    waited_record = run_store.wait_for_run_change(
        run_id,
        timeout_seconds=_run_wait_timeout_seconds(request),
        since_version=_run_wait_since_version(request),
    )
    if waited_record is None:
        return HttpResponse(status=204)
    return JsonResponse({"changed": True, "run": waited_record.to_status_json()})


@require_GET
def run_log_download(request: HttpRequest, run_id: str) -> HttpResponse:
    """Return the browser-accessible masked JSON execution log.

    Args:
        request: The incoming browser GET request.
        run_id: The unique run identifier from the URL path.

    Returns:
        ``application/json`` response for known runs, 404 for unknown
        runs, or 500 when the run exists without an attached log buffer.
    """
    record = run_store.get_run(run_id)
    if record is None:
        return HttpResponseNotFound("Run not found")
    if record.execution_logger is None:
        return JsonResponse({"error": "Execution log unavailable for this run"}, status=500)
    response = HttpResponse(record.execution_logger.to_json_bytes(), content_type="application/json")
    response["Content-Disposition"] = f'attachment; filename="{record.run_id}-execution-log.json"'
    return response


@require_GET
def run_result_download(request: HttpRequest, run_id: str) -> JsonResponse:
    """Return the browser-accessible masked JSON result for a run.

    Args:
        request: The incoming browser GET request.
        run_id: The unique run identifier from the URL path.

    Returns:
        JSON response containing the completed result, or an error response
        when the run is unknown, incomplete, failed, or missing its result.
    """
    record = run_store.get_run(run_id)
    if record is None:
        return JsonResponse({"error": "Run not found"}, status=404)
    if record.status in ("pending", "running"):
        return JsonResponse({"error": "Run has not completed yet", "status": record.status}, status=409)
    if record.status == "failed":
        return JsonResponse({"error": "Run failed internally"}, status=500)
    if record.result is None:
        return JsonResponse({"error": "Run result unavailable"}, status=500)
    response = JsonResponse(record.result)
    response["Content-Disposition"] = f'attachment; filename="{record.run_id}-result.json"'
    return response


def _plan_context(
    form: PlanBuilderForm,
    *,
    launch_error: str | None = None,
    active_run_id: str | None = None,
) -> dict[str, object]:
    """Build template context for plan-builder views.

    Args:
        form: Bound or unbound plan-builder form.
        launch_error: Optional launch failure message to display.
        active_run_id: Optional active run id supplied for conflict links.

    Returns:
        Template context containing the form, preview, counts, and launch
        failure details.
    """
    preview = form.preview
    context: dict[str, object] = {"form": form, "preview": preview, **guided_flow_context(form)}
    if preview is not None:
        context["preview_counts"] = preview_step_counts(preview)
    context["rows_by_id"] = {row.id: row for row in preview.rows} if preview else {}
    if launch_error is not None:
        context["launch_error"] = launch_error
    if active_run_id is not None:
        context["active_run_id"] = active_run_id
    return context


def _run_context(record: RunRecord) -> dict[str, object]:
    """Build template context for run detail and partial views.

    Args:
        record: Snapshot of the run record to render.

    Returns:
        Template context containing status, raw endpoint URLs, log metadata,
        and summarised result fields.
    """
    step_progress = _step_progress_rows(record)
    is_active_run = record.status in {"pending", "running"}
    has_pending_psu_authorisation_action = _has_pending_psu_authorisation_action(record)
    pending_psu_authorisation_deadline = _pending_psu_authorisation_deadline(record)
    return {
        "run": record,
        "run_times": {
            "created_at": _run_time_display(record.created_at),
            "started_at": _run_time_display(record.started_at),
            "finished_at": _run_time_display(record.finished_at),
        },
        "status_url": reverse("ui-run-status", kwargs={"run_id": record.run_id}),
        "steps_partial_url": reverse("ui-run-steps", kwargs={"run_id": record.run_id}),
        "log_partial_url": reverse("ui-run-log", kwargs={"run_id": record.run_id}),
        "result_partial_url": reverse("ui-run-result", kwargs={"run_id": record.run_id}),
        "raw_log_url": reverse("ui-run-log-download", kwargs={"run_id": record.run_id}),
        "raw_result_url": reverse("ui-run-result-download", kwargs={"run_id": record.run_id}),
        "participant_action_wait_url": reverse("ui-run-wait", kwargs={"run_id": record.run_id}),
        "log_event_count": _log_event_count(record),
        "result_summary": _result_summary(record.result),
        "plan_summary": _plan_summary(record.result),
        "certification_eligibility": _certification_eligibility(record.result),
        "step_progress": step_progress,
        "step_progress_counts": _step_progress_counts(step_progress),
        "result_issue_count": _result_issue_count(step_progress),
        "result_status_counts": _result_status_counts(step_progress),
        "custom_test_value_impact": _custom_test_value_impact(record.result),
        "developer_mode": _run_developer_mode(record),
        "page_auto_refresh_enabled": is_active_run,
        "live_polling_enabled": is_active_run and not has_pending_psu_authorisation_action,
        "participant_action_deadline": _run_time_display(pending_psu_authorisation_deadline),
    }


def _run_wait_timeout_seconds(request: HttpRequest) -> float:
    """Parse the browser wait timeout and clamp it to the server maximum.

    Args:
        request: The incoming browser GET request.

    Returns:
        Non-negative timeout seconds for the wait endpoint, capped at the
        module-level maximum wait duration.
    """
    raw_timeout = request.GET.get("timeout")
    if raw_timeout is None:
        return _RUN_WAIT_MAX_TIMEOUT_SECONDS
    try:
        requested_timeout = float(raw_timeout)
    except TypeError, ValueError:
        return _RUN_WAIT_MAX_TIMEOUT_SECONDS
    return min(max(requested_timeout, 0.0), _RUN_WAIT_MAX_TIMEOUT_SECONDS)


def _run_wait_since_version(request: HttpRequest) -> int | None:
    """Parse the browser-rendered run version from the wait request.

    Args:
        request: The incoming browser GET request.

    Returns:
        Non-negative run version supplied by the caller, or ``None`` when the
        parameter is missing or malformed.
    """
    raw_version = request.GET.get("since")
    if raw_version is None:
        return None
    try:
        version = int(raw_version)
    except ValueError:
        return None
    if version < 0:
        return None
    return version


def _has_pending_psu_authorisation_action(record: RunRecord) -> bool:
    """Return whether the run is waiting on a PSU authorisation action.

    Args:
        record: Snapshot of the run record to inspect.

    Returns:
        ``True`` when at least one pending participant action has type
        ``psu-authorization-url``; otherwise ``False``.
    """
    return any(
        action.type == "psu-authorization-url" and action.status == "pending"
        for action in record.participant_actions.values()
    )


def _pending_psu_authorisation_deadline(record: RunRecord) -> datetime | None:
    """Return the earliest deadline among pending PSU authorisation actions.

    Args:
        record: Snapshot of the run record to inspect.

    Returns:
        Earliest pending PSU action deadline, or ``None`` when no pending
        action carries timeout metadata.
    """
    pending_deadlines = [
        action.expires_at
        for action in record.participant_actions.values()
        if action.type == "psu-authorization-url" and action.status == "pending" and action.expires_at is not None
    ]
    if not pending_deadlines:
        return None
    return min(pending_deadlines)


def _run_time_display(timestamp: datetime | None) -> dict[str, str] | None:
    """Build timestamp fields for server-rendered UI fallback and JS enhancement.

    Args:
        timestamp: A run lifecycle timestamp from :class:`RunRecord`, or
            ``None`` when that lifecycle point has not been reached.

    Returns:
        A dictionary containing local display text plus canonical ISO strings
        for ``datetime``, ``data-utc-datetime``, and ``title`` attributes, or
        ``None`` when ``timestamp`` is absent.
    """
    if timestamp is None:
        return None

    canonical_iso = timestamp.isoformat()
    display_local = timestamp.astimezone(_UI_DISPLAY_TIME_ZONE)
    return {
        "display": display_local.strftime("%d/%m/%Y %H:%M:%S %Z"),
        "datetime": canonical_iso,
        "utc_datetime": canonical_iso,
        "title": canonical_iso,
    }


def _step_progress_rows(record: RunRecord) -> list[dict[str, object]]:
    """Build selected-step progress rows for live run rendering.

    Args:
        record: Snapshot of the run record to render.

    Returns:
        Ordered list of selected planned-step rows with launch metadata,
        current lifecycle status, and masked event evidence summaries.
    """
    rows: list[dict[str, object]] = []
    row_by_step_id: dict[str, dict[str, object]] = {}
    for planned_step in sorted(record.planned_steps, key=lambda step: step.order):
        row: dict[str, object] = {
            "step_id": planned_step.step_id,
            "name": planned_step.name,
            "kind": planned_step.kind,
            "group": planned_step.group,
            "phase": planned_step.phase,
            "mandatory": planned_step.mandatory,
            "optional": planned_step.optional,
            "order": planned_step.order,
            "status": "pending",
            "message": "",
            "status_code": None,
            "started_at": None,
            "completed_at": None,
            "awaiting_authorisation": False,
            "evidence_events": [],
            # Final-result fields — populated by _reconcile_step_progress_with_result
            "url": None,
            "request_method": None,
            "request_url": None,
            "response_status_code": None,
            "assertion_summaries": [],
            "issues": [],
            "request_json": None,
            "request_json_preview": None,
            "response_json": None,
            "response_json_preview": None,
            "remaining_details_json": None,
            "remaining_details_json_preview": None,
            "custom_test_value_impact_references": [],
            "custom_test_value_impact_values": [],
            "custom_test_value_impact_count": 0,
        }
        rows.append(row)
        row_by_step_id[planned_step.step_id] = row

    if not rows:
        return []

    logger_events = record.execution_logger.events() if record.execution_logger is not None else []
    for event in logger_events:
        if event.step_id is None:
            continue
        event_row = row_by_step_id.get(event.step_id)
        if event_row is None:
            continue
        _apply_progress_event_to_row(event_row, event)

    for participant_action in record.participant_actions.values():
        action_row = row_by_step_id.get(participant_action.step_id)
        if action_row is None:
            continue
        if participant_action.status != "pending":
            continue
        action_row["awaiting_authorisation"] = True
        row_status = action_row.get("status")
        if isinstance(row_status, str) and row_status in {"pending", "running"}:
            action_row["status"] = "awaiting"
            if not action_row["message"]:
                action_row["message"] = "Waiting for PSU authorisation callback"

    _reconcile_step_progress_with_result(rows, record.result)
    return rows


def _apply_progress_event_to_row(row: dict[str, object], event: ExecutionEvent) -> None:
    """Update a progress row from one execution-log event.

    Args:
        row: Mutable selected-step progress row.
        event: Structured execution-log event for the same step.
    """
    event_payload: JsonObject = event.payload if isinstance(event.payload, dict) else {}

    if event.type == "step-started":
        row["started_at"] = event.timestamp
        if row["status"] == "pending":
            row["status"] = "running"
    elif event.type == "step-completed":
        completion_status = event_payload.get("status")
        row["status"] = completion_status if isinstance(completion_status, str) and completion_status else "completed"
        completion_message = event_payload.get("message")
        if isinstance(completion_message, str):
            row["message"] = completion_message
        completion_status_code = event_payload.get("statusCode")
        if isinstance(completion_status_code, int):
            row["status_code"] = completion_status_code
        row["completed_at"] = event.timestamp

    event_evidence = _progress_event_evidence(event)
    if event_evidence is None:
        return
    evidence_events = row["evidence_events"]
    if isinstance(evidence_events, list):
        evidence_events.append(event_evidence)


def _progress_event_evidence(event: ExecutionEvent) -> dict[str, object] | None:
    """Build a template-friendly masked evidence summary for one event.

    Args:
        event: Structured execution-log event linked to a selected step.

    Returns:
        Evidence dictionary for supported event types, or ``None`` when the
        event type is omitted from progress evidence.
    """
    payload = event.payload if isinstance(event.payload, dict) else {}
    event_type = event.type
    if event_type not in {
        "request-sent",
        "response-received",
        "assertion-evaluated",
        "placeholder-error",
        "application-error",
        "psu-authorization-url",
        "step-completed",
    }:
        return None

    payload_json = _pretty_json(payload)
    return {
        "timestamp": event.timestamp,
        "type": event_type,
        "summary": _progress_event_summary(event_type=event_type, payload=payload),
        "payload_json": payload_json,
        "payload_json_preview": _json_preview(payload_json),
    }


def _progress_event_summary(*, event_type: str, payload: JsonObject) -> str:
    """Return a one-line summary for a step evidence event.

    Args:
        event_type: Execution-log event type to summarise.
        payload: Masked event payload for ``event_type``.

    Returns:
        Human-readable evidence summary for UI rendering.
    """
    if event_type == "request-sent":
        method = payload.get("method")
        url = payload.get("url")
        parts = ["Request sent"]
        if isinstance(method, str):
            parts.append(method)
        if isinstance(url, str):
            parts.append(url)
        return " - ".join(parts)

    if event_type == "response-received":
        status_code = payload.get("statusCode")
        url = payload.get("url")
        if isinstance(status_code, int) and isinstance(url, str):
            return f"Response received ({status_code}) - {url}"
        if isinstance(status_code, int):
            return f"Response received ({status_code})"
        return "Response received"

    if event_type == "assertion-evaluated":
        assertion_status = payload.get("status")
        assertion_message = payload.get("message")
        if isinstance(assertion_status, str) and isinstance(assertion_message, str):
            return f"Assertion {assertion_status}: {assertion_message}"
        if isinstance(assertion_status, str):
            return f"Assertion {assertion_status}"
        return "Assertion evaluated"

    if event_type in {"placeholder-error", "application-error"}:
        message = payload.get("message")
        if isinstance(message, str) and message:
            label = "Placeholder error" if event_type == "placeholder-error" else "Application error"
            return f"{label}: {message}"
        return "Placeholder error" if event_type == "placeholder-error" else "Application error"

    if event_type == "psu-authorization-url":
        mode = payload.get("mode")
        if isinstance(mode, str) and mode:
            return f"PSU authorisation URL emitted ({mode})"
        return "PSU authorisation URL emitted"

    if event_type == "step-completed":
        completion_status = payload.get("status")
        completion_message = payload.get("message")
        if isinstance(completion_status, str) and isinstance(completion_message, str):
            return f"Step completed ({completion_status}): {completion_message}"
        if isinstance(completion_status, str):
            return f"Step completed ({completion_status})"
        return "Step completed"

    return event_type


def _result_step_details(raw_step: JsonObject) -> dict[str, object]:
    """Extract final-result display fields from a raw result step dict.

    Args:
        raw_step: A single step entry from the completed run result JSON.

    Returns:
        Dictionary of template-friendly fields covering the step URL,
        request/response summaries, assertion summaries, failed/warn issues,
        pretty-printed JSON blocks, and compact preview strings for each
        JSON block.
    """
    details = raw_step.get("details")
    step_details: JsonObject = details if isinstance(details, dict) else {}

    assertions_raw = step_details.get("assertions")
    assertion_summaries: list[dict[str, str]] = []
    if isinstance(assertions_raw, list):
        for assertion in assertions_raw:
            if not isinstance(assertion, dict):
                continue
            a_status = assertion.get("status")
            a_message = assertion.get("message")
            if isinstance(a_status, str) and isinstance(a_message, str):
                assertion_summaries.append({"status": a_status, "message": a_message})

    request_evidence = step_details.get("request")
    response_evidence = step_details.get("response")
    request_dict = request_evidence if isinstance(request_evidence, dict) else None
    response_dict = response_evidence if isinstance(response_evidence, dict) else None

    request_method = request_dict.get("method") if request_dict is not None else None
    request_url = request_dict.get("url") if request_dict is not None else None
    response_status_code = response_dict.get("statusCode") if response_dict is not None else None
    if not isinstance(request_method, str):
        request_method = None
    if not isinstance(request_url, str):
        request_url = None
    if not isinstance(response_status_code, int):
        response_status_code = None

    request_json = _pretty_json(request_dict)
    response_json = _pretty_json(response_dict)
    remaining_details_json = _pretty_json(_remaining_step_details(step_details))

    step_url = raw_step.get("url")
    return {
        "url": step_url if isinstance(step_url, str) else None,
        "request_method": request_method,
        "request_url": request_url,
        "response_status_code": response_status_code,
        "assertion_summaries": assertion_summaries,
        "issues": _step_issues(step_details),
        "request_json": request_json,
        "request_json_preview": _json_preview(request_json),
        "response_json": response_json,
        "response_json_preview": _json_preview(response_json),
        "remaining_details_json": remaining_details_json,
        "remaining_details_json_preview": _json_preview(remaining_details_json),
    }


def _reconcile_step_progress_with_result(rows: list[dict[str, object]], result: JsonObject | None) -> None:
    """Reconcile live rows with canonical final result step outcomes.

    Args:
        rows: Mutable selected-step progress rows.
        result: Completed run result JSON, or ``None`` for active runs.

    Notes:
        Result steps are matched by ``name`` against planned ``step_id``.
        Only existing selected rows are updated; unmatched result steps are
        intentionally ignored to preserve selected-steps-only rendering.
    """
    if result is None:
        return

    raw_steps = result.get("steps")
    if not isinstance(raw_steps, list):
        return

    result_by_name: dict[str, JsonObject] = {}
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            continue
        name = raw_step.get("name")
        if isinstance(name, str):
            result_by_name[name] = raw_step
    impact_by_step = _executed_custom_test_value_impact_by_step(result)

    for row in rows:
        step_id = row.get("step_id")
        if not isinstance(step_id, str):
            continue
        raw_step = result_by_name.get(step_id)
        if raw_step is None:
            continue

        result_status = raw_step.get("status")
        if isinstance(result_status, str) and result_status:
            row["status"] = result_status

        result_message = raw_step.get("message")
        if isinstance(result_message, str):
            row["message"] = result_message

        result_details = _result_step_details(raw_step)
        row.update(result_details)
        impact_for_step = impact_by_step.get(step_id, {})
        references = impact_for_step.get("references")
        value_entries = impact_for_step.get("value_entries")
        reference_count = impact_for_step.get("reference_count")
        row["custom_test_value_impact_references"] = references if isinstance(references, list) else []
        row["custom_test_value_impact_values"] = value_entries if isinstance(value_entries, list) else []
        row["custom_test_value_impact_count"] = reference_count if isinstance(reference_count, int) else 0

        response_status_code = result_details.get("response_status_code")
        if isinstance(response_status_code, int):
            row["status_code"] = response_status_code


def _step_progress_counts(step_progress: list[dict[str, object]]) -> dict[str, int]:
    """Count aggregate progress-state totals across selected planned steps.

    Args:
        step_progress: Selected-step progress rows returned by
            :func:`_step_progress_rows`.

    Returns:
        Counts for selected-step totals and status buckets used by run-detail
        progress metrics.
    """
    counts = {
        "total": len(step_progress),
        "pending": 0,
        "running_or_awaiting": 0,
        "passed": 0,
        "failed": 0,
        "warn": 0,
        "skipped": 0,
        "completed": 0,
    }

    for row in step_progress:
        status = row.get("status")
        if status == "pending":
            counts["pending"] += 1
            continue
        if status in {"running", "awaiting"}:
            counts["running_or_awaiting"] += 1
            continue
        if status == "passed":
            counts["passed"] += 1
            counts["completed"] += 1
            continue
        if status == "failed":
            counts["failed"] += 1
            counts["completed"] += 1
            continue
        if status in {"warn", "warning"}:
            counts["warn"] += 1
            counts["completed"] += 1
            continue
        if status == "skipped":
            counts["skipped"] += 1
            counts["completed"] += 1
            continue
        if status == "completed":
            counts["completed"] += 1

    return counts


def _result_steps(result: JsonObject | None) -> list[dict[str, object]]:
    """Build template-friendly display rows for per-step result details.

    Args:
        result: Structured run result JSON, or ``None`` before completion.

    Returns:
        Ordered list of step display dictionaries with summary fields,
        assertion summaries, failed/warn issues, and pretty JSON blocks for
        request/response/remaining details.
    """
    if result is None:
        return []
    raw_steps = result.get("steps")
    if not isinstance(raw_steps, list):
        return []

    rendered_steps: list[dict[str, object]] = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            continue
        details = raw_step.get("details")
        step_details = details if isinstance(details, dict) else {}

        assertions_raw = step_details.get("assertions")
        assertion_summaries: list[dict[str, str]] = []
        if isinstance(assertions_raw, list):
            for assertion in assertions_raw:
                if not isinstance(assertion, dict):
                    continue
                status = assertion.get("status")
                message = assertion.get("message")
                if isinstance(status, str) and isinstance(message, str):
                    assertion_summaries.append({"status": status, "message": message})

        request_evidence = step_details.get("request")
        response_evidence = step_details.get("response")
        request_dict = request_evidence if isinstance(request_evidence, dict) else None
        response_dict = response_evidence if isinstance(response_evidence, dict) else None

        request_method = request_dict.get("method") if request_dict is not None else None
        request_url = request_dict.get("url") if request_dict is not None else None
        response_status_code = response_dict.get("statusCode") if response_dict is not None else None
        if not isinstance(request_method, str):
            request_method = None
        if not isinstance(request_url, str):
            request_url = None
        if not isinstance(response_status_code, int):
            response_status_code = None

        request_json = _pretty_json(request_dict)
        response_json = _pretty_json(response_dict)
        remaining_details_json = _pretty_json(_remaining_step_details(step_details))

        rendered_steps.append(
            {
                "name": raw_step.get("name") if isinstance(raw_step.get("name"), str) else "-",
                "status": raw_step.get("status") if isinstance(raw_step.get("status"), str) else "-",
                "message": raw_step.get("message") if isinstance(raw_step.get("message"), str) else "",
                "url": raw_step.get("url") if isinstance(raw_step.get("url"), str) else None,
                "request_method": request_method,
                "request_url": request_url,
                "response_status_code": response_status_code,
                "assertion_summaries": assertion_summaries,
                "issues": _step_issues(step_details),
                "request_json": request_json,
                "request_json_preview": _json_preview(request_json),
                "response_json": response_json,
                "response_json_preview": _json_preview(response_json),
                "remaining_details_json": remaining_details_json,
                "remaining_details_json_preview": _json_preview(remaining_details_json),
            }
        )
    return rendered_steps


def _step_issues(step_details: JsonObject) -> list[dict[str, str]]:
    """Extract failed/warn issue entries from a step details object.

    Args:
        step_details: ``details`` object from a step entry in result JSON.

    Returns:
        Ordered issue dictionaries with ``status`` and ``message`` keys,
        including failed assertions, warning assertions, and step-level
        warning strings.
    """
    issues: list[dict[str, str]] = []
    assertions_raw = step_details.get("assertions")
    if isinstance(assertions_raw, list):
        for assertion in assertions_raw:
            if not isinstance(assertion, dict):
                continue
            status = assertion.get("status")
            message = assertion.get("message")
            if not isinstance(status, str) or not isinstance(message, str):
                continue
            if status in {"failed", "warn"}:
                issues.append({"status": status, "message": message})

    warning = step_details.get("warning")
    if isinstance(warning, str) and warning:
        issues.append({"status": "warn", "message": warning})
    return issues


def _pretty_json(value: JsonValue | None) -> str | None:
    """Render a JSON value as deterministic pretty-printed text.

    Args:
        value: JSON-compatible value to render.

    Returns:
        Indented JSON string when ``value`` is not ``None``; otherwise
        ``None``.
    """
    if value is None:
        return None
    return json.dumps(value, indent=2, sort_keys=True)


def _json_preview(value: str | None, *, line_count: int = 9) -> str | None:
    """Return a compact preview for a pretty-printed JSON payload.

    Args:
        value: Pretty-printed JSON text to truncate for collapsed display.
        line_count: Maximum number of lines to include in the preview.

    Returns:
        The first ``line_count`` lines of ``value``, or ``None`` when the
        input is ``None``.
    """
    if value is None:
        return None
    return "\n".join(value.splitlines()[:line_count])


def _remaining_step_details(step_details: JsonObject) -> JsonObject | None:
    """Return non-evidence detail keys for optional raw JSON fallback display.

    Args:
        step_details: ``details`` object from a step entry in result JSON.

    Returns:
        Dictionary containing keys other than ``request``, ``response``, and
        ``assertions``, or ``None`` when no remaining keys exist.
    """
    remaining: JsonObject = {
        key: value for key, value in step_details.items() if key not in {"request", "response", "assertions"}
    }
    if not remaining:
        return None
    return remaining


def _run_developer_mode(record: RunRecord) -> bool:
    """Return whether the run was captured in developer-unmasked mode.

    Args:
        record: Snapshot of the run record being rendered.

    Returns:
        True when the attached execution logger bypassed masking for this
        run; otherwise False.
    """
    if record.execution_logger is None:
        return False
    return record.execution_logger.developer_mode


def _result_status_counts(step_progress: list[dict[str, object]]) -> dict[str, int]:
    """Count terminal per-status outcomes from reconciled progress rows.

    Args:
        step_progress: Selected-step progress rows returned by
            :func:`_step_progress_rows`, including final result reconciliation
            when the run is completed.

    Returns:
        Mapping of known status keys (``passed``, ``failed``, ``warn``,
        ``skipped``) to integer counts.
    """
    counts = {"passed": 0, "failed": 0, "warn": 0, "skipped": 0}
    for step in step_progress:
        status = step.get("status")
        if isinstance(status, str) and status in counts:
            counts[status] += 1
    return counts


def _result_issue_count(step_progress: list[dict[str, object]]) -> int:
    """Count failed/warn issue entries across reconciled progress rows.

    Args:
        step_progress: Selected-step progress rows returned by
            :func:`_step_progress_rows`, including final result reconciliation
            when the run is completed.

    Returns:
        Total issue entry count across all rows.
    """
    total = 0
    for step in step_progress:
        issues = step.get("issues")
        if isinstance(issues, list):
            total += len(issues)
    return total


def _log_event_count(record: RunRecord) -> int:
    """Count currently buffered execution-log events for a run.

    Args:
        record: Snapshot of the run record whose attached logger is shared
            with the live record.

    Returns:
        Number of execution-log events currently available.
    """
    if record.execution_logger is None:
        return 0
    return len(record.execution_logger.events())


def _result_summary(result: JsonObject | None) -> JsonObject | None:
    """Extract the result summary object from completed run JSON.

    Args:
        result: Structured run result JSON, or ``None`` before completion.

    Returns:
        The ``summary`` object when present, otherwise ``None``.
    """
    if result is None:
        return None
    summary = result.get("summary")
    if isinstance(summary, dict):
        return summary
    return None


def _plan_summary(result: JsonObject | None) -> JsonObject | None:
    """Extract the plan summary object from completed run JSON.

    Args:
        result: Structured run result JSON, or ``None`` before completion.

    Returns:
        The ``plan`` object when present, otherwise ``None``.
    """
    if result is None:
        return None
    plan = result.get("plan")
    if isinstance(plan, dict):
        return plan
    return None


def _certification_eligibility(result: JsonObject | None) -> str | None:
    """Extract a certification eligibility label from completed result JSON.

    Args:
        result: Structured run result JSON, or ``None`` before completion.

    Returns:
        Certification eligibility label when present, otherwise ``None``.
    """
    if result is None:
        return None
    value = result.get("certificationEligibility")
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        eligible = value.get("eligible")
        if eligible is True:
            return "eligible"
        if eligible is False:
            reason = value.get("reason")
            if isinstance(reason, str) and reason:
                return f"ineligible: {reason}"
            return "ineligible"
    return None


_IMPACT_SURFACE_LABEL: dict[str, str] = {
    "request-json-body": "Body",
    "request-header": "Headers",
    "request-url": "URL",
    "request-form-body": "Form body",
}
"""Display labels for request surfaces in result custom-value panels."""

_IMPACT_SURFACE_ORDER: tuple[str, ...] = (
    "request-json-body",
    "request-header",
    "request-url",
    "request-form-body",
)
"""Display order for request surfaces in result custom-value panels."""

_IMPACT_SURFACE_PREFIX: dict[str, str] = {
    "request-json-body": "request.body.",
    "request-header": "request.headers.",
    "request-url": "request.",
    "request-form-body": "request.body.fields.",
}
"""Field-path prefixes removed before rendering request-surface tree labels."""


def _custom_test_value_impact(result: JsonObject | None) -> dict[str, object] | None:
    """Extract top-level custom-test-value impact evidence for result templates.

    Handles both the new baseline-delta shape (``baselineDeltaKeys``,
    ``baselineDeltaKeyCount``, ``source: "custom"``) and the legacy
    override-key shape (``overrideKeys``, ``overrideKeyCount``).

    Args:
        result: Structured run result JSON, or ``None`` before completion.

    Returns:
        Template-ready impact dictionary, or ``None`` when no persisted impact
        evidence is available.
    """
    if result is None:
        return None
    impact = result.get("customTestValueImpact")
    if not isinstance(impact, dict):
        return None

    def _summary_count(key: str) -> int:
        """Read one integer summary count from the impact summary object.

        Args:
            key: Summary field name to parse.

        Returns:
            Parsed integer count, or ``0`` when the field is absent or invalid.
        """
        raw_value = summary.get(key) if isinstance(summary, dict) else None
        return raw_value if isinstance(raw_value, int) else 0

    summary = impact.get("summary")
    baseline_delta_key_count = _summary_count("baselineDeltaKeyCount") or _summary_count("overrideKeyCount")
    summary_block: dict[str, object]
    if isinstance(summary, dict):
        summary_block = {
            "baselineDeltaKeyCount": baseline_delta_key_count,
            "overrideKeyCount": baseline_delta_key_count,
            "executedReferenceCount": _summary_count("executedReferenceCount"),
            "referencedButNotRunCount": _summary_count("referencedButNotRunCount"),
            "executedStepCount": _summary_count("executedStepCount"),
            "referencedButNotRunStepCount": _summary_count("referencedButNotRunStepCount"),
        }
    else:
        summary_block = {
            "baselineDeltaKeyCount": 0,
            "overrideKeyCount": 0,
            "executedReferenceCount": 0,
            "referencedButNotRunCount": 0,
            "executedStepCount": 0,
            "referencedButNotRunStepCount": 0,
        }

    executed_references = _impact_executed_references(impact)
    references_by_key: dict[str, list[dict[str, str]]] = {}
    for reference in executed_references:
        references_by_key.setdefault(reference["key"], []).append(reference)
    value_entries = _impact_value_entries(impact=impact, references_by_key=references_by_key)

    source = impact.get("source")
    delta_keys = impact.get("baselineDeltaKeys") or impact.get("overrideKeys")
    overridden_values = impact.get("overriddenValues")
    referenced_but_not_run = impact.get("referencedButNotRun")
    return {
        "source": source if isinstance(source, str) else None,
        "delta_keys": delta_keys if isinstance(delta_keys, list) else [],
        "overridden_values": overridden_values if isinstance(overridden_values, dict) else {},
        "referenced_but_not_run": referenced_but_not_run if isinstance(referenced_but_not_run, list) else [],
        "summary": summary_block,
        "value_entries": value_entries,
    }


def _impact_executed_references(impact: JsonObject) -> list[dict[str, str]]:
    """Parse executed custom-value references from impact evidence.

    Args:
        impact: Persisted ``customTestValueImpact`` evidence object.

    Returns:
        Normalised references list with snake_case keys used by templates.
    """
    raw_references = impact.get("executedReferences")
    if not isinstance(raw_references, list):
        return []
    parsed: list[dict[str, str]] = []
    for raw_reference in raw_references:
        if not isinstance(raw_reference, dict):
            continue
        step_id = raw_reference.get("stepId")
        key = raw_reference.get("key")
        request_area = raw_reference.get("requestArea")
        field_path = raw_reference.get("fieldPath")
        status = raw_reference.get("status")
        if not isinstance(step_id, str):
            continue
        if not isinstance(key, str) or not isinstance(request_area, str) or not isinstance(field_path, str):
            continue
        parsed.append(
            {
                "step_id": step_id,
                "key": key,
                "request_area": request_area,
                "field_path": field_path,
                "status": status if isinstance(status, str) else "",
            }
        )
    return parsed


def _impact_value_entries(
    *,
    impact: JsonObject,
    references_by_key: dict[str, list[dict[str, str]]],
) -> list[dict[str, object]]:
    """Build run-level custom-value entries for result rendering.

    Args:
        impact: Persisted ``customTestValueImpact`` evidence object.
        references_by_key: Parsed executed references grouped by custom-value key.

    Returns:
        Per-key entries containing used/baseline values and request-surface trees.
    """
    entries: list[dict[str, object]] = []
    raw_value_details = impact.get("valueDetails")
    if isinstance(raw_value_details, list):
        for raw_detail in raw_value_details:
            if not isinstance(raw_detail, dict):
                continue
            key = raw_detail.get("key")
            if not isinstance(key, str):
                continue
            references = references_by_key.get(key, [])
            entries.append(
                _build_impact_value_entry(
                    key=key,
                    used_value=_impact_value_for_display(raw_detail, "usedValue", "customValue", "effectiveValue"),
                    baseline_value=_impact_value_for_display(raw_detail, "baselineValue", "defaultValue", "baseline"),
                    used_value_display=_impact_value_display(
                        raw_detail,
                        display_fields=("usedValueDisplay", "customValueDisplay", "effectiveValueDisplay"),
                        value_fields=("usedValue", "customValue", "effectiveValue"),
                    ),
                    baseline_value_display=_impact_value_display(
                        raw_detail,
                        display_fields=("baselineValueDisplay", "defaultValueDisplay", "baselineDisplay"),
                        value_fields=("baselineValue", "defaultValue", "baseline"),
                    ),
                    references=references,
                )
            )
        if entries:
            return sorted(entries, key=lambda entry: cast(str, entry["key"]))

    overridden_values = impact.get("overriddenValues")
    if not isinstance(overridden_values, dict):
        return []
    for key in sorted(overridden_values.keys()):
        raw_value = overridden_values.get(key)
        if not isinstance(key, str) or not isinstance(raw_value, dict):
            continue
        references = references_by_key.get(key, [])
        entries.append(
            _build_impact_value_entry(
                key=key,
                used_value=_impact_value_for_display(raw_value, "customValue", "effectiveValue"),
                baseline_value=_impact_value_for_display(raw_value, "defaultValue", "baseline"),
                used_value_display=_impact_value_display(
                    raw_value,
                    display_fields=("customValueDisplay", "effectiveValueDisplay"),
                    value_fields=("customValue", "effectiveValue"),
                ),
                baseline_value_display=_impact_value_display(
                    raw_value,
                    display_fields=("defaultValueDisplay", "baselineDisplay"),
                    value_fields=("defaultValue", "baseline"),
                ),
                references=references,
            )
        )
    return entries


def _impact_value_display(
    raw_value: Mapping[str, object],
    *,
    display_fields: tuple[str, ...],
    value_fields: tuple[str, ...],
) -> dict[str, object] | None:
    """Read a structured custom-value display object with legacy fallbacks.

    Args:
        raw_value: Persisted impact value object containing display fields.
        display_fields: Candidate structured display field names checked in order.
        value_fields: Candidate legacy string fields used to build fallback.

    Returns:
        Normalised display dictionary containing ``preview`` and optional
        ``full_value`` when present; ``None`` when no usable value exists.
    """
    for field_name in display_fields:
        candidate = raw_value.get(field_name)
        if not isinstance(candidate, Mapping):
            continue
        preview = candidate.get("preview")
        full_value = candidate.get("fullValue")
        masked = candidate.get("masked")
        display: dict[str, object] = {
            "preview": preview if isinstance(preview, str) else None,
            "full_value": full_value if isinstance(full_value, str) else None,
            "masked": masked if isinstance(masked, bool) else None,
        }
        if any(display[field] is not None for field in ("preview", "full_value", "masked")):
            return display
    fallback_value = _impact_value_for_display(raw_value, *value_fields)
    if fallback_value is None:
        return None
    return {
        "preview": fallback_value,
        "full_value": None,
        "masked": fallback_value == "***",
    }


def _impact_value_for_display(raw_value: Mapping[str, object], *candidate_fields: str) -> str | None:
    """Read the first string value from candidate impact fields.

    Args:
        raw_value: Value-detail or overridden-value object from impact evidence.
        *candidate_fields: Field names checked in order.

    Returns:
        The first string value found, otherwise ``None``.
    """
    for field_name in candidate_fields:
        candidate = raw_value.get(field_name)
        if isinstance(candidate, str):
            return candidate
    return None


def _build_impact_value_entry(
    *,
    key: str,
    used_value: str | None,
    baseline_value: str | None,
    used_value_display: dict[str, object] | None,
    baseline_value_display: dict[str, object] | None,
    references: list[dict[str, str]],
) -> dict[str, object]:
    """Build one template-ready run-level custom-value entry.

    Args:
        key: Custom-value key.
        used_value: Masked value used by the run when available.
        baseline_value: Masked suite-baseline value when available.
        used_value_display: Structured display object for the used value.
        baseline_value_display: Structured display object for the baseline value.
        references: Executed references for this key.

    Returns:
        Dictionary rendered by the run-level and per-step templates.
    """
    unique_references = _dedupe_references(references)
    return {
        "key": key,
        "used_value": used_value,
        "baseline_value": baseline_value,
        "used_value_display": used_value_display,
        "baseline_value_display": baseline_value_display,
        "references": unique_references,
        "consuming_paths": sorted({ref["field_path"] for ref in unique_references}),
        "surface_groups": _request_surface_groups(unique_references),
    }


def _dedupe_references(references: list[dict[str, str]]) -> list[dict[str, str]]:
    """Drop duplicate reference rows while preserving deterministic order.

    Args:
        references: Parsed executed references for one custom-value key.

    Returns:
        Deduplicated references sorted by step id and request path.
    """
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, str]] = []
    for reference in sorted(references, key=lambda ref: (ref["step_id"], ref["field_path"], ref["request_area"])):
        fingerprint = (reference["step_id"], reference["request_area"], reference["field_path"])
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        deduped.append(reference)
    return deduped


def _request_surface_groups(references: list[dict[str, str]]) -> list[dict[str, object]]:
    """Build request-surface tree groups for one custom-value key.

    Args:
        references: Deduplicated executed references for one key.

    Returns:
        Surface groups with ``rows`` payloads rendered by result templates.
    """
    references_by_surface: dict[str, list[dict[str, str]]] = {}
    for reference in references:
        references_by_surface.setdefault(reference["request_area"], []).append(reference)

    groups: list[dict[str, object]] = []
    for request_area in _IMPACT_SURFACE_ORDER:
        surface_references = references_by_surface.pop(request_area, [])
        if not surface_references:
            continue
        groups.append(
            {
                "request_area": request_area,
                "label": _IMPACT_SURFACE_LABEL.get(request_area, request_area),
                "rows": _request_surface_rows(request_area=request_area, references=surface_references),
            }
        )

    for request_area in sorted(references_by_surface.keys()):
        surface_references = references_by_surface[request_area]
        groups.append(
            {
                "request_area": request_area,
                "label": _IMPACT_SURFACE_LABEL.get(request_area, request_area),
                "rows": _request_surface_rows(request_area=request_area, references=surface_references),
            }
        )
    return groups


def _request_surface_rows(*, request_area: str, references: list[dict[str, str]]) -> list[dict[str, object]]:
    """Build flat tree rows for one request surface.

    Args:
        request_area: Request-surface identifier (for example ``request-json-body``).
        references: References targeting this surface.

    Returns:
        Flat rows with depth metadata for template rendering.
    """
    if request_area == "request-json-body":
        trie: dict[str, object] = {}
        for reference in references:
            stripped_path = _strip_impact_surface_prefix(
                request_area=request_area,
                field_path=reference["field_path"],
            )
            _insert_impact_path(trie=trie, segments=[segment for segment in stripped_path.split(".") if segment])
        return _impact_trie_rows(trie=trie, depth=0)
    rows: list[dict[str, object]] = []
    for reference in sorted(references, key=lambda entry: entry["field_path"]):
        rows.append(
            {
                "row_type": "leaf",
                "depth": 0,
                "label": _strip_impact_surface_prefix(
                    request_area=request_area,
                    field_path=reference["field_path"],
                ),
            }
        )
    return rows


def _strip_impact_surface_prefix(*, request_area: str, field_path: str) -> str:
    """Remove a known request-surface prefix from a reference field path.

    Args:
        request_area: Request-surface identifier from impact evidence.
        field_path: Full field path from impact evidence.

    Returns:
        Field path without the request-surface prefix when present.
    """
    prefix = _IMPACT_SURFACE_PREFIX.get(request_area, "")
    if prefix and field_path.startswith(prefix):
        return field_path[len(prefix) :]
    return field_path


def _insert_impact_path(*, trie: dict[str, object], segments: list[str]) -> None:
    """Insert one dotted request-body path into a mutable trie.

    Args:
        trie: Mutable trie root keyed by path segment.
        segments: Body-path segments for one reference.
    """
    node = trie
    for segment in segments:
        child = node.get(segment)
        if not isinstance(child, dict):
            child = {}
            node[segment] = child
        node = child


def _impact_trie_rows(*, trie: dict[str, object], depth: int) -> list[dict[str, object]]:
    """Convert a request-body trie into depth-aware template rows.

    Args:
        trie: Current trie node to traverse.
        depth: Depth for rows emitted at this level.

    Returns:
        Flat list of group/leaf rows for body-path tree rendering.
    """
    rows: list[dict[str, object]] = []
    for label in sorted(trie.keys()):
        raw_child = trie[label]
        child = raw_child if isinstance(raw_child, dict) else {}
        has_children = bool(child)
        rows.append(
            {
                "row_type": "group" if has_children else "leaf",
                "depth": depth,
                "label": label,
            }
        )
        if has_children:
            rows.extend(_impact_trie_rows(trie=child, depth=depth + 1))
    return rows


def _executed_custom_test_value_impact_by_step(result: JsonObject | None) -> dict[str, dict[str, object]]:
    """Index executed custom-test-value impact references and values by step id.

    Args:
        result: Structured run result JSON, or ``None`` before completion.

    Returns:
        Mapping from step id to dictionaries containing legacy reference lists,
        count metrics, and value entries filtered to the step.
    """
    if result is None:
        return {}
    impact = result.get("customTestValueImpact")
    if not isinstance(impact, dict):
        return {}

    executed_references = _impact_executed_references(impact)
    references_by_step: dict[str, list[dict[str, str]]] = {}
    references_by_key: dict[str, list[dict[str, str]]] = {}
    for reference in executed_references:
        references_by_step.setdefault(reference["step_id"], []).append(reference)
        references_by_key.setdefault(reference["key"], []).append(reference)

    value_entries = _impact_value_entries(impact=impact, references_by_key=references_by_key)
    value_entry_by_key: dict[str, dict[str, object]] = {
        cast(str, entry["key"]): entry for entry in value_entries if isinstance(entry.get("key"), str)
    }

    rendered: dict[str, dict[str, object]] = {}
    for step_id, step_references in references_by_step.items():
        step_reference_rows = [
            {
                "key": reference["key"],
                "request_area": reference["request_area"],
                "field_path": reference["field_path"],
                "status": reference["status"],
            }
            for reference in step_references
        ]
        step_value_entries: list[dict[str, object]] = []
        keys_for_step = sorted({reference["key"] for reference in step_references})
        for key in keys_for_step:
            base_entry = value_entry_by_key.get(key)
            if base_entry is None:
                continue
            key_refs = [reference for reference in step_references if reference["key"] == key]
            step_value_entries.append(
                {
                    "key": key,
                    "used_value": base_entry.get("used_value"),
                    "baseline_value": base_entry.get("baseline_value"),
                    "used_value_display": base_entry.get("used_value_display"),
                    "baseline_value_display": base_entry.get("baseline_value_display"),
                    "references": _dedupe_references(key_refs),
                    "surface_groups": _request_surface_groups(_dedupe_references(key_refs)),
                }
            )
        rendered[step_id] = {
            "references": step_reference_rows,
            "value_entries": step_value_entries,
            "reference_count": len(step_reference_rows),
        }
    return rendered


def preview_step_counts(preview: PlanPreview) -> dict[str, int]:
    """Return aggregate counts for a plan preview.

    Args:
        preview: Validated participant plan preview.

    Returns:
        Counts used by templates to summarise selected, optional, and
        certification-impacting steps.
    """
    return {
        "total": len(preview.rows),
        "selected": len(preview.selected_plan.selected_step_ids()),
        "optional": sum(1 for row in preview.rows if row.optional),
        "mandatory": sum(1 for row in preview.rows if row.mandatory),
        "mandatory_deselected": len(preview.selected_plan.deselected_mandatory_step_ids()),
    }
