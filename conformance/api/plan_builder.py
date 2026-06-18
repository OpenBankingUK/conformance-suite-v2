"""Plan-builder forms and presenters for participant-facing browser workflows."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast

from django import forms
from django.core.files.uploadedfile import UploadedFile
from django.utils.datastructures import MultiValueDict

from conformance.auth_metadata import AuthBundleDeclaration
from conformance.environment_capabilities import (
    PsuMode,
    evaluate_suite_environment_support,
    make_custom_environment_reference,
)
from conformance.json_types import JsonValue
from conformance.manifest import (
    FormBody,
    GeneratedRequestObject,
    JsonBody,
    Manifest,
    ManifestError,
    ManifestStep,
    PsuAuthorizationStep,
    StepPhase,
    load_manifest_from_object,
)
from conformance.model_bank_config import (
    ConfigError,
    ModelBankConfig,
    TokenEndpointClientAuthMode,
    parse_model_bank_config,
)
from conformance.openapi_plan_metadata import StepTreeNode, build_plan_tree
from conformance.suite_catalog import SuiteCatalogError, SuiteMetadata, list_supported_suites, resolve_suite
from conformance.test_plan import (
    PlanTestValueContext,
    TestPlan,
    TestPlanEntry,
    TestValueProfileSource,
    build_plan_test_value_context,
)

StepKind = Literal["http", "psu-authorization"]
"""Step kind labels displayed by the participant plan-builder UI."""

SelectionMode = Literal["deselect", "select"]
"""Modes used by browser forms to describe participant step selection."""


@dataclass(frozen=True)
class GuidedApiOption:
    """One guided-flow API option available for a specification version.

    Attributes:
        spec_version: Specification version that exposes this API family.
        api: API family value posted back by the browser form.
        label: Human-readable API family label shown in the browser UI.
    """

    spec_version: str
    api: str
    label: str


@dataclass(frozen=True)
class GuidedSuiteOption:
    """One guided-flow suite option rendered in the browser selector.

    Attributes:
        standard: Suite standard value written into generated config JSON.
        spec_version: Specification version value posted by the browser form.
        profile: Security profile value written into generated config JSON.
        api: API family value posted by the browser form.
        suite: Suite identifier value posted by the browser form.
        label: Human-readable suite label shown in the browser UI.
        description: Short scope note shown beside the selector.
        prompts_oauth: Whether the guided form should show OAuth fields.
        prompts_intent_id: Whether the guided form should show the intent id field.
        prompts_resource_base_url: Whether the guided form should show the resource base URL field.
        prompts_signing: Whether the guided form should show FAPI signing fields.
    """

    standard: str
    spec_version: str
    profile: str
    api: str
    suite: str
    label: str
    description: str
    prompts_oauth: bool
    prompts_intent_id: bool
    prompts_resource_base_url: bool
    prompts_signing: bool


@dataclass(frozen=True)
class GuidedModelBankOption:
    """One known model-bank environment available in the guided flow.

    Attributes:
        value: Stable option value posted by the browser form.
        label: Human-readable model-bank label shown in the browser UI.
        environment: Environment value written into generated config JSON.
        discovery_url: OpenID Provider discovery URL written into generated
            config JSON.
    """

    value: str
    label: str
    environment: str
    discovery_url: str


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
        conditional: Whether the step is a conditional row driven by test-value profile availability.
        condition_id: Optional machine-readable condition identifier from the manifest.
        condition_label: Optional human-readable condition label for badge display.
        required_test_value_keys: Test-value key names that the condition requires.
        missing_test_value_keys: Required keys absent from the effective test-value profile.
            Non-empty means the default plan deselected this step due to missing values.
        test_value_profile_id: The effective test-value profile id used at plan build time.
            ``None`` when the manifest declares no ``testValueProfiles``.
        test_value_profile_source: Whether the effective profile was the manifest default
            or participant-overridden.  ``None`` when no profiles are declared.
        test_value_override_keys: Test-value key names supplied by participant overrides.
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
    conditional: bool = False
    condition_id: str | None = None
    condition_label: str | None = None
    required_test_value_keys: tuple[str, ...] = ()
    missing_test_value_keys: tuple[str, ...] = ()
    test_value_profile_id: str | None = None
    test_value_profile_source: TestValueProfileSource | None = None
    test_value_override_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanStepAuthRequirement:
    """Auth bundle mapping for one selected manifest step.

    Attributes:
        step_id: Selected manifest step id requiring an access token.
        bundle_id: Stable auth bundle identifier consumed by the step.
    """

    step_id: str
    bundle_id: str


@dataclass(frozen=True)
class PlanAuthBundle:
    """Consent/token requirement bundle required by selected steps.

    Attributes:
        id: Stable identifier derived from token source and effective requirements.
        token_step_id: Step id that mints the access token consumed by this bundle.
        consent_step_id: Step id that creates the consent backing this token,
            or ``None`` when no consent-creation step is detected.
        required_scopes: Required OAuth scopes for this bundle.
        required_ob_permissions: Required Open Banking consent permissions for this bundle.
        consuming_step_ids: Selected step ids that consume this bundle.
    """

    id: str
    token_step_id: str
    consent_step_id: str | None
    required_scopes: tuple[str, ...]
    required_ob_permissions: tuple[str, ...]
    consuming_step_ids: tuple[str, ...]


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
        launch_blockers: Human-readable reasons launch is disabled, including
            hard environment capability blockers for known unsupported
            suite/auth/environment combinations.
        certification_eligible_by_selection: Whether the submitted selection preserves certification eligibility.
        auth_inventory: Stable consent/token bundles required by selected
            token-protected steps.
        step_auth_requirements: Selected step to auth-bundle mappings.
        capability_warnings: Conservative warnings from environment capability
            evaluation where compatibility is unknown (e.g. undeclared custom
            environment capabilities).  These do not block launch but are
            surfaced to the participant for awareness.
        tree_nodes: Hierarchical tree nodes derived from OpenAPI standards
            documents and manifest step analysis, for visual tree selection
            rendering. Empty when ``suite_metadata`` is ``None`` or the suite
            has no bundled OpenAPI document.
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
    auth_inventory: tuple[PlanAuthBundle, ...]
    step_auth_requirements: tuple[PlanStepAuthRequirement, ...]
    capability_warnings: tuple[str, ...]
    tree_nodes: tuple[StepTreeNode, ...]


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
        guided_model_bank: Structured model-bank example selector used to
            populate guided environment and discovery values.
        guided_environment: Structured environment input used when building a
            config from guided browser fields instead of pasted JSON.
        guided_discovery_url: Structured discovery URL input used by the
            guided browser flow.
        guided_spec_version: Structured selector for the target spec version.
        guided_api: Structured selector for the target API family.
        guided_suite: Structured selector for the bundled suite identifier.
        guided_client_id: Structured OAuth client id prompt used by guided
            suites that require OAuth input.
        guided_redirect_uri: Structured OAuth redirect URI prompt used by
            guided suites that require OAuth input.
        guided_authorization_endpoint: Optional structured authorization
            endpoint override prompt for legacy registrations.
        guided_open_banking_intent_id: Optional structured Open Banking intent
            id prompt for starter suites.
        guided_resource_base_url: Optional structured protected-resource base
            URL prompt for AIS suites.
        guided_signing_certificate_path_root: Optional structured root path for
            signing certificate material.
        guided_signing_certificate_path: Optional structured signing
            certificate path prompt.
        guided_signing_private_key_path: Optional structured signing private
            key path prompt.
        guided_signing_kid: Optional structured signing key id prompt.
        guided_signing_client_assertion_issuer: Optional structured client
            assertion issuer prompt.
        guided_signing_client_assertion_subject: Optional structured client
            assertion subject prompt.
        guided_signing_token_endpoint_auth_method: Optional structured token
            endpoint auth-method prompt.
        selection_mode: Choice controlling whether selected or deselected ids drive the submitted plan.
        selected_step_ids: Step ids posted by checked step checkboxes.
        deselect_step_ids: Step ids posted as explicit deselections.
        preview: Typed preview built after successful form validation.
        generated_config_json: Optional generated JSON emitted from guided
            fields after validation.
    """

    config_json: forms.CharField = forms.CharField(label="Config JSON", required=False, widget=forms.Textarea)
    manifest_json: forms.CharField = forms.CharField(label="Manifest JSON", required=False, widget=forms.Textarea)
    guided_model_bank: forms.ChoiceField = forms.ChoiceField(label="Model bank example", required=False)
    guided_environment: forms.CharField = forms.CharField(label="Environment", required=False)
    guided_discovery_url: forms.CharField = forms.CharField(label="Discovery URL", required=False)
    guided_spec_version: forms.ChoiceField = forms.ChoiceField(label="Specification version", required=False)
    guided_api: forms.ChoiceField = forms.ChoiceField(label="API family", required=False)
    guided_suite: forms.ChoiceField = forms.ChoiceField(label="Suite", required=False)
    guided_client_id: forms.CharField = forms.CharField(label="Client ID", required=False)
    guided_redirect_uri: forms.CharField = forms.CharField(label="Redirect URI", required=False)
    guided_authorization_endpoint: forms.CharField = forms.CharField(
        label="Authorization endpoint override",
        required=False,
    )
    guided_open_banking_intent_id: forms.CharField = forms.CharField(label="Intent ID", required=False)
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
    selection_mode: forms.ChoiceField = forms.ChoiceField(
        choices=(("deselect", "Deselect submitted ids"), ("select", "Select submitted ids")),
        required=False,
    )
    selected_step_ids: StepIdListField = StepIdListField(required=False)
    deselect_step_ids: StepIdListField = StepIdListField(required=False)

    preview: PlanPreview | None = None
    generated_config_json: str | None = None

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
        """Initialise the form with the current guided-flow catalog choices.

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
        guided_model_bank_field = cast(forms.ChoiceField, self.fields["guided_model_bank"])
        guided_spec_version_field = cast(forms.ChoiceField, self.fields["guided_spec_version"])
        guided_api_field = cast(forms.ChoiceField, self.fields["guided_api"])
        guided_suite_field = cast(forms.ChoiceField, self.fields["guided_suite"])
        guided_signing_auth_method_field = cast(
            forms.ChoiceField,
            self.fields["guided_signing_token_endpoint_auth_method"],
        )
        guided_model_bank_field.choices = [("", "Custom environment"), *guided_model_bank_choices()]
        guided_spec_version_field.choices = [("", "Select version"), *guided_spec_version_choices()]
        guided_api_field.choices = [("", "Select API"), *guided_api_choices()]
        guided_suite_field.choices = [("", "Select suite"), *guided_suite_name_choices()]
        guided_signing_auth_method_field.choices = [
            ("", "Select auth method"),
            ("private_key_jwt", "private_key_jwt"),
            ("tls_client_auth", "tls_client_auth"),
        ]

    def clean_config_json(self) -> ModelBankConfig | None:
        """Validate the submitted model-bank config JSON.

        Returns:
            Parsed and validated model-bank configuration, or ``None`` when
            the browser form leaves the textarea blank and relies on guided
            inputs instead.

        Raises:
            ValidationError: If the value is not JSON, is not a JSON object,
                or fails model-bank config validation.
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
        if config is None:
            config = self._build_guided_config(cleaned_data=cleaned_data, requires_suite=manifest is None)
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

    def _build_guided_config(
        self,
        *,
        cleaned_data: dict[str, object],
        requires_suite: bool,
    ) -> ModelBankConfig | None:
        """Build a validated config object from the guided browser fields.

        Args:
            cleaned_data: Current cleaned-data dictionary from the form.
            requires_suite: Whether blank manifest input means the guided flow
                must provide ``testSuite`` fields.

        Returns:
            Parsed and validated config built from guided fields, or ``None``
            when the submission did not provide enough guided data to build a
            config.
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

        model_bank_option = _guided_model_bank_option_from_cleaned_data(cleaned_data)
        environment = _cleaned_optional_string(cleaned_data.get("guided_environment"))
        discovery_url = _cleaned_optional_string(cleaned_data.get("guided_discovery_url"))
        if model_bank_option is not None:
            environment = environment or model_bank_option.environment
            discovery_url = discovery_url or model_bank_option.discovery_url
        if environment is None:
            self.add_error("guided_environment", "Environment is required for guided config generation.")
        if discovery_url is None:
            self.add_error("guided_discovery_url", "Discovery URL is required for guided config generation.")

        suite_option = _guided_suite_option_from_cleaned_data(cleaned_data)
        suite_choice_supplied = _guided_suite_choice_supplied(cleaned_data)
        if suite_choice_supplied and suite_option is None:
            if _cleaned_optional_string(cleaned_data.get("guided_spec_version")) is None:
                self.add_error(
                    "guided_spec_version",
                    "Specification version is required when selecting a guided suite.",
                )
            if _cleaned_optional_string(cleaned_data.get("guided_api")) is None:
                self.add_error("guided_api", "API family is required when selecting a guided suite.")
            if _cleaned_optional_string(cleaned_data.get("guided_suite")) is None:
                self.add_error("guided_suite", "Suite is required when selecting a guided suite.")
        if requires_suite and suite_option is None:
            self.add_error(
                "guided_suite",
                "Guided suite selection is required unless you paste a manifest JSON object.",
            )

        if self.errors:
            return None

        raw_config: dict[str, JsonValue] = {
            "environment": environment,
            "discoveryUrl": discovery_url,
        }
        if suite_option is not None:
            raw_config["testSuite"] = {
                "standard": suite_option.standard,
                "specVersion": suite_option.spec_version,
                "profile": suite_option.profile,
                "api": suite_option.api,
                "suite": suite_option.suite,
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

    When the manifest carries an explicit ``authMetadata`` section, auth
    bundles and step requirements are derived from the declared inventory,
    filtered to selected steps only.  When ``authMetadata`` is absent, the
    legacy heuristic inventory builder is used as a fallback so that existing
    suites continue to behave correctly.

    When ``suite_metadata`` is available, environment capability evaluation is
    performed and hard blockers are merged into ``launch_blockers``.
    Capability warnings (unknown custom environment dimensions) are stored
    separately in ``capability_warnings`` and do not block launch.

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
    try:
        test_value_ctx = build_plan_test_value_context(manifest, config.test_values)
    except ValueError:
        # Gracefully degrade when profile resolution fails (e.g. unknown profile
        # id in a partial config): treat as no effective test values so
        # conditional steps appear with missing-value status in the preview.
        test_value_ctx = PlanTestValueContext()
    default_plan = TestPlan.default_plan_from_manifest(manifest, test_value_context=test_value_ctx)
    if selection_mode == "select":
        selected_plan = _plan_from_selected_step_ids(default_plan, selected_step_ids or [])
    else:
        selected_plan = default_plan.with_deselection(deselect_step_ids or [])

    rows = _build_step_rows(manifest=manifest, default_plan=default_plan, selected_plan=selected_plan)

    if manifest.auth_inventory is not None:
        auth_inventory, step_auth_requirements = _build_auth_inventory_from_explicit_metadata(
            manifest=manifest,
            selected_plan=selected_plan,
        )
    else:
        auth_inventory, step_auth_requirements = _build_auth_inventory(
            manifest=manifest,
            selected_plan=selected_plan,
        )
    tree_nodes = build_plan_tree(
        manifest=manifest,
        suite_metadata=suite_metadata,
        selected_plan=selected_plan,
        rows=rows,
        auth_bundles=auth_inventory,
    )

    capability_blockers, capability_warnings = _evaluate_capability_support(
        config=config,
        manifest=manifest,
        suite_metadata=suite_metadata,
    )
    manifest_blockers = _launch_blockers(manifest)
    all_blockers = (*manifest_blockers, *capability_blockers)
    return PlanPreview(
        config=config,
        manifest=manifest,
        suite_metadata=suite_metadata,
        default_plan=default_plan,
        selected_plan=selected_plan,
        rows=rows,
        launch_supported=not all_blockers,
        launch_blockers=all_blockers,
        certification_eligible_by_selection=selected_plan.is_eligible_by_selection(),
        auth_inventory=auth_inventory,
        step_auth_requirements=step_auth_requirements,
        capability_warnings=capability_warnings,
        tree_nodes=tree_nodes,
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


def guided_spec_version_choices() -> tuple[tuple[str, str], ...]:
    """Return guided-flow specification version choices.

    Returns:
        Distinct specification version value/label pairs in catalog order.
    """
    versions = tuple(dict.fromkeys(metadata.spec_version for metadata in list_supported_suites()))
    return tuple((version, version) for version in versions)


def guided_api_choices() -> tuple[tuple[str, str], ...]:
    """Return guided-flow API family choices.

    Returns:
        Distinct API family value/label pairs in deterministic order.
    """
    apis = tuple(dict.fromkeys(metadata.api for metadata in list_supported_suites()))
    return tuple((api, _guided_api_label(api)) for api in apis)


def guided_suite_name_choices() -> tuple[tuple[str, str], ...]:
    """Return guided-flow suite identifier choices.

    Returns:
        Distinct suite-name value/label pairs in deterministic order.
    """
    suites = tuple(dict.fromkeys(metadata.suite for metadata in list_supported_suites()))
    return tuple((suite, _guided_suite_label(suite)) for suite in suites)


def guided_model_bank_choices() -> tuple[tuple[str, str], ...]:
    """Return known model-bank example choices for the guided flow.

    Returns:
        Model-bank option value/label pairs in display order.
    """
    return tuple((option.value, option.label) for option in guided_model_bank_options())


def guided_flow_context(form: PlanBuilderForm) -> dict[str, object]:
    """Build template context for the structured guided browser flow.

    Args:
        form: Bound or unbound plan-builder form.

    Returns:
        Template context containing guided selector options, current
        selection, suite requirements, and the generated config preview.
    """
    selected_spec_version = _raw_form_value(form, "guided_spec_version")
    selected_api = _raw_form_value(form, "guided_api")
    selected_suite = _raw_form_value(form, "guided_suite")
    suite_options = guided_suite_options()
    selected_suite_option = next(
        (
            option
            for option in suite_options
            if option.spec_version == selected_spec_version
            and option.api == selected_api
            and option.suite == selected_suite
        ),
        None,
    )
    return {
        "guided_versions": tuple(version for version, _label in guided_spec_version_choices()),
        "guided_model_bank_options": guided_model_bank_options(),
        "guided_api_options": guided_api_options(),
        "guided_suite_options": suite_options,
        "guided_selected_suite": selected_suite_option,
        "generated_config_json": form.generated_config_json,
    }


def guided_api_options() -> tuple[GuidedApiOption, ...]:
    """Return version-aware API selector options for the guided flow.

    Returns:
        Guided API options in catalog order with per-version scoping.
    """
    seen: set[tuple[str, str]] = set()
    options: list[GuidedApiOption] = []
    for metadata in list_supported_suites():
        key = (metadata.spec_version, metadata.api)
        if key in seen:
            continue
        seen.add(key)
        options.append(
            GuidedApiOption(
                spec_version=metadata.spec_version,
                api=metadata.api,
                label=_guided_api_label(metadata.api),
            )
        )
    return tuple(options)


def guided_suite_options() -> tuple[GuidedSuiteOption, ...]:
    """Return suite selector options and prompt requirements.

    Returns:
        Guided suite options derived from the supported suite catalog.
    """
    return tuple(_guided_suite_option(metadata) for metadata in list_supported_suites())


def guided_model_bank_options() -> tuple[GuidedModelBankOption, ...]:
    """Return known model-bank examples for environment/discovery input.

    Returns:
        Guided model-bank examples that can populate editable environment and
        discovery URL fields.
    """
    return (
        GuidedModelBankOption(
            value="ozone-obie-preprod",
            label="Ozone OBIE pre-production",
            environment="ozone-model-bank",
            discovery_url="https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration",
        ),
    )


def _guided_api_label(api: str) -> str:
    """Return the participant-facing label for an API family.

    Args:
        api: API family value from the suite catalog.

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


