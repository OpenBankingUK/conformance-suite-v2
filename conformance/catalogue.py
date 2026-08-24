"""Catalogue-domain model and compiler for endpoint-selected conformance plans."""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import ClassVar, Literal, cast

from conformance.json_types import JsonObject, JsonValue
from conformance.url_validation import HttpsUrlValidationError, validate_https_url


class CatalogueError(ValueError):
    """Raised when a catalogue, plan spec, or compilation request is invalid."""


type SecurityProfile = Literal["all", "fapi1-advanced", "fapi2"]
"""Security profile selectors supported by catalogue applicability rules."""

type PlanExecutionMode = Literal["certification", "development"]
"""Execution modes supported by JSON-first Open Banking test plans."""

type HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
"""HTTP methods accepted for implemented endpoints and catalogue request steps."""

type RuntimeInputType = Literal["string", "number", "boolean", "json", "url", "file_reference"]
"""Runtime input value types accepted by catalogue test cases."""

type TestCaseRole = Literal["setup", "security", "resource", "consent", "token"]
"""Execution/compliance role assigned to catalogue test cases."""

type AssertionKind = Literal["http_status", "json_field", "header", "response_schema"]
"""Assertion families supported by the catalogue foundation model."""

_SUPPORTED_SECURITY_PROFILES: set[str] = {"all", "fapi1-advanced", "fapi2"}
"""Security profile names accepted at plan-spec parse time."""

_SUPPORTED_HTTP_METHODS: set[str] = {"GET", "POST", "PUT", "PATCH", "DELETE"}
"""HTTP methods accepted at plan-spec parse time."""

_SUPPORTED_RUNTIME_INPUT_TYPES: set[str] = {"string", "number", "boolean", "json", "url", "file_reference"}
"""Runtime input type names accepted in catalogue test cases."""

_V2_OPEN_BANKING_UK_SCHEME = "open-banking-uk"
"""User-facing v2 scheme identifier for Open Banking UK catalogues."""

_V2_READ_WRITE_SPECIFICATION = "read-write"
"""User-facing v2 specification identifier for Open Banking Read/Write."""

_V2_OPEN_BANKING_INTERNAL_STANDARD = "open-banking"
"""Internal catalogue standard name backing the Open Banking UK v2 scheme."""

_V2_READ_WRITE_API_FAMILIES = ("ais", "pis", "cbpii", "vrp")
"""Open Banking catalogue API families that can contribute to one Read/Write v2 plan."""

_V2_READ_WRITE_VERSION_MAP = {"4.0": "v4.0", "4.0.0": "v4.0", "4.0.1": "v4.0"}
"""Map user-facing Read/Write versions to bundled catalogue versions."""

_V2_READ_WRITE_DISPLAY_VERSIONS = ("4.0.1", "4.0.0", "4.0")
"""Preferred display order for compile-ready Read/Write v2 versions."""

_CANONICAL_PLAN_SCHEMA_VERSION = "1.0"
"""Current JSON-first test-plan schema version."""

_CANONICAL_OBL_READ_WRITE_FAMILY = "OBL_READ_WRITE"
"""Canonical PRD family value for Open Banking UK Read/Write plans."""

_CANONICAL_RESOURCE_GROUPS_BY_API = {
    "ais": ("AIS", "account-and-transaction", "Account and Transaction"),
    "pis": ("PIS", "payment-initiation", "Payment Initiation"),
    "cbpii": ("CBPII", "confirmation-of-funds", "Confirmation of Funds"),
    "vrp": ("VRP", "variable-recurring-payments", "Variable Recurring Payments"),
}
"""Canonical, builder, and display identifiers for Open Banking resource groups."""

_RESOURCE_GROUP_API_BY_CANONICAL_ID = {
    canonical_id: api for api, (canonical_id, _builder_id, _label) in _CANONICAL_RESOURCE_GROUPS_BY_API.items()
}
"""Lookup from canonical resource group ids to internal catalogue API families."""

_RESOURCE_GROUP_API_BY_BUILDER_ID = {
    builder_id: api for api, (_canonical_id, builder_id, _label) in _CANONICAL_RESOURCE_GROUPS_BY_API.items()
}
"""Lookup from builder resource group ids to internal catalogue API families."""

_SUPPORTED_PLAN_EXECUTION_MODES = {"certification", "development"}
"""Execution modes accepted by canonical JSON test plans."""

_MODEL_BANK_CONFIG_KEYS = {
    "discoveryUrl",
    "timeoutSeconds",
    "followUp",
    "tls",
    "fapiSigning",
    "resultOutputPath",
    "executionLogPath",
    "approvedReleasePolicyPath",
    "oauth",
    "resourceServer",
    "clientCredentials",
    "openBanking",
    "ais",
    "pis",
    "cbpii",
    "conditionalProperties",
}
"""Executable model-bank config keys derivable from a canonical test plan."""


@dataclass(frozen=True)
class CatalogueKey:
    """Canonical catalogue boundary selected before endpoint applicability.

    Attributes:
        standard: Standards namespace, for example ``"open-banking"``.
        version: Standards version, for example ``"v4.0"``.
        api: API family inside the standard/version boundary, for example
            ``"ais"`` or ``"pis"``.
    """

    standard: str
    version: str
    api: str


@dataclass(frozen=True)
class EndpointRef:
    """Exact HTTP operation reference used for endpoint applicability.

    Attributes:
        method: HTTP method for the implemented operation.
        path: Absolute standards path for the implemented operation.
    """

    method: HttpMethod
    path: str


@dataclass(frozen=True)
class ImplementedEndpoint:
    """Participant-declared implemented endpoint grouped for UI presentation.

    Attributes:
        method: HTTP method implemented by the participant.
        path: Absolute standards path implemented by the participant.
        resource_group: Human-readable resource group, such as ``"Accounts"``.
        operation_id: Optional standards/OpenAPI operation identifier.
        capability_ids: Catalogue-owned implementation capabilities selected
            for this endpoint. Required capabilities may be omitted by the
            input plan spec and are normalised during compilation.
    """

    method: HttpMethod
    path: str
    resource_group: str
    operation_id: str | None = None
    capability_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SecurityProfileApplicability:
    """Security profile filter carried by catalogue test cases.

    Attributes:
        profiles: Accepted security profiles. The special ``"all"`` profile
            means the test case applies to every selected security profile.
    """

    profiles: tuple[SecurityProfile, ...]

    def applies_to(self, profile: SecurityProfile) -> bool:
        """Return whether this applicability rule matches ``profile``.

        Args:
            profile: Security profile selected by the test-plan spec.

        Returns:
            True when the rule includes ``"all"`` or the selected profile.
        """
        return "all" in self.profiles or profile in self.profiles


@dataclass(frozen=True)
class TestCaseApplicability:
    """Applicability rules for a catalogue test case.

    Attributes:
        security_profiles: Profile filter for the test case.
        endpoint_refs: Endpoint operations that make this test case applicable.
            An empty tuple means the case applies whenever the profile matches,
            which is useful for setup/security prerequisites.
        required_capability_ids: Endpoint capability ids that must be selected
            before this case becomes directly applicable. Required catalogue
            capabilities are selected automatically for implemented endpoints;
            optional capabilities only apply when the participant declares them.
    """

    # Class starts with "Test" but is production code, not a pytest collection target.
    __test__: ClassVar[bool] = False

    security_profiles: SecurityProfileApplicability
    endpoint_refs: tuple[EndpointRef, ...] = ()
    required_capability_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EndpointCapability:
    """Catalogue-owned implementation capability scoped to endpoint operations.

    Attributes:
        capability_id: Stable domain identifier for the capability.
        label: Human-readable label for builder and preview surfaces.
        description: Explanation of what implementation feature this declares.
        required: Whether the capability is baseline endpoint coverage and is
            implicitly selected whenever an applicable endpoint is implemented.
        endpoint_refs: Endpoint operations this capability can be declared on.
    """

    capability_id: str
    label: str
    description: str
    required: bool
    endpoint_refs: tuple[EndpointRef, ...]


@dataclass(frozen=True)
class RuntimeInputRequirement:
    """Runtime data required to execute a catalogue test case.

    Attributes:
        input_id: Stable identifier referenced by request steps.
        input_type: Expected runtime value type.
        label: Human-readable label suitable for UI prompts.
        required: Whether compilation must fail when the value is absent.
        sensitive: Whether the value must be omitted from trace snapshots.
    """

    input_id: str
    input_type: RuntimeInputType
    label: str
    required: bool = True
    sensitive: bool = False


@dataclass(frozen=True)
class CatalogueRequestStep:
    """Executable request-step skeleton owned by a catalogue test case.

    Attributes:
        step_id: Stable request-step identifier unique within the test case.
        name: Human-readable request-step name.
        method: HTTP method to execute.
        path: Standards path to resolve against participant runtime config.
        runtime_input_refs: Runtime input identifiers consumed by this step.
    """

    step_id: str
    name: str
    method: HttpMethod
    path: str
    runtime_input_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class CatalogueAssertion:
    """Locked normative assertion attached to a catalogue test case.

    Attributes:
        assertion_id: Stable assertion identifier unique within the test case.
        kind: Assertion family used by the future executor.
        description: Human-readable assertion summary.
        rule: JSON-serialisable assertion rule payload.
        locked: Whether this assertion is catalogue-owned and certifying.
    """

    assertion_id: str
    kind: AssertionKind
    description: str
    rule: Mapping[str, JsonValue]
    locked: bool = True


@dataclass(frozen=True)
class CatalogueTestCase:
    """Canonical conformance test case selected by the compiler.

    Attributes:
        test_case_id: Stable catalogue test case identifier.
        name: Human-readable test case name.
        role: Execution/compliance role for scheduling and evidence.
        compliance_scope: Standards clauses or coverage labels traced to this case.
        applicability: Profile and endpoint applicability rules.
        mandatory: Whether an applicable case is required for certification.
        dependencies: Other test case ids that must be compiled before this case.
        runtime_input_requirements: Runtime inputs needed by this case.
        request_steps: Executable request skeletons owned by this case.
        assertions: Locked catalogue assertions evaluated for this case.
        response_signature_required: Whether this case requires response
            ``x-jws-signature`` validation for legacy FCS parity.
    """

    test_case_id: str
    name: str
    role: TestCaseRole
    compliance_scope: tuple[str, ...]
    applicability: TestCaseApplicability
    mandatory: bool
    dependencies: tuple[str, ...] = ()
    runtime_input_requirements: tuple[RuntimeInputRequirement, ...] = ()
    request_steps: tuple[CatalogueRequestStep, ...] = ()
    assertions: tuple[CatalogueAssertion, ...] = ()
    response_signature_required: bool = False


@dataclass(frozen=True)
class TestCatalogue:
    """Versioned catalogue for one standard/version/API boundary.

    Attributes:
        key: Boundary selected by a plan spec.
        catalogue_version: Version of the catalogue content, independent of
            the standards version.
        test_cases: Ordered canonical test cases. Catalogue order is used as a
            deterministic tie-breaker after dependency ordering.
        capabilities: Endpoint-scoped implementation capabilities exposed by
            the catalogue for participant plan-spec declarations.
    """

    # Class starts with "Test" but is production code, not a pytest collection target.
    __test__: ClassVar[bool] = False

    key: CatalogueKey
    catalogue_version: str
    test_cases: tuple[CatalogueTestCase, ...]
    capabilities: tuple[EndpointCapability, ...] = ()


@dataclass(frozen=True)
class AssertionOverride:
    """Participant assertion override request that makes a plan non-certifying.

    Attributes:
        test_case_id: Compiled test case containing the overridden assertion.
        assertion_id: Assertion identifier overridden by the participant.
        reason: Human-readable justification supplied by the participant.
    """

    test_case_id: str
    assertion_id: str
    reason: str


@dataclass(frozen=True)
class TestPlanSpec:
    """Participant-authored, exportable input to the catalogue compiler.

    Attributes:
        schema_version: Plan-spec schema version.
        catalogue_key: Standard/version/API catalogue to compile against.
        security_profile: Selected security profile for applicability.
        implemented_endpoints: Exact endpoint operations implemented by the
            participant.
        runtime_inputs: Runtime values or references keyed by
            :class:`RuntimeInputRequirement.input_id`.
        deselected_test_case_ids: Optional non-mandatory applicable cases the
            participant chose not to run. Mandatory applicable cases cannot be
            deselected.
        assertion_overrides: Assertion override declarations. Any override
            makes the compiled plan non-certifying.
    """

    # Class starts with "Test" but is production code, not a pytest collection target.
    __test__: ClassVar[bool] = False

    schema_version: Literal["v1"]
    catalogue_key: CatalogueKey
    security_profile: SecurityProfile
    implemented_endpoints: tuple[ImplementedEndpoint, ...]
    runtime_inputs: Mapping[str, JsonValue]
    deselected_test_case_ids: tuple[str, ...] = ()
    assertion_overrides: tuple[AssertionOverride, ...] = ()


@dataclass(frozen=True)
class PlanDocumentEndpoint:
    """Endpoint selection nested inside a v2 shared plan document.

    Attributes:
        method: HTTP method implemented by the participant.
        path: Absolute standards path implemented by the participant.
        operation_id: Optional standards/OpenAPI operation identifier.
        capability_ids: Endpoint-scoped capabilities declared for this
            endpoint in the containing resource-group context.
    """

    method: HttpMethod
    path: str
    operation_id: str | None = None
    capability_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanResourceGroup:
    """Resource-group scope nested inside a v2 shared plan document.

    Attributes:
        resource_group_id: Stable user-facing resource-group identifier.
        label: Optional display label imported from a UI-built plan.
        endpoints: Endpoint selections declared inside this resource group.
        select_all: Whether this group was declared by canonical shorthand and
            should expand to every catalogue endpoint for the group.
    """

    resource_group_id: str
    label: str | None
    endpoints: tuple[PlanDocumentEndpoint, ...]
    select_all: bool = False


@dataclass(frozen=True)
class PlanDocumentV2:
    """Shared JSON-first plan document used by UI import/export, API, and CLI.

    Attributes:
        schema_version: Shared plan-document schema version.
        scheme: User-facing standards scheme, for example
            ``"open-banking-uk"``.
        specification: User-facing specification, for example
            ``"read-write"``.
        version: User-facing specification version, for example ``"4.0.1"``.
        security_profile: Security profile selected for catalogue
            applicability.
        resource_groups: Selected resource groups with nested endpoints and
            endpoint-scoped capabilities.
        config: Raw execution/config section preserved for import/export.
        runtime_inputs: Runtime values derived from ``config`` for catalogue
            compilation and execution.
        security_environment: Canonical security environment section from the
            PRD document.
        business_test_data: Canonical resource-group-specific business data
            section from the PRD document.
        metadata: Optional participant/export metadata, such as ASPSP and brand.
        execution_mode: Whether the plan is intended for strict certification
            execution or relaxed development/debug execution.
    """

    # Class starts with "Plan" and is production code, but keep pytest explicit.
    __test__: ClassVar[bool] = False

    schema_version: str
    scheme: str
    specification: str
    version: str
    security_profile: SecurityProfile
    resource_groups: tuple[PlanResourceGroup, ...]
    config: Mapping[str, JsonValue]
    runtime_inputs: Mapping[str, JsonValue]
    security_environment: Mapping[str, JsonValue] = field(default_factory=lambda: MappingProxyType({}))
    business_test_data: Mapping[str, JsonValue] = field(default_factory=lambda: MappingProxyType({}))
    metadata: Mapping[str, JsonValue] = field(default_factory=lambda: MappingProxyType({}))
    execution_mode: PlanExecutionMode = "certification"


@dataclass(frozen=True)
class PlanDocumentBoundary:
    """User-facing v2 plan-document catalogue boundary.

    Attributes:
        scheme: Standards scheme selected by a participant.
        specification: Standards specification family selected under the
            scheme.
        version: Specification version selected under the specification
            family.
    """

    scheme: str
    specification: str
    version: str


type ParsedPlanDocument = TestPlanSpec | PlanDocumentV2
"""Parsed shared plan input accepted by API and CLI compilation paths."""


