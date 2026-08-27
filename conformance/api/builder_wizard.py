"""Forms and presenters for the multi-page browser test-plan wizard."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast

from django import forms

from conformance.api.builder_draft_store import BuilderDraft
from conformance.catalogue import (
    CatalogueError,
    CatalogueTestCase,
    EndpointRef,
    HttpMethod,
    PlanDocumentBoundary,
    PlanDocumentV2,
    RuntimeInputRequirement,
    SecurityProfile,
    TestCatalogue,
    business_test_data_from_plan_config,
    catalogue_areas_for_plan_document_boundary,
    compile_test_plan_document,
    parse_test_plan_document,
    plan_document_to_json_object,
    security_environment_from_plan_config,
    supported_plan_document_boundaries,
)
from conformance.catalogue_registry import supported_catalogues
from conformance.json_types import JsonObject, JsonValue
from conformance.model_bank_config import ConfigError, parse_model_bank_config
from conformance.test_plan_validation import safe_test_plan_snapshot
from conformance.url_validation import HttpsUrlValidationError, validate_https_url

_SCHEME_LABELS = {"open-banking-uk": "Open Banking UK"}
"""Participant-facing labels for supported v2 plan schemes."""

_DCR_SPECIFICATION = "dynamic-client-registration"
"""User-facing v2 specification id for Dynamic Client Registration."""

_SPECIFICATION_LABELS = {"read-write": "Read/Write", _DCR_SPECIFICATION: "Dynamic Client Registration (DCR)"}
"""Participant-facing labels for supported v2 plan specifications."""

_API_LABELS = {
    "ais": "AIS",
    "pis": "PIS",
    "cbpii": "CBPII",
    "vrp": "VRP",
    "cvrp": "cVRP",
}
"""Participant-facing labels for Read/Write catalogue API-family groups."""

_CANONICAL_RESOURCE_GROUP_ID_BY_API = {
    "ais": "AIS",
    "pis": "PIS",
    "cbpii": "CBPII",
    "vrp": "VRP",
}
"""Canonical JSON-first resource-group ids keyed by internal catalogue API."""

_API_PATH_SEGMENTS = {"ais": "aisp", "pis": "pisp", "cbpii": "cbpii", "vrp": "vrp", "cvrp": "cvrp"}
"""Path segments used to infer resource-group labels from bundled catalogues."""

_CAPABILITY_VALUE_SEPARATOR = "::"
"""Separator used in endpoint-scoped capability checkbox values."""

_RUNTIME_INPUT_PREFIX = "runtime_input__"
"""Prefix used for grouped-config runtime input form fields."""

_MODEL_CONFIG_KEYS = {
    "discoveryUrl",
    "followUp",
    "tls",
    "fapiSigning",
    "resultOutputPath",
    "executionLogPath",
    "approvedReleasePolicyPath",
    "oauth",
    "resourceServer",
    "ais",
    "pis",
    "cbpii",
    "vrp",
    "conditionalProperties",
}
"""Top-level v2 config keys that also belong to the executable model-bank config."""

_STRUCTURED_CONFIG_RUNTIME_INPUT_IDS = {
    "resourceBaseUrl",
    "consentedAccountId",
    "fromBookingDateTime",
    "toBookingDateTime",
    "debtorAccountSchemeName",
    "debtorAccountIdentification",
    "debtorAccountName",
    "vrpCreditorAccountSchemeName",
    "vrpCreditorAccountIdentification",
    "vrpCreditorAccountName",
    "vrpInstructedAmountAmount",
    "vrpInstructedAmountCurrency",
    "vrpValidFromDateTime",
    "vrpValidToDateTime",
}
"""Plan-authored runtime inputs that have first-class grouped-config equivalents."""

_BUSINESS_CONFIG_KEYS = frozenset({"ais", "pis", "cbpii", "vrp", "conditionalProperties"})
"""Config keys owned by the business/request defaults step."""

_DISCOVERY_CONFIG_KEYS = frozenset({"discoveryUrl", "timeoutSeconds", "followUp"})
"""Config keys owned by the discovery step."""

_SECURITY_CONFIG_KEYS = frozenset(
    {
        "oauth",
        "resourceServer",
        "fapiSigning",
        "tls",
    }
)
"""Config keys owned by the OAuth/FAPI/security step."""

SecurityRequirementStatus = Literal["required", "conditional", "optional"]
"""User-facing field requirement status values for builder security fields."""

_RUNTIME_CONFIG_KEYS = frozenset({"inputs"})
"""Config keys owned by the runtime-artifact step."""

_SUPPORTED_SECURITY_PROFILE_OPTIONS: tuple[tuple[SecurityProfile, str], ...] = (
    ("fapi1-advanced", "FAPI 1 Advanced"),
    ("fapi2", "FAPI 2"),
)
"""Security-profile choices exposed by the specification step."""


@dataclass(frozen=True)
class _ResourceGroupMetadata:
    """Participant-facing high-level resource-group metadata.

    Attributes:
        group_id: Stable id used by the builder for resource-group selection.
        label: Human-readable resource area label.
    """

    group_id: str
    label: str


_RESOURCE_GROUP_METADATA_BY_API = {
    "ais": _ResourceGroupMetadata(
        group_id="account-and-transaction",
        label="Account and Transaction",
    ),
    "pis": _ResourceGroupMetadata(
        group_id="payment-initiation",
        label="Payment Initiation",
    ),
    "cbpii": _ResourceGroupMetadata(
        group_id="confirmation-of-funds",
        label="Confirmation of Funds",
    ),
    "vrp": _ResourceGroupMetadata(
        group_id="variable-recurring-payments",
        label="Variable Recurring Payments",
    ),
}
"""High-level resource groups shown by the Read/Write wizard."""

_SELECTOR_ONLY_BOUNDARIES = (
    PlanDocumentBoundary(scheme="open-banking-uk", specification=_DCR_SPECIFICATION, version="3.4"),
)
"""Plan boundaries shown in the wizard before catalogue compilation support exists."""


@dataclass(frozen=True)
class SchemeOption:
    """One scheme option rendered by the first wizard step.

    Attributes:
        value: Stable scheme identifier selected by the wizard.
        label: Participant-facing scheme label.
    """

    value: str
    label: str


@dataclass(frozen=True)
class SpecificationOption:
    """One specification option rendered by the first wizard step.

    Attributes:
        value: Stable specification identifier written into the v2 plan
            document.
        label: Participant-facing specification label.
        scheme: Scheme value that owns this specification.
    """

    value: str
    label: str
    scheme: str


@dataclass(frozen=True)
class VersionOption:
    """One specification-version option rendered by the first wizard step.

    Attributes:
        value: Stable specification version selected by the wizard.
        label: Participant-facing version label.
        scheme: Scheme value that owns this version.
        specification: Specification value that owns this version.
    """

    value: str
    label: str
    scheme: str
    specification: str


@dataclass(frozen=True)
class FeatureOption:
    """One endpoint-scoped optional or required implementation feature.

    Attributes:
        value: Stable form value pairing endpoint id and capability id.
        endpoint_id: Endpoint option id that owns the feature.
        capability_id: Catalogue-owned capability id.
        label: Participant-facing capability label.
        description: Explanation of the implementation feature.
        required: Whether this is baseline endpoint coverage implied by
            endpoint selection.
        kind: Participant-facing feature kind.
        selected: Whether the feature is explicitly selected or implied.
    """

    value: str
    endpoint_id: str
    capability_id: str
    label: str
    description: str
    required: bool
    kind: str
    selected: bool


@dataclass(frozen=True)
class EndpointOption:
    """One endpoint option shown under a selected resource group.

    Attributes:
        id: Stable form value for this exact endpoint.
        method: HTTP method for the endpoint.
        path: Standards endpoint path.
        display_path: Participant-facing endpoint path for guided selection.
        operation_id: Stable generated operation id for later plan export.
        api: Internal catalogue API-family id.
        api_label: Participant-facing API-family label.
        resource_group_id: Stable user-facing resource-group id.
        resource_group_label: Participant-facing resource-group label.
        baseline: Whether the endpoint has baseline/mandatory catalogue
            coverage.
        selected: Whether the participant selected this endpoint.
        features: Endpoint-scoped required and optional features.
    """

    id: str
    method: HttpMethod
    path: str
    display_path: str
    operation_id: str
    api: str
    api_label: str
    resource_group_id: str
    resource_group_label: str
    baseline: bool
    selected: bool
    features: tuple[FeatureOption, ...]


@dataclass(frozen=True)
class ResourceGroupOption:
    """One resource-group option in the wizard scope tree.

    Attributes:
        id: Stable user-facing resource-group id.
        label: Participant-facing resource-group label.
        api: Internal catalogue API-family id.
        api_label: Participant-facing API-family label.
        endpoint_count: Number of endpoints available under the group.
        selected: Whether the participant selected the group as scope.
        endpoints: Endpoint options, populated only when the group is selected.
    """

    id: str
    label: str
    api: str
    api_label: str
    endpoint_count: int
    selected: bool
    endpoints: tuple[EndpointOption, ...]


@dataclass(frozen=True)
class CatalogueScopeHierarchy:
    """Catalogue-derived resource/endpoints/features tree for wizard step two.

    Attributes:
        boundary: User-facing scheme/specification/version boundary.
        resource_groups: Resource groups available under the boundary.
    """

    boundary: PlanDocumentBoundary
    resource_groups: tuple[ResourceGroupOption, ...]


@dataclass(frozen=True)
class WizardRuntimeInputPrompt:
    """One grouped-config runtime input prompt derived from selected scope.

    Attributes:
        input_id: Stable catalogue runtime input id.
        name: Form field name used by the grouped config step.
        label: Participant-facing prompt label.
        input_type: Runtime input type expected by the catalogue compiler.
        required: Whether the compiled plan requires this value before launch.
        sensitive: Whether the value is secret-bearing and must be masked.
        value: Current draft value as a display string.
        group: Participant-facing config group for this prompt.
        description: Optional participant-facing guidance for this prompt.
    """

    input_id: str
    name: str
    label: str
    input_type: str
    required: bool
    sensitive: bool
    value: str
    group: str
    description: str | None = None


@dataclass(frozen=True)
class RuntimeInputGroup:
    """Runtime input prompts grouped for the configuration template.

    Attributes:
        label: Participant-facing group label.
        prompts: Runtime input prompts in compiler order.
    """

    label: str
    prompts: tuple[WizardRuntimeInputPrompt, ...]


@dataclass(frozen=True)
class ConfigVisibility:
    """Scope-derived visibility flags for grouped execution config.

    Attributes:
        selected_api_ids: Internal catalogue API families represented by the
            selected endpoints.
        show_ais: Whether AIS/account-and-transaction config fields apply.
        show_pis: Whether payment-initiation config fields apply.
        show_cbpii: Whether confirmation-of-funds config fields apply.
        show_vrp: Whether variable-recurring-payment runtime prompts apply.
        show_business_defaults: Whether any domain-specific business/request
            defaults section should be rendered.
        ais_account_id_required: Whether the selected AIS scope needs a
            participant-provided consented account identifier.
        pis_domestic_creditor_account_required: Whether selected PIS endpoints
            need domestic creditor account defaults.
        pis_international_creditor_account_required: Whether selected PIS
            endpoints need international creditor account defaults.
        pis_instructed_amount_required: Whether selected PIS endpoints need an
            instructed amount default.
        pis_currency_of_transfer_required: Whether selected PIS endpoints need
            a currency-of-transfer default.
        pis_requested_execution_date_time_required: Whether selected PIS
            endpoints need a requested execution date/time.
        pis_first_payment_date_time_required: Whether selected PIS endpoints
            need a first payment date/time.
        pis_standing_order_frequency_required: Whether selected PIS endpoints
            need a standing-order frequency object.
    """

    selected_api_ids: frozenset[str]
    show_ais: bool
    show_pis: bool
    show_cbpii: bool
    show_vrp: bool
    show_business_defaults: bool
    ais_account_id_required: bool = False
    pis_domestic_creditor_account_required: bool = False
    pis_international_creditor_account_required: bool = False
    pis_instructed_amount_required: bool = False
    pis_currency_of_transfer_required: bool = False
    pis_requested_execution_date_time_required: bool = False
    pis_first_payment_date_time_required: bool = False
    pis_standing_order_frequency_required: bool = False


@dataclass(frozen=True)
class SecurityFieldMetadata:
    """Participant-facing metadata for one security configuration field.

    Attributes:
        name: Django form field name.
        status: Run-derived requirement status.
        label: Human-readable status label.
        type_hint: Concise expected type or format.
        description: Short explanation of what the value affects.
        requirement: Short explanation of why the status applies.
    """

    name: str
    status: SecurityRequirementStatus
    label: str
    type_hint: str
    description: str
    requirement: str


_FULL_CONFIG_VISIBILITY = ConfigVisibility(
    selected_api_ids=frozenset({"ais", "pis", "cbpii", "vrp"}),
    show_ais=True,
    show_pis=True,
    show_cbpii=True,
    show_vrp=True,
    show_business_defaults=True,
    ais_account_id_required=True,
    pis_domestic_creditor_account_required=True,
    pis_international_creditor_account_required=True,
    pis_instructed_amount_required=True,
    pis_currency_of_transfer_required=True,
    pis_requested_execution_date_time_required=True,
    pis_first_payment_date_time_required=True,
    pis_standing_order_frequency_required=True,
)
"""Default grouped-config visibility used outside a scoped wizard draft."""

_SECURITY_FIELD_METADATA: tuple[SecurityFieldMetadata, ...] = (
    SecurityFieldMetadata(
        name="oauth_client_id",
        status="conditional",
        label="Conditional",
        type_hint="String",
        description="OAuth client identifier used by PSU authorisation and manifest-driven OAuth requests.",
        requirement="Required only when the selected run includes OAuth or PSU flows that reference client_id.",
    ),
    SecurityFieldMetadata(
        name="oauth_redirect_uri",
        status="conditional",
        label="Conditional",
        type_hint="HTTPS URL",
        description="Redirect URI registered for the OAuth client.",
        requirement="Required only when the selected run performs browser/PSU authorisation or resolves redirect_uri.",
    ),
    SecurityFieldMetadata(
        name="oauth_authorization_endpoint",
        status="conditional",
        label="Conditional",
        type_hint="HTTPS URL",
        description="Authorisation endpoint override for OAuth/PSU flows.",
        requirement="Required only when a selected flow directly uses an authorisation endpoint override.",
    ),
    SecurityFieldMetadata(
        name="oauth_issuer",
        status="conditional",
        label="Conditional",
        type_hint="HTTPS URL",
        description="OpenID Provider issuer value exposed to manifest placeholders.",
        requirement="Required only when a selected manifest step references the issuer placeholder.",
    ),
    SecurityFieldMetadata(
        name="oauth_token_endpoint",
        status="conditional",
        label="Conditional",
        type_hint="HTTPS URL",
        description="Token endpoint override for token-exchange flows.",
        requirement="Required only when a selected token step uses a config token endpoint rather than discovery.",
    ),
    SecurityFieldMetadata(
        name="oauth_response_type",
        status="conditional",
        label="Conditional",
        type_hint="String, for example code id_token",
        description="OAuth response_type value for authorisation requests.",
        requirement="Required only when a selected OAuth flow resolves response_type from config.",
    ),
    SecurityFieldMetadata(
        name="oauth_request_object_signing_alg",
        status="conditional",
        label="Conditional",
        type_hint="JOSE alg, for example PS256",
        description="Request-object signing algorithm exposed to manifest placeholders.",
        requirement="Required only when a selected flow resolves the request object signing algorithm from config.",
    ),
    SecurityFieldMetadata(
        name="resource_server_base_url",
        status="conditional",
        label="Conditional",
        type_hint="HTTPS URL without the Open Banking API path",
        description="Protected-resource base URL used to build selected AIS, PIS, CBPII, and VRP API requests.",
        requirement="Required when selected catalogue cases require the resourceBaseUrl runtime input.",
    ),
    SecurityFieldMetadata(
        name="signing_token_endpoint_auth_method",
        status="conditional",
        label="Conditional",
        type_hint="Enum: private_key_jwt or tls_client_auth",
        description="FAPI token endpoint client-authentication mode.",
        requirement="Required when FAPI signing/client-auth is configured for selected token or signing flows.",
    ),
    SecurityFieldMetadata(
        name="signing_kid",
        status="conditional",
        label="Conditional",
        type_hint="String",
        description="JOSE key identifier placed in signed request objects and private-key JWTs.",
        requirement="Required with the rest of the FAPI signing group when selected flows need runtime signing.",
    ),
    SecurityFieldMetadata(
        name="signing_certificate_path",
        status="conditional",
        label="Conditional",
        type_hint="Absolute file path",
        description="X.509 certificate used by runtime FAPI signing.",
        requirement="Required with the rest of the FAPI signing group when selected flows need runtime signing.",
    ),
    SecurityFieldMetadata(
        name="signing_private_key_path",
        status="conditional",
        label="Conditional",
        type_hint="Absolute file path",
        description="Private key paired with the signing certificate.",
        requirement="Required with the rest of the FAPI signing group when selected flows need runtime signing.",
    ),
    SecurityFieldMetadata(
        name="signing_client_assertion_issuer",
        status="conditional",
        label="Conditional",
        type_hint="String",
        description="iss claim used when the runner signs private-key JWT client assertions.",
        requirement="Required with the rest of the FAPI signing group when selected flows need private_key_jwt.",
    ),
    SecurityFieldMetadata(
        name="signing_client_assertion_subject",
        status="conditional",
        label="Conditional",
        type_hint="String",
        description="sub claim used when the runner signs private-key JWT client assertions.",
        requirement="Required with the rest of the FAPI signing group when selected flows need private_key_jwt.",
    ),
    SecurityFieldMetadata(
        name="tls_ca_bundle_path",
        status="optional",
        label="Optional",
        type_hint="Absolute file path",
        description="Custom CA bundle used to verify the ASPSP TLS certificate.",
        requirement="Only needed for environments that use a private or non-standard issuing CA.",
    ),
    SecurityFieldMetadata(
        name="tls_client_certificate_path",
        status="conditional",
        label="Conditional",
        type_hint="Absolute file path",
        description="Client certificate used by the HTTP client for mTLS.",
        requirement=(
            "Required with the private key when the selected environment or tls_client_auth flow requires mTLS."
        ),
    ),
    SecurityFieldMetadata(
        name="tls_client_private_key_path",
        status="conditional",
        label="Conditional",
        type_hint="Absolute file path",
        description="Private key paired with the mTLS client certificate.",
        requirement=(
            "Required with the client certificate when the selected environment or tls_client_auth flow requires mTLS."
        ),
    ),
)
"""Security form metadata independent of discovery-prefilled values."""

_SECURITY_FIELD_METADATA_BY_NAME = {metadata.name: metadata for metadata in _SECURITY_FIELD_METADATA}
"""Security form metadata keyed by Django form field name."""


@dataclass
class _ResourceGroupAccumulator:
    """Mutable builder record used while collecting resource groups.

    Attributes:
        group_id: Stable user-facing resource-group id.
        label: Participant-facing resource-group label.
        api: Internal catalogue API-family id.
        api_label: Participant-facing API-family label.
        endpoints: Endpoint options accumulated for this group.
    """

    group_id: str
    label: str
    api: str
    api_label: str
    endpoints: list[EndpointOption]


class CatalogueBoundaryForm(forms.Form):
    """Form for selecting the wizard's catalogue boundary.

    Attributes:
        scheme: Standards scheme selector.
        specification: Specification family selector filtered by scheme.
        version: Specification-version selector filtered by scheme and
            specification.
        security_profile: Security profile selector written into the canonical
            test plan's specification profile.
        resource_groups: Deprecated compatibility field ignored by the
            specification/profile step.
    """

    scheme: forms.ChoiceField = forms.ChoiceField(label="Scheme")
    specification: forms.ChoiceField = forms.ChoiceField(label="Specification")
    version: forms.ChoiceField = forms.ChoiceField(label="Version")
    security_profile: forms.ChoiceField = forms.ChoiceField(label="Security profile", required=False)
    resource_groups: forms.MultipleChoiceField = forms.MultipleChoiceField(required=False)

    def __init__(
        self,
        data: Mapping[str, object] | None = None,
        *,
        initial: Mapping[str, object] | None = None,
        boundaries: Iterable[PlanDocumentBoundary] | None = None,
        validate_resource_groups: bool = True,
    ) -> None:
        """Initialise the form with compile-ready catalogue boundaries.

        Args:
            data: Optional bound form data.
            initial: Optional initial field values for redisplaying a draft.
            boundaries: Optional boundary override used by tests.
            validate_resource_groups: Whether resource-group requirements should
                be enforced. Dynamic boundary refreshes disable this because
                they render choice lists rather than saving the step.
        """
        self._boundaries = tuple(boundaries) if boundaries is not None else catalogue_boundary_options()
        self._validate_resource_groups = validate_resource_groups
        effective_initial = _effective_initial(initial, boundaries=self._boundaries)
        selected_boundary = _boundary_from_form_values(data, effective_initial, boundaries=self._boundaries)
        effective_initial.setdefault("security_profile", "fapi1-advanced")
        self.selected_boundary = selected_boundary
        selected_resource_groups = _raw_or_initial_values(data, effective_initial, "resource_groups")
        self.resource_group_hierarchy = catalogue_scope_hierarchy(
            selected_boundary,
            selected_resource_group_ids=selected_resource_groups,
        )
        effective_data = (
            _pruned_catalogue_boundary_form_data(data, hierarchy=self.resource_group_hierarchy)
            if data is not None
            else data
        )
        super().__init__(
            data=cast(MutableMapping[str, object] | None, effective_data),
            initial=cast(MutableMapping[str, object] | None, effective_initial),
        )
        cast(forms.ChoiceField, self.fields["scheme"]).choices = [
            (option.value, option.label) for option in scheme_options(boundaries=self._boundaries)
        ]
        cast(forms.ChoiceField, self.fields["specification"]).choices = [
            (option.value, option.label) for option in specification_options(boundaries=self._boundaries)
        ]
        cast(forms.ChoiceField, self.fields["version"]).choices = [
            (option.value, option.label) for option in version_options(boundaries=self._boundaries)
        ]
        cast(forms.ChoiceField, self.fields["security_profile"]).choices = security_profile_options()
        cast(forms.MultipleChoiceField, self.fields["resource_groups"]).choices = [
            (group.id, group.label) for group in self.resource_group_hierarchy.resource_groups
        ]

    def clean(self) -> dict[str, object]:
        """Validate that the selected boundary and resource groups are usable.

        Returns:
            Cleaned form data.

        Raises:
            ValidationError: If the scheme/specification/version combination
                is not backed by a supported catalogue boundary.
        """
        base_cleaned_data = super().clean()
        cleaned_data: dict[str, object] = {} if base_cleaned_data is None else dict(base_cleaned_data)
        scheme = cleaned_data.get("scheme")
        specification = cleaned_data.get("specification")
        version = cleaned_data.get("version")
        if not (isinstance(scheme, str) and isinstance(specification, str) and isinstance(version, str)):
            return cleaned_data
        selected = PlanDocumentBoundary(scheme=scheme, specification=specification, version=version)
        if selected not in self._boundaries:
            raise forms.ValidationError(
                "Choose a supported scheme, specification, and version from the available catalogue options.",
                code="unsupported_boundary",
            )
        security_profile = cleaned_data.get("security_profile") or "fapi1-advanced"
        supported_values = {value for value, _label in security_profile_options()}
        if security_profile not in supported_values:
            supported = ", ".join(label for _value, label in security_profile_options())
            self.add_error("security_profile", f"Choose a supported security profile: {supported}.")
        cleaned_data["security_profile"] = security_profile
        return cleaned_data

    @property
    def selected_resource_group_ids(self) -> tuple[str, ...]:
        """Return cleaned selected resource-group ids.

        Returns:
            Selected high-level resource-group ids in submitted order.
        """
        return _cleaned_string_tuple(self.cleaned_data.get("resource_groups"))


class ScopeSelectionForm(forms.Form):
    """Form for selecting resource groups, endpoints, and endpoint features.

    Attributes:
        resource_groups: Selected resource-group ids.
        endpoints: Selected endpoint option ids.
        endpoint_capabilities: Optional endpoint feature values selected by the
            participant.
    """

    resource_groups: forms.MultipleChoiceField = forms.MultipleChoiceField(required=False)
    endpoints: forms.MultipleChoiceField = forms.MultipleChoiceField(required=False)
    endpoint_capabilities: forms.MultipleChoiceField = forms.MultipleChoiceField(required=False)

    def __init__(
        self,
        data: Mapping[str, object] | None = None,
        *,
        boundary: PlanDocumentBoundary,
        initial: Mapping[str, object] | None = None,
        catalogues: Iterable[TestCatalogue] | None = None,
        prune_unavailable_choices: bool = False,
    ) -> None:
        """Initialise the form from catalogue-derived scope options.

        Args:
            data: Optional bound form data.
            boundary: Selected scheme/specification/version boundary.
            initial: Optional initial values from a saved draft.
            catalogues: Optional catalogue override used by tests.
            prune_unavailable_choices: Whether to discard stale child checkbox
                values before field validation. This is used only for dynamic
                preview refreshes where a just-deselected parent can still post
                previously rendered child inputs.
        """
        selected_resource_groups = _raw_or_initial_values(data, initial, "resource_groups")
        selected_endpoints = _raw_or_initial_values(data, initial, "endpoints")
        selected_capability_values = _raw_or_initial_values(data, initial, "endpoint_capabilities")
        self.hierarchy = catalogue_scope_hierarchy(
            boundary,
            selected_resource_group_ids=selected_resource_groups,
            selected_endpoint_ids=selected_endpoints,
            selected_capability_values=selected_capability_values,
            catalogues=catalogues,
        )
        effective_initial = {
            "resource_groups": list(selected_resource_groups),
            "endpoints": list(selected_endpoints),
            "endpoint_capabilities": list(selected_capability_values),
        }
        effective_data = (
            _pruned_scope_form_data(data, hierarchy=self.hierarchy)
            if data is not None and prune_unavailable_choices
            else data
        )
        super().__init__(
            data=cast(MutableMapping[str, object] | None, effective_data),
            initial=cast(MutableMapping[str, object], effective_initial),
        )
        cast(forms.MultipleChoiceField, self.fields["resource_groups"]).choices = [
            (group.id, group.label) for group in self.hierarchy.resource_groups
        ]
        cast(forms.MultipleChoiceField, self.fields["endpoints"]).choices = [
            (endpoint.id, f"{endpoint.method} {endpoint.path}") for endpoint in _endpoint_options(self.hierarchy)
        ]
        cast(forms.MultipleChoiceField, self.fields["endpoint_capabilities"]).choices = [
            (feature.value, feature.label) for feature in _feature_options(self.hierarchy)
        ]

    @property
    def selected_resource_group_ids(self) -> tuple[str, ...]:
        """Return cleaned selected resource-group ids.

        Returns:
            Selected resource-group ids in submitted order.
        """
        return _cleaned_string_tuple(self.cleaned_data.get("resource_groups"))

    @property
    def selected_endpoint_ids(self) -> tuple[str, ...]:
        """Return cleaned selected endpoint ids.

        Returns:
            Selected endpoint option ids in submitted order.
        """
        return _cleaned_string_tuple(self.cleaned_data.get("endpoints"))

    @property
    def selected_endpoint_capability_ids(self) -> dict[str, tuple[str, ...]]:
        """Return cleaned optional capability ids keyed by endpoint id.

        Returns:
            Mapping of selected endpoint id to selected optional capability ids.
        """
        values = _cleaned_string_tuple(self.cleaned_data.get("endpoint_capabilities"))
        selected_by_endpoint = _selected_capability_values_by_endpoint(values, strict=True)
        options_by_endpoint = {endpoint.id: endpoint for endpoint in _endpoint_options(self.hierarchy)}
        selected: dict[str, tuple[str, ...]] = {}
        for endpoint_id, capability_ids in selected_by_endpoint.items():
            option = options_by_endpoint.get(endpoint_id)
            if option is None:
                continue
            optional_ids = {
                feature.capability_id
                for feature in option.features
                if not feature.required and feature.capability_id in capability_ids
            }
            if optional_ids:
                selected[endpoint_id] = tuple(
                    capability_id for capability_id in capability_ids if capability_id in optional_ids
                )
        return selected

    def clean(self) -> dict[str, object]:
        """Validate selected endpoints and features against the selected scope.

        Returns:
            Cleaned form data.

        Raises:
            ValidationError: If an endpoint or feature is selected outside its
                currently selected parent context.
        """
        base_cleaned_data = super().clean()
        cleaned_data: dict[str, object] = {} if base_cleaned_data is None else dict(base_cleaned_data)
        selected_group_ids = set(_cleaned_string_tuple(cleaned_data.get("resource_groups")))
        selected_endpoint_ids = set(_cleaned_string_tuple(cleaned_data.get("endpoints")))
        endpoint_options = {endpoint.id: endpoint for endpoint in _endpoint_options(self.hierarchy)}
        for endpoint_id in selected_endpoint_ids:
            endpoint = endpoint_options.get(endpoint_id)
            if endpoint is not None and endpoint.resource_group_id not in selected_group_ids:
                raise forms.ValidationError(
                    f"Endpoint selected outside selected resource group: {endpoint.method} {endpoint.path}",
                    code="endpoint_outside_resource_group",
                )

        selected_capability_values = _cleaned_string_tuple(cleaned_data.get("endpoint_capabilities"))
        selected_capabilities = _selected_capability_values_by_endpoint(selected_capability_values, strict=True)
        for endpoint_id, capability_ids in selected_capabilities.items():
            endpoint = endpoint_options.get(endpoint_id)
            if endpoint is None:
                raise forms.ValidationError(
                    f"Capability selected for unknown endpoint: {endpoint_id}",
                    code="invalid_capability",
                )
            if endpoint_id not in selected_endpoint_ids:
                raise forms.ValidationError(
                    f"Capability selected for unselected endpoint: {endpoint.method} {endpoint.path}",
                    code="capability_without_endpoint",
                )
            available_ids = {feature.capability_id for feature in endpoint.features}
            unknown_ids = sorted(capability_ids - available_ids)
            if unknown_ids:
                raise forms.ValidationError(
                    f"Unknown capability id(s) for {endpoint.method} {endpoint.path}: {', '.join(unknown_ids)}",
                    code="invalid_capability",
                )
        return cleaned_data


class ExecutionConfigForm(forms.Form):
    """Form for grouped execution config and runtime input values.

    Attributes:
        discovery_url: OpenID discovery document URL.
        oauth_client_id: Optional OAuth client id.
        oauth_redirect_uri: Optional OAuth redirect URI.
        oauth_authorization_endpoint: Optional authorization endpoint override.
        oauth_issuer: Optional OpenID issuer URL.
        oauth_token_endpoint: Optional token endpoint URL.
        oauth_resource_base_url: Optional OAuth resource server base URL.
        oauth_response_type: Optional OAuth response type.
        oauth_request_object_signing_alg: Optional request-object signing alg.
        resource_server_base_url: Optional protected-resource base URL.
        signing_certificate_path: Optional absolute signing certificate path.
        signing_private_key_path: Optional absolute signing private-key path.
        signing_kid: Optional JOSE key id.
        signing_client_assertion_issuer: Optional private-key JWT issuer.
        signing_client_assertion_subject: Optional private-key JWT subject.
        signing_token_endpoint_auth_method: Optional token endpoint auth method.
        tls_ca_bundle_path: Optional absolute CA bundle path.
        tls_client_certificate_path: Optional absolute mTLS client certificate path.
        tls_client_private_key_path: Optional absolute mTLS private-key path.
        ais_resource_ids_json: Optional AIS resource ids JSON object.
        ais_transaction_from_date: Optional transaction lower date bound.
        ais_transaction_to_date: Optional transaction upper date bound.
        pis_creditor_account_json: Optional domestic creditor account object.
        pis_international_creditor_account_json: Optional international
            creditor account object.
        pis_instructed_amount_json: Optional instructed amount object.
        pis_currency_of_transfer: Optional currency of transfer.
        pis_requested_execution_date_time: Optional requested execution time.
        pis_first_payment_date_time: Optional first payment time.
        pis_standing_order_frequency_json: Optional standing-order frequency
            object.
        cbpii_debtor_account_json: Optional CBPII debtor account object.
        conditional_properties_json: Optional conditional properties array.
        config_json: Optional advanced v2 config JSON override.
        config: Parsed v2 config object after successful validation.
    """

    discovery_url: forms.CharField = forms.CharField(label="Discovery URL", required=False)
    oauth_client_id: forms.CharField = forms.CharField(label="Client ID", required=False)
    oauth_redirect_uri: forms.CharField = forms.CharField(label="Redirect URI", required=False)
    oauth_authorization_endpoint: forms.CharField = forms.CharField(
        label="Authorization endpoint override",
        required=False,
    )
    oauth_issuer: forms.CharField = forms.CharField(label="Issuer", required=False)
    oauth_token_endpoint: forms.CharField = forms.CharField(label="Token endpoint", required=False)
    oauth_resource_base_url: forms.CharField = forms.CharField(label="OAuth resource base URL", required=False)
    oauth_response_type: forms.CharField = forms.CharField(label="Response type", required=False)
    oauth_request_object_signing_alg: forms.CharField = forms.CharField(
        label="Request object signing algorithm",
        required=False,
    )
    resource_server_base_url: forms.CharField = forms.CharField(label="Resource server base URL", required=False)
    signing_certificate_path: forms.CharField = forms.CharField(
        label="Signing certificate absolute path",
        required=False,
    )
    signing_private_key_path: forms.CharField = forms.CharField(
        label="Signing private key absolute path",
        required=False,
    )
    signing_kid: forms.CharField = forms.CharField(label="Signing key ID", required=False)
    signing_client_assertion_issuer: forms.CharField = forms.CharField(
        label="Client assertion issuer",
        required=False,
    )
    signing_client_assertion_subject: forms.CharField = forms.CharField(
        label="Client assertion subject",
        required=False,
    )
    signing_token_endpoint_auth_method: forms.ChoiceField = forms.ChoiceField(
        label="Token endpoint auth method",
        required=False,
    )
    tls_ca_bundle_path: forms.CharField = forms.CharField(label="CA bundle absolute path", required=False)
    tls_client_certificate_path: forms.CharField = forms.CharField(
        label="mTLS client certificate absolute path",
        required=False,
    )
    tls_client_private_key_path: forms.CharField = forms.CharField(
        label="mTLS client private key absolute path",
        required=False,
    )
    ais_resource_ids_json: forms.CharField = forms.CharField(
        label="AIS resource IDs JSON",
        required=False,
        widget=forms.Textarea,
    )
    ais_transaction_from_date: forms.CharField = forms.CharField(label="Transaction from date", required=False)
    ais_transaction_to_date: forms.CharField = forms.CharField(label="Transaction to date", required=False)
    pis_creditor_account_json: forms.CharField = forms.CharField(
        label="Creditor account JSON",
        required=False,
        widget=forms.Textarea,
    )
    pis_international_creditor_account_json: forms.CharField = forms.CharField(
        label="International creditor account JSON",
        required=False,
        widget=forms.Textarea,
    )
    pis_instructed_amount_json: forms.CharField = forms.CharField(
        label="Instructed amount JSON",
        required=False,
        widget=forms.Textarea,
    )
    pis_currency_of_transfer: forms.CharField = forms.CharField(label="Currency of transfer", required=False)
    pis_requested_execution_date_time: forms.CharField = forms.CharField(
        label="Requested execution date/time",
        required=False,
    )
    pis_first_payment_date_time: forms.CharField = forms.CharField(label="First payment date/time", required=False)
    pis_standing_order_frequency_json: forms.CharField = forms.CharField(
        label="Standing-order frequency JSON",
        required=False,
        widget=forms.Textarea,
    )
    cbpii_debtor_account_json: forms.CharField = forms.CharField(
        label="CBPII debtor account JSON",
        required=False,
        widget=forms.Textarea,
    )
    conditional_properties_json: forms.CharField = forms.CharField(
        label="Conditional properties JSON",
        required=False,
        widget=forms.Textarea,
    )
    config_json: forms.CharField = forms.CharField(label="Advanced config JSON", required=False, widget=forms.Textarea)

    config: JsonObject | None = None

    def __init__(
        self,
        data: Mapping[str, object] | None = None,
        *,
        initial: Mapping[str, object] | None = None,
        runtime_prompts: Iterable[WizardRuntimeInputPrompt] = (),
        config_visibility: ConfigVisibility | None = None,
    ) -> None:
        """Initialise the grouped config form.

        Args:
            data: Optional bound form data.
            initial: Initial values decoded from the draft config.
            runtime_prompts: Runtime prompts derived from selected endpoints.
            config_visibility: Optional scope-derived structured-field
                visibility. Defaults to all grouped fields for standalone unit
                tests and non-wizard callers.
        """
        self.runtime_prompts = tuple(runtime_prompts)
        self.config_visibility = config_visibility if config_visibility is not None else _FULL_CONFIG_VISIBILITY
        super().__init__(
            data=cast(MutableMapping[str, object] | None, data),
            initial=cast(MutableMapping[str, object] | None, initial),
        )
        cast(forms.ChoiceField, self.fields["signing_token_endpoint_auth_method"]).choices = [
            ("", "Select auth method"),
            ("private_key_jwt", "private_key_jwt"),
            ("tls_client_auth", "tls_client_auth"),
        ]
        for prompt in self.runtime_prompts:
            self.fields[prompt.name] = forms.CharField(
                label=prompt.label,
                required=False,
                initial=prompt.value,
            )

    @property
    def runtime_prompt_groups(self) -> tuple[RuntimeInputGroup, ...]:
        """Return runtime prompts grouped for rendering.

        Returns:
            Runtime prompt groups in a stable participant-facing order.
        """
        groups: dict[str, list[WizardRuntimeInputPrompt]] = {}
        for prompt in self.runtime_prompts:
            groups.setdefault(prompt.group, []).append(prompt)
        return tuple(RuntimeInputGroup(label=label, prompts=tuple(prompts)) for label, prompts in groups.items())

    def clean(self) -> dict[str, object]:
        """Build and validate the draft v2 config object.

        Returns:
            Cleaned form data.
        """
        base_cleaned_data = super().clean()
        cleaned_data: dict[str, object] = {} if base_cleaned_data is None else dict(base_cleaned_data)
        if self.errors:
            return cleaned_data

        raw_config_json = _cleaned_optional_string(cleaned_data.get("config_json"))
        if raw_config_json is not None:
            config = _load_json_object(raw_config_json, label="Advanced config JSON")
        else:
            config = _config_from_grouped_fields(cleaned_data, self.runtime_prompts, self.config_visibility)

        self._validate_model_config(config)
        self.config = config
        return cleaned_data

    def _validate_model_config(self, config: Mapping[str, JsonValue]) -> None:
        """Validate the executable model-bank portion of a v2 config object.

        Args:
            config: Draft executable config object.
        """
        try:
            parse_model_bank_config(model_bank_config_from_plan_config(config), base_dir=Path.cwd())
        except ConfigError as error:
            self.add_error("config_json", f"Config validation failed: {error}")


class BusinessConfigForm(forms.Form):
    """Form for endpoint-scoped business and request defaults.

    Attributes:
        ais_consented_account_id: Optional AIS account id used by account-detail
            and transaction requests.
        ais_transaction_from_date: Optional AIS lower transaction date bound.
        ais_transaction_to_date: Optional AIS upper transaction date bound.
        ais_resource_ids_json: Advanced AIS resource-id object fallback.
        pis_creditor_account_scheme_name: Domestic creditor account scheme.
        pis_creditor_account_identification: Domestic creditor account
            identifier.
        pis_creditor_account_name: Domestic creditor account name.
        pis_creditor_account_json: Advanced domestic creditor account fallback.
        pis_international_creditor_account_scheme_name: International creditor
            account scheme.
        pis_international_creditor_account_identification: International
            creditor account identifier.
        pis_international_creditor_account_name: International creditor account
            name.
        pis_international_creditor_account_json: Advanced international
            creditor account fallback.
        pis_instructed_amount_amount: Payment amount.
        pis_instructed_amount_currency: Payment currency.
        pis_instructed_amount_json: Advanced instructed amount fallback.
        pis_currency_of_transfer: International payment transfer currency.
        pis_requested_execution_date_time: Requested execution date/time.
        pis_first_payment_date_time: First recurring-payment date/time.
        pis_standing_order_frequency_type: Standing-order frequency type.
        pis_standing_order_frequency_point_in_time: Standing-order frequency
            point in time.
        pis_standing_order_frequency_json: Advanced standing-order frequency
            fallback.
        cbpii_debtor_account_scheme_name: CBPII debtor account scheme.
        cbpii_debtor_account_identification: CBPII debtor account identifier.
        cbpii_debtor_account_name: CBPII debtor account name.
        cbpii_debtor_account_json: Advanced CBPII debtor account fallback.
        vrp_creditor_account_scheme_name: VRP creditor account scheme.
        vrp_creditor_account_identification: VRP creditor account identifier.
        vrp_creditor_account_name: VRP creditor account name.
        vrp_instructed_amount_amount: VRP instructed amount.
        vrp_instructed_amount_currency: VRP instructed amount currency.
        vrp_valid_from_date_time: VRP consent valid-from date/time.
        vrp_valid_to_date_time: VRP consent valid-to date/time.
        conditional_properties_json: Optional conditional properties array.
        config: Parsed partial v2 config owned by this step.
    """

    ais_consented_account_id: forms.CharField = forms.CharField(label="Consented account identifier", required=False)
    ais_transaction_from_date: forms.CharField = forms.CharField(label="Transaction from date", required=False)
    ais_transaction_to_date: forms.CharField = forms.CharField(label="Transaction to date", required=False)
    ais_resource_ids_json: forms.CharField = forms.CharField(
        label="AIS resource IDs JSON",
        required=False,
        widget=forms.Textarea,
    )
    pis_creditor_account_scheme_name: forms.CharField = forms.CharField(
        label="Domestic creditor account scheme",
        required=False,
    )
    pis_creditor_account_identification: forms.CharField = forms.CharField(
        label="Domestic creditor account identification",
        required=False,
    )
    pis_creditor_account_name: forms.CharField = forms.CharField(label="Domestic creditor account name", required=False)
    pis_creditor_account_json: forms.CharField = forms.CharField(
        label="Domestic creditor account JSON",
        required=False,
        widget=forms.Textarea,
    )
    pis_international_creditor_account_scheme_name: forms.CharField = forms.CharField(
        label="International creditor account scheme",
        required=False,
    )
    pis_international_creditor_account_identification: forms.CharField = forms.CharField(
        label="International creditor account identification",
        required=False,
    )
    pis_international_creditor_account_name: forms.CharField = forms.CharField(
        label="International creditor account name",
        required=False,
    )
    pis_international_creditor_account_json: forms.CharField = forms.CharField(
        label="International creditor account JSON",
        required=False,
        widget=forms.Textarea,
    )
    pis_instructed_amount_amount: forms.CharField = forms.CharField(label="Instructed amount", required=False)
    pis_instructed_amount_currency: forms.CharField = forms.CharField(
        label="Instructed amount currency",
        required=False,
    )
    pis_instructed_amount_json: forms.CharField = forms.CharField(
        label="Instructed amount JSON",
        required=False,
        widget=forms.Textarea,
    )
    pis_currency_of_transfer: forms.CharField = forms.CharField(label="Currency of transfer", required=False)
    pis_requested_execution_date_time: forms.CharField = forms.CharField(
        label="Requested execution date/time",
        required=False,
    )
    pis_first_payment_date_time: forms.CharField = forms.CharField(label="First payment date/time", required=False)
    pis_standing_order_frequency_type: forms.CharField = forms.CharField(
        label="Standing-order frequency type",
        required=False,
    )
    pis_standing_order_frequency_point_in_time: forms.CharField = forms.CharField(
        label="Standing-order frequency point in time",
        required=False,
    )
    pis_standing_order_frequency_json: forms.CharField = forms.CharField(
        label="Standing-order frequency JSON",
        required=False,
        widget=forms.Textarea,
    )
    cbpii_debtor_account_scheme_name: forms.CharField = forms.CharField(label="Debtor account scheme", required=False)
    cbpii_debtor_account_identification: forms.CharField = forms.CharField(
        label="Debtor account identification",
        required=False,
    )
    cbpii_debtor_account_name: forms.CharField = forms.CharField(label="Debtor account name", required=False)
    cbpii_debtor_account_json: forms.CharField = forms.CharField(
        label="CBPII debtor account JSON",
        required=False,
        widget=forms.Textarea,
    )
    vrp_creditor_account_scheme_name: forms.CharField = forms.CharField(
        label="VRP creditor account scheme",
        required=False,
    )
    vrp_creditor_account_identification: forms.CharField = forms.CharField(
        label="VRP creditor account identification",
        required=False,
    )
    vrp_creditor_account_name: forms.CharField = forms.CharField(label="VRP creditor account name", required=False)
    vrp_instructed_amount_amount: forms.CharField = forms.CharField(label="VRP instructed amount", required=False)
    vrp_instructed_amount_currency: forms.CharField = forms.CharField(
        label="VRP instructed amount currency",
        required=False,
    )
    vrp_valid_from_date_time: forms.CharField = forms.CharField(label="VRP valid-from date/time", required=False)
    vrp_valid_to_date_time: forms.CharField = forms.CharField(label="VRP valid-to date/time", required=False)
    conditional_properties_json: forms.CharField = forms.CharField(
        label="Conditional properties JSON",
        required=False,
        widget=forms.Textarea,
    )

    config: JsonObject | None = None

    def __init__(
        self,
        data: Mapping[str, object] | None = None,
        *,
        initial: Mapping[str, object] | None = None,
        config_visibility: ConfigVisibility | None = None,
    ) -> None:
        """Initialise the business defaults form.

        Args:
            data: Optional bound form data.
            initial: Initial values decoded from the draft config.
            config_visibility: Optional scope-derived field visibility.
        """
        self.config_visibility = config_visibility if config_visibility is not None else _FULL_CONFIG_VISIBILITY
        super().__init__(
            data=cast(MutableMapping[str, object] | None, data),
            initial=cast(MutableMapping[str, object] | None, initial),
        )
        if self.config_visibility.show_cbpii:
            self.fields["cbpii_debtor_account_scheme_name"].required = True
            self.fields["cbpii_debtor_account_identification"].required = True
            self.fields["cbpii_debtor_account_name"].required = True
        if self.config_visibility.show_vrp:
            self.fields["vrp_creditor_account_scheme_name"].required = True
            self.fields["vrp_creditor_account_identification"].required = True
            self.fields["vrp_creditor_account_name"].required = True
            self.fields["vrp_instructed_amount_amount"].required = True
            self.fields["vrp_instructed_amount_currency"].required = True
            self.fields["vrp_valid_from_date_time"].required = True
            self.fields["vrp_valid_to_date_time"].required = True

    def clean(self) -> dict[str, object]:
        """Build and validate the business-default partial config.

        Returns:
            Cleaned form data.
        """
        base_cleaned_data = super().clean()
        cleaned_data: dict[str, object] = {} if base_cleaned_data is None else dict(base_cleaned_data)
        if self.errors:
            return cleaned_data
        self.config = _business_config_from_fields(cleaned_data, self.config_visibility)
        if self.config_visibility.show_ais and self.config_visibility.ais_account_id_required:
            ais_config = self.config.get("ais")
            if not _ais_config_has_account_id(ais_config):
                self.add_error(
                    "ais_consented_account_id",
                    "Consented account identifier is required for selected account-scoped AIS endpoints.",
                )
        if self.config_visibility.show_pis:
            _add_required_pis_errors(self, cleaned_data)
        return cleaned_data


class DiscoveryConfigForm(forms.Form):
    """Form for OpenID Provider discovery settings.

    Attributes:
        discovery_url: OpenID discovery document URL.
        config: Parsed partial v2 config owned by this step.
    """

    discovery_url: forms.CharField = forms.CharField(label="Discovery URL", required=False)

    config: JsonObject | None = None

    def __init__(
        self,
        data: Mapping[str, object] | None = None,
        *,
        initial: Mapping[str, object] | None = None,
    ) -> None:
        """Initialise the discovery config form.

        Args:
            data: Optional bound form data.
            initial: Initial values decoded from the draft config.
        """
        super().__init__(
            data=cast(MutableMapping[str, object] | None, data),
            initial=cast(MutableMapping[str, object] | None, initial),
        )

    def clean_discovery_url(self) -> str:
        """Validate the submitted OpenID discovery URL.

        Returns:
            HTTPS discovery URL.

        Raises:
            ValidationError: If the URL is not an HTTPS URL accepted by the
                conformance-suite URL validator.
        """
        value = cast(str, self.cleaned_data["discovery_url"]).strip()
        if not value:
            return ""
        try:
            validate_https_url(value, label="discoveryUrl")
        except HttpsUrlValidationError as error:
            raise forms.ValidationError(str(error), code="invalid_url") from error
        return value

    def clean(self) -> dict[str, object]:
        """Build and validate the discovery partial config.

        Returns:
            Cleaned form data.
        """
        base_cleaned_data = super().clean()
        cleaned_data: dict[str, object] = {} if base_cleaned_data is None else dict(base_cleaned_data)
        if self.errors:
            return cleaned_data
        self.config = _discovery_config_from_fields(cleaned_data)
        return cleaned_data


class SecurityConfigForm(forms.Form):
    """Form for OAuth, FAPI signing, TLS, and resource-server settings.

    Attributes:
        oauth_client_id: Optional OAuth client id.
        oauth_redirect_uri: Optional OAuth redirect URI.
        oauth_authorization_endpoint: Optional authorization endpoint override.
        oauth_issuer: Optional OpenID issuer URL.
        oauth_token_endpoint: Optional token endpoint URL.
        oauth_response_type: Optional OAuth response type.
        oauth_request_object_signing_alg: Optional request-object signing alg.
        resource_server_base_url: Optional protected-resource base URL.
        signing_certificate_path: Optional absolute signing certificate path.
        signing_private_key_path: Optional absolute signing private-key path.
        signing_kid: Optional JOSE key id.
        signing_client_assertion_issuer: Optional private-key JWT issuer.
        signing_client_assertion_subject: Optional private-key JWT subject.
        signing_token_endpoint_auth_method: Optional token endpoint auth method.
        tls_ca_bundle_path: Optional absolute CA bundle path.
        tls_client_certificate_path: Optional absolute mTLS client certificate path.
        tls_client_private_key_path: Optional absolute mTLS private-key path.
        config: Parsed partial v2 config owned by this step.
    """

    oauth_client_id: forms.CharField = forms.CharField(label="Client ID", required=False)
    oauth_redirect_uri: forms.CharField = forms.CharField(label="Redirect URI", required=False)
    oauth_authorization_endpoint: forms.CharField = forms.CharField(
        label="Authorization endpoint override",
        required=False,
    )
    oauth_issuer: forms.CharField = forms.CharField(label="Issuer", required=False)
    oauth_token_endpoint: forms.CharField = forms.CharField(label="Token endpoint", required=False)
    oauth_response_type: forms.CharField = forms.CharField(label="Response type", required=False)
    oauth_request_object_signing_alg: forms.CharField = forms.CharField(
        label="Request object signing algorithm",
        required=False,
    )
    resource_server_base_url: forms.CharField = forms.CharField(label="Resource server base URL", required=False)
    signing_certificate_path: forms.CharField = forms.CharField(
        label="Signing certificate absolute path",
        required=False,
    )
    signing_private_key_path: forms.CharField = forms.CharField(
        label="Signing private key absolute path",
        required=False,
    )
    signing_kid: forms.CharField = forms.CharField(label="Signing key ID", required=False)
    signing_client_assertion_issuer: forms.CharField = forms.CharField(
        label="Client assertion issuer",
        required=False,
    )
    signing_client_assertion_subject: forms.CharField = forms.CharField(
        label="Client assertion subject",
        required=False,
    )
    signing_token_endpoint_auth_method: forms.ChoiceField = forms.ChoiceField(
        label="Token endpoint auth method",
        required=False,
    )
    tls_ca_bundle_path: forms.CharField = forms.CharField(label="CA bundle absolute path", required=False)
    tls_client_certificate_path: forms.CharField = forms.CharField(
        label="mTLS client certificate absolute path",
        required=False,
    )
    tls_client_private_key_path: forms.CharField = forms.CharField(
        label="mTLS client private key absolute path",
        required=False,
    )

    config: JsonObject | None = None

    def __init__(
        self,
        data: Mapping[str, object] | None = None,
        *,
        initial: Mapping[str, object] | None = None,
    ) -> None:
        """Initialise the OAuth/FAPI/security config form.

        Args:
            data: Optional bound form data.
            initial: Initial values decoded from draft config and discovery
                helper metadata.
        """
        super().__init__(
            data=cast(MutableMapping[str, object] | None, data),
            initial=cast(MutableMapping[str, object] | None, initial),
        )
        cast(forms.ChoiceField, self.fields["signing_token_endpoint_auth_method"]).choices = [
            ("", "Select auth method"),
            ("private_key_jwt", "private_key_jwt"),
            ("tls_client_auth", "tls_client_auth"),
        ]

    def clean(self) -> dict[str, object]:
        """Build and validate the security partial config.

        Returns:
            Cleaned form data.
        """
        base_cleaned_data = super().clean()
        cleaned_data: dict[str, object] = {} if base_cleaned_data is None else dict(base_cleaned_data)
        if self.errors:
            return cleaned_data
        signing_fields = (
            "signing_certificate_path",
            "signing_private_key_path",
            "signing_kid",
            "signing_client_assertion_issuer",
            "signing_client_assertion_subject",
            "signing_token_endpoint_auth_method",
        )
        if any(_cleaned_optional_string(cleaned_data.get(field_name)) is not None for field_name in signing_fields):
            for field_name in signing_fields:
                if _cleaned_optional_string(cleaned_data.get(field_name)) is None:
                    self.add_error(field_name, "Complete every FAPI signing field, or leave the whole group blank.")

        mtls_certificate = _cleaned_optional_string(cleaned_data.get("tls_client_certificate_path"))
        mtls_private_key = _cleaned_optional_string(cleaned_data.get("tls_client_private_key_path"))
        if (mtls_certificate is None) != (mtls_private_key is None):
            message = "mTLS client certificate and private key must be supplied together."
            if mtls_certificate is None:
                self.add_error("tls_client_certificate_path", message)
            if mtls_private_key is None:
                self.add_error("tls_client_private_key_path", message)

        if self.errors:
            return cleaned_data
        self.config = _security_config_from_fields(cleaned_data)
        return cleaned_data


class RuntimeInputsConfigForm(forms.Form):
    """Form for catalogue-generated runtime execution artifacts.

    Attributes:
        config: Parsed partial v2 config owned by this step.
    """

    config: JsonObject | None = None

    def __init__(
        self,
        data: Mapping[str, object] | None = None,
        *,
        initial: Mapping[str, object] | None = None,
        runtime_prompts: Iterable[WizardRuntimeInputPrompt] = (),
    ) -> None:
        """Initialise the runtime-input form.

        Args:
            data: Optional bound form data.
            initial: Initial values decoded from the draft config.
            runtime_prompts: Runtime prompts derived from selected endpoints and
                current structured config.
        """
        self.runtime_prompts = tuple(runtime_prompts)
        super().__init__(
            data=cast(MutableMapping[str, object] | None, data),
            initial=cast(MutableMapping[str, object] | None, initial),
        )
        for prompt in self.runtime_prompts:
            self.fields[prompt.name] = forms.CharField(
                label=prompt.label,
                required=False,
                initial=prompt.value,
            )

    @property
    def runtime_prompt_groups(self) -> tuple[RuntimeInputGroup, ...]:
        """Return runtime prompts grouped for rendering.

        Returns:
            Runtime prompt groups in a stable participant-facing order.
        """
        groups: dict[str, list[WizardRuntimeInputPrompt]] = {}
        for prompt in self.runtime_prompts:
            groups.setdefault(prompt.group, []).append(prompt)
        return tuple(RuntimeInputGroup(label=label, prompts=tuple(prompts)) for label, prompts in groups.items())

    def clean(self) -> dict[str, object]:
        """Build and validate the runtime-input partial config.

        Returns:
            Cleaned form data.
        """
        base_cleaned_data = super().clean()
        cleaned_data: dict[str, object] = {} if base_cleaned_data is None else dict(base_cleaned_data)
        if self.errors:
            return cleaned_data
        inputs: JsonObject = {}
        for prompt in self.runtime_prompts:
            value = _runtime_input_value_from_form(prompt, cleaned_data.get(prompt.name))
            if value is not None:
                inputs[prompt.input_id] = {"value": value}
        self.config = {"inputs": inputs} if inputs else {}
        return cleaned_data


def catalogue_boundary_options() -> tuple[PlanDocumentBoundary, ...]:
    """Return v2 plan boundaries shown by the wizard.

    Returns:
        User-facing catalogue boundaries, including catalogue-backed options and
        selector-only examples.
    """
    catalogue_backed_boundaries = supported_plan_document_boundaries(supported_catalogues())
    return catalogue_backed_boundaries + tuple(
        boundary for boundary in _SELECTOR_ONLY_BOUNDARIES if boundary not in catalogue_backed_boundaries
    )


def boundary_requires_resource_groups(boundary: PlanDocumentBoundary) -> bool:
    """Return whether a boundary requires participant resource-group selection.

    Args:
        boundary: User-facing scheme/specification/version boundary.

    Returns:
        True when the boundary uses Read/Write-style resource groups.
    """
    return not boundary_is_selector_only(boundary)


def boundary_is_selector_only(boundary: PlanDocumentBoundary) -> bool:
    """Return whether a boundary is present only for selector UX demonstration.

    Args:
        boundary: User-facing scheme/specification/version boundary.

    Returns:
        True when the wizard can display the boundary but cannot compile or
        launch plans for it yet.
    """
    return boundary in _SELECTOR_ONLY_BOUNDARIES


def catalogue_boundary_continue_blocker(boundary: PlanDocumentBoundary) -> str | None:
    """Return the participant-facing continuation blocker for a boundary.

    Args:
        boundary: User-facing scheme/specification/version boundary.

    Returns:
        Blocking message for selector-only boundaries, otherwise ``None``.
    """
    if not boundary_is_selector_only(boundary):
        return None
    return (
        "Dynamic Client Registration v3.4 is shown as a selector-only example. "
        "DCR catalogue coverage is not implemented yet, so this builder cannot "
        "continue to endpoints or launch a DCR plan."
    )


def catalogue_scope_hierarchy(
    boundary: PlanDocumentBoundary,
    *,
    selected_resource_group_ids: Iterable[str] = (),
    selected_endpoint_ids: Iterable[str] = (),
    selected_capability_values: Iterable[str] = (),
    catalogues: Iterable[TestCatalogue] | None = None,
) -> CatalogueScopeHierarchy:
    """Return resource-group, endpoint, and feature options for a boundary.

    Args:
        boundary: Selected scheme/specification/version boundary.
        selected_resource_group_ids: Resource groups currently selected by the
            participant.
        selected_endpoint_ids: Endpoints currently selected by the participant.
        selected_capability_values: Endpoint capability checkbox values
            currently selected by the participant.
        catalogues: Optional catalogue override used by tests.

    Returns:
        Catalogue-derived scope hierarchy with endpoints revealed only for
        selected resource groups and features revealed only for selected
        endpoints.

    Raises:
        CatalogueError: If the boundary has no backing catalogue areas.
    """
    if boundary_is_selector_only(boundary):
        return CatalogueScopeHierarchy(boundary=boundary, resource_groups=())

    selected_endpoints = set(selected_endpoint_ids)
    candidate_catalogues = catalogue_areas_for_plan_document_boundary(
        boundary,
        tuple(catalogues) if catalogues is not None else supported_catalogues(),
    )
    selected_groups = _normalized_resource_group_ids(
        selected_resource_group_ids,
        catalogues=candidate_catalogues,
    )
    selected_capabilities = _selected_capability_values_by_endpoint(selected_capability_values, strict=False)
    accumulators: dict[str, _ResourceGroupAccumulator] = {}
    group_order: list[str] = []
    for catalogue in candidate_catalogues:
        for endpoint in _endpoint_options_for_catalogue(
            catalogue,
            selected_endpoint_ids=selected_endpoints,
            selected_capability_ids_by_endpoint=selected_capabilities,
        ):
            accumulator = accumulators.get(endpoint.resource_group_id)
            if accumulator is None:
                accumulator = _ResourceGroupAccumulator(
                    group_id=endpoint.resource_group_id,
                    label=endpoint.resource_group_label,
                    api=endpoint.api,
                    api_label=endpoint.api_label,
                    endpoints=[],
                )
                accumulators[endpoint.resource_group_id] = accumulator
                group_order.append(endpoint.resource_group_id)
            accumulator.endpoints.append(endpoint)
    resource_groups = tuple(
        ResourceGroupOption(
            id=accumulator.group_id,
            label=accumulator.label,
            api=accumulator.api,
            api_label=accumulator.api_label,
            endpoint_count=len(accumulator.endpoints),
            selected=accumulator.group_id in selected_groups,
            endpoints=tuple(accumulator.endpoints) if accumulator.group_id in selected_groups else (),
        )
        for accumulator in (accumulators[group_id] for group_id in group_order)
    )
    return CatalogueScopeHierarchy(boundary=boundary, resource_groups=resource_groups)


def scheme_options(*, boundaries: Iterable[PlanDocumentBoundary] | None = None) -> tuple[SchemeOption, ...]:
    """Return scheme selector options.

    Args:
        boundaries: Optional boundary set to derive the selector from.

    Returns:
        Distinct scheme options in boundary order.
    """
    boundary_values = tuple(boundaries) if boundaries is not None else catalogue_boundary_options()
    seen: set[str] = set()
    options: list[SchemeOption] = []
    for boundary in boundary_values:
        if boundary.scheme in seen:
            continue
        seen.add(boundary.scheme)
        options.append(SchemeOption(value=boundary.scheme, label=_scheme_label(boundary.scheme)))
    return tuple(options)


def specification_options(
    *,
    boundaries: Iterable[PlanDocumentBoundary] | None = None,
) -> tuple[SpecificationOption, ...]:
    """Return specification selector options.

    Args:
        boundaries: Optional boundary set to derive the selector from.

    Returns:
        Distinct specification options scoped to their owning schemes.
    """
    boundary_values = tuple(boundaries) if boundaries is not None else catalogue_boundary_options()
    seen: set[tuple[str, str]] = set()
    options: list[SpecificationOption] = []
    for boundary in boundary_values:
        key = (boundary.scheme, boundary.specification)
        if key in seen:
            continue
        seen.add(key)
        options.append(
            SpecificationOption(
                value=boundary.specification,
                label=_specification_label(boundary.specification),
                scheme=boundary.scheme,
            )
        )
    return tuple(options)


def version_options(*, boundaries: Iterable[PlanDocumentBoundary] | None = None) -> tuple[VersionOption, ...]:
    """Return specification-version selector options.

    Args:
        boundaries: Optional boundary set to derive the selector from.

    Returns:
        Version options scoped to their owning scheme and specification.
    """
    boundary_values = tuple(boundaries) if boundaries is not None else catalogue_boundary_options()
    return tuple(
        VersionOption(
            value=boundary.version,
            label=boundary.version,
            scheme=boundary.scheme,
            specification=boundary.specification,
        )
        for boundary in boundary_values
    )


def security_profile_options() -> tuple[tuple[SecurityProfile, str], ...]:
    """Return security-profile choices for the specification step.

    Returns:
        Security profile value/label pairs in display order.
    """
    return _SUPPORTED_SECURITY_PROFILE_OPTIONS


def endpoint_capability_value(*, endpoint_id: str, capability_id: str) -> str:
    """Return the form value for an endpoint-scoped capability.

    Args:
        endpoint_id: Endpoint option id owning the capability.
        capability_id: Catalogue capability id selected under the endpoint.

    Returns:
        Stable compound checkbox value.
    """
    return f"{endpoint_id}{_CAPABILITY_VALUE_SEPARATOR}{capability_id}"


def endpoint_capability_values_from_mapping(capability_ids_by_endpoint: Mapping[str, Iterable[str]]) -> tuple[str, ...]:
    """Return capability form values from a draft-store mapping.

    Args:
        capability_ids_by_endpoint: Selected capability ids keyed by endpoint
            option id.

    Returns:
        Compound endpoint/capability checkbox values in mapping iteration order.
    """
    values: list[str] = []
    for endpoint_id, capability_ids in capability_ids_by_endpoint.items():
        values.extend(
            endpoint_capability_value(endpoint_id=endpoint_id, capability_id=capability_id)
            for capability_id in capability_ids
        )
    return tuple(values)


def config_form_initial(config: Mapping[str, JsonValue]) -> dict[str, object]:
    """Return grouped-config form initial values from a v2 config object.

    Args:
        config: Draft executable config object.

    Returns:
        Initial form values keyed by grouped-config field name.
    """
    initial: dict[str, object] = {
        "discovery_url": _string_config_value(config, "discoveryUrl"),
    }
    oauth = _object_config_value(config, "oauth")
    initial.update(
        {
            "oauth_client_id": _string_config_value(oauth, "clientId"),
            "oauth_redirect_uri": _string_config_value(oauth, "redirectUri"),
            "oauth_authorization_endpoint": _string_config_value(oauth, "authorizationEndpoint"),
            "oauth_issuer": _string_config_value(oauth, "issuer"),
            "oauth_token_endpoint": _string_config_value(oauth, "tokenEndpoint"),
            "oauth_resource_base_url": _string_config_value(oauth, "resourceBaseUrl"),
            "oauth_response_type": _string_config_value(oauth, "responseType"),
            "oauth_request_object_signing_alg": _string_config_value(oauth, "requestObjectSigningAlg"),
        }
    )
    resource_server = _object_config_value(config, "resourceServer")
    initial.update(
        {
            "resource_server_base_url": _string_config_value(resource_server, "baseUrl"),
        }
    )
    signing = _object_config_value(config, "fapiSigning")
    initial.update(
        {
            "signing_certificate_path": _string_config_value(signing, "signingCertificatePath"),
            "signing_private_key_path": _string_config_value(signing, "signingPrivateKeyPath"),
            "signing_kid": _string_config_value(signing, "kid"),
            "signing_client_assertion_issuer": _string_config_value(signing, "clientAssertionIssuer"),
            "signing_client_assertion_subject": _string_config_value(signing, "clientAssertionSubject"),
            "signing_token_endpoint_auth_method": _string_config_value(signing, "tokenEndpointAuthMethod"),
        }
    )
    tls = _object_config_value(config, "tls")
    initial.update(
        {
            "tls_ca_bundle_path": _string_config_value(tls, "caBundlePath"),
            "tls_client_certificate_path": _string_config_value(tls, "clientCertificatePath"),
            "tls_client_private_key_path": _string_config_value(tls, "clientPrivateKeyPath"),
        }
    )
    ais = _object_config_value(config, "ais")
    initial.update(
        {
            "ais_resource_ids_json": _json_config_value(ais, "resourceIds"),
            "ais_transaction_from_date": _string_config_value(ais, "transactionFromDate"),
            "ais_transaction_to_date": _string_config_value(ais, "transactionToDate"),
        }
    )
    pis = _object_config_value(config, "pis")
    initial.update(
        {
            "pis_creditor_account_json": _json_config_value(pis, "creditorAccount"),
            "pis_international_creditor_account_json": _json_config_value(pis, "internationalCreditorAccount"),
            "pis_instructed_amount_json": _json_config_value(pis, "instructedAmount"),
            "pis_currency_of_transfer": _string_config_value(pis, "currencyOfTransfer"),
            "pis_requested_execution_date_time": _string_config_value(pis, "requestedExecutionDateTime"),
            "pis_first_payment_date_time": _string_config_value(pis, "firstPaymentDateTime"),
            "pis_standing_order_frequency_json": _json_config_value(pis, "standingOrderFrequency"),
        }
    )
    cbpii = _object_config_value(config, "cbpii")
    initial["cbpii_debtor_account_json"] = _json_config_value(cbpii, "debtorAccount")
    initial["conditional_properties_json"] = _display_json_value(config.get("conditionalProperties"))
    for input_id, value in _runtime_input_values_from_config(config).items():
        initial[f"{_RUNTIME_INPUT_PREFIX}{input_id}"] = _display_json_value(value)
    return initial


def business_config_form_initial(config: Mapping[str, JsonValue]) -> dict[str, object]:
    """Return business-default form initial values from a v2 config object.

    Args:
        config: Draft executable config object.

    Returns:
        Initial form values keyed by business-default field name.
    """
    initial: dict[str, object] = {}
    ais = _object_config_value(config, "ais")
    resource_ids = _object_config_value(ais, "resourceIds")
    initial.update(
        {
            "ais_consented_account_id": _first_form_config_object_string(resource_ids.get("accountIds"), "accountId"),
            "ais_transaction_from_date": _string_config_value(ais, "transactionFromDate"),
            "ais_transaction_to_date": _string_config_value(ais, "transactionToDate"),
            "ais_resource_ids_json": _json_config_value(ais, "resourceIds"),
        }
    )

    pis = _object_config_value(config, "pis")
    creditor_account = _object_config_value(pis, "creditorAccount")
    international_creditor_account = _object_config_value(pis, "internationalCreditorAccount")
    instructed_amount = _object_config_value(pis, "instructedAmount")
    standing_order_frequency = _object_config_value(pis, "standingOrderFrequency")
    initial.update(
        {
            "pis_creditor_account_scheme_name": _string_config_value(creditor_account, "schemeName"),
            "pis_creditor_account_identification": _string_config_value(creditor_account, "identification"),
            "pis_creditor_account_name": _string_config_value(creditor_account, "name"),
            "pis_creditor_account_json": _json_config_value(pis, "creditorAccount"),
            "pis_international_creditor_account_scheme_name": _string_config_value(
                international_creditor_account,
                "schemeName",
            ),
            "pis_international_creditor_account_identification": _string_config_value(
                international_creditor_account,
                "identification",
            ),
            "pis_international_creditor_account_name": _string_config_value(international_creditor_account, "name"),
            "pis_international_creditor_account_json": _json_config_value(pis, "internationalCreditorAccount"),
            "pis_instructed_amount_amount": _string_config_value(instructed_amount, "amount"),
            "pis_instructed_amount_currency": _string_config_value(instructed_amount, "currency"),
            "pis_instructed_amount_json": _json_config_value(pis, "instructedAmount"),
            "pis_currency_of_transfer": _string_config_value(pis, "currencyOfTransfer"),
            "pis_requested_execution_date_time": _string_config_value(pis, "requestedExecutionDateTime"),
            "pis_first_payment_date_time": _string_config_value(pis, "firstPaymentDateTime"),
            "pis_standing_order_frequency_type": _string_config_value(standing_order_frequency, "type"),
            "pis_standing_order_frequency_point_in_time": _string_config_value(standing_order_frequency, "pointInTime"),
            "pis_standing_order_frequency_json": _json_config_value(pis, "standingOrderFrequency"),
            "conditional_properties_json": _display_json_value(config.get("conditionalProperties")),
        }
    )

    cbpii = _object_config_value(config, "cbpii")
    debtor_account = _object_config_value(cbpii, "debtorAccount")
    initial.update(
        {
            "cbpii_debtor_account_scheme_name": _string_config_value(debtor_account, "schemeName"),
            "cbpii_debtor_account_identification": _string_config_value(debtor_account, "identification"),
            "cbpii_debtor_account_name": _string_config_value(debtor_account, "name"),
            "cbpii_debtor_account_json": _json_config_value(cbpii, "debtorAccount"),
        }
    )
    vrp = _object_config_value(config, "vrp")
    vrp_creditor_account = _object_config_value(vrp, "creditorAccount")
    vrp_instructed_amount = _object_config_value(vrp, "instructedAmount")
    initial.update(
        {
            "vrp_creditor_account_scheme_name": _string_config_value(vrp_creditor_account, "schemeName"),
            "vrp_creditor_account_identification": _string_config_value(vrp_creditor_account, "identification"),
            "vrp_creditor_account_name": _string_config_value(vrp_creditor_account, "name"),
            "vrp_instructed_amount_amount": _string_config_value(vrp_instructed_amount, "amount"),
            "vrp_instructed_amount_currency": _string_config_value(vrp_instructed_amount, "currency"),
            "vrp_valid_from_date_time": _string_config_value(vrp, "validFromDateTime"),
            "vrp_valid_to_date_time": _string_config_value(vrp, "validToDateTime"),
        }
    )
    return initial


def discovery_config_form_initial(config: Mapping[str, JsonValue]) -> dict[str, object]:
    """Return discovery form initial values from a v2 config object.

    Args:
        config: Draft executable config object.

    Returns:
        Initial form values keyed by discovery field name.
    """
    initial: dict[str, object] = {
        "discovery_url": _string_config_value(config, "discoveryUrl"),
    }
    return initial


def security_config_form_initial(
    config: Mapping[str, JsonValue],
    discovery_metadata: Mapping[str, JsonValue],
) -> dict[str, object]:
    """Return security form initial values from config and discovery metadata.

    Args:
        config: Draft executable config object.
        discovery_metadata: Session-only discovery metadata used for defaults.

    Returns:
        Initial form values keyed by security field name.
    """
    oauth = _object_config_value(config, "oauth")
    resource_server = _object_config_value(config, "resourceServer")
    signing = _object_config_value(config, "fapiSigning")
    tls = _object_config_value(config, "tls")
    initial: dict[str, object] = {
        "oauth_client_id": _string_config_value(oauth, "clientId"),
        "oauth_redirect_uri": _string_config_value(oauth, "redirectUri"),
        "oauth_authorization_endpoint": _config_or_discovery_string(
            oauth,
            "authorizationEndpoint",
            discovery_metadata,
            "authorization_endpoint",
        ),
        "oauth_issuer": _config_or_discovery_string(oauth, "issuer", discovery_metadata, "issuer"),
        "oauth_token_endpoint": _config_or_discovery_string(
            oauth,
            "tokenEndpoint",
            discovery_metadata,
            "token_endpoint",
        ),
        "oauth_response_type": _string_config_value(oauth, "responseType")
        or _single_discovery_list_value(discovery_metadata, "response_types_supported"),
        "oauth_request_object_signing_alg": _string_config_value(oauth, "requestObjectSigningAlg")
        or _single_discovery_list_value(discovery_metadata, "request_object_signing_alg_values_supported"),
        "resource_server_base_url": _string_config_value(resource_server, "baseUrl")
        or _string_config_value(oauth, "resourceBaseUrl"),
        "signing_certificate_path": _string_config_value(signing, "signingCertificatePath"),
        "signing_private_key_path": _string_config_value(signing, "signingPrivateKeyPath"),
        "signing_kid": _string_config_value(signing, "kid"),
        "signing_client_assertion_issuer": _string_config_value(signing, "clientAssertionIssuer"),
        "signing_client_assertion_subject": _string_config_value(signing, "clientAssertionSubject"),
        "signing_token_endpoint_auth_method": _string_config_value(signing, "tokenEndpointAuthMethod"),
        "tls_ca_bundle_path": _string_config_value(tls, "caBundlePath"),
        "tls_client_certificate_path": _string_config_value(tls, "clientCertificatePath"),
        "tls_client_private_key_path": _string_config_value(tls, "clientPrivateKeyPath"),
    }
    return initial


def merge_config_sections(
    config: Mapping[str, JsonValue],
    section_config: Mapping[str, JsonValue],
    *,
    section_keys: Iterable[str],
) -> JsonObject:
    """Return ``config`` with one guided-config section replaced.

    Args:
        config: Existing draft config.
        section_config: Partial config emitted by one wizard page.
        section_keys: Top-level keys owned by that page.

    Returns:
        Updated config with unrelated sections preserved.
    """
    updated = _copy_json_mapping(config)
    for key in section_keys:
        updated.pop(key, None)
    for key, value in section_config.items():
        updated[key] = _copy_json_value(value)
    return updated


def merge_business_config(config: Mapping[str, JsonValue], section_config: Mapping[str, JsonValue]) -> JsonObject:
    """Return ``config`` with business/default sections replaced.

    Args:
        config: Existing draft config.
        section_config: Business partial config emitted by the form.

    Returns:
        Updated config with business keys replaced.
    """
    return merge_config_sections(config, section_config, section_keys=_BUSINESS_CONFIG_KEYS)


def merge_discovery_config(config: Mapping[str, JsonValue], section_config: Mapping[str, JsonValue]) -> JsonObject:
    """Return ``config`` with discovery fields replaced.

    Args:
        config: Existing draft config.
        section_config: Discovery partial config emitted by the form.

    Returns:
        Updated config with discovery keys replaced.
    """
    return merge_config_sections(config, section_config, section_keys=_DISCOVERY_CONFIG_KEYS)


def merge_security_config(config: Mapping[str, JsonValue], section_config: Mapping[str, JsonValue]) -> JsonObject:
    """Return ``config`` with OAuth/FAPI/security fields replaced.

    Args:
        config: Existing draft config.
        section_config: Security partial config emitted by the form.

    Returns:
        Updated config with security keys replaced.
    """
    return merge_config_sections(config, section_config, section_keys=_SECURITY_CONFIG_KEYS)


def merge_runtime_input_config(config: Mapping[str, JsonValue], section_config: Mapping[str, JsonValue]) -> JsonObject:
    """Return ``config`` with runtime input fields replaced.

    Args:
        config: Existing draft config.
        section_config: Runtime-input partial config emitted by the form.

    Returns:
        Updated config with runtime-input keys replaced.
    """
    return merge_config_sections(config, section_config, section_keys=_RUNTIME_CONFIG_KEYS)


def model_bank_config_from_plan_config(config: Mapping[str, JsonValue]) -> JsonObject:
    """Extract executable model-bank config fields from editable plan config.

    Args:
        config: Builder config object, which may also contain
            catalogue runtime inputs under ``inputs`` or ``runtimeInputs``.

    Returns:
        Model-bank config JSON object accepted by
        :func:`conformance.model_bank_config.parse_model_bank_config`.
    """
    return {key: _copy_json_value(value) for key, value in config.items() if key in _MODEL_CONFIG_KEYS}


def plan_document_from_draft(draft: BuilderDraft, *, config: Mapping[str, JsonValue] | None = None) -> PlanDocumentV2:
    """Build a parsed canonical test plan document from a wizard draft.

    Args:
        draft: Session-backed builder draft.
        config: Optional config object to use instead of ``draft.config``.

    Returns:
        Parsed schemaVersion ``1.0`` plan document suitable for compile, export,
        or launch.

    Raises:
        CatalogueError: If the draft is incomplete or contains stale scope ids.
    """
    boundary = _draft_boundary_or_error(draft)
    hierarchy = catalogue_scope_hierarchy(
        boundary,
        selected_resource_group_ids=draft.resource_group_ids,
        selected_endpoint_ids=draft.endpoint_ids,
        selected_capability_values=endpoint_capability_values_from_mapping(draft.endpoint_capability_ids),
    )
    selected_group_ids = _normalized_resource_group_ids_for_hierarchy(draft.resource_group_ids, hierarchy=hierarchy)
    selected_endpoint_ids = set(draft.endpoint_ids)
    known_group_ids = {group.id for group in hierarchy.resource_groups}
    unknown_group_ids = selected_group_ids - known_group_ids
    if unknown_group_ids:
        raise CatalogueError(f"Unknown resource group id(s): {', '.join(sorted(unknown_group_ids))}")

    included_endpoint_ids: set[str] = set()
    selected_api_ids: set[str] = set()
    raw_groups: list[JsonValue] = []
    for group in hierarchy.resource_groups:
        if group.id not in selected_group_ids:
            continue
        raw_endpoints: list[JsonValue] = []
        for endpoint in group.endpoints:
            endpoint_ref = EndpointRef(method=endpoint.method, path=endpoint.path)
            legacy_endpoint_id = _legacy_endpoint_id(endpoint_ref)
            if endpoint.id not in selected_endpoint_ids and legacy_endpoint_id not in selected_endpoint_ids:
                continue
            included_endpoint_ids.add(endpoint.id)
            included_endpoint_ids.add(legacy_endpoint_id)
            capability_ids = tuple(
                draft.endpoint_capability_ids.get(
                    endpoint.id,
                    draft.endpoint_capability_ids.get(legacy_endpoint_id, ()),
                )
            )
            raw_endpoints.append(_raw_canonical_endpoint(endpoint, capability_ids))
        if raw_endpoints:
            selected_api_ids.add(group.api)
            raw_groups.append(_raw_canonical_resource_group(group=group, endpoints=raw_endpoints))

    unknown_endpoint_ids = selected_endpoint_ids - included_endpoint_ids
    if unknown_endpoint_ids:
        raise CatalogueError(f"Unknown endpoint id(s): {', '.join(sorted(unknown_endpoint_ids))}")

    config_object = _copy_json_mapping(config if config is not None else draft.config)
    security_environment = _merged_plan_context(
        draft.security_environment,
        security_environment_from_plan_config(config_object),
    )
    business_test_data = _scoped_business_plan_context(
        draft.business_test_data,
        business_test_data_from_plan_config(config_object),
        selected_api_ids=frozenset(selected_api_ids),
    )
    raw_plan: JsonObject = {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": boundary.version,
            "profile": _canonical_security_profile(draft.security_profile),
        },
        "executionMode": draft.execution_mode,
        "securityEnvironment": security_environment,
        "resourceGroups": raw_groups,
        "businessTestData": business_test_data,
        "metadata": _copy_json_mapping(draft.metadata),
    }
    document = parse_test_plan_document(raw_plan)
    if not isinstance(document, PlanDocumentV2):
        raise CatalogueError("Builder drafts must produce a canonical test plan document")
    return document


def _canonical_security_profile(security_profile: str) -> str:
    """Return the canonical JSON-first security profile value.

    Args:
        security_profile: Internal compiler security profile.

    Returns:
        Canonical profile string used by exported JSON-first test plans.
    """
    profiles = {"fapi1-advanced": "FAPI1_ADVANCED", "fapi2": "FAPI2", "all": "ALL"}
    return profiles.get(security_profile, security_profile)


def _canonical_resource_group_id(api: str) -> str:
    """Return the canonical JSON-first resource-group id for an API family.

    Args:
        api: Internal catalogue API family id.

    Returns:
        Canonical resource-group id.

    Raises:
        CatalogueError: If the API family cannot be represented in the
            JSON-first resource-group model.
    """
    canonical_id = _CANONICAL_RESOURCE_GROUP_ID_BY_API.get(api)
    if canonical_id is None:
        raise CatalogueError(f"Unsupported resource group API for canonical JSON export: {api}")
    return canonical_id


def _raw_canonical_resource_group(
    *,
    group: ResourceGroupOption,
    endpoints: list[JsonValue],
) -> JsonObject:
    """Return one canonical resource-group object for selected endpoints.

    Args:
        group: Selected resource-group option from the current scope hierarchy.
        endpoints: Endpoint objects selected within the group.

    Returns:
        Canonical detailed ``resourceGroups`` entry.
    """
    return {
        "id": _canonical_resource_group_id(group.api),
        "label": group.label,
        "endpoints": endpoints,
    }


def _raw_canonical_endpoint(endpoint: EndpointOption, capability_ids: tuple[str, ...]) -> JsonObject:
    """Return one canonical endpoint declaration for a selected endpoint.

    Args:
        endpoint: Selected endpoint option.
        capability_ids: Optional capability ids selected under the endpoint.

    Returns:
        Canonical endpoint object.
    """
    raw_endpoint: JsonObject = {
        "method": endpoint.method,
        "path": endpoint.path,
        "operationId": endpoint.operation_id,
    }
    if capability_ids:
        raw_endpoint["capabilities"] = list(capability_ids)
    return raw_endpoint


def draft_scope_from_plan_document(
    document: PlanDocumentV2,
) -> tuple[tuple[str, ...], tuple[str, ...], dict[str, tuple[str, ...]]]:
    """Return draft scope ids decoded from an imported test-plan document.

    Args:
        document: Parsed canonical test-plan document imported through the browser.

    Returns:
        Tuple of resource-group ids, endpoint ids, and endpoint capability ids
        suitable for :meth:`BuilderDraft.with_scope_selection`.
    """
    resource_group_ids: list[str] = []
    endpoint_ids: list[str] = []
    capability_ids_by_endpoint: dict[str, tuple[str, ...]] = {}
    for resource_group in document.resource_groups:
        api = _api_from_resource_group_id(resource_group.resource_group_id)
        resource_group_id = _resource_group_id(api) if api is not None else resource_group.resource_group_id
        if resource_group_id not in resource_group_ids:
            resource_group_ids.append(resource_group_id)
        if resource_group.select_all:
            hierarchy = catalogue_scope_hierarchy(
                PlanDocumentBoundary(document.scheme, document.specification, document.version),
                selected_resource_group_ids=(resource_group_id,),
            )
            endpoint_ids.extend(
                endpoint.id
                for group in hierarchy.resource_groups
                for endpoint in group.endpoints
                if group.id == resource_group_id
            )
            continue
        for endpoint in resource_group.endpoints:
            endpoint_ref = EndpointRef(method=endpoint.method, path=endpoint.path)
            endpoint_id = _endpoint_id_for_resource_group_endpoint(
                document,
                resource_group_id=resource_group.resource_group_id,
                endpoint_ref=endpoint_ref,
            )
            endpoint_ids.append(endpoint_id)
            if endpoint.capability_ids:
                capability_ids_by_endpoint[endpoint_id] = endpoint.capability_ids
    return tuple(resource_group_ids), tuple(endpoint_ids), capability_ids_by_endpoint


def runtime_input_prompts_for_draft(draft: BuilderDraft) -> tuple[WizardRuntimeInputPrompt, ...]:
    """Return runtime prompts derived from a draft's selected endpoints.

    Args:
        draft: Current wizard draft.

    Returns:
        Runtime prompts in compiler trace order.

    Raises:
        CatalogueError: If the draft scope cannot be resolved against bundled
            catalogues.
    """
    return runtime_input_prompts_for_plan_document(plan_document_from_draft(draft))


def runtime_input_prompts_for_plan_document(document: PlanDocumentV2) -> tuple[WizardRuntimeInputPrompt, ...]:
    """Return runtime prompts derived from a canonical test-plan document.

    Args:
        document: Parsed canonical test-plan document.

    Returns:
        Runtime prompts in compiler trace order.

    Raises:
        CatalogueError: If endpoint scope cannot be compiled.
    """
    if not any(resource_group.endpoints or resource_group.select_all for resource_group in document.resource_groups):
        return ()
    boundary_requirements = _runtime_requirements_for_boundary(
        PlanDocumentBoundary(document.scheme, document.specification, document.version)
    )
    preview_document = plan_document_with_runtime_placeholders(document, boundary_requirements.values())
    compiled_plan = compile_test_plan_document(preview_document, supported_catalogues())
    requirements = _runtime_requirements_for_test_cases(compiled_plan.test_cases)
    actual_values = document.runtime_inputs
    prompts: list[WizardRuntimeInputPrompt] = []
    for trace in compiled_plan.traceability.runtime_input_snapshot:
        if trace.input_id in _STRUCTURED_CONFIG_RUNTIME_INPUT_IDS and _runtime_input_is_present(
            actual_values.get(trace.input_id)
        ):
            continue
        requirement = requirements.get(trace.input_id)
        if requirement is not None and requirement.source != "plan":
            continue
        label = requirement.label if requirement is not None else trace.input_id
        description = requirement.description if requirement is not None else None
        prompts.append(
            WizardRuntimeInputPrompt(
                input_id=trace.input_id,
                name=f"{_RUNTIME_INPUT_PREFIX}{trace.input_id}",
                label=label,
                input_type=trace.input_type,
                required=trace.required,
                sensitive=trace.sensitive,
                value=_display_json_value(actual_values.get(trace.input_id)),
                group=_runtime_prompt_group(trace.input_id, trace.input_type),
                description=description,
            )
        )
    return tuple(prompts)


def config_visibility_for_draft(draft: BuilderDraft) -> ConfigVisibility:
    """Return grouped-config field visibility for a draft's selected scope.

    Args:
        draft: Current wizard draft.

    Returns:
        Scope-derived grouped-config field visibility.

    Raises:
        CatalogueError: If the draft scope cannot be resolved against bundled
            catalogues.
    """
    return config_visibility_for_plan_document(plan_document_from_draft(draft))


def config_visibility_for_plan_document(document: PlanDocumentV2) -> ConfigVisibility:
    """Return grouped-config field visibility for a canonical test-plan document.

    Args:
        document: Parsed canonical test-plan document with selected resource groups and
            endpoints.

    Returns:
        Visibility flags for domain-specific grouped config fields.
    """
    selected_api_ids = _selected_endpoint_api_ids(document)
    show_ais = "ais" in selected_api_ids
    show_pis = "pis" in selected_api_ids
    show_cbpii = "cbpii" in selected_api_ids
    ais_account_id_required = show_ais and _selected_scope_requires_runtime_input(document, "consentedAccountId")
    pis_requiredness = _pis_requiredness_for_selected_scope(document) if show_pis else {}
    return ConfigVisibility(
        selected_api_ids=selected_api_ids,
        show_ais=show_ais,
        show_pis=show_pis,
        show_cbpii=show_cbpii,
        show_vrp="vrp" in selected_api_ids,
        show_business_defaults=show_ais or show_pis or show_cbpii or "vrp" in selected_api_ids,
        ais_account_id_required=ais_account_id_required,
        pis_domestic_creditor_account_required=pis_requiredness.get("domestic_creditor_account", False),
        pis_international_creditor_account_required=pis_requiredness.get("international_creditor_account", False),
        pis_instructed_amount_required=pis_requiredness.get("instructed_amount", False),
        pis_currency_of_transfer_required=pis_requiredness.get("currency_of_transfer", False),
        pis_requested_execution_date_time_required=pis_requiredness.get("requested_execution_date_time", False),
        pis_first_payment_date_time_required=pis_requiredness.get("first_payment_date_time", False),
        pis_standing_order_frequency_required=pis_requiredness.get("standing_order_frequency", False),
    )


def _pis_requiredness_for_selected_scope(document: PlanDocumentV2) -> dict[str, bool]:
    """Return PIS business-default requirements for selected endpoint families.

    Args:
        document: Parsed canonical test-plan document with selected endpoints.

    Returns:
        Requirement flags keyed by PIS business-default concept.
    """
    requiredness = {
        "domestic_creditor_account": False,
        "international_creditor_account": False,
        "instructed_amount": False,
        "currency_of_transfer": False,
        "requested_execution_date_time": False,
        "first_payment_date_time": False,
        "standing_order_frequency": False,
    }
    for path in _selected_pis_endpoint_paths(document):
        _update_pis_requiredness_for_path(requiredness, path)
    return requiredness


def _selected_pis_endpoint_paths(document: PlanDocumentV2) -> tuple[str, ...]:
    """Return selected PIS endpoint paths, expanding group-level selections.

    Args:
        document: Parsed canonical test-plan document with selected endpoints.

    Returns:
        Standards paths for selected PIS endpoints.
    """
    paths: list[str] = []
    for resource_group in document.resource_groups:
        group_api = _api_from_resource_group_id(resource_group.resource_group_id)
        if resource_group.select_all and group_api == "pis":
            paths.extend(_all_pis_endpoint_paths())
            continue
        for endpoint in resource_group.endpoints:
            endpoint_api = _api_from_endpoint_path(endpoint.path) or group_api
            if endpoint_api == "pis":
                paths.append(endpoint.path)
    return tuple(paths)


def _all_pis_endpoint_paths() -> tuple[str, ...]:
    """Return every bundled PIS endpoint path for a group-level selection.

    Returns:
        De-duplicated PIS endpoint paths from the supported catalogues.
    """
    paths: list[str] = []
    seen: set[str] = set()
    for catalogue in supported_catalogues():
        if catalogue.key.api != "pis":
            continue
        for test_case in catalogue.test_cases:
            for endpoint_ref in test_case.applicability.endpoint_refs:
                if endpoint_ref.path not in seen:
                    seen.add(endpoint_ref.path)
                    paths.append(endpoint_ref.path)
        for capability in catalogue.capabilities:
            for endpoint_ref in capability.endpoint_refs:
                if endpoint_ref.path not in seen:
                    seen.add(endpoint_ref.path)
                    paths.append(endpoint_ref.path)
    return tuple(paths)


def _update_pis_requiredness_for_path(requiredness: dict[str, bool], path: str) -> None:
    """Mark PIS business defaults required for one selected endpoint path.

    Args:
        requiredness: Mutable requiredness mapping to update.
        path: Standards endpoint path selected in the builder.
    """
    if "/pisp/domestic-standing-order" in path:
        requiredness["domestic_creditor_account"] = True
        requiredness["instructed_amount"] = True
        requiredness["first_payment_date_time"] = True
        requiredness["standing_order_frequency"] = True
        return
    if "/pisp/domestic-scheduled-payment" in path:
        requiredness["domestic_creditor_account"] = True
        requiredness["instructed_amount"] = True
        requiredness["requested_execution_date_time"] = True
        return
    if "/pisp/domestic-payment" in path:
        requiredness["domestic_creditor_account"] = True
        requiredness["instructed_amount"] = True
        return
    if "/pisp/international-scheduled-payment" in path:
        requiredness["international_creditor_account"] = True
        requiredness["instructed_amount"] = True
        requiredness["currency_of_transfer"] = True
        requiredness["requested_execution_date_time"] = True
        return
    if "/pisp/international-payment" in path:
        requiredness["international_creditor_account"] = True
        requiredness["instructed_amount"] = True
        requiredness["currency_of_transfer"] = True


def _selected_scope_requires_runtime_input(document: PlanDocumentV2, input_id: str) -> bool:
    """Return whether the selected scope requires a plan-sourced runtime input.

    Args:
        document: Parsed canonical test-plan document with selected endpoints.
        input_id: Runtime input identifier to check.

    Returns:
        ``True`` when selected catalogue cases require the input from plan
        business data.
    """
    if not any(resource_group.endpoints or resource_group.select_all for resource_group in document.resource_groups):
        return False
    boundary_requirements = _runtime_requirements_for_boundary(
        PlanDocumentBoundary(document.scheme, document.specification, document.version)
    )
    preview_document = plan_document_with_runtime_placeholders(document, boundary_requirements.values())
    compiled_plan = compile_test_plan_document(preview_document, supported_catalogues())
    requirement = _runtime_requirements_for_test_cases(compiled_plan.test_cases).get(input_id)
    return requirement is not None and requirement.required and requirement.source == "plan"


def security_field_metadata() -> dict[str, SecurityFieldMetadata]:
    """Return security field metadata keyed by form field name.

    Returns:
        Mapping of security form field names to participant-facing metadata.
    """
    return dict(_SECURITY_FIELD_METADATA_BY_NAME)


def plan_document_with_runtime_placeholders(
    document: PlanDocumentV2,
    requirements: Iterable[RuntimeInputRequirement | WizardRuntimeInputPrompt],
) -> PlanDocumentV2:
    """Return a copy of ``document`` with missing required runtime inputs filled.

    Args:
        document: Parsed canonical test-plan document to copy.
        requirements: Runtime requirements or prompts whose required missing
            values should be substituted for preview compilation only.

    Returns:
        Parsed canonical test-plan document with placeholder runtime values.
    """
    return _plan_document_with_config(document, _config_with_runtime_placeholders(document.config, requirements))


def missing_required_runtime_inputs(
    document: PlanDocumentV2,
    prompts: Iterable[WizardRuntimeInputPrompt],
) -> tuple[WizardRuntimeInputPrompt, ...]:
    """Return required runtime prompts absent from a plan document.

    Args:
        document: Parsed canonical test-plan document with actual participant config.
        prompts: Runtime prompts derived from selected scope.

    Returns:
        Required prompts whose value is absent or blank.
    """
    return tuple(
        prompt
        for prompt in prompts
        if prompt.required and not _runtime_input_is_present(document.runtime_inputs.get(prompt.input_id))
    )


def plan_document_to_export_json(
    document: PlanDocumentV2,
    *,
    sensitive_runtime_input_ids: Iterable[str],
    include_secrets: bool,
) -> JsonObject:
    """Return browser export JSON for a canonical test-plan document.

    Args:
        document: Parsed canonical test-plan document to export.
        sensitive_runtime_input_ids: Runtime input ids marked sensitive by the
            compiler trace.
        include_secrets: Whether to include actual secret-bearing values.

    Returns:
        Exportable JSON object. Safe exports preserve keys but replace
        secret-bearing strings with empty strings.
    """
    exported = plan_document_to_json_object(document)
    if include_secrets:
        return exported
    return safe_test_plan_snapshot(document, sensitive_runtime_input_ids=sensitive_runtime_input_ids)


def _endpoint_options_for_catalogue(
    catalogue: TestCatalogue,
    *,
    selected_endpoint_ids: set[str],
    selected_capability_ids_by_endpoint: Mapping[str, set[str]],
) -> tuple[EndpointOption, ...]:
    """Build endpoint options for one catalogue area.

    Args:
        catalogue: Catalogue whose endpoint and capability refs should be shown.
        selected_endpoint_ids: Endpoint option ids currently selected.
        selected_capability_ids_by_endpoint: Capability ids selected under each
            endpoint option id.

    Returns:
        De-duplicated endpoint options sorted by resource group and path.
    """
    endpoint_refs: list[EndpointRef] = []
    seen: set[EndpointRef] = set()
    for test_case in catalogue.test_cases:
        for endpoint_ref in test_case.applicability.endpoint_refs:
            if endpoint_ref not in seen:
                seen.add(endpoint_ref)
                endpoint_refs.append(endpoint_ref)
    for capability in catalogue.capabilities:
        for endpoint_ref in capability.endpoint_refs:
            if endpoint_ref not in seen:
                seen.add(endpoint_ref)
                endpoint_refs.append(endpoint_ref)

    options: list[EndpointOption] = []
    resource_group_id = _resource_group_id(catalogue.key.api)
    resource_group_label = _resource_group_label(catalogue.key.api)
    for endpoint_ref in endpoint_refs:
        endpoint_id = _endpoint_id(catalogue.key.api, endpoint_ref)
        legacy_endpoint_id = _legacy_endpoint_id(endpoint_ref)
        selected_capability_ids = selected_capability_ids_by_endpoint.get(
            endpoint_id, set()
        ) | selected_capability_ids_by_endpoint.get(
            legacy_endpoint_id,
            set(),
        )
        selected = endpoint_id in selected_endpoint_ids or legacy_endpoint_id in selected_endpoint_ids
        options.append(
            EndpointOption(
                id=endpoint_id,
                method=endpoint_ref.method,
                path=endpoint_ref.path,
                display_path=_endpoint_display_path(endpoint_ref.path),
                operation_id=_operation_id(catalogue.key.api, endpoint_ref),
                api=catalogue.key.api,
                api_label=_api_label(catalogue.key.api),
                resource_group_id=resource_group_id,
                resource_group_label=resource_group_label,
                baseline=_endpoint_has_baseline_coverage(catalogue, endpoint_ref),
                selected=selected,
                features=(
                    _feature_options_for_endpoint(
                        catalogue=catalogue,
                        endpoint_ref=endpoint_ref,
                        endpoint_id=endpoint_id,
                        selected_capability_ids=selected_capability_ids,
                    )
                    if selected
                    else ()
                ),
            )
        )
    return tuple(
        sorted(
            options,
            key=lambda option: (_endpoint_family_label(option.api, option.path), option.path, option.method),
        )
    )


def _feature_options_for_endpoint(
    *,
    catalogue: TestCatalogue,
    endpoint_ref: EndpointRef,
    endpoint_id: str,
    selected_capability_ids: set[str],
) -> tuple[FeatureOption, ...]:
    """Build feature options for one selected endpoint.

    Args:
        catalogue: Catalogue containing capability definitions.
        endpoint_ref: Endpoint reference owning the rendered features.
        endpoint_id: Endpoint option id that scopes checkbox values.
        selected_capability_ids: Optional capability ids selected under this
            endpoint.

    Returns:
        Required baseline features and optional features in catalogue order.
    """
    return tuple(
        FeatureOption(
            value=endpoint_capability_value(endpoint_id=endpoint_id, capability_id=capability.capability_id),
            endpoint_id=endpoint_id,
            capability_id=capability.capability_id,
            label=capability.label,
            description=capability.description,
            required=capability.required,
            kind="Required baseline" if capability.required else "Optional feature",
            selected=capability.required or capability.capability_id in selected_capability_ids,
        )
        for capability in catalogue.capabilities
        if endpoint_ref in capability.endpoint_refs
    )


def _endpoint_has_baseline_coverage(catalogue: TestCatalogue, endpoint_ref: EndpointRef) -> bool:
    """Return whether an endpoint has required or mandatory catalogue coverage.

    Args:
        catalogue: Catalogue owning the endpoint.
        endpoint_ref: Endpoint reference to inspect.

    Returns:
        True when the endpoint has a required capability or a mandatory test
        case in the current catalogue.
    """
    required_capability = any(
        capability.required and endpoint_ref in capability.endpoint_refs for capability in catalogue.capabilities
    )
    mandatory_case = any(
        test_case.mandatory and endpoint_ref in test_case.applicability.endpoint_refs
        for test_case in catalogue.test_cases
    )
    return required_capability or mandatory_case


def _endpoint_options(hierarchy: CatalogueScopeHierarchy) -> tuple[EndpointOption, ...]:
    """Return endpoint options currently revealed by a hierarchy.

    Args:
        hierarchy: Scope hierarchy built for the current form state.

    Returns:
        Flattened endpoint options from selected resource groups.
    """
    return tuple(endpoint for group in hierarchy.resource_groups for endpoint in group.endpoints)


def _feature_options(hierarchy: CatalogueScopeHierarchy) -> tuple[FeatureOption, ...]:
    """Return feature options currently revealed by a hierarchy.

    Args:
        hierarchy: Scope hierarchy built for the current form state.

    Returns:
        Flattened feature options from selected endpoints.
    """
    return tuple(feature for endpoint in _endpoint_options(hierarchy) for feature in endpoint.features)


def _pruned_scope_form_data(
    data: Mapping[str, object],
    *,
    hierarchy: CatalogueScopeHierarchy,
) -> dict[str, object]:
    """Return scope form data with stale child selections removed.

    Args:
        data: Raw scope form submission.
        hierarchy: Hierarchy built from the same submitted parent selections.

    Returns:
        Form data containing only currently visible resource groups, endpoints,
        and endpoint capabilities.
    """
    selected_group_ids = [group.id for group in hierarchy.resource_groups if group.selected]
    available_endpoint_ids = {endpoint.id for endpoint in _endpoint_options(hierarchy)}
    selected_endpoint_ids = [
        endpoint_id for endpoint_id in _raw_values(data, "endpoints") if endpoint_id in available_endpoint_ids
    ]
    available_feature_values = {feature.value for feature in _feature_options(hierarchy)}
    selected_capability_values = [
        capability_value
        for capability_value in _raw_values(data, "endpoint_capabilities")
        if capability_value in available_feature_values
    ]
    return {
        "resource_groups": selected_group_ids,
        "endpoints": selected_endpoint_ids,
        "endpoint_capabilities": selected_capability_values,
    }


def _pruned_catalogue_boundary_form_data(
    data: Mapping[str, object],
    *,
    hierarchy: CatalogueScopeHierarchy,
) -> dict[str, object]:
    """Return boundary form data with stale resource groups removed.

    Args:
        data: Raw catalogue-boundary form submission.
        hierarchy: Hierarchy built for the submitted boundary values.

    Returns:
        Form data containing submitted boundary selector values and only
        resource groups available for that boundary.
    """
    available_group_ids = {group.id for group in hierarchy.resource_groups}
    pruned_data: dict[str, object] = {}
    for key in ("scheme", "specification", "version"):
        values = _raw_values(data, key)
        if values:
            pruned_data[key] = values[0]
    pruned_data["resource_groups"] = [
        group_id for group_id in _raw_values(data, "resource_groups") if group_id in available_group_ids
    ]
    return pruned_data


def _business_config_from_fields(
    cleaned_data: Mapping[str, object],
    config_visibility: ConfigVisibility,
) -> JsonObject:
    """Build a business/default partial config from form fields.

    Args:
        cleaned_data: Cleaned form values.
        config_visibility: Scope-derived structured-field visibility.

    Returns:
        Partial v2 plan config containing business/request default sections.

    Raises:
        ValidationError: If an advanced JSON fallback value is malformed.
    """
    config: JsonObject = {}
    if config_visibility.show_ais:
        ais = _nested_object_from_fields(
            cleaned_data,
            {
                "transactionFromDate": "ais_transaction_from_date",
                "transactionToDate": "ais_transaction_to_date",
            },
        )
        resource_ids_json = _cleaned_optional_string(cleaned_data.get("ais_resource_ids_json"))
        if resource_ids_json is not None:
            ais["resourceIds"] = _load_json_object(resource_ids_json, label="AIS resource IDs JSON")
        else:
            account_id = _cleaned_optional_string(cleaned_data.get("ais_consented_account_id"))
            if account_id is not None:
                ais["resourceIds"] = {"accountIds": [{"accountId": account_id}]}
        if ais:
            config["ais"] = ais

    if config_visibility.show_pis:
        pis = _nested_object_from_fields(
            cleaned_data,
            {
                "currencyOfTransfer": "pis_currency_of_transfer",
                "requestedExecutionDateTime": "pis_requested_execution_date_time",
                "firstPaymentDateTime": "pis_first_payment_date_time",
            },
        )
        _set_object_from_fields_or_json(
            pis,
            "creditorAccount",
            cleaned_data,
            json_field="pis_creditor_account_json",
            field_mapping={
                "schemeName": "pis_creditor_account_scheme_name",
                "identification": "pis_creditor_account_identification",
                "name": "pis_creditor_account_name",
            },
            label="Domestic creditor account JSON",
        )
        _set_object_from_fields_or_json(
            pis,
            "internationalCreditorAccount",
            cleaned_data,
            json_field="pis_international_creditor_account_json",
            field_mapping={
                "schemeName": "pis_international_creditor_account_scheme_name",
                "identification": "pis_international_creditor_account_identification",
                "name": "pis_international_creditor_account_name",
            },
            label="International creditor account JSON",
        )
        _set_object_from_fields_or_json(
            pis,
            "instructedAmount",
            cleaned_data,
            json_field="pis_instructed_amount_json",
            field_mapping={
                "amount": "pis_instructed_amount_amount",
                "currency": "pis_instructed_amount_currency",
            },
            label="Instructed amount JSON",
        )
        _set_object_from_fields_or_json(
            pis,
            "standingOrderFrequency",
            cleaned_data,
            json_field="pis_standing_order_frequency_json",
            field_mapping={
                "type": "pis_standing_order_frequency_type",
                "pointInTime": "pis_standing_order_frequency_point_in_time",
            },
            label="Standing-order frequency JSON",
        )
        if pis:
            config["pis"] = pis
        _set_optional_json_array(config, "conditionalProperties", cleaned_data.get("conditional_properties_json"))

    if config_visibility.show_cbpii:
        cbpii: JsonObject = {}
        _set_object_from_fields_or_json(
            cbpii,
            "debtorAccount",
            cleaned_data,
            json_field="cbpii_debtor_account_json",
            field_mapping={
                "schemeName": "cbpii_debtor_account_scheme_name",
                "identification": "cbpii_debtor_account_identification",
                "name": "cbpii_debtor_account_name",
            },
            label="CBPII debtor account JSON",
        )
        if cbpii:
            config["cbpii"] = cbpii
    if config_visibility.show_vrp:
        vrp = _nested_object_from_fields(
            cleaned_data,
            {
                "validFromDateTime": "vrp_valid_from_date_time",
                "validToDateTime": "vrp_valid_to_date_time",
            },
        )
        vrp["creditorAccount"] = _nested_object_from_fields(
            cleaned_data,
            {
                "schemeName": "vrp_creditor_account_scheme_name",
                "identification": "vrp_creditor_account_identification",
                "name": "vrp_creditor_account_name",
            },
        )
        vrp["instructedAmount"] = _nested_object_from_fields(
            cleaned_data,
            {
                "amount": "vrp_instructed_amount_amount",
                "currency": "vrp_instructed_amount_currency",
            },
        )
        config["vrp"] = vrp
    return config


def _ais_config_has_account_id(value: JsonValue | None) -> bool:
    """Return whether AIS business config contains a consented account id.

    Args:
        value: Candidate ``planSpec.config.ais`` object.

    Returns:
        ``True`` when the first configured account id is a non-blank string.
    """
    if not isinstance(value, dict):
        return False
    resource_ids = value.get("resourceIds")
    if not isinstance(resource_ids, dict):
        return False
    account_id = _first_form_config_object_string(resource_ids.get("accountIds"), "accountId")
    return account_id is not None and bool(account_id.strip())


def _add_required_pis_errors(form: BusinessConfigForm, cleaned_data: Mapping[str, object]) -> None:
    """Add scoped PIS business-default validation errors to a bound form.

    Args:
        form: Bound business config form whose config has already been built.
        cleaned_data: Cleaned form values used to identify JSON fallback usage.
    """
    visibility = form.config_visibility
    config = form.config if form.config is not None else {}
    pis_config = config.get("pis")
    pis = pis_config if isinstance(pis_config, dict) else {}
    if visibility.pis_domestic_creditor_account_required:
        _add_required_json_object_errors(
            form,
            pis,
            object_key="creditorAccount",
            required_keys=("schemeName", "identification", "name"),
            field_names={
                "schemeName": "pis_creditor_account_scheme_name",
                "identification": "pis_creditor_account_identification",
                "name": "pis_creditor_account_name",
            },
            json_field_name="pis_creditor_account_json",
            json_field_supplied=_cleaned_optional_string(cleaned_data.get("pis_creditor_account_json")) is not None,
            message="Domestic creditor account is required for selected PIS endpoints.",
        )
    if visibility.pis_international_creditor_account_required:
        _add_required_json_object_errors(
            form,
            pis,
            object_key="internationalCreditorAccount",
            required_keys=("schemeName", "identification", "name"),
            field_names={
                "schemeName": "pis_international_creditor_account_scheme_name",
                "identification": "pis_international_creditor_account_identification",
                "name": "pis_international_creditor_account_name",
            },
            json_field_name="pis_international_creditor_account_json",
            json_field_supplied=(
                _cleaned_optional_string(cleaned_data.get("pis_international_creditor_account_json")) is not None
            ),
            message="International creditor account is required for selected PIS endpoints.",
        )
    if visibility.pis_instructed_amount_required:
        _add_required_json_object_errors(
            form,
            pis,
            object_key="instructedAmount",
            required_keys=("amount", "currency"),
            field_names={
                "amount": "pis_instructed_amount_amount",
                "currency": "pis_instructed_amount_currency",
            },
            json_field_name="pis_instructed_amount_json",
            json_field_supplied=_cleaned_optional_string(cleaned_data.get("pis_instructed_amount_json")) is not None,
            message="Instructed amount is required for selected PIS endpoints.",
        )
    if visibility.pis_currency_of_transfer_required:
        _add_required_string_error(
            form,
            pis,
            config_key="currencyOfTransfer",
            field_name="pis_currency_of_transfer",
            message="Currency of transfer is required for selected international PIS endpoints.",
        )
    if visibility.pis_requested_execution_date_time_required:
        _add_required_string_error(
            form,
            pis,
            config_key="requestedExecutionDateTime",
            field_name="pis_requested_execution_date_time",
            message="Requested execution date/time is required for selected scheduled PIS endpoints.",
        )
    if visibility.pis_first_payment_date_time_required:
        _add_required_string_error(
            form,
            pis,
            config_key="firstPaymentDateTime",
            field_name="pis_first_payment_date_time",
            message="First payment date/time is required for selected standing-order PIS endpoints.",
        )
    if visibility.pis_standing_order_frequency_required:
        _add_required_json_object_errors(
            form,
            pis,
            object_key="standingOrderFrequency",
            required_keys=("type", "pointInTime"),
            field_names={
                "type": "pis_standing_order_frequency_type",
                "pointInTime": "pis_standing_order_frequency_point_in_time",
            },
            json_field_name="pis_standing_order_frequency_json",
            json_field_supplied=(
                _cleaned_optional_string(cleaned_data.get("pis_standing_order_frequency_json")) is not None
            ),
            message="Standing-order frequency is required for selected standing-order PIS endpoints.",
        )


def _add_required_json_object_errors(
    form: forms.Form,
    parent: Mapping[str, JsonValue],
    *,
    object_key: str,
    required_keys: tuple[str, ...],
    field_names: Mapping[str, str],
    json_field_name: str,
    json_field_supplied: bool,
    message: str,
) -> None:
    """Add errors when a required nested JSON object is missing string fields.

    Args:
        form: Form that receives validation errors.
        parent: Parent JSON object containing the nested object.
        object_key: Nested object key to inspect.
        required_keys: Required non-blank string keys inside the nested object.
        field_names: Friendly form field names keyed by nested object key.
        json_field_name: Advanced JSON fallback form field name.
        json_field_supplied: Whether the participant submitted the JSON fallback.
        message: Participant-facing validation message.
    """
    value = parent.get(object_key)
    nested = value if isinstance(value, dict) else {}
    missing_keys = tuple(key for key in required_keys if _cleaned_optional_string(nested.get(key)) is None)
    if not missing_keys:
        return
    if json_field_supplied:
        form.add_error(json_field_name, f"{message} Missing fields: {', '.join(missing_keys)}.")
        return
    for key in missing_keys:
        form.add_error(field_names[key], message)


def _add_required_string_error(
    form: forms.Form,
    config: Mapping[str, JsonValue],
    *,
    config_key: str,
    field_name: str,
    message: str,
) -> None:
    """Add an error when a required string config value is absent.

    Args:
        form: Form that receives validation errors.
        config: Config object to inspect.
        config_key: Required config key.
        field_name: Form field receiving the error.
        message: Participant-facing validation message.
    """
    if _cleaned_optional_string(config.get(config_key)) is None:
        form.add_error(field_name, message)


def _discovery_config_from_fields(cleaned_data: Mapping[str, object]) -> JsonObject:
    """Build a discovery partial config from form fields.

    Args:
        cleaned_data: Cleaned form values.

    Returns:
        Partial v2 plan config containing discovery fields.
    """
    config: JsonObject = {}
    _set_optional_string(config, "discoveryUrl", cleaned_data.get("discovery_url"))
    return config


def _security_config_from_fields(cleaned_data: Mapping[str, object]) -> JsonObject:
    """Build an OAuth/FAPI/security partial config from form fields.

    Args:
        cleaned_data: Cleaned form values.

    Returns:
        Partial v2 plan config containing security and communication settings.

    Raises:
        ValidationError: If a JSON field is malformed.
    """
    config: JsonObject = {}
    oauth = _nested_object_from_fields(
        cleaned_data,
        {
            "clientId": "oauth_client_id",
            "redirectUri": "oauth_redirect_uri",
            "authorizationEndpoint": "oauth_authorization_endpoint",
            "issuer": "oauth_issuer",
            "tokenEndpoint": "oauth_token_endpoint",
            "responseType": "oauth_response_type",
            "requestObjectSigningAlg": "oauth_request_object_signing_alg",
        },
    )
    if oauth:
        config["oauth"] = oauth

    resource_server = _nested_object_from_fields(
        cleaned_data,
        {
            "baseUrl": "resource_server_base_url",
        },
    )
    if resource_server:
        config["resourceServer"] = resource_server

    signing = _nested_object_from_fields(
        cleaned_data,
        {
            "signingCertificatePath": "signing_certificate_path",
            "signingPrivateKeyPath": "signing_private_key_path",
            "kid": "signing_kid",
            "clientAssertionIssuer": "signing_client_assertion_issuer",
            "clientAssertionSubject": "signing_client_assertion_subject",
            "tokenEndpointAuthMethod": "signing_token_endpoint_auth_method",
        },
    )
    if signing:
        config["fapiSigning"] = signing

    tls = _nested_object_from_fields(
        cleaned_data,
        {
            "caBundlePath": "tls_ca_bundle_path",
            "clientCertificatePath": "tls_client_certificate_path",
            "clientPrivateKeyPath": "tls_client_private_key_path",
        },
    )
    if tls:
        config["tls"] = tls
    return config


def _set_object_from_fields_or_json(
    target: JsonObject,
    key: str,
    cleaned_data: Mapping[str, object],
    *,
    json_field: str,
    field_mapping: Mapping[str, str],
    label: str,
) -> None:
    """Set a nested object from friendly fields or an advanced JSON fallback.

    Args:
        target: Mutable object to update.
        key: Target nested config key.
        cleaned_data: Cleaned form values.
        json_field: Field containing advanced JSON fallback text.
        field_mapping: Mapping of nested config key to friendly form field.
        label: Human-readable JSON field label for validation messages.

    Raises:
        ValidationError: If the advanced JSON fallback is malformed.
    """
    raw_json = _cleaned_optional_string(cleaned_data.get(json_field))
    if raw_json is not None:
        target[key] = _load_json_object(raw_json, label=label)
        return
    nested = _nested_object_from_fields(cleaned_data, field_mapping)
    if nested:
        target[key] = nested


def _config_from_grouped_fields(
    cleaned_data: Mapping[str, object],
    runtime_prompts: Iterable[WizardRuntimeInputPrompt],
    config_visibility: ConfigVisibility,
) -> JsonObject:
    """Build a v2 config object from grouped form fields.

    Args:
        cleaned_data: Cleaned form values.
        runtime_prompts: Runtime prompts rendered by the config step.
        config_visibility: Scope-derived structured-field visibility.

    Returns:
        builder config object.

    Raises:
        ValidationError: If a typed runtime input value is malformed.
    """
    config: JsonObject = {}
    _set_optional_string(config, "discoveryUrl", cleaned_data.get("discovery_url"))

    oauth = _nested_object_from_fields(
        cleaned_data,
        {
            "clientId": "oauth_client_id",
            "redirectUri": "oauth_redirect_uri",
            "authorizationEndpoint": "oauth_authorization_endpoint",
            "issuer": "oauth_issuer",
            "tokenEndpoint": "oauth_token_endpoint",
            "resourceBaseUrl": "oauth_resource_base_url",
            "responseType": "oauth_response_type",
            "requestObjectSigningAlg": "oauth_request_object_signing_alg",
        },
    )
    if oauth:
        config["oauth"] = oauth

    resource_server = _nested_object_from_fields(
        cleaned_data,
        {
            "baseUrl": "resource_server_base_url",
        },
    )
    if resource_server:
        config["resourceServer"] = resource_server

    signing = _nested_object_from_fields(
        cleaned_data,
        {
            "signingCertificatePath": "signing_certificate_path",
            "signingPrivateKeyPath": "signing_private_key_path",
            "kid": "signing_kid",
            "clientAssertionIssuer": "signing_client_assertion_issuer",
            "clientAssertionSubject": "signing_client_assertion_subject",
            "tokenEndpointAuthMethod": "signing_token_endpoint_auth_method",
        },
    )
    if signing:
        config["fapiSigning"] = signing

    tls = _nested_object_from_fields(
        cleaned_data,
        {
            "caBundlePath": "tls_ca_bundle_path",
            "clientCertificatePath": "tls_client_certificate_path",
            "clientPrivateKeyPath": "tls_client_private_key_path",
        },
    )
    if tls:
        config["tls"] = tls

    if config_visibility.show_ais:
        ais = _nested_object_from_fields(
            cleaned_data,
            {
                "transactionFromDate": "ais_transaction_from_date",
                "transactionToDate": "ais_transaction_to_date",
            },
        )
        _set_optional_json_object(ais, "resourceIds", cleaned_data.get("ais_resource_ids_json"))
        if ais:
            config["ais"] = ais

    if config_visibility.show_pis:
        pis = _nested_object_from_fields(
            cleaned_data,
            {
                "currencyOfTransfer": "pis_currency_of_transfer",
                "requestedExecutionDateTime": "pis_requested_execution_date_time",
                "firstPaymentDateTime": "pis_first_payment_date_time",
            },
        )
        _set_optional_json_object(pis, "creditorAccount", cleaned_data.get("pis_creditor_account_json"))
        _set_optional_json_object(
            pis,
            "internationalCreditorAccount",
            cleaned_data.get("pis_international_creditor_account_json"),
        )
        _set_optional_json_object(pis, "instructedAmount", cleaned_data.get("pis_instructed_amount_json"))
        _set_optional_json_object(
            pis,
            "standingOrderFrequency",
            cleaned_data.get("pis_standing_order_frequency_json"),
        )
        if pis:
            config["pis"] = pis
        _set_optional_json_array(config, "conditionalProperties", cleaned_data.get("conditional_properties_json"))

    if config_visibility.show_cbpii:
        cbpii: JsonObject = {}
        _set_optional_json_object(cbpii, "debtorAccount", cleaned_data.get("cbpii_debtor_account_json"))
        if cbpii:
            config["cbpii"] = cbpii

    inputs: JsonObject = {}
    for prompt in runtime_prompts:
        value = _runtime_input_value_from_form(prompt, cleaned_data.get(prompt.name))
        if value is not None:
            inputs[prompt.input_id] = {"value": value}
    if inputs:
        config["inputs"] = inputs
    return config


def _runtime_input_value_from_form(prompt: WizardRuntimeInputPrompt, raw_value: object) -> JsonValue | None:
    """Parse one runtime input from the grouped config form.

    Args:
        prompt: Runtime prompt carrying the expected input type.
        raw_value: Submitted form value.

    Returns:
        Parsed JSON value, or ``None`` when the field is blank.

    Raises:
        ValidationError: If the value cannot be parsed as the prompt type.
    """
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None
    value = raw_value.strip()
    if prompt.input_type == "json":
        return _load_json_value(value, label=prompt.label)
    if prompt.input_type == "number":
        return _load_json_number(value, label=prompt.label)
    if prompt.input_type == "boolean":
        return _load_json_boolean(value, label=prompt.label)
    return value


def _nested_object_from_fields(
    cleaned_data: Mapping[str, object],
    field_mapping: Mapping[str, str],
) -> JsonObject:
    """Build a nested config object from optional string fields.

    Args:
        cleaned_data: Cleaned form values.
        field_mapping: Mapping of config key to form field name.

    Returns:
        JSON object containing non-empty submitted field values.
    """
    nested: JsonObject = {}
    for config_key, field_name in field_mapping.items():
        _set_optional_string(nested, config_key, cleaned_data.get(field_name))
    return nested


def _set_optional_string(target: JsonObject, key: str, raw_value: object) -> None:
    """Set ``target[key]`` when ``raw_value`` is a non-empty string.

    Args:
        target: Mutable JSON object to update.
        key: Target key to set.
        raw_value: Candidate string value from cleaned form data.
    """
    value = _cleaned_optional_string(raw_value)
    if value is not None:
        target[key] = value


def _set_optional_json_object(target: JsonObject, key: str, raw_value: object) -> None:
    """Set ``target[key]`` from a JSON object form value when supplied.

    Args:
        target: Mutable JSON object to update.
        key: Target key to set.
        raw_value: Candidate JSON object text from cleaned form data.

    Raises:
        ValidationError: If the supplied value is malformed or not an object.
    """
    value = _cleaned_optional_string(raw_value)
    if value is not None:
        target[key] = _load_json_object(value, label=key)


def _set_optional_json_array(target: JsonObject, key: str, raw_value: object) -> None:
    """Set ``target[key]`` from a JSON array form value when supplied.

    Args:
        target: Mutable JSON object to update.
        key: Target key to set.
        raw_value: Candidate JSON array text from cleaned form data.

    Raises:
        ValidationError: If the supplied value is malformed or not an array.
    """
    value = _cleaned_optional_string(raw_value)
    if value is None:
        return
    loaded = _load_json_value(value, label=key)
    if not isinstance(loaded, list):
        raise forms.ValidationError(f"{key} must be a JSON array", code="invalid_json_array")
    target[key] = loaded


def _runtime_requirements_for_boundary(boundary: PlanDocumentBoundary) -> dict[str, RuntimeInputRequirement]:
    """Return runtime requirements indexed by id for a v2 boundary.

    Args:
        boundary: User-facing v2 plan boundary.

    Returns:
        Runtime requirements from all catalogue areas backing the boundary.

    Raises:
        CatalogueError: If backing catalogue areas disagree on a requirement.
    """
    requirements: dict[str, RuntimeInputRequirement] = {}
    for catalogue in catalogue_areas_for_plan_document_boundary(boundary, supported_catalogues()):
        for test_case in catalogue.test_cases:
            for requirement in test_case.runtime_input_requirements:
                existing = requirements.get(requirement.input_id)
                if existing is None:
                    requirements[requirement.input_id] = requirement
                    continue
                if existing.input_type != requirement.input_type:
                    raise CatalogueError(
                        f"Runtime input '{requirement.input_id}' has conflicting catalogue requirements"
                    )
                requirements[requirement.input_id] = RuntimeInputRequirement(
                    input_id=existing.input_id,
                    input_type=existing.input_type,
                    label=existing.label,
                    required=existing.required or requirement.required,
                    sensitive=existing.sensitive or requirement.sensitive,
                    description=existing.description or requirement.description,
                    source=existing.source,
                )
    return requirements


def _runtime_requirements_for_test_cases(test_cases: Iterable[CatalogueTestCase]) -> dict[str, RuntimeInputRequirement]:
    """Return runtime requirements indexed by id for selected compiled cases.

    Args:
        test_cases: Catalogue test cases selected by the compiler.

    Returns:
        Runtime requirements from selected cases only.

    Raises:
        CatalogueError: If selected cases disagree on a requirement.
    """
    requirements: dict[str, RuntimeInputRequirement] = {}
    for test_case in test_cases:
        for requirement in test_case.runtime_input_requirements:
            existing = requirements.get(requirement.input_id)
            if existing is None:
                requirements[requirement.input_id] = _normalised_runtime_requirement_label(requirement)
                continue
            if existing.input_type != requirement.input_type:
                raise CatalogueError(f"Runtime input '{requirement.input_id}' has conflicting catalogue requirements")
            normalised_requirement = _normalised_runtime_requirement_label(requirement)
            requirements[requirement.input_id] = RuntimeInputRequirement(
                input_id=existing.input_id,
                input_type=existing.input_type,
                label=existing.label,
                required=existing.required or normalised_requirement.required,
                sensitive=existing.sensitive or normalised_requirement.sensitive,
                description=existing.description or normalised_requirement.description,
                source=existing.source,
            )
    return requirements


def _normalised_runtime_requirement_label(requirement: RuntimeInputRequirement) -> RuntimeInputRequirement:
    """Return ``requirement`` with shared input labels normalised for the UI.

    Args:
        requirement: Catalogue runtime requirement to display.

    Returns:
        Requirement with neutral labels for cross-domain runtime inputs.
    """
    if requirement.input_id != "resourceBaseUrl":
        return requirement
    return RuntimeInputRequirement(
        input_id=requirement.input_id,
        input_type=requirement.input_type,
        label="Resource server base URL",
        required=requirement.required,
        sensitive=requirement.sensitive,
        description=requirement.description,
        source=requirement.source,
    )


def _plan_document_with_config(document: PlanDocumentV2, config: Mapping[str, JsonValue]) -> PlanDocumentV2:
    """Return a parsed copy of a plan document with replaced config.

    Args:
        document: Existing parsed canonical test-plan document.
        config: Replacement executable config object.

    Returns:
        Parsed canonical test-plan document with runtime inputs re-derived from
        config.

    Raises:
        CatalogueError: If serialisation or parsing unexpectedly produces a
            non-canonical test-plan document.
    """
    return PlanDocumentV2(
        schema_version=document.schema_version,
        scheme=document.scheme,
        specification=document.specification,
        version=document.version,
        security_profile=document.security_profile,
        resource_groups=document.resource_groups,
        config=_copy_json_mapping(config),
        runtime_inputs=_runtime_input_values_from_config(config),
        security_environment=document.security_environment,
        business_test_data=document.business_test_data,
        metadata=document.metadata,
        execution_mode=document.execution_mode,
    )


def _merged_plan_context(
    imported_context: Mapping[str, JsonValue],
    derived_context: Mapping[str, JsonValue],
) -> JsonObject:
    """Return imported plan context updated with non-empty derived values.

    Args:
        imported_context: Canonical context preserved from imported plan JSON.
        derived_context: Context derived from the current editable config.

    Returns:
        Merged context where current form/config values override imported values
        while blank derived strings do not erase imported metadata.
    """
    merged = _copy_json_mapping(imported_context)
    for key, value in derived_context.items():
        if isinstance(value, str) and not value:
            continue
        merged[key] = _copy_json_value(value)
    return merged


def _scoped_business_plan_context(
    imported_context: Mapping[str, JsonValue],
    derived_context: Mapping[str, JsonValue],
    *,
    selected_api_ids: frozenset[str],
) -> JsonObject:
    """Return business data with stale resource-family sections removed.

    Args:
        imported_context: Canonical business data preserved from imported plan
            JSON.
        derived_context: Business data derived from the current editable config.
        selected_api_ids: API families represented by the current scope.

    Returns:
        Merged business context without resource-family keys outside the current
        selected scope.
    """
    api_keys = {"ais", "pis", "cbpii", "vrp"}
    scoped_imported = {
        key: value for key, value in imported_context.items() if key not in api_keys or key in selected_api_ids
    }
    scoped_derived = {
        key: value for key, value in derived_context.items() if key not in api_keys or key in selected_api_ids
    }
    return _merged_plan_context(scoped_imported, scoped_derived)


def _config_with_runtime_placeholders(
    config: Mapping[str, JsonValue],
    requirements: Iterable[RuntimeInputRequirement | WizardRuntimeInputPrompt],
) -> JsonObject:
    """Return config with required missing runtime inputs set to placeholders.

    Args:
        config: Original v2 config object.
        requirements: Runtime requirements or prompts to inspect.

    Returns:
        Config copy with required missing input values supplied.
    """
    updated = _copy_json_mapping(config)
    current_values = _runtime_input_values_from_config(updated)
    for requirement in requirements:
        if isinstance(requirement, RuntimeInputRequirement) and requirement.source != "plan":
            continue
        if not requirement.required:
            continue
        if _runtime_input_is_present(current_values.get(requirement.input_id)):
            continue
        _set_runtime_input_value(updated, requirement.input_id, _runtime_placeholder(requirement.input_type))
    return updated


def _set_runtime_input_value(config: JsonObject, input_id: str, value: JsonValue) -> None:
    """Set a runtime input value in the least surprising config location.

    Args:
        config: Mutable v2 config object.
        input_id: Runtime input id to set.
        value: Runtime input value.
    """
    existing = config.get(input_id)
    if existing is None or isinstance(existing, str | int | float | bool):
        config[input_id] = value
        return
    raw_inputs = config.get("inputs")
    inputs = raw_inputs if isinstance(raw_inputs, dict) else {}
    inputs[input_id] = {"value": value}
    config["inputs"] = inputs


def _runtime_placeholder(input_type: str) -> JsonValue:
    """Return a compiler-valid placeholder for a runtime input type.

    Args:
        input_type: Catalogue runtime input type.

    Returns:
        Placeholder JSON value used only for preview compilation.
    """
    if input_type == "url":
        return "https://placeholder.example.com"
    if input_type == "number":
        return 0
    if input_type == "boolean":
        return False
    if input_type == "json":
        return {}
    return "placeholder"


def _runtime_prompt_group(input_id: str, input_type: str) -> str:
    """Return the grouped-config section for a runtime prompt.

    Args:
        input_id: Runtime input id.
        input_type: Runtime input type.

    Returns:
        Participant-facing config group label.
    """
    normalized = input_id.lower()
    if normalized.startswith("xfapi") or normalized.startswith("xcustomer") or normalized == "idempotencykey":
        return "Request metadata and headers"
    if input_type == "url" or "baseurl" in normalized or normalized.endswith("url"):
        return "Resource server targets"
    return "PSU authorisation/runtime inputs"


def _runtime_input_values_from_config(config: Mapping[str, JsonValue]) -> JsonObject:
    """Derive runtime input values from a v2 config object.

    Args:
        config: Builder config object.

    Returns:
        Flat runtime-input mapping mirroring catalogue parser behaviour.
    """
    values: JsonObject = {}
    for key, value in config.items():
        if key in {"inputs", "runtimeInputs"} or isinstance(value, dict | list):
            continue
        values[key] = _copy_json_value(value)
    _merge_structured_config_runtime_values(values, config)
    raw_runtime_inputs = config.get("runtimeInputs")
    if isinstance(raw_runtime_inputs, dict):
        for input_id, value in raw_runtime_inputs.items():
            if isinstance(input_id, str):
                values[input_id] = _copy_json_value(value)
    raw_inputs = config.get("inputs")
    if isinstance(raw_inputs, dict):
        for input_id, raw_value in raw_inputs.items():
            if not isinstance(input_id, str):
                continue
            if isinstance(raw_value, dict) and "value" in raw_value:
                values[input_id] = _copy_json_value(raw_value["value"])
            else:
                values[input_id] = _copy_json_value(raw_value)
    return values


def _merge_structured_config_runtime_values(values: JsonObject, config: Mapping[str, JsonValue]) -> None:
    """Merge prompt-visible runtime values derived from structured config.

    Args:
        values: Mutable flat runtime-input mapping.
        config: Builder config object.
    """
    resource_server = _object_config_value(config, "resourceServer")
    _set_derived_runtime_value(values, "resourceBaseUrl", resource_server.get("baseUrl"))
    oauth = _object_config_value(config, "oauth")
    _set_derived_runtime_value(values, "resourceBaseUrl", oauth.get("resourceBaseUrl"))
    cbpii = _object_config_value(config, "cbpii")
    debtor_account = _object_config_value(cbpii, "debtorAccount")
    _set_derived_runtime_value(values, "debtorAccountSchemeName", debtor_account.get("schemeName"))
    _set_derived_runtime_value(values, "debtorAccountIdentification", debtor_account.get("identification"))
    _set_derived_runtime_value(values, "debtorAccountName", debtor_account.get("name"))
    vrp = _object_config_value(config, "vrp")
    vrp_creditor_account = _object_config_value(vrp, "creditorAccount")
    vrp_instructed_amount = _object_config_value(vrp, "instructedAmount")
    _set_derived_runtime_value(values, "vrpCreditorAccountSchemeName", vrp_creditor_account.get("schemeName"))
    _set_derived_runtime_value(values, "vrpCreditorAccountIdentification", vrp_creditor_account.get("identification"))
    _set_derived_runtime_value(values, "vrpCreditorAccountName", vrp_creditor_account.get("name"))
    _set_derived_runtime_value(values, "vrpInstructedAmountAmount", vrp_instructed_amount.get("amount"))
    _set_derived_runtime_value(values, "vrpInstructedAmountCurrency", vrp_instructed_amount.get("currency"))
    _set_derived_runtime_value(values, "vrpValidFromDateTime", vrp.get("validFromDateTime"))
    _set_derived_runtime_value(values, "vrpValidToDateTime", vrp.get("validToDateTime"))
    ais = _object_config_value(config, "ais")
    resource_ids = _object_config_value(ais, "resourceIds")
    _set_derived_runtime_value(
        values,
        "consentedAccountId",
        _first_form_config_object_string(resource_ids.get("accountIds"), "accountId"),
    )
    _set_derived_runtime_value(values, "fromBookingDateTime", ais.get("transactionFromDate"))
    _set_derived_runtime_value(values, "toBookingDateTime", ais.get("transactionToDate"))


def _set_derived_runtime_value(values: JsonObject, input_id: str, value: JsonValue | None) -> None:
    """Set a derived runtime value when no explicit value already exists.

    Args:
        values: Mutable flat runtime-input mapping.
        input_id: Runtime input id to set.
        value: Candidate JSON value.
    """
    if input_id in values or value is None:
        return
    if isinstance(value, str) and not value.strip():
        return
    values[input_id] = _copy_json_value(value)


def _first_form_config_object_string(value: JsonValue | None, key: str) -> str | None:
    """Return a string field from the first object in a form config array.

    Args:
        value: Candidate JSON array.
        key: Field to read from the first object.

    Returns:
        String value, or ``None`` when unavailable.
    """
    if not isinstance(value, list) or not value:
        return None
    first = value[0]
    if not isinstance(first, dict):
        return None
    raw_value = first.get(key)
    return raw_value if isinstance(raw_value, str) else None


def _runtime_input_is_present(value: JsonValue | None) -> bool:
    """Return whether a runtime input value is present for launch.

    Args:
        value: Runtime input value or ``None``.

    Returns:
        True when the value is non-null and not a blank string.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _safe_export_config(config: Mapping[str, JsonValue], sensitive_runtime_input_ids: set[str]) -> JsonObject:
    """Return a config object with secret-bearing strings emptied.

    Args:
        config: Original v2 config object.
        sensitive_runtime_input_ids: Runtime input ids marked sensitive by the
            compiler trace.

    Returns:
        Secret-safe config preserving object shape.
    """
    return {key: _safe_export_config_value(key, value, sensitive_runtime_input_ids) for key, value in config.items()}


