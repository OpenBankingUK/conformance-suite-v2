"""Browser views for participant-facing plan builder and run monitoring."""

from __future__ import annotations

import json

from django.http import HttpRequest, HttpResponse, HttpResponseNotFound, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from conformance.api.plan_builder import PlanBuilderForm, PlanPreview, guided_flow_context
from conformance.api.run_lifecycle import start_run
from conformance.api.run_store import RunConflictError, RunRecord, run_store
from conformance.json_types import JsonObject, JsonValue


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
    result_steps = _result_steps(record.result)
    return {
        "run": record,
        "status_url": reverse("ui-run-status", kwargs={"run_id": record.run_id}),
        "log_partial_url": reverse("ui-run-log", kwargs={"run_id": record.run_id}),
        "result_partial_url": reverse("ui-run-result", kwargs={"run_id": record.run_id}),
        "raw_log_url": reverse("ui-run-log-download", kwargs={"run_id": record.run_id}),
        "raw_result_url": reverse("ui-run-result-download", kwargs={"run_id": record.run_id}),
        "log_event_count": _log_event_count(record),
        "result_summary": _result_summary(record.result),
        "plan_summary": _plan_summary(record.result),
        "certification_eligibility": _certification_eligibility(record.result),
        "result_steps": result_steps,
        "result_issue_count": _result_issue_count(result_steps),
        "result_status_counts": _result_status_counts(result_steps),
        "developer_mode": _run_developer_mode(record),
    }


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
                "request_json": _pretty_json(request_dict),
                "response_json": _pretty_json(response_dict),
                "remaining_details_json": _pretty_json(_remaining_step_details(step_details)),
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


def _result_status_counts(result_steps: list[dict[str, object]]) -> dict[str, int]:
    """Count per-status outcomes from rendered step rows.

    Args:
        result_steps: Template-friendly step rows returned by
            :func:`_result_steps`.

    Returns:
        Mapping of known status keys (``passed``, ``failed``, ``warn``,
        ``skipped``) to integer counts.
    """
    counts = {"passed": 0, "failed": 0, "warn": 0, "skipped": 0}
    for step in result_steps:
        status = step.get("status")
        if isinstance(status, str) and status in counts:
            counts[status] += 1
    return counts


def _result_issue_count(result_steps: list[dict[str, object]]) -> int:
    """Count failed/warn issue entries across all rendered step rows.

    Args:
        result_steps: Template-friendly step rows returned by
            :func:`_result_steps`.

    Returns:
        Total issue entry count across all rows.
    """
    total = 0
    for step in result_steps:
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
