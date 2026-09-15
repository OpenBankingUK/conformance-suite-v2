"""Immutable typed models populated only from schema-valid configuration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import NewType

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
    operation_id: str
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
class TestRequest:
    """Operation and logical-input bindings used by one test definition."""

    endpoint_id: StableId
    input_bindings: tuple[RequestInputBinding, ...]
    modifications: tuple[RequestModification, ...]


@dataclass(frozen=True, slots=True)
class TestAssertion:
    """One strict assertion declared by a test definition."""

    id: StableId
    type: str
    expected_status: int | None = None
    expected_statuses: tuple[int, ...] | None = None
    schema_ref: str | None = None
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
class ParticipantPlan:
    """Participant intent for the configuration-driven walking skeleton."""

    schema_version: str
    document_type: str
    id: StableId
    suite_release_id: StableId
    scheme: StableId
    specification: SpecificationReference
    security_profile: str
    selected_capability_ids: tuple[StableId, ...]
    predefined_inputs: tuple[ParticipantInput, ...]


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


@dataclass(frozen=True, slots=True)
class ExecutionManifestInput:
    """Resolved non-sensitive logical input available to executable steps."""

    id: StableId
    source: InputResolutionSource
    value: PredefinedInputValue


@dataclass(frozen=True, slots=True)
class ExecutionManifestRequest:
    """Exact operation and logical bindings for one executable HTTP step."""

    method: HttpMethod
    path: str
    input_bindings: tuple[RequestInputBinding, ...]
    modifications: tuple[RequestModification, ...]


@dataclass(frozen=True, slots=True)
class ExecutionManifestAssertion:
    """Resolved assertion evaluated for one executable step."""

    id: StableId
    type: str
    expected_status: int | None = None
    expected_statuses: tuple[int, ...] | None = None
    schema_ref: str | None = None
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