def _safe_export_config_value(
    key: str,
    value: JsonValue,
    sensitive_runtime_input_ids: set[str],
) -> JsonValue:
    """Return one config value with secret-bearing data removed.

    Args:
        key: Config key associated with ``value``.
        value: JSON value to sanitise.
        sensitive_runtime_input_ids: Runtime input ids marked sensitive.

    Returns:
        Sanitised JSON value.
    """
    if key in sensitive_runtime_input_ids or _is_sensitive_config_key(key):
        if isinstance(value, dict) and "value" in value:
            sanitized = _copy_json_mapping(value)
            sanitized["value"] = ""
            return sanitized
        if isinstance(value, str):
            return ""
    if isinstance(value, dict):
        return _safe_export_config(value, sensitive_runtime_input_ids)
    if isinstance(value, list):
        return [_safe_export_config_value(key, item, sensitive_runtime_input_ids) for item in value]
    return _copy_json_value(value)


def _is_sensitive_config_key(key: str) -> bool:
    """Return whether a config key should be emptied in safe exports.

    Args:
        key: Config key to inspect.

    Returns:
        True when the key conventionally carries a credential or key path.
    """
    normalized = key.replace("-", "").replace("_", "").lower()
    return (
        normalized.endswith("token")
        or "secret" in normalized
        or "password" in normalized
        or "privatekey" in normalized
        or normalized in {"identification", "accountid", "statementid", "xfapicustomeripaddress", "xfapifinancialid"}
    )


