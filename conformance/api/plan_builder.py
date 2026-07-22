"""Plan-builder forms and presenters for participant-facing browser workflows."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from hashlib import sha256
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar, Literal, cast

from django import forms
from django.core.files.uploadedfile import UploadedFile
from django.utils.datastructures import MultiValueDict

from conformance.auth_metadata import AuthBundleDeclaration
from conformance.catalogue import (
    CatalogueExecutableTest,
    CatalogueFieldSchema,
    CatalogueReadinessPolicy,
)
from conformance.config_document import (
    ConfigDocumentError,
    parse_participant_config_document,
    resolve_config_document_execution_plan,
)
from conformance.environment_capabilities import (
    EnvironmentReference,
    PsuMode,
    evaluate_suite_environment_support,
    list_environment_presets,
    make_custom_environment_reference,
    make_preset_environment_reference,
)
from conformance.json_types import JsonObject, JsonValue
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
    TestDataConfig,
    TestValuesConfig,
    TokenEndpointClientAuthMode,
    parse_model_bank_config,
)
from conformance.openapi_plan_metadata import StepTreeNode, build_plan_tree
from conformance.plan_executor import compile_catalogue_graph_for_plan, run_plan_from_test_target
from conformance.plugins.dcr.plugin import DcrPlugin
from conformance.plugins.read_write.plugin import ReadWritePlugin
from conformance.plugins.registry import PluginRegistry, PluginRegistryError
from conformance.run_configuration import compile_run_configuration
from conformance.run_plan import (
    RunPlan,
    RunPlanParseError,
    RunPlanSuiteCoordinates,
    RunPlanTestData,
    RunPlanTestValues,
    compute_manifest_hash,
    parse_run_plan,
    serialise_run_plan,
)
from conformance.run_plan_v2 import RunPlanV2
from conformance.suite_catalog import SuiteCatalogError, SuiteMetadata, list_supported_suites
from conformance.target_config import Specification, Standard, TestTargetConfig
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


PlanDriftStatus = Literal["ok", "hash_mismatch", "stale_step_ids", "stale_custom_keys"]
"""Drift classifications for imported Run Plan compatibility checks."""

_RUNTIME_MAPPED_FIELD_IDS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "read-write": frozenset(
            {
                "oauth.resourceBaseUrl",
                "oauth.authorizationBaseUrl",
                "oauth.clientId",
                "psu.authorizationRedirectUri",
                "tls.certPath",
                "tls.keyPath",
                "tls.caBundlePath",
                "fapiSigning.signingCertificatePath",
                "fapiSigning.signingPrivateKeyPath",
                "fapiSigning.kid",
                "fapiSigning.clientAssertionIssuer",
                "fapiSigning.clientAssertionSubject",
                "fapiSigning.tokenEndpointAuthMethod",
                "fapiSigning.signatureIssuer",
                "fapiSigning.signatureTrustAnchor",
                "openBanking.financialId",
            }
        ),
        "dynamic-client-registration": frozenset(
            {
                "dcr.ssaPath",
                "dcr.signingPrivateKeyPath",
                "dcr.signingCertificatePath",
                "dcr.transportCertificatePath",
                "dcr.transportPrivateKeyPath",
                "dcr.caBundlePath",
                "dcr.tokenEndpointAuthMethod",
                "dcr.disableKeepAlives",
                "dcr.tlsSkipVerify",
            }
        ),
    }
)
"""Catalogue field IDs that are exported into the participant runtime config."""


@dataclass(frozen=True)
class PlanImportResult:
    """Result of parsing and normalising an optional imported Run Plan payload.

    Attributes:
        imported_plan: Parsed imported plan, or ``None`` when no valid import
            payload was supplied.
        selected_step_ids: Imported selected step ids filtered to steps present
            in the currently loaded manifest.
        custom_values: Imported legacy ``testValues.customValues`` overrides
            filtered to keys allowed by the currently loaded manifest profile
            contract.
        test_data_values: Participant-supplied ``testData.values`` from the
            imported Run Plan, filtered to keys allowed by the currently
            loaded manifest ``testValues.allowedCustomKeys`` contract.
            Baseline filling and full-snapshot assembly are deferred to the
            snapshot step in :func:`build_plan_preview`.
        uses_legacy_test_values: Whether the imported Run Plan payload supplied
            legacy ``testValues.profile`` and/or ``testValues.customValues``.
        drift_statuses: Drift classifications detected while validating the
            imported plan against the current manifest.
        warnings: Human-readable non-blocking warnings shown in the preview.
        launch_blockers: Human-readable hard blockers that disable launch.
        plan_drift_blocks_launch: Whether drift requires launch to be blocked.
    """

    imported_plan: RunPlan | None
    selected_step_ids: tuple[str, ...]
    custom_values: Mapping[str, str]
    test_data_values: Mapping[str, str]
    uses_legacy_test_values: bool
    drift_statuses: tuple[PlanDriftStatus, ...]
    warnings: tuple[str, ...]
    launch_blockers: tuple[str, ...]
    plan_drift_blocks_launch: bool


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
    """One known model-bank discovery preset available in the guided flow.

    Attributes:
        value: Stable option value posted by the browser form.
        label: Human-readable model-bank label shown in the browser UI.
        discovery_url: OpenID Provider discovery URL written into generated
            config JSON.
    """

    value: str
    label: str
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
            from ``config.test_target``.
        suite_metadata: Display metadata for a config-resolved suite, or
            ``None`` when the preview uses an explicit manifest.
        default_plan: Default plan derived from the manifest before form input.
        selected_plan: Plan after applying submitted selection or deselection input.
        rows: Step presenters in manifest order.
        launch_supported: Whether this preview can be launched by the browser UI slice.
        launch_blockers: Human-readable reasons launch is disabled, including
            hard environment capability blockers for known unsupported
            suite/auth/environment combinations.
        is_exploratory_run: Whether the selected Run Plan produces at least
            one effective test-data value that differs from the manifest
            baseline (new-schema manifests), or uses a non-default profile
            or custom override values (legacy manifests).
        exploratory_ack_required: Whether launch requires explicit exploratory
            acknowledgement from the participant.
        certification_eligible_by_selection: Whether the submitted selection preserves certification eligibility.
        auth_inventory: Stable consent/token bundles required by selected
            token-protected steps.
        step_auth_requirements: Selected step to auth-bundle mappings.
        capability_warnings: Conservative warnings from environment capability
            evaluation where compatibility is unknown (e.g. undeclared custom
            environment capabilities).  These do not block launch but are
            surfaced to the participant for awareness.
        run_plan: Snapshot of the participant-selected Run Plan generated from
            this preview state.
        legacy_test_values_warning: Whether ``config.testValues`` was supplied
            through the legacy config path and will be migrated into
            ``run_plan.test_values``.
        run_plan_json: Pretty-printed JSON serialisation of ``run_plan`` for
            participant export/reuse.
        plan_drift_warnings: Human-readable warnings raised while validating an
            imported Run Plan against the current manifest.
        plan_drift_blocks_launch: Whether Run Plan drift has blocked launch
            until the participant re-exports the plan from the current manifest.
        test_value_fields: UI metadata for editable test-value keys. For new
            manifests this is derived from selected-step references and
            ``testValues.allowedCustomKeys``. For legacy manifests it is
            derived from the selected ``testValueProfiles`` profile.
        test_value_step_groups: Request-shaped tree groups for new-schema
            manifests only.  Each group corresponds to one selected step and
            contains per-surface trees whose leaves map onto API body paths,
            headers, URL, or form fields.  Empty for legacy manifests; when
            non-empty the template renders the tree UI instead of the flat
            ``test_value_fields`` grid.
        tree_nodes: Hierarchical tree nodes derived from OpenAPI standards
            documents and manifest step analysis, for visual tree selection
            rendering. Empty when ``suite_metadata`` is ``None`` or the suite
            has no bundled OpenAPI document.
        baseline_delta_keys: Set of test-value keys whose effective value differs
            from the suite manifest baseline for the selected steps. Empty when
            the manifest has no ``testValues`` block or all participant-supplied
            values match the baseline. Populated from :class:`RunConfiguration`
            for result evidence and certification gating.
    """

    config: ModelBankConfig
    manifest: Manifest
    suite_metadata: SuiteMetadata | None
    default_plan: TestPlan
    selected_plan: TestPlan
    rows: tuple[PlanStepRow, ...]
    launch_supported: bool
    launch_blockers: tuple[str, ...]
    is_exploratory_run: bool
    exploratory_ack_required: bool
    certification_eligible_by_selection: bool
    auth_inventory: tuple[PlanAuthBundle, ...]
    step_auth_requirements: tuple[PlanStepAuthRequirement, ...]
    capability_warnings: tuple[str, ...]
    run_plan: RunPlan
    legacy_test_values_warning: bool
    run_plan_json: str
    plan_drift_warnings: tuple[str, ...]
    plan_drift_blocks_launch: bool
    test_value_fields: tuple[TestValueFieldSpec, ...]
    test_value_step_groups: tuple[TestValueStepGroup, ...]
    tree_nodes: tuple[StepTreeNode, ...]
    baseline_delta_keys: frozenset[str]