@dataclass(frozen=True)
class RuntimeInputTrace:
    """Trace-safe runtime input snapshot for a compiled plan.

    Attributes:
        input_id: Runtime input identifier.
        input_type: Expected value type from the catalogue requirement.
        required: Whether the value was required for compilation.
        sensitive: Whether the value was omitted from the trace.
        provided: Whether the plan spec supplied the value.
        value: Deep-copied non-sensitive value, or ``None`` for sensitive or
            absent values.
    """

    input_id: str
    input_type: RuntimeInputType
    required: bool
    sensitive: bool
    provided: bool
    value: JsonValue | None


@dataclass(frozen=True)
class EndpointCapabilitySelection:
    """Normalised endpoint capability selected for a compiled plan.

    Attributes:
        method: Endpoint HTTP method the capability is selected for.
        path: Endpoint standards path the capability is selected for.
        capability_id: Stable catalogue capability identifier.
        label: Human-readable catalogue capability label.
        required: Whether the capability was implicitly selected as baseline
            endpoint coverage.
    """

    method: HttpMethod
    path: str
    capability_id: str
    label: str
    required: bool


@dataclass(frozen=True)
class ApplicabilityDecision:
    """Traceability decision for one catalogue test case.

    Attributes:
        test_case_id: Catalogue test case under consideration.
        selected: Whether the compiler included the test case.
        reason: Human-readable inclusion/exclusion reason.
        dependency_of: Test cases that pulled this case in as a dependency.
    """

    test_case_id: str
    selected: bool
    reason: str
    dependency_of: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompilerTraceability:
    """Audit metadata explaining a compiled test plan.

    Attributes:
        catalogue_key: Catalogue boundary used for compilation.
        catalogue_version: Version of the catalogue content.
        security_profile: Security profile selected by the plan spec.
        selected_endpoints: Participant-declared implemented endpoints.
        selected_capabilities: Normalised required and explicitly selected
            endpoint capabilities used for applicability decisions.
        applicability_decisions: Per-test-case applicability decisions.
        generated_test_case_ids: Ordered compiled test case ids.
        runtime_input_snapshot: Trace-safe runtime input values/references.
        non_certifying_reasons: Reasons the compiled plan cannot be used for
            certification.
    """

    catalogue_key: CatalogueKey
    catalogue_version: str
    security_profile: SecurityProfile
    selected_endpoints: tuple[ImplementedEndpoint, ...]
    selected_capabilities: tuple[EndpointCapabilitySelection, ...]
    applicability_decisions: tuple[ApplicabilityDecision, ...]
    generated_test_case_ids: tuple[str, ...]
    runtime_input_snapshot: tuple[RuntimeInputTrace, ...]
    non_certifying_reasons: tuple[str, ...]


@dataclass(frozen=True)
class CompiledTestPlan:
    """Deterministic executable graph generated from a catalogue and plan spec.

    Attributes:
        catalogue_key: Catalogue boundary used for compilation.
        catalogue_version: Version of the catalogue content.
        security_profile: Security profile selected by the plan spec.
        test_cases: Ordered test cases after dependency expansion and
            topological sorting.
        traceability: Audit metadata explaining how the plan was compiled.
        certifying: Whether the plan remains eligible for certification from a
            planning perspective.
    """

    catalogue_key: CatalogueKey
    catalogue_version: str
    security_profile: SecurityProfile
    test_cases: tuple[CatalogueTestCase, ...]
    traceability: CompilerTraceability
    certifying: bool


def parse_test_plan_spec(raw_spec: object) -> TestPlanSpec:
    """Parse a decoded JSON object into a validated test-plan spec.

    Args:
        raw_spec: Decoded JSON value expected to match the plan-spec schema.

    Returns:
        Validated test-plan spec.

    Raises:
        CatalogueError: If the decoded value is malformed or unsupported.
    """
    spec = _json_object(raw_spec, location="planSpec")
    _reject_unknown_keys(
        spec,
        allowed_keys={
            "schemaVersion",
            "catalogue",
            "securityProfile",
            "implementedEndpoints",
            "runtimeInputs",
            "deselectedTestCaseIds",
            "assertionOverrides",
        },
        location="planSpec",
    )
    schema_version = _required_string(spec, "schemaVersion", location="planSpec")
    if schema_version != "v1":
        raise CatalogueError("planSpec.schemaVersion must be v1")

    return TestPlanSpec(
        schema_version="v1",
        catalogue_key=_parse_catalogue_key(_required_object(spec, "catalogue", location="planSpec")),
        security_profile=_parse_security_profile(
            _required_string(spec, "securityProfile", location="planSpec"),
            location="planSpec.securityProfile",
        ),
        implemented_endpoints=_parse_implemented_endpoints(
            _optional_object_array(spec, "implementedEndpoints", location="planSpec")
        ),
        runtime_inputs=MappingProxyType(_parse_runtime_inputs(spec)),
        deselected_test_case_ids=_parse_optional_string_array(
            spec,
            "deselectedTestCaseIds",
            location="planSpec",
        ),
        assertion_overrides=_parse_assertion_overrides(
            _optional_object_array(spec, "assertionOverrides", location="planSpec")
        ),
    )


def parse_test_plan_document(raw_spec: object) -> ParsedPlanDocument:
    """Parse a decoded shared plan document.

    Args:
        raw_spec: Decoded JSON value expected to match a supported plan
            document schema.

    Returns:
        Parsed v1 plan spec or v2 shared plan document.

    Raises:
        CatalogueError: If the decoded value is malformed or unsupported.
    """
    spec = _json_object(raw_spec, location="planSpec")
    schema_version = _required_string(spec, "schemaVersion", location="planSpec")
    if schema_version == "v1":
        return parse_test_plan_spec(spec)
    if schema_version == _CANONICAL_PLAN_SCHEMA_VERSION:
        return _parse_canonical_plan_document(spec)
    if schema_version == "v2":
        return _parse_plan_document_v2(spec)
    raise CatalogueError("planSpec.schemaVersion must be one of: 1.0, v1, v2")


def plan_document_to_json_object(document: PlanDocumentV2) -> JsonObject:
    """Serialise a shared plan document to the canonical PRD JSON shape.

    Args:
        document: Parsed plan document to serialise.

    Returns:
        JSON object preserving the canonical JSON-first test-plan shape.
    """
    return {
        "schemaVersion": _CANONICAL_PLAN_SCHEMA_VERSION,
        "specification": {
            "family": _canonical_family_for_plan_document(document),
            "version": document.version,
            "profile": _canonical_security_profile(document.security_profile),
        },
        "executionMode": document.execution_mode,
        "securityEnvironment": _security_environment_for_export(document),
        "resourceGroups": [
            _canonical_resource_group_for_export(resource_group) for resource_group in document.resource_groups
        ],
        "businessTestData": _business_test_data_for_export(document),
        "metadata": {key: _copy_json_value(value) for key, value in document.metadata.items()},
    }


def model_bank_config_from_plan_document(document: PlanDocumentV2) -> JsonObject:
    """Extract executable model-bank config fields from a test plan document.

    Args:
        document: Parsed JSON-first test plan document.

    Returns:
        Model-bank config JSON object accepted by
        :func:`conformance.model_bank_config.parse_model_bank_config`.
    """
    return {key: _copy_json_value(value) for key, value in document.config.items() if key in _MODEL_BANK_CONFIG_KEYS}


def supported_plan_document_boundaries(catalogues: Iterable[TestCatalogue]) -> tuple[PlanDocumentBoundary, ...]:
    """Return v2 plan boundaries backed by the supplied catalogues.

    Args:
        catalogues: Candidate catalogues available to parser/compiler callers.

    Returns:
        User-facing scheme/specification/version boundaries that have at least
        one backing bundled catalogue area.
    """
    internal_versions = {
        catalogue.key.version
        for catalogue in catalogues
        if catalogue.key.standard == _V2_OPEN_BANKING_INTERNAL_STANDARD
        and catalogue.key.api in _V2_READ_WRITE_API_FAMILIES
    }
    return tuple(
        PlanDocumentBoundary(
            scheme=_V2_OPEN_BANKING_UK_SCHEME,
            specification=_V2_READ_WRITE_SPECIFICATION,
            version=version,
        )
        for version in _V2_READ_WRITE_DISPLAY_VERSIONS
        if _V2_READ_WRITE_VERSION_MAP[version] in internal_versions
    )


def catalogue_areas_for_plan_document_boundary(
    boundary: PlanDocumentBoundary,
    catalogues: Iterable[TestCatalogue],
) -> tuple[TestCatalogue, ...]:
    """Return catalogue areas backing a user-facing v2 plan boundary.

    Args:
        boundary: User-facing scheme/specification/version boundary selected by
            the participant.
        catalogues: Candidate catalogues available to the caller.

    Returns:
        Catalogue areas that can contribute endpoints to the boundary.

    Raises:
        CatalogueError: If the boundary is unsupported or has no backing
            catalogue areas.
    """
    catalogue_version = _internal_catalogue_version_for_plan_boundary(boundary)
    candidates = tuple(
        catalogue
        for catalogue in catalogues
        if catalogue.key.standard == _V2_OPEN_BANKING_INTERNAL_STANDARD
        and catalogue.key.version == catalogue_version
        and catalogue.key.api in _V2_READ_WRITE_API_FAMILIES
    )
    if not candidates:
        raise CatalogueError(
            f"No catalogues are available for {boundary.scheme}/{boundary.specification}/{boundary.version}"
        )
    return candidates


def compile_test_plan_document(document: ParsedPlanDocument, catalogues: Iterable[TestCatalogue]) -> CompiledTestPlan:
    """Compile a parsed shared plan document against available catalogues.

    Args:
        document: Parsed v1 or v2 plan document.
        catalogues: Catalogue set available to the caller.

    Returns:
        Compiled executable plan, using aggregate traceability for v2 plans
        spanning multiple Read/Write catalogue areas.

    Raises:
        CatalogueError: If the document cannot be resolved or compiled against
            the supplied catalogues.
    """
    available_catalogues = tuple(catalogues)
    if isinstance(document, TestPlanSpec):
        return compile_test_plan(
            _resolve_catalogue_from_collection(document.catalogue_key, available_catalogues), document
        )
    return _compile_plan_document_v2(document, available_catalogues)


def compile_test_plan(catalogue: TestCatalogue, spec: TestPlanSpec) -> CompiledTestPlan:
    """Compile a participant plan spec into a deterministic catalogue plan.

    Args:
        catalogue: Versioned catalogue to compile.
        spec: Participant-authored plan spec selecting endpoints and runtime data.

    Returns:
        Compiled test plan with ordered test cases and traceability metadata.

    Raises:
        CatalogueError: If the catalogue/spec are inconsistent, mandatory cases
            are deselected, dependencies are invalid, runtime inputs are
            missing, or assertion overrides target unknown assertions.
    """
    if catalogue.key != spec.catalogue_key:
        raise CatalogueError("planSpec.catalogue does not match the supplied catalogue")

    cases_by_id = _catalogue_cases_by_id(catalogue)
    capabilities_by_id, capabilities_by_ref = _catalogue_capability_indexes(catalogue)
    implemented_refs = _implemented_endpoint_refs(spec.implemented_endpoints)
    selected_capabilities_by_ref, selected_capabilities = _selected_capabilities_by_endpoint(
        spec.implemented_endpoints,
        capabilities_by_id=capabilities_by_id,
        capabilities_by_ref=capabilities_by_ref,
        catalogue_capabilities=catalogue.capabilities,
    )
    _validate_test_case_capability_references(catalogue, capabilities_by_id)
    direct_ids, base_decisions = _directly_applicable_case_ids(
        catalogue,
        spec,
        implemented_refs,
        selected_capabilities_by_ref,
    )
    _reject_invalid_deselection(spec.deselected_test_case_ids, cases_by_id, direct_ids)
    ordered_ids, dependency_edges = _ordered_case_ids(
        cases_by_id,
        direct_ids - set(spec.deselected_test_case_ids),
        profile=spec.security_profile,
    )
    selected_ids = set(ordered_ids)
    dependency_deselections = selected_ids.intersection(spec.deselected_test_case_ids)
    if dependency_deselections:
        blocked_ids = ", ".join(sorted(dependency_deselections))
        raise CatalogueError(f"Cannot deselect dependency test case(s): {blocked_ids}")

    runtime_snapshot = _runtime_input_snapshot(cases_by_id, ordered_ids, spec.runtime_inputs)
    non_certifying_reasons = _validate_assertion_overrides(cases_by_id, ordered_ids, spec.assertion_overrides)
    selected_cases = tuple(cases_by_id[case_id] for case_id in ordered_ids)
    decisions = _traceability_decisions(base_decisions, selected_ids, dependency_edges, spec.deselected_test_case_ids)
    traceability = CompilerTraceability(
        catalogue_key=catalogue.key,
        catalogue_version=catalogue.catalogue_version,
        security_profile=spec.security_profile,
        selected_endpoints=spec.implemented_endpoints,
        selected_capabilities=selected_capabilities,
        applicability_decisions=decisions,
        generated_test_case_ids=ordered_ids,
        runtime_input_snapshot=runtime_snapshot,
        non_certifying_reasons=non_certifying_reasons,
    )
    return CompiledTestPlan(
        catalogue_key=catalogue.key,
        catalogue_version=catalogue.catalogue_version,
        security_profile=spec.security_profile,
        test_cases=selected_cases,
        traceability=traceability,
        certifying=not non_certifying_reasons,
    )


def _resolve_catalogue_from_collection(key: CatalogueKey, catalogues: Iterable[TestCatalogue]) -> TestCatalogue:
    """Resolve a catalogue key from an explicit catalogue collection.

    Args:
        key: Catalogue key requested by a v1 plan spec.
        catalogues: Candidate catalogues available to the caller.

    Returns:
        Matching catalogue.

    Raises:
        CatalogueError: If no supplied catalogue matches ``key``.
    """
    available_catalogues = tuple(catalogues)
    for catalogue in available_catalogues:
        if catalogue.key == key:
            return catalogue
    supported = ", ".join(
        f"{catalogue.key.standard}/{catalogue.key.version}/{catalogue.key.api}" for catalogue in available_catalogues
    )
    requested = f"{key.standard}/{key.version}/{key.api}"
    raise CatalogueError(f"Unsupported catalogue: {requested}. Supported catalogues: {supported}")


def _compile_plan_document_v2(document: PlanDocumentV2, catalogues: Iterable[TestCatalogue]) -> CompiledTestPlan:
    """Compile a v2 shared plan document across owning catalogues.

    Args:
        document: Parsed v2 plan document.
        catalogues: Candidate catalogues available to the caller.

    Returns:
        Aggregate compiled plan with merged test cases and traceability.

    Raises:
        CatalogueError: If the v2 boundary, endpoint ownership, or per-area
            compilation is invalid.
    """
    candidate_catalogues = _candidate_catalogues_for_plan_document_v2(document, tuple(catalogues))
    implemented_endpoints = _implemented_endpoints_from_plan_document_v2(document, candidate_catalogues)
    if not implemented_endpoints:
        raise CatalogueError("planSpec.scope.resourceGroups must select at least one endpoint")

    compiled_plans: list[CompiledTestPlan] = []
    for catalogue, endpoints in _partition_implemented_endpoints_by_catalogue(
        implemented_endpoints, candidate_catalogues
    ):
        area_spec = TestPlanSpec(
            schema_version="v1",
            catalogue_key=catalogue.key,
            security_profile=document.security_profile,
            implemented_endpoints=endpoints,
            runtime_inputs=document.runtime_inputs,
        )
        compiled_plans.append(compile_test_plan(catalogue, area_spec))

    return _merge_compiled_plan_documents_v2(document, tuple(compiled_plans), implemented_endpoints)


def _candidate_catalogues_for_plan_document_v2(
    document: PlanDocumentV2,
    catalogues: tuple[TestCatalogue, ...],
) -> tuple[TestCatalogue, ...]:
    """Return catalogue areas matching a v2 plan boundary.

    Args:
        document: Parsed v2 plan document.
        catalogues: Candidate catalogues available to the caller.

    Returns:
        Catalogue areas that can contribute to the requested v2 boundary.

    Raises:
        CatalogueError: If the v2 scheme/specification/version is unsupported.
    """
    return catalogue_areas_for_plan_document_boundary(
        PlanDocumentBoundary(scheme=document.scheme, specification=document.specification, version=document.version),
        catalogues,
    )