def _draft_boundary_or_error(draft: BuilderDraft) -> PlanDocumentBoundary:
    """Return a draft boundary or raise a catalogue error.

    Args:
        draft: Current wizard draft.

    Returns:
        Selected scheme/specification/version boundary.

    Raises:
        CatalogueError: If the draft has not completed the boundary step.
    """
    if draft.scheme is None or draft.specification is None or draft.version is None:
        raise CatalogueError("Builder draft catalogue boundary is incomplete")
    return PlanDocumentBoundary(draft.scheme, draft.specification, draft.version)


def _load_json_object(raw_value: str, *, label: str) -> JsonObject:
    """Decode a JSON object from a form text value.

    Args:
        raw_value: JSON text submitted through the browser.
        label: Human-readable field label for validation messages.

    Returns:
        Decoded JSON object.

    Raises:
        ValidationError: If the value is malformed JSON or not an object.
    """
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError as error:
        raise forms.ValidationError(f"{label} must be valid JSON: {error.msg}", code="invalid_json") from error
    if not isinstance(value, dict):
        raise forms.ValidationError(f"{label} must be a JSON object", code="invalid_json_object")
    return cast(JsonObject, value)


def _load_json_value(raw_value: str, *, label: str) -> JsonValue:
    """Decode a JSON runtime input value.

    Args:
        raw_value: JSON text submitted for a runtime input.
        label: Human-readable prompt label.

    Returns:
        Decoded JSON value.

    Raises:
        ValidationError: If the value is malformed JSON.
    """
    try:
        return cast(JsonValue, json.loads(raw_value))
    except json.JSONDecodeError as error:
        raise forms.ValidationError(f"{label} must be valid JSON: {error.msg}", code="invalid_json") from error


