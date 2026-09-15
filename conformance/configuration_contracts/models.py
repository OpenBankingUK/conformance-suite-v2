"""Immutable typed models populated only from schema-valid configuration."""

from __future__ import annotations

from dataclasses import dataclass
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
