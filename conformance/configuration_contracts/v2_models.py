"""Immutable models for the consolidated schema-version 2.0 contracts."""

from __future__ import annotations

from dataclasses import dataclass

from conformance.configuration_contracts.models import (
    ArtifactReference,
    Capability,
    CompilationFinding,
    Endpoint,
    ExecutionEvidencePolicy,
    ExecutionManifestAssertion,
    ExecutionManifestInput,
    ExecutionManifestRequest,
    InputResolutionSource,
    ParticipantExecutionConfiguration,
    ParticipantInput,
    PredefinedInput,
    PredefinedInputValue,
    ResolutionReason,
    ResolvedCapability,
    SelectionOrigin,
    StableId,
    TechnicalSource,
    TestAssertion,
    TestOutput,
    ToolRelease,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class TestDefinitionRequest(ExecutionManifestRequest):
    """Complete executable request linked to its catalogue endpoint."""

    endpoint_id: StableId


@dataclass(frozen=True, slots=True)
class Specification:
    """Specification identity and executable test scope owned by a catalogue."""

    id: StableId
    version: str
    test_scope: StableId


@dataclass(frozen=True, slots=True)
class TestApplicability:
    """Direct capability applicability for one executable test definition."""

    capability_ids: tuple[StableId, ...]


@dataclass(frozen=True, slots=True)
class TestDefinition:
    """One complete executable test definition without normative intermediaries."""

    id: StableId
    name: str
    description: str
    purpose: str
    applicability: TestApplicability
    dependencies: tuple[StableId, ...]
    request: TestDefinitionRequest
    assertions: tuple[TestAssertion, ...]
    evidence_policy: ExecutionEvidencePolicy
    outputs: tuple[TestOutput, ...] = ()


@dataclass(frozen=True, slots=True)
class TestDefinitionCatalogue:
    """Manually authored executable authority for one scheme and test scope."""

    schema_version: str
    document_type: str
    id: StableId
    scheme: StableId
    specification: Specification
    allowed_security_profiles: tuple[str, ...]
    technical_sources: tuple[TechnicalSource, ...]
    capabilities: tuple[Capability, ...]
    endpoints: tuple[Endpoint, ...]
    predefined_inputs: tuple[PredefinedInput, ...]
    test_definitions: tuple[TestDefinition, ...]


@dataclass(frozen=True, slots=True)
class ParticipantPlan:
    """Participant intent and compatibility runtime environment."""

    schema_version: str
    document_type: str
    id: StableId
    suite_release_id: StableId
    scheme: StableId
    specification: Specification
    security_profile: str
    selected_capability_ids: tuple[StableId, ...]
    predefined_inputs: tuple[ParticipantInput, ...]
    execution_configuration: ParticipantExecutionConfiguration | None = None


@dataclass(frozen=True, slots=True)
class ResolvedEndpoint:
    """Endpoint selected from catalogue-owned capability and test applicability."""

    id: StableId
    origin: SelectionOrigin
    reasons: tuple[ResolutionReason, ...]


@dataclass(frozen=True, slots=True)
class ResolvedPredefinedInput:
    """Logical participant input resolved under catalogue metadata."""

    id: StableId
    source: InputResolutionSource
    value: PredefinedInputValue | None
    redacted: bool
    reasons: tuple[ResolutionReason, ...]


@dataclass(frozen=True, slots=True)
class ResolvedTestInstance:
    """Deterministic instance of an applicable executable test definition."""

    id: StableId
    test_definition_id: StableId
    dependency_ids: tuple[StableId, ...]
    reasons: tuple[ResolutionReason, ...]


@dataclass(frozen=True, slots=True)
class ResolvedPlanProvenance:
    """Exact released catalogue and participant sources used for compilation."""

    participant_plan_id: StableId
    suite_release_id: StableId
    suite_release_version: str
    suite_published_at: str
    test_definition_catalogue_id: StableId
    tool_releases: tuple[ToolRelease, ...]
    artifacts: tuple[ArtifactReference, ...]


@dataclass(frozen=True, slots=True)
class ResolvedPlan:
    """Inspectable compilation of participant intent into executable instances."""

    schema_version: str
    document_type: str
    id: StableId
    selection_valid: bool
    scheme: StableId
    specification: Specification
    security_profile: str
    capabilities: tuple[ResolvedCapability, ...]
    endpoints: tuple[ResolvedEndpoint, ...]
    predefined_inputs: tuple[ResolvedPredefinedInput, ...]
    test_instances: tuple[ResolvedTestInstance, ...]
    findings: tuple[CompilationFinding, ...]
    provenance: ResolvedPlanProvenance


@dataclass(frozen=True, slots=True)
class ExecutionManifestStep:
    """One manifest-authoritative request and its catalogue-owned policy."""

    id: StableId
    test_instance_id: StableId
    test_definition_id: StableId
    name: str
    dependency_ids: tuple[StableId, ...]
    request: ExecutionManifestRequest
    assertions: tuple[ExecutionManifestAssertion, ...]
    outputs: tuple[TestOutput, ...]
    evidence: ExecutionEvidencePolicy


@dataclass(frozen=True, slots=True)
class ExecutionManifestProvenance:
    """Immutable release-to-catalogue provenance for a generated manifest."""

    resolved_plan_id: StableId
    participant_plan_id: StableId
    suite_release_id: StableId
    suite_release_version: str
    suite_published_at: str
    test_definition_catalogue_id: StableId
    tool_releases: tuple[ToolRelease, ...]
    artifacts: tuple[ArtifactReference, ...]


@dataclass(frozen=True, slots=True)
class ExecutionManifest:
    """Generated instructions consumed by the manifest-authoritative runner."""

    schema_version: str
    document_type: str
    id: StableId
    security_profile: str
    inputs: tuple[ExecutionManifestInput, ...]
    steps: tuple[ExecutionManifestStep, ...]
    provenance: ExecutionManifestProvenance
