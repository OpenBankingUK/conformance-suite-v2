"""Browser views for participant-facing plan builder and run monitoring."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from django.http import HttpRequest, HttpResponse, HttpResponseNotFound, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from conformance.api.builder_draft_store import BuilderDraft, SessionBuilderDraftStore
from conformance.api.builder_wizard import (
    BusinessConfigForm,
    CatalogueBoundaryForm,
    DiscoveryConfigForm,
    RuntimeInputsConfigForm,
    ScopeSelectionForm,
    SecurityConfigForm,
    WizardRuntimeInputPrompt,
    boundary_requires_resource_groups,
    business_config_form_initial,
    catalogue_boundary_continue_blocker,
    config_visibility_for_plan_document,
    discovery_config_form_initial,
    draft_scope_from_plan_document,
    endpoint_capability_values_from_mapping,
    merge_business_config,
    merge_discovery_config,
    merge_runtime_input_config,
    merge_security_config,
    missing_required_runtime_inputs,
    model_bank_config_from_plan_config,
    plan_document_from_draft,
    plan_document_to_export_json,
    plan_document_with_runtime_placeholders,
    runtime_input_prompts_for_plan_document,
    security_config_form_initial,
    security_field_metadata,
    specification_options,
    version_options,
)
from conformance.api.plan_review import PlanTestCaseRow, compiled_plan_rows
from conformance.api.run_lifecycle import start_run
from conformance.api.run_store import RunConflictError, RunRecord, run_store
from conformance.catalogue import (
    CatalogueError,
    CompiledTestPlan,
    PlanDocumentBoundary,
    PlanDocumentV2,
    compile_test_plan_document,
    parse_test_plan_document,
    plan_document_to_json_object,
)
from conformance.catalogue_registry import supported_catalogues
from conformance.execution_log import ExecutionEvent
from conformance.http import build_json_http_client
from conformance.json_types import JsonObject, JsonValue
from conformance.model_bank_config import ConfigError, parse_model_bank_config
from conformance.ozone_client import OzoneClientError, OzoneModelBankClient
from conformance.plan_configuration import parse_dcr_plan_configuration, validate_dcr_file_references
from conformance.test_plan_validation import (
    TestPlanValidationError,
    prepare_test_plan_for_run,
    validate_test_plan_for_load,
)

_UI_DISPLAY_TIME_ZONE = ZoneInfo("Europe/London")
"""Open Banking UK browser fallback timezone for server-rendered timestamps."""


@dataclass(frozen=True)
class _BuilderReviewState:
    """Computed review state for a browser builder draft.

    Attributes:
        document: Parsed canonical test-plan document built from the draft, when
            available.
        compiled_plan: Preview-compiled plan, using placeholders only for
            missing runtime inputs so generated-test rows remain inspectable.
        rows: Read-only generated test rows.
        runtime_prompts: Runtime input prompts derived from selected scope.
        missing_runtime_prompts: Required runtime prompts absent from the draft.
        blockers: Human-readable launch blockers.
        error: Non-recoverable review error, if the draft cannot be interpreted.
        safe_export_json: Secret-safe export text.
        sensitive_export_warning: Warning shown beside export-with-secrets.
    """

    document: PlanDocumentV2 | None
    compiled_plan: CompiledTestPlan | None
    rows: tuple[PlanTestCaseRow, ...]
    runtime_prompts: tuple[WizardRuntimeInputPrompt, ...]
    missing_runtime_prompts: tuple[WizardRuntimeInputPrompt, ...]
    blockers: tuple[str, ...]
    error: str | None
    safe_export_json: str
    sensitive_export_warning: str

    @property
    def launch_supported(self) -> bool:
        """Return whether the reviewed draft can be launched.

        Returns:
            True when the document compiles and has no launch blockers.
        """
        return self.document is not None and self.compiled_plan is not None and not self.blockers and self.error is None


@require_GET
def home(request: HttpRequest) -> HttpResponse:
    """Render the participant-facing browser main menu.

    Args:
        request: The incoming browser GET request.

    Returns:
        HTML response with entry points for builder creation, future import, and
        operational status.
    """
    return render(request, "conformance/home.html")


@require_POST
def builder_new(request: HttpRequest) -> HttpResponse:
    """Create a new wizard draft and redirect to the first builder step.

    Args:
        request: The incoming browser POST request.

    Returns:
        Redirect response to the scheme/specification/version wizard step.
    """
    draft = SessionBuilderDraftStore(request.session).create()
    return redirect("builder-catalogue-boundary", draft_id=draft.draft_id)


@require_http_methods(["GET", "POST"])
def builder_catalogue_boundary(request: HttpRequest, draft_id: str) -> HttpResponse:
    """Render or save the scheme/specification/version wizard step.

    Args:
        request: The incoming browser request.
        draft_id: Session-scoped draft id from the route.

    Returns:
        HTML response for the first wizard step, a redirect after a successful
        save, or ``404`` when the draft is not known to this browser session.
    """
    draft_store = SessionBuilderDraftStore(request.session)
    draft = draft_store.get(draft_id)
    if draft is None:
        return HttpResponseNotFound("Builder draft not found")

    if request.method == "POST":
        form = CatalogueBoundaryForm(data=request.POST, initial=_boundary_form_initial(draft))
        if form.is_valid():
            selected_boundary = PlanDocumentBoundary(
                scheme=cast(str, form.cleaned_data["scheme"]),
                specification=cast(str, form.cleaned_data["specification"]),
                version=cast(str, form.cleaned_data["version"]),
            )
            pruned_scope = ScopeSelectionForm(
                data={
                    "resource_groups": list(draft.resource_group_ids),
                    "endpoints": list(draft.endpoint_ids),
                    "endpoint_capabilities": list(
                        endpoint_capability_values_from_mapping(draft.endpoint_capability_ids)
                    ),
                },
                boundary=selected_boundary,
                prune_unavailable_choices=True,
            )
            pruned_scope.is_valid()
            updated_draft = draft.with_catalogue_boundary(
                scheme=selected_boundary.scheme,
                specification=selected_boundary.specification,
                version=selected_boundary.version,
            ).with_scope_selection(
                resource_group_ids=pruned_scope.selected_resource_group_ids,
                endpoint_ids=pruned_scope.selected_endpoint_ids,
                endpoint_capability_ids=pruned_scope.selected_endpoint_capability_ids,
            )
            draft_store.save(updated_draft)
            if catalogue_boundary_continue_blocker(selected_boundary) is not None:
                return render(
                    request,
                    "conformance/builder_catalogue_boundary.html",
                    _builder_catalogue_boundary_context(draft=updated_draft, form=form, saved=False),
                    status=400,
                )
            if not boundary_requires_resource_groups(selected_boundary):
                return redirect("builder-scope", draft_id=draft.draft_id)
            return redirect("builder-discovery-config", draft_id=draft.draft_id)
        return render(
            request,
            "conformance/builder_catalogue_boundary.html",
            _builder_catalogue_boundary_context(draft=draft, form=form, saved=False),
            status=400,
        )

    form = CatalogueBoundaryForm(initial=_boundary_form_initial(draft))
    return render(
        request,
        "conformance/builder_catalogue_boundary.html",
        _builder_catalogue_boundary_context(draft=draft, form=form, saved=request.GET.get("saved") == "1"),
    )


@require_http_methods(["GET", "POST"])
def builder_scope(request: HttpRequest, draft_id: str) -> HttpResponse:
    """Render or save the resource/endpoints/features wizard step.

    Args:
        request: The incoming browser request.
        draft_id: Session-scoped draft id from the route.

    Returns:
        HTML response for the second wizard step, a redirect after a successful
        save, or ``404`` when the draft is not known to this browser session.
    """
    draft_store = SessionBuilderDraftStore(request.session)
    draft = draft_store.get(draft_id)
    if draft is None:
        return HttpResponseNotFound("Builder draft not found")
    boundary = _draft_boundary(draft)
    if boundary is None:
        return redirect("builder-catalogue-boundary", draft_id=draft.draft_id)
    if catalogue_boundary_continue_blocker(boundary) is not None:
        return redirect("builder-catalogue-boundary", draft_id=draft.draft_id)

    if request.method == "POST":
        form = ScopeSelectionForm(data=request.POST, boundary=boundary, initial=_scope_form_initial(draft))
        if form.is_valid():
            updated_draft = draft.with_scope_selection(
                resource_group_ids=form.selected_resource_group_ids,
                endpoint_ids=form.selected_endpoint_ids,
                endpoint_capability_ids=form.selected_endpoint_capability_ids,
            )
            draft_store.save(updated_draft)
            if not boundary_requires_resource_groups(boundary):
                return redirect("builder-discovery-config", draft_id=draft.draft_id)
            return redirect("builder-config", draft_id=draft.draft_id)
        return render(
            request,
            "conformance/builder_scope.html",
            _builder_scope_context(draft=draft, form=form, saved=False),
            status=400,
        )

    form = ScopeSelectionForm(boundary=boundary, initial=_scope_form_initial(draft))
    return render(
        request,
        "conformance/builder_scope.html",
        _builder_scope_context(draft=draft, form=form, saved=request.GET.get("saved") == "1"),
    )


@require_POST
def builder_scope_options(request: HttpRequest, draft_id: str) -> HttpResponse:
    """Render the HTMX fragment for resource/endpoints/features choices.

    Args:
        request: The incoming HTMX POST request.
        draft_id: Session-scoped draft id from the route.

    Returns:
        Partial HTML response containing the filtered scope tree, or ``404``
        when the draft is not known to this browser session.
    """
    draft = SessionBuilderDraftStore(request.session).get(draft_id)
    if draft is None:
        return HttpResponseNotFound("Builder draft not found")
    boundary = _draft_boundary(draft)
    if boundary is None:
        return HttpResponseNotFound("Builder draft catalogue boundary not selected")

    form = ScopeSelectionForm(
        data=request.POST,
        boundary=boundary,
        initial=_scope_form_initial(draft),
        prune_unavailable_choices=True,
    )
    status = 200 if form.is_valid() else 400
    return render(
        request,
        "conformance/partials/builder_scope_options.html",
        _builder_scope_options_context(form=form),
        status=status,
    )


@require_http_methods(["GET", "POST"])
def builder_config(request: HttpRequest, draft_id: str) -> HttpResponse:
    """Render or save business defaults for a builder draft.

    Args:
        request: The incoming browser request.
        draft_id: Session-scoped draft id from the route.

    Returns:
        HTML response for the business config step, a redirect to discovery
        after save, or ``404`` when the draft is not known.
    """
    draft_store = SessionBuilderDraftStore(request.session)
    draft = draft_store.get(draft_id)
    if draft is None:
        return HttpResponseNotFound("Builder draft not found")
    if _draft_boundary(draft) is None:
        return redirect("builder-catalogue-boundary", draft_id=draft.draft_id)
    boundary = _draft_boundary(draft)
    if boundary is not None and not boundary_requires_resource_groups(boundary):
        return redirect("builder-discovery-config", draft_id=draft.draft_id)
    if not draft.resource_group_ids:
        return redirect("builder-scope", draft_id=draft.draft_id)

    try:
        config_visibility = config_visibility_for_plan_document(plan_document_from_draft(draft))
    except CatalogueError as error:
        return render(
            request,
            "conformance/builder_business_config.html",
            _builder_business_config_context(
                draft=draft,
                form=BusinessConfigForm(initial=business_config_form_initial(draft.config)),
                review_error=f"Scope validation failed: {error}",
            ),
            status=400,
        )

    if request.method == "POST":
        form = BusinessConfigForm(
            data=request.POST,
            initial=business_config_form_initial(draft.config),
            config_visibility=config_visibility,
        )
        if form.is_valid() and form.config is not None:
            draft_store.save(draft.with_config(config=merge_business_config(draft.config, form.config)))
            return redirect("builder-runtime-config", draft_id=draft.draft_id)
        return render(
            request,
            "conformance/builder_business_config.html",
            _builder_business_config_context(draft=draft, form=form),
            status=400,
        )

    form = BusinessConfigForm(
        initial=business_config_form_initial(draft.config),
        config_visibility=config_visibility,
    )
    return render(
        request,
        "conformance/builder_business_config.html",
        _builder_business_config_context(draft=draft, form=form),
    )


@require_http_methods(["GET", "POST"])
def builder_discovery_config(request: HttpRequest, draft_id: str) -> HttpResponse:
    """Render or save environment and discovery settings for a draft.

    Args:
        request: The incoming browser request.
        draft_id: Session-scoped draft id from the route.

    Returns:
        HTML response for the discovery config step, a redirect to the security
        step after save, or ``404`` when the draft is not known.
    """
    draft_store = SessionBuilderDraftStore(request.session)
    draft = draft_store.get(draft_id)
    if draft is None:
        return HttpResponseNotFound("Builder draft not found")
    if _draft_boundary(draft) is None:
        return redirect("builder-catalogue-boundary", draft_id=draft.draft_id)

    if request.method == "POST":
        form = DiscoveryConfigForm(
            data=request.POST,
            initial=discovery_config_form_initial(draft.config),
            discovery_required=_is_dcr_draft(draft),
        )
        if form.is_valid() and form.config is not None:
            updated_config = merge_discovery_config(draft.config, form.config)
            metadata = (
                _fetch_discovery_metadata(updated_config) if _metadata_string(updated_config, "discoveryUrl") else {}
            )
            draft_store.save(
                draft.with_config(config=updated_config).with_discovery_metadata(discovery_metadata=metadata)
            )
            return redirect("builder-security-config", draft_id=draft.draft_id)
        return render(
            request,
            "conformance/builder_discovery_config.html",
            _builder_discovery_config_context(draft=draft, form=form),
            status=400,
        )

    form = DiscoveryConfigForm(
        initial=discovery_config_form_initial(draft.config),
        discovery_required=_is_dcr_draft(draft),
    )
    return render(
        request,
        "conformance/builder_discovery_config.html",
        _builder_discovery_config_context(draft=draft, form=form),
    )


@require_http_methods(["GET", "POST"])
def builder_security_config(request: HttpRequest, draft_id: str) -> HttpResponse:
    """Render or save OAuth/FAPI/security settings for a draft.

    Args:
        request: The incoming browser request.
        draft_id: Session-scoped draft id from the route.

    Returns:
        HTML response for the security config step, a redirect to runtime
        inputs after save, or ``404`` when the draft is not known.
    """
    draft_store = SessionBuilderDraftStore(request.session)
    draft = draft_store.get(draft_id)
    if draft is None:
        return HttpResponseNotFound("Builder draft not found")
    if _draft_boundary(draft) is None:
        return redirect("builder-catalogue-boundary", draft_id=draft.draft_id)
    if request.method == "POST":
        form = SecurityConfigForm(
            data=request.POST,
            initial=security_config_form_initial(
                draft.config,
                draft.discovery_metadata,
                security_environment=draft.security_environment,
                dynamic_client_registration=draft.dynamic_client_registration,
                metadata=draft.metadata,
                execution_mode=draft.execution_mode,
            ),
            dcr_mode=_is_dcr_draft(draft),
        )
        if form.is_valid() and form.config is not None:
            updated_config = merge_security_config(draft.config, form.config)
            validation_error = None if _is_dcr_draft(draft) else _validate_model_config(updated_config)
            if validation_error is None:
                updated_draft = draft.with_config(config=updated_config)
                if _is_dcr_draft(draft):
                    updated_draft = updated_draft.with_plan_context(
                        security_environment=form.security_environment or {},
                        business_test_data=draft.business_test_data,
                        metadata=form.metadata or {},
                        execution_mode=form.execution_mode or draft.execution_mode,
                        dynamic_client_registration=form.dynamic_client_registration or {},
                    )
                draft_store.save(updated_draft)
                destination = "builder-review" if _is_dcr_draft(draft) else "builder-scope"
                return redirect(destination, draft_id=draft.draft_id)
            form.add_error(None, validation_error)
        return render(
            request,
            "conformance/builder_security_config.html",
            _builder_security_config_context(draft=draft, form=form),
            status=400,
        )

    form = SecurityConfigForm(
        initial=security_config_form_initial(
            draft.config,
            draft.discovery_metadata,
            security_environment=draft.security_environment,
            dynamic_client_registration=draft.dynamic_client_registration,
            metadata=draft.metadata,
            execution_mode=draft.execution_mode,
        ),
        dcr_mode=_is_dcr_draft(draft),
    )
    return render(
        request,
        "conformance/builder_security_config.html",
        _builder_security_config_context(draft=draft, form=form),
    )


@require_http_methods(["GET", "POST"])
def builder_runtime_config(request: HttpRequest, draft_id: str) -> HttpResponse:
    """Render or save catalogue-generated runtime inputs for a draft.

    Args:
        request: The incoming browser request.
        draft_id: Session-scoped draft id from the route.

    Returns:
        HTML response for the runtime input step, a redirect to review after
        save, or ``404`` when the draft is not known.
    """
    draft_store = SessionBuilderDraftStore(request.session)
    draft = draft_store.get(draft_id)
    if draft is None:
        return HttpResponseNotFound("Builder draft not found")
    if _draft_boundary(draft) is None:
        return redirect("builder-catalogue-boundary", draft_id=draft.draft_id)

    try:
        runtime_prompts = runtime_input_prompts_for_plan_document(plan_document_from_draft(draft))
    except CatalogueError as error:
        return render(
            request,
            "conformance/builder_runtime_config.html",
            _builder_runtime_config_context(
                draft=draft,
                form=RuntimeInputsConfigForm(runtime_prompts=()),
                review_error=f"Scope validation failed: {error}",
            ),
            status=400,
        )

    if request.method == "POST":
        form = RuntimeInputsConfigForm(data=request.POST, runtime_prompts=runtime_prompts)
        if form.is_valid() and form.config is not None:
            draft_store.save(draft.with_config(config=merge_runtime_input_config(draft.config, form.config)))
            return redirect("builder-review", draft_id=draft.draft_id)
        return render(
            request,
            "conformance/builder_runtime_config.html",
            _builder_runtime_config_context(draft=draft, form=form),
            status=400,
        )

    form = RuntimeInputsConfigForm(runtime_prompts=runtime_prompts)
    return render(
        request,
        "conformance/builder_runtime_config.html",
        _builder_runtime_config_context(draft=draft, form=form),
    )


@require_http_methods(["GET", "POST"])
def builder_import(request: HttpRequest) -> HttpResponse:
    """Render or process the browser v2 test-plan import flow.

    Args:
        request: The incoming browser request.

    Returns:
        HTML import page, validation errors, or a redirect to the imported
        draft review page.
    """
    if request.method == "GET":
        return render(request, "conformance/builder_import.html", {"plan_json": "", "import_error": None})

    raw_plan_json = request.POST.get("plan_json", "")
    try:
        raw_document = json.loads(raw_plan_json)
    except json.JSONDecodeError as error:
        return render(
            request,
            "conformance/builder_import.html",
            {"plan_json": raw_plan_json, "import_error": f"Plan JSON must be valid JSON: {error.msg}"},
            status=400,
        )
    try:
        validation_result = validate_test_plan_for_load(raw_document)
        if not validation_result.valid:
            raise CatalogueError(validation_result.summary_message())
        parsed_document = parse_test_plan_document(raw_document)
        if not isinstance(parsed_document, PlanDocumentV2) or parsed_document.schema_version != "1.0":
            raise CatalogueError("Browser import accepts schemaVersion 1.0 test plans only")
        runtime_input_prompts_for_plan_document(parsed_document)
    except CatalogueError as error:
        return render(
            request,
            "conformance/builder_import.html",
            {"plan_json": raw_plan_json, "import_error": f"Plan validation failed: {error}"},
            status=400,
        )

    draft_store = SessionBuilderDraftStore(request.session)
    draft = draft_store.create()
    resource_group_ids, endpoint_ids, capability_ids = draft_scope_from_plan_document(parsed_document)
    imported_draft = (
        draft.with_catalogue_boundary(
            scheme=parsed_document.scheme,
            specification=parsed_document.specification,
            version=parsed_document.version,
        )
        .with_scope_selection(
            resource_group_ids=resource_group_ids,
            endpoint_ids=endpoint_ids,
            endpoint_capability_ids=capability_ids,
        )
        .with_config(config=parsed_document.config)
        .with_plan_context(
            security_environment=parsed_document.security_environment,
            business_test_data=parsed_document.business_test_data,
            metadata=parsed_document.metadata,
            execution_mode=parsed_document.execution_mode,
            dynamic_client_registration=parsed_document.dynamic_client_registration,
        )
    )
    draft_store.save(imported_draft)
    return redirect("builder-review", draft_id=draft.draft_id)


@require_GET
def builder_review(request: HttpRequest, draft_id: str) -> HttpResponse:
    """Render the generated read-only review page for a builder draft.

    Args:
        request: The incoming browser GET request.
        draft_id: Session-scoped draft id from the route.

    Returns:
        HTML review page or ``404`` when the draft is not known.
    """
    draft = SessionBuilderDraftStore(request.session).get(draft_id)
    if draft is None:
        return HttpResponseNotFound("Builder draft not found")
    return render(request, "conformance/builder_review.html", _builder_review_context(draft=draft))


@require_http_methods(["GET", "POST"])
def builder_export(request: HttpRequest, draft_id: str) -> HttpResponse:
    """Download a reviewed builder draft as v2 plan JSON.

    Args:
        request: The incoming browser GET or POST request.
        draft_id: Session-scoped draft id from the route.

    Returns:
        JSON attachment containing a safe GET export by default, a secret-bearing
        POST export when explicitly requested, or an error response.
    """
    draft = SessionBuilderDraftStore(request.session).get(draft_id)
    if draft is None:
        return HttpResponseNotFound("Builder draft not found")
    state = _builder_review_state(draft)
    if state.document is None or state.compiled_plan is None:
        return JsonResponse({"error": state.error or "Builder draft cannot be exported"}, status=400)
    if request.method == "GET" and request.GET.get("include_secrets") == "1":
        return JsonResponse({"error": "Secret exports require POST"}, status=405)
    include_secrets = request.method == "POST" and request.POST.get("include_secrets") == "1"
    exported = plan_document_to_export_json(
        state.document,
        sensitive_runtime_input_ids=_sensitive_runtime_input_ids(state.compiled_plan),
        include_secrets=include_secrets,
    )
    response = HttpResponse(
        json.dumps(exported, indent=2, sort_keys=True),
        content_type="application/json",
    )
    suffix = "with-secrets" if include_secrets else "safe"
    response["Content-Disposition"] = f'attachment; filename="test-plan-{draft.draft_id}-{suffix}.json"'
    response["Cache-Control"] = "no-store"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


@require_POST
def builder_launch(request: HttpRequest, draft_id: str) -> HttpResponse:
    """Launch a conformance run from the reviewed builder draft.

    Args:
        request: The incoming browser POST request.
        draft_id: Session-scoped draft id from the route.

    Returns:
        Redirect to run detail on success, or the review page with launch
        blockers/conflict details.
    """
    draft = SessionBuilderDraftStore(request.session).get(draft_id)
    if draft is None:
        return HttpResponseNotFound("Builder draft not found")
    state = _builder_review_state(draft)
    if not state.launch_supported or state.document is None:
        return render(
            request,
            "conformance/builder_review.html",
            _builder_review_context(draft=draft, launch_error="Resolve review blockers before launching."),
            status=400,
        )

    try:
        prepared = prepare_test_plan_for_run(plan_document_to_json_object(state.document), base_dir=Path.cwd())
        status_body = start_run(
            config=prepared.config,
            compiled_plan=prepared.compiled_plan,
            runtime_inputs=prepared.runtime_inputs,
            runtime_input_base_dir=Path.cwd(),
            browser_psu_prompts=True,
            plan_snapshot=prepared.snapshot,
            validation_result=prepared.validation.to_json_object(),
        )
    except (CatalogueError, ConfigError, TestPlanValidationError) as error:
        return render(
            request,
            "conformance/builder_review.html",
            _builder_review_context(draft=draft, launch_error=f"Launch validation failed: {error}"),
            status=400,
        )
    except RunConflictError as error:
        return render(
            request,
            "conformance/builder_review.html",
            _builder_review_context(
                draft=draft,
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


def _boundary_form_initial(draft: BuilderDraft) -> dict[str, object]:
    """Return initial selector values from a builder draft.

    Args:
        draft: Current browser wizard draft.

    Returns:
        Initial form values for the scheme/specification/version step.
    """
    initial: dict[str, object] = {}
    if draft.scheme is not None:
        initial["scheme"] = draft.scheme
    if draft.specification is not None:
        initial["specification"] = draft.specification
    if draft.version is not None:
        initial["version"] = draft.version
    initial["resource_groups"] = list(draft.resource_group_ids)
    return initial


def _draft_boundary(draft: BuilderDraft) -> PlanDocumentBoundary | None:
    """Return the selected catalogue boundary from a draft.

    Args:
        draft: Current browser wizard draft.

    Returns:
        Selected plan-document boundary, or ``None`` until step one is saved.
    """
    if draft.scheme is None or draft.specification is None or draft.version is None:
        return None
    return PlanDocumentBoundary(scheme=draft.scheme, specification=draft.specification, version=draft.version)


def _is_dcr_draft(draft: BuilderDraft) -> bool:
    """Return whether a browser draft targets Open Banking DCR 3.4.

    Args:
        draft: Current browser builder draft.

    Returns:
        True for the DCR specification boundary.
    """
    return draft.specification == "dynamic-client-registration" and draft.version == "3.4"


def _scope_form_initial(draft: BuilderDraft) -> dict[str, object]:
    """Return initial scope form values from a builder draft.

    Args:
        draft: Current browser wizard draft.

    Returns:
        Initial form values for resource groups, endpoints, and capabilities.
    """
    return {
        "resource_groups": list(draft.resource_group_ids),
        "endpoints": list(draft.endpoint_ids),
        "endpoint_capabilities": list(endpoint_capability_values_from_mapping(draft.endpoint_capability_ids)),
    }


def _builder_catalogue_boundary_context(
    *,
    draft: BuilderDraft,
    form: CatalogueBoundaryForm,
    saved: bool,
) -> dict[str, object]:
    """Build template context for the first wizard step.

    Args:
        draft: Current browser wizard draft.
        form: Boundary selection form to render.
        saved: Whether to show the successful-save confirmation message.

    Returns:
        Template context for the scheme/specification/version wizard page.
    """
    context = {
        "draft": draft,
        "form": form,
        "saved": saved,
        "specification_options": specification_options(),
        "version_options": version_options(),
        "catalogue_boundary_blocker": catalogue_boundary_continue_blocker(form.selected_boundary),
    }
    return context


def _builder_scope_context(
    *,
    draft: BuilderDraft,
    form: ScopeSelectionForm,
    saved: bool,
) -> dict[str, object]:
    """Build template context for the resource/endpoints/features step.

    Args:
        draft: Current browser wizard draft.
        form: Scope selection form to render.
        saved: Whether to show the successful-save confirmation message.

    Returns:
        Template context for the second wizard step.
    """
    context = _builder_scope_options_context(form=form)
    context.update({"draft": draft, "saved": saved})
    return context


def _builder_scope_options_context(form: ScopeSelectionForm) -> dict[str, object]:
    """Build template context for the scope-tree fragment.

    Args:
        form: Scope selection form whose hierarchy should be rendered.

    Returns:
        Partial-template context for the current scope selections.
    """
    return {
        "form": form,
        "hierarchy": form.hierarchy,
        "selected_resource_groups": tuple(group for group in form.hierarchy.resource_groups if group.selected),
        "direct_endpoints": form.hierarchy.direct_endpoints,
    }


def _builder_business_config_context(
    *,
    draft: BuilderDraft,
    form: BusinessConfigForm,
    review_error: str | None = None,
) -> dict[str, object]:
    """Build template context for business/request defaults.

    Args:
        draft: Current browser wizard draft.
        form: Business config form.
        review_error: Optional scope/config validation error to render.

    Returns:
        Template context for the business config wizard page.
    """
    context: dict[str, object] = {
        "draft": draft,
        "form": form,
        "config_visibility": form.config_visibility,
    }
    if review_error is not None:
        context["review_error"] = review_error
    return context


def _builder_discovery_config_context(
    *,
    draft: BuilderDraft,
    form: DiscoveryConfigForm,
) -> dict[str, object]:
    """Build template context for discovery config.

    Args:
        draft: Current browser wizard draft.
        form: Discovery config form.

    Returns:
        Template context for the discovery config wizard page.
    """
    return {
        "draft": draft,
        "form": form,
        "discovery_metadata": _discovery_metadata_context(draft.discovery_metadata),
    }


def _builder_security_config_context(
    *,
    draft: BuilderDraft,
    form: SecurityConfigForm,
) -> dict[str, object]:
    """Build template context for OAuth/FAPI/security config.

    Args:
        draft: Current browser wizard draft.
        form: Security config form.

    Returns:
        Template context for the security config wizard page.
    """
    return {
        "draft": draft,
        "form": form,
        "discovery_metadata": _discovery_metadata_context(draft.discovery_metadata),
        "security_requirements": security_field_metadata(),
        "dcr_mode": _is_dcr_draft(draft),
    }


def _builder_runtime_config_context(
    *,
    draft: BuilderDraft,
    form: RuntimeInputsConfigForm,
    review_error: str | None = None,
) -> dict[str, object]:
    """Build template context for runtime input config.

    Args:
        draft: Current browser wizard draft.
        form: Runtime inputs form.
        review_error: Optional scope/config validation error to render.

    Returns:
        Template context for the runtime input config wizard page.
    """
    context: dict[str, object] = {
        "draft": draft,
        "form": form,
        "runtime_prompt_groups": _runtime_prompt_group_context(form),
    }
    if review_error is not None:
        context["review_error"] = review_error
    return context


def _runtime_prompt_group_context(form: RuntimeInputsConfigForm) -> list[dict[str, object]]:
    """Return template-ready runtime prompt groups.

    Args:
        form: Runtime inputs form containing dynamic runtime input fields.

    Returns:
        List of group dictionaries with bound fields for rendering.
    """
    groups: list[dict[str, object]] = []
    for group in form.runtime_prompt_groups:
        groups.append(
            {
                "label": group.label,
                "prompts": [{"prompt": prompt, "field": form[prompt.name]} for prompt in group.prompts],
            }
        )
    return groups


def _discovery_metadata_context(discovery_metadata: Mapping[str, JsonValue]) -> dict[str, object]:
    """Return template-ready discovery metadata values.

    Args:
        discovery_metadata: Session-only discovery metadata object.

    Returns:
        Metadata display context with warning and field rows.
    """
    labels = {
        "issuer": "Issuer",
        "authorization_endpoint": "Authorization endpoint",
        "token_endpoint": "Token endpoint",
        "jwks_uri": "JWKS URI",
        "token_endpoint_auth_methods_supported": "Token endpoint auth methods supported",
        "token_endpoint_auth_signing_alg_values_supported": "Token endpoint auth signing algorithms supported",
        "response_types_supported": "Response types supported",
        "request_object_signing_alg_values_supported": "Request object signing algorithms supported",
    }
    fields = []
    for key, label in labels.items():
        value = discovery_metadata.get(key)
        if value is None:
            continue
        fields.append({"label": label, "value": _display_metadata_value(value)})
    return {
        "fetch_error": _metadata_string(discovery_metadata, "fetchError"),
        "source_url": _metadata_string(discovery_metadata, "sourceUrl"),
        "fields": fields,
    }


def _fetch_discovery_metadata(config: Mapping[str, JsonValue]) -> JsonObject:
    """Fetch non-secret OpenID discovery metadata for later form defaults.

    Args:
        config: Draft config containing ``discoveryUrl``.

    Returns:
        Metadata values safe to keep in the browser session. Failures are
        returned as ``fetchError`` so the next page can allow manual entry.
    """
    discovery_url = config.get("discoveryUrl")
    if not isinstance(discovery_url, str) or not discovery_url.strip():
        return {}
    client: OzoneModelBankClient | None = None
    try:
        client = OzoneModelBankClient(build_json_http_client())
        discovery_document, response = client.fetch_discovery_document(discovery_url.strip())
    except (OzoneClientError, ValueError) as error:
        return {"fetchError": str(error), "sourceUrl": discovery_url.strip()}
    finally:
        if client is not None:
            client.close()
    metadata = _normalised_discovery_metadata(discovery_document.raw)
    metadata["sourceUrl"] = response.url
    metadata["statusCode"] = response.status_code
    return metadata


def _normalised_discovery_metadata(raw_metadata: Mapping[str, JsonValue]) -> JsonObject:
    """Return the discovery metadata fields used by guided config.

    Args:
        raw_metadata: Raw discovery document body.

    Returns:
        Non-secret discovery fields safe to display and store in the session.
    """
    metadata: JsonObject = {}
    for key in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
        value = raw_metadata.get(key)
        if isinstance(value, str) and value.strip():
            metadata[key] = value.strip()
    for key in (
        "token_endpoint_auth_methods_supported",
        "token_endpoint_auth_signing_alg_values_supported",
        "response_types_supported",
        "request_object_signing_alg_values_supported",
    ):
        values = _string_list_metadata_value(raw_metadata.get(key))
        if values:
            metadata[key] = values
    return metadata


def _string_list_metadata_value(value: JsonValue | None) -> list[JsonValue]:
    """Return a discovery metadata list containing only strings.

    Args:
        value: Candidate discovery metadata value.

    Returns:
        String list safe for JSON session storage.
    """
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _metadata_string(discovery_metadata: Mapping[str, JsonValue], key: str) -> str:
    """Return a string discovery metadata value.

    Args:
        discovery_metadata: Session-only discovery metadata object.
        key: Metadata key to read.

    Returns:
        String value or an empty string.
    """
    value = discovery_metadata.get(key)
    return value if isinstance(value, str) else ""


def _display_metadata_value(value: JsonValue) -> str:
    """Return a participant-facing discovery metadata display value.

    Args:
        value: Discovery metadata value.

    Returns:
        Display string for templates.
    """
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _validate_model_config(config: Mapping[str, JsonValue]) -> str | None:
    """Return a model-config validation error for ``config`` when invalid.

    Args:
        config: Draft v2 plan config.

    Returns:
        Error message, or ``None`` when the executable config validates.
    """
    try:
        parse_model_bank_config(model_bank_config_from_plan_config(config), base_dir=Path.cwd())
    except ConfigError as error:
        return f"Config validation failed: {error}"
    return None


def _builder_review_context(
    *,
    draft: BuilderDraft,
    launch_error: str | None = None,
    active_run_id: str | None = None,
) -> dict[str, object]:
    """Build template context for the builder review page.

    Args:
        draft: Current browser wizard draft.
        launch_error: Optional launch failure message.
        active_run_id: Optional active run id supplied for conflict links.

    Returns:
        Template context for the generated review/summary page.
    """
    state = _builder_review_state(draft)
    context: dict[str, object] = {
        "draft": draft,
        "review": state,
        "review_counts": _builder_review_counts(state),
        "review_phase_counts": _builder_review_phase_counts(state.rows),
        "masked_test_plan_json": _masked_review_test_plan_json(state),
    }
    if launch_error is not None:
        context["launch_error"] = launch_error
    if active_run_id is not None:
        context["active_run_id"] = active_run_id
    return context


def _builder_review_state(draft: BuilderDraft) -> _BuilderReviewState:
    """Compute generated review state for a builder draft.

    Args:
        draft: Current browser wizard draft.

    Returns:
        Review state with preview rows, launch blockers, and export JSON.
    """
    sensitive_export_warning = (
        "Export with secrets includes inline tokens, certificate/private-key paths, and runtime values. "
        "Use it only for local hand-off workflows and do not share it."
    )
    try:
        document = plan_document_from_draft(draft)
        runtime_prompts = runtime_input_prompts_for_plan_document(document)
        missing_prompts = missing_required_runtime_inputs(document, runtime_prompts)
        blockers = list(_model_config_blockers(document))
        boundary_blocker = catalogue_boundary_continue_blocker(
            PlanDocumentBoundary(document.scheme, document.specification, document.version)
        )
        if boundary_blocker is not None:
            blockers.append(boundary_blocker)
        blockers.extend(f"Required runtime input '{prompt.input_id}' is missing." for prompt in missing_prompts)
        has_selected_scope = bool(document.endpoints) or any(
            resource_group.endpoints or resource_group.select_all for resource_group in document.resource_groups
        )
        if not has_selected_scope:
            if boundary_blocker is None:
                blockers.append("Select at least one implemented endpoint before launch.")
            safe_export = plan_document_to_export_json(
                document,
                sensitive_runtime_input_ids=(),
                include_secrets=False,
            )
            return _BuilderReviewState(
                document=document,
                compiled_plan=None,
                rows=(),
                runtime_prompts=runtime_prompts,
                missing_runtime_prompts=missing_prompts,
                blockers=tuple(blockers),
                error=None,
                safe_export_json=json.dumps(safe_export, indent=2, sort_keys=True),
                sensitive_export_warning=sensitive_export_warning,
            )
        preview_document = plan_document_with_runtime_placeholders(document, runtime_prompts)
        compiled_plan = compile_test_plan_document(preview_document, supported_catalogues())
        rows = compiled_plan_rows(compiled_plan)
        blockers.extend(_selected_security_blockers(document, compiled_plan))
        safe_export = plan_document_to_export_json(
            document,
            sensitive_runtime_input_ids=_sensitive_runtime_input_ids(compiled_plan),
            include_secrets=False,
        )
        return _BuilderReviewState(
            document=document,
            compiled_plan=compiled_plan,
            rows=rows,
            runtime_prompts=runtime_prompts,
            missing_runtime_prompts=missing_prompts,
            blockers=tuple(blockers),
            error=None,
            safe_export_json=json.dumps(safe_export, indent=2, sort_keys=True),
            sensitive_export_warning=sensitive_export_warning,
        )
    except (CatalogueError, ConfigError) as error:
        return _BuilderReviewState(
            document=None,
            compiled_plan=None,
            rows=(),
            runtime_prompts=(),
            missing_runtime_prompts=(),
            blockers=(str(error),),
            error=str(error),
            safe_export_json="",
            sensitive_export_warning=sensitive_export_warning,
        )


def _model_config_blockers(document: PlanDocumentV2) -> tuple[str, ...]:
    """Return launch blockers from executable model-bank config validation.

    Args:
        document: Parsed canonical test-plan document.

    Returns:
        Empty tuple when the model-bank config is valid, otherwise one blocker.
    """
    try:
        if document.specification == "dynamic-client-registration":
            validate_dcr_file_references(
                parse_dcr_plan_configuration(
                    document.security_environment,
                    document.dynamic_client_registration,
                    document.metadata,
                )
            )
        parse_model_bank_config(model_bank_config_from_plan_config(document.config), base_dir=Path.cwd())
    except ConfigError as error:
        return (f"Config validation failed: {error}",)
    return ()


def _selected_security_blockers(document: PlanDocumentV2, compiled_plan: CompiledTestPlan) -> tuple[str, ...]:
    """Return blockers for security fields required by selected test cases.

    Args:
        document: Parsed canonical test-plan document.
        compiled_plan: Compiled selected-run preview.

    Returns:
        Blocking messages for missing security config needed by selected cases.
    """
    if not any(test_case.response_signature_required for test_case in compiled_plan.test_cases):
        return ()
    discovery_url = document.security_environment.get("discoveryUrl") or document.config.get("discoveryUrl")
    if isinstance(discovery_url, str) and discovery_url.strip():
        return ()
    return ("Discovery URL is required because the selected run validates response signatures.",)


def _builder_review_counts(state: _BuilderReviewState) -> dict[str, int]:
    """Return high-level counts for the review summary.

    Args:
        state: Computed builder review state.

    Returns:
        Counts used by the review template.
    """
    document = state.document
    resource_group_count = len(document.resource_groups) if document is not None else 0
    if state.compiled_plan is not None:
        endpoint_count = len(state.compiled_plan.traceability.selected_endpoints)
    else:
        endpoint_count = (
            (
                len(document.endpoints)
                if document.endpoints
                else sum(len(resource_group.endpoints) for resource_group in document.resource_groups)
            )
            if document is not None
            else 0
        )
    capability_count = (
        len(state.compiled_plan.traceability.selected_capabilities) if state.compiled_plan is not None else 0
    )
    consent_count = sum(1 for row in state.rows if row.role == "consent")
    psu_authorisation_count = sum(1 for row in state.rows if row.role in {"consent", "token"})
    return {
        "generated": len(state.rows),
        "resource_groups": resource_group_count,
        "endpoints": endpoint_count,
        "capabilities": capability_count,
        "runtime_inputs": len(state.runtime_prompts),
        "missing_runtime_inputs": len(state.missing_runtime_prompts),
        "consents": consent_count,
        "psu_authorisations": psu_authorisation_count,
        "blockers": len(state.blockers),
    }


def _builder_review_phase_counts(rows: tuple[PlanTestCaseRow, ...]) -> dict[str, int]:
    """Return setup/security/resource phase counts for review cards.

    Args:
        rows: Generated test rows.

    Returns:
        Counts keyed by phase label.
    """
    return {
        "setup": sum(1 for row in rows if row.role in {"setup", "token", "consent"}),
        "security": sum(1 for row in rows if row.role == "security"),
        "resource": sum(1 for row in rows if row.role == "resource"),
    }


def _masked_review_test_plan_json(state: _BuilderReviewState) -> str:
    """Return masked canonical test-plan JSON for the review summary.

    Args:
        state: Computed builder review state.

    Returns:
        JSON text with secret-bearing values replaced by ``"***"``.
    """
    if state.document is None or state.compiled_plan is None:
        return ""
    safe_plan = plan_document_to_export_json(
        state.document,
        sensitive_runtime_input_ids=_sensitive_runtime_input_ids(state.compiled_plan),
        include_secrets=False,
    )
    masked_plan = _replace_empty_secret_markers(safe_plan)
    return json.dumps(masked_plan, indent=2, sort_keys=True)


def _replace_empty_secret_markers(value: JsonValue) -> JsonValue:
    """Replace safe-export empty secret strings with a review mask.

    Args:
        value: Safe-export JSON value.

    Returns:
        JSON value with empty secret placeholders rendered as ``"***"`` for
        participant-facing review.
    """
    if isinstance(value, dict):
        replaced: JsonObject = {}
        for key, item in value.items():
            if item == "" and (_review_key_looks_sensitive(key) or key == "value"):
                replaced[key] = "***"
            else:
                replaced[key] = _replace_empty_secret_markers(item)
        return replaced
    if isinstance(value, list):
        return [_replace_empty_secret_markers(item) for item in value]
    return value


def _review_key_looks_sensitive(key: str) -> bool:
    """Return whether a safe-export key likely had a secret value.

    Args:
        key: JSON object key.

    Returns:
        True when the key conventionally names secret-bearing data.
    """
    normalized = key.replace("-", "").replace("_", "").lower()
    return (
        normalized.endswith("token") or "secret" in normalized or "password" in normalized or "privatekey" in normalized
    )


def _sensitive_runtime_input_ids(compiled_plan: CompiledTestPlan) -> tuple[str, ...]:
    """Return sensitive runtime input ids from compiler traceability.

    Args:
        compiled_plan: Compiled plan whose trace should be inspected.

    Returns:
        Runtime input ids marked sensitive by catalogue metadata.
    """
    return tuple(trace.input_id for trace in compiled_plan.traceability.runtime_input_snapshot if trace.sensitive)


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
        "catalogue_trace_summary": _catalogue_trace_summary(record.result),
        "test_plan_validation_summary": _test_plan_validation_summary(record.result),
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


def _catalogue_trace_summary(result: JsonObject | None) -> dict[str, object] | None:
    """Extract compact catalogue traceability fields from completed result JSON.

    Args:
        result: Structured run result JSON, or ``None`` before completion.

    Returns:
        Summary fields for run-detail rendering, or ``None`` when the result
        does not contain compiled catalogue evidence.
    """
    if result is None:
        return None
    catalogue = result.get("catalogue")
    if not isinstance(catalogue, dict):
        return None
    generated_cases = catalogue.get("generatedTestCaseIds")
    selected_endpoints = catalogue.get("selectedEndpoints")
    selected_capabilities = catalogue.get("selectedCapabilities")
    non_certifying_reasons = catalogue.get("nonCertifyingReasons")
    trace_groups = catalogue.get("traceGroups")
    trace_group_statuses = (
        [
            group.get("status")
            for group in trace_groups
            if isinstance(group, dict) and isinstance(group.get("status"), str)
        ]
        if isinstance(trace_groups, list)
        else []
    )
    return {
        "standard": catalogue.get("standard") if isinstance(catalogue.get("standard"), str) else "-",
        "version": catalogue.get("version") if isinstance(catalogue.get("version"), str) else "-",
        "api": catalogue.get("api") if isinstance(catalogue.get("api"), str) else "-",
        "catalogueVersion": (
            catalogue.get("catalogueVersion") if isinstance(catalogue.get("catalogueVersion"), str) else "-"
        ),
        "generatedCount": len(generated_cases) if isinstance(generated_cases, list) else 0,
        "endpointCount": len(selected_endpoints) if isinstance(selected_endpoints, list) else 0,
        "capabilityCount": len(selected_capabilities) if isinstance(selected_capabilities, list) else 0,
        "traceGroupCount": len(trace_groups) if isinstance(trace_groups, list) else 0,
        "traceGroupPassed": sum(status == "passed" for status in trace_group_statuses),
        "traceGroupFailed": sum(status == "failed" for status in trace_group_statuses),
        "traceGroupSkipped": sum(status == "skipped" for status in trace_group_statuses),
        "nonCertifyingReasons": non_certifying_reasons if isinstance(non_certifying_reasons, list) else [],
    }


def _test_plan_validation_summary(result: JsonObject | None) -> dict[str, object] | None:
    """Extract compact test-plan validation fields from completed result JSON.

    Args:
        result: Structured run result JSON, or ``None`` before completion.

    Returns:
        Summary fields for run-detail rendering, or ``None`` when the result
        does not contain test-plan validation evidence.
    """
    if result is None:
        return None
    validation = result.get("testPlanValidation")
    if not isinstance(validation, dict):
        return None
    issues = validation.get("issues")
    return {
        "schemaVersion": validation.get("schemaVersion") if isinstance(validation.get("schemaVersion"), str) else "-",
        "executionMode": validation.get("executionMode") if isinstance(validation.get("executionMode"), str) else "-",
        "valid": validation.get("valid") is True,
        "issueCount": len(issues) if isinstance(issues, list) else 0,
        "issues": issues if isinstance(issues, list) else [],
    }


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
