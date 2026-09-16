"""Runtime preparation for immutable execution manifests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from conformance.configuration_contracts.diagnostics import ConfigurationContractError
from conformance.configuration_contracts.suite_release_artifacts import SuiteReleaseArtifactResolver
from conformance.configuration_contracts.v2_loader import (
    execution_manifest_to_document,
    parse_execution_manifest,
)
from conformance.configuration_contracts.v2_models import ExecutionManifest
from conformance.json_types import JsonValue
from conformance.results import ResultTraceabilitySource


class ResolvedPlanAdapterError(ValueError):
    """Raised when a generated manifest cannot use the current execution path."""


class LegacyExecutionEngine(StrEnum):
    """Existing runtime selected behind the execution-manifest boundary."""

    READ_WRITE = "read-write"
    DCR = "dcr"


@dataclass(frozen=True, slots=True)
class PreparedExecutionManifest:
    """Immutable manifest plus non-work runtime environment and provenance."""

    manifest: ExecutionManifest
    engine: LegacyExecutionEngine
    runtime_inputs: Mapping[str, JsonValue]
    runtime_input_base_dir: Path
    artifact_resolver: SuiteReleaseArtifactResolver
    result_traceability: ResultTraceabilitySource | None = None


def validate_execution_manifest_compatibility(prepared: PreparedExecutionManifest) -> None:
    """Reject incomplete or unsupported runner instructions before execution."""
    manifest = prepared.manifest
    try:
        parse_execution_manifest(execution_manifest_to_document(manifest))
    except ConfigurationContractError as error:
        raise ResolvedPlanAdapterError("Prepared execution manifest is invalid") from error
    if prepared.engine not in {LegacyExecutionEngine.READ_WRITE, LegacyExecutionEngine.DCR}:
        raise ResolvedPlanAdapterError("Unsupported execution-manifest compatibility engine")
    if prepared.result_traceability is None:
        raise ResolvedPlanAdapterError("Execution manifests require result traceability")
    for step in manifest.steps:
        if step.request.base_url_source is None:
            raise ResolvedPlanAdapterError(f"Manifest request {step.id!s} has no baseUrlSource")
    if prepared.artifact_resolver.manifest != manifest:
        raise ResolvedPlanAdapterError("Artifact resolver was not preflighted for this execution manifest")