def _load_json_number(raw_value: str, *, label: str) -> int | float:
    """Decode a JSON number runtime input value.

    Args:
        raw_value: Submitted number text.
        label: Human-readable prompt label.

    Returns:
        Parsed integer or floating-point number.

    Raises:
        ValidationError: If the submitted value is not a JSON number.
    """
    value = _load_json_value(raw_value, label=label)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise forms.ValidationError(f"{label} must be a JSON number", code="invalid_number")
    return value


def _load_json_boolean(raw_value: str, *, label: str) -> bool:
    """Decode a boolean runtime input value.

    Args:
        raw_value: Submitted boolean text.
        label: Human-readable prompt label.

    Returns:
        Parsed boolean value.

    Raises:
        ValidationError: If the submitted value is not ``true`` or ``false``.
    """
    normalized = raw_value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise forms.ValidationError(f"{label} must be true or false", code="invalid_boolean")


def _cleaned_optional_string(value: object) -> str | None:
    """Return a stripped non-empty string from form data.

    Args:
        value: Raw or cleaned form value.

    Returns:
        Stripped string, or ``None`` when absent.
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _string_config_value(config: Mapping[str, JsonValue], key: str) -> str:
    """Return a string config value for form initial data.

    Args:
        config: Config mapping to inspect.
        key: Config key to read.

    Returns:
        String value, or an empty string when absent/non-string.
    """
    value = config.get(key)
    return value if isinstance(value, str) else ""


def _scalar_config_value(config: Mapping[str, JsonValue], key: str) -> str:
    """Return a scalar config value as a form string.

    Args:
        config: Config mapping to inspect.
        key: Config key to read.

    Returns:
        String representation for JSON scalar values, or an empty string.
    """
    value = config.get(key)
    if value is None or isinstance(value, dict | list):
        return ""
    return str(value)


def _boolean_config_value(config: Mapping[str, JsonValue], key: str) -> bool:
    """Return a boolean config value for form initial data.

    Args:
        config: Config mapping to inspect.
        key: Config key to read.

    Returns:
        Boolean value, or ``False`` when absent/non-boolean.
    """
    value = config.get(key)
    return value if isinstance(value, bool) else False


def _object_config_value(config: Mapping[str, JsonValue], key: str) -> Mapping[str, JsonValue]:
    """Return a nested config object.

    Args:
        config: Config mapping to inspect.
        key: Config key to read.

    Returns:
        Nested JSON object, or an empty mapping.
    """
    value = config.get(key)
    return value if isinstance(value, dict) else {}


def _config_or_discovery_string(
    config: Mapping[str, JsonValue],
    config_key: str,
    discovery_metadata: Mapping[str, JsonValue],
    metadata_key: str,
) -> str:
    """Return a config value, falling back to a discovery metadata value.

    Args:
        config: Config section to inspect first.
        config_key: Config key to read.
        discovery_metadata: Session-only discovery metadata object.
        metadata_key: Discovery metadata key to read when config is blank.

    Returns:
        Config string, discovery metadata string, or an empty string.
    """
    value = _string_config_value(config, config_key)
    if value:
        return value
    metadata_value = discovery_metadata.get(metadata_key)
    return metadata_value if isinstance(metadata_value, str) else ""


def _json_config_value(config: Mapping[str, JsonValue], key: str) -> str:
    """Return a nested JSON value for form initial data.

    Args:
        config: Config mapping to inspect.
        key: Config key to read.

    Returns:
        JSON display string, or an empty string when absent.
    """
    return _display_json_value(config.get(key))


def _single_discovery_list_value(discovery_metadata: Mapping[str, JsonValue], key: str) -> str:
    """Return the sole string value in a discovery metadata list.

    Args:
        discovery_metadata: Session-only discovery metadata object.
        key: Metadata list key to inspect.

    Returns:
        Sole string value when the list has one entry, otherwise an empty
        string.
    """
    value = discovery_metadata.get(key)
    if not isinstance(value, list) or len(value) != 1:
        return ""
    first = value[0]
    return first if isinstance(first, str) else ""


def _discovery_list_json(discovery_metadata: Mapping[str, JsonValue], key: str) -> str:
    """Return a JSON display value for a discovery metadata list.

    Args:
        discovery_metadata: Session-only discovery metadata object.
        key: Metadata list key to inspect.

    Returns:
        JSON array string, or an empty string when metadata is unavailable.
    """
    value = discovery_metadata.get(key)
    if not isinstance(value, list):
        return ""
    return _display_json_value(value)


def _display_json_value(value: JsonValue | None) -> str:
    """Return a form-display string for a JSON value.

    Args:
        value: JSON value to display.

    Returns:
        String suitable for an input value.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(value, sort_keys=True)