@dataclass(frozen=True)
class TestValueFieldSpec:
    """Spec for one editable test-value field in the Plan Builder UI.

    Attributes:
        key: Manifest allow-listed override key (for example ``paymentAmount``).
        default_value: Suite baseline value, or legacy selected-profile value.
        is_overridden: Whether the participant supplied a custom value.
        current_value: Effective value shown in the UI.
        is_generated: Whether the manifest marks this key as runtime-generated.
        shape_warning: Advisory warning for obvious shape mismatches, or
            ``None`` when no warning applies.
    """

    key: str
    default_value: str
    is_overridden: bool
    current_value: str
    is_generated: bool
    shape_warning: str | None


@dataclass(frozen=True)
class TestValueTreeRow:
    """One rendered row in the request-shaped test-value tree.

    Rows are either intermediate group headers (showing a path-segment label
    only) or leaf rows (showing an editable or read-only input).  The
    ``depth`` field controls visual indentation in the template.

    Attributes:
        row_type: ``"group"`` for path-segment headers with no input;
            ``"leaf"`` for rows that carry an editable or read-only input.
        depth: Visual indentation depth (0 = top-level path segment).
        label: Display label — a path-segment name for group rows, the
            API field name for leaf rows.
        key: Referenced ``${testValues.<key>}`` key when ``row_type``
            is ``"leaf"``, otherwise ``None``.
        is_canonical: For leaf rows, ``True`` when the canonical
            ``<input name="custom_tv_<key>">`` element is rendered here.
            ``False`` means another step has already rendered the canonical
            input for this key and a read-only reference is shown instead.
        default_value: Suite baseline value for this key. Empty string
            for group rows or keys with no baseline entry.
        current_value: Effective participant-visible value.  Equal to
            ``default_value`` when not overridden.
        is_overridden: ``True`` when the participant has supplied a
            custom value that differs from the baseline.
        is_generated: ``True`` when the manifest marks this key as
            runtime-generated (e.g. UUIDs minted per execution).
        shape_warning: Advisory warning for obvious value-shape mismatches,
            or ``None`` when no warning applies.
    """

    row_type: Literal["group", "leaf"]
    depth: int
    label: str
    key: str | None
    is_canonical: bool
    default_value: str = ""
    current_value: str = ""
    is_overridden: bool = False
    is_generated: bool = False
    shape_warning: str | None = None
    __test__: ClassVar[bool] = False


@dataclass(frozen=True)
class TestValueSurfaceTree:
    """Test-value display tree for one HTTP request surface within a step.

    Attributes:
        surface_label: Human-readable label shown in the UI surface heading
            (e.g. ``"Body"``, ``"Headers"``, ``"URL"``, ``"Form body"``).
        request_area: Machine-readable area identifier as declared in
            :class:`conformance.manifest.TestValueReference`
            (e.g. ``"request-json-body"``).
        rows: Ordered tree rows combining group headers and leaf inputs.
    """

    surface_label: str
    request_area: str
    rows: tuple[TestValueTreeRow, ...]


