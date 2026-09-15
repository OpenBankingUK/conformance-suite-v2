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