def _copy_json_mapping(value: Mapping[str, JsonValue]) -> JsonObject:
    """Return a deep copy of a JSON mapping.

    Args:
        value: JSON object mapping to copy.

    Returns:
        Independent JSON object.
    """
    return {key: _copy_json_value(item) for key, item in value.items()}


def _copy_json_value(value: JsonValue) -> JsonValue:
    """Return a deep copy of a JSON value.

    Args:
        value: JSON value to copy.

    Returns:
        Independent JSON value.
    """
    if isinstance(value, dict):
        return {key: _copy_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_json_value(item) for item in value]
    return value


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
        Mapping of endpoint option id to selected capability ids.

    Raises:
        ValidationError: If ``strict`` is true and a submitted value is
            malformed.
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


def _raw_or_initial_values(
    data: Mapping[str, object] | None,
    initial: Mapping[str, object] | None,
    key: str,
) -> tuple[str, ...]:
    """Return repeated form values from bound data or initial state.

    Args:
        data: Optional bound form data.
        initial: Optional initial state for unbound forms.
        key: Form field key to read.

    Returns:
        Tuple of string values.
    """
    if data is not None:
        return _raw_values(data, key)
    if initial is None:
        return ()
    return _cleaned_string_tuple(initial.get(key))


def _raw_values(data: Mapping[str, object], key: str) -> tuple[str, ...]:
    """Return repeated string values from raw form data.

    Args:
        data: Raw form data.
        key: Field name to read.

    Returns:
        Tuple of submitted string values.
    """
    if hasattr(data, "getlist"):
        values = data.getlist(key)
        return tuple(value for value in values if isinstance(value, str))
    value = data.get(key)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    return (value,) if isinstance(value, str) and value else ()


