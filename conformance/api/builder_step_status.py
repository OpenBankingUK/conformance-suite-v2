"""Saved-data completeness of each guided builder step.

Builder pages save partial input as the participant moves between them, so
completeness is no longer enforced when leaving a page. Instead each step's
issues are worked out here from the saved draft by replaying that step's
strict form (the same required-field and whole-group rules a page used to
enforce on Continue) over the saved values plus any retained invalid values.
The step bar shows the result per step, and review lists the issues as launch
blockers, so review and the step bar can never disagree.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from django import forms

from conformance.api.builder_draft_store import BuilderDraft, BuilderStepId
from conformance.api.builder_steps import (
    BuilderNavigationTarget,
    StepProgress,
    builder_flow,
    specification_selected,
)
from conformance.api.builder_wizard import (
    RUN_CONFIG_FIELD_NAMES,
    BusinessConfigForm,
    DiscoveryConfigForm,
    SecurityConfigForm,
    boundary_requires_resource_groups,
    business_config_form_initial,
    config_visibility_for_plan_document,
    discovery_config_form_initial,
    model_bank_config_from_plan_config,
    plan_document_from_draft,
    resource_groups_without_endpoints,
    run_config_requirements_for_document,
    security_config_form_initial,
    stored_security_credentials,
)
from conformance.catalogue import CatalogueError, PlanDocumentBoundary
from conformance.credentials import CredentialMaterial
from conformance.json_types import JsonValue
from conformance.model_bank_config import ConfigError, parse_model_bank_config
from conformance.run_config_requirements import RUN_CONFIG_REASONS, RunConfigRequirement, missing_run_config

type StepIssues = Mapping[BuilderStepId, tuple[str, ...]]
"""Participant-facing issue messages keyed by builder step."""


def draft_boundary(draft: BuilderDraft) -> PlanDocumentBoundary | None:
    """Return the draft's selected specification boundary.

    Args:
        draft: Builder draft.

    Returns:
        Boundary, or ``None`` before a specification is saved.
    """
    if draft.scheme is None or draft.specification is None or draft.version is None:
        return None
    return PlanDocumentBoundary(scheme=draft.scheme, specification=draft.specification, version=draft.version)


def is_dcr_draft(draft: BuilderDraft) -> bool:
    """Return whether a draft targets Open Banking DCR 3.4.

    Args:
        draft: Builder draft.

    Returns:
        True for the DCR specification boundary.
    """
    return draft.specification == "dynamic-client-registration" and draft.version == "3.4"


def model_config_error(config: Mapping[str, JsonValue]) -> str | None:
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


def business_config_form_for_draft(
    draft: BuilderDraft,
    *,
    data: Mapping[str, object] | None = None,
    lenient: bool = False,
) -> BusinessConfigForm | None:
    """Return the business-data form for the draft's current scope.

    Args:
        draft: Builder draft.
        data: Optional bound form data.
        lenient: Whether to skip required-field checks.

    Returns:
        Form showing only fields for the selected scope, or ``None`` when no
        scope with endpoints is selected yet.
    """
    try:
        visibility = config_visibility_for_plan_document(plan_document_from_draft(draft))
    except CatalogueError, ValueError:
        return None
    return BusinessConfigForm(
        data=data,
        initial=business_config_form_initial(draft.config),
        config_visibility=visibility,
        lenient=lenient,
    )


def discovery_form_for_draft(
    draft: BuilderDraft,
    *,
    data: Mapping[str, object] | None = None,
    lenient: bool = False,
) -> DiscoveryConfigForm:
    """Return the discovery form for a draft.

    Args:
        draft: Builder draft.
        data: Optional bound form data.
        lenient: Whether an empty required URL is allowed.

    Returns:
        Discovery form.
    """
    return DiscoveryConfigForm(
        data=data,
        initial=discovery_config_form_initial(draft.config),
        discovery_required=is_dcr_draft(draft),
        lenient=lenient,
    )


def security_form_initial(draft: BuilderDraft) -> dict[str, object]:
    """Return initial security form values for a draft.

    Args:
        draft: Builder draft.

    Returns:
        Initial values decoded from the draft config and discovery metadata.
    """
    return security_config_form_initial(
        draft.config,
        draft.discovery_metadata,
        security_environment=draft.security_environment,
        dynamic_client_registration=draft.dynamic_client_registration,
        metadata=draft.metadata,
        execution_mode=draft.execution_mode,
        psu_authorization_mode=draft.psu_authorization_mode,
        psu_authorization_headers=draft.psu_authorization_headers,
        psu_authorization_parameters=draft.psu_authorization_parameters,
    )


def security_form_for_draft(
    draft: BuilderDraft,
    *,
    data: Mapping[str, object] | None = None,
    files: Mapping[str, object] | None = None,
    lenient: bool = False,
) -> SecurityConfigForm:
    """Return the security form for a draft.

    Args:
        draft: Builder draft.
        data: Optional bound form data.
        files: Optional uploaded credential files.
        lenient: Whether to skip required-field and whole-group checks.

    Returns:
        Security form that keeps stored credentials unless replaced.
    """
    return SecurityConfigForm(
        data=data,
        files=files,
        initial=security_form_initial(draft),
        dcr_mode=is_dcr_draft(draft),
        stored_credentials=_stored_credentials(draft),
        lenient=lenient,
    )


def _stored_credentials(draft: BuilderDraft) -> Mapping[str, CredentialMaterial | None]:
    """Return credentials already held by a draft.

    Args:
        draft: Builder draft.

    Returns:
        Stored credential material keyed by credential name.
    """
    return stored_security_credentials(
        draft.config,
        security_environment=draft.security_environment,
        dynamic_client_registration=draft.dynamic_client_registration,
    )


def _form_messages(form: forms.Form) -> tuple[str, ...]:
    """Return labelled error messages from a bound form.

    Args:
        form: Bound, validated form.

    Returns:
        ``"<label>: <message>"`` for field errors, plain messages otherwise.
        Messages describe the problem only and never echo submitted values.
    """
    messages: list[str] = []
    for name, errors in form.errors.items():
        label = form[name].label if name in form.fields else None
        for error in errors:
            message = f"{label}: {error}" if label else str(error)
            if message not in messages:
                messages.append(message)
    return tuple(messages)


def _replay_data(initial: Mapping[str, object], draft: BuilderDraft, step: BuilderStepId) -> dict[str, object]:
    """Return form data replaying the saved values plus retained invalid values.

    Args:
        initial: Initial form values decoded from the draft.
        draft: Builder draft.
        step: Step being replayed.

    Returns:
        Bound form data equivalent to pressing Continue without edits.
    """
    return {**initial, **draft.invalid_field_values.get(step, {})}


def _scope_issues(draft: BuilderDraft, boundary: PlanDocumentBoundary) -> tuple[str, ...]:
    """Return scope completeness issues.

    Args:
        draft: Builder draft.
        boundary: Selected specification boundary.

    Returns:
        Issue messages for the scope step.
    """
    if not boundary_requires_resource_groups(boundary):
        return () if draft.endpoint_ids else ("Select at least one endpoint.",)
    if not draft.resource_group_ids:
        return ("Select at least one resource group.",)
    try:
        missing = resource_groups_without_endpoints(
            boundary, resource_group_ids=draft.resource_group_ids, endpoint_ids=draft.endpoint_ids
        )
    except CatalogueError as error:
        return (f"Scope could not be resolved: {error}",)
    return tuple(f"Select at least one {label} endpoint." for label in missing)


def _config_issues(draft: BuilderDraft) -> tuple[str, ...]:
    """Return business-data issues for the saved scope.

    Args:
        draft: Builder draft.

    Returns:
        Issue messages; empty when no scope is selected (scope reports that).
    """
    initial = business_config_form_initial(draft.config)
    form = business_config_form_for_draft(draft, data=_replay_data(initial, draft, "config"))
    return () if form is None or form.is_valid() else _form_messages(form)


def _discovery_issues(draft: BuilderDraft) -> tuple[str, ...]:
    """Return discovery issues.

    Args:
        draft: Builder draft.

    Returns:
        Issue messages for the discovery step.
    """
    initial = discovery_config_form_initial(draft.config)
    form = discovery_form_for_draft(draft, data=_replay_data(initial, draft, "discovery"))
    return () if form.is_valid() else _form_messages(form)


def draft_run_config_requirements(draft: BuilderDraft) -> frozenset[RunConfigRequirement] | None:
    """Return the connection and security values a Read/Write draft's scope needs to run.

    Args:
        draft: Builder draft.

    Returns:
        Required values, or ``None`` for DCR drafts and drafts whose scope is
        not selected or cannot be resolved yet.
    """
    if is_dcr_draft(draft):
        return None
    try:
        return run_config_requirements_for_document(plan_document_from_draft(draft))
    except CatalogueError, ValueError:
        return None


def _run_config_issues(draft: BuilderDraft, form: SecurityConfigForm) -> tuple[str, ...]:
    """Return connection and security values the selected scope needs but the draft lacks.

    Args:
        draft: Builder draft.
        form: Replayed security form, used for field labels.

    Returns:
        ``"<label>: required to run because ..."`` messages; empty before a
        scope is selected. Fields already reported as invalid are skipped.
    """
    requirements = draft_run_config_requirements(draft)
    if not requirements:
        return ()
    invalid_fields = {
        *draft.invalid_field_values.get("security", {}),
        *draft.invalid_field_values.get("discovery", {}),
    }
    ignore = tuple(key for key, name in RUN_CONFIG_FIELD_NAMES.items() if name in invalid_fields)
    messages: list[str] = []
    for missing in missing_run_config(
        requirements, draft.config, security_environment=draft.security_environment, ignore=ignore
    ):
        name = RUN_CONFIG_FIELD_NAMES[missing.key]
        label = form[name].label if name in form.fields else "Discovery URL"
        messages.append(f"{label}: required to run because {RUN_CONFIG_REASONS[missing.key]}.")
    return tuple(messages)


def _security_issues(draft: BuilderDraft) -> tuple[str, ...]:
    """Return security issues.

    For Read/Write drafts this is the connection and security step, so it
    also reports discovery issues and the values the selected scope needs to
    run.

    Args:
        draft: Builder draft.

    Returns:
        Issue messages for the security step.
    """
    form = security_form_for_draft(draft, data=_replay_data(security_form_initial(draft), draft, "security"))
    if is_dcr_draft(draft):
        return () if form.is_valid() else _form_messages(form)
    issues = [*_discovery_issues(draft)]
    if not form.is_valid():
        issues.extend(_form_messages(form))
    else:
        error = model_config_error(draft.config)
        if error is not None:
            issues.append(error)
    issues.extend(message for message in _run_config_issues(draft, form) if message not in issues)
    return tuple(issues)


def builder_step_issues(draft: BuilderDraft) -> dict[BuilderStepId, tuple[str, ...]]:
    """Return completeness and format issues for every step in the draft's flow.

    Args:
        draft: Builder draft as saved.

    Returns:
        Issue messages keyed by step id. Only the specification step is
        reported until a supported specification is selected.
    """
    boundary = draft_boundary(draft)
    if boundary is None or not specification_selected(draft):
        return {"catalogue": ("Choose a supported specification.",)}
    issues: dict[BuilderStepId, tuple[str, ...]] = {"catalogue": ()}
    for step in builder_flow(draft):
        match step.step_id:
            case "scope":
                issues["scope"] = _scope_issues(draft, boundary)
            case "config":
                issues["config"] = _config_issues(draft)
            case "discovery":
                issues["discovery"] = _discovery_issues(draft)
            case "security":
                issues["security"] = _security_issues(draft)
            case _:
                pass
    return issues


def _step_started(draft: BuilderDraft, step: BuilderStepId) -> bool:
    """Return whether a step has been saved or holds any participant data.

    Args:
        draft: Builder draft.
        step: Step to inspect.

    Returns:
        True when the step has been saved, or differs from a blank draft with
        the same boundary.
    """
    if draft.invalid_field_values.get(step):
        return True
    if step in draft.saved_steps and (step != "config" or business_config_form_for_draft(draft) is not None):
        # Business data has no inputs until a scope is chosen, so saving it
        # empty then is not progress; scope reports the missing selection.
        return True
    match step:
        case "catalogue":
            return draft_boundary(draft) is not None
        case "scope":
            return bool(draft.resource_group_ids or draft.endpoint_ids)
        case "config":
            blank_config = BuilderDraft.create().config
            return business_config_form_initial(draft.config) != business_config_form_initial(blank_config)
        case "discovery":
            blank_config = BuilderDraft.create().config
            return discovery_config_form_initial(draft.config) != discovery_config_form_initial(blank_config)
        case "security":
            if "discovery" not in {step.step_id for step in builder_flow(draft)} and _step_started(draft, "discovery"):
                return True
            reference = _with_boundary_of(
                replace(BuilderDraft.create(), discovery_metadata=draft.discovery_metadata), draft
            )
            return security_form_initial(draft) != security_form_initial(reference) or any(
                value is not None for value in _stored_credentials(draft).values()
            )


def _with_boundary_of(reference: BuilderDraft, draft: BuilderDraft) -> BuilderDraft:
    """Return ``reference`` carrying ``draft``'s specification boundary fields.

    Args:
        reference: Blank comparison draft.
        draft: Draft whose boundary is copied.

    Returns:
        Comparison draft with the same boundary and security profile.
    """
    return replace(
        reference,
        scheme=draft.scheme,
        specification=draft.specification,
        version=draft.version,
        security_profile=draft.security_profile,
        openapi_document_update=draft.openapi_document_update,
    )


def builder_step_progress(
    draft: BuilderDraft,
    issues: StepIssues | None = None,
) -> dict[BuilderNavigationTarget, StepProgress]:
    """Return step-bar progress for every saved step in the draft's flow.

    Args:
        draft: Builder draft as saved.
        issues: Precomputed issues, when already available.

    Returns:
        ``complete`` (data and no issues), ``attention`` (data with issues), or
        ``not_started`` (never saved and no data yet) per step.
    """
    step_issues = builder_step_issues(draft) if issues is None else issues
    progress: dict[BuilderNavigationTarget, StepProgress] = {}
    for step in builder_flow(draft):
        if step.step_id == "review":
            continue
        step_id: BuilderStepId = step.step_id
        if not _step_started(draft, step_id):
            progress[step_id] = "not_started"
        elif step_issues.get(step_id):
            progress[step_id] = "attention"
        else:
            progress[step_id] = "complete"
    return progress