def _guided_suite_label(suite: str) -> str:
    """Return the participant-facing label for a suite identifier.

    Args:
        suite: Suite identifier from the suite catalog.

    Returns:
        Short label used in the browser suite selector.
    """
    labels = {
        "discovery-jwks": "Discovery and JWKS smoke",
        "psu-auth-starter": "PSU authorization starter",
        "pis-domestic-payment-starter": "PIS domestic payment starter",
        "ais-certification-slice": "AIS certification slice",
        "ais-certification-baseline": "AIS certification baseline",
        "ais-fcs-legacy-benchmark": "AIS FCS legacy benchmark",
        "pis-fcs-legacy-benchmark": "PIS FCS legacy benchmark",
    }
    return labels.get(suite, suite)


def _guided_suite_option(metadata: SuiteMetadata) -> GuidedSuiteOption:
    """Convert suite metadata into a guided-flow suite option.

    Args:
        metadata: Supported suite metadata row from the catalog.

    Returns:
        Guided suite option including field-prompt requirements.
    """
    prompts_oauth = metadata.suite != "discovery-jwks"
    prompts_intent_id = metadata.suite == "psu-auth-starter"
    prompts_resource_base_url = metadata.suite in {
        "ais-certification-slice",
        "ais-certification-baseline",
        "ais-fcs-legacy-benchmark",
        "pis-domestic-payment-starter",
        "pis-fcs-legacy-benchmark",
    }
    prompts_signing = metadata.suite in {
        "psu-auth-starter",
        "ais-certification-slice",
        "ais-certification-baseline",
        "ais-fcs-legacy-benchmark",
        "pis-domestic-payment-starter",
        "pis-fcs-legacy-benchmark",
    }
    return GuidedSuiteOption(
        standard=metadata.standard,
        spec_version=metadata.spec_version,
        profile=metadata.profile,
        api=metadata.api,
        suite=metadata.suite,
        label=_guided_suite_label(metadata.suite),
        description=metadata.description,
        prompts_oauth=prompts_oauth,
        prompts_intent_id=prompts_intent_id,
        prompts_resource_base_url=prompts_resource_base_url,
        prompts_signing=prompts_signing,
    )