def _cleaned_string_tuple(value: object) -> tuple[str, ...]:
    """Return a tuple of strings from cleaned or initial form values.

    Args:
        value: Raw value read from cleaned data or initial state.

    Returns:
        Tuple of string values, or an empty tuple for absent values.
    """
    if value is None:
        return ()
    if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    return ()


def _endpoint_id(api: str | None, endpoint_ref: EndpointRef) -> str:
    """Return the stable wizard id for an API-family endpoint reference.

    Args:
        api: Catalogue API family that owns the endpoint, or ``None`` when
            importing a non-builder resource-group id.
        endpoint_ref: Endpoint method/path reference.

    Returns:
        Stable endpoint id derived from API family, method, and path.
    """
    digest = sha256(f"{api or ''} {endpoint_ref.method} {endpoint_ref.path}".encode()).hexdigest()[:12]
    return f"endpoint-{digest}"


def _legacy_endpoint_id(endpoint_ref: EndpointRef) -> str:
    """Return the pre-family-scoped wizard id for an endpoint reference.

    Args:
        endpoint_ref: Endpoint method/path reference.

    Returns:
        Legacy endpoint id derived from method and path only.
    """
    digest = sha256(f"{endpoint_ref.method} {endpoint_ref.path}".encode()).hexdigest()[:12]
    return f"endpoint-{digest}"


