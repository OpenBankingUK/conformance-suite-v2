"""Immutable typed models populated only from schema-valid configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import NewType

from conformance.json_types import JsonValue

StableId = NewType("StableId", str)
"""Opaque stable identifier whose wire constraints are owned by JSON Schema."""

Sha256Digest = NewType("Sha256Digest", str)
"""Exact-byte SHA-256 digest whose wire constraints are owned by JSON Schema."""


@dataclass(frozen=True, slots=True)
class ToolRelease:
    """One conformance tool release compatible with a suite release."""

    id: StableId
    version: str


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    """Content-addressed artefact bound into a suite release."""

    id: StableId
    kind: StableId
    media_type: str
    schema_version: str
    uri: str
    digest: Sha256Digest


@dataclass(frozen=True, slots=True)
class SuiteRelease:
    """OBL-authored immutable binding of compatible suite artefacts."""

    schema_version: str
    document_type: str
    id: StableId
    release_version: str
    published_at: str
    tool_releases: tuple[ToolRelease, ...]
    artifacts: tuple[ArtifactReference, ...]


class HttpMethod(StrEnum):
    """HTTP methods supported by configuration-driven operation inventories."""

    DELETE = "DELETE"
    GET = "GET"
    PATCH = "PATCH"
    POST = "POST"
    PUT = "PUT"


class RequirementTargetType(StrEnum):
    """Kinds of catalogue object that a requirements rule can require."""

    ENDPOINT = "endpoint"
    PREDEFINED_INPUT = "predefined-input"


@dataclass(frozen=True, slots=True)
class SpecificationReference:
    """Specification and functional scope governed by a requirements catalogue."""

    id: StableId
    version: str
    requirements_scope: StableId


@dataclass(frozen=True, slots=True)
class NormativeReference:
    """Stable citation into normative Open Banking material."""

    id: StableId
    title: str
    uri: str
    section: str


@dataclass(frozen=True, slots=True)
class TechnicalSource:
    """Content-addressed technical source used to define executable operations."""

    id: StableId
    title: str
    uri: str
    digest: Sha256Digest


@dataclass(frozen=True, slots=True)
class Capability:
    """Participant-selectable conditional capability and its required endpoints."""

    id: StableId
    name: str
    description: str
    selection: str
    required_endpoint_ids: tuple[StableId, ...]
    required_capability_ids: tuple[StableId, ...] = ()


@dataclass(frozen=True, slots=True)
class Endpoint:
    """Technical operation identified independently from normative obligation."""

    id: StableId
    method: HttpMethod
    path: str
    operation_id: str | None
    source_id: StableId
    source_pointer: str


@dataclass(frozen=True, slots=True)
class StandingOrderFrequency:
    """Logical v4 standing-order frequency value, before request rendering."""

    frequency_type: str
    count_per_period: int | None
    point_in_time: str | None


type PredefinedInputValue = str | StandingOrderFrequency
"""Immutable logical value types supported by predefined catalogue inputs."""


@dataclass(frozen=True, slots=True)
class PredefinedInput:
    """Standards-owned logical input permitted for a capability."""

    id: StableId
    label: str
    description: str
    value_type: StableId
    required_for_capability_ids: tuple[StableId, ...]
    sensitivity: str
    example_value: PredefinedInputValue
    default_value: PredefinedInputValue | None = None


@dataclass(frozen=True, slots=True)
class RequirementRule:
    """One explicit conditional requirement rule from the initial vocabulary."""

    type: str
    capability_id: StableId
    target_type: RequirementTargetType
    target_id: StableId


@dataclass(frozen=True, slots=True)
class Requirement:
    """One normative obligation with explicit rule and citations."""

    id: StableId
    statement: str
    rule: RequirementRule
    normative_reference_ids: tuple[StableId, ...]
    assessment: str = "tested"


@dataclass(frozen=True, slots=True)
class RequirementsCatalogue:
    """Immutable Standards-owned requirements for one specification scope."""

    schema_version: str
    document_type: str
    id: StableId
    scheme: StableId
    specification: SpecificationReference
    normative_references: tuple[NormativeReference, ...]
    technical_sources: tuple[TechnicalSource, ...]
    capabilities: tuple[Capability, ...]
    endpoints: tuple[Endpoint, ...]
    predefined_inputs: tuple[PredefinedInput, ...]
    requirements: tuple[Requirement, ...]


@dataclass(frozen=True, slots=True)
class RequestInputBinding:
    """Test-owned mapping from a logical input to a request location."""

    input_id: StableId
    type: str
    target: str
    transform: StableId


@dataclass(frozen=True, slots=True)
class RequestModification:
    """Protocol-neutral modification applied to a generated test request."""

    id: StableId
    operation: str
    location: str
    target: str | None = None
    value: str | None = None
    generator: StableId | None = None


@dataclass(frozen=True, slots=True)
class RequestStateBinding:
    """Binding from a preceding test output into a dependent request."""

    output_id: StableId
    type: str
    target: str


@dataclass(frozen=True, slots=True)
class TestOutput:
    """Named runtime value produced by a test for dependent definitions."""

    id: StableId
    source: str
    json_pointer: str | None
    sensitive: bool


@dataclass(frozen=True, slots=True)
class TestRequest:
    """Operation and logical-input bindings used by one test definition."""

    endpoint_id: StableId
    input_bindings: tuple[RequestInputBinding, ...]
    modifications: tuple[RequestModification, ...]
    state_bindings: tuple[RequestStateBinding, ...] = ()
    content_type: str | None = None
    transport_profile: StableId | None = None
    authorization_profile: StableId | None = None


@dataclass(frozen=True, slots=True)
class TestAssertion:
    """One strict assertion declared by a test definition."""

    id: StableId
    type: str
    expected_status: int | None = None
    expected_statuses: tuple[int, ...] | None = None
    schema_ref: str | None = None
    schema_source_id: StableId | None = None
    header_name: str | None = None
    json_pointer: str | None = None
    expected_value: str | None = None


@dataclass(frozen=True, slots=True)
class TestDefinition:
    """Reusable executable test description with explicit requirement coverage."""

    id: StableId
    name: str
    description: str
    purpose: str
    capability_id: StableId
    covered_requirement_ids: tuple[StableId, ...]
    dependencies: tuple[StableId, ...]
    request: TestRequest
    assertions: tuple[TestAssertion, ...]
    outputs: tuple[TestOutput, ...] = ()


@dataclass(frozen=True, slots=True)
class TestDefinitionCatalogue:
    """Immutable test-author-owned catalogue for reusable definitions."""

    schema_version: str
    document_type: str
    id: StableId
    requirements_catalogue_id: StableId
    test_definitions: tuple[TestDefinition, ...]


@dataclass(frozen=True, slots=True)
class ParticipantInput:
    """One participant-supplied value for a predefined logical input."""

    input_id: StableId
    value: PredefinedInputValue


@dataclass(frozen=True, slots=True)
class ParticipantExecutionConfiguration:
    """Participant environment values used after scope compilation.

    The compiler deliberately ignores this configuration. Trusted catalogues
    remain authoritative for requirements, test selection, and logical input
    bindings; these values only configure the compatibility execution engine.
    """

    security_environment: Mapping[str, JsonValue]
    compatibility_runtime_inputs: Mapping[str, JsonValue]
    dynamic_client_registration: Mapping[str, JsonValue]
    metadata: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ParticipantPlan:
    """Participant intent and execution environment for one trusted scope."""

    schema_version: str
    document_type: str
    id: StableId
    suite_release_id: StableId
    scheme: StableId
    specification: SpecificationReference
    security_profile: str
    selected_capability_ids: tuple[StableId, ...]
    predefined_inputs: tuple[ParticipantInput, ...]
    execution_configuration: ParticipantExecutionConfiguration | None = None


class SelectionOrigin(StrEnum):
    """How a resolved object entered the selected scope."""

    EXPLICIT = "explicit"
    INFERRED = "inferred"


class InputResolutionSource(StrEnum):
    """Where a resolved logical input value came from."""

    PARTICIPANT = "participant"
    DEFAULT = "default"


class FindingSeverity(StrEnum):
    """Severity of a participant-plan compilation finding."""

    ERROR = "error"
    WARNING = "warning"


class FindingSourceDocument(StrEnum):
    """Configuration document addressed by a compilation finding pointer."""

    PARTICIPANT_PLAN = "participant-plan"
    REQUIREMENTS_CATALOGUE = "requirements-catalogue"
    RESOLVED_PLAN = "resolved-plan"


@dataclass(frozen=True, slots=True)
class ResolutionReason:
    """Stable explanation and source IDs for one compiler decision."""

    code: StableId
    source_ids: tuple[StableId, ...]


@dataclass(frozen=True, slots=True)
class ResolvedCapability:
    """Capability included in resolved participant scope."""

    id: StableId
    origin: SelectionOrigin
    reasons: tuple[ResolutionReason, ...]


@dataclass(frozen=True, slots=True)
class ResolvedEndpoint:
    """Endpoint inferred by applying immutable catalogue requirements."""

    id: StableId
    origin: SelectionOrigin
    requirement_ids: tuple[StableId, ...]
    reasons: tuple[ResolutionReason, ...]


@dataclass(frozen=True, slots=True)
class ResolvedPredefinedInput:
    """Trace-safe logical input selected and resolved for execution."""

    id: StableId
    source: InputResolutionSource
    value: PredefinedInputValue | None
    redacted: bool
    requirement_ids: tuple[StableId, ...]
    reasons: tuple[ResolutionReason, ...]


@dataclass(frozen=True, slots=True)
class ResolvedRequirement:
    """Applicable immutable requirement and its normative references."""

    id: StableId
    capability_id: StableId
    target_type: RequirementTargetType
    target_id: StableId
    normative_reference_ids: tuple[StableId, ...]
    reasons: tuple[ResolutionReason, ...]
    assessment: str = "tested"


@dataclass(frozen=True, slots=True)
class ResolvedTestInstance:
    """Deterministic compiled instance of one reusable test definition."""

    id: StableId
    test_definition_id: StableId
    dependency_ids: tuple[StableId, ...]
    covered_requirement_ids: tuple[StableId, ...]
    reasons: tuple[ResolutionReason, ...]


@dataclass(frozen=True, slots=True)
class CompilationFinding:
    """Stable validation or policy finding retained in resolved output."""

    code: StableId
    severity: FindingSeverity
    message: str
    source_document: FindingSourceDocument
    instance_path: str
    related_ids: tuple[StableId, ...]


@dataclass(frozen=True, slots=True)
class ResolvedPlanProvenance:
    """Exact release and configuration sources used for compilation."""

    participant_plan_id: StableId
    suite_release_id: StableId
    suite_release_version: str
    suite_published_at: str
    requirements_catalogue_id: StableId
    test_definition_catalogue_id: StableId
    tool_releases: tuple[ToolRelease, ...]
    artifacts: tuple[ArtifactReference, ...]


@dataclass(frozen=True, slots=True)
class ResolvedPlan:
    """Generated, inspectable result of participant-plan compilation."""

    schema_version: str
    document_type: str
    id: StableId
    selection_valid: bool
    scheme: StableId
    specification: SpecificationReference
    security_profile: str
    capabilities: tuple[ResolvedCapability, ...]
    endpoints: tuple[ResolvedEndpoint, ...]
    requirements: tuple[ResolvedRequirement, ...]
    predefined_inputs: tuple[ResolvedPredefinedInput, ...]
    test_instances: tuple[ResolvedTestInstance, ...]
    findings: tuple[CompilationFinding, ...]
    provenance: ResolvedPlanProvenance


class EvidenceMode(StrEnum):
    """Evidence handling modes supported by the initial runner boundary."""

    MASKED = "masked"


class RequestBaseUrlSource(StrEnum):
    """Allowlisted runtime source used to form an outbound request URL."""

    RESOURCE = "resource"
    DISCOVERY = "discovery"
    TOKEN = "token"  # noqa: S105 - protocol endpoint vocabulary, not a credential
    DCR_REGISTRATION = "dcr-registration"
    DCR_MANAGEMENT = "dcr-management"


class GeneratedValueStrategy(StrEnum):
    """Allowlisted runtime value generators copied from catalogue requests."""

    UUID4 = "uuid4"
    UUID4_HEX = "uuid4-hex"
    INVALID_RESOURCE_ID = "invalid-resource-id"
    LEGACY_INVALID_CONSENT_ID = "legacy-invalid-consent-id"
    INVALID_ACCESS_TOKEN = "invalid-access-token"  # noqa: S105 - generator ID, not a credential
    NEXT_DAY_DATE_OFFSET = "next-day-date-offset"
    NEXT_DAY_DATE_UTC = "next-day-date-utc"
    NEXT_DAY_DATE_TIME_OFFSET = "next-day-date-time-offset"
    NEXT_DAY_DATE_TIME_OFFSET_MILLISECONDS = "next-day-date-time-offset-milliseconds"
    NEXT_DAY_DATE_TIME_UTC = "next-day-date-time-utc"
    NEXT_DAY_DATE_TIME_UTC_MILLISECONDS = "next-day-date-time-utc-milliseconds"


class GeneratedHeaderValue(StrEnum):
    """Allowlisted generated outbound header strategies."""

    UUID4 = "uuid4"


class DetachedJwsProfile(StrEnum):
    """Open Banking detached-JWS profiles supported by the runtime."""

    LEGACY_B64_FALSE = "legacy-b64-false"
    OB_V3_1_4_PLUS = "ob-v3.1.4+"


class DetachedJwsOmittedClaim(StrEnum):
    """Open Banking protected-header aliases permitted in negative tests."""

    IAT = "iat"
    ISS = "iss"
    TAN = "tan"


class TokenEndpointAuthSource(StrEnum):
    """Allowlisted source of OAuth token-endpoint client authentication."""

    FAPI_SIGNING = "fapi-signing"


class DetachedJwsSource(StrEnum):
    """Allowlisted source of detached-JWS signing material."""

    FAPI_SIGNING = "fapi-signing"


class ResponseSignatureSource(StrEnum):
    """Allowlisted source of response detached-JWS verification keys."""

    DISCOVERY_JWKS = "discovery-jwks"


@dataclass(frozen=True, slots=True)
class ExecutionManifestHeader:
    """One literal, runtime-input, or generated outbound header template."""

    name: str
    literal_value: str | None = None
    runtime_input_ref: str | None = None
    generated_value: GeneratedHeaderValue | None = None

    def __post_init__(self) -> None:
        """Reject ambiguous header value sources in directly constructed models."""
        if sum(value is not None for value in (self.literal_value, self.runtime_input_ref, self.generated_value)) != 1:
            raise ValueError("Execution manifest header requires exactly one value source")


@dataclass(frozen=True, slots=True)
class ExecutionDetachedJws:
    """Runtime detached-JWS signing instructions for one exact request."""

    profile: DetachedJwsProfile
    omitted_claims: tuple[DetachedJwsOmittedClaim, ...] = ()
    source: DetachedJwsSource = DetachedJwsSource.FAPI_SIGNING


@dataclass(frozen=True, slots=True)
class ExecutionTokenEndpointAuth:
    """Runtime OAuth client-authentication instructions for a form request."""

    source: TokenEndpointAuthSource


@dataclass(frozen=True, slots=True)
class ExecutionResponseSignature:
    """Runtime response-signature verification instructions."""

    source: ResponseSignatureSource


@dataclass(frozen=True, slots=True)
class ExecutionPsuAuthorization:
    """Explicit helper exchanges nested under an owning consent request."""

    authorization_step_id: StableId
    authorization_step_name: str
    token_step_id: StableId
    token_id: StableId
    flow_label: str


def _immutable_json_value(value: JsonValue) -> JsonValue:
    """Detach and recursively freeze configuration-owned JSON data."""
    if isinstance(value, Mapping):
        frozen = {key: _immutable_json_value(item) for key, item in value.items()}
        return MappingProxyType(frozen)  # type: ignore[return-value]  # immutable runtime representation
    if isinstance(value, tuple | list):
        return tuple(_immutable_json_value(item) for item in value)  # type: ignore[return-value]  # immutable runtime representation
    return value


@dataclass(frozen=True, slots=True)
class ExecutionManifestInput:
    """Resolved logical input reference available to executable steps."""

    id: StableId
    source: InputResolutionSource
    value: PredefinedInputValue | None
    redacted: bool


@dataclass(frozen=True, slots=True)
class ExecutionManifestRequest:
    """Exact operation and logical bindings for one executable HTTP step."""

    method: HttpMethod
    path: str
    input_bindings: tuple[RequestInputBinding, ...]
    modifications: tuple[RequestModification, ...]
    state_bindings: tuple[RequestStateBinding, ...] = ()
    content_type: str | None = None
    transport_profile: StableId | None = None
    authorization_profile: StableId | None = None
    base_url_source: RequestBaseUrlSource | None = None
    query_templates: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    header_templates: tuple[ExecutionManifestHeader, ...] = ()
    json_body_template: JsonValue | None = None
    form_body_template: Mapping[str, str] | None = None
    runtime_input_refs: tuple[str, ...] = ()
    generated_values: Mapping[str, GeneratedValueStrategy] = field(default_factory=lambda: MappingProxyType({}))
    required_token_id: StableId | None = None
    required_token_scope: str | None = None
    produced_token_id: StableId | None = None
    invalidate_produced_authorization_token: bool = False
    detached_jws: ExecutionDetachedJws | None = None
    token_endpoint_auth: ExecutionTokenEndpointAuth | None = None
    response_signature: ExecutionResponseSignature | None = None
    psu_authorization: ExecutionPsuAuthorization | None = None
    required_psu_authorization_step_id: StableId | None = None

    def __post_init__(self) -> None:
        """Detach caller-owned templates and expose immutable mappings."""
        object.__setattr__(
            self,
            "query_templates",
            MappingProxyType(dict(self.query_templates)),
        )
        object.__setattr__(
            self,
            "generated_values",
            MappingProxyType(dict(self.generated_values)),
        )
        if self.form_body_template is not None:
            object.__setattr__(
                self,
                "form_body_template",
                MappingProxyType(dict(self.form_body_template)),
            )
        if self.json_body_template is not None:
            object.__setattr__(
                self,
                "json_body_template",
                _immutable_json_value(self.json_body_template),
            )


@dataclass(frozen=True, slots=True)
class ExecutionManifestAssertion:
    """Resolved assertion evaluated for one executable step."""

    id: StableId
    type: str
    expected_status: int | None = None
    expected_statuses: tuple[int, ...] | None = None
    schema_ref: str | None = None
    schema_source_id: StableId | None = None
    header_name: str | None = None
    json_pointer: str | None = None
    expected_value: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionEvidencePolicy:
    """Runner instructions for retaining safe request and response evidence."""

    request: EvidenceMode
    response: EvidenceMode


@dataclass(frozen=True, slots=True)
class ExecutionManifestStep:
    """One ordered runner-facing test instance and its executable request."""

    id: StableId
    test_instance_id: StableId
    test_definition_id: StableId
    name: str
    dependency_ids: tuple[StableId, ...]
    covered_requirement_ids: tuple[StableId, ...]
    request: ExecutionManifestRequest
    assertions: tuple[ExecutionManifestAssertion, ...]
    outputs: tuple[TestOutput, ...]
    evidence: ExecutionEvidencePolicy


@dataclass(frozen=True, slots=True)
class ExecutionManifestProvenance:
    """Immutable source provenance copied from the resolved plan."""

    resolved_plan_id: StableId
    participant_plan_id: StableId
    suite_release_id: StableId
    suite_release_version: str
    suite_published_at: str
    requirements_catalogue_id: StableId
    test_definition_catalogue_id: StableId
    tool_releases: tuple[ToolRelease, ...]
    artifacts: tuple[ArtifactReference, ...]


@dataclass(frozen=True, slots=True)
class ExecutionManifest:
    """Generated immutable instructions consumed by the runner boundary."""

    schema_version: str
    document_type: str
    id: StableId
    security_profile: str
    inputs: tuple[ExecutionManifestInput, ...]
    steps: tuple[ExecutionManifestStep, ...]
    provenance: ExecutionManifestProvenance