@dataclass(frozen=True)
class TestValueStepGroup:
    """Test-value surface trees grouped under one selected plan step.

    Attributes:
        step_id: Step identifier as declared in the manifest.
        step_name: Human-readable step name for display in the accordion.
        surfaces: Surface trees for this step, ordered with body first.
        has_canonical_keys: ``True`` when at least one leaf in this step
            renders the canonical editable input.  Steps where all key
            inputs are already rendered canonically in an earlier step
            are shown collapsed with read-only references only.
    """

    step_id: str
    step_name: str
    surfaces: tuple[TestValueSurfaceTree, ...]
    has_canonical_keys: bool


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
            JSON. When blank, ``config.testTarget`` may resolve a bundled suite.
        guided_model_bank: Structured model-bank example selector used to
            populate guided discovery values.
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
        run_plan_import: Optional textarea containing imported Run Plan JSON.
        test_value_profile: Optional hidden profile selector value posted by the
            test-values UI section.
        exploratory_ack: Optional acknowledgement checkbox for exploratory runs.
        selected_step_ids: Step ids posted by checked step checkboxes.
        deselect_step_ids: Step ids posted as explicit deselections.
        preview: Typed preview built after successful form validation.
        generated_config_json: Optional generated JSON emitted from guided
            fields after validation.
        embedded_test_plan: Optional internal catalogue intent embedded in submitted config JSON.
    """

    config_json: forms.CharField = forms.CharField(label="Config JSON", required=False, widget=forms.Textarea)
    manifest_json: forms.CharField = forms.CharField(label="Manifest JSON", required=False, widget=forms.Textarea)
    guided_model_bank: forms.ChoiceField = forms.ChoiceField(label="Model bank example", required=False)
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
    run_plan_import: forms.CharField = forms.CharField(
        label="Run Plan import JSON",
        required=False,
        widget=forms.Textarea,
    )
    test_value_profile: forms.CharField = forms.CharField(required=False, widget=forms.HiddenInput)
    exploratory_ack: forms.BooleanField = forms.BooleanField(required=False)
    selected_step_ids: StepIdListField = StepIdListField(required=False)
    deselect_step_ids: StepIdListField = StepIdListField(required=False)

    preview: PlanPreview | None = None
    generated_config_json: str | None = None
    embedded_test_plan: RunPlanV2 | None = None

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
        guided_model_bank_field.choices = [("", "Custom discovery URL"), *guided_model_bank_choices()]
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
            document = parse_participant_config_document(raw_config, base_dir=Path.cwd())
        except ConfigDocumentError as error:
            raise forms.ValidationError(f"Config validation failed: {error}", code="invalid_config") from error
        self.embedded_test_plan = resolve_config_document_execution_plan(document)
        return document.config

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
            if config.test_target is None and self.embedded_test_plan is None:
                self.add_error(
                    "manifest_json",
                    forms.ValidationError(
                        "Manifest JSON is required unless config.testTarget or config.testPlan selects a "
                        "bundled suite.",
                        code="missing_manifest_or_suite",
                    ),
                )
                return cleaned_data
            from conformance.plan_executor import resolve_rw_suite_for_plan  # noqa: PLC0415

            try:
                if self.embedded_test_plan is not None:
                    derived_plan = self.embedded_test_plan
                else:
                    assert config.test_target is not None  # noqa: S101 - guarded by validation above.
                    derived_plan = run_plan_from_test_target(config.test_target)
                compile_catalogue_graph_for_plan(derived_plan)
                manifest, suite_metadata = resolve_rw_suite_for_plan(derived_plan)
            except (PluginRegistryError, SuiteCatalogError, ValueError) as error:
                raise forms.ValidationError(f"Suite resolution failed: {error}", code="invalid_suite") from error
        elif not isinstance(manifest, Manifest):
            return cleaned_data

        selected_step_ids = _cleaned_step_ids(cleaned_data.get("selected_step_ids"))
        deselect_step_ids = _cleaned_step_ids(cleaned_data.get("deselect_step_ids"))
        selection_mode = _cleaned_selection_mode(cleaned_data.get("selection_mode"))
        run_plan_import = _cleaned_optional_string(cleaned_data.get("run_plan_import"))
        test_value_profile = _cleaned_optional_string(cleaned_data.get("test_value_profile"))
        exploratory_ack = bool(cleaned_data.get("exploratory_ack"))
        has_custom_test_value_fields = _has_custom_test_value_fields(self.data)
        custom_test_values = _extract_custom_test_values(self.data)
        cleaned_data["run_plan_import"] = run_plan_import
        cleaned_data["test_value_profile"] = test_value_profile
        cleaned_data["exploratory_ack"] = exploratory_ack
        cleaned_data["custom_test_values"] = custom_test_values
        manifest_bytes = _submitted_manifest_json_bytes(self.data.get("manifest_json"))
        try:
            self.preview = build_plan_preview(
                config=config,
                manifest=manifest,
                suite_metadata=suite_metadata,
                manifest_bytes=manifest_bytes,
                selected_step_ids=selected_step_ids,
                deselect_step_ids=deselect_step_ids,
                selection_mode=selection_mode,
                run_plan_import=run_plan_import,
                test_value_profile=test_value_profile,
                custom_test_values=custom_test_values if has_custom_test_value_fields else None,
                exploratory_ack=exploratory_ack,
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
                must provide ``testTarget`` fields.

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
        discovery_url = _cleaned_optional_string(cleaned_data.get("guided_discovery_url"))
        if model_bank_option is not None:
            discovery_url = discovery_url or model_bank_option.discovery_url
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

        raw_config: dict[str, JsonValue] = {"discoveryUrl": discovery_url}
        if suite_option is not None:
            raw_config["testTarget"] = {
                "standard": suite_option.standard,
                "specification": "read-write",
                "securityProfile": suite_option.profile,
                "specificationVersion": suite_option.spec_version,
                "resourceGroups": [suite_option.api],
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
    manifest_bytes: bytes | None = None,
    selected_step_ids: list[str] | None = None,
    deselect_step_ids: list[str] | None = None,
    selection_mode: SelectionMode = "deselect",
    run_plan_import: str | None = None,
    test_value_profile: str | None = None,
    custom_test_values: Mapping[str, str] | None = None,
    exploratory_ack: bool = False,
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
        manifest_bytes: Optional raw manifest bytes supplied by the caller.
            When absent, bytes are resolved from suite metadata or a
            deterministic manifest serialisation fallback.
        selected_step_ids: Step ids checked in a selection-mode form post.
        deselect_step_ids: Step ids unchecked or explicitly deselected by a deselection-mode form post.
        selection_mode: Whether to derive the submitted plan from selected ids
            or by deselecting ids from the default plan.
        run_plan_import: Optional imported Run Plan JSON supplied by the
            participant for pre-populating selection and custom values.
        test_value_profile: Optional profile override supplied by the browser
            form. ``None`` uses ``config.test_values.profile``.
        custom_test_values: Optional per-key custom test-value overrides
            supplied by the browser form. ``None`` uses
            ``config.test_values.overrides``.
        exploratory_ack: Whether the participant explicitly acknowledged an
            exploratory run in the submitted form data.

    Returns:
        A complete plan preview with step rows and launch support flags.

    Raises:
        ValueError: If ``manifest`` is not v1 or any submitted step id is unknown.
    """
    if manifest.schema_version != "v1":
        raise ValueError("Plan builder supports v1 manifests only")
    manifest_hash = _compute_preview_manifest_hash(
        manifest=manifest,
        suite_metadata=suite_metadata,
        manifest_bytes=manifest_bytes,
    )
    import_result = _parse_plan_import_result(
        run_plan_import=run_plan_import,
        manifest=manifest,
        current_manifest_hash=manifest_hash,
    )

    effective_selection_mode = selection_mode
    effective_selected_step_ids = selected_step_ids or []
    supports_test_data_schema = manifest.test_values is not None
    if import_result.imported_plan is not None:
        if supports_test_data_schema:
            effective_profile = None
            effective_custom_values = {}
            effective_test_data_values = dict(import_result.test_data_values)
        else:
            effective_profile = import_result.imported_plan.test_values.profile
            effective_custom_values = dict(import_result.custom_values)
            effective_test_data_values = dict(import_result.imported_plan.test_data.values)
        effective_selection_mode = "select"
        effective_selected_step_ids = list(import_result.selected_step_ids)
    else:
        effective_profile = test_value_profile
        if effective_profile is None and config.test_values is not None:
            effective_profile = config.test_values.profile

        if custom_test_values is None:
            effective_custom_values = dict(config.test_values.overrides) if config.test_values is not None else {}
            effective_test_data_values = dict(config.test_data.values) if config.test_data is not None else {}
        else:
            if supports_test_data_schema:
                effective_profile = None
                effective_custom_values = {}
                # Keep raw form-posted values here; the outer normalise below
                # strips to deltas for context/step-selection purposes.
                effective_test_data_values = dict(custom_test_values)
            else:
                effective_custom_values = _normalise_custom_test_values(
                    manifest=manifest,
                    profile_id=effective_profile,
                    custom_values=dict(custom_test_values),
                )
                effective_test_data_values = {}

    # Save participant values BEFORE delta-normalisation so the full snapshot
    # builder can include baseline-equal values in Run Plan storage.
    participant_test_data_values = effective_test_data_values
    if supports_test_data_schema:
        effective_test_data_values = _normalise_test_data_values(
            manifest=manifest,
            test_data_values=effective_test_data_values,
        )

    effective_test_values = (
        TestValuesConfig(
            profile=effective_profile,
            overrides=MappingProxyType(effective_custom_values),
        )
        if effective_profile is not None or bool(effective_custom_values)
        else None
    )
    effective_test_data = (
        TestDataConfig(values=MappingProxyType(effective_test_data_values)) if effective_test_data_values else None
    )

    try:
        test_value_ctx = build_plan_test_value_context(manifest, effective_test_values, effective_test_data)
    except ValueError:
        # Gracefully degrade when profile resolution fails (e.g. unknown profile
        # id in a partial config): treat as no effective test values so
        # conditional steps appear with missing-value status in the preview.
        test_value_ctx = PlanTestValueContext()
    default_plan = TestPlan.default_plan_from_manifest(manifest, test_value_context=test_value_ctx)
    if effective_selection_mode == "select":
        selected_plan = _plan_from_selected_step_ids(default_plan, effective_selected_step_ids)
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
    # Compile run configuration before building Run Plan so that
    # baseline_delta_keys drives both the full snapshot and is_exploratory_run.
    selected_step_id_set = {entry.step_id for entry in selected_plan.entries if entry.selected}
    try:
        run_config = compile_run_configuration(
            manifest=manifest,
            selected_step_ids=selected_step_id_set,
            test_data_values=effective_test_data_values,
        )
    except ValueError as exc:
        run_config = None
        test_data_blockers: tuple[str, ...] = (str(exc),)
    else:
        if run_config is not None and run_config.missing_required_keys:
            test_data_blockers = (
                f"Run cannot proceed: test data is missing required keys: {sorted(run_config.missing_required_keys)}",
            )
        else:
            test_data_blockers = ()
    # Build the full executable snapshot for Run Plan storage.
    # For new-schema manifests this includes baseline values for all referenced
    # keys plus participant-supplied values for any allow-listed key, giving a
    # complete stand-alone document that launch can execute directly.
    run_plan_test_data_values: dict[str, str] = (
        _build_full_test_data_snapshot(
            manifest=manifest,
            selected_plan=selected_plan,
            participant_values=participant_test_data_values,
        )
        if supports_test_data_schema
        else effective_test_data_values
    )
    run_plan = RunPlan(
        schema_version="1",
        suite=RunPlanSuiteCoordinates(
            id=suite_metadata.catalog_id if suite_metadata is not None else "custom",
            version=suite_metadata.spec_version if suite_metadata is not None else "unknown",
            manifest_hash=manifest_hash,
        ),
        selected_step_ids=tuple(entry.step_id for entry in selected_plan.entries if entry.selected),
        test_values=RunPlanTestValues(
            profile=effective_profile,
            custom_values=effective_custom_values,
        ),
        test_data=RunPlanTestData(values=MappingProxyType(run_plan_test_data_values)),
    )
    legacy_test_values_warning = config.test_values is not None or import_result.uses_legacy_test_values
    run_plan_json = json.dumps(serialise_run_plan(run_plan), indent=2)
    test_value_fields = _build_test_value_field_specs(manifest=manifest, run_plan=run_plan)
    test_value_step_groups = _build_test_value_step_groups(manifest=manifest, run_plan=run_plan)
    # Derive exploratory status from baseline_delta_keys for new-schema manifests
    # so that a Run Plan containing baseline-equal values is not treated as
    # exploratory merely because testData.values is non-empty.
    is_exploratory_run = _is_exploratory_run(
        run_plan,
        supports_test_data_schema=supports_test_data_schema,
        baseline_delta_keys=run_config.baseline_delta_keys if run_config is not None else frozenset(),
    )
    exploratory_ack_required = is_exploratory_run
    manifest_blockers = _launch_blockers(manifest)
    legacy_schema_blockers: tuple[str, ...] = ()
    if supports_test_data_schema and config.test_values is not None:
        legacy_schema_blockers = (
            "Config uses legacy testValues.profile/testValues.overrides, but this suite uses "
            "testValues.baseline + testData.values. Remove testValues and move custom keys to testData.values.",
        )
    all_blockers_list = [*manifest_blockers, *capability_blockers, *import_result.launch_blockers, *test_data_blockers]
    all_blockers_list.extend(legacy_schema_blockers)
    if exploratory_ack_required and not exploratory_ack:
        all_blockers_list.append("Exploratory Run acknowledgement required — check the acknowledgement box to launch.")
    all_blockers = tuple(all_blockers_list)
    return PlanPreview(
        config=config,
        manifest=manifest,
        suite_metadata=suite_metadata,
        default_plan=default_plan,
        selected_plan=selected_plan,
        rows=rows,
        launch_supported=not all_blockers,
        launch_blockers=all_blockers,
        is_exploratory_run=is_exploratory_run,
        exploratory_ack_required=exploratory_ack_required,
        certification_eligible_by_selection=selected_plan.is_eligible_by_selection() and not is_exploratory_run,
        auth_inventory=auth_inventory,
        step_auth_requirements=step_auth_requirements,
        capability_warnings=capability_warnings,
        run_plan=run_plan,
        legacy_test_values_warning=legacy_test_values_warning,
        run_plan_json=run_plan_json,
        plan_drift_warnings=import_result.warnings,
        plan_drift_blocks_launch=import_result.plan_drift_blocks_launch,
        test_value_fields=test_value_fields,
        test_value_step_groups=test_value_step_groups,
        tree_nodes=tree_nodes,
        baseline_delta_keys=run_config.baseline_delta_keys if run_config is not None else frozenset(),
    )


def _is_exploratory_run(
    run_plan: RunPlan,
    *,
    supports_test_data_schema: bool = False,
    baseline_delta_keys: frozenset[str] = frozenset(),
) -> bool:
    """Return whether a Run Plan is exploratory for certification purposes.

    For new-schema manifests (those with a ``testValues`` baseline block),
    exploratory status is determined by whether any effective test-data value
    referenced by the selected steps differs from the manifest baseline, as
    reflected in ``baseline_delta_keys`` from
    :func:`~conformance.run_configuration.compile_run_configuration`.

    For legacy manifests (those using ``testValueProfiles``), exploratory
    status is determined by a non-default profile selection or any custom
    override value.

    Args:
        run_plan: Run Plan generated from participant preview selections.
        supports_test_data_schema: Whether the manifest uses the new
            ``testValues`` baseline/allowedCustomKeys contract.  Defaults
            to ``False`` so that callers using only a ``RunPlan`` (e.g.
            unit tests) continue to exercise the legacy path without change.
        baseline_delta_keys: Keys whose effective value differs from the
            manifest baseline, as computed by
            :func:`~conformance.run_configuration.compile_run_configuration`.
            Meaningful only when ``supports_test_data_schema`` is ``True``.

    Returns:
        ``True`` when the run is exploratory, otherwise ``False``.
    """
    if supports_test_data_schema:
        return bool(baseline_delta_keys)
    return run_plan.test_values.profile is not None or bool(run_plan.test_values.custom_values)


