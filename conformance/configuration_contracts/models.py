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
    """HTTP methods supported by the walking-skeleton operation inventory."""

    GET = "GET"
    POST = "POST"


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
class Capability:
    """Participant-selectable conditional capability and its required endpoints."""

    id: StableId
    name: str
    description: str
    selection: str
    required_endpoint_ids: tuple[StableId, ...]


@dataclass(frozen=True, slots=True)
class Endpoint:
    """Technical operation identified independently from normative obligation."""

    id: StableId
    method: HttpMethod
    path: str
    operation_id: str


@dataclass(frozen=True, slots=True)
class StandingOrderFrequency:
    """Logical v4 standing-order frequency value, before request rendering."""

    frequency_type: str
    count_per_period: int | None
    point_in_time: str | None


@dataclass(frozen=True, slots=True)
class PredefinedInput:
    """Standards-owned logical input permitted for a capability."""

    id: StableId
    label: str
    description: str
    value_type: StableId
    required_for_capability_ids: tuple[StableId, ...]
    sensitivity: str
    example_value: StandingOrderFrequency


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
class TestRequest:
    """Operation and logical-input bindings used by one test definition."""

    endpoint_id: StableId
    input_bindings: tuple[RequestInputBinding, ...]


@dataclass(frozen=True, slots=True)
class TestAssertion:
    """One strict assertion declared by a test definition."""

    id: StableId
    type: str
    expected_status: int


@dataclass(frozen=True, slots=True)
class TestDefinition:
    """Reusable executable test description with explicit requirement coverage."""

    id: StableId
    name: str
    description: str
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
class ParticipantSpecification:
    """Functional and protocol boundary selected by a participant."""

    id: StableId
    version: str
    requirements_scope: StableId


@dataclass(frozen=True, slots=True)
class ParticipantInput:
    """One participant-supplied value for a predefined logical input."""

    input_id: StableId
    value: StandingOrderFrequency


@dataclass(frozen=True, slots=True)
class ParticipantPlan:
    """Participant-authored intent for the walking-skeleton compiler."""

    schema_version: str
    document_type: str
    id: StableId
    suite_release_id: StableId
    scheme: StableId
    specification: ParticipantSpecification
    security_profile: StableId
    selected_capability_ids: tuple[StableId, ...]
    predefined_inputs: tuple[ParticipantInput, ...]


class ResolutionSource(StrEnum):
    """How a resolved compiler object entered the effective plan."""

    EXPLICIT = "explicit"
    INFERRED = "inferred"
    SUPPLIED = "supplied"
    DEPENDENCY = "dependency"


class CompilerFindingSeverity(StrEnum):
    """Severity of one deterministic participant-plan compiler finding."""

    ERROR = "error"
    WARNING = "warning"


class CompilerFindingCode(StrEnum):
    """Stable machine-readable findings emitted by participant resolution."""

    INPUT_DUPLICATE = "compiler.input.duplicate"
    INPUT_MISSING = "compiler.input.missing"
    INPUT_NOT_APPLICABLE = "compiler.input.not-applicable"
    INPUT_UNKNOWN = "compiler.input.unknown"
    SELECTION_DUPLICATE = "compiler.selection.duplicate"
    SELECTION_EMPTY = "compiler.selection.empty"
    SELECTION_UNKNOWN = "compiler.selection.unknown"
    SCOPE_MISMATCH = "compiler.scope.mismatch"
    SUITE_RELEASE_MISMATCH = "compiler.suite-release.mismatch"
    REQUIREMENT_COVERAGE_MISSING = "compiler.requirement.coverage-missing"
    RELEASE_ARTIFACT_CONTENT_MISMATCH = "compiler.release.artifact-content-mismatch"
    RELEASE_ARTIFACT_MISSING = "compiler.release.artifact-missing"


@dataclass(frozen=True, slots=True)
class ResolvedSelection:
    """One selected capability, endpoint, or applicable requirement."""

    id: StableId
    source: ResolutionSource
    source_ids: tuple[StableId, ...]


@dataclass(frozen=True, slots=True)
class ResolvedInput:
    """One logical input resolved for an applicable requirement."""

    id: StableId
    value_type: StableId
    sensitivity: str
    source: ResolutionSource
    source_ids: tuple[StableId, ...]
    value: StandingOrderFrequency | None


@dataclass(frozen=True, slots=True)
class ResolvedTestInstance:
    """One deterministic test-definition instance selected for execution."""

    id: StableId
    test_definition_id: StableId
    source: ResolutionSource
    source_ids: tuple[StableId, ...]
    dependency_instance_ids: tuple[StableId, ...]
    covered_requirement_ids: tuple[StableId, ...]


@dataclass(frozen=True, slots=True)
class CompilerFinding:
    """One stable validation or policy finding emitted during resolution."""

    code: StableId
    severity: CompilerFindingSeverity
    message: str
    instance_path: str
    related_ids: tuple[StableId, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedPlanProvenance:
    """Immutable release and source-document provenance for a resolved plan."""

    suite_release_id: StableId
    suite_release_version: str
    suite_release_published_at: str
    tool_releases: tuple[ToolRelease, ...]
    artifacts: tuple[ArtifactReference, ...]
    participant_plan_id: StableId
    participant_plan_digest: Sha256Digest
    suite_release_digest: Sha256Digest
    requirements_catalogue_id: StableId
    test_definition_catalogue_id: StableId


@dataclass(frozen=True, slots=True)
class ResolvedPlan:
    """Deterministic, inspectable output of participant-plan resolution."""

    schema_version: str
    document_type: str
    id: StableId
    valid: bool
    compilation_allowed: bool
    certification_eligible: bool
    security_profile: StableId
    selected_capabilities: tuple[ResolvedSelection, ...]
    selected_endpoints: tuple[ResolvedSelection, ...]
    applicable_requirements: tuple[ResolvedSelection, ...]
    resolved_inputs: tuple[ResolvedInput, ...]
    test_instances: tuple[ResolvedTestInstance, ...]
    findings: tuple[CompilerFinding, ...]
    provenance: ResolvedPlanProvenance