def _guided_suite_choice_supplied(cleaned_data: dict[str, object]) -> bool:
    """Return whether the guided suite selectors contain any value.

    Args:
        cleaned_data: Current form cleaned-data dictionary.

    Returns:
        ``True`` when version, API, or suite selectors were supplied.
    """
    return any(
        _cleaned_optional_string(cleaned_data.get(field_name)) is not None
        for field_name in ("guided_spec_version", "guided_api", "guided_suite")
    )


def _guided_suite_option_from_cleaned_data(cleaned_data: dict[str, object]) -> GuidedSuiteOption | None:
    """Resolve the currently selected guided suite option.

    Args:
        cleaned_data: Current form cleaned-data dictionary.

    Returns:
        Matching guided suite option, or ``None`` when the selection is blank
        or does not match a supported catalog row.
    """
    spec_version = _cleaned_optional_string(cleaned_data.get("guided_spec_version"))
    api = _cleaned_optional_string(cleaned_data.get("guided_api"))
    suite = _cleaned_optional_string(cleaned_data.get("guided_suite"))
    if spec_version is None or api is None or suite is None:
        return None
    return next(
        (
            option
            for option in guided_suite_options()
            if option.spec_version == spec_version and option.api == api and option.suite == suite
        ),
        None,
    )


def _guided_model_bank_option_from_cleaned_data(cleaned_data: dict[str, object]) -> GuidedModelBankOption | None:
    """Resolve the selected guided model-bank example.

    Args:
        cleaned_data: Current form cleaned-data dictionary.

    Returns:
        Matching model-bank option, or ``None`` when custom values are used.
    """
    selected_value = _cleaned_optional_string(cleaned_data.get("guided_model_bank"))
    if selected_value is None:
        return None
    return next((option for option in guided_model_bank_options() if option.value == selected_value), None)


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
            "guided_model_bank",
            "guided_environment",
            "guided_discovery_url",
            "guided_spec_version",
            "guided_api",
            "guided_suite",
            "guided_client_id",
            "guided_redirect_uri",
            "guided_authorization_endpoint",
            "guided_open_banking_intent_id",
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


