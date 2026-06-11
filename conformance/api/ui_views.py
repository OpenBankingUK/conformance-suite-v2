"""Browser views for participant-facing plan builder and run monitoring."""

from __future__ import annotations

import json
from datetime import datetime
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
        "log_event_count": _log_event_count(record),
        "result_summary": _result_summary(record.result),
        "plan_summary": _plan_summary(record.result),
        "certification_eligibility": _certification_eligibility(record.result),
        "step_progress": step_progress,
        "step_progress_counts": _step_progress_counts(step_progress),
        "result_issue_count": _result_issue_count(step_progress),
        "result_status_counts": _result_status_counts(step_progress),
        "developer_mode": _run_developer_mode(record),
    }


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
        "display": display_local.strftime("%Y-%m-%d %H:%M:%S %Z"),
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
