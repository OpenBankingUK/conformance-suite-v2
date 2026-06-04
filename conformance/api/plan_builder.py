"""Plan-builder forms and presenters for participant-facing browser workflows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from django import forms

from conformance.json_types import JsonValue
from conformance.manifest import (
    Manifest,
    ManifestError,
    ManifestStep,
    PsuAuthorizationStep,
    StepPhase,
    load_manifest_from_object,
)
from conformance.model_bank_config import ConfigError, ModelBankConfig, parse_model_bank_config
from conformance.suite_catalog import SuiteCatalogError, SuiteMetadata, resolve_suite
from conformance.test_plan import TestPlan, TestPlanEntry

StepKind = Literal["http", "psu-authorization"]
"""Step kind labels displayed by the participant plan-builder UI."""

SelectionMode = Literal["deselect", "select"]
"""Modes used by browser forms to describe participant step selection."""


@dataclass(frozen=True)
class PlanStepRow:
    """Rendered row for one manifest step in the participant plan builder.

    Attributes:
        id: Stable manifest step identifier.
        name: Human-readable manifest step name.
        kind: Manifest step kind displayed to participants.
        group: Execution group label shown in the plan preview.
        phase: Scheduling phase shown in the plan preview.
        mandatory: Whether the manifest marks the step as certification mandatory.
        optional: Whether the manifest marks the step as opt-in optional.
        default_selected: Whether the default plan selects the step before form input.
        selected_after_form: Whether the submitted form selection selects the step.
        certification_required: Whether certification eligibility depends on the step.
        deselection_impacts_certification: Whether deselecting this step affects certification eligibility.
        certification_blocked_by_deselection: Whether this submitted selection blocks certification eligibility.
    """

    id: str
    name: str
    kind: StepKind
    group: str
    phase: StepPhase
    mandatory: bool
    optional: bool
    default_selected: bool
    selected_after_form: bool
    certification_required: bool
    deselection_impacts_certification: bool
    certification_blocked_by_deselection: bool


@dataclass(frozen=True)
class PlanPreview:
    """Validated plan-builder state ready for template rendering or launch.

    Attributes:
        config: Validated model-bank configuration supplied through the form.
        manifest: Validated v1 manifest supplied through the form or resolved
            from ``config.test_suite``.
        suite_metadata: Display metadata for a config-resolved suite, or
            ``None`` when the preview uses an explicit manifest.
        default_plan: Default plan derived from the manifest before form input.
        selected_plan: Plan after applying submitted selection or deselection input.
        rows: Step presenters in manifest order.
        launch_supported: Whether this preview can be launched by the browser UI slice.
        launch_blockers: Human-readable reasons launch is disabled.
        certification_eligible_by_selection: Whether the submitted selection preserves certification eligibility.
    """

    config: ModelBankConfig
    manifest: Manifest
    suite_metadata: SuiteMetadata | None
    default_plan: TestPlan
    selected_plan: TestPlan
    rows: tuple[PlanStepRow, ...]
    launch_supported: bool
    launch_blockers: tuple[str, ...]
    certification_eligible_by_selection: bool


class StepIdListField(forms.Field):
    """Form field that accepts repeated checkbox values as a list of step ids.

    Attributes:
        widget: Checkbox widget used to read repeated values from form data.
    """

    widget = forms.CheckboxSelectMultiple

    def to_python(self, value: object) -> list[str]:
        """Convert a Django form value into a list of non-empty step ids.

        Args:
            value: Raw value extracted from form data. Checkbox widgets usually
                provide a list, while tests or alternate clients may provide a
                single string.

        Returns:
            Ordered, non-empty step ids submitted by the caller.

        Raises:
            ValidationError: If any submitted value is not a string.
        """
        if value in self.empty_values:
            return []
        if isinstance(value, str):
            return [value] if value else []
        if isinstance(value, (list, tuple)) and all(isinstance(step_id, str) for step_id in value):
            return [step_id for step_id in value if step_id]
        raise forms.ValidationError("Step ids must be submitted as strings", code="invalid_step_ids")


class PlanBuilderForm(forms.Form):
    """Django Form boundary for participant plan-builder preview and launch posts.

    Attributes:
        config_json: Textarea containing model-bank config JSON.
        manifest_json: Optional textarea containing v1 conformance manifest
            JSON. When blank, ``config.testSuite`` may resolve a bundled suite.
        selection_mode: Choice controlling whether selected or deselected ids drive the submitted plan.
        selected_step_ids: Step ids posted by checked step checkboxes.
        deselect_step_ids: Step ids posted as explicit deselections.
        preview: Typed preview built after successful form validation.
    """

    config_json: forms.CharField = forms.CharField(label="Config JSON", widget=forms.Textarea)
    manifest_json: forms.CharField = forms.CharField(label="Manifest JSON", required=False, widget=forms.Textarea)
    selection_mode: forms.ChoiceField = forms.ChoiceField(
        choices=(("deselect", "Deselect submitted ids"), ("select", "Select submitted ids")),
        required=False,
    )
    selected_step_ids: StepIdListField = StepIdListField(required=False)
    deselect_step_ids: StepIdListField = StepIdListField(required=False)

    preview: PlanPreview | None = None

    def clean_config_json(self) -> ModelBankConfig:
        """Validate the submitted model-bank config JSON.

        Returns:
            Parsed and validated model-bank configuration.

        Raises:
            ValidationError: If the value is not JSON, is not a JSON object,
                or fails model-bank config validation.
        """
        raw_value = self.cleaned_data["config_json"]
        if not isinstance(raw_value, str):
            raise forms.ValidationError("Config JSON must be text", code="invalid_config_json")
        raw_config = _load_json_object(raw_value, label="Config JSON")
        try:
            return parse_model_bank_config(raw_config, base_dir=Path.cwd())
        except ConfigError as error:
            raise forms.ValidationError(f"Config validation failed: {error}", code="invalid_config") from error

    def clean_manifest_json(self) -> Manifest | None:
        """Validate the submitted v1 conformance manifest JSON.

        Returns:
            Parsed v1 manifest, or ``None`` when the field is blank so the
            form can attempt config-driven suite resolution.

        Raises:
            ValidationError: If the value is not JSON, fails manifest
                validation, or is not a v1 manifest.
        """
        raw_value = self.cleaned_data["manifest_json"]
        if not isinstance(raw_value, str):
            raise forms.ValidationError("Manifest JSON must be text", code="invalid_manifest_json")
        if raw_value.strip() == "":
            return None
        raw_manifest = _load_json_object(raw_value, label="Manifest JSON")
        try:
            manifest = load_manifest_from_object(raw_manifest)
        except ManifestError as error:
            raise forms.ValidationError(f"Manifest validation failed: {error}", code="invalid_manifest") from error
        if manifest.schema_version != "v1":
            raise forms.ValidationError(
                "Plan builder supports v1 manifests only; v0 manifests do not carry selectable plan steps.",
                code="unsupported_manifest_version",
            )
        return manifest

    def clean(self) -> dict[str, object]:
        """Build the typed preview once individual form fields are valid.

        Returns:
            The cleaned data dictionary returned by ``forms.Form``.
        """
        base_cleaned_data = super().clean()
        cleaned_data: dict[str, object] = {} if base_cleaned_data is None else dict(base_cleaned_data)
        config = cleaned_data.get("config_json")
        manifest = cleaned_data.get("manifest_json")
        if not isinstance(config, ModelBankConfig):
            return cleaned_data

        suite_metadata: SuiteMetadata | None = None
        if manifest is None:
            if config.test_suite is None:
                self.add_error(
                    "manifest_json",
                    forms.ValidationError(
                        "Manifest JSON is required unless config.testSuite selects a bundled suite.",
                        code="missing_manifest_or_suite",
                    ),
                )
                return cleaned_data
            try:
                resolved_suite = resolve_suite(config.test_suite)
            except SuiteCatalogError as error:
                raise forms.ValidationError(f"Suite resolution failed: {error}", code="invalid_suite") from error
            manifest = resolved_suite.manifest
            suite_metadata = resolved_suite.metadata
        elif not isinstance(manifest, Manifest):
            return cleaned_data

        selected_step_ids = _cleaned_step_ids(cleaned_data.get("selected_step_ids"))
        deselect_step_ids = _cleaned_step_ids(cleaned_data.get("deselect_step_ids"))
        selection_mode = _cleaned_selection_mode(cleaned_data.get("selection_mode"))
        try:
            self.preview = build_plan_preview(
                config=config,
                manifest=manifest,
                suite_metadata=suite_metadata,
                selected_step_ids=selected_step_ids,
                deselect_step_ids=deselect_step_ids,
                selection_mode=selection_mode,
            )
        except ValueError as error:
            raise forms.ValidationError(f"Plan validation failed: {error}", code="invalid_plan") from error
        return cleaned_data


def build_plan_preview(
    *,
    config: ModelBankConfig,
    manifest: Manifest,
    suite_metadata: SuiteMetadata | None = None,
    selected_step_ids: list[str] | None = None,
    deselect_step_ids: list[str] | None = None,
    selection_mode: SelectionMode = "deselect",
) -> PlanPreview:
    """Build the typed step-row presenter for a validated v1 manifest.

    Args:
        config: Validated model-bank configuration to carry into launch.
        manifest: Validated v1 manifest to preview.
        suite_metadata: Optional metadata describing the config-resolved suite
            that supplied ``manifest``.
        selected_step_ids: Step ids checked in a selection-mode form post.
        deselect_step_ids: Step ids unchecked or explicitly deselected by a deselection-mode form post.
        selection_mode: Whether to derive the submitted plan from selected ids
            or by deselecting ids from the default plan.

    Returns:
        A complete plan preview with step rows and launch support flags.

    Raises:
        ValueError: If ``manifest`` is not v1 or any submitted step id is unknown.
    """
    if manifest.schema_version != "v1":
        raise ValueError("Plan builder supports v1 manifests only")
    default_plan = TestPlan.default_plan_from_manifest(manifest)
    if selection_mode == "select":
        selected_plan = _plan_from_selected_step_ids(default_plan, selected_step_ids or [])
    else:
        selected_plan = default_plan.with_deselection(deselect_step_ids or [])

    rows = _build_step_rows(manifest=manifest, default_plan=default_plan, selected_plan=selected_plan)
    launch_blockers = _launch_blockers(manifest)
    return PlanPreview(
        config=config,
        manifest=manifest,
        suite_metadata=suite_metadata,
        default_plan=default_plan,
        selected_plan=selected_plan,
        rows=rows,
        launch_supported=not launch_blockers,
        launch_blockers=launch_blockers,
        certification_eligible_by_selection=selected_plan.is_eligible_by_selection(),
    )


def _load_json_object(raw_value: str, *, label: str) -> dict[str, JsonValue]:
    """Decode a JSON text field and require an object root.

    Args:
        raw_value: JSON text submitted through the form.
        label: Human-readable field label for validation messages.

    Returns:
        Decoded JSON object.

    Raises:
        ValidationError: If the text is malformed JSON or the root is not an object.
    """
    try:
        raw_object = json.loads(raw_value)
    except json.JSONDecodeError as error:
        raise forms.ValidationError(f"{label} must be valid JSON: {error.msg}", code="invalid_json") from error
    if not isinstance(raw_object, dict):
        raise forms.ValidationError(f"{label} must be a JSON object", code="invalid_json_object")
    return cast(dict[str, JsonValue], raw_object)


def _cleaned_step_ids(value: object) -> list[str] | None:
    """Read optional step ids from cleaned form data.

    Args:
        value: Cleaned value from ``StepIdListField``.

    Returns:
        A list of step ids, or ``None`` when the field was absent.

    Raises:
        TypeError: If a non-string step id reaches cleaned data.
    """
    if value is None:
        return None
    if isinstance(value, list) and all(isinstance(step_id, str) for step_id in value):
        return value
    raise TypeError("Cleaned step ids must be a list of strings")


def _cleaned_selection_mode(value: object) -> SelectionMode:
    """Read the selection mode from cleaned form data.

    Args:
        value: Cleaned value from the ``selection_mode`` choice field.

    Returns:
        The submitted selection mode, defaulting to ``"deselect"``.
    """
    if value == "select":
        return "select"
    return "deselect"


def _plan_from_selected_step_ids(default_plan: TestPlan, selected_step_ids: list[str]) -> TestPlan:
    """Create a plan by marking exactly the submitted ids as selected.

    Args:
        default_plan: Default plan derived from a v1 manifest.
        selected_step_ids: Step ids submitted as checked by the participant.

    Returns:
        A plan whose entries preserve manifest order and metadata while using
        the submitted selection state.

    Raises:
        ValueError: If any submitted id is not present in the default plan.
    """
    selected_set = set(selected_step_ids)
    known_ids = {entry.step_id for entry in default_plan.entries}
    unknown = selected_set - known_ids
    if unknown:
        unknown_list = ", ".join(sorted(unknown))
        raise ValueError(f"Unknown step id(s) in selection: {unknown_list}")
    return TestPlan(
        entries=tuple(
            TestPlanEntry(
                step_id=entry.step_id,
                mandatory=entry.mandatory,
                optional=entry.optional,
                selected=entry.step_id in selected_set,
            )
            for entry in default_plan.entries
        )
    )


def _build_step_rows(*, manifest: Manifest, default_plan: TestPlan, selected_plan: TestPlan) -> tuple[PlanStepRow, ...]:
    """Build participant-facing row presenters for every manifest step.

    Args:
        manifest: Validated v1 manifest whose steps are being rendered.
        default_plan: Plan before participant form input.
        selected_plan: Plan after participant form input.

    Returns:
        Step rows in manifest order.
    """
    default_entries = {entry.step_id: entry for entry in default_plan.entries}
    selected_entries = {entry.step_id: entry for entry in selected_plan.entries}
    rows: list[PlanStepRow] = []
    for step in manifest.steps:
        default_entry = default_entries[step.id]
        selected_entry = selected_entries[step.id]
        rows.append(
            PlanStepRow(
                id=step.id,
                name=step.name,
                kind=_step_kind(step),
                group=step.group,
                phase=step.phase,
                mandatory=step.mandatory,
                optional=step.optional,
                default_selected=default_entry.selected,
                selected_after_form=selected_entry.selected,
                certification_required=step.mandatory,
                deselection_impacts_certification=step.mandatory,
                certification_blocked_by_deselection=step.mandatory and not selected_entry.selected,
            )
        )
    return tuple(rows)


def _step_kind(step: ManifestStep | PsuAuthorizationStep) -> StepKind:
    """Return the display kind for a v1 manifest step.

    Args:
        step: Parsed v1 manifest step.

    Returns:
        ``"psu-authorization"`` for PSU steps, otherwise ``"http"``.
    """
    if isinstance(step, PsuAuthorizationStep):
        return "psu-authorization"
    return "http"


def _launch_blockers(manifest: Manifest) -> tuple[str, ...]:
    """Return reasons the browser UI must not launch this manifest.

    Args:
        manifest: Validated v1 manifest being previewed.

    Returns:
        Human-readable launch blockers. Empty when browser launch is supported.
    """
    return ()
