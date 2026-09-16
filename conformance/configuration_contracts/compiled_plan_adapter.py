"""Compatibility binding from execution manifests to the current runtime."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

from conformance.catalogue import CompiledTestPlan
from conformance.configuration_contracts.diagnostics import ConfigurationContractError
from conformance.configuration_contracts.loader import (
    execution_manifest_to_document,
    parse_execution_manifest,
)
from conformance.configuration_contracts.models import ExecutionManifest
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
    """Immutable manifest plus temporary binding to existing execution data."""

    manifest: ExecutionManifest | None
    engine: LegacyExecutionEngine
    compiled_plan: CompiledTestPlan
    runtime_inputs: Mapping[str, JsonValue]
    runtime_input_base_dir: Path
    sensitive_json_pointers_by_observation_id: Mapping[str, tuple[str, ...]]
    result_traceability: ResultTraceabilitySource | None = None


def adapt_compiled_plan_to_execution_manifest(
    compiled_plan: CompiledTestPlan,
    *,
    runtime_inputs: Mapping[str, JsonValue],
    runtime_input_base_dir: Path,
) -> PreparedExecutionManifest:
    """Wrap direct compiled-plan tests behind the execution-manifest runner.

    Public participant surfaces always provide a stable manifest. This
    manifest-less wrapper remains only so focused compatibility-runtime tests
    can exercise compiled Read/Write and DCR plans directly.
    """
    is_dcr = compiled_plan.catalogue_key.api in {"dcr", "dynamic-client-registration"}
    return PreparedExecutionManifest(
        manifest=None,
        engine=LegacyExecutionEngine.DCR if is_dcr else LegacyExecutionEngine.READ_WRITE,
        compiled_plan=compiled_plan,
        runtime_inputs=MappingProxyType(dict(runtime_inputs)),
        runtime_input_base_dir=runtime_input_base_dir,
        sensitive_json_pointers_by_observation_id=MappingProxyType({}),
    )


def validate_execution_manifest_compatibility(prepared: PreparedExecutionManifest) -> None:
    """Reject drift between a stable manifest and its legacy runtime binding."""
    manifest = prepared.manifest
    if manifest is None:
        return
    try:
        parse_execution_manifest(execution_manifest_to_document(manifest))
    except ConfigurationContractError as error:
        raise ResolvedPlanAdapterError("Prepared execution manifest is invalid") from error
    compiled_plan = prepared.compiled_plan
    if manifest.security_profile != compiled_plan.security_profile:
        raise ResolvedPlanAdapterError("Execution manifest security profile differs from the compiled plan")
    if prepared.engine is LegacyExecutionEngine.DCR:
        if compiled_plan.catalogue_key.api not in {"dcr", "dynamic-client-registration"}:
            raise ResolvedPlanAdapterError("DCR manifests require a DCR compiled plan")
        if prepared.result_traceability is None:
            raise ResolvedPlanAdapterError("DCR manifests require result observation traceability")
        available_observation_ids = {
            step.step_id for test_case in compiled_plan.test_cases for step in test_case.execution_steps
        }
        mapped_observation_ids = set(prepared.result_traceability.result_observation_id_by_manifest_step_id.values())
        if not mapped_observation_ids.issubset(available_observation_ids):
            raise ResolvedPlanAdapterError("DCR manifest references unavailable result observations")
        return
    if prepared.engine is not LegacyExecutionEngine.READ_WRITE:
        raise ResolvedPlanAdapterError("Unsupported execution-manifest compatibility engine")
    if prepared.result_traceability is None:
        raise ResolvedPlanAdapterError("Migrated execution manifests require result observation traceability")
    available_observation_ids = {
        request.step_id for test_case in compiled_plan.test_cases for request in test_case.request_steps
    }
    mapped_observation_ids = set(prepared.result_traceability.result_observation_id_by_manifest_step_id.values())
    if not mapped_observation_ids.issubset(available_observation_ids):
        raise ResolvedPlanAdapterError("Manifest references unavailable result observations")