def _endpoint_id_for_resource_group_endpoint(
    document: PlanDocumentV2,
    *,
    resource_group_id: str,
    endpoint_ref: EndpointRef,
) -> str:
    """Return the wizard endpoint id for an imported v2 endpoint.

    Args:
        document: Imported canonical test-plan document carrying the selected
            boundary.
        resource_group_id: Resource-group id from the imported document.
        endpoint_ref: Endpoint reference nested under the resource group.

    Returns:
        Stable endpoint option id for the wizard draft.
    """
    api = _api_from_resource_group_id(resource_group_id)
    if api is not None:
        return _endpoint_id(api, endpoint_ref)

    boundary = PlanDocumentBoundary(
        scheme=document.scheme,
        specification=document.specification,
        version=document.version,
    )
    hierarchy = catalogue_scope_hierarchy(boundary, selected_resource_group_ids=(resource_group_id,))
    for group in hierarchy.resource_groups:
        if not group.selected:
            continue
        for endpoint in group.endpoints:
            if endpoint.method == endpoint_ref.method and endpoint.path == endpoint_ref.path:
                return endpoint.id
    return _legacy_endpoint_id(endpoint_ref)


def _selected_endpoint_api_ids(document: PlanDocumentV2) -> frozenset[str]:
    """Return API families represented by selected plan endpoints.

    Args:
        document: Parsed canonical test-plan document.

    Returns:
        Internal catalogue API-family ids for selected endpoints.
    """
    api_ids: set[str] = set()
    for resource_group in document.resource_groups:
        group_api = _api_from_resource_group_id(resource_group.resource_group_id)
        if resource_group.select_all and group_api is not None:
            api_ids.add(group_api)
        for endpoint in resource_group.endpoints:
            endpoint_api = _api_from_endpoint_path(endpoint.path)
            if endpoint_api is not None:
                api_ids.add(endpoint_api)
            elif group_api is not None:
                api_ids.add(group_api)
    return frozenset(api_ids)