def _internal_catalogue_version_for_plan_document_v2(document: PlanDocumentV2) -> str:
    """Resolve the internal catalogue version for a v2 plan boundary.

    Args:
        document: Parsed v2 plan document.

    Returns:
        Internal catalogue version key.

    Raises:
        CatalogueError: If the v2 boundary is unsupported.
    """
    return _internal_catalogue_version_for_plan_boundary(
        PlanDocumentBoundary(scheme=document.scheme, specification=document.specification, version=document.version)
    )


def _internal_catalogue_version_for_plan_boundary(boundary: PlanDocumentBoundary) -> str:
    """Resolve the internal catalogue version for a v2 plan boundary.

    Args:
        boundary: User-facing scheme/specification/version boundary.

    Returns:
        Internal catalogue version key.

    Raises:
        CatalogueError: If the v2 boundary is unsupported.
    """
    if boundary.scheme != _V2_OPEN_BANKING_UK_SCHEME:
        raise CatalogueError(f"planSpec.scheme must be {_V2_OPEN_BANKING_UK_SCHEME}")
    if boundary.specification != _V2_READ_WRITE_SPECIFICATION:
        raise CatalogueError(f"planSpec.specification must be {_V2_READ_WRITE_SPECIFICATION}")
    catalogue_version = _V2_READ_WRITE_VERSION_MAP.get(boundary.version)
    if catalogue_version is None:
        supported_versions = ", ".join(sorted(_V2_READ_WRITE_VERSION_MAP))
        raise CatalogueError(f"planSpec.version must be one of: {supported_versions}")
    return catalogue_version


def _implemented_endpoints_from_plan_document_v2(
    document: PlanDocumentV2,
    catalogues: tuple[TestCatalogue, ...],
) -> tuple[ImplementedEndpoint, ...]:
    """Flatten v2 resource-group endpoints into compiler endpoint declarations.

    Args:
        document: Parsed v2 plan document.
        catalogues: Catalogue areas available for expanding group-level
            shorthand selections.

    Returns:
        Implemented endpoint declarations in plan-document order.
    """
    endpoints: list[ImplementedEndpoint] = []
    catalogues_by_api = {catalogue.key.api: catalogue for catalogue in catalogues}
    for resource_group in document.resource_groups:
        if resource_group.select_all:
            endpoints.extend(_all_endpoints_for_resource_group(resource_group, catalogues_by_api=catalogues_by_api))
            continue
        for endpoint in resource_group.endpoints:
            endpoints.append(
                ImplementedEndpoint(
                    method=endpoint.method,
                    path=endpoint.path,
                    resource_group=resource_group.resource_group_id,
                    operation_id=endpoint.operation_id,
                    capability_ids=endpoint.capability_ids,
                )
            )
    return tuple(endpoints)


def _all_endpoints_for_resource_group(
    resource_group: PlanResourceGroup,
    *,
    catalogues_by_api: Mapping[str, TestCatalogue],
) -> tuple[ImplementedEndpoint, ...]:
    """Expand a resource-group shorthand to every endpoint in its catalogue.

    Args:
        resource_group: Resource group whose shorthand selection is being
            compiled.
        catalogues_by_api: Candidate catalogues keyed by internal API family.

    Returns:
        Implemented endpoint declarations for all endpoint refs in catalogue
        order.

    Raises:
        CatalogueError: If the resource group cannot be mapped to a catalogue.
    """
    api = _api_for_plan_resource_group_id(resource_group.resource_group_id)
    if api is None or api not in catalogues_by_api:
        raise CatalogueError(f"No catalogue area owns resource group '{resource_group.resource_group_id}'")
    endpoints: list[ImplementedEndpoint] = []
    for endpoint_ref in _ordered_catalogue_endpoint_refs(catalogues_by_api[api]):
        endpoints.append(
            ImplementedEndpoint(
                method=endpoint_ref.method,
                path=endpoint_ref.path,
                resource_group=resource_group.resource_group_id,
            )
        )
    return tuple(endpoints)


