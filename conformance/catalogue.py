"""Catalogue-domain model and compiler for endpoint-selected conformance plans."""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar, Literal, cast

from conformance.json_types import JsonValue
from conformance.url_validation import HttpsUrlValidationError, validate_https_url


class CatalogueError(ValueError):
    """Raised when a catalogue, plan spec, or compilation request is invalid."""


type SecurityProfile = Literal["all", "fapi1-advanced", "fapi2"]
"""Security profile selectors supported by catalogue applicability rules."""

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
