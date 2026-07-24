"""Plan-builder forms and presenters for catalogue-backed browser workflows."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast

from django import forms
from django.core.files.uploadedfile import UploadedFile
from django.utils.datastructures import MultiValueDict

from conformance.catalogue import (
    ApplicabilityDecision,
    CatalogueError,
    CatalogueKey,
    CatalogueTestCase,
    CompiledTestPlan,
    EndpointRef,
    HttpMethod,
    ImplementedEndpoint,
    RuntimeInputRequirement,
    SecurityProfile,
    TestCatalogue,
    TestPlanSpec,
    compile_test_plan,
    parse_test_plan_spec,
)
from conformance.catalogue_registry import resolve_catalogue, supported_catalogues
from conformance.json_types import JsonObject, JsonValue
from conformance.model_bank_config import ConfigError, ModelBankConfig, parse_model_bank_config

_RUNTIME_INPUT_PREFIX = "runtime_input__"
"""Prefix used for runtime input names in browser form submissions."""

_CAPABILITY_VALUE_SEPARATOR = "::"
"""Separator used in endpoint capability checkbox values."""


@dataclass(frozen=True)
class GuidedApiOption:
    """One guided-flow API option available for a specification version.

    Attributes:
        standard: Standards namespace that owns the catalogue.
        spec_version: Specification version that exposes this API family.
        api: API family value posted back by the browser form.
        label: Human-readable API family label shown in the browser UI.
    """

    standard: str
    spec_version: str
    api: str
    label: str


@dataclass(frozen=True)
class EndpointCapabilityOption:
    """One endpoint-scoped capability rendered inside an endpoint card.

    Attributes:
        value: Stable browser checkbox value pairing endpoint and capability.
        endpoint_id: Endpoint option id that owns the capability.
        capability_id: Catalogue-owned capability id.
        label: Human-readable capability label.
        description: Participant-facing capability explanation.
        required: Whether the capability is locked baseline endpoint coverage.
        selected: Whether the capability is currently selected or implied.
    """

    value: str
    endpoint_id: str
    capability_id: str
    label: str
    description: str
    required: bool
    selected: bool = False


@dataclass(frozen=True)
class CatalogueEndpointOption:
    """One implemented-endpoint option rendered by the browser plan builder.

    Attributes:
        id: Stable form value for this exact method/path reference.
        standard: Standards namespace that owns the endpoint.
        spec_version: Specification version that owns the endpoint.
        api: API family that owns the endpoint.
        method: HTTP method for the endpoint.
        path: Standards path for the endpoint.
        resource_group: Human-readable grouping label.
        operation_id: Optional operation identifier exported in the plan spec.
        capabilities: Capability selectors available for this endpoint.
        selected: Whether the submitted form currently selects this endpoint.
    """

    id: str
    standard: str
    spec_version: str
    api: str
    method: str
    path: str
    resource_group: str
    operation_id: str | None
    capabilities: tuple[EndpointCapabilityOption, ...] = ()
    selected: bool = False


@dataclass(frozen=True)
class RuntimeInputPrompt:
    """One runtime input prompt driven by selected catalogue endpoints.

    Attributes:
        input_id: Stable runtime input identifier used by the plan spec.
        name: Browser form field name.
        label: Human-readable prompt label from the catalogue.
        input_type: Catalogue runtime input type.
        required: Whether the compiler requires the value.
        sensitive: Whether values are secrets or credentials and must not be
            shown in audit snapshots.
        value: Current submitted value for redisplay.
    """

    input_id: str
    name: str
    label: str
    input_type: str
    required: bool
    sensitive: bool
    value: str


@dataclass(frozen=True)
class PlanRuntimeRequirement:
    """Runtime requirement summary shown in the generated plan preview.

    Attributes:
        input_id: Stable runtime input identifier.
        label: Human-readable catalogue prompt label.
        required: Whether the generated case requires the input.
        sensitive: Whether values for this input are secret-bearing.
    """

    input_id: str
    label: str
    required: bool
    sensitive: bool


@dataclass(frozen=True)
class PlanTestCaseRow:
    """Read-only generated test-case preview row.

    Attributes:
        id: Catalogue test-case identifier.
        name: Human-readable test-case name.
        role: Execution/compliance role.
        phase: High-level execution phase derived from the compiled case role.
        source: Human-readable reason this case was generated.
        source_detail: Additional endpoint, capability, or dependency context.
        mandatory: Whether the generated case is mandatory for certification.
        dependencies: Other generated case ids this case depends on.
        request_count: Number of request steps generated for the case.
        assertion_count: Number of locked assertions attached to the case.
        runtime_requirements: Runtime inputs consumed by the generated case.
        request_step_ids: Request-step identifiers owned by the case.
        assertion_summaries: Assertion summaries owned by the case.
        compliance_scope: Traceability labels for standards and legacy coverage.
    """

    id: str
    name: str
    role: str
    phase: str
    source: str
    source_detail: str
    mandatory: bool
    dependencies: tuple[str, ...]
    request_count: int
    assertion_count: int
    runtime_requirements: tuple[PlanRuntimeRequirement, ...]
    request_step_ids: tuple[str, ...]
    assertion_summaries: tuple[str, ...]
    compliance_scope: tuple[str, ...]


@dataclass(frozen=True)
class PlanPreview:
    """Validated catalogue plan-builder state ready for preview or launch.

    Attributes:
        config: Validated runtime configuration supplied through the form.
        plan_spec: Participant-authored plan spec generated by guided endpoint
            selection or imported through JSON mode.
        compiled_plan: Deterministic executable plan generated by the compiler.
        catalogue_label: Human-readable catalogue boundary label.
        endpoint_options: Endpoint options for the selected catalogue.
        selected_endpoint_ids: Endpoint option ids included in ``plan_spec``.
        runtime_input_prompts: Runtime prompts derived from selected endpoints.
        generated_plan_spec_json: Exportable JSON plan spec.
        rows: Generated test-case audit rows. Templates keep these collapsed by
            default so participants see counts before implementation details.
        launch_supported: Whether this preview can be launched.
        launch_blockers: Human-readable launch blockers.
        certification_eligible_by_selection: Whether the generated plan is
            certifying before execution.
    """

    config: ModelBankConfig
    plan_spec: TestPlanSpec
    compiled_plan: CompiledTestPlan
    catalogue_label: str
    endpoint_options: tuple[CatalogueEndpointOption, ...]
    selected_endpoint_ids: tuple[str, ...]
    runtime_input_prompts: tuple[RuntimeInputPrompt, ...]
    generated_plan_spec_json: str
    rows: tuple[PlanTestCaseRow, ...]
    launch_supported: bool
    launch_blockers: tuple[str, ...]
    certification_eligible_by_selection: bool


class EndpointIdListField(forms.Field):
    """Form field that accepts repeated endpoint checkbox values.

    Attributes:
        widget: Checkbox widget used to read repeated values from form data.
    """

    widget = forms.CheckboxSelectMultiple

    def to_python(self, value: object) -> list[str]:
        """Convert a Django form value into a list of non-empty endpoint ids.

        Args:
            value: Raw value extracted from form data.

        Returns:
            Ordered, non-empty endpoint ids submitted by the caller.

        Raises:
            ValidationError: If any submitted value is not a string.
        """
        if value in self.empty_values:
            return []
        if isinstance(value, str):
            return [value] if value else []
        if isinstance(value, (list, tuple)) and all(isinstance(endpoint_id, str) for endpoint_id in value):
            return [endpoint_id for endpoint_id in value if endpoint_id]
        raise forms.ValidationError("Endpoint ids must be submitted as strings", code="invalid_endpoint_ids")


class PlanBuilderForm(forms.Form):
    """Django Form boundary for catalogue plan-builder preview and launch posts.

    Attributes:
        config_json: Textarea containing model-bank config JSON.
        plan_spec_json: Optional textarea containing an exportable plan spec.
        guided_environment: Structured environment input used when building
            config from guided browser fields.
        guided_discovery_url: Structured discovery URL input used by the guided
            browser flow.
        guided_standard: Structured selector for the standards namespace.
        guided_spec_version: Structured selector for the target spec version.
        guided_api: Structured selector for the target API family.
        guided_security_profile: Structured selector for the security profile.
        guided_client_id: Structured OAuth client id prompt.
        guided_redirect_uri: Structured OAuth redirect URI prompt.
        guided_authorization_endpoint: Optional authorization endpoint override.
        guided_resource_base_url: Optional protected-resource base URL.
        guided_signing_certificate_path_root: Optional signing path root.
        guided_signing_certificate_path: Optional signing certificate path.
        guided_signing_private_key_path: Optional signing private-key path.
        guided_signing_kid: Optional signing key id.
        guided_signing_client_assertion_issuer: Optional assertion issuer.
        guided_signing_client_assertion_subject: Optional assertion subject.
        guided_signing_token_endpoint_auth_method: Optional token endpoint auth method.
        implemented_endpoint_ids: Endpoint ids posted by endpoint checkboxes.
        implemented_endpoint_capabilities: Endpoint capability checkbox values
            posted for optional implementation features.
        preview: Typed preview built after successful form validation.
        generated_config_json: Optional generated config JSON emitted from guided fields.
        runtime_input_prompts: Runtime prompts derived from current endpoint selection.
    """

    config_json: forms.CharField = forms.CharField(label="Config JSON", required=False, widget=forms.Textarea)
    plan_spec_json: forms.CharField = forms.CharField(label="Plan spec JSON", required=False, widget=forms.Textarea)
    guided_environment: forms.CharField = forms.CharField(label="Environment", required=False)
    guided_discovery_url: forms.CharField = forms.CharField(label="Discovery URL", required=False)
    guided_standard: forms.ChoiceField = forms.ChoiceField(label="Standard", required=False)
    guided_spec_version: forms.ChoiceField = forms.ChoiceField(label="Specification version", required=False)
    guided_api: forms.ChoiceField = forms.ChoiceField(label="API family", required=False)
    guided_security_profile: forms.ChoiceField = forms.ChoiceField(label="Security profile", required=False)
    guided_client_id: forms.CharField = forms.CharField(label="Client ID", required=False)
    guided_redirect_uri: forms.CharField = forms.CharField(label="Redirect URI", required=False)
    guided_authorization_endpoint: forms.CharField = forms.CharField(
        label="Authorization endpoint override",
        required=False,
    )
    guided_resource_base_url: forms.CharField = forms.CharField(label="Resource base URL", required=False)
    guided_signing_certificate_path_root: forms.CharField = forms.CharField(
        label="Certificate path root",
        required=False,
    )
    guided_signing_certificate_path: forms.CharField = forms.CharField(label="Signing certificate path", required=False)
    guided_signing_private_key_path: forms.CharField = forms.CharField(label="Signing private key path", required=False)
    guided_signing_kid: forms.CharField = forms.CharField(label="Signing key id", required=False)
    guided_signing_client_assertion_issuer: forms.CharField = forms.CharField(
        label="Client assertion issuer",
        required=False,
    )
    guided_signing_client_assertion_subject: forms.CharField = forms.CharField(
        label="Client assertion subject",
        required=False,
    )
    guided_signing_token_endpoint_auth_method: forms.ChoiceField = forms.ChoiceField(
        label="Token endpoint auth method",
        required=False,
    )
    implemented_endpoint_ids: EndpointIdListField = EndpointIdListField(required=False)
    implemented_endpoint_capabilities: EndpointIdListField = EndpointIdListField(required=False)

    preview: PlanPreview | None = None
    generated_config_json: str | None = None
    runtime_input_prompts: tuple[RuntimeInputPrompt, ...] = ()

    def __init__(
        self,
        data: Mapping[str, object] | None = None,
        files: MultiValueDict[str, UploadedFile] | None = None,
        *,
        auto_id: bool | str = "id_%s",
        prefix: str | None = None,
        initial: Mapping[str, object] | None = None,
        error_class: type[forms.utils.ErrorList] = forms.utils.ErrorList,
        label_suffix: str | None = None,
        empty_permitted: bool = False,
        field_order: list[str] | None = None,
        use_required_attribute: bool | None = None,
        renderer: forms.renderers.BaseRenderer | None = None,
        bound_field_class: type[forms.BoundField] | None = None,
    ) -> None:
        """Initialise the form with catalogue choices.

        Args:
            data: Optional bound form data.
            files: Optional uploaded files mapping.
            auto_id: HTML id generation mode.
            prefix: Optional form field prefix.
            initial: Optional initial field values.
            error_class: Error list class used by Django forms.
            label_suffix: Optional label suffix for rendered fields.
            empty_permitted: Whether empty bound forms are permitted.
            field_order: Optional field render order override.
            use_required_attribute: Optional HTML required-attribute mode.
            renderer: Optional Django form renderer.
            bound_field_class: Optional custom bound-field class.
        """
        super().__init__(
            data=cast(MutableMapping[str, object] | None, data),
            files=files,
            auto_id=auto_id,
            prefix=prefix,
            initial=cast(MutableMapping[str, object] | None, initial),
            error_class=error_class,
            label_suffix=label_suffix,
            empty_permitted=empty_permitted,
            field_order=field_order,
            use_required_attribute=use_required_attribute,
            renderer=renderer,
            bound_field_class=bound_field_class,
        )
        cast(forms.ChoiceField, self.fields["guided_standard"]).choices = guided_standard_choices()
        cast(forms.ChoiceField, self.fields["guided_spec_version"]).choices = guided_spec_version_choices()
        cast(forms.ChoiceField, self.fields["guided_api"]).choices = guided_api_choices()
        cast(forms.ChoiceField, self.fields["guided_security_profile"]).choices = guided_security_profile_choices()
        cast(forms.ChoiceField, self.fields["guided_signing_token_endpoint_auth_method"]).choices = [
            ("", "Select auth method"),
            ("private_key_jwt", "private_key_jwt"),
            ("tls_client_auth", "tls_client_auth"),
        ]

    def clean_config_json(self) -> ModelBankConfig | None:
        """Validate the submitted model-bank config JSON.

        Returns:
            Parsed and validated model-bank configuration, or ``None`` when
            guided fields should generate the config.

        Raises:
            ValidationError: If the value is not JSON, is not a JSON object, or
                fails model-bank config validation.
        """
        raw_value = self.cleaned_data["config_json"]
        if not isinstance(raw_value, str):
            raise forms.ValidationError("Config JSON must be text", code="invalid_config_json")
        if raw_value.strip() == "":
            return None
        raw_config = _load_json_object(raw_value, label="Config JSON")
        try:
            return parse_model_bank_config(raw_config, base_dir=Path.cwd())
        except ConfigError as error:
            raise forms.ValidationError(f"Config validation failed: {error}", code="invalid_config") from error

    def clean(self) -> dict[str, object]:
        """Build the typed preview once individual form fields are valid.

        Returns:
            The cleaned data dictionary returned by ``forms.Form``.
        """
        base_cleaned_data = super().clean()
        cleaned_data: dict[str, object] = {} if base_cleaned_data is None else dict(base_cleaned_data)
        config = cleaned_data.get("config_json")
        if config is None:
            config = self._build_guided_config(cleaned_data=cleaned_data)
        if not isinstance(config, ModelBankConfig):
            return cleaned_data

        selected_catalogue = _catalogue_from_cleaned_data(cleaned_data)
        selected_endpoint_ids = _cleaned_endpoint_ids(cleaned_data.get("implemented_endpoint_ids"))
        selected_capability_values = _cleaned_endpoint_ids(cleaned_data.get("implemented_endpoint_capabilities"))
        security_profile = _security_profile_from_cleaned_data(cleaned_data)
        endpoint_options = _endpoint_options_for_catalogue(
            selected_catalogue,
            selected_endpoint_ids,
            selected_capability_values=selected_capability_values,
        )
        self.runtime_input_prompts = _runtime_prompts_for_endpoint_selection(
            catalogue=selected_catalogue,
            selected_endpoint_ids=selected_endpoint_ids,
            selected_capability_values=selected_capability_values,
            security_profile=security_profile,
            data=self.data if self.is_bound else {},
        )

        try:
            plan_spec = self._plan_spec_from_cleaned_data(
                cleaned_data=cleaned_data,
                catalogue=selected_catalogue,
                endpoint_options=endpoint_options,
                selected_endpoint_ids=selected_endpoint_ids,
                selected_capability_values=selected_capability_values,
            )
            compiled_catalogue = resolve_catalogue(plan_spec.catalogue_key)
            compiled_plan = compile_test_plan(compiled_catalogue, plan_spec)
        except forms.ValidationError:
            raise
        except CatalogueError as error:
            raise forms.ValidationError(f"Plan validation failed: {error}", code="invalid_plan") from error

        selected_endpoint_ids = tuple(_endpoint_id(endpoint) for endpoint in plan_spec.implemented_endpoints)
        selected_capability_values = _capability_values_from_plan_spec(plan_spec)
        endpoint_options = _endpoint_options_for_catalogue(
            compiled_catalogue,
            selected_endpoint_ids,
            selected_capability_values=selected_capability_values,
        )
        self.runtime_input_prompts = _runtime_prompts_from_trace(compiled_plan, data=self.data if self.is_bound else {})
        self.preview = _build_preview(
            config=config,
            plan_spec=plan_spec,
            compiled_plan=compiled_plan,
            catalogue=compiled_catalogue,
            endpoint_options=endpoint_options,
            selected_endpoint_ids=selected_endpoint_ids,
            runtime_input_prompts=self.runtime_input_prompts,
        )
        return cleaned_data

    def _build_guided_config(self, *, cleaned_data: dict[str, object]) -> ModelBankConfig | None:
        """Build a validated config object from guided browser fields.

        Args:
            cleaned_data: Current cleaned-data dictionary from the form.

        Returns:
            Parsed and validated config built from guided fields, or ``None``
            when the submission did not provide enough guided data.
        """
        if not _has_guided_input(cleaned_data):
            self.add_error(
                "config_json",
                forms.ValidationError(
                    "Config JSON is required unless guided inputs supply a config.",
                    code="missing_config",
                ),
            )
            return None

        environment = _cleaned_optional_string(cleaned_data.get("guided_environment"))
        discovery_url = _cleaned_optional_string(cleaned_data.get("guided_discovery_url"))
        if environment is None:
            self.add_error("guided_environment", "Environment is required for guided config generation.")
        if discovery_url is None:
            self.add_error("guided_discovery_url", "Discovery URL is required for guided config generation.")
        if self.errors:
            return None

        raw_config: JsonObject = {
            "environment": environment or "",
            "discoveryUrl": discovery_url or "",
        }
        raw_oauth = _build_guided_oauth_object(cleaned_data)
        if raw_oauth is not None:
            raw_config["oauth"] = raw_oauth
        raw_fapi_signing = _build_guided_fapi_signing_object(cleaned_data)
        if raw_fapi_signing is not None:
            raw_config["fapiSigning"] = raw_fapi_signing

        self.generated_config_json = json.dumps(raw_config, indent=2, sort_keys=True)
        try:
            return parse_model_bank_config(raw_config, base_dir=Path.cwd())
        except ConfigError as error:
            self.add_error("config_json", f"Guided config validation failed: {error}")
            return None

    def _plan_spec_from_cleaned_data(
        self,
        *,
        cleaned_data: dict[str, object],
        catalogue: TestCatalogue,
        endpoint_options: tuple[CatalogueEndpointOption, ...],
        selected_endpoint_ids: tuple[str, ...],
        selected_capability_values: tuple[str, ...],
    ) -> TestPlanSpec:
        """Read an imported plan spec or build one from guided endpoint fields.

        Args:
            cleaned_data: Current form cleaned-data dictionary.
            catalogue: Catalogue selected by guided standard/version/API fields.
            endpoint_options: Endpoint options available for ``catalogue``.
            selected_endpoint_ids: Endpoint option ids submitted by the form.
            selected_capability_values: Endpoint capability checkbox values
                submitted by the form.

        Returns:
            Parsed plan spec.

        Raises:
            ValidationError: If JSON mode is malformed or endpoint selection is invalid.
            CatalogueError: If the plan spec fails schema validation.
        """
        raw_plan_spec_json = cleaned_data.get("plan_spec_json")
        if isinstance(raw_plan_spec_json, str) and raw_plan_spec_json.strip():
            raw_plan_spec = _load_json_object(raw_plan_spec_json, label="Plan spec JSON")
            return parse_test_plan_spec(raw_plan_spec)

        selected_options = [option for option in endpoint_options if option.id in selected_endpoint_ids]
        unknown_ids = set(selected_endpoint_ids) - {option.id for option in endpoint_options}
        if unknown_ids:
            unknown_list = ", ".join(sorted(unknown_ids))
            raise forms.ValidationError(f"Unknown implemented endpoint id(s): {unknown_list}", code="invalid_endpoint")
        _validate_selected_capability_values(
            endpoint_options=endpoint_options,
            selected_capability_values=selected_capability_values,
        )

        raw_plan_spec = _guided_plan_spec_object(
            catalogue=catalogue,
            security_profile=_security_profile_from_cleaned_data(cleaned_data),
            endpoint_options=tuple(selected_options),
            runtime_inputs=_runtime_inputs_from_prompts(self.runtime_input_prompts),
        )
        return parse_test_plan_spec(raw_plan_spec)


def build_plan_preview(
    *,
    config: ModelBankConfig,
    raw_plan_spec: JsonObject,
) -> PlanPreview:
    """Build a catalogue preview from already-decoded config and plan-spec objects.

    Args:
        config: Validated runtime configuration.
        raw_plan_spec: Decoded plan-spec JSON object.

    Returns:
        Complete plan preview with generated-test audit rows.

    Raises:
        CatalogueError: If plan-spec parsing, catalogue resolution, or
            compilation fails.
    """
    plan_spec = parse_test_plan_spec(raw_plan_spec)
    catalogue = resolve_catalogue(plan_spec.catalogue_key)
    compiled_plan = compile_test_plan(catalogue, plan_spec)
    selected_endpoint_ids = tuple(_endpoint_id(endpoint) for endpoint in plan_spec.implemented_endpoints)
    return _build_preview(
        config=config,
        plan_spec=plan_spec,
        compiled_plan=compiled_plan,
        catalogue=catalogue,
        endpoint_options=_endpoint_options_for_catalogue(catalogue, selected_endpoint_ids),
        selected_endpoint_ids=selected_endpoint_ids,
        runtime_input_prompts=_runtime_prompts_from_trace(compiled_plan, data={}),
    )


def guided_standard_choices() -> tuple[tuple[str, str], ...]:
    """Return guided-flow standards namespace choices.

    Returns:
        Distinct standard value/label pairs in catalogue order.
    """
    standards = tuple(dict.fromkeys(catalogue.key.standard for catalogue in supported_catalogues()))
    return tuple((standard, _standard_label(standard)) for standard in standards)


def guided_spec_version_choices() -> tuple[tuple[str, str], ...]:
    """Return guided-flow specification version choices.

    Returns:
        Distinct specification version value/label pairs in catalogue order.
    """
    versions = tuple(dict.fromkeys(catalogue.key.version for catalogue in supported_catalogues()))
    return tuple((version, version) for version in versions)


def guided_api_choices() -> tuple[tuple[str, str], ...]:
    """Return guided-flow API family choices.

    Returns:
        Distinct API family value/label pairs in deterministic order.
    """
    apis = tuple(dict.fromkeys(catalogue.key.api for catalogue in supported_catalogues()))
    return tuple((api, _guided_api_label(api)) for api in apis)


def guided_security_profile_choices() -> tuple[tuple[str, str], ...]:
    """Return security profile choices supported by the browser flow.

    Returns:
        Security profile value/label pairs in display order.
    """
    return (
        ("fapi1-advanced", "FAPI 1 Advanced"),
        ("fapi2", "FAPI 2"),
    )


def guided_flow_context(form: PlanBuilderForm) -> dict[str, object]:
    """Build template context for the structured guided browser flow.

    Args:
        form: Bound or unbound plan-builder form.

    Returns:
        Template context containing catalogue selector options, endpoint
        options, runtime prompts, and generated JSON previews.
    """
    selected_catalogue = _selected_catalogue_for_form(form)
    if form.preview is None:
        selected_endpoint_ids = tuple(_raw_form_values(form, "implemented_endpoint_ids"))
        selected_capability_values = tuple(_raw_form_values(form, "implemented_endpoint_capabilities"))
    else:
        selected_endpoint_ids = form.preview.selected_endpoint_ids
        selected_capability_values = _capability_values_from_plan_spec(form.preview.plan_spec)
    endpoint_options = _endpoint_options_for_catalogue(
        selected_catalogue,
        selected_endpoint_ids,
        selected_capability_values=selected_capability_values,
    )
    prompts = form.runtime_input_prompts
    if not prompts:
        prompts = _runtime_prompts_for_endpoint_selection(
            catalogue=selected_catalogue,
            selected_endpoint_ids=selected_endpoint_ids,
            selected_capability_values=selected_capability_values,
            security_profile=_security_profile_from_raw_form(form),
            data=form.data if form.is_bound else {},
        )
    return {
        "guided_standards": guided_standard_choices(),
        "guided_versions": guided_spec_version_choices(),
        "guided_api_options": guided_api_options(),
        "guided_security_profiles": guided_security_profile_choices(),
        "guided_endpoint_options": endpoint_options,
        "runtime_input_prompts": prompts,
        "generated_config_json": form.generated_config_json,
        "selected_catalogue": selected_catalogue,
        "selected_catalogue_label": _catalogue_label(selected_catalogue.key),
    }


def guided_api_options() -> tuple[GuidedApiOption, ...]:
    """Return version-aware API selector options for the guided flow.

    Returns:
        Guided API options in catalogue order with per-version scoping.
    """
    seen: set[tuple[str, str, str]] = set()
    options: list[GuidedApiOption] = []
    for catalogue in supported_catalogues():
        key = (catalogue.key.standard, catalogue.key.version, catalogue.key.api)
        if key in seen:
            continue
        seen.add(key)
        options.append(
            GuidedApiOption(
                standard=catalogue.key.standard,
                spec_version=catalogue.key.version,
                api=catalogue.key.api,
                label=_guided_api_label(catalogue.key.api),
            )
        )
    return tuple(options)


def _build_preview(
    *,
    config: ModelBankConfig,
    plan_spec: TestPlanSpec,
    compiled_plan: CompiledTestPlan,
    catalogue: TestCatalogue,
    endpoint_options: tuple[CatalogueEndpointOption, ...],
    selected_endpoint_ids: tuple[str, ...],
    runtime_input_prompts: tuple[RuntimeInputPrompt, ...],
) -> PlanPreview:
    """Build a template-ready plan preview.

    Args:
        config: Validated runtime configuration.
        plan_spec: Parsed plan spec used for compilation.
        compiled_plan: Compiler output for ``plan_spec``.
        catalogue: Catalogue used for compilation.
        endpoint_options: Endpoint options for the selected catalogue.
        selected_endpoint_ids: Endpoint ids selected by the participant.
        runtime_input_prompts: Runtime prompts derived from compiled tests.

    Returns:
        Complete plan preview for rendering and launch.
    """
    rows = _build_plan_rows(compiled_plan)
    launch_blockers = _launch_blockers(compiled_plan)
    return PlanPreview(
        config=config,
        plan_spec=plan_spec,
        compiled_plan=compiled_plan,
        catalogue_label=_catalogue_label(catalogue.key),
        endpoint_options=endpoint_options,
        selected_endpoint_ids=selected_endpoint_ids,
        runtime_input_prompts=runtime_input_prompts,
        generated_plan_spec_json=json.dumps(
            _plan_spec_to_json_object(plan_spec, compiled_plan=compiled_plan),
            indent=2,
            sort_keys=True,
        ),
        rows=rows,
        launch_supported=not launch_blockers,
        launch_blockers=launch_blockers,
        certification_eligible_by_selection=compiled_plan.certifying,
    )


def _load_json_object(raw_value: str, *, label: str) -> JsonObject:
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
    return cast(JsonObject, raw_object)


def _catalogue_from_cleaned_data(cleaned_data: dict[str, object]) -> TestCatalogue:
    """Resolve the guided catalogue selection from cleaned form data.

    Args:
        cleaned_data: Current form cleaned-data dictionary.

    Returns:
        Matching bundled catalogue, defaulting to the first bundled catalogue.
    """
    standard = _cleaned_optional_string(cleaned_data.get("guided_standard"))
    version = _cleaned_optional_string(cleaned_data.get("guided_spec_version"))
    api = _cleaned_optional_string(cleaned_data.get("guided_api"))
    if standard is None or version is None or api is None:
        return supported_catalogues()[0]
    try:
        return resolve_catalogue(CatalogueKey(standard=standard, version=version, api=api))
    except CatalogueError:
        return supported_catalogues()[0]


def _selected_catalogue_for_form(form: PlanBuilderForm) -> TestCatalogue:
    """Resolve the currently selected catalogue for a bound or unbound form.

    Args:
        form: Form whose raw selector values should be inspected.

    Returns:
        Matching bundled catalogue, defaulting to the first bundled catalogue.
    """
    standard = _raw_form_value(form, "guided_standard") or supported_catalogues()[0].key.standard
    version = _raw_form_value(form, "guided_spec_version") or supported_catalogues()[0].key.version
    api = _raw_form_value(form, "guided_api") or supported_catalogues()[0].key.api
    try:
        return resolve_catalogue(CatalogueKey(standard=standard, version=version, api=api))
    except CatalogueError:
        return supported_catalogues()[0]


def _security_profile_from_cleaned_data(cleaned_data: dict[str, object]) -> SecurityProfile:
    """Return the selected security profile.

    Args:
        cleaned_data: Current form cleaned-data dictionary.

    Returns:
        Selected profile, defaulting to ``"fapi1-advanced"``.
    """
    value = _cleaned_optional_string(cleaned_data.get("guided_security_profile"))
    return cast(SecurityProfile, value if value in {"fapi1-advanced", "fapi2"} else "fapi1-advanced")


def _security_profile_from_raw_form(form: forms.Form) -> SecurityProfile:
    """Return the selected security profile from raw form data.

    Args:
        form: Bound or unbound form whose selector should be inspected.

    Returns:
        Selected profile, defaulting to ``"fapi1-advanced"``.
    """
    value = _raw_form_value(form, "guided_security_profile")
    return cast(SecurityProfile, value if value in {"fapi1-advanced", "fapi2"} else "fapi1-advanced")


def _endpoint_options_for_catalogue(
    catalogue: TestCatalogue,
    selected_endpoint_ids: Iterable[str],
    *,
    selected_capability_values: Iterable[str] = (),
) -> tuple[CatalogueEndpointOption, ...]:
    """Build endpoint options for a catalogue.

    Args:
        catalogue: Catalogue whose endpoint applicability refs should be shown.
        selected_endpoint_ids: Endpoint option ids currently selected.
        selected_capability_values: Endpoint capability checkbox values
            currently selected.

    Returns:
        De-duplicated endpoint options grouped by resource label then path.
    """
    selected = set(selected_endpoint_ids)
    selected_capability_ids = _selected_capability_values_by_endpoint(
        selected_capability_values,
        strict=False,
    )
    refs: dict[EndpointRef, CatalogueEndpointOption] = {}
    for test_case in catalogue.test_cases:
        for endpoint_ref in test_case.applicability.endpoint_refs:
            if endpoint_ref in refs:
                continue
            endpoint = ImplementedEndpoint(
                method=endpoint_ref.method,
                path=endpoint_ref.path,
                resource_group=_resource_group_label(catalogue.key.api, endpoint_ref.path),
                operation_id=_operation_id(catalogue.key.api, endpoint_ref),
            )
            option_id = _endpoint_id(endpoint)
            selected_ids_for_endpoint = selected_capability_ids.get(option_id, set())
            refs[endpoint_ref] = CatalogueEndpointOption(
                id=option_id,
                standard=catalogue.key.standard,
                spec_version=catalogue.key.version,
                api=catalogue.key.api,
                method=endpoint.method,
                path=endpoint.path,
                resource_group=endpoint.resource_group,
                operation_id=endpoint.operation_id,
                capabilities=_capability_options_for_endpoint(
                    catalogue=catalogue,
                    endpoint_ref=endpoint_ref,
                    endpoint_id=option_id,
                    selected_capability_ids=selected_ids_for_endpoint,
                ),
                selected=option_id in selected,
            )
    return tuple(sorted(refs.values(), key=lambda option: (option.resource_group, option.path, option.method)))


def _capability_options_for_endpoint(
    *,
    catalogue: TestCatalogue,
    endpoint_ref: EndpointRef,
    endpoint_id: str,
    selected_capability_ids: set[str],
) -> tuple[EndpointCapabilityOption, ...]:
    """Build capability options for one endpoint option.

    Args:
        catalogue: Catalogue containing capability definitions.
        endpoint_ref: Endpoint reference owning the rendered options.
        endpoint_id: Browser endpoint option id.
        selected_capability_ids: Explicitly submitted capability ids for the
            endpoint.

    Returns:
        Capability options in catalogue definition order.
    """
    return tuple(
        EndpointCapabilityOption(
            value=_capability_selection_value(endpoint_id=endpoint_id, capability_id=capability.capability_id),
            endpoint_id=endpoint_id,
            capability_id=capability.capability_id,
            label=capability.label,
            description=capability.description,
            required=capability.required,
            selected=capability.required or capability.capability_id in selected_capability_ids,
        )
        for capability in catalogue.capabilities
        if endpoint_ref in capability.endpoint_refs
    )


def _runtime_prompts_for_endpoint_selection(
    *,
    catalogue: TestCatalogue,
    selected_endpoint_ids: Iterable[str],
    selected_capability_values: Iterable[str],
    security_profile: SecurityProfile,
    data: Mapping[str, object],
) -> tuple[RuntimeInputPrompt, ...]:
    """Build runtime prompts for currently selected endpoints and capabilities.

    Args:
        catalogue: Catalogue selected by the form.
        selected_endpoint_ids: Endpoint option ids selected by the participant.
        selected_capability_values: Capability checkbox values selected by the
            participant.
        security_profile: Security profile selected by the participant.
        data: Raw form data used to redisplay submitted runtime values.

    Returns:
        De-duplicated runtime input prompts in catalogue order.
    """
    selected_ids = set(selected_endpoint_ids)
    selected_options = _endpoint_options_for_catalogue(
        catalogue,
        selected_ids,
        selected_capability_values=selected_capability_values,
    )
    selected_refs = _selected_refs_from_options(selected_options, selected_ids=selected_ids)
    selected_capabilities_by_ref = _selected_capability_ids_by_ref(selected_options, selected_ids=selected_ids)
    requirements: list[RuntimeInputRequirement] = []
    seen: dict[str, RuntimeInputRequirement] = {}
    for test_case in catalogue.test_cases:
        if not test_case.applicability.security_profiles.applies_to(security_profile):
            continue
        endpoint_refs = set(test_case.applicability.endpoint_refs)
        if endpoint_refs and not endpoint_refs.issubset(selected_refs):
            continue
        if not _test_case_capabilities_are_selected(test_case, selected_capabilities_by_ref):
            continue
        for requirement in test_case.runtime_input_requirements:
            existing = seen.get(requirement.input_id)
            if existing is None:
                seen[requirement.input_id] = requirement
                requirements.append(requirement)
                continue
            if existing != requirement:
                raise forms.ValidationError(
                    f"Runtime input '{requirement.input_id}' has conflicting catalogue requirements",
                    code="invalid_runtime_input",
                )
    return tuple(_runtime_prompt(requirement, data=data) for requirement in requirements)


def _selected_refs_from_options(
    endpoint_options: Iterable[CatalogueEndpointOption],
    *,
    selected_ids: set[str],
) -> set[EndpointRef]:
    """Return selected endpoint refs from rendered endpoint options.

    Args:
        endpoint_options: Endpoint options built for the current catalogue.
        selected_ids: Selected endpoint option ids.

    Returns:
        Exact endpoint refs selected by the participant.
    """
    return {
        EndpointRef(method=cast(HttpMethod, option.method), path=option.path)
        for option in endpoint_options
        if option.id in selected_ids
    }


def _selected_capability_ids_by_ref(
    endpoint_options: Iterable[CatalogueEndpointOption],
    *,
    selected_ids: set[str],
) -> dict[EndpointRef, set[str]]:
    """Return selected capability ids keyed by endpoint ref.

    Args:
        endpoint_options: Endpoint options with capability selection state.
        selected_ids: Selected endpoint option ids.

    Returns:
        Selected required and optional capability ids by endpoint reference.
    """
    selected: dict[EndpointRef, set[str]] = {}
    for option in endpoint_options:
        if option.id not in selected_ids:
            continue
        endpoint_ref = EndpointRef(method=cast(HttpMethod, option.method), path=option.path)
        selected[endpoint_ref] = {capability.capability_id for capability in option.capabilities if capability.selected}
    return selected


def _test_case_capabilities_are_selected(
    test_case: CatalogueTestCase,
    selected_capabilities_by_ref: Mapping[EndpointRef, set[str]],
) -> bool:
    """Return whether a catalogue test case's required capabilities are selected.

    Args:
        test_case: Catalogue test case being evaluated.
        selected_capabilities_by_ref: Selected capabilities by endpoint ref.

    Returns:
        True when every required capability is selected for at least one of the
        test case's endpoint refs.
    """
    required_ids = set(test_case.applicability.required_capability_ids)
    if not required_ids:
        return True
    selected_ids: set[str] = set()
    for endpoint_ref in test_case.applicability.endpoint_refs:
        selected_ids.update(selected_capabilities_by_ref.get(endpoint_ref, set()))
    return required_ids.issubset(selected_ids)


def _runtime_prompts_from_trace(
    compiled_plan: CompiledTestPlan,
    *,
    data: Mapping[str, object],
) -> tuple[RuntimeInputPrompt, ...]:
    """Build runtime prompts from compiler traceability.

    Args:
        compiled_plan: Compiled plan whose runtime snapshot should be rendered.
        data: Raw form data used to redisplay submitted runtime values.

    Returns:
        Runtime input prompts in compiler trace order.
    """
    return tuple(
        RuntimeInputPrompt(
            input_id=trace.input_id,
            name=f"{_RUNTIME_INPUT_PREFIX}{trace.input_id}",
            label=trace.input_id,
            input_type=trace.input_type,
            required=trace.required,
            sensitive=trace.sensitive,
            value=_raw_mapping_value(data, f"{_RUNTIME_INPUT_PREFIX}{trace.input_id}"),
        )
        for trace in compiled_plan.traceability.runtime_input_snapshot
    )


def _runtime_prompt(requirement: RuntimeInputRequirement, *, data: Mapping[str, object]) -> RuntimeInputPrompt:
    """Convert a catalogue runtime requirement into a browser prompt.

    Args:
        requirement: Catalogue runtime input requirement.
        data: Raw form data used to redisplay submitted values.

    Returns:
        Template-friendly runtime input prompt.
    """
    name = f"{_RUNTIME_INPUT_PREFIX}{requirement.input_id}"
    return RuntimeInputPrompt(
        input_id=requirement.input_id,
        name=name,
        label=requirement.label,
        input_type=requirement.input_type,
        required=requirement.required,
        sensitive=requirement.sensitive,
        value=_raw_mapping_value(data, name),
    )


def _runtime_inputs_from_prompts(prompts: Iterable[RuntimeInputPrompt]) -> JsonObject:
    """Build runtime input JSON from rendered prompts.

    Args:
        prompts: Runtime prompts with submitted string values.

    Returns:
        Runtime input mapping suitable for plan-spec parsing.

    Raises:
        ValidationError: If a typed prompt value is malformed JSON, number, or boolean input.
    """
    runtime_inputs: JsonObject = {}
    for prompt in prompts:
        raw_value = prompt.value.strip()
        if not raw_value:
            continue
        if prompt.input_type == "json":
            runtime_inputs[prompt.input_id] = _load_json_value(raw_value, label=prompt.label)
        elif prompt.input_type == "number":
            runtime_inputs[prompt.input_id] = _parse_number(raw_value, label=prompt.label)
        elif prompt.input_type == "boolean":
            runtime_inputs[prompt.input_id] = _parse_boolean(raw_value, label=prompt.label)
        else:
            runtime_inputs[prompt.input_id] = raw_value
    return runtime_inputs


def _load_json_value(raw_value: str, *, label: str) -> JsonValue:
    """Decode a runtime JSON value.

    Args:
        raw_value: JSON text submitted through a runtime input prompt.
        label: Human-readable prompt label for validation messages.

    Returns:
        Decoded JSON value.

    Raises:
        ValidationError: If the text is malformed JSON.
    """
    try:
        return cast(JsonValue, json.loads(raw_value))
    except json.JSONDecodeError as error:
        raise forms.ValidationError(f"{label} must be valid JSON: {error.msg}", code="invalid_runtime_json") from error


def _parse_number(raw_value: str, *, label: str) -> int | float:
    """Parse a runtime number value.

    Args:
        raw_value: Submitted number text.
        label: Human-readable prompt label for validation messages.

    Returns:
        Parsed integer or floating-point number.

    Raises:
        ValidationError: If the text is not a JSON number.
    """
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as error:
        raise forms.ValidationError(f"{label} must be a JSON number", code="invalid_runtime_number") from error
    if isinstance(parsed, bool) or not isinstance(parsed, int | float):
        raise forms.ValidationError(f"{label} must be a JSON number", code="invalid_runtime_number")
    return parsed


def _parse_boolean(raw_value: str, *, label: str) -> bool:
    """Parse a runtime boolean value.

    Args:
        raw_value: Submitted boolean text.
        label: Human-readable prompt label for validation messages.

    Returns:
        Parsed boolean value.

    Raises:
        ValidationError: If the text is not ``true`` or ``false``.
    """
    normalized = raw_value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise forms.ValidationError(f"{label} must be true or false", code="invalid_runtime_boolean")


def _guided_plan_spec_object(
    *,
    catalogue: TestCatalogue,
    security_profile: SecurityProfile,
    endpoint_options: tuple[CatalogueEndpointOption, ...],
    runtime_inputs: JsonObject,
) -> JsonObject:
    """Build the exportable plan-spec JSON object from guided fields.

    Args:
        catalogue: Catalogue selected by the participant.
        security_profile: Security profile selected by the participant.
        endpoint_options: Implemented endpoints selected by the participant.
        runtime_inputs: Runtime input values supplied by the participant.

    Returns:
        Plan-spec JSON object accepted by :func:`parse_test_plan_spec`.
    """
    return {
        "schemaVersion": "v1",
        "catalogue": {
            "standard": catalogue.key.standard,
            "version": catalogue.key.version,
            "api": catalogue.key.api,
        },
        "securityProfile": security_profile,
        "implementedEndpoints": [
            {
                "method": option.method,
                "path": option.path,
                "resourceGroup": option.resource_group,
                **(
                    {
                        "capabilities": [
                            capability.capability_id
                            for capability in option.capabilities
                            if capability.selected and not capability.required
                        ]
                    }
                    if any(capability.selected and not capability.required for capability in option.capabilities)
                    else {}
                ),
                **({"operationId": option.operation_id} if option.operation_id is not None else {}),
            }
            for option in endpoint_options
        ],
        "runtimeInputs": runtime_inputs,
    }


def _plan_spec_to_json_object(plan_spec: TestPlanSpec, *, compiled_plan: CompiledTestPlan) -> JsonObject:
    """Convert a parsed plan spec back to exportable JSON.

    Args:
        plan_spec: Parsed plan spec.
        compiled_plan: Compiled plan whose runtime trace identifies sensitive
            values that must not be exported.

    Returns:
        JSON object preserving non-secret runtime references and selected endpoints.
    """
    return {
        "schemaVersion": plan_spec.schema_version,
        "catalogue": {
            "standard": plan_spec.catalogue_key.standard,
            "version": plan_spec.catalogue_key.version,
            "api": plan_spec.catalogue_key.api,
        },
        "securityProfile": plan_spec.security_profile,
        "implementedEndpoints": [
            {
                "method": endpoint.method,
                "path": endpoint.path,
                "resourceGroup": endpoint.resource_group,
                **({"capabilities": list(endpoint.capability_ids)} if endpoint.capability_ids else {}),
                **({"operationId": endpoint.operation_id} if endpoint.operation_id is not None else {}),
            }
            for endpoint in plan_spec.implemented_endpoints
        ],
        "runtimeInputs": _exportable_runtime_inputs(plan_spec, compiled_plan=compiled_plan),
        **(
            {"deselectedTestCaseIds": list(plan_spec.deselected_test_case_ids)}
            if plan_spec.deselected_test_case_ids
            else {}
        ),
        **(
            {
                "assertionOverrides": [
                    {
                        "testCaseId": override.test_case_id,
                        "assertionId": override.assertion_id,
                        "reason": override.reason,
                    }
                    for override in plan_spec.assertion_overrides
                ]
            }
            if plan_spec.assertion_overrides
            else {}
        ),
    }


def _build_plan_rows(compiled_plan: CompiledTestPlan) -> tuple[PlanTestCaseRow, ...]:
    """Build rich read-only preview rows for generated catalogue cases.

    Args:
        compiled_plan: Compiled catalogue plan to render.

    Returns:
        Template-ready generated plan rows in execution order.
    """
    decisions = {decision.test_case_id: decision for decision in compiled_plan.traceability.applicability_decisions}
    selected_capabilities = _selected_capability_labels_by_id(compiled_plan)
    rows: list[PlanTestCaseRow] = []
    for test_case in compiled_plan.test_cases:
        source, source_detail = _test_case_source(
            test_case,
            decisions[test_case.test_case_id],
            selected_capabilities,
        )
        rows.append(
            PlanTestCaseRow(
                id=test_case.test_case_id,
                name=test_case.name,
                role=test_case.role,
                phase=_test_case_phase(test_case),
                source=source,
                source_detail=source_detail,
                mandatory=test_case.mandatory,
                dependencies=test_case.dependencies,
                request_count=len(test_case.request_steps),
                assertion_count=len(test_case.assertions),
                runtime_requirements=tuple(
                    PlanRuntimeRequirement(
                        input_id=requirement.input_id,
                        label=requirement.label,
                        required=requirement.required,
                        sensitive=requirement.sensitive,
                    )
                    for requirement in test_case.runtime_input_requirements
                ),
                request_step_ids=tuple(request_step.step_id for request_step in test_case.request_steps),
                assertion_summaries=tuple(assertion.description for assertion in test_case.assertions),
                compliance_scope=test_case.compliance_scope,
            )
        )
    return tuple(rows)


def _selected_capability_labels_by_id(compiled_plan: CompiledTestPlan) -> dict[str, tuple[str, bool]]:
    """Return selected capability labels and required flags by capability id.

    Args:
        compiled_plan: Compiled plan whose traceability carries capability selections.

    Returns:
        Mapping from capability id to display label and required flag.
    """
    return {
        capability.capability_id: (capability.label, capability.required)
        for capability in compiled_plan.traceability.selected_capabilities
    }


def _test_case_source(
    test_case: CatalogueTestCase,
    decision: ApplicabilityDecision,
    selected_capabilities: Mapping[str, tuple[str, bool]],
) -> tuple[str, str]:
    """Describe why a generated test case appears in the compiled preview.

    Args:
        test_case: Generated catalogue test case.
        decision: Compiler traceability decision for the test case.
        selected_capabilities: Selected capability labels and required flags by id.

    Returns:
        Pair of short source label and detailed source context.
    """
    if decision.dependency_of and decision.reason == "included as dependency":
        return "Automatic dependency", f"Required by {', '.join(decision.dependency_of)}"
    if not test_case.applicability.endpoint_refs:
        if test_case.role in {"setup", "token", "consent"}:
            return "Setup coverage", "Generated automatically for the selected security profile"
        if test_case.role == "security":
            return "Security coverage", "Generated automatically for the selected security profile"
        return "Catalogue coverage", "Generated automatically for the selected security profile"
    if test_case.applicability.required_capability_ids:
        labels = [
            selected_capabilities.get(capability_id, (capability_id, False))
            for capability_id in test_case.applicability.required_capability_ids
        ]
        source = "Required capability" if all(required for _, required in labels) else "Selected capability"
        return source, ", ".join(label for label, _ in labels)
    endpoints = ", ".join(f"{endpoint.method} {endpoint.path}" for endpoint in test_case.applicability.endpoint_refs)
    return "Selected endpoint", endpoints


def _test_case_phase(test_case: CatalogueTestCase) -> str:
    """Return the preview phase for a generated catalogue test case.

    Args:
        test_case: Generated catalogue test case.

    Returns:
        ``"setup"`` for setup/security/token cases, otherwise ``"execution"``.
    """
    return "setup" if test_case.role in {"setup", "security", "token"} else "execution"


def _exportable_runtime_inputs(plan_spec: TestPlanSpec, *, compiled_plan: CompiledTestPlan) -> JsonObject:
    """Return non-sensitive runtime inputs for plan-spec export.

    Args:
        plan_spec: Parsed plan spec containing submitted runtime inputs.
        compiled_plan: Compiled plan whose trace marks sensitive inputs.

    Returns:
        Runtime input mapping with sensitive values omitted.
    """
    sensitive_input_ids = {
        trace.input_id for trace in compiled_plan.traceability.runtime_input_snapshot if trace.sensitive
    }
    return {
        input_id: value for input_id, value in plan_spec.runtime_inputs.items() if input_id not in sensitive_input_ids
    }


def _capability_selection_value(*, endpoint_id: str, capability_id: str) -> str:
    """Return the form value for an endpoint capability checkbox.

    Args:
        endpoint_id: Endpoint option id owning the capability.
        capability_id: Catalogue capability id selected under the endpoint.

    Returns:
        Stable compound checkbox value.
    """
    return f"{endpoint_id}{_CAPABILITY_VALUE_SEPARATOR}{capability_id}"


def _capability_values_from_plan_spec(plan_spec: TestPlanSpec) -> tuple[str, ...]:
    """Return endpoint capability checkbox values selected by a plan spec.

    Args:
        plan_spec: Parsed plan spec whose endpoint capability declarations
            should be reflected in the guided UI.

    Returns:
        Compound endpoint/capability checkbox values in plan-spec order.
    """
    values: list[str] = []
    for endpoint in plan_spec.implemented_endpoints:
        endpoint_id = _endpoint_id(endpoint)
        values.extend(
            _capability_selection_value(endpoint_id=endpoint_id, capability_id=capability_id)
            for capability_id in endpoint.capability_ids
        )
    return tuple(values)


def _selected_capability_values_by_endpoint(
    values: Iterable[str],
    *,
    strict: bool,
) -> dict[str, set[str]]:
    """Decode selected capability values into endpoint-scoped ids.

    Args:
        values: Compound endpoint/capability checkbox values.
        strict: Whether malformed values should raise a form validation error.

    Returns:
        Mapping of endpoint option ids to selected capability ids.

    Raises:
        ValidationError: If ``strict`` is true and a submitted value is malformed.
    """
    selected: dict[str, set[str]] = {}
    for value in values:
        if _CAPABILITY_VALUE_SEPARATOR not in value:
            if strict:
                raise forms.ValidationError("Endpoint capability values are malformed", code="invalid_capability")
            continue
        endpoint_id, capability_id = value.split(_CAPABILITY_VALUE_SEPARATOR, maxsplit=1)
        if not endpoint_id or not capability_id:
            if strict:
                raise forms.ValidationError("Endpoint capability values are malformed", code="invalid_capability")
            continue
        selected.setdefault(endpoint_id, set()).add(capability_id)
    return selected


def _validate_selected_capability_values(
    *,
    endpoint_options: tuple[CatalogueEndpointOption, ...],
    selected_capability_values: tuple[str, ...],
) -> None:
    """Validate selected capability checkbox values against rendered options.

    Args:
        endpoint_options: Endpoint options available for the selected catalogue.
        selected_capability_values: Submitted endpoint capability checkbox values.

    Raises:
        ValidationError: If a capability belongs to an unknown endpoint or is not
            available on the submitted endpoint.
    """
    selected_by_endpoint = _selected_capability_values_by_endpoint(selected_capability_values, strict=True)
    options_by_id = {option.id: option for option in endpoint_options}
    for endpoint_id, capability_ids in selected_by_endpoint.items():
        option = options_by_id.get(endpoint_id)
        if option is None:
            raise forms.ValidationError(
                f"Unknown endpoint capability endpoint id: {endpoint_id}",
                code="invalid_capability",
            )
        if not option.selected:
            raise forms.ValidationError(
                f"Capability selected for unselected endpoint: {option.method} {option.path}",
                code="invalid_capability",
            )
        available_ids = {capability.capability_id for capability in option.capabilities}
        unknown_ids = sorted(capability_ids - available_ids)
        if unknown_ids:
            unknown_list = ", ".join(unknown_ids)
            raise forms.ValidationError(
                f"Unknown capability id(s) for {option.method} {option.path}: {unknown_list}",
                code="invalid_capability",
            )


def _cleaned_endpoint_ids(value: object) -> tuple[str, ...]:
    """Read endpoint ids from cleaned form data.

    Args:
        value: Cleaned value from ``EndpointIdListField``.

    Returns:
        Tuple of endpoint ids preserving submitted order.

    Raises:
        TypeError: If a non-string endpoint id reaches cleaned data.
    """
    if value is None:
        return ()
    if isinstance(value, list) and all(isinstance(endpoint_id, str) for endpoint_id in value):
        return tuple(value)
    raise TypeError("Cleaned endpoint ids must be a list of strings")


def _cleaned_optional_string(value: object) -> str | None:
    """Normalise an optional string from form cleaned data.

    Args:
        value: Raw cleaned-data value from a Django form field.

    Returns:
        Stripped string value, or ``None`` when the value is blank.
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _has_guided_input(cleaned_data: dict[str, object]) -> bool:
    """Return whether the submission supplied any guided-form values.

    Args:
        cleaned_data: Current form cleaned-data dictionary.

    Returns:
        ``True`` when at least one guided field contains a non-empty value.
    """
    return any(
        _cleaned_optional_string(cleaned_data.get(field_name)) is not None
        for field_name in (
            "guided_environment",
            "guided_discovery_url",
            "guided_client_id",
            "guided_redirect_uri",
            "guided_authorization_endpoint",
            "guided_resource_base_url",
            "guided_signing_certificate_path_root",
            "guided_signing_certificate_path",
            "guided_signing_private_key_path",
            "guided_signing_kid",
            "guided_signing_client_assertion_issuer",
            "guided_signing_client_assertion_subject",
            "guided_signing_token_endpoint_auth_method",
        )
    )