def _build_full_test_data_snapshot(
    *,
    manifest: Manifest,
    selected_plan: TestPlan,
    participant_values: Mapping[str, str],
) -> dict[str, str]:
    """Build the full executable test-data snapshot for Run Plan storage.

    Combines manifest baseline values (for keys referenced by selected steps)
    with participant-supplied values (for any allow-listed key) to produce a
    complete, stand-alone snapshot covering all effective test data.  Generated
    keys are excluded because they are materialised at execution time.

    Args:
        manifest: Manifest currently loaded in the preview.
        selected_plan: The participant-selected execution plan.
        participant_values: Config-supplied or form-posted test-data values
            to overlay on the baseline foundation.

    Returns:
        Mapping of test-data keys to their effective values.  Includes all
        allow-listed non-generated keys referenced by selected steps (seeded
        from the manifest baseline when absent from ``participant_values``),
        plus any additional participant-supplied allow-listed keys.
    """
    manifest_test_values = manifest.test_values
    if manifest_test_values is None:
        return dict(participant_values)
    baseline_values = manifest_test_values.baseline
    allowed_custom_keys = manifest_test_values.allowed_custom_keys
    generated_keys = manifest_test_values.generated_keys
    selected_step_ids = {entry.step_id for entry in selected_plan.entries if entry.selected}
    referenced_keys: set[str] = set()
    for step in manifest.steps:
        if step.id in selected_step_ids:
            referenced_keys.update(step.consumed_test_value_keys)
    snapshot: dict[str, str] = {}
    for key in referenced_keys & allowed_custom_keys:
        if key not in generated_keys and key in baseline_values:
            snapshot[key] = baseline_values[key]
    for key, value in participant_values.items():
        if key in allowed_custom_keys and key not in generated_keys:
            snapshot[key] = value
    return snapshot


def _parse_plan_import_result(
    *,
    run_plan_import: str | None,
    manifest: Manifest,
    current_manifest_hash: str,
) -> PlanImportResult:
    """Parse optional Run Plan import JSON and compute drift metadata.

    Args:
        run_plan_import: Raw Run Plan JSON pasted by the participant.
        manifest: Currently loaded v1 manifest used for preview.
        current_manifest_hash: Hash of the currently loaded manifest bytes.

    Returns:
        Parsed import metadata with preview warnings, launch blockers, and
        filtered selection/custom-value data for the current manifest.
    """
    raw_import = (run_plan_import or "").strip()
    if raw_import == "":
        return _empty_plan_import_result()

    try:
        imported_plan = parse_run_plan(json.loads(raw_import))
    except json.JSONDecodeError as error:
        return _empty_plan_import_result(
            launch_blockers=(f"Run Plan import must be valid JSON: {error.msg}",),
        )
    except RunPlanParseError as error:
        return _empty_plan_import_result(
            launch_blockers=(f"Run Plan import parse failed: {error}",),
        )

    manifest_step_ids = {step.id for step in manifest.steps}
    stale_step_ids = sorted(
        {step_id for step_id in imported_plan.selected_step_ids if step_id not in manifest_step_ids}
    )
    selected_step_ids = tuple(step_id for step_id in imported_plan.selected_step_ids if step_id in manifest_step_ids)
    allowed_override_keys = (
        manifest.test_value_profiles.allowed_override_keys if manifest.test_value_profiles is not None else frozenset()
    )
    filtered_custom_values = MappingProxyType(
        {key: value for key, value in imported_plan.test_values.custom_values.items() if key in allowed_override_keys}
    )
    stale_custom_keys = sorted(
        key for key in imported_plan.test_values.custom_values if key not in allowed_override_keys
    )
    allowed_test_data_keys = (
        manifest.test_values.allowed_custom_keys if manifest.test_values is not None else frozenset()
    )
    filtered_import_test_data_values = {
        key: value for key, value in imported_plan.test_data.values.items() if key in allowed_test_data_keys
    }
    stale_test_data_keys = sorted(key for key in imported_plan.test_data.values if key not in allowed_test_data_keys)
    filtered_legacy_test_data_values = {
        key: value for key, value in imported_plan.test_values.custom_values.items() if key in allowed_test_data_keys
    }
    stale_legacy_test_data_keys = sorted(
        key for key in imported_plan.test_values.custom_values if key not in allowed_test_data_keys
    )
    legacy_profile_test_data_deltas = _map_profile_to_test_data_deltas(
        manifest=manifest,
        profile_id=imported_plan.test_values.profile,
    )
    merged_test_data_values = {
        **legacy_profile_test_data_deltas,
        **filtered_legacy_test_data_values,
        **filtered_import_test_data_values,
    }
    # Retain participant values from the import as-is (filtered to allowed
    # keys); the full executable snapshot is built in build_plan_preview once
    # the selected plan is known.  Delta-only stripping is deferred to the
    # context-normalisation step that follows.
    normalised_test_data_values = {
        key: value for key, value in merged_test_data_values.items() if key in allowed_test_data_keys
    }
    uses_legacy_test_values = imported_plan.test_values.profile is not None or bool(
        imported_plan.test_values.custom_values
    )

    warnings: list[str] = []
    launch_blockers: list[str] = []
    statuses: list[PlanDriftStatus] = []
    blocks_launch = False
    if imported_plan.suite.manifest_hash != current_manifest_hash:
        warnings.append(
            "Imported Run Plan was created against a different manifest version. "
            "Preview is available but launch is blocked until you re-export from "
            "the current manifest."
        )
        launch_blockers.append(
            "Run Plan manifest hash mismatch — re-export the plan from the current manifest before launching."
        )
        statuses.append("hash_mismatch")
        blocks_launch = True

    for stale_step_id in stale_step_ids:
        warnings.append(
            f"Step '{stale_step_id}' in the imported Run Plan is not present in the current manifest (stale step ID)."
        )
    if stale_step_ids:
        warnings.append(
            f"{len(stale_step_ids)} step(s) from the imported plan are no longer "
            "present in the current manifest and have been removed."
        )
        statuses.append("stale_step_ids")

    for stale_custom_key in stale_custom_keys:
        warnings.append(
            f"Custom value key '{stale_custom_key}' in the imported Run Plan is "
            "not an allowed override key in the current manifest."
        )
    if stale_custom_keys:
        statuses.append("stale_custom_keys")
    stale_import_test_data_keys = sorted(set(stale_test_data_keys) | set(stale_legacy_test_data_keys))
    for stale_test_data_key in stale_import_test_data_keys:
        warnings.append(
            f"Custom value key '{stale_test_data_key}' in the imported Run Plan "
            "is not an allowed testData key in the current manifest."
        )
    if uses_legacy_test_values and manifest.test_values is not None:
        warnings.append(
            "Imported Run Plan uses legacy testValues fields; values have been migrated into testData.values."
        )

    return PlanImportResult(
        imported_plan=imported_plan,
        selected_step_ids=selected_step_ids,
        custom_values=filtered_custom_values,
        test_data_values=MappingProxyType(normalised_test_data_values),
        uses_legacy_test_values=uses_legacy_test_values,
        drift_statuses=tuple(statuses) if statuses else ("ok",),
        warnings=tuple(warnings),
        launch_blockers=tuple(launch_blockers),
        plan_drift_blocks_launch=blocks_launch,
    )


def _empty_plan_import_result(
    *,
    launch_blockers: tuple[str, ...] = (),
) -> PlanImportResult:
    """Return a PlanImportResult representing no applied imported plan.

    Args:
        launch_blockers: Optional launch blockers to include when import parsing
            fails.

    Returns:
        A :class:`PlanImportResult` with no imported plan and optional blockers.
    """
    return PlanImportResult(
        imported_plan=None,
        selected_step_ids=(),
        custom_values=MappingProxyType({}),
        test_data_values=MappingProxyType({}),
        uses_legacy_test_values=False,
        drift_statuses=("ok",),
        warnings=(),
        launch_blockers=launch_blockers,
        plan_drift_blocks_launch=False,
    )


def _map_profile_to_test_data_deltas(*, manifest: Manifest, profile_id: str | None) -> dict[str, str]:
    """Convert a legacy profile selection into test-data deltas from baseline.

    Args:
        manifest: Manifest currently loaded in the preview.
        profile_id: Imported legacy ``testValues.profile`` id.

    Returns:
        Mapping of profile key/value pairs that differ from the manifest
        baseline and are allow-listed by ``testValues.allowedCustomKeys``.
    """
    if profile_id is None or manifest.test_value_profiles is None or manifest.test_values is None:
        return {}
    selected_profile = next(
        (profile for profile in manifest.test_value_profiles.profiles if profile.id == profile_id), None
    )
    if selected_profile is None:
        return {}
    baseline_values = manifest.test_values.baseline
    allowed_custom_keys = manifest.test_values.allowed_custom_keys
    return {
        key: value
        for key, value in selected_profile.values.items()
        if key in allowed_custom_keys and baseline_values.get(key) != value
    }