def _ordered_catalogue_endpoint_refs(catalogue: TestCatalogue) -> tuple[EndpointRef, ...]:
    """Return catalogue endpoint refs in deterministic catalogue order.

    Args:
        catalogue: Catalogue whose endpoint refs should be enumerated.

    Returns:
        De-duplicated endpoint refs in test-case then capability order.
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
    return tuple(endpoint_refs)


def _partition_implemented_endpoints_by_catalogue(
    endpoints: tuple[ImplementedEndpoint, ...],
    catalogues: tuple[TestCatalogue, ...],
) -> tuple[tuple[TestCatalogue, tuple[ImplementedEndpoint, ...]], ...]:
    """Partition implemented endpoints by the catalogue area that owns them.

    Args:
        endpoints: Flattened endpoint declarations from a v2 plan document.
        catalogues: Candidate catalogues for the selected boundary.

    Returns:
        Catalogue/endpoints pairs in catalogue order.

    Raises:
        CatalogueError: If an endpoint has no owning catalogue or is ambiguous.
    """
    endpoint_refs_by_catalogue = tuple(_catalogue_endpoint_refs(catalogue) for catalogue in catalogues)
    catalogue_indexes_by_api = {catalogue.key.api: index for index, catalogue in enumerate(catalogues)}
    endpoints_by_catalogue_index: dict[int, list[ImplementedEndpoint]] = {}
    seen_refs_by_catalogue_index: dict[int, set[EndpointRef]] = {}
    for endpoint in endpoints:
        endpoint_ref = EndpointRef(method=endpoint.method, path=endpoint.path)
        resource_group_index = _catalogue_index_for_resource_group(
            endpoint.resource_group,
            catalogue_indexes_by_api=catalogue_indexes_by_api,
        )
        if resource_group_index is not None:
            if endpoint_ref not in endpoint_refs_by_catalogue[resource_group_index]:
                raise CatalogueError(
                    f"Resource group '{endpoint.resource_group}' does not contain endpoint "
                    f"{endpoint.method} {endpoint.path}"
                )
            _append_partitioned_endpoint(
                endpoints_by_catalogue_index,
                seen_refs_by_catalogue_index,
                catalogue_index=resource_group_index,
                endpoint=endpoint,
                endpoint_ref=endpoint_ref,
                catalogue=catalogues[resource_group_index],
            )
            continue

        owner_indexes = tuple(
            index for index, endpoint_refs in enumerate(endpoint_refs_by_catalogue) if endpoint_ref in endpoint_refs
        )
        if not owner_indexes:
            raise CatalogueError(f"No catalogue area owns endpoint {endpoint.method} {endpoint.path}")
        if len(owner_indexes) > 1:
            owner_keys = ", ".join(catalogues[index].key.api for index in owner_indexes)
            raise CatalogueError(
                f"Endpoint {endpoint.method} {endpoint.path} is ambiguous across catalogues: {owner_keys}"
            )
        _append_partitioned_endpoint(
            endpoints_by_catalogue_index,
            seen_refs_by_catalogue_index,
            catalogue_index=owner_indexes[0],
            endpoint=endpoint,
            endpoint_ref=endpoint_ref,
            catalogue=catalogues[owner_indexes[0]],
        )

    return tuple(
        (catalogue, tuple(endpoints_by_catalogue_index[index]))
        for index, catalogue in enumerate(catalogues)
        if index in endpoints_by_catalogue_index
    )


def _catalogue_index_for_resource_group(
    resource_group: str,
    *,
    catalogue_indexes_by_api: Mapping[str, int],
) -> int | None:
    """Return the catalogue index indicated by a v2 resource-group id.

    Args:
        resource_group: Resource-group id from the v2 plan document.
        catalogue_indexes_by_api: Mapping of API-family ids to catalogue
            positions in the selected boundary.

    Returns:
        Catalogue position when the resource group uses the ``api.slug`` shape,
        otherwise ``None`` so legacy/non-builder ids use endpoint ownership.

    Raises:
        CatalogueError: If the resource group names an API family that is not
            available in the selected v2 boundary.
    """
    api = _api_for_plan_resource_group_id(resource_group)
    if api is None:
        return None
    catalogue_index = catalogue_indexes_by_api.get(api)
    if catalogue_index is None:
        available_apis = ", ".join(sorted(catalogue_indexes_by_api))
        raise CatalogueError(
            f"Resource group '{resource_group}' is not available for this plan boundary. "
            f"Available API families: {available_apis}"
        )
    return catalogue_index


def _append_partitioned_endpoint(
    endpoints_by_catalogue_index: dict[int, list[ImplementedEndpoint]],
    seen_refs_by_catalogue_index: dict[int, set[EndpointRef]],
    *,
    catalogue_index: int,
    endpoint: ImplementedEndpoint,
    endpoint_ref: EndpointRef,
    catalogue: TestCatalogue,
) -> None:
    """Append an endpoint to one catalogue partition, rejecting duplicates.

    Args:
        endpoints_by_catalogue_index: Mutable partition accumulator.
        seen_refs_by_catalogue_index: Endpoint refs already added per catalogue
            partition.
        catalogue_index: Index of the owning catalogue.
        endpoint: Implemented endpoint declaration being partitioned.
        endpoint_ref: Method/path reference for duplicate checks.
        catalogue: Owning catalogue used in error messages.

    Raises:
        CatalogueError: If the same endpoint is selected twice for one
            catalogue area.
    """
    seen_refs = seen_refs_by_catalogue_index.setdefault(catalogue_index, set())
    if endpoint_ref in seen_refs:
        raise CatalogueError(
            f"planSpec scope duplicates implemented endpoint {endpoint.method} {endpoint.path} "
            f"in catalogue area {catalogue.key.api}"
        )
    seen_refs.add(endpoint_ref)
    endpoints_by_catalogue_index.setdefault(catalogue_index, []).append(endpoint)


def _catalogue_endpoint_refs(catalogue: TestCatalogue) -> set[EndpointRef]:
    """Return endpoint refs owned by a catalogue.

    Args:
        catalogue: Catalogue whose endpoint coverage should be indexed.

    Returns:
        Endpoint refs referenced by test-case applicability or capabilities.
    """
    endpoint_refs: set[EndpointRef] = set()
    for test_case in catalogue.test_cases:
        endpoint_refs.update(test_case.applicability.endpoint_refs)
    for capability in catalogue.capabilities:
        endpoint_refs.update(capability.endpoint_refs)
    return endpoint_refs


def _merge_compiled_plan_documents_v2(
    document: PlanDocumentV2,
    compiled_plans: tuple[CompiledTestPlan, ...],
    implemented_endpoints: tuple[ImplementedEndpoint, ...],
) -> CompiledTestPlan:
    """Merge per-catalogue compiled plans into one v2 compiled graph.

    Args:
        document: Parsed v2 plan document that produced the per-catalogue
            compiled plans.
        compiled_plans: Per-catalogue compiler outputs.
        implemented_endpoints: Flattened endpoint declarations from the plan.

    Returns:
        Aggregate compiled test plan.

    Raises:
        CatalogueError: If compiled plans cannot be merged safely.
    """
    aggregate_key = CatalogueKey(standard=document.scheme, version=document.version, api=document.specification)
    test_cases = _merge_compiled_test_cases(compiled_plans)
    runtime_snapshot = _merge_runtime_input_snapshots(compiled_plans)
    non_certifying_reasons = tuple(
        reason for compiled_plan in compiled_plans for reason in compiled_plan.traceability.non_certifying_reasons
    )
    generated_test_case_ids = tuple(case.test_case_id for case in test_cases)
    traceability = CompilerTraceability(
        catalogue_key=aggregate_key,
        catalogue_version=_aggregate_catalogue_version(compiled_plans),
        security_profile=document.security_profile,
        selected_endpoints=implemented_endpoints,
        selected_capabilities=tuple(
            capability
            for compiled_plan in compiled_plans
            for capability in compiled_plan.traceability.selected_capabilities
        ),
        applicability_decisions=tuple(
            decision
            for compiled_plan in compiled_plans
            for decision in compiled_plan.traceability.applicability_decisions
        ),
        generated_test_case_ids=generated_test_case_ids,
        runtime_input_snapshot=runtime_snapshot,
        non_certifying_reasons=non_certifying_reasons,
    )
    return CompiledTestPlan(
        catalogue_key=aggregate_key,
        catalogue_version=traceability.catalogue_version,
        security_profile=document.security_profile,
        test_cases=test_cases,
        traceability=traceability,
        certifying=all(compiled_plan.certifying for compiled_plan in compiled_plans),
    )


def _merge_compiled_test_cases(compiled_plans: tuple[CompiledTestPlan, ...]) -> tuple[CatalogueTestCase, ...]:
    """Merge compiled test cases and reject duplicate ids.

    Args:
        compiled_plans: Per-catalogue compiler outputs.

    Returns:
        Test cases in catalogue compilation order.

    Raises:
        CatalogueError: If two catalogue areas emit the same test-case id.
    """
    merged_cases: list[CatalogueTestCase] = []
    seen_case_ids: set[str] = set()
    for compiled_plan in compiled_plans:
        for test_case in compiled_plan.test_cases:
            if test_case.test_case_id in seen_case_ids:
                raise CatalogueError(
                    f"Compiled test case id '{test_case.test_case_id}' is duplicated across catalogues"
                )
            seen_case_ids.add(test_case.test_case_id)
            merged_cases.append(test_case)
    return tuple(merged_cases)


def _aggregate_catalogue_version(compiled_plans: tuple[CompiledTestPlan, ...]) -> str:
    """Build aggregate catalogue-version evidence for a v2 compiled plan.

    Args:
        compiled_plans: Per-catalogue compiler outputs.

    Returns:
        Stable semicolon-delimited catalogue-version summary.
    """
    return "; ".join(
        f"{compiled_plan.traceability.catalogue_key.api}:{compiled_plan.catalogue_version}"
        for compiled_plan in compiled_plans
    )


def _merge_runtime_input_snapshots(compiled_plans: tuple[CompiledTestPlan, ...]) -> tuple[RuntimeInputTrace, ...]:
    """Merge trace-safe runtime input snapshots across catalogue areas.

    Args:
        compiled_plans: Per-catalogue compiler outputs.

    Returns:
        Runtime input traces de-duplicated by input id in first-use order.

    Raises:
        CatalogueError: If catalogue areas disagree on runtime input types or
            incompatible non-sensitive values.
    """
    traces: list[RuntimeInputTrace] = []
    positions: dict[str, int] = {}
    for compiled_plan in compiled_plans:
        for trace in compiled_plan.traceability.runtime_input_snapshot:
            position = positions.get(trace.input_id)
            if position is None:
                positions[trace.input_id] = len(traces)
                traces.append(trace)
                continue
            traces[position] = _merge_runtime_input_trace(traces[position], trace)
    return tuple(traces)


def _merge_runtime_input_trace(existing: RuntimeInputTrace, incoming: RuntimeInputTrace) -> RuntimeInputTrace:
    """Merge two trace entries for the same runtime input id.

    Args:
        existing: Existing trace entry in the aggregate snapshot.
        incoming: New trace entry from another catalogue area.

    Returns:
        Merged trace entry.

    Raises:
        CatalogueError: If the two trace entries are incompatible.
    """
    if existing.input_type != incoming.input_type:
        raise CatalogueError(f"Runtime input '{existing.input_id}' has conflicting catalogue types")
    sensitive = existing.sensitive or incoming.sensitive
    value = _merged_runtime_input_trace_value(existing, incoming, sensitive=sensitive)
    return RuntimeInputTrace(
        input_id=existing.input_id,
        input_type=existing.input_type,
        required=existing.required or incoming.required,
        sensitive=sensitive,
        provided=existing.provided or incoming.provided,
        value=value,
    )


def _merged_runtime_input_trace_value(
    existing: RuntimeInputTrace,
    incoming: RuntimeInputTrace,
    *,
    sensitive: bool,
) -> JsonValue | None:
    """Merge trace-safe values for the same runtime input id.

    Args:
        existing: Existing trace entry in the aggregate snapshot.
        incoming: New trace entry from another catalogue area.
        sensitive: Whether the merged trace must suppress the value.

    Returns:
        Merged trace value, or ``None`` when absent or sensitive.

    Raises:
        CatalogueError: If two non-sensitive traces expose different values.
    """
    if sensitive:
        return None
    if existing.value is None:
        return _copy_json_value(incoming.value) if incoming.value is not None else None
    if incoming.value is None or existing.value == incoming.value:
        return _copy_json_value(existing.value)
    raise CatalogueError(f"Runtime input '{existing.input_id}' has conflicting trace values")


def _json_object(raw_value: object, *, location: str) -> dict[str, JsonValue]:
    """Return ``raw_value`` as a JSON object.

    Args:
        raw_value: Decoded value to inspect.
        location: Dot-path location string used in error messages.

    Returns:
        JSON object with string keys.

    Raises:
        CatalogueError: If ``raw_value`` is not a JSON object.
    """
    if not isinstance(raw_value, dict):
        raise CatalogueError(f"{location} must be a JSON object")
    return cast("dict[str, JsonValue]", raw_value)


def _reject_unknown_keys(raw_object: Mapping[str, JsonValue], *, allowed_keys: set[str], location: str) -> None:
    """Reject keys that are not part of the schema.

    Args:
        raw_object: JSON object to validate.
        allowed_keys: Exact set of accepted key names.
        location: Dot-path location string used in error messages.

    Raises:
        CatalogueError: If an unknown key is present.
    """
    unknown_keys = sorted(set(raw_object) - allowed_keys)
    if unknown_keys:
        raise CatalogueError(f"Unknown {location} field(s): {', '.join(unknown_keys)}")


def _required_object(raw_object: Mapping[str, JsonValue], key: str, *, location: str) -> dict[str, JsonValue]:
    """Extract a required JSON object field.

    Args:
        raw_object: Parent JSON object.
        key: Field name to extract.
        location: Dot-path location string used in error messages.

    Returns:
        Required child JSON object.

    Raises:
        CatalogueError: If the field is absent or not a JSON object.
    """
    if key not in raw_object:
        raise CatalogueError(f"{location}.{key} is required")
    return _json_object(raw_object[key], location=f"{location}.{key}")


def _required_string(raw_object: Mapping[str, JsonValue], key: str, *, location: str) -> str:
    """Extract a required non-empty string field.

    Args:
        raw_object: Parent JSON object.
        key: Field name to extract.
        location: Dot-path location string used in error messages.

    Returns:
        Stripped string value.

    Raises:
        CatalogueError: If the field is absent or not a non-empty string.
    """
    if key not in raw_object:
        raise CatalogueError(f"{location}.{key} is required")
    value = raw_object[key]
    if not isinstance(value, str) or not value.strip():
        raise CatalogueError(f"{location}.{key} must be a non-empty string")
    return value.strip()


def _optional_string(raw_object: Mapping[str, JsonValue], key: str, *, location: str) -> str | None:
    """Extract an optional non-empty string field.

    Args:
        raw_object: Parent JSON object.
        key: Field name to extract.
        location: Dot-path location string used in error messages.

    Returns:
        Stripped string value, or ``None`` when absent.

    Raises:
        CatalogueError: If the field is present but not a non-empty string.
    """
    if key not in raw_object:
        return None
    value = raw_object[key]
    if not isinstance(value, str) or not value.strip():
        raise CatalogueError(f"{location}.{key} must be a non-empty string when present")
    return value.strip()


def _optional_object_array(
    raw_object: Mapping[str, JsonValue],
    key: str,
    *,
    location: str,
) -> tuple[dict[str, JsonValue], ...]:
    """Extract an optional array of JSON objects.

    Args:
        raw_object: Parent JSON object.
        key: Field name to extract.
        location: Dot-path location string used in error messages.

    Returns:
        Tuple of JSON objects, empty when the field is absent.

    Raises:
        CatalogueError: If the field is not an array of JSON objects.
    """
    if key not in raw_object:
        return ()
    value = raw_object[key]
    if not isinstance(value, list):
        raise CatalogueError(f"{location}.{key} must be an array")
    return tuple(_json_object(item, location=f"{location}.{key}[{index}]") for index, item in enumerate(value))


def _required_object_array(
    raw_object: Mapping[str, JsonValue],
    key: str,
    *,
    location: str,
) -> tuple[dict[str, JsonValue], ...]:
    """Extract a required array of JSON objects.

    Args:
        raw_object: Parent JSON object.
        key: Field name to extract.
        location: Dot-path location string used in error messages.

    Returns:
        Tuple of JSON objects.

    Raises:
        CatalogueError: If the field is absent or not an array of JSON objects.
    """
    if key not in raw_object:
        raise CatalogueError(f"{location}.{key} is required")
    return _optional_object_array(raw_object, key, location=location)


def _parse_optional_string_array(raw_object: Mapping[str, JsonValue], key: str, *, location: str) -> tuple[str, ...]:
    """Extract an optional array of non-empty strings.

    Args:
        raw_object: Parent JSON object.
        key: Field name to extract.
        location: Dot-path location string used in error messages.

    Returns:
        Tuple of stripped strings, empty when the field is absent.

    Raises:
        CatalogueError: If the field is not an array of non-empty strings.
    """
    if key not in raw_object:
        return ()
    value = raw_object[key]
    if not isinstance(value, list):
        raise CatalogueError(f"{location}.{key} must be an array")
    parsed: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise CatalogueError(f"{location}.{key}[{index}] must be a non-empty string")
        parsed.append(item.strip())
    return tuple(parsed)


def _parse_catalogue_key(raw_key: Mapping[str, JsonValue]) -> CatalogueKey:
    """Parse a catalogue key object from a plan spec.

    Args:
        raw_key: Raw ``catalogue`` JSON object.

    Returns:
        Parsed catalogue key.

    Raises:
        CatalogueError: If required key fields are missing or unsupported.
    """
    _reject_unknown_keys(raw_key, allowed_keys={"standard", "version", "api"}, location="planSpec.catalogue")
    return CatalogueKey(
        standard=_required_string(raw_key, "standard", location="planSpec.catalogue"),
        version=_required_string(raw_key, "version", location="planSpec.catalogue"),
        api=_required_string(raw_key, "api", location="planSpec.catalogue"),
    )


def _parse_canonical_plan_document(spec: Mapping[str, JsonValue]) -> PlanDocumentV2:
    """Parse a PRD JSON-first test plan document.

    Args:
        spec: Raw test-plan JSON object with ``schemaVersion`` already
            identified as ``"1.0"``.

    Returns:
        Parsed shared plan document with compiler-facing fields derived from the
        canonical Open Banking test-plan sections.

    Raises:
        CatalogueError: If the canonical document is malformed or unsupported.
    """
    _reject_unknown_keys(
        spec,
        allowed_keys={
            "schemaVersion",
            "specification",
            "securityEnvironment",
            "resourceGroups",
            "businessTestData",
            "metadata",
            "executionMode",
        },
        location="testPlan",
    )
    raw_specification = _required_object(spec, "specification", location="testPlan")
    boundary, security_profile = _parse_canonical_specification(raw_specification)
    execution_mode = _parse_execution_mode(spec)
    security_environment = _parse_canonical_security_environment(
        _required_object(spec, "securityEnvironment", location="testPlan")
    )
    business_test_data = _parse_canonical_business_test_data(spec)
    metadata = _parse_canonical_metadata(spec)
    config = _canonical_plan_config(
        security_environment=security_environment,
        business_test_data=business_test_data,
    )
    return PlanDocumentV2(
        schema_version=_CANONICAL_PLAN_SCHEMA_VERSION,
        scheme=boundary.scheme,
        specification=boundary.specification,
        version=boundary.version,
        security_profile=security_profile,
        resource_groups=_parse_canonical_resource_groups(spec, location="testPlan"),
        config=MappingProxyType(config),
        runtime_inputs=MappingProxyType(_runtime_inputs_from_plan_config(config)),
        security_environment=MappingProxyType(security_environment),
        business_test_data=MappingProxyType(business_test_data),
        metadata=MappingProxyType(metadata),
        execution_mode=execution_mode,
    )


def _parse_canonical_specification(
    raw_specification: Mapping[str, JsonValue],
) -> tuple[PlanDocumentBoundary, SecurityProfile]:
    """Parse the canonical ``specification`` section.

    Args:
        raw_specification: Raw ``testPlan.specification`` object.

    Returns:
        Compiler boundary and security-profile pair.

    Raises:
        CatalogueError: If the specification family, profile, or version is not
            supported.
    """
    _reject_unknown_keys(
        raw_specification,
        allowed_keys={"family", "version", "profile", "securityProfile"},
        location="testPlan.specification",
    )
    family = _required_string(raw_specification, "family", location="testPlan.specification")
    if family != _CANONICAL_OBL_READ_WRITE_FAMILY:
        raise CatalogueError(f"testPlan.specification.family must be {_CANONICAL_OBL_READ_WRITE_FAMILY}")
    version = _required_string(raw_specification, "version", location="testPlan.specification")
    boundary = PlanDocumentBoundary(
        scheme=_V2_OPEN_BANKING_UK_SCHEME,
        specification=_V2_READ_WRITE_SPECIFICATION,
        version=version,
    )
    _internal_catalogue_version_for_plan_boundary(boundary)
    raw_profile = raw_specification.get("profile", raw_specification.get("securityProfile"))
    security_profile = _parse_canonical_security_profile(raw_profile, location="testPlan.specification.profile")
    return boundary, security_profile


def _parse_canonical_security_profile(value: JsonValue | None, *, location: str) -> SecurityProfile:
    """Parse a canonical or compiler-native security profile.

    Args:
        value: Raw security profile value, or ``None`` to use the default.
        location: Dot-path location string used in error messages.

    Returns:
        Compiler security-profile value.

    Raises:
        CatalogueError: If the supplied profile is not supported.
    """
    if value is None:
        return "fapi1-advanced"
    if not isinstance(value, str) or not value.strip():
        raise CatalogueError(f"{location} must be a non-empty string when present")
    normalized = value.strip()
    profile_map = {
        "FAPI1_ADVANCED": "fapi1-advanced",
        "FAPI2": "fapi2",
        "ALL": "all",
        "fapi1-advanced": "fapi1-advanced",
        "fapi2": "fapi2",
        "all": "all",
    }
    mapped = profile_map.get(normalized)
    if mapped is None:
        supported = ", ".join(sorted(profile_map))
        raise CatalogueError(f"{location} must be one of: {supported}")
    return cast("SecurityProfile", mapped)


def _parse_execution_mode(raw_plan: Mapping[str, JsonValue]) -> PlanExecutionMode:
    """Parse a canonical test-plan execution mode.

    Args:
        raw_plan: Raw top-level test-plan object.

    Returns:
        Parsed execution mode, defaulting to ``"certification"``.

    Raises:
        CatalogueError: If the mode is present but unsupported.
    """
    value = raw_plan.get("executionMode")
    if value is None:
        return "certification"
    if not isinstance(value, str) or value not in _SUPPORTED_PLAN_EXECUTION_MODES:
        supported = ", ".join(sorted(_SUPPORTED_PLAN_EXECUTION_MODES))
        raise CatalogueError(f"testPlan.executionMode must be one of: {supported}")
    return cast("PlanExecutionMode", value)


def _parse_canonical_security_environment(raw_environment: Mapping[str, JsonValue]) -> JsonObject:
    """Parse the canonical ``securityEnvironment`` section.

    Args:
        raw_environment: Raw security environment object.

    Returns:
        Deep-copied security environment object.

    Raises:
        CatalogueError: If required fields or known nested fields are malformed.
    """
    _reject_unknown_keys(
        raw_environment,
        allowed_keys={
            "name",
            "discoveryUrl",
            "issuer",
            "authorizationEndpoint",
            "tokenEndpoint",
            "jwksUri",
            "clientAuthMethod",
            "signingAlgorithm",
            "mtls",
            "clientId",
            "redirectUri",
            "openBankingIntentId",
            "resourceBaseUrl",
            "responseType",
            "acrValuesSupported",
            "signingCertificateRef",
            "signingPrivateKeyRef",
            "signingKeyId",
            "clientAssertionIssuer",
            "clientAssertionSubject",
            "tppSignatureIssuer",
            "tppSignatureTan",
            "caBundleRef",
            "xFapiFinancialId",
            "sendXFapiCustomerIpAddress",
            "xFapiCustomerIpAddress",
            "timeoutSeconds",
        },
        location="testPlan.securityEnvironment",
    )
    discovery_url = _required_string(raw_environment, "discoveryUrl", location="testPlan.securityEnvironment")
    try:
        validate_https_url(discovery_url, label="testPlan.securityEnvironment.discoveryUrl")
    except HttpsUrlValidationError as error:
        raise CatalogueError(str(error)) from error
    parsed = _copy_json_mapping(raw_environment)
    if "mtls" in parsed:
        _parse_canonical_mtls(_json_object(parsed["mtls"], location="testPlan.securityEnvironment.mtls"))
    if "clientAuthMethod" in parsed:
        _parse_canonical_client_auth_method(parsed["clientAuthMethod"])
    if "acrValuesSupported" in parsed:
        _parse_string_array_value(
            parsed["acrValuesSupported"],
            location="testPlan.securityEnvironment.acrValuesSupported",
        )
    return parsed


def _parse_canonical_mtls(raw_mtls: Mapping[str, JsonValue]) -> None:
    """Validate the canonical ``securityEnvironment.mtls`` object.

    Args:
        raw_mtls: Raw mTLS object from the canonical plan.

    Raises:
        CatalogueError: If known mTLS fields are malformed.
    """
    _reject_unknown_keys(
        raw_mtls,
        allowed_keys={"enabled", "certificateRef", "privateKeyRef", "caBundleRef", "certificatePathRoot"},
        location="testPlan.securityEnvironment.mtls",
    )
    enabled = raw_mtls.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise CatalogueError("testPlan.securityEnvironment.mtls.enabled must be a JSON boolean")
    for key in ("certificateRef", "privateKeyRef", "caBundleRef", "certificatePathRoot"):
        value = raw_mtls.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise CatalogueError(f"testPlan.securityEnvironment.mtls.{key} must be a non-empty string when present")


def _parse_canonical_client_auth_method(value: JsonValue) -> None:
    """Validate a canonical client authentication method.

    Args:
        value: Raw ``clientAuthMethod`` value.

    Raises:
        CatalogueError: If the auth method is not supported by the local runner.
    """
    if not isinstance(value, str) or not value.strip():
        raise CatalogueError("testPlan.securityEnvironment.clientAuthMethod must be a non-empty string when present")
    if value not in {"private_key_jwt", "tls_client_auth"}:
        raise CatalogueError(
            "testPlan.securityEnvironment.clientAuthMethod must be one of: private_key_jwt, tls_client_auth"
        )


def _parse_canonical_business_test_data(raw_plan: Mapping[str, JsonValue]) -> JsonObject:
    """Parse the canonical ``businessTestData`` section.

    Args:
        raw_plan: Raw top-level test-plan object.

    Returns:
        Deep-copied business test data object.

    Raises:
        CatalogueError: If ``businessTestData`` is present but not an object.
    """
    if "businessTestData" not in raw_plan:
        return {}
    return _copy_json_mapping(_json_object(raw_plan["businessTestData"], location="testPlan.businessTestData"))


def _parse_canonical_metadata(raw_plan: Mapping[str, JsonValue]) -> JsonObject:
    """Parse optional canonical plan metadata.

    Args:
        raw_plan: Raw top-level test-plan object.

    Returns:
        Deep-copied metadata object.

    Raises:
        CatalogueError: If ``metadata`` is present but not an object.
    """
    if "metadata" not in raw_plan:
        return {}
    return _copy_json_mapping(_json_object(raw_plan["metadata"], location="testPlan.metadata"))


def _parse_canonical_resource_groups(
    raw_plan: Mapping[str, JsonValue],
    *,
    location: str,
) -> tuple[PlanResourceGroup, ...]:
    """Parse canonical resource group declarations.

    Args:
        raw_plan: Raw top-level canonical test-plan object.
        location: Dot-path location string used in error messages.

    Returns:
        Resource-group declarations suitable for compilation.

    Raises:
        CatalogueError: If resource groups are missing, malformed, or duplicated.
    """
    raw_groups = raw_plan.get("resourceGroups")
    if not isinstance(raw_groups, list):
        raise CatalogueError(f"{location}.resourceGroups must be an array")
    groups: list[PlanResourceGroup] = []
    seen_ids: set[str] = set()
    for index, raw_group in enumerate(raw_groups):
        group_location = f"{location}.resourceGroups[{index}]"
        group = (
            _parse_canonical_resource_group_object(raw_group, location=group_location)
            if isinstance(raw_group, dict)
            else _parse_canonical_resource_group_string(raw_group, location=group_location)
        )
        if group.resource_group_id in seen_ids:
            raise CatalogueError(f"{group_location} duplicates resource group '{group.resource_group_id}'")
        seen_ids.add(group.resource_group_id)
        groups.append(group)
    return tuple(groups)


def _parse_canonical_resource_group_string(raw_group: JsonValue, *, location: str) -> PlanResourceGroup:
    """Parse a canonical resource group shorthand value.

    Args:
        raw_group: Raw resource group value from ``resourceGroups``.
        location: Dot-path location string used in error messages.

    Returns:
        Resource-group declaration that expands to all endpoints in the group.

    Raises:
        CatalogueError: If the group id is not a supported Open Banking group.
    """
    if not isinstance(raw_group, str) or not raw_group.strip():
        raise CatalogueError(f"{location} must be a non-empty string or JSON object")
    return _canonical_group_for_id(raw_group.strip(), location=location, select_all=True)


def _parse_canonical_resource_group_object(
    raw_group: Mapping[str, JsonValue],
    *,
    location: str,
) -> PlanResourceGroup:
    """Parse a detailed canonical resource group declaration.

    Args:
        raw_group: Raw resource group object.
        location: Dot-path location string used in error messages.

    Returns:
        Resource-group declaration with endpoint selections.

    Raises:
        CatalogueError: If the group id or endpoints are malformed.
    """
    _reject_unknown_keys(raw_group, allowed_keys={"id", "label", "endpoints"}, location=location)
    group = _canonical_group_for_id(_required_string(raw_group, "id", location=location), location=location)
    seen_refs: set[EndpointRef] = set()
    raw_endpoints = _optional_object_array(raw_group, "endpoints", location=location)
    endpoints = tuple(
        _parse_plan_document_v2_endpoint(raw_endpoint, location=f"{location}.endpoints[{index}]", seen_refs=seen_refs)
        for index, raw_endpoint in enumerate(raw_endpoints)
    )
    return PlanResourceGroup(
        resource_group_id=group.resource_group_id,
        label=_optional_string(raw_group, "label", location=location) or group.label,
        endpoints=endpoints,
        select_all=not endpoints,
    )


def _canonical_group_for_id(group_id: str, *, location: str, select_all: bool = False) -> PlanResourceGroup:
    """Return a resource-group declaration for a canonical or legacy group id.

    Args:
        group_id: Resource group identifier from a plan document.
        location: Dot-path location string used in error messages.
        select_all: Whether the declaration should expand to all group
            endpoints during compilation.

    Returns:
        Normalised resource-group declaration.

    Raises:
        CatalogueError: If the group id is not a supported resource group.
    """
    api = _api_for_plan_resource_group_id(group_id)
    if api is None or api not in _CANONICAL_RESOURCE_GROUPS_BY_API:
        supported = ", ".join(sorted(_RESOURCE_GROUP_API_BY_CANONICAL_ID))
        raise CatalogueError(f"{location} must be one of: {supported}")
    _canonical_id, builder_id, label = _CANONICAL_RESOURCE_GROUPS_BY_API[api]
    return PlanResourceGroup(resource_group_id=builder_id, label=label, endpoints=(), select_all=select_all)


def _canonical_plan_config(
    *,
    security_environment: Mapping[str, JsonValue],
    business_test_data: Mapping[str, JsonValue],
) -> JsonObject:
    """Build executable model-bank config from canonical plan sections.

    Args:
        security_environment: Parsed canonical security environment.
        business_test_data: Parsed canonical resource-group business data.

    Returns:
        Config object accepted by the existing model-bank runner.
    """
    config = _config_from_security_environment(security_environment)
    config.update(_config_from_business_test_data(business_test_data))
    return config


def _config_from_security_environment(security_environment: Mapping[str, JsonValue]) -> JsonObject:
    """Map canonical security environment fields to executable config.

    Args:
        security_environment: Canonical ``securityEnvironment`` object.

    Returns:
        Model-bank config fields derived from the canonical security section.
    """
    config: JsonObject = {"discoveryUrl": _copy_json_value(security_environment["discoveryUrl"])}
    _copy_optional_top_level_value(
        config,
        security_environment,
        source_key="timeoutSeconds",
        target_key="timeoutSeconds",
    )
    oauth = _oauth_config_from_security_environment(security_environment)
    if oauth:
        config["oauth"] = oauth
    fapi_signing = _fapi_signing_config_from_security_environment(security_environment)
    if fapi_signing:
        config["fapiSigning"] = fapi_signing
    tls = _tls_config_from_security_environment(security_environment)
    if tls:
        config["tls"] = tls
    resource_server = _resource_server_config_from_security_environment(security_environment)
    if resource_server:
        config["resourceServer"] = resource_server
    open_banking = _open_banking_config_from_security_environment(security_environment)
    if open_banking:
        config["openBanking"] = open_banking
    return config


def _oauth_config_from_security_environment(security_environment: Mapping[str, JsonValue]) -> JsonObject:
    """Return OAuth config fields derived from canonical security metadata.

    Args:
        security_environment: Canonical ``securityEnvironment`` object.

    Returns:
        OAuth config object, or an empty object when OAuth client fields are
        absent.
    """
    oauth: JsonObject = {}
    _copy_optional_top_level_value(oauth, security_environment, source_key="clientId", target_key="clientId")
    _copy_optional_top_level_value(oauth, security_environment, source_key="redirectUri", target_key="redirectUri")
    _copy_optional_top_level_value(
        oauth,
        security_environment,
        source_key="authorizationEndpoint",
        target_key="authorizationEndpoint",
    )
    _copy_optional_top_level_value(oauth, security_environment, source_key="issuer", target_key="issuer")
    _copy_optional_top_level_value(oauth, security_environment, source_key="tokenEndpoint", target_key="tokenEndpoint")
    _copy_optional_top_level_value(
        oauth,
        security_environment,
        source_key="openBankingIntentId",
        target_key="openBankingIntentId",
    )
    _copy_optional_top_level_value(
        oauth,
        security_environment,
        source_key="resourceBaseUrl",
        target_key="resourceBaseUrl",
    )
    _copy_optional_top_level_value(oauth, security_environment, source_key="responseType", target_key="responseType")
    _copy_optional_top_level_value(
        oauth,
        security_environment,
        source_key="acrValuesSupported",
        target_key="acrValuesSupported",
    )
    _copy_optional_top_level_value(
        oauth,
        security_environment,
        source_key="signingAlgorithm",
        target_key="requestObjectSigningAlg",
    )
    if oauth and ("clientId" not in oauth or "redirectUri" not in oauth):
        return {}
    return oauth


def _fapi_signing_config_from_security_environment(security_environment: Mapping[str, JsonValue]) -> JsonObject:
    """Return FAPI signing config fields from canonical security metadata.

    Args:
        security_environment: Canonical ``securityEnvironment`` object.

    Returns:
        FAPI signing config object, or an empty object when no signing reference
        fields are supplied.
    """
    signing: JsonObject = {}
    _copy_optional_top_level_value(
        signing,
        security_environment,
        source_key="signingCertificateRef",
        target_key="signingCertificatePath",
    )
    _copy_optional_top_level_value(
        signing,
        security_environment,
        source_key="signingPrivateKeyRef",
        target_key="signingPrivateKeyPath",
    )
    _copy_optional_top_level_value(signing, security_environment, source_key="signingKeyId", target_key="kid")
    _copy_optional_top_level_value(
        signing,
        security_environment,
        source_key="clientAssertionIssuer",
        target_key="clientAssertionIssuer",
    )
    _copy_optional_top_level_value(
        signing,
        security_environment,
        source_key="clientAssertionSubject",
        target_key="clientAssertionSubject",
    )
    _copy_optional_top_level_value(
        signing,
        security_environment,
        source_key="clientAuthMethod",
        target_key="tokenEndpointAuthMethod",
    )
    raw_mtls = security_environment.get("mtls")
    if isinstance(raw_mtls, dict):
        _copy_optional_top_level_value(
            signing,
            raw_mtls,
            source_key="certificatePathRoot",
            target_key="certificatePathRoot",
        )
    required_fields = {
        "signingCertificatePath",
        "signingPrivateKeyPath",
        "kid",
        "clientAssertionIssuer",
        "clientAssertionSubject",
        "tokenEndpointAuthMethod",
    }
    return signing if required_fields.issubset(signing) else {}


def _tls_config_from_security_environment(security_environment: Mapping[str, JsonValue]) -> JsonObject:
    """Return TLS config fields from canonical mTLS certificate references.

    Args:
        security_environment: Canonical ``securityEnvironment`` object.

    Returns:
        TLS config object, or an empty object when no mTLS references are supplied.
    """
    raw_mtls = security_environment.get("mtls")
    mtls = raw_mtls if isinstance(raw_mtls, dict) else {}
    tls: JsonObject = {}
    _copy_optional_top_level_value(tls, mtls, source_key="certificatePathRoot", target_key="certificatePathRoot")
    _copy_optional_top_level_value(tls, mtls, source_key="caBundleRef", target_key="caBundlePath")
    _copy_optional_top_level_value(tls, mtls, source_key="certificateRef", target_key="clientCertificatePath")
    _copy_optional_top_level_value(tls, mtls, source_key="privateKeyRef", target_key="clientPrivateKeyPath")
    return tls


def _resource_server_config_from_security_environment(security_environment: Mapping[str, JsonValue]) -> JsonObject:
    """Return resource-server config fields from canonical security metadata.

    Args:
        security_environment: Canonical ``securityEnvironment`` object.

    Returns:
        Resource-server config object, or an empty object when absent.
    """
    resource_server: JsonObject = {}
    _copy_optional_top_level_value(
        resource_server,
        security_environment,
        source_key="resourceBaseUrl",
        target_key="baseUrl",
    )
    _copy_optional_top_level_value(
        resource_server,
        security_environment,
        source_key="xFapiFinancialId",
        target_key="xFapiFinancialId",
    )
    _copy_optional_top_level_value(
        resource_server,
        security_environment,
        source_key="sendXFapiCustomerIpAddress",
        target_key="sendXFapiCustomerIpAddress",
    )
    _copy_optional_top_level_value(
        resource_server,
        security_environment,
        source_key="xFapiCustomerIpAddress",
        target_key="xFapiCustomerIpAddress",
    )
    return resource_server


def _open_banking_config_from_security_environment(security_environment: Mapping[str, JsonValue]) -> JsonObject:
    """Return Open Banking signature config from canonical security metadata.

    Args:
        security_environment: Canonical ``securityEnvironment`` object.

    Returns:
        Open Banking signature config object, or an empty object when absent.
    """
    open_banking: JsonObject = {}
    _copy_optional_top_level_value(
        open_banking,
        security_environment,
        source_key="tppSignatureIssuer",
        target_key="tppSignatureIssuer",
    )
    _copy_optional_top_level_value(
        open_banking,
        security_environment,
        source_key="tppSignatureTan",
        target_key="tppSignatureTan",
    )
    return open_banking


def _config_from_business_test_data(business_test_data: Mapping[str, JsonValue]) -> JsonObject:
    """Map canonical business test data to executable config sections.

    Args:
        business_test_data: Canonical ``businessTestData`` object.

    Returns:
        Model-bank config fields derived from the canonical business data.
    """
    config: JsonObject = {}
    ais = _business_section_object(business_test_data, "ais")
    if ais:
        config["ais"] = _ais_config_from_business_data(ais)
    for key in ("pis", "cbpii", "inputs", "runtimeInputs", "conditionalProperties"):
        value = business_test_data.get(key)
        if value is not None:
            config[key] = _copy_json_value(value)
    return config


def _business_section_object(business_test_data: Mapping[str, JsonValue], key: str) -> JsonObject:
    """Return a copied business test-data subsection.

    Args:
        business_test_data: Canonical business test-data object.
        key: Resource group key to inspect.

    Returns:
        Copied JSON object for the section, or an empty object when absent.

    Raises:
        CatalogueError: If the section is present but not an object.
    """
    value = business_test_data.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CatalogueError(f"testPlan.businessTestData.{key} must be a JSON object")
    return _copy_json_mapping(value)


def _ais_config_from_business_data(ais_data: Mapping[str, JsonValue]) -> JsonObject:
    """Return executable AIS config from canonical AIS business data.

    Args:
        ais_data: Canonical ``businessTestData.ais`` object.

    Returns:
        AIS config object accepted by runtime-input derivation.
    """
    ais_config = _copy_json_mapping(ais_data)
    raw_account_ids = ais_config.pop("accountIds", None)
    if raw_account_ids is not None:
        ais_config["resourceIds"] = {"accountIds": _account_id_objects(raw_account_ids)}
    return ais_config


def _account_id_objects(raw_account_ids: JsonValue) -> list[JsonValue]:
    """Return canonical account id data in legacy executable config shape.

    Args:
        raw_account_ids: Canonical ``businessTestData.ais.accountIds`` value.

    Returns:
        Array of account-id objects for executable AIS config.

    Raises:
        CatalogueError: If the value is not an array of strings or objects.
    """
    if not isinstance(raw_account_ids, list):
        raise CatalogueError("testPlan.businessTestData.ais.accountIds must be an array")
    account_ids: list[JsonValue] = []
    for index, raw_account_id in enumerate(raw_account_ids):
        if isinstance(raw_account_id, str) and raw_account_id.strip():
            account_ids.append({"accountId": raw_account_id.strip()})
            continue
        if isinstance(raw_account_id, dict):
            account_ids.append(_copy_json_mapping(cast("Mapping[str, JsonValue]", raw_account_id)))
            continue
        raise CatalogueError(
            f"testPlan.businessTestData.ais.accountIds[{index}] must be a non-empty string or JSON object"
        )
    return account_ids


def _copy_optional_top_level_value(
    target: JsonObject,
    source: Mapping[str, JsonValue],
    *,
    source_key: str,
    target_key: str,
) -> None:
    """Copy one optional non-empty value between JSON objects.

    Args:
        target: Mutable destination object.
        source: Source object.
        source_key: Source field name.
        target_key: Destination field name.
    """
    value = source.get(source_key)
    if value is None:
        return
    if isinstance(value, str) and not value.strip():
        return
    target[target_key] = _copy_json_value(value)


def _parse_plan_document_v2(spec: Mapping[str, JsonValue]) -> PlanDocumentV2:
    """Parse a legacy v2 shared plan document.

    Args:
        spec: Raw plan-spec JSON object with ``schemaVersion`` already
            identified as ``"v2"``.

    Returns:
        Parsed v2 plan document with nested scope and derived runtime inputs.

    Raises:
        CatalogueError: If the v2 document is malformed.
    """
    _reject_unknown_keys(
        spec,
        allowed_keys={"schemaVersion", "scheme", "specification", "version", "securityProfile", "scope", "config"},
        location="planSpec",
    )
    raw_config = _required_object(spec, "config", location="planSpec")
    config = _copy_json_mapping(raw_config)
    config.pop("environment", None)
    return PlanDocumentV2(
        schema_version="v2",
        scheme=_required_string(spec, "scheme", location="planSpec"),
        specification=_required_string(spec, "specification", location="planSpec"),
        version=_required_string(spec, "version", location="planSpec"),
        security_profile=_parse_security_profile(
            _required_string(spec, "securityProfile", location="planSpec"),
            location="planSpec.securityProfile",
        ),
        resource_groups=_parse_plan_document_v2_resource_groups(_required_object(spec, "scope", location="planSpec")),
        config=MappingProxyType(config),
        runtime_inputs=MappingProxyType(_runtime_inputs_from_plan_config(config)),
        security_environment=MappingProxyType(_security_environment_from_plan_config(config)),
        business_test_data=MappingProxyType(_business_test_data_from_plan_config(config)),
        metadata=MappingProxyType({}),
        execution_mode="certification",
    )


def _parse_plan_document_v2_resource_groups(raw_scope: Mapping[str, JsonValue]) -> tuple[PlanResourceGroup, ...]:
    """Parse v2 scope resource groups.

    Args:
        raw_scope: Raw ``scope`` JSON object from a v2 plan document.

    Returns:
        Parsed resource groups with nested endpoint declarations.

    Raises:
        CatalogueError: If resource groups or nested endpoints are malformed.
    """
    _reject_unknown_keys(raw_scope, allowed_keys={"resourceGroups"}, location="planSpec.scope")
    resource_groups: list[PlanResourceGroup] = []
    seen_group_ids: set[str] = set()
    for index, raw_group in enumerate(_required_object_array(raw_scope, "resourceGroups", location="planSpec.scope")):
        location = f"planSpec.scope.resourceGroups[{index}]"
        resource_group = _parse_plan_document_v2_resource_group(raw_group, location=location)
        if resource_group.resource_group_id in seen_group_ids:
            raise CatalogueError(f"{location}.id duplicates resource group '{resource_group.resource_group_id}'")
        seen_group_ids.add(resource_group.resource_group_id)
        resource_groups.append(resource_group)
    return tuple(resource_groups)


def _parse_plan_document_v2_resource_group(
    raw_group: Mapping[str, JsonValue],
    *,
    location: str,
) -> PlanResourceGroup:
    """Parse one v2 resource-group declaration.

    Args:
        raw_group: Raw resource-group JSON object.
        location: Dot-path location string used in error messages.

    Returns:
        Parsed resource group.

    Raises:
        CatalogueError: If the resource group or endpoints are malformed.
    """
    _reject_unknown_keys(raw_group, allowed_keys={"id", "label", "endpoints"}, location=location)
    seen_refs: set[EndpointRef] = set()
    endpoints = tuple(
        _parse_plan_document_v2_endpoint(raw_endpoint, location=f"{location}.endpoints[{index}]", seen_refs=seen_refs)
        for index, raw_endpoint in enumerate(_required_object_array(raw_group, "endpoints", location=location))
    )
    return PlanResourceGroup(
        resource_group_id=_required_string(raw_group, "id", location=location),
        label=_optional_string(raw_group, "label", location=location),
        endpoints=endpoints,
    )


def _parse_plan_document_v2_endpoint(
    raw_endpoint: Mapping[str, JsonValue],
    *,
    location: str,
    seen_refs: set[EndpointRef],
) -> PlanDocumentEndpoint:
    """Parse one v2 endpoint declaration.

    Args:
        raw_endpoint: Raw endpoint JSON object.
        location: Dot-path location string used in error messages.
        seen_refs: Endpoint refs already declared elsewhere in the document.

    Returns:
        Parsed endpoint declaration.

    Raises:
        CatalogueError: If the endpoint is malformed or duplicated.
    """
    _reject_unknown_keys(
        raw_endpoint, allowed_keys={"method", "path", "operationId", "capabilities"}, location=location
    )
    method = _parse_http_method(
        _required_string(raw_endpoint, "method", location=location), location=f"{location}.method"
    )
    path = _parse_absolute_path(_required_string(raw_endpoint, "path", location=location), location=f"{location}.path")
    endpoint_ref = EndpointRef(method=method, path=path)
    if endpoint_ref in seen_refs:
        raise CatalogueError(f"{location} duplicates implemented endpoint {method} {path}")
    seen_refs.add(endpoint_ref)
    return PlanDocumentEndpoint(
        method=method,
        path=path,
        operation_id=_optional_string(raw_endpoint, "operationId", location=location),
        capability_ids=_parse_endpoint_capability_ids(raw_endpoint, location=location),
    )


def _canonical_family_for_plan_document(document: PlanDocumentV2) -> str:
    """Return the canonical standards family for a parsed plan document.

    Args:
        document: Parsed plan document.

    Returns:
        Canonical PRD standards family value.
    """
    if document.scheme == _V2_OPEN_BANKING_UK_SCHEME and document.specification == _V2_READ_WRITE_SPECIFICATION:
        return _CANONICAL_OBL_READ_WRITE_FAMILY
    return f"{document.scheme}:{document.specification}"


def _canonical_security_profile(security_profile: SecurityProfile) -> str:
    """Return a canonical display value for a compiler security profile.

    Args:
        security_profile: Compiler security-profile selector.

    Returns:
        Canonical profile value used in exported plan JSON.
    """
    profile_map = {
        "fapi1-advanced": "FAPI1_ADVANCED",
        "fapi2": "FAPI2",
        "all": "ALL",
    }
    return profile_map[security_profile]


def _security_environment_for_export(document: PlanDocumentV2) -> JsonObject:
    """Return canonical security environment export data.

    Args:
        document: Parsed plan document.

    Returns:
        Security environment object in canonical PRD shape.
    """
    if document.security_environment:
        return {key: _copy_json_value(value) for key, value in document.security_environment.items()}
    return _security_environment_from_plan_config(document.config)


def _business_test_data_for_export(document: PlanDocumentV2) -> JsonObject:
    """Return canonical business test-data export data.

    Args:
        document: Parsed plan document.

    Returns:
        Business test-data object in canonical PRD shape.
    """
    if document.business_test_data:
        return {key: _copy_json_value(value) for key, value in document.business_test_data.items()}
    return _business_test_data_from_plan_config(document.config)


def _canonical_resource_group_for_export(resource_group: PlanResourceGroup) -> JsonValue:
    """Return one canonical resource-group export entry.

    Args:
        resource_group: Parsed resource group selected in the plan.

    Returns:
        A shorthand resource-group string when the group selects all endpoints,
        otherwise a detailed object preserving endpoint selections.
    """
    group_id = _canonical_resource_group_id(resource_group.resource_group_id)
    if resource_group.select_all:
        return group_id
    exported: JsonObject = {
        "id": group_id,
        **({"label": resource_group.label} if resource_group.label is not None else {}),
        "endpoints": [
            {
                "method": endpoint.method,
                "path": endpoint.path,
                **({"operationId": endpoint.operation_id} if endpoint.operation_id is not None else {}),
                **({"capabilities": list(endpoint.capability_ids)} if endpoint.capability_ids else {}),
            }
            for endpoint in resource_group.endpoints
        ],
    }
    return exported


def _security_environment_from_plan_config(config: Mapping[str, JsonValue]) -> JsonObject:
    """Build canonical security metadata from legacy executable config.

    Args:
        config: Model-bank config section preserved by a plan document.

    Returns:
        Canonical security environment object.
    """
    security_environment: JsonObject = {
        "discoveryUrl": _copy_json_value(config["discoveryUrl"]) if "discoveryUrl" in config else ""
    }
    _copy_optional_top_level_value(
        security_environment,
        config,
        source_key="timeoutSeconds",
        target_key="timeoutSeconds",
    )
    oauth = config.get("oauth")
    if isinstance(oauth, dict):
        _copy_optional_top_level_value(security_environment, oauth, source_key="issuer", target_key="issuer")
        _copy_optional_top_level_value(
            security_environment,
            oauth,
            source_key="authorizationEndpoint",
            target_key="authorizationEndpoint",
        )
        _copy_optional_top_level_value(
            security_environment,
            oauth,
            source_key="tokenEndpoint",
            target_key="tokenEndpoint",
        )
        _copy_optional_top_level_value(security_environment, oauth, source_key="clientId", target_key="clientId")
        _copy_optional_top_level_value(security_environment, oauth, source_key="redirectUri", target_key="redirectUri")
        _copy_optional_top_level_value(
            security_environment,
            oauth,
            source_key="openBankingIntentId",
            target_key="openBankingIntentId",
        )
        _copy_optional_top_level_value(
            security_environment,
            oauth,
            source_key="resourceBaseUrl",
            target_key="resourceBaseUrl",
        )
        _copy_optional_top_level_value(
            security_environment,
            oauth,
            source_key="responseType",
            target_key="responseType",
        )
        _copy_optional_top_level_value(
            security_environment,
            oauth,
            source_key="acrValuesSupported",
            target_key="acrValuesSupported",
        )
        _copy_optional_top_level_value(
            security_environment,
            oauth,
            source_key="requestObjectSigningAlg",
            target_key="signingAlgorithm",
        )
    fapi_signing = config.get("fapiSigning")
    if isinstance(fapi_signing, dict):
        _copy_optional_top_level_value(
            security_environment,
            fapi_signing,
            source_key="tokenEndpointAuthMethod",
            target_key="clientAuthMethod",
        )
        _copy_optional_top_level_value(
            security_environment,
            fapi_signing,
            source_key="signingCertificatePath",
            target_key="signingCertificateRef",
        )
        _copy_optional_top_level_value(
            security_environment,
            fapi_signing,
            source_key="signingPrivateKeyPath",
            target_key="signingPrivateKeyRef",
        )
        _copy_optional_top_level_value(security_environment, fapi_signing, source_key="kid", target_key="signingKeyId")
        _copy_optional_top_level_value(
            security_environment,
            fapi_signing,
            source_key="clientAssertionIssuer",
            target_key="clientAssertionIssuer",
        )
        _copy_optional_top_level_value(
            security_environment,
            fapi_signing,
            source_key="clientAssertionSubject",
            target_key="clientAssertionSubject",
        )
    _merge_mtls_export(security_environment, config)
    resource_server = config.get("resourceServer")
    if isinstance(resource_server, dict):
        _copy_optional_top_level_value(
            security_environment,
            resource_server,
            source_key="baseUrl",
            target_key="resourceBaseUrl",
        )
        _copy_optional_top_level_value(
            security_environment,
            resource_server,
            source_key="xFapiFinancialId",
            target_key="xFapiFinancialId",
        )
        _copy_optional_top_level_value(
            security_environment,
            resource_server,
            source_key="sendXFapiCustomerIpAddress",
            target_key="sendXFapiCustomerIpAddress",
        )
        _copy_optional_top_level_value(
            security_environment,
            resource_server,
            source_key="xFapiCustomerIpAddress",
            target_key="xFapiCustomerIpAddress",
        )
    open_banking = config.get("openBanking")
    if isinstance(open_banking, dict):
        _copy_optional_top_level_value(
            security_environment,
            open_banking,
            source_key="tppSignatureIssuer",
            target_key="tppSignatureIssuer",
        )
        _copy_optional_top_level_value(
            security_environment,
            open_banking,
            source_key="tppSignatureTan",
            target_key="tppSignatureTan",
        )
    return security_environment


def _merge_mtls_export(security_environment: JsonObject, config: Mapping[str, JsonValue]) -> None:
    """Merge legacy TLS config into canonical ``mtls`` export metadata.

    Args:
        security_environment: Mutable security environment export object.
        config: Model-bank config section preserved by a plan document.
    """
    tls = config.get("tls")
    if not isinstance(tls, dict):
        return
    mtls: JsonObject = {}
    _copy_optional_top_level_value(mtls, tls, source_key="certificatePathRoot", target_key="certificatePathRoot")
    _copy_optional_top_level_value(mtls, tls, source_key="caBundlePath", target_key="caBundleRef")
    _copy_optional_top_level_value(mtls, tls, source_key="clientCertificatePath", target_key="certificateRef")
    _copy_optional_top_level_value(mtls, tls, source_key="clientPrivateKeyPath", target_key="privateKeyRef")
    mtls["enabled"] = "certificateRef" in mtls and "privateKeyRef" in mtls
    if mtls:
        security_environment["mtls"] = mtls


def _business_test_data_from_plan_config(config: Mapping[str, JsonValue]) -> JsonObject:
    """Build canonical business test data from legacy executable config.

    Args:
        config: Model-bank config section preserved by a plan document.

    Returns:
        Canonical business test-data object.
    """
    business_test_data: JsonObject = {}
    ais = config.get("ais")
    if isinstance(ais, dict):
        business_test_data["ais"] = _ais_business_data_from_config(ais)
    for key in ("pis", "cbpii", "vrp", "inputs", "runtimeInputs", "conditionalProperties"):
        value = config.get(key)
        if value is not None:
            business_test_data[key] = _copy_json_value(value)
    top_level_runtime_inputs = {
        key: _copy_json_value(value)
        for key, value in config.items()
        if key not in _MODEL_BANK_CONFIG_KEYS | {"inputs", "runtimeInputs"} and not isinstance(value, dict | list)
    }
    if top_level_runtime_inputs and "runtimeInputs" not in business_test_data:
        business_test_data["runtimeInputs"] = top_level_runtime_inputs
    return business_test_data


def _ais_business_data_from_config(ais_config: Mapping[str, JsonValue]) -> JsonObject:
    """Return canonical AIS business test data from executable config.

    Args:
        ais_config: Legacy AIS config object.

    Returns:
        Canonical AIS business data object.
    """
    ais_data = _copy_json_mapping(ais_config)
    raw_resource_ids = ais_data.pop("resourceIds", None)
    if isinstance(raw_resource_ids, dict):
        raw_account_ids = raw_resource_ids.get("accountIds")
        if isinstance(raw_account_ids, list):
            account_ids: list[JsonValue] = []
            for raw_account_id in raw_account_ids:
                if isinstance(raw_account_id, dict) and isinstance(raw_account_id.get("accountId"), str):
                    account_ids.append(raw_account_id["accountId"])
                else:
                    account_ids.append(_copy_json_value(raw_account_id))
            ais_data["accountIds"] = account_ids
        else:
            ais_data["resourceIds"] = _copy_json_mapping(raw_resource_ids)
    return ais_data


def _canonical_resource_group_id(resource_group_id: str) -> str:
    """Return a canonical resource-group id for exports.

    Args:
        resource_group_id: Internal, legacy, or canonical resource-group id.

    Returns:
        Canonical PRD resource-group id.
    """
    api = _api_for_plan_resource_group_id(resource_group_id)
    if api is None:
        return resource_group_id
    return _CANONICAL_RESOURCE_GROUPS_BY_API.get(api, (resource_group_id, resource_group_id, resource_group_id))[0]


def _api_for_plan_resource_group_id(resource_group_id: str) -> str | None:
    """Return the internal API family for a resource-group id.

    Args:
        resource_group_id: Canonical, high-level builder, or legacy resource
            group id.

    Returns:
        Internal catalogue API family, or ``None`` when the id is not recognised.
    """
    canonical_api = _RESOURCE_GROUP_API_BY_CANONICAL_ID.get(resource_group_id)
    if canonical_api is not None:
        return canonical_api
    builder_api = _RESOURCE_GROUP_API_BY_BUILDER_ID.get(resource_group_id)
    if builder_api is not None:
        return builder_api
    api, separator, _slug_value = resource_group_id.partition(".")
    return api if separator and api else None


def _parse_string_array_value(value: JsonValue, *, location: str) -> tuple[str, ...]:
    """Parse an arbitrary JSON value as an array of non-empty strings.

    Args:
        value: Raw JSON value to parse.
        location: Dot-path location string used in error messages.

    Returns:
        Tuple of stripped string values.

    Raises:
        CatalogueError: If the value is not an array of non-empty strings.
    """
    if not isinstance(value, list):
        raise CatalogueError(f"{location} must be a JSON array")
    parsed: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise CatalogueError(f"{location}[{index}] must be a non-empty string")
        parsed.append(item.strip())
    return tuple(parsed)


def _parse_security_profile(value: str, *, location: str) -> SecurityProfile:
    """Parse a supported security profile value.

    Args:
        value: Raw string value from the plan spec.
        location: Dot-path location string used in error messages.

    Returns:
        Typed security profile.

    Raises:
        CatalogueError: If the value is not supported.
    """
    if value not in _SUPPORTED_SECURITY_PROFILES:
        supported = ", ".join(sorted(_SUPPORTED_SECURITY_PROFILES))
        raise CatalogueError(f"{location} must be one of: {supported}")
    return cast("SecurityProfile", value)


def _parse_http_method(value: str, *, location: str) -> HttpMethod:
    """Parse a supported HTTP method.

    Args:
        value: Raw method string.
        location: Dot-path location string used in error messages.

    Returns:
        Typed HTTP method.

    Raises:
        CatalogueError: If the method is unsupported.
    """
    method = value.upper()
    if method not in _SUPPORTED_HTTP_METHODS:
        supported = ", ".join(sorted(_SUPPORTED_HTTP_METHODS))
        raise CatalogueError(f"{location} must be one of: {supported}")
    return cast("HttpMethod", method)


def _parse_implemented_endpoints(raw_endpoints: Iterable[Mapping[str, JsonValue]]) -> tuple[ImplementedEndpoint, ...]:
    """Parse implemented endpoint declarations.

    Args:
        raw_endpoints: Raw endpoint objects from the plan spec.

    Returns:
        Parsed implemented endpoints.

    Raises:
        CatalogueError: If an endpoint is malformed or duplicated.
    """
    endpoints: list[ImplementedEndpoint] = []
    seen_refs: set[EndpointRef] = set()
    for index, raw_endpoint in enumerate(raw_endpoints):
        location = f"planSpec.implementedEndpoints[{index}]"
        _reject_unknown_keys(
            raw_endpoint,
            allowed_keys={"method", "path", "resourceGroup", "operationId", "capabilities"},
            location=location,
        )
        endpoint = ImplementedEndpoint(
            method=_parse_http_method(
                _required_string(raw_endpoint, "method", location=location),
                location=f"{location}.method",
            ),
            path=_parse_absolute_path(
                _required_string(raw_endpoint, "path", location=location),
                location=f"{location}.path",
            ),
            resource_group=_required_string(raw_endpoint, "resourceGroup", location=location),
            operation_id=_optional_string(raw_endpoint, "operationId", location=location),
            capability_ids=_parse_endpoint_capability_ids(raw_endpoint, location=location),
        )
        endpoint_ref = EndpointRef(method=endpoint.method, path=endpoint.path)
        if endpoint_ref in seen_refs:
            raise CatalogueError(f"{location} duplicates implemented endpoint {endpoint.method} {endpoint.path}")
        seen_refs.add(endpoint_ref)
        endpoints.append(endpoint)
    return tuple(endpoints)


def _parse_endpoint_capability_ids(raw_endpoint: Mapping[str, JsonValue], *, location: str) -> tuple[str, ...]:
    """Parse endpoint-scoped capability ids from a plan spec endpoint.

    Args:
        raw_endpoint: Raw endpoint object from ``implementedEndpoints``.
        location: Dot-path location string used in error messages.

    Returns:
        Tuple of stripped capability ids, empty when the field is absent.

    Raises:
        CatalogueError: If capability ids are malformed or duplicated within
            the endpoint declaration.
    """
    capability_ids = _parse_optional_string_array(raw_endpoint, "capabilities", location=location)
    seen: set[str] = set()
    for index, capability_id in enumerate(capability_ids):
        if capability_id in seen:
            raise CatalogueError(f"{location}.capabilities[{index}] duplicates capability '{capability_id}'")
        seen.add(capability_id)
    return capability_ids


def _parse_absolute_path(value: str, *, location: str) -> str:
    """Parse an absolute standards path.

    Args:
        value: Raw path string.
        location: Dot-path location string used in error messages.

    Returns:
        Normalized path without a trailing slash unless it is the root path.

    Raises:
        CatalogueError: If the value is not an absolute path.
    """
    if not value.startswith("/"):
        raise CatalogueError(f"{location} must be an absolute path")
    normalized = "/" + "/".join(segment for segment in value.split("/") if segment)
    return "/" if normalized == "/" else normalized.rstrip("/")


def _parse_runtime_inputs(raw_spec: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Parse the optional runtime input mapping from a plan spec.

    Args:
        raw_spec: Parent plan-spec object.

    Returns:
        Runtime input mapping with copied JSON values.

    Raises:
        CatalogueError: If the runtime input field is not a JSON object.
    """
    if "runtimeInputs" not in raw_spec:
        return {}
    raw_inputs = _json_object(raw_spec["runtimeInputs"], location="planSpec.runtimeInputs")
    return {key: _copy_json_value(value) for key, value in raw_inputs.items()}