def _build_guided_oauth_object(cleaned_data: dict[str, object]) -> JsonObject | None:
    """Build an ``oauth`` JSON object from guided browser fields.

    Args:
        cleaned_data: Current form cleaned-data dictionary.

    Returns:
        Guided OAuth JSON object, or ``None`` when all guided OAuth fields are blank.
    """
    field_mapping = {
        "clientId": "guided_client_id",
        "redirectUri": "guided_redirect_uri",
        "authorizationEndpoint": "guided_authorization_endpoint",
        "resourceBaseUrl": "guided_resource_base_url",
    }
    raw_oauth: JsonObject = {}
    for config_key, field_name in field_mapping.items():
        value = _cleaned_optional_string(cleaned_data.get(field_name))
        if value is not None:
            raw_oauth[config_key] = value
    return raw_oauth or None


def _build_guided_fapi_signing_object(cleaned_data: dict[str, object]) -> JsonObject | None:
    """Build an ``fapiSigning`` JSON object from guided browser fields.

    Args:
        cleaned_data: Current form cleaned-data dictionary.

    Returns:
        Guided signing JSON object, or ``None`` when all signing fields are blank.
    """
    field_mapping = {
        "certificatePathRoot": "guided_signing_certificate_path_root",
        "signingCertificatePath": "guided_signing_certificate_path",
        "signingPrivateKeyPath": "guided_signing_private_key_path",  # pragma: allowlist secret
        "kid": "guided_signing_kid",
        "clientAssertionIssuer": "guided_signing_client_assertion_issuer",
        "clientAssertionSubject": "guided_signing_client_assertion_subject",
        "tokenEndpointAuthMethod": "guided_signing_token_endpoint_auth_method",
    }
    raw_fapi_signing: JsonObject = {}
    for config_key, field_name in field_mapping.items():
        value = _cleaned_optional_string(cleaned_data.get(field_name))
        if value is not None:
            raw_fapi_signing[config_key] = value
    return raw_fapi_signing or None