def _build_guided_oauth_object(cleaned_data: dict[str, object]) -> dict[str, JsonValue] | None:
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
        "openBankingIntentId": "guided_open_banking_intent_id",
        "resourceBaseUrl": "guided_resource_base_url",
    }
    raw_oauth: dict[str, JsonValue] = {}
    for config_key, field_name in field_mapping.items():
        value = _cleaned_optional_string(cleaned_data.get(field_name))
        if value is not None:
            raw_oauth[config_key] = value
    return raw_oauth or None


def _build_guided_fapi_signing_object(cleaned_data: dict[str, object]) -> dict[str, JsonValue] | None:
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
    raw_fapi_signing: dict[str, JsonValue] = {}
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
        raw_value = form.data.get(field_name, "")
        return raw_value if isinstance(raw_value, str) else ""
    initial_value = form.initial.get(field_name, "")
    return initial_value if isinstance(initial_value, str) else ""


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

    Conditional-row status (condition id/label, required/missing keys, profile
    id and source, override keys) is carried over from the plan entries produced
    by :meth:`TestPlan.default_plan_from_manifest` with a
    :class:`PlanTestValueContext`.  When the plan was built without a context
    the conditional fields default to their zero values, preserving backward
    compatibility for manifests without ``testValueProfiles``.

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
                # Conditional-row fields — sourced from the default-plan entry so
                # the profile/missing-key status reflects what the default plan
                # computed, even when the participant subsequently selects/deselects.
                conditional=default_entry.conditional,
                condition_id=default_entry.condition_id,
                condition_label=default_entry.condition_label,
                required_test_value_keys=default_entry.required_test_value_keys,
                missing_test_value_keys=default_entry.missing_test_value_keys,
                test_value_profile_id=default_entry.test_value_profile_id,
                test_value_profile_source=default_entry.test_value_profile_source,
                test_value_override_keys=default_entry.test_value_override_keys,
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