def _normalise_test_data_values(*, manifest: Manifest, test_data_values: Mapping[str, str]) -> dict[str, str]:
    """Filter test-data values to allowed keys and baseline deltas only.

    Args:
        manifest: Manifest currently loaded in the preview.
        test_data_values: Candidate test-data values to normalise.

    Returns:
        Mapping containing only keys allow-listed by
        ``manifest.test_values.allowed_custom_keys`` whose values differ from
        the suite baseline.
    """
    manifest_test_values = manifest.test_values
    if manifest_test_values is None:
        return dict(test_data_values)
    baseline_values = manifest_test_values.baseline
    allowed_custom_keys = manifest_test_values.allowed_custom_keys
    return {
        key: value
        for key, value in test_data_values.items()
        if key in allowed_custom_keys and baseline_values.get(key) != value
    }


def _normalise_custom_test_values(
    *,
    manifest: Manifest,
    profile_id: str | None,
    custom_values: dict[str, str],
) -> dict[str, str]:
    """Filter posted custom test values to meaningful per-profile override deltas.

    Args:
        manifest: Manifest currently loaded in the preview.
        profile_id: Selected test-value profile id, or ``None`` for default.
        custom_values: Raw ``custom_tv_*`` values extracted from form POST data.

    Returns:
        Filtered mapping containing only allow-listed keys whose values differ
        from the selected profile defaults.
    """
    profile_spec = manifest.test_value_profiles
    if profile_spec is None:
        return {}
    selected_profile_id = profile_id or profile_spec.default_profile_id
    selected_profile = next((profile for profile in profile_spec.profiles if profile.id == selected_profile_id), None)
    if selected_profile is None:
        return {}
    return {
        key: value
        for key, value in custom_values.items()
        if key in profile_spec.allowed_override_keys
        and key not in selected_profile.generated_keys
        and value != selected_profile.values.get(key, "")
    }


def _build_test_value_field_specs(*, manifest: Manifest, run_plan: RunPlan) -> tuple[TestValueFieldSpec, ...]:
    """Build UI field specs for editable test values.

    Args:
        manifest: Manifest currently loaded in the plan preview.
        run_plan: Effective run-plan snapshot for the current preview.

    Returns:
        Tuple of field specs for each editable key, or an empty tuple when no
        test-value UI should be rendered.
    """
    if manifest.test_values is not None:
        return _build_test_data_field_specs(manifest=manifest, run_plan=run_plan)
    return _build_legacy_test_value_field_specs(manifest=manifest, run_plan=run_plan)


def _build_test_data_field_specs(*, manifest: Manifest, run_plan: RunPlan) -> tuple[TestValueFieldSpec, ...]:
    """Build UI field specs for new-schema ``testData.values`` edits.

    Args:
        manifest: Manifest currently loaded in the plan preview.
        run_plan: Effective run-plan snapshot for the current preview.

    Returns:
        Tuple of field specs for selected-step keys that are allow-listed in
        ``testValues.allowedCustomKeys``.
    """
    manifest_test_values = manifest.test_values
    if manifest_test_values is None or not manifest_test_values.allowed_custom_keys:
        return ()

    selected_step_ids = set(run_plan.selected_step_ids)
    referenced_keys: set[str] = set()
    for step in manifest.steps:
        if step.id in selected_step_ids:
            referenced_keys.update(step.consumed_test_value_keys)

    editable_keys = sorted(referenced_keys & manifest_test_values.allowed_custom_keys)
    field_specs: list[TestValueFieldSpec] = []
    for key in editable_keys:
        default_value = manifest_test_values.baseline.get(key, "")
        is_generated = key in manifest_test_values.generated_keys
        # Run Plan now stores the full snapshot, so a key is considered
        # overridden when its stored value differs from the baseline rather
        # than merely being present in the mapping.
        current_value = run_plan.test_data.values.get(key, default_value)
        is_overridden = current_value != default_value
        shape_warning = (
            _infer_shape_warning(key, default_value, current_value) if is_overridden and not is_generated else None
        )
        field_specs.append(
            TestValueFieldSpec(
                key=key,
                default_value=default_value,
                is_overridden=is_overridden,
                current_value=current_value,
                is_generated=is_generated,
                shape_warning=shape_warning,
            )
        )
    return tuple(field_specs)


def _build_legacy_test_value_field_specs(*, manifest: Manifest, run_plan: RunPlan) -> tuple[TestValueFieldSpec, ...]:
    """Build UI field specs for legacy profile-backed test-value overrides.

    Args:
        manifest: Manifest currently loaded in the plan preview.
        run_plan: Effective run-plan snapshot for the current preview.

    Returns:
        Tuple of field specs for each allow-listed legacy profile override key,
        or an empty tuple when no legacy profile override UI should be rendered.
    """
    profile_spec = manifest.test_value_profiles
    if profile_spec is None or not profile_spec.allowed_override_keys:
        return ()
    selected_profile_id = run_plan.test_values.profile or profile_spec.default_profile_id
    selected_profile = next((profile for profile in profile_spec.profiles if profile.id == selected_profile_id), None)
    if selected_profile is None:
        return ()

    field_specs: list[TestValueFieldSpec] = []
    for key in sorted(profile_spec.allowed_override_keys):
        default_value = selected_profile.values.get(key, "")
        is_generated = key in selected_profile.generated_keys
        is_overridden = key in run_plan.test_values.custom_values
        current_value = run_plan.test_values.custom_values[key] if is_overridden else default_value
        shape_warning = (
            _infer_shape_warning(key, default_value, current_value) if is_overridden and not is_generated else None
        )
        field_specs.append(
            TestValueFieldSpec(
                key=key,
                default_value=default_value,
                is_overridden=is_overridden,
                current_value=current_value,
                is_generated=is_generated,
                shape_warning=shape_warning,
            )
        )
    return tuple(field_specs)


_SURFACE_LABEL: dict[str, str] = {
    "request-json-body": "Body",
    "request-form-body": "Form body",
    "request-header": "Headers",
    "request-url": "URL",
}
"""Human-readable labels for each request-area identifier used in tree headings."""

_SURFACE_ORDER: tuple[str, ...] = (
    "request-json-body",
    "request-form-body",
    "request-header",
    "request-url",
)
"""Display order for request surfaces — body surfaces first, URL last."""

_BODY_PATH_PREFIXES: dict[str, str] = {
    "request-json-body": "request.body.",
    "request-form-body": "request.body.fields.",
    "request-header": "request.headers.",
    "request-url": "request.",
}
"""Field-path prefix stripped when computing display segments per request area."""


def _strip_body_prefix(field_path: str, request_area: str) -> str:
    """Strip the request-area prefix from a manifest field path.

    Args:
        field_path: Full dot-path field path from a
            :class:`conformance.manifest.TestValueReference` (e.g.
            ``"request.body.Data.Initiation.CreditorAccount.Name"``).
        request_area: Request-area identifier matching the reference
            (e.g. ``"request-json-body"``).

    Returns:
        The field path with its leading area prefix removed, or the
        original path unchanged when no known prefix matches.
    """
    prefix = _BODY_PATH_PREFIXES.get(request_area, "")
    if prefix and field_path.startswith(prefix):
        return field_path[len(prefix) :]
    return field_path


def _insert_body_path(
    trie: dict[str, object],
    segments: list[str],
    key: str,
    is_canonical: bool,
) -> None:
    """Insert a body path into a mutable trie structure.

    Intermediate segments create nested ``dict`` nodes; the final segment
    records the leaf ``(key, is_canonical)`` pair alongside any children.

    Args:
        trie: Mutable dict representing the current trie level to insert into.
        segments: Ordered path segments from the stripped field path
            (e.g. ``["Data", "Initiation", "CreditorAccount", "Name"]``).
        key: Test-value key to record at the leaf segment.
        is_canonical: Whether the canonical editable input is rendered here.
    """
    node = trie
    for segment in segments[:-1]:
        if segment not in node:
            node[segment] = {"_children": {}, "_leaf": None}
        child = cast("dict[str, object]", node[segment])
        node = cast("dict[str, object]", child["_children"])
    leaf_segment = segments[-1]
    if leaf_segment not in node:
        node[leaf_segment] = {"_children": {}, "_leaf": None}
    entry = cast("dict[str, object]", node[leaf_segment])
    entry["_leaf"] = (key, is_canonical)