def _raw_form_value(form: forms.Form, field_name: str) -> str:
    """Return the current raw string value for a bound or unbound form field.

    Args:
        form: Django form whose raw field value should be read.
        field_name: Form field name to read.

    Returns:
        Raw string value suitable for selector comparisons and template state.
    """
    if form.is_bound:
        return _raw_mapping_value(form.data, field_name)
    initial_value = form.initial.get(field_name, "")
    return initial_value if isinstance(initial_value, str) else ""


def _raw_form_values(form: forms.Form, field_name: str) -> list[str]:
    """Return current raw list values for a repeated form field.

    Args:
        form: Django form whose raw field values should be read.
        field_name: Repeated field name to read.

    Returns:
        Raw submitted values, or an empty list for unbound forms.
    """
    if not form.is_bound:
        return []
    if hasattr(form.data, "getlist"):
        values = form.data.getlist(field_name)
        return [value for value in values if isinstance(value, str)]
    value = form.data.get(field_name)
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return [value] if isinstance(value, str) and value else []


def _raw_mapping_value(data: Mapping[str, object], key: str) -> str:
    """Return a string value from a raw mapping.

    Args:
        data: Mapping submitted by a browser or test client.
        key: Field name to read.

    Returns:
        String value, or an empty string when absent/non-string.
    """
    value = data.get(key, "")
    return value if isinstance(value, str) else ""