def _manifest_psu_mode(manifest: Manifest) -> PsuMode | None:
    """Return the PSU authorisation mode declared by the first PSU step in the manifest.

    The PSU mode is extracted from the first :class:`~conformance.manifest.PsuAuthorizationStep`
    encountered in manifest step order.  Both ``"manual"`` and ``"headless"`` are
    valid :data:`~conformance.environment_capabilities.PsuMode` values.

    Args:
        manifest: Validated v1 manifest to inspect.

    Returns:
        The PSU mode string when a PSU step is present, or ``None`` when the
        manifest contains no PSU authorisation steps.
    """
    for step in manifest.steps:
        if isinstance(step, PsuAuthorizationStep):
            return step.mode  # PsuAuthorizationMode is a subset of PsuMode
    return None


def _manifest_token_endpoint_auth_methods(
    *,
    config: ModelBankConfig,
    manifest: Manifest,
) -> tuple[TokenEndpointClientAuthMode | None, ...]:
    """Return token endpoint auth methods that should drive capability checks.

    Explicit ``authMetadata`` is authoritative: no-auth and PSU-starter bundles
    must not inherit an unrelated config-level FAPI signing method. Legacy
    manifests without explicit metadata keep the previous behaviour and use the
    participant config when present.

    Args:
        config: Validated model-bank configuration for this preview.
        manifest: Validated v1 manifest being previewed.

    Returns:
        Ordered unique token endpoint auth methods to evaluate. A single
        ``None`` entry means no token endpoint auth method is selected.
    """
    if manifest.auth_inventory is None:
        method = config.fapi_signing.token_endpoint_auth_method if config.fapi_signing is not None else None
        return (method,)

    declared_methods = tuple(
        dict.fromkeys(
            bundle.token_endpoint_auth_method
            for bundle in manifest.auth_inventory.bundles
            if bundle.token_endpoint_auth_method is not None
        )
    )
    if not declared_methods:
        return (None,)
    return declared_methods