def _runtime_inputs_from_plan_config(config: Mapping[str, JsonValue]) -> JsonObject:
    """Derive compiler runtime inputs from a v2 plan ``config`` section.

    Args:
        config: Raw v2 config mapping preserved by the plan document.

    Returns:
        Flat runtime-input mapping for catalogue compilation and execution.

    Raises:
        CatalogueError: If structured runtime-input config fields are malformed.
    """
    runtime_inputs: JsonObject = {}
    for key, value in config.items():
        if key in {"inputs", "runtimeInputs"} or isinstance(value, dict | list):
            continue
        runtime_inputs[key] = _copy_json_value(value)

    _merge_structured_runtime_inputs(runtime_inputs, config)

    if "runtimeInputs" in config:
        raw_runtime_inputs = _json_object(config["runtimeInputs"], location="planSpec.config.runtimeInputs")
        for input_id, value in raw_runtime_inputs.items():
            _merge_plan_config_runtime_input(runtime_inputs, input_id, value, location="planSpec.config.runtimeInputs")

    if "inputs" in config:
        raw_inputs = _json_object(config["inputs"], location="planSpec.config.inputs")
        for input_id, raw_value in raw_inputs.items():
            value = _runtime_value_from_plan_config_input(raw_value)
            _merge_plan_config_runtime_input(runtime_inputs, input_id, value, location="planSpec.config.inputs")

    return runtime_inputs