def _endpoint_id(endpoint: ImplementedEndpoint) -> str:
    """Return the stable form id for an implemented endpoint.

    Args:
        endpoint: Implemented endpoint selected by the participant.

    Returns:
        Stable endpoint id derived from method and path.
    """
    digest = sha256(f"{endpoint.method} {endpoint.path}".encode()).hexdigest()[:12]
    return f"endpoint-{digest}"


def _resource_group_label(api: str, path: str) -> str:
    """Infer a participant-facing resource group from an endpoint path.

    Args:
        api: API family selected by the catalogue.
        path: Standards endpoint path.

    Returns:
        Human-readable group label.
    """
    segments = [segment for segment in path.split("/") if segment and not segment.startswith("{")]
    api_segment = {"ais": "aisp", "pis": "pisp", "cbpii": "cbpii", "vrp": "vrp", "cvrp": "cvrp"}.get(api)
    if api_segment in segments:
        index = segments.index(api_segment)
        if index + 1 < len(segments):
            return _title_segment(segments[index + 1])
    if segments:
        return _title_segment(segments[-1])
    return _guided_api_label(api)


def _title_segment(value: str) -> str:
    """Convert a path segment into a title-cased display label.

    Args:
        value: Raw standards path segment.

    Returns:
        Human-readable label.
    """
    return " ".join(word.capitalize() for word in value.split("-"))