def _evaluate_capability_support(
    *,
    config: ModelBankConfig,
    manifest: Manifest,
    suite_metadata: SuiteMetadata | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Evaluate environment capability compatibility and return blockers and warnings.

    When ``suite_metadata`` is ``None`` (explicit manifest without a catalog
    suite), capability evaluation is skipped and empty tuples are returned.

    For catalog suites, the evaluation calls
    :func:`~conformance.environment_capabilities.evaluate_suite_environment_support`
    using a custom :class:`~conformance.environment_capabilities.EnvironmentReference`
    built from :attr:`~conformance.model_bank_config.ModelBankConfig.environment`.
    Known hard blockers (e.g. unsupported suite/auth/PSU combinations) are
    returned as capability blockers.  Unknown custom environment dimensions
    produce warnings rather than blockers, matching the contract defined by
    :func:`~conformance.environment_capabilities.evaluate_suite_environment_support`.

    Args:
        config: Validated model-bank configuration for this preview.
        manifest: Validated v1 manifest being previewed.
        suite_metadata: Optional catalog metadata for the config-resolved suite.

    Returns:
        Two-tuple of ``(blockers, warnings)`` where each is a tuple of
        human-readable strings.  Both are empty when ``suite_metadata`` is
        ``None``.
    """
    if suite_metadata is None:
        return (), ()

    selection = suite_metadata.to_suite_selection()
    environment = make_custom_environment_reference(label=config.environment)
    psu_mode = _manifest_psu_mode(manifest)
    blockers: list[str] = []
    warnings: list[str] = []
    for token_endpoint_auth_method in _manifest_token_endpoint_auth_methods(config=config, manifest=manifest):
        evaluation = evaluate_suite_environment_support(
            selection=selection,
            environment=environment,
            psu_mode=psu_mode,
            token_endpoint_auth_method=token_endpoint_auth_method,
        )
        blockers.extend(evaluation.blockers)
        warnings.extend(evaluation.warnings)
    return tuple(dict.fromkeys(blockers)), tuple(dict.fromkeys(warnings))


def _build_auth_inventory_from_explicit_metadata(
    *,
    manifest: Manifest,
    selected_plan: TestPlan,
) -> tuple[tuple[PlanAuthBundle, ...], tuple[PlanStepAuthRequirement, ...]]:
    """Build auth bundle inventory from a manifest's explicit ``authMetadata``.

    This path is used when the manifest author has supplied an explicit
    ``authMetadata`` section carrying :class:`~conformance.auth_metadata.AuthBundleInventory`
    data.  The inventory is filtered to the selected plan:

    * Step requirements are retained only for selected step ids.
    * Bundle consuming-step lists are narrowed to selected steps only.
    * Bundles referenced by no selected step requirement are omitted.
    * Bundle order reflects first-use order within the selected step sequence.

    Unlike the heuristic fallback (:func:`_build_auth_inventory`), this path
    uses the declared bundle ids directly and does not apply Basic/Detail name
    heuristics.

    Args:
        manifest: Validated v1 manifest carrying a non-``None``
            :attr:`~conformance.manifest.Manifest.auth_inventory`.
        selected_plan: Plan after applying participant selection input.

    Returns:
        Two-tuple of filtered auth bundles and selected step-to-bundle mappings.
    """
    inventory = manifest.auth_inventory
    assert inventory is not None, "_build_auth_inventory_from_explicit_metadata requires auth_inventory"  # noqa: S101

    selected_ids = set(selected_plan.selected_step_ids())

    # Filter step requirements to selected steps only, preserving manifest order.
    selected_step_reqs: list[PlanStepAuthRequirement] = [
        PlanStepAuthRequirement(step_id=req.step_id, bundle_id=req.bundle_id)
        for req in inventory.step_requirements
        if req.step_id in selected_ids
    ]

    # Build index of referenced bundle ids in first-use order from selected requirements.
    ordered_bundle_ids: list[str] = list(dict.fromkeys(req.bundle_id for req in selected_step_reqs))
    bundles_by_id: dict[str, AuthBundleDeclaration] = {bundle.id: bundle for bundle in inventory.bundles}

    bundles: list[PlanAuthBundle] = []
    for bundle_id in ordered_bundle_ids:
        decl = bundles_by_id.get(bundle_id)
        if decl is None:
            continue
        selected_consuming = tuple(step_id for step_id in decl.consuming_step_ids if step_id in selected_ids)
        bundles.append(
            PlanAuthBundle(
                id=decl.id,
                token_step_id=decl.token_step_id,
                consent_step_id=decl.consent_step_id,
                required_scopes=decl.required_scopes,
                required_ob_permissions=decl.required_ob_permissions,
                consuming_step_ids=selected_consuming,
            )
        )

    return tuple(bundles), tuple(selected_step_reqs)


@dataclass(frozen=True)
class _TokenBundleSeed:
    """Intermediate auth-bundle seed linked to a token-minting step.

    Attributes:
        token_step_id: Step id that mints the access token.
        consent_step_id: Consent-creation step id linked to this token.
        required_scopes: OAuth scopes carried by the linked PSU step.
        required_ob_permissions: Consent permissions linked to the token.
    """

    token_step_id: str
    consent_step_id: str | None
    required_scopes: tuple[str, ...]
    required_ob_permissions: tuple[str, ...]


def _build_auth_inventory(
    *,
    manifest: Manifest,
    selected_plan: TestPlan,
) -> tuple[tuple[PlanAuthBundle, ...], tuple[PlanStepAuthRequirement, ...]]:
    """Build selected-step auth bundle inventory for plan previews.

    Args:
        manifest: Validated manifest whose selected steps are being previewed.
        selected_plan: Plan after applying participant selection input.

    Returns:
        Tuple of auth bundles and selected step-to-bundle mappings.
    """
    selected_ids = set(selected_plan.selected_step_ids())
    token_seeds = _token_bundle_seeds(manifest)
    if not token_seeds:
        return (), ()

    bundle_map: dict[tuple[str, tuple[str, ...], tuple[str, ...]], PlanAuthBundle] = {}
    bundle_consumers: dict[str, list[str]] = {}
    step_requirements: list[PlanStepAuthRequirement] = []
    for step in manifest.steps:
        if step.id not in selected_ids:
            continue
        if isinstance(step, PsuAuthorizationStep):
            continue
        token_step_id = _authorization_token_step_id(step)
        if token_step_id is None:
            continue
        seed = token_seeds.get(token_step_id)
        if seed is None:
            continue
        effective_permissions = _effective_permissions_for_step(step=step, seed=seed)
        bundle_key = (seed.token_step_id, seed.required_scopes, effective_permissions)
        bundle = bundle_map.get(bundle_key)
        if bundle is None:
            bundle_id = _stable_bundle_id(
                token_step_id=seed.token_step_id,
                required_scopes=seed.required_scopes,
                required_permissions=effective_permissions,
            )
            bundle = PlanAuthBundle(
                id=bundle_id,
                token_step_id=seed.token_step_id,
                consent_step_id=seed.consent_step_id,
                required_scopes=seed.required_scopes,
                required_ob_permissions=effective_permissions,
                consuming_step_ids=(),
            )
            bundle_map[bundle_key] = bundle
            bundle_consumers[bundle_id] = []
        bundle_consumers[bundle.id].append(step.id)
        step_requirements.append(PlanStepAuthRequirement(step_id=step.id, bundle_id=bundle.id))

    ordered_bundle_ids = [
        requirement.bundle_id for requirement in step_requirements if requirement.bundle_id in bundle_consumers
    ]
    deduplicated_ordered_bundle_ids = tuple(dict.fromkeys(ordered_bundle_ids))
    bundles = tuple(
        PlanAuthBundle(
            id=bundle.id,
            token_step_id=bundle.token_step_id,
            consent_step_id=bundle.consent_step_id,
            required_scopes=bundle.required_scopes,
            required_ob_permissions=bundle.required_ob_permissions,
            consuming_step_ids=tuple(bundle_consumers[bundle.id]),
        )
        for bundle_id in deduplicated_ordered_bundle_ids
        for bundle in bundle_map.values()
        if bundle.id == bundle_id
    )
    return bundles, tuple(step_requirements)


def _token_bundle_seeds(manifest: Manifest) -> dict[str, _TokenBundleSeed]:
    """Build token-step seeds carrying consent permissions and PSU scopes.

    Args:
        manifest: Validated manifest to inspect.

    Returns:
        Mapping of token-minting step id to auth bundle seed.
    """
    consent_permissions = _consent_permissions_by_step_id(manifest)
    psu_steps = {step.id: step for step in manifest.steps if isinstance(step, PsuAuthorizationStep)}
    seeds: dict[str, _TokenBundleSeed] = {}
    for step in manifest.steps:
        if isinstance(step, PsuAuthorizationStep):
            continue
        psu_step_id = _authorization_code_source_step_id(step)
        if psu_step_id is None:
            continue
        psu_step = psu_steps.get(psu_step_id)
        if psu_step is None:
            continue
        consent_step_id = _consent_step_id_for_psu(psu_step=psu_step, manifest=manifest)
        permissions = () if consent_step_id is None else consent_permissions.get(consent_step_id, ())
        seeds[step.id] = _TokenBundleSeed(
            token_step_id=step.id,
            consent_step_id=consent_step_id,
            required_scopes=_scope_tokens(psu_step.scope),
            required_ob_permissions=permissions,
        )
    return seeds


def _authorization_code_source_step_id(step: ManifestStep | PsuAuthorizationStep) -> str | None:
    """Return the PSU step id used as authorization-code source.

    Args:
        step: Manifest step to inspect.

    Returns:
        PSU step id referenced by the token exchange ``code`` field, or
        ``None`` when the step is not an authorization-code exchange.
    """
    if isinstance(step, PsuAuthorizationStep):
        return None
    if step.request.body is None or not isinstance(step.request.body, FormBody):
        return None
    grant_type = step.request.body.fields.get("grant_type")
    if grant_type != "authorization_code":
        return None
    code_value = step.request.body.fields.get("code")
    if code_value is None:
        return None
    return _extract_step_placeholder_step_id(code_value, field="code")


def _authorization_token_step_id(step: ManifestStep | PsuAuthorizationStep) -> str | None:
    """Return the token step id referenced by a bearer Authorization header.

    Args:
        step: Manifest step to inspect.

    Returns:
        Token-minting step id when the request uses a bearer placeholder,
        or ``None`` for non-protected steps.
    """
    if isinstance(step, PsuAuthorizationStep):
        return None
    headers = step.request.headers or {}
    auth_header = headers.get("Authorization")
    if auth_header is None:
        return None
    auth_header_value = auth_header.strip()
    if not auth_header_value.startswith("Bearer "):
        return None
    token_placeholder = auth_header_value.removeprefix("Bearer ").strip()
    return _extract_step_placeholder_step_id(token_placeholder, field="access_token")


def _consent_permissions_by_step_id(manifest: Manifest) -> dict[str, tuple[str, ...]]:
    """Return Open Banking permissions declared by consent-creation steps.

    Args:
        manifest: Validated manifest to inspect.

    Returns:
        Mapping from consent-creation step id to sorted permissions.
    """
    permissions_by_step: dict[str, tuple[str, ...]] = {}
    for step in manifest.steps:
        if isinstance(step, PsuAuthorizationStep):
            continue
        if step.request.method != "POST":
            continue
        if "account-access-consents" not in step.request.url:
            continue
        request_body = step.request.body
        if request_body is None or not isinstance(request_body, JsonBody):
            continue
        permissions_by_step[step.id] = _permissions_from_request_body(request_body.value)
    return permissions_by_step


def _permissions_from_request_body(body_value: JsonValue) -> tuple[str, ...]:
    """Extract consent permissions from a consent request body.

    Args:
        body_value: JSON request body value.

    Returns:
        Sorted unique permission strings from ``Data.Permissions``.
    """
    if not isinstance(body_value, dict):
        return ()
    raw_data = body_value.get("Data")
    if not isinstance(raw_data, dict):
        return ()
    raw_permissions = raw_data.get("Permissions")
    if not isinstance(raw_permissions, list):
        return ()
    permissions = [permission for permission in raw_permissions if isinstance(permission, str) and permission]
    return tuple(sorted(set(permissions)))


def _consent_step_id_for_psu(*, psu_step: PsuAuthorizationStep, manifest: Manifest) -> str | None:
    """Resolve the consent step id associated with a PSU authorization step.

    Args:
        psu_step: PSU authorization step whose consent dependency is resolved.
        manifest: Manifest containing the PSU step.

    Returns:
        Consent step id, or ``None`` when no linked consent step is found.
    """
    request_object = psu_step.request_object
    if isinstance(request_object, GeneratedRequestObject) and request_object.openbanking_intent_id is not None:
        from_request_object = _extract_step_placeholder_step_id(
            request_object.openbanking_intent_id,
            field="Data.ConsentId",
        )
        if from_request_object is not None:
            return from_request_object

    consent_step_ids = set(_consent_permissions_by_step_id(manifest).keys())
    if not consent_step_ids:
        return None
    psu_index = next((index for index, step in enumerate(manifest.steps) if step.id == psu_step.id), None)
    if psu_index is None:
        return None
    for step in reversed(manifest.steps[:psu_index]):
        if step.id in consent_step_ids:
            return step.id
    return None


def _extract_step_placeholder_step_id(placeholder: str, *, field: str) -> str | None:
    """Extract a step id from a ``${steps.<id>.response.body.<field>}`` placeholder.

    Args:
        placeholder: Placeholder string to parse.
        field: Expected response-body field suffix.

    Returns:
        Referenced step id, or ``None`` when the placeholder does not match.
    """
    pattern = re.compile(r"^\$\{steps\.([A-Za-z0-9_.-]+)\.response\.body\." + re.escape(field) + r"\}$")
    match = pattern.match(placeholder)
    if match is None:
        return None
    return match.group(1)


def _scope_tokens(scope: str) -> tuple[str, ...]:
    """Normalize an OAuth scope string into sorted unique tokens.

    Args:
        scope: Raw OAuth scope string from a PSU authorization step.

    Returns:
        Sorted unique non-empty scope tokens.
    """
    tokens = [token for token in scope.split(" ") if token]
    return tuple(sorted(set(tokens)))


def _effective_permissions_for_step(
    *,
    step: ManifestStep | PsuAuthorizationStep,
    seed: _TokenBundleSeed,
) -> tuple[str, ...]:
    """Compute step-specific effective permissions for auth-bundle grouping.

    Args:
        step: Selected token-protected step consuming the bundle.
        seed: Bundle seed carrying manifest-level consent permissions.

    Returns:
        Effective permissions, potentially narrowed to Basic or Detail when
        both variants exist in the same permission family.
    """
    if isinstance(step, PsuAuthorizationStep):
        return seed.required_ob_permissions

    variant = _permission_variant_for_step(step)
    if variant is None:
        return seed.required_ob_permissions

    families_with_split = _families_with_basic_detail(seed.required_ob_permissions)
    if not families_with_split:
        return seed.required_ob_permissions

    narrowed_permissions: list[str] = []
    for permission in seed.required_ob_permissions:
        family, permission_variant = _permission_family_and_variant(permission)
        if (
            family in families_with_split
            and permission_variant in {"Basic", "Detail"}
            and permission_variant != variant
        ):
            continue
        narrowed_permissions.append(permission)
    return tuple(narrowed_permissions)


def _permission_variant_for_step(step: ManifestStep) -> Literal["Basic", "Detail"] | None:
    """Infer whether a selected step consumes Basic or Detail permissions.

    Args:
        step: Selected HTTP manifest step using a protected resource token.

    Returns:
        ``"Basic"`` or ``"Detail"`` when inferred from step metadata,
        otherwise ``None``.
    """
    metadata = f"{step.id} {step.name}".lower()
    if "detail" in metadata:
        return "Detail"
    if "basic" in metadata or "list" in metadata:
        return "Basic"
    return None


def _families_with_basic_detail(permissions: tuple[str, ...]) -> set[str]:
    """Return permission families that contain both Basic and Detail variants.

    Args:
        permissions: Consent permission set.

    Returns:
        Families containing both ``Basic`` and ``Detail`` variants.
    """
    family_variants: dict[str, set[str]] = {}
    for permission in permissions:
        family, variant = _permission_family_and_variant(permission)
        if variant is None:
            continue
        family_variants.setdefault(family, set()).add(variant)
    return {family for family, variants in family_variants.items() if "Basic" in variants and "Detail" in variants}


def _permission_family_and_variant(permission: str) -> tuple[str, Literal["Basic", "Detail"] | None]:
    """Split an OB permission into family and Basic/Detail variant.

    Args:
        permission: Open Banking permission name.

    Returns:
        Tuple of permission family and optional Basic/Detail variant.
    """
    if permission.endswith("Basic"):
        return permission[: -len("Basic")], "Basic"
    if permission.endswith("Detail"):
        return permission[: -len("Detail")], "Detail"
    return permission, None


def _stable_bundle_id(
    *,
    token_step_id: str,
    required_scopes: tuple[str, ...],
    required_permissions: tuple[str, ...],
) -> str:
    """Build a deterministic auth bundle identifier for preview consumers.

    Args:
        token_step_id: Step id minting the access token.
        required_scopes: Required OAuth scopes for the bundle.
        required_permissions: Required consent permissions for the bundle.

    Returns:
        Stable short id suitable for payload references and UI rendering.
    """
    digest_source = "|".join((token_step_id, ",".join(required_scopes), ",".join(required_permissions)))
    digest = sha256(digest_source.encode("utf-8")).hexdigest()[:12]
    return f"auth-{token_step_id}-{digest}"