def _api_from_endpoint_path(path: str) -> str | None:
    """Infer an API family from an Open Banking endpoint path.

    Args:
        path: Standards endpoint path.

    Returns:
        Internal API-family id when the path contains a known API segment.
    """
    segments = {segment for segment in path.split("/") if segment}
    for api, api_segment in _API_PATH_SEGMENTS.items():
        if api_segment in segments:
            return api
    return None


def _api_from_resource_group_id(resource_group_id: str) -> str | None:
    """Return the API-family prefix from a builder resource-group id.

    Args:
        resource_group_id: Resource-group id from a canonical test-plan document.

    Returns:
        API-family id when the resource group is canonical, high-level, or uses
        the legacy builder ``api.slug`` shape; otherwise ``None``.
    """
    canonical_lookup = {"AIS": "ais", "PIS": "pis", "CBPII": "cbpii", "VRP": "vrp"}
    if resource_group_id in canonical_lookup:
        return canonical_lookup[resource_group_id]
    high_level_lookup = {metadata.group_id: api for api, metadata in _RESOURCE_GROUP_METADATA_BY_API.items()}
    high_level_api = high_level_lookup.get(resource_group_id)
    if high_level_api is not None:
        return high_level_api
    api, separator, _slug_value = resource_group_id.partition(".")
    return api if separator and api else None


def _normalized_resource_group_ids(
    resource_group_ids: Iterable[str],
    *,
    catalogues: Iterable[TestCatalogue],
) -> set[str]:
    """Return high-level builder ids for selected resource groups.

    Args:
        resource_group_ids: Submitted or imported resource-group ids.
        catalogues: Catalogue areas available for the selected plan boundary.

    Returns:
        Resource-group ids normalised to the current high-level builder groups
        where a selected id can be associated with an available API family.
    """
    available_apis = {catalogue.key.api for catalogue in catalogues}
    normalized: set[str] = set()
    for resource_group_id in resource_group_ids:
        api = _api_from_resource_group_id(resource_group_id)
        if api in available_apis:
            normalized.add(_resource_group_id(api))
        else:
            normalized.add(resource_group_id)
    return normalized


def _normalized_resource_group_ids_for_hierarchy(
    resource_group_ids: Iterable[str],
    *,
    hierarchy: CatalogueScopeHierarchy,
) -> set[str]:
    """Return submitted resource-group ids normalised against a hierarchy.

    Args:
        resource_group_ids: Submitted or imported resource-group ids.
        hierarchy: Current high-level scope hierarchy.

    Returns:
        Resource-group ids mapped from legacy ``api.slug`` groups to the
        matching high-level group id where possible.
    """
    group_ids_by_api = {group.api: group.id for group in hierarchy.resource_groups}
    normalized: set[str] = set()
    for resource_group_id in resource_group_ids:
        api = _api_from_resource_group_id(resource_group_id)
        if api in group_ids_by_api:
            normalized.add(group_ids_by_api[api])
        else:
            normalized.add(resource_group_id)
    return normalized


def _resource_group_id(api: str) -> str:
    """Return the current high-level resource-group id for an API family.

    Args:
        api: Internal catalogue API-family id.

    Returns:
        Stable resource-group id, for example
        ``"account-and-transaction"``.
    """
    metadata = _RESOURCE_GROUP_METADATA_BY_API.get(api)
    if metadata is not None:
        return metadata.group_id
    return f"{api}.{_slug(_api_label(api))}"


def _resource_group_label(api: str) -> str:
    """Return the current high-level resource-group label for an API family.

    Args:
        api: Internal catalogue API-family id.

    Returns:
        Human-readable resource area label.
    """
    metadata = _RESOURCE_GROUP_METADATA_BY_API.get(api)
    return metadata.label if metadata is not None else _api_label(api)


def _endpoint_family_label(api: str, path: str) -> str:
    """Infer a lower-level endpoint-family label from an endpoint path.

    Args:
        api: Internal catalogue API-family id.
        path: Standards endpoint path.

    Returns:
        Human-readable endpoint-family label used for endpoint ordering.
    """
    segments = [segment for segment in path.split("/") if segment and not segment.startswith("{")]
    api_segment = _API_PATH_SEGMENTS.get(api)
    if api_segment in segments:
        index = segments.index(api_segment)
        if index + 1 < len(segments):
            return _title_segment(segments[index + 1])
    if segments:
        return _title_segment(segments[0])
    return _api_label(api)


def _endpoint_display_path(path: str) -> str:
    """Return a concise endpoint path for participant-facing builder labels.

    Args:
        path: Standards endpoint path from the catalogue.

    Returns:
        Path with the known Open Banking version prefix removed, or the original
        path when it does not match that prefix shape.
    """
    segments = [segment for segment in path.split("/") if segment]
    version_segment = segments[1] if len(segments) > 1 else ""
    if (
        len(segments) >= 3
        and segments[0] == "open-banking"
        and version_segment.startswith("v")
        and len(version_segment) > 1
        and version_segment[1].isdigit()
    ):
        return f"/{'/'.join(segments[2:])}"
    return path


def _operation_id(api: str, endpoint_ref: EndpointRef) -> str:
    """Build a stable operation id for later v2 plan exports.

    Args:
        api: Internal catalogue API-family id.
        endpoint_ref: Endpoint method/path reference.

    Returns:
        Operation id derived from API, method, and standards path.
    """
    suffix = endpoint_ref.path.strip("/").replace("{", "").replace("}", "").replace("/", "-")
    return f"{api}-{endpoint_ref.method.lower()}-{suffix}".replace("--", "-")


def _api_label(api: str) -> str:
    """Return a participant-facing label for an API-family id.

    Args:
        api: Internal catalogue API-family id.

    Returns:
        Display label for the API family.
    """
    return _API_LABELS.get(api, api.upper())


def _title_segment(value: str) -> str:
    """Convert a standards path segment into a display label.

    Args:
        value: Raw path segment.

    Returns:
        Title-cased display label.
    """
    return " ".join(word.capitalize() for word in value.split("-"))


def _slug(value: str) -> str:
    """Convert a display label into a stable lowercase slug.

    Args:
        value: Display label to normalise.

    Returns:
        Lowercase alphanumeric slug with hyphen separators.
    """
    chars: list[str] = []
    previous_was_separator = False
    for character in value.lower():
        if character.isalnum():
            chars.append(character)
            previous_was_separator = False
        elif not previous_was_separator and chars:
            chars.append("-")
            previous_was_separator = True
    return "".join(chars).strip("-") or "resource-group"


def _effective_initial(
    initial: Mapping[str, object] | None,
    *,
    boundaries: tuple[PlanDocumentBoundary, ...],
) -> dict[str, object]:
    """Build initial selector values from a draft or first supported boundary.

    Args:
        initial: Optional initial values supplied by the caller.
        boundaries: Compile-ready boundary choices available to the form.

    Returns:
        Initial form values with sensible defaults when no draft value exists.
    """
    effective: dict[str, object] = dict(initial or {})
    if boundaries:
        first_boundary = boundaries[0]
        effective.setdefault("scheme", first_boundary.scheme)
        effective.setdefault("specification", first_boundary.specification)
        effective.setdefault("version", first_boundary.version)
    return effective


def _boundary_from_form_values(
    data: Mapping[str, object] | None,
    initial: Mapping[str, object],
    *,
    boundaries: tuple[PlanDocumentBoundary, ...],
) -> PlanDocumentBoundary:
    """Return the boundary currently selected by raw form values.

    Args:
        data: Optional bound form data.
        initial: Effective initial form values.
        boundaries: Compile-ready boundary choices available to the form.

    Returns:
        Matching boundary from the submitted or initial values, falling back to
        the first supported boundary when the current values are incomplete or
        invalid.

    Raises:
        CatalogueError: If no compile-ready boundary exists.
    """
    if not boundaries:
        raise CatalogueError("No catalogue boundaries are available")
    scheme = _first_raw_or_initial_value(data, initial, "scheme")
    specification = _first_raw_or_initial_value(data, initial, "specification")
    version = _first_raw_or_initial_value(data, initial, "version")
    selected = PlanDocumentBoundary(scheme=scheme, specification=specification, version=version)
    return selected if selected in boundaries else boundaries[0]


def _first_raw_or_initial_value(data: Mapping[str, object] | None, initial: Mapping[str, object], key: str) -> str:
    """Return the first raw form value or initial string for ``key``.

    Args:
        data: Optional bound form data.
        initial: Effective initial form values.
        key: Form field key to read.

    Returns:
        Submitted or initial string value, or an empty string.
    """
    values = _raw_values(data, key) if data is not None else ()
    if values:
        return values[0]
    value = initial.get(key)
    return value if isinstance(value, str) else ""


def _scheme_label(value: str) -> str:
    """Return a participant-facing label for a scheme value.

    Args:
        value: Scheme identifier.

    Returns:
        Display label for the scheme.
    """
    return _SCHEME_LABELS.get(value, value)


def _specification_label(value: str) -> str:
    """Return a participant-facing label for a specification value.

    Args:
        value: Specification identifier.

    Returns:
        Display label for the specification.
    """
    return _SPECIFICATION_LABELS.get(value, value)