def _operation_id(api: str, endpoint_ref: EndpointRef) -> str:
    """Build a stable operation id for generated plan-spec exports.

    Args:
        api: API family selected by the catalogue.
        endpoint_ref: Endpoint method/path reference.

    Returns:
        Operation id derived from API, method, and path segments.
    """
    suffix = endpoint_ref.path.strip("/").replace("{", "").replace("}", "").replace("/", "-")
    return f"{api}-{endpoint_ref.method.lower()}-{suffix}".replace("--", "-")


def _launch_blockers(compiled_plan: CompiledTestPlan) -> tuple[str, ...]:
    """Return reasons the browser UI must not launch this compiled plan.

    Args:
        compiled_plan: Compiled plan being previewed.

    Returns:
        Human-readable launch blockers. Empty when browser launch is supported.
    """
    if not compiled_plan.test_cases:
        return ("Select at least one implemented endpoint before launch.",)
    return ()


def _catalogue_label(key: CatalogueKey) -> str:
    """Return the browser label for a catalogue key.

    Args:
        key: Catalogue key to label.

    Returns:
        Human-readable catalogue label.
    """
    return f"{_standard_label(key.standard)} {key.version} {_guided_api_label(key.api)}"


def _standard_label(standard: str) -> str:
    """Return the participant-facing label for a standard namespace.

    Args:
        standard: Standards namespace value from a catalogue key.

    Returns:
        Human-readable standards label.
    """
    labels = {"open-banking": "Open Banking"}
    return labels.get(standard, standard)


def _guided_api_label(api: str) -> str:
    """Return the participant-facing label for an API family.

    Args:
        api: API family value from the catalogue registry.

    Returns:
        Short uppercase label used in the browser selector.
    """
    labels = {
        "ais": "AIS",
        "pis": "PIS",
        "cbpii": "CBPII",
        "vrp": "VRP",
        "cvrp": "cVRP",
    }
    return labels.get(api, api)