def _merge_structured_runtime_inputs(runtime_inputs: JsonObject, config: Mapping[str, JsonValue]) -> None:
    """Merge runtime inputs derived from structured v2 config sections.

    Args:
        runtime_inputs: Mutable runtime-input mapping being built.
        config: Raw v2 plan ``config`` object.

    Raises:
        CatalogueError: If a derived value conflicts with another config value.
    """
    resource_server = _optional_runtime_config_object(config, "resourceServer")
    if resource_server is not None:
        _merge_optional_structured_runtime_input(
            runtime_inputs,
            "resourceBaseUrl",
            resource_server.get("baseUrl"),
            location="planSpec.config.resourceServer.baseUrl",
        )

    oauth = _optional_runtime_config_object(config, "oauth")
    if oauth is not None:
        _merge_optional_structured_runtime_input(
            runtime_inputs,
            "resourceBaseUrl",
            oauth.get("resourceBaseUrl"),
            location="planSpec.config.oauth.resourceBaseUrl",
        )

    ais = _optional_runtime_config_object(config, "ais")
    if ais is not None:
        resource_ids = _optional_nested_runtime_config_object(ais, "resourceIds")
        if resource_ids is not None:
            account_id = _first_object_string(resource_ids.get("accountIds"), "accountId")
            _merge_optional_structured_runtime_input(
                runtime_inputs,
                "consentedAccountId",
                account_id,
                location="planSpec.config.ais.resourceIds.accountIds[0].accountId",
            )
        _merge_optional_structured_runtime_input(
            runtime_inputs,
            "fromBookingDateTime",
            ais.get("transactionFromDate"),
            location="planSpec.config.ais.transactionFromDate",
        )
        _merge_optional_structured_runtime_input(
            runtime_inputs,
            "toBookingDateTime",
            ais.get("transactionToDate"),
            location="planSpec.config.ais.transactionToDate",
        )

    cbpii = _optional_runtime_config_object(config, "cbpii")
    if cbpii is not None:
        debtor_account = _optional_nested_runtime_config_object(cbpii, "debtorAccount")
        if debtor_account is not None:
            _merge_optional_structured_runtime_input(
                runtime_inputs,
                "debtorAccountSchemeName",
                debtor_account.get("schemeName"),
                location="planSpec.config.cbpii.debtorAccount.schemeName",
            )
            _merge_optional_structured_runtime_input(
                runtime_inputs,
                "debtorAccountIdentification",
                debtor_account.get("identification"),
                location="planSpec.config.cbpii.debtorAccount.identification",
            )
            _merge_optional_structured_runtime_input(
                runtime_inputs,
                "debtorAccountName",
                debtor_account.get("name"),
                location="planSpec.config.cbpii.debtorAccount.name",
            )