def _trie_to_rows(
    trie: dict[str, object],
    depth: int,
    field_specs_by_key: dict[str, TestValueFieldSpec],
) -> list[TestValueTreeRow]:
    """DFS-traverse a body trie and emit flat tree rows with depth metadata.

    Args:
        trie: Current trie node whose keys are sorted path-segment labels
            and whose values are ``{"_children": …, "_leaf": …}`` dicts.
        depth: Indentation depth for rows emitted at this level.
        field_specs_by_key: Mapping of test-value key to its full field spec
            for populating leaf row display data.

    Returns:
        Ordered list of :class:`TestValueTreeRow` items suitable for
        direct template iteration.
    """
    rows: list[TestValueTreeRow] = []
    for label in sorted(trie.keys()):
        entry = cast("dict[str, object]", trie[label])
        children: dict[str, object] = cast("dict[str, object]", entry["_children"])
        leaf: tuple[str, bool] | None = cast("tuple[str, bool] | None", entry["_leaf"])
        if children:
            rows.append(TestValueTreeRow(row_type="group", depth=depth, label=label, key=None, is_canonical=False))
            rows.extend(_trie_to_rows(children, depth + 1, field_specs_by_key))
        if leaf is not None:
            leaf_key, leaf_is_canonical = leaf
            leaf_depth = depth + 1 if children else depth
            spec = field_specs_by_key.get(leaf_key)
            rows.append(
                TestValueTreeRow(
                    row_type="leaf",
                    depth=leaf_depth,
                    label=label,
                    key=leaf_key,
                    is_canonical=leaf_is_canonical,
                    default_value=spec.default_value if spec else "",
                    current_value=spec.current_value if spec else "",
                    is_overridden=spec.is_overridden if spec else False,
                    is_generated=spec.is_generated if spec else False,
                    shape_warning=spec.shape_warning if spec else None,
                )
            )
    return rows


def _build_surface_rows_for_area(
    refs: list[tuple[str, str, bool]],
    request_area: str,
    field_specs_by_key: dict[str, TestValueFieldSpec],
) -> tuple[TestValueTreeRow, ...]:
    """Build tree rows for one request surface from a list of references.

    For JSON body surfaces the path is decomposed into a nested trie and
    emitted as a depth-indented flat row list.  For flat surfaces (headers,
    URL, form body) a single leaf row per reference is emitted.

    Args:
        refs: Triples of ``(field_path, key, is_canonical)`` for this surface.
        request_area: Request-area identifier controlling path stripping and
            tree structure (e.g. ``"request-json-body"``).
        field_specs_by_key: Mapping of test-value key to its full field spec
            for populating leaf row display data.

    Returns:
        Tuple of tree rows ready for template iteration.
    """
    if request_area == "request-json-body":
        trie: dict[str, object] = {}
        for field_path, key, is_canonical in refs:
            stripped = _strip_body_prefix(field_path, request_area)
            segments = stripped.split(".")
            _insert_body_path(trie, segments, key, is_canonical)
        return tuple(_trie_to_rows(trie, depth=0, field_specs_by_key=field_specs_by_key))
    rows: list[TestValueTreeRow] = []
    for field_path, key, is_canonical in sorted(refs, key=lambda r: r[0]):
        stripped = _strip_body_prefix(field_path, request_area)
        spec = field_specs_by_key.get(key)
        rows.append(
            TestValueTreeRow(
                row_type="leaf",
                depth=0,
                label=stripped,
                key=key,
                is_canonical=is_canonical,
                default_value=spec.default_value if spec else "",
                current_value=spec.current_value if spec else "",
                is_overridden=spec.is_overridden if spec else False,
                is_generated=spec.is_generated if spec else False,
                shape_warning=spec.shape_warning if spec else None,
            )
        )
    return tuple(rows)


def _build_test_value_step_groups(
    *,
    manifest: Manifest,
    run_plan: RunPlan,
) -> tuple[TestValueStepGroup, ...]:
    """Build request-shaped test-value step groups for the Plan Builder tree UI.

    Only new-schema manifests with a ``testValues`` block and at least one
    ``allowedCustomKeys`` entry produce output.  Legacy manifests (those using
    ``testValueProfiles``) return an empty tuple; the template falls back to
    the flat ``test_value_fields`` grid for those.

    Each selected step with test-value references yields a
    :class:`TestValueStepGroup`.  To avoid duplicate ``name="custom_tv_<key>"``
    form inputs, each key is marked canonical in the first step where it
    appears among the selected steps in manifest order.  Subsequent steps
    that reference the same key receive ``is_canonical=False`` leaf rows and
    the template renders a read-only "also edited above" reference instead.

    Args:
        manifest: Manifest currently loaded in the plan preview.
        run_plan: Effective run-plan snapshot for the current preview.

    Returns:
        Ordered tuple of step groups for tree rendering, or an empty
        tuple when the manifest has no new-schema ``testValues`` block.
    """
    manifest_test_values = manifest.test_values
    if manifest_test_values is None or not manifest_test_values.allowed_custom_keys:
        return ()

    selected_step_ids = set(run_plan.selected_step_ids)
    allowed_keys = manifest_test_values.allowed_custom_keys

    field_specs_by_key: dict[str, TestValueFieldSpec] = {}
    for key in allowed_keys:
        default_value = manifest_test_values.baseline.get(key, "")
        is_generated = key in manifest_test_values.generated_keys
        # Run Plan stores a full snapshot so use value-differs-from-baseline
        # as the overridden signal rather than key-present-in-mapping.
        current_value = run_plan.test_data.values.get(key, default_value)
        is_overridden = current_value != default_value
        shape_warning = (
            _infer_shape_warning(key, default_value, current_value) if is_overridden and not is_generated else None
        )
        field_specs_by_key[key] = TestValueFieldSpec(
            key=key,
            default_value=default_value,
            is_overridden=is_overridden,
            current_value=current_value,
            is_generated=is_generated,
            shape_warning=shape_warning,
        )

    canonical_rendered: set[str] = set()
    groups: list[TestValueStepGroup] = []

    for step in manifest.steps:
        if step.id not in selected_step_ids:
            continue
        refs_for_step = [ref for ref in step.test_value_references if ref.key in allowed_keys]
        if not refs_for_step:
            continue

        refs_by_area: dict[str, list[tuple[str, str, bool]]] = {}
        step_has_canonical = False
        for ref in refs_for_step:
            is_canonical = ref.key not in canonical_rendered
            if is_canonical:
                canonical_rendered.add(ref.key)
                step_has_canonical = True
            refs_by_area.setdefault(ref.request_area, []).append((ref.field_path, ref.key, is_canonical))

        surfaces: list[TestValueSurfaceTree] = []
        for area in _SURFACE_ORDER:
            area_refs = refs_by_area.get(area)
            if not area_refs:
                continue
            rows = _build_surface_rows_for_area(area_refs, area, field_specs_by_key)
            surfaces.append(
                TestValueSurfaceTree(
                    surface_label=_SURFACE_LABEL.get(area, area),
                    request_area=area,
                    rows=rows,
                )
            )
        for area, area_refs in refs_by_area.items():
            if area not in _SURFACE_ORDER:
                rows = _build_surface_rows_for_area(area_refs, area, field_specs_by_key)
                surfaces.append(
                    TestValueSurfaceTree(
                        surface_label=_SURFACE_LABEL.get(area, area),
                        request_area=area,
                        rows=rows,
                    )
                )

        groups.append(
            TestValueStepGroup(
                step_id=step.id,
                step_name=step.name,
                surfaces=tuple(surfaces),
                has_canonical_keys=step_has_canonical,
            )
        )

    return tuple(groups)


def _infer_shape_warning(key: str, default_value: str, override_value: str) -> str | None:
    """Return advisory warning text for obvious test-value shape mismatches.

    Args:
        key: Override key being validated.
        default_value: Default value from the selected profile.
        override_value: Participant-supplied override value.

    Returns:
        Advisory warning text when the override shape differs from the default
        shape, or ``None`` when no warning applies.
    """
    if default_value == "":
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", default_value):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", override_value):
            return None
        return f"Expected ISO date for {key} (e.g. 2025-01-15)"
    if _matches_iso_datetime_shape(default_value):
        if _matches_iso_datetime_shape(override_value):
            return None
        return f"Expected ISO datetime for {key} (e.g. 2025-01-15T10:30:00Z)"
    if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", default_value):
        if re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            override_value,
        ):
            return None
        return f"Expected UUID for {key} (e.g. 123e4567-e89b-12d3-a456-426614174000)"
    if re.match(r"^https?://", default_value):
        if re.match(r"^https?://", override_value):
            return None
        return f"Expected HTTPS URL for {key} (e.g. https://example.com/path)"
    if re.fullmatch(r"\d+", default_value):
        if re.fullmatch(r"\d+", override_value):
            return None
        return f"Expected integer for {key} (e.g. 123)"
    if re.fullmatch(r"\d+\.\d+", default_value):
        try:
            float(override_value)
        except ValueError:
            return f"Expected decimal for {key} (e.g. 10.50)"
        return None
    if default_value in {"true", "false"}:
        if override_value in {"true", "false"}:
            return None
        return f"Expected boolean for {key} (true or false)"
    return None


def _matches_iso_datetime_shape(value: str) -> bool:
    """Return whether a value matches a basic ISO-8601 datetime shape.

    Args:
        value: Candidate datetime string.

    Returns:
        ``True`` when the value matches ``YYYY-MM-DDTHH:MM:SS`` with optional
        fractional seconds and timezone suffix.
    """
    return bool(
        re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?",
            value,
        )
    )