def _optional_runtime_config_object(config: Mapping[str, JsonValue], key: str) -> Mapping[str, JsonValue] | None:
    """Return an optional structured runtime config object.

    Args:
        config: Raw v2 plan ``config`` object.
        key: Section key to inspect.

    Returns:
        The nested object, or ``None`` when absent.

    Raises:
        CatalogueError: If the section is present but not an object.
    """
    value = config.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise CatalogueError(f"planSpec.config.{key} must be a JSON object")
    return value


def _optional_nested_runtime_config_object(
    config: Mapping[str, JsonValue],
    key: str,
) -> Mapping[str, JsonValue] | None:
    """Return an optional nested runtime config object.

    Args:
        config: Parent structured config object.
        key: Nested key to inspect.

    Returns:
        The nested object, or ``None`` when absent.

    Raises:
        CatalogueError: If the section is present but not an object.
    """
    value = config.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise CatalogueError(f"Structured config field '{key}' must be a JSON object")
    return value


def _first_object_string(value: JsonValue | None, key: str) -> str | None:
    """Return a string field from the first object in an array.

    Args:
        value: Candidate JSON array.
        key: Object key to extract from the first entry.

    Returns:
        The string value, or ``None`` when no suitable value is present.

    Raises:
        CatalogueError: If the supplied value is not an array of JSON objects.
    """
    if value is None:
        return None
    if not isinstance(value, list):
        raise CatalogueError(f"Structured config field '{key}' source must be a JSON array")
    if not value:
        return None
    first = value[0]
    if not isinstance(first, dict):
        raise CatalogueError(f"Structured config field '{key}' source must contain JSON objects")
    raw_value = first.get(key)
    if raw_value is None:
        return None
    if not isinstance(raw_value, str):
        raise CatalogueError(f"Structured config field '{key}' must be a string")
    return raw_value


def _merge_optional_structured_runtime_input(
    runtime_inputs: JsonObject,
    input_id: str,
    value: JsonValue | None,
    *,
    location: str,
) -> None:
    """Merge a structured runtime input when a value is present.

    Args:
        runtime_inputs: Mutable runtime-input mapping being built.
        input_id: Runtime input id to merge.
        value: Candidate JSON value.
        location: Dot-path location string used in error messages.

    Raises:
        CatalogueError: If the value conflicts with another config value.
    """
    if value is None:
        return
    if isinstance(value, str) and not value.strip():
        return
    _merge_plan_config_runtime_input(runtime_inputs, input_id, value, location=location)


def _merge_plan_config_runtime_input(
    runtime_inputs: JsonObject,
    input_id: str,
    value: JsonValue,
    *,
    location: str,
) -> None:
    """Merge one runtime input derived from v2 config.

    Args:
        runtime_inputs: Mutable derived runtime-input mapping.
        input_id: Runtime input identifier to merge.
        value: JSON value supplied for the runtime input.
        location: Dot-path location string used in error messages.

    Raises:
        CatalogueError: If the same runtime input is supplied with conflicting
            values in multiple config locations.
    """
    if not input_id.strip():
        raise CatalogueError(f"{location} contains an empty runtime input id")
    copied_value = _copy_json_value(value)
    existing = runtime_inputs.get(input_id)
    if input_id in runtime_inputs and existing != copied_value:
        raise CatalogueError(f"{location}.{input_id} conflicts with another config value")
    runtime_inputs[input_id] = copied_value


def _runtime_value_from_plan_config_input(raw_value: JsonValue) -> JsonValue:
    """Extract an executable runtime value from a v2 ``config.inputs`` entry.

    Args:
        raw_value: Raw value from ``config.inputs``.

    Returns:
        Direct runtime value, or the explicit ``value`` field when the input
        uses an object wrapper.
    """
    if isinstance(raw_value, dict) and "value" in raw_value:
        return _copy_json_value(raw_value["value"])
    return _copy_json_value(raw_value)


def _parse_assertion_overrides(raw_overrides: Iterable[Mapping[str, JsonValue]]) -> tuple[AssertionOverride, ...]:
    """Parse assertion override declarations from a plan spec.

    Args:
        raw_overrides: Raw override objects from the plan spec.

    Returns:
        Parsed assertion override declarations.

    Raises:
        CatalogueError: If an override is malformed or duplicated.
    """
    overrides: list[AssertionOverride] = []
    seen: set[tuple[str, str]] = set()
    for index, raw_override in enumerate(raw_overrides):
        location = f"planSpec.assertionOverrides[{index}]"
        _reject_unknown_keys(raw_override, allowed_keys={"testCaseId", "assertionId", "reason"}, location=location)
        override = AssertionOverride(
            test_case_id=_required_string(raw_override, "testCaseId", location=location),
            assertion_id=_required_string(raw_override, "assertionId", location=location),
            reason=_required_string(raw_override, "reason", location=location),
        )
        key = (override.test_case_id, override.assertion_id)
        if key in seen:
            raise CatalogueError(f"{location} duplicates override for {override.test_case_id}.{override.assertion_id}")
        seen.add(key)
        overrides.append(override)
    return tuple(overrides)


def _copy_json_value(value: JsonValue) -> JsonValue:
    """Deep-copy a JSON value.

    Args:
        value: JSON value to copy.

    Returns:
        Independent JSON value copy.
    """
    return copy.deepcopy(value)


def _copy_json_mapping(value: Mapping[str, JsonValue]) -> JsonObject:
    """Deep-copy a JSON object mapping.

    Args:
        value: JSON object mapping to copy.

    Returns:
        Independent JSON object.
    """
    return {key: _copy_json_value(item) for key, item in value.items()}


def _catalogue_cases_by_id(catalogue: TestCatalogue) -> dict[str, CatalogueTestCase]:
    """Index catalogue cases by id and reject duplicates.

    Args:
        catalogue: Catalogue to index.

    Returns:
        Mapping of test case id to test case.

    Raises:
        CatalogueError: If a test case id is duplicated.
    """
    cases_by_id: dict[str, CatalogueTestCase] = {}
    for test_case in catalogue.test_cases:
        if test_case.test_case_id in cases_by_id:
            raise CatalogueError(f"Catalogue test case id '{test_case.test_case_id}' is a duplicate")
        cases_by_id[test_case.test_case_id] = test_case
    return cases_by_id


def _catalogue_capability_indexes(
    catalogue: TestCatalogue,
) -> tuple[dict[str, EndpointCapability], dict[EndpointRef, dict[str, EndpointCapability]]]:
    """Build capability indexes and validate catalogue capability metadata.

    Args:
        catalogue: Catalogue whose capability definitions should be indexed.

    Returns:
        Pair of capability-by-id and capability-by-endpoint-ref indexes.

    Raises:
        CatalogueError: If catalogue capability definitions are malformed or
            duplicate a capability id.
    """
    capabilities_by_id: dict[str, EndpointCapability] = {}
    capabilities_by_ref: dict[EndpointRef, dict[str, EndpointCapability]] = {}
    for capability in catalogue.capabilities:
        if not capability.capability_id.strip():
            raise CatalogueError("Catalogue capability id must be a non-empty string")
        if not capability.label.strip():
            raise CatalogueError(f"Catalogue capability '{capability.capability_id}' label must be non-empty")
        if not capability.description.strip():
            raise CatalogueError(f"Catalogue capability '{capability.capability_id}' description must be non-empty")
        if not capability.endpoint_refs:
            raise CatalogueError(
                f"Catalogue capability '{capability.capability_id}' must apply to at least one endpoint"
            )
        if capability.capability_id in capabilities_by_id:
            raise CatalogueError(f"Catalogue capability id '{capability.capability_id}' is a duplicate")
        capabilities_by_id[capability.capability_id] = capability
        for endpoint_ref in capability.endpoint_refs:
            capabilities_by_ref.setdefault(endpoint_ref, {})[capability.capability_id] = capability
    return capabilities_by_id, capabilities_by_ref


def _selected_capabilities_by_endpoint(
    endpoints: Iterable[ImplementedEndpoint],
    *,
    capabilities_by_id: Mapping[str, EndpointCapability],
    capabilities_by_ref: Mapping[EndpointRef, Mapping[str, EndpointCapability]],
    catalogue_capabilities: Iterable[EndpointCapability],
) -> tuple[dict[EndpointRef, set[str]], tuple[EndpointCapabilitySelection, ...]]:
    """Normalise endpoint capability selections for compilation.

    Args:
        endpoints: Participant-declared implemented endpoints.
        capabilities_by_id: Catalogue capabilities indexed by id.
        capabilities_by_ref: Catalogue capabilities indexed by endpoint ref.
        catalogue_capabilities: Capabilities in catalogue order.

    Returns:
        Pair of selected capability ids by endpoint ref and trace entries in
        participant endpoint order, then catalogue capability order.

    Raises:
        CatalogueError: If the plan spec selects an unknown capability or a
            capability that does not apply to the declared endpoint.
    """
    selected_by_ref: dict[EndpointRef, set[str]] = {}
    selections: list[EndpointCapabilitySelection] = []
    capability_order = tuple(catalogue_capabilities)
    for index, endpoint in enumerate(endpoints):
        endpoint_ref = EndpointRef(method=endpoint.method, path=endpoint.path)
        endpoint_capabilities = capabilities_by_ref.get(endpoint_ref, {})
        explicit_capability_ids = set(endpoint.capability_ids)
        for capability_id in endpoint.capability_ids:
            if capability_id not in capabilities_by_id:
                raise CatalogueError(
                    f"planSpec.implementedEndpoints[{index}].capabilities contains unknown capability '{capability_id}'"
                )
            if capability_id not in endpoint_capabilities:
                raise CatalogueError(
                    f"Capability '{capability_id}' does not apply to implemented endpoint "
                    f"{endpoint.method} {endpoint.path}"
                )

        normalised_ids = {
            capability.capability_id
            for capability in endpoint_capabilities.values()
            if capability.required or capability.capability_id in explicit_capability_ids
        }
        selected_by_ref[endpoint_ref] = normalised_ids
        for capability in capability_order:
            if endpoint_ref not in capability.endpoint_refs or capability.capability_id not in normalised_ids:
                continue
            selections.append(
                EndpointCapabilitySelection(
                    method=endpoint.method,
                    path=endpoint.path,
                    capability_id=capability.capability_id,
                    label=capability.label,
                    required=capability.required,
                )
            )
    return selected_by_ref, tuple(selections)


def _validate_test_case_capability_references(
    catalogue: TestCatalogue,
    capabilities_by_id: Mapping[str, EndpointCapability],
) -> None:
    """Validate test-case capability references against catalogue definitions.

    Args:
        catalogue: Catalogue containing the test cases to validate.
        capabilities_by_id: Catalogue capability definitions indexed by id.

    Raises:
        CatalogueError: If a test case references an unknown capability or a
            capability that cannot apply to any endpoint used by the case.
    """
    for test_case in catalogue.test_cases:
        required_capability_ids = test_case.applicability.required_capability_ids
        if not required_capability_ids:
            continue
        if not test_case.applicability.endpoint_refs:
            raise CatalogueError(
                f"Catalogue test case '{test_case.test_case_id}' cannot require capabilities without endpoint refs"
            )
        endpoint_refs = set(test_case.applicability.endpoint_refs)
        for capability_id in required_capability_ids:
            capability = capabilities_by_id.get(capability_id)
            if capability is None:
                raise CatalogueError(
                    f"Catalogue test case '{test_case.test_case_id}' references unknown capability '{capability_id}'"
                )
            if endpoint_refs.isdisjoint(capability.endpoint_refs):
                raise CatalogueError(
                    f"Catalogue test case '{test_case.test_case_id}' references capability "
                    f"'{capability_id}' outside its endpoint applicability"
                )


def _implemented_endpoint_refs(endpoints: Iterable[ImplementedEndpoint]) -> set[EndpointRef]:
    """Return exact endpoint references implemented by the participant.

    Args:
        endpoints: Participant-declared implemented endpoints.

    Returns:
        Set of exact method/path endpoint references.
    """
    return {EndpointRef(method=endpoint.method, path=endpoint.path) for endpoint in endpoints}


def _directly_applicable_case_ids(
    catalogue: TestCatalogue,
    spec: TestPlanSpec,
    implemented_refs: set[EndpointRef],
    selected_capabilities_by_ref: Mapping[EndpointRef, set[str]],
) -> tuple[set[str], dict[str, ApplicabilityDecision]]:
    """Select cases directly applicable to the profile and endpoints.

    Args:
        catalogue: Catalogue containing candidate test cases.
        spec: Plan spec with profile and endpoint selections.
        implemented_refs: Exact method/path refs implemented by the participant.
        selected_capabilities_by_ref: Normalised selected capability ids by
            implemented endpoint ref.

    Returns:
        Pair of selected case ids and initial per-case traceability decisions.
    """
    selected_ids: set[str] = set()
    decisions: dict[str, ApplicabilityDecision] = {}
    for test_case in catalogue.test_cases:
        if not test_case.applicability.security_profiles.applies_to(spec.security_profile):
            decisions[test_case.test_case_id] = ApplicabilityDecision(
                test_case_id=test_case.test_case_id,
                selected=False,
                reason=f"not applicable to security profile {spec.security_profile}",
            )
            continue
        endpoint_refs = set(test_case.applicability.endpoint_refs)
        if not endpoint_refs:
            selected_ids.add(test_case.test_case_id)
            decisions[test_case.test_case_id] = ApplicabilityDecision(
                test_case_id=test_case.test_case_id,
                selected=True,
                reason="applicable to selected profile and implemented endpoints",
            )
            continue
        if not endpoint_refs.issubset(implemented_refs):
            decisions[test_case.test_case_id] = ApplicabilityDecision(
                test_case_id=test_case.test_case_id,
                selected=False,
                reason="no matching implemented endpoint",
            )
            continue
        missing_capability_ids = sorted(
            set(test_case.applicability.required_capability_ids)
            - _selected_capability_ids_for_refs(endpoint_refs, selected_capabilities_by_ref)
        )
        if missing_capability_ids:
            decisions[test_case.test_case_id] = ApplicabilityDecision(
                test_case_id=test_case.test_case_id,
                selected=False,
                reason=f"required capability not selected: {', '.join(missing_capability_ids)}",
            )
            continue
        reason = (
            "applicable to selected profile, implemented endpoints, and selected capabilities"
            if test_case.applicability.required_capability_ids
            else "applicable to selected profile and implemented endpoints"
        )
        selected_ids.add(test_case.test_case_id)
        decisions[test_case.test_case_id] = ApplicabilityDecision(
            test_case_id=test_case.test_case_id,
            selected=True,
            reason=reason,
        )
    return selected_ids, decisions


def _selected_capability_ids_for_refs(
    endpoint_refs: Iterable[EndpointRef],
    selected_capabilities_by_ref: Mapping[EndpointRef, set[str]],
) -> set[str]:
    """Return capability ids selected across endpoint refs.

    Args:
        endpoint_refs: Endpoint refs used by a catalogue test case.
        selected_capabilities_by_ref: Normalised selected capability ids by
            implemented endpoint ref.

    Returns:
        Set of capability ids selected for any of the supplied endpoint refs.
    """
    selected_ids: set[str] = set()
    for endpoint_ref in endpoint_refs:
        selected_ids.update(selected_capabilities_by_ref.get(endpoint_ref, set()))
    return selected_ids


def _ordered_case_ids(
    cases_by_id: Mapping[str, CatalogueTestCase],
    direct_ids: set[str],
    *,
    profile: SecurityProfile,
) -> tuple[tuple[str, ...], dict[str, set[str]]]:
    """Topologically order directly selected cases and their dependencies.

    Args:
        cases_by_id: Catalogue cases indexed by id.
        direct_ids: Directly applicable case ids before dependency expansion.
        profile: Selected security profile used to validate dependency applicability.

    Returns:
        Ordered case ids and reverse dependency edges for traceability.

    Raises:
        CatalogueError: If dependencies are missing, cyclic, or profile-inapplicable.
    """
    ordered: list[str] = []
    dependency_edges: dict[str, set[str]] = {}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(case_id: str, *, dependent_id: str | None) -> None:
        """Visit a test case dependency node.

        Args:
            case_id: Test case id to visit.
            dependent_id: Test case that depends on ``case_id``, or ``None``
                for a directly applicable root.

        Raises:
            CatalogueError: If the dependency graph is invalid.
        """
        if case_id not in cases_by_id:
            raise CatalogueError(f"Catalogue dependency '{case_id}' does not exist")
        if dependent_id is not None:
            dependency_edges.setdefault(case_id, set()).add(dependent_id)
        if case_id in visited:
            return
        if case_id in visiting:
            raise CatalogueError(f"Catalogue dependency cycle includes '{case_id}'")
        test_case = cases_by_id[case_id]
        if not test_case.applicability.security_profiles.applies_to(profile):
            raise CatalogueError(f"Catalogue dependency '{case_id}' is not applicable to security profile {profile}")
        visiting.add(case_id)
        for dependency_id in test_case.dependencies:
            visit(dependency_id, dependent_id=case_id)
        visiting.remove(case_id)
        visited.add(case_id)
        ordered.append(case_id)

    for case_id in cases_by_id:
        if case_id in direct_ids:
            visit(case_id, dependent_id=None)
    return tuple(ordered), dependency_edges


def _reject_invalid_deselection(
    deselected_ids: Iterable[str],
    cases_by_id: Mapping[str, CatalogueTestCase],
    direct_ids: set[str],
) -> None:
    """Reject unknown, inapplicable, or mandatory case deselections.

    Args:
        deselected_ids: Test case ids requested for deselection.
        cases_by_id: Catalogue cases indexed by id.
        direct_ids: Case ids directly selected before dependency expansion.

    Raises:
        CatalogueError: If any deselection is invalid.
    """
    for case_id in deselected_ids:
        if case_id not in cases_by_id:
            raise CatalogueError(f"Cannot deselect unknown test case '{case_id}'")
        if case_id not in direct_ids:
            raise CatalogueError(f"Cannot deselect inapplicable test case '{case_id}'")
        if cases_by_id[case_id].mandatory:
            raise CatalogueError(f"Mandatory applicable test case '{case_id}' cannot be deselected")


def _runtime_input_snapshot(
    cases_by_id: Mapping[str, CatalogueTestCase],
    ordered_ids: Iterable[str],
    runtime_inputs: Mapping[str, JsonValue],
) -> tuple[RuntimeInputTrace, ...]:
    """Validate runtime inputs and build a trace-safe snapshot.

    Args:
        cases_by_id: Catalogue cases indexed by id.
        ordered_ids: Compiled test case ids.
        runtime_inputs: Runtime values supplied by the plan spec.

    Returns:
        Runtime input trace entries in first-requirement order.

    Raises:
        CatalogueError: If required values are missing or typed incorrectly.
    """
    requirements = _combined_runtime_requirements(cases_by_id, ordered_ids)
    traces: list[RuntimeInputTrace] = []
    for requirement in requirements:
        provided = requirement.input_id in runtime_inputs and runtime_inputs[requirement.input_id] is not None
        if requirement.required and not provided:
            raise CatalogueError(f"Required runtime input '{requirement.input_id}' is missing")
        value = runtime_inputs.get(requirement.input_id)
        if provided and value is not None:
            _validate_runtime_input_value(requirement, value)
        traces.append(
            RuntimeInputTrace(
                input_id=requirement.input_id,
                input_type=requirement.input_type,
                required=requirement.required,
                sensitive=requirement.sensitive,
                provided=provided,
                value=None if requirement.sensitive or value is None else _copy_json_value(value),
            )
        )
    return tuple(traces)


def _combined_runtime_requirements(
    cases_by_id: Mapping[str, CatalogueTestCase],
    ordered_ids: Iterable[str],
) -> tuple[RuntimeInputRequirement, ...]:
    """Combine runtime input requirements from compiled test cases.

    Args:
        cases_by_id: Catalogue cases indexed by id.
        ordered_ids: Compiled test case ids.

    Returns:
        De-duplicated runtime requirements in first-use order.

    Raises:
        CatalogueError: If the same runtime input id has conflicting metadata.
    """
    requirements_by_id: dict[str, RuntimeInputRequirement] = {}
    ordered_requirements: list[RuntimeInputRequirement] = []
    for case_id in ordered_ids:
        for requirement in cases_by_id[case_id].runtime_input_requirements:
            if requirement.input_type not in _SUPPORTED_RUNTIME_INPUT_TYPES:
                raise CatalogueError(
                    f"Runtime input '{requirement.input_id}' has unsupported type '{requirement.input_type}'"
                )
            existing = requirements_by_id.get(requirement.input_id)
            if existing is None:
                requirements_by_id[requirement.input_id] = requirement
                ordered_requirements.append(requirement)
                continue
            if existing != requirement:
                raise CatalogueError(f"Runtime input '{requirement.input_id}' has conflicting catalogue requirements")
    return tuple(ordered_requirements)


def _validate_runtime_input_value(requirement: RuntimeInputRequirement, value: JsonValue) -> None:
    """Validate a runtime value against a requirement type.

    Args:
        requirement: Catalogue runtime input requirement.
        value: Supplied runtime value.

    Raises:
        CatalogueError: If the value does not match the required type.
    """
    if requirement.input_type in {"string", "file_reference"}:
        if not isinstance(value, str) or not value.strip():
            raise CatalogueError(f"Runtime input '{requirement.input_id}' must be a non-empty string")
        return
    if requirement.input_type == "url":
        if not isinstance(value, str) or not value.strip():
            raise CatalogueError(f"Runtime input '{requirement.input_id}' must be a non-empty HTTPS URL")
        try:
            validate_https_url(value.strip(), label=f"Runtime input '{requirement.input_id}'")
        except HttpsUrlValidationError as error:
            raise CatalogueError(str(error)) from error
        return
    if requirement.input_type == "number":
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise CatalogueError(f"Runtime input '{requirement.input_id}' must be a JSON number")
        return
    if requirement.input_type == "boolean":
        if not isinstance(value, bool):
            raise CatalogueError(f"Runtime input '{requirement.input_id}' must be a JSON boolean")
        return


def _validate_assertion_overrides(
    cases_by_id: Mapping[str, CatalogueTestCase],
    ordered_ids: Iterable[str],
    overrides: Iterable[AssertionOverride],
) -> tuple[str, ...]:
    """Validate assertion overrides and return non-certifying reasons.

    Args:
        cases_by_id: Catalogue cases indexed by id.
        ordered_ids: Compiled test case ids.
        overrides: Assertion override declarations from the plan spec.

    Returns:
        Non-certifying reasons derived from accepted overrides.

    Raises:
        CatalogueError: If an override targets an unknown case or assertion.
    """
    selected_ids = set(ordered_ids)
    reasons: list[str] = []
    for override in overrides:
        if override.test_case_id not in selected_ids:
            raise CatalogueError(f"Assertion override targets uncompiled test case '{override.test_case_id}'")
        assertion_ids = {assertion.assertion_id for assertion in cases_by_id[override.test_case_id].assertions}
        if override.assertion_id not in assertion_ids:
            raise CatalogueError(
                f"Assertion override targets unknown assertion '{override.test_case_id}.{override.assertion_id}'"
            )
        reasons.append(
            f"Assertion override supplied for {override.test_case_id}.{override.assertion_id}: {override.reason}"
        )
    return tuple(reasons)


def _traceability_decisions(
    base_decisions: Mapping[str, ApplicabilityDecision],
    selected_ids: set[str],
    dependency_edges: Mapping[str, set[str]],
    deselected_ids: Iterable[str],
) -> tuple[ApplicabilityDecision, ...]:
    """Build final traceability decisions after dependencies and deselection.

    Args:
        base_decisions: Initial direct applicability decisions.
        selected_ids: Final selected test case ids.
        dependency_edges: Reverse dependency mapping.
        deselected_ids: Test case ids deliberately removed from the plan.

    Returns:
        Final applicability decisions in catalogue order.
    """
    deselected_set = set(deselected_ids)
    decisions: list[ApplicabilityDecision] = []
    for case_id, decision in base_decisions.items():
        if case_id in deselected_set:
            decisions.append(
                ApplicabilityDecision(
                    test_case_id=case_id,
                    selected=False,
                    reason="deselected by participant",
                    dependency_of=tuple(sorted(dependency_edges.get(case_id, set()))),
                )
            )
            continue
        if case_id in selected_ids:
            dependency_of = tuple(sorted(dependency_edges.get(case_id, set())))
            reason = "included as dependency" if dependency_of and not decision.selected else decision.reason
            decisions.append(
                ApplicabilityDecision(
                    test_case_id=case_id,
                    selected=True,
                    reason=reason,
                    dependency_of=dependency_of,
                )
            )
            continue
        decisions.append(decision)
    return tuple(decisions)