def _compute_preview_manifest_hash(
    *,
    manifest: Manifest,
    suite_metadata: SuiteMetadata | None,
    manifest_bytes: bytes | None,
) -> str:
    """Resolve raw bytes for preview drift detection and return their hash.

    Args:
        manifest: Parsed manifest currently being previewed.
        suite_metadata: Optional metadata for config-selected bundled suites.
        manifest_bytes: Optional raw bytes supplied directly by the caller.

    Returns:
        Canonical ``sha256:<hex>`` hash of the best-available manifest bytes.
    """
    if manifest_bytes is not None:
        return compute_manifest_hash(manifest_bytes)
    if suite_metadata is not None:
        try:
            suite_manifest_bytes = (
                resources.files("conformance.suites").joinpath(suite_metadata.manifest_resource).read_bytes()
            )
            return compute_manifest_hash(suite_manifest_bytes)
        except FileNotFoundError, ModuleNotFoundError, OSError:
            pass
    fallback_source = repr(manifest).encode("utf-8")
    return compute_manifest_hash(fallback_source)


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


def _submitted_manifest_json_bytes(raw_value: object) -> bytes | None:
    """Return UTF-8 bytes for submitted manifest JSON text when available.

    Args:
        raw_value: Raw submitted ``manifest_json`` form value from ``self.data``.

    Returns:
        UTF-8 encoded JSON bytes when a non-blank string was supplied, or
        ``None`` when the manifest field is absent/blank.
    """
    if not isinstance(raw_value, str):
        return None
    if raw_value.strip() == "":
        return None
    return raw_value.encode("utf-8")


def _extract_custom_test_values(raw_data: Mapping[str, object]) -> dict[str, str]:
    """Extract ``custom_tv_*`` dynamic fields from submitted browser form data.

    Args:
        raw_data: Submitted form payload mapping (typically ``self.data``).

    Returns:
        Mapping of override keys to submitted string values. Empty-string values
        are preserved. Keys absent from the POST payload are omitted.
    """
    custom_values: dict[str, str] = {}
    for field_name in raw_data:
        if not field_name.startswith("custom_tv_"):
            continue
        key = field_name.removeprefix("custom_tv_")
        if key == "":
            continue
        string_value = _coerce_form_value_to_string(raw_data.get(field_name))
        if string_value is None:
            continue
        custom_values[key] = string_value
    return custom_values


def _has_custom_test_value_fields(raw_data: Mapping[str, object]) -> bool:
    """Return whether posted form data contains any ``custom_tv_*`` fields.

    Args:
        raw_data: Submitted form payload mapping (typically ``self.data``).

    Returns:
        ``True`` when at least one dynamic custom test-value field is present.
    """
    return any(field_name.startswith("custom_tv_") for field_name in raw_data)


def _coerce_form_value_to_string(value: object) -> str | None:
    """Convert a raw form value to a submitted string when possible.

    Args:
        value: Raw value from form payload mapping.

    Returns:
        Submitted string value, or ``None`` when the value cannot be treated as
        a single string.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, tuple) and value and isinstance(value[-1], str):
        return value[-1]
    if isinstance(value, list) and value and isinstance(value[-1], str):
        return value[-1]
    return None


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
    """Return known model-bank examples for discovery input.

    Returns:
        Guided model-bank examples that can populate editable discovery URL
        fields.
    """
    return (
        GuidedModelBankOption(
            value="ozone-obie-preprod",
            label="Ozone OBIE pre-production",
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
            "guided_discovery_url",
            "guided_spec_version",
            "guided_api",
            "guided_suite",
            "guided_client_id",
            "guided_redirect_uri",
            "guided_authorization_endpoint",
            "guided_open_banking_intent_id",
            "guided_resource_base_url",
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
    using an :class:`~conformance.environment_capabilities.EnvironmentReference`
    resolved from discovery URL first, then the legacy config environment label.
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
    environment = _environment_reference_for_config(config)
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


def _environment_reference_for_config(config: ModelBankConfig) -> EnvironmentReference:
    """Resolve an environment-capability reference for a config preview.

    Args:
        config: Validated model-bank configuration.

    Returns:
        Preset reference when the discovery URL matches a known preset;
        otherwise a conservative custom reference.
    """
    for preset in list_environment_presets():
        if config.discovery_url == preset.discovery_url:
            preset_reference = make_preset_environment_reference(preset.key)
            if preset_reference is not None:
                return preset_reference
    return make_custom_environment_reference(label=config.environment or "target/discovery config")


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


# ---------------------------------------------------------------------------
# Plugin-aware staged UI helpers (endpoint-first DCR/plugin architecture)
# ---------------------------------------------------------------------------

#: Module-level singleton registry pre-populated with bundled plugins.
_PLUGIN_REGISTRY: PluginRegistry = PluginRegistry()
_PLUGIN_REGISTRY.register(ReadWritePlugin())
_PLUGIN_REGISTRY.register(DcrPlugin())


@dataclass(frozen=True)
class StandardOption:
    """One standard option for the guided staged UI.

    Attributes:
        id: Machine-readable standard identifier (e.g. ``"obl"``).
        display_label: Human-readable label shown in the UI.
    """

    id: str
    display_label: str


@dataclass(frozen=True)
class SpecificationOption:
    """One specification option for the guided staged UI.

    Attributes:
        id: Machine-readable specification identifier (e.g. ``"read-write"``).
        display_label: Human-readable label shown in the UI.
        standard: Standard identifier this specification belongs to.
    """

    id: str
    display_label: str
    standard: str


@dataclass(frozen=True)
class VersionOption:
    """One specification version option for the guided staged UI.

    Attributes:
        version: Specification version string (e.g. ``"v4.0.1"``).
        standard: Standard identifier.
        specification: Specification identifier.
    """

    version: str
    standard: str
    specification: str


@dataclass(frozen=True)
class ResourceGroupOption:
    """One resource-group option for the guided staged UI.

    Attributes:
        id: Machine-readable resource-group identifier (e.g. ``"ais"``).
        display_label: Human-readable label shown in the UI.
        standard: Standard identifier.
        specification: Specification identifier.
        version: Specification version string.
    """

    id: str
    display_label: str
    standard: str
    specification: str
    version: str


@dataclass(frozen=True)
class EndpointCoverageOption:
    """One endpoint option for the coverage selection step.

    Attributes:
        endpoint_id: Stable endpoint identifier.
        display_label: Human-readable label shown in the UI.
        method: HTTP method string.
        path: API path template.
        resource_group: Resource-group the endpoint belongs to.
        requirement: Requirement level (mandatory/conditional/optional).
    """

    endpoint_id: str
    display_label: str
    method: str
    path: str
    resource_group: str | None
    requirement: str


def plugin_registry() -> PluginRegistry:
    """Return the module-level plugin registry singleton.

    Returns:
        The :class:`~conformance.plugins.registry.PluginRegistry` pre-populated
        with all bundled plugins.
    """
    return _PLUGIN_REGISTRY


def standard_options() -> tuple[StandardOption, ...]:
    """Return the ordered list of standards available in the staged UI.

    Currently only Open Banking Limited (``"obl"``) is supported.

    Returns:
        Ordered tuple of :class:`StandardOption` instances.
    """
    return (StandardOption(id="obl", display_label="Open Banking Limited"),)


def specification_options(*, standard: str) -> tuple[SpecificationOption, ...]:
    """Return specification options for a given standard from registered plugins.

    Iterates over all registered plugins and collects the distinct
    specifications they expose for the requested standard.

    Args:
        standard: Standard identifier to filter by (e.g. ``"obl"``).

    Returns:
        Ordered tuple of :class:`SpecificationOption` instances whose
        ``standard`` field matches the given value.
    """
    seen: set[tuple[str, str]] = set()
    options: list[SpecificationOption] = []
    for plugin_id in _PLUGIN_REGISTRY.plugin_ids:
        plugin = _PLUGIN_REGISTRY.get(plugin_id)
        meta = plugin.target_metadata()
        key = (meta.standard, meta.specification)
        if meta.standard != standard or key in seen:
            continue
        seen.add(key)
        options.append(
            SpecificationOption(
                id=meta.specification,
                display_label=meta.display_label or meta.specification,
                standard=meta.standard,
            )
        )
    return tuple(options)


def version_options(*, standard: str, specification: str) -> tuple[VersionOption, ...]:
    """Return version options for a standard/specification pair from registered plugins.

    Args:
        standard: Standard identifier (e.g. ``"obl"``).
        specification: Specification identifier (e.g. ``"read-write"``).

    Returns:
        Ordered tuple of :class:`VersionOption` instances for all
        supported versions of the matching plugin, in the order declared
        by :attr:`~conformance.plugins.domain.PluginTargetMetadata.supported_versions`.
    """
    options: list[VersionOption] = []
    for plugin_id in _PLUGIN_REGISTRY.plugin_ids:
        plugin = _PLUGIN_REGISTRY.get(plugin_id)
        meta = plugin.target_metadata()
        if meta.standard != standard or meta.specification != specification:
            continue
        for version in meta.supported_versions:
            options.append(
                VersionOption(
                    version=version,
                    standard=standard,
                    specification=specification,
                )
            )
    return tuple(options)


def resource_group_options(
    *,
    standard: str,
    specification: str,
    version: str,
) -> tuple[ResourceGroupOption, ...]:
    """Return resource-group options for the guided staged UI selection step.

    Resolves the plugin for the given coordinates and returns one option per
    resource group declared by the plugin's target metadata.  Returns an empty
    tuple for plugins that do not use resource groups (e.g. DCR).

    Args:
        standard: Standard identifier (e.g. ``"obl"``).
        specification: Specification identifier (e.g. ``"read-write"``).
        version: Specification version string (e.g. ``"v4.0.1"``).

    Returns:
        Ordered tuple of :class:`ResourceGroupOption` instances, or an empty
        tuple when the specification does not use resource groups or when no
        plugin matches.
    """
    target = TestTargetConfig(
        standard=standard,  # type: ignore[arg-type]
        specification=specification,  # type: ignore[arg-type]
        security_profile="fapi1-advanced",
        specification_version=version,
    )
    try:
        plugin = _PLUGIN_REGISTRY.resolve(target)
    except Exception:  # noqa: BLE001 — registry miss returns empty options
        return ()
    meta = plugin.target_metadata()
    if not meta.uses_resource_groups:
        return ()
    _rg_labels: dict[str, str] = {
        "ais": "Accounts and Transactions (AIS)",
        "pis": "Payments (PIS)",
        "cbpii": "Confirmation of Funds (CBPII)",
        "vrp": "Variable Recurring Payments (VRP)",
    }
    return tuple(
        ResourceGroupOption(
            id=rg,
            display_label=_rg_labels.get(rg, rg.upper()),
            standard=standard,
            specification=specification,
            version=version,
        )
        for rg in meta.resource_groups
    )


def endpoint_coverage_options(
    *,
    standard: str,
    specification: str,
    version: str,
    resource_groups: tuple[str, ...],
) -> tuple[EndpointCoverageOption, ...]:
    """Return endpoint coverage options for the guided staged UI.

    Loads the catalogue for the given target and returns one option per
    endpoint entry, filtered to those whose ``resource_group`` is in the
    requested ``resource_groups`` set.  When ``resource_groups`` is empty
    all endpoints are returned.

    Args:
        standard: Standard identifier (e.g. ``"obl"``).
        specification: Specification identifier (e.g. ``"read-write"``).
        version: Specification version string (e.g. ``"v4.0.1"``).
        resource_groups: Tuple of selected resource-group identifiers to
            filter by.  An empty tuple returns all endpoints.

    Returns:
        Ordered tuple of :class:`EndpointCoverageOption` instances for the
        matching endpoints, in catalogue order.
    """
    target = TestTargetConfig(
        standard=standard,  # type: ignore[arg-type]
        specification=specification,  # type: ignore[arg-type]
        security_profile="fapi1-advanced",
        specification_version=version,
        resource_groups=resource_groups,
    )
    try:
        plugin = _PLUGIN_REGISTRY.resolve(target)
        catalogue = plugin.load_catalogue(target)
    except Exception:  # noqa: BLE001 — registry miss or I/O error → empty
        return ()

    rg_filter: frozenset[str] = frozenset(resource_groups) if resource_groups else frozenset()
    return tuple(
        EndpointCoverageOption(
            endpoint_id=entry.endpoint_id,
            display_label=entry.display_label,
            method=entry.method,
            path=entry.path,
            resource_group=entry.resource_group,
            requirement=entry.requirement,
        )
        for entry in catalogue.endpoints
        if not rg_filter or entry.resource_group in rg_filter
    )


def staged_catalogue_data() -> JsonObject:
    """Return catalogue metadata consumed by the staged endpoint-first UI.

    The staged browser journey is deliberately backed by the same plugin
    catalogues used by catalogue-native planning.  This payload gives the
    client-side state machine enough reviewed metadata to render endpoint or
    operation coverage, catalogue-driven field prompts, readiness implications,
    applicable test previews, and a valid RunPlanV2 export with the live
    catalogue hash.

    Returns:
        JSON-compatible mapping keyed by standard, specification, and version.
    """
    result: JsonObject = {}
    for std in standard_options():
        specs_payload: JsonObject = {}
        for spec in specification_options(standard=std.id):
            versions_payload: JsonObject = {}
            for version in version_options(standard=std.id, specification=spec.id):
                target = TestTargetConfig(
                    standard=cast(Standard, std.id),
                    specification=cast(Specification, spec.id),
                    security_profile="fapi1-advanced",
                    specification_version=version.version,
                )
                plugin = _PLUGIN_REGISTRY.resolve(target)
                catalogue = plugin.load_catalogue(target)
                versions_payload[version.version] = {
                    "pluginId": plugin.plugin_id,
                    "usesResourceGroups": bool(catalogue.resource_groups),
                    "catalogueHash": catalogue.identity.content_hash,
                    "resourceGroups": [
                        {
                            "id": group.resource_group,
                            "displayLabel": group.display_label,
                            "requirement": group.requirement,
                        }
                        for group in catalogue.resource_groups
                    ],
                    "endpoints": [
                        {
                            "endpointId": endpoint.endpoint_id,
                            "operation": endpoint.operation,
                            "displayLabel": endpoint.display_label,
                            "method": endpoint.method,
                            "path": endpoint.path,
                            "resourceGroup": endpoint.resource_group,
                            "requirement": endpoint.requirement,
                        }
                        for endpoint in catalogue.endpoints
                    ],
                    "fieldSchemas": [
                        _field_schema_payload(field_schema)
                        for field_schema in catalogue.field_schemas
                        if _is_runtime_mapped_field(
                            specification=target.specification,
                            field_schema=field_schema,
                        )
                    ],
                    "readinessPolicy": _readiness_policy_payload(catalogue.readiness_policy),
                    "executableTests": [_executable_test_payload(test) for test in catalogue.executable_tests],
                }
            specs_payload[spec.id] = versions_payload
        result[std.id] = specs_payload
    return result


def _is_runtime_mapped_field(*, specification: str, field_schema: CatalogueFieldSchema) -> bool:
    """Return whether a catalogue field is saved into the runtime config.

    Args:
        specification: Target specification identifier owning the catalogue.
        field_schema: Catalogue field metadata being considered for browser
            prompting.

    Returns:
        ``True`` when the field has an explicit saved-config/runtime mapping.
    """
    return field_schema.field_id in _RUNTIME_MAPPED_FIELD_IDS.get(specification, frozenset())


def _field_schema_payload(field_schema: CatalogueFieldSchema) -> JsonObject:
    """Serialise one catalogue field schema for the staged UI.

    Args:
        field_schema: Parsed catalogue field metadata.

    Returns:
        JSON-compatible field schema payload.
    """
    return {
        "fieldId": field_schema.field_id,
        "displayLabel": field_schema.display_label,
        "scope": field_schema.scope,
        "valueType": field_schema.value_type,
        "required": field_schema.required,
        "sensitive": field_schema.sensitive,
    }


def _readiness_policy_payload(readiness_policy: CatalogueReadinessPolicy | None) -> JsonObject | None:
    """Serialise catalogue readiness policy metadata for browser preview.

    Args:
        readiness_policy: Parsed catalogue readiness policy, or ``None``.

    Returns:
        JSON-compatible readiness policy payload, or ``None``.
    """
    if readiness_policy is None:
        return None
    return {
        "policyId": readiness_policy.policy_id,
        "certificationStatus": readiness_policy.certification_status,
        "omittedMandatoryOutcome": readiness_policy.omitted_mandatory_outcome,
        "failedSelectedOutcome": readiness_policy.failed_selected_outcome,
    }


def _executable_test_payload(test: CatalogueExecutableTest) -> JsonObject:
    """Serialise one executable catalogue test for staged UI previews.

    Args:
        test: Parsed executable test definition.

    Returns:
        JSON-compatible executable test payload.
    """
    return {
        "testId": test.test_id,
        "displayLabel": test.display_label,
        "endpointId": test.endpoint_id,
        "resourceGroup": test.resource_group,
    }


def staged_ui_context() -> dict[str, object]:
    """Build the base template context for the staged plan-builder UI.

    Returns the initial stage data needed to render the first step of the
    staged journey (standard selection) along with options for all stages so
    the client-side state machine can populate subsequent steps.

    Returns:
        Template context mapping with ``standard_options``,
        ``specification_options_by_standard``, and plugin metadata.
    """
    stds = standard_options()
    all_specs: dict[str, list[dict[str, object]]] = {}
    all_versions: dict[str, dict[str, list[dict[str, object]]]] = {}
    all_rgs: dict[str, dict[str, dict[str, list[dict[str, object]]]]] = {}

    for std in stds:
        specs = specification_options(standard=std.id)
        all_specs[std.id] = [{"id": s.id, "displayLabel": s.display_label} for s in specs]
        all_versions[std.id] = {}
        all_rgs[std.id] = {}
        for spec in specs:
            versions = version_options(standard=std.id, specification=spec.id)
            all_versions[std.id][spec.id] = [{"version": v.version} for v in versions]
            all_rgs[std.id][spec.id] = {}
            for ver in versions:
                rgs = resource_group_options(
                    standard=std.id,
                    specification=spec.id,
                    version=ver.version,
                )
                all_rgs[std.id][spec.id][ver.version] = [{"id": rg.id, "displayLabel": rg.display_label} for rg in rgs]

    return {
        "staged_standard_options": stds,
        "staged_specification_options": json.dumps(all_specs),
        "staged_version_options": json.dumps(all_versions),
        "staged_resource_group_options": json.dumps(all_rgs),
        "staged_catalogue_data": json.dumps(staged_catalogue_data()),
    }
