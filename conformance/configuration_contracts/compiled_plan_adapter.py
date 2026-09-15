"""Compatibility adapter from the resolved skeleton to current execution."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import cast

from conformance.catalogue import (
    CatalogueError,
    CatalogueKey,
    CatalogueTestCase,
    CompiledTestPlan,
    EndpointRef,
    ImplementedEndpoint,
    SecurityProfile,
    TestCatalogue,
    TestPlanSpec,
    compile_test_plan,
)
from conformance.configuration_contracts.diagnostics import ConfigurationContractError
from conformance.configuration_contracts.execution_manifest import generate_execution_manifest
from conformance.configuration_contracts.loader import (
    execution_manifest_to_document,
    parse_execution_manifest,
)
from conformance.configuration_contracts.models import (
    ExecutionManifest,
    ParticipantPlan,
    RequirementsCatalogue,
    ResolvedPlan,
    ResolvedTestInstance,
    StableId,
    TestDefinitionCatalogue,
)
from conformance.json_types import JsonValue
from conformance.results import ResultTraceabilitySource, build_safe_participant_plan_snapshot

_LEGACY_CASE_ID_BY_TEST_DEFINITION_ID: Mapping[StableId, str] = MappingProxyType(
    {
        StableId("pis.dso.test.consent-create"): "pis-v4-domestic-standing-order-consent-create",
        StableId("pis.dso.test.consent-read"): "pis-v4-domestic-standing-order-consent-read",
        StableId("pis.dso.test.order-create"): "pis-v4-domestic-standing-order-create",
        StableId("pis.dso.test.order-read"): "pis-v4-domestic-standing-order-read",
    }
)
_PIS_V4_CATALOGUE_KEY = CatalogueKey(standard="open-banking", version="v4.0", api="pis")


class ResolvedPlanAdapterError(ValueError):
    """Raised when a resolved skeleton cannot use the current execution path."""


@dataclass(frozen=True, slots=True)
class AdaptedCompiledExecution:
    """Existing execution inputs produced from one valid resolved plan."""

    compiled_plan: CompiledTestPlan
    runtime_inputs: Mapping[str, JsonValue]


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
    result_traceability: ResultTraceabilitySource | None = None


def prepare_resolved_execution_manifest(
    resolved_plan: ResolvedPlan,
    requirements_catalogue: RequirementsCatalogue,
    test_definition_catalogue: TestDefinitionCatalogue,
    catalogue: TestCatalogue,
    *,
    participant_plan: ParticipantPlan,
    runtime_inputs: Mapping[str, JsonValue],
    runtime_input_base_dir: Path,
) -> PreparedExecutionManifest:
    """Generate a stable manifest and bind it to the current Read/Write engine."""
    _validate_participant_plan_snapshot(participant_plan, resolved_plan, requirements_catalogue)
    manifest = generate_execution_manifest(
        resolved_plan,
        requirements_catalogue,
        test_definition_catalogue,
    )
    adapted = adapt_resolved_plan_to_compiled_execution(
        resolved_plan,
        catalogue,
        runtime_inputs=runtime_inputs,
    )
    prepared = PreparedExecutionManifest(
        manifest=manifest,
        engine=LegacyExecutionEngine.READ_WRITE,
        compiled_plan=adapted.compiled_plan,
        runtime_inputs=adapted.runtime_inputs,
        runtime_input_base_dir=runtime_input_base_dir,
        result_traceability=ResultTraceabilitySource(
            execution_manifest=manifest,
            resolved_plan=resolved_plan,
            participant_plan_snapshot=build_safe_participant_plan_snapshot(
                participant_plan,
                requirements_catalogue,
            ),
            result_observation_id_by_manifest_step_id=_result_observation_ids_by_manifest_step(
                manifest,
                adapted.compiled_plan,
            ),
        ),
    )
    validate_execution_manifest_compatibility(prepared)
    return prepared


def adapt_compiled_plan_to_execution_manifest(
    compiled_plan: CompiledTestPlan,
    *,
    runtime_inputs: Mapping[str, JsonValue],
    runtime_input_base_dir: Path,
) -> PreparedExecutionManifest:
    """Wrap current catalogue execution behind the new runner boundary.

    Existing participant surfaces do not yet produce resolved plans, so their
    compatibility wrapper intentionally has no stable manifest document.
    """
    is_dcr = compiled_plan.catalogue_key.api in {"dcr", "dynamic-client-registration"}
    return PreparedExecutionManifest(
        manifest=None,
        engine=LegacyExecutionEngine.DCR if is_dcr else LegacyExecutionEngine.READ_WRITE,
        compiled_plan=compiled_plan,
        runtime_inputs=MappingProxyType(dict(runtime_inputs)),
        runtime_input_base_dir=runtime_input_base_dir,
    )


def _validate_participant_plan_snapshot(
    participant_plan: ParticipantPlan,
    resolved_plan: ResolvedPlan,
    requirements_catalogue: RequirementsCatalogue,
) -> None:
    provenance = resolved_plan.provenance
    if participant_plan.id != provenance.participant_plan_id:
        raise ResolvedPlanAdapterError("Participant plan differs from resolved-plan provenance")
    if participant_plan.suite_release_id != provenance.suite_release_id:
        raise ResolvedPlanAdapterError("Participant plan suite release differs from resolved-plan provenance")
    if (
        participant_plan.scheme != resolved_plan.scheme
        or participant_plan.specification != resolved_plan.specification
        or participant_plan.security_profile != resolved_plan.security_profile
    ):
        raise ResolvedPlanAdapterError("Participant plan scope differs from the resolved plan")
    explicit_capability_ids = {
        capability.id for capability in resolved_plan.capabilities if capability.origin.value == "explicit"
    }
    if set(participant_plan.selected_capability_ids) != explicit_capability_ids:
        raise ResolvedPlanAdapterError("Participant capability selections differ from the resolved plan")
    resolved_participant_inputs = {
        predefined_input.id: predefined_input
        for predefined_input in resolved_plan.predefined_inputs
        if predefined_input.source.value == "participant"
    }
    participant_inputs = {
        participant_input.input_id: participant_input for participant_input in participant_plan.predefined_inputs
    }
    if participant_inputs.keys() != resolved_participant_inputs.keys():
        raise ResolvedPlanAdapterError("Participant predefined inputs differ from the resolved plan")
    input_definitions = {
        predefined_input.id: predefined_input for predefined_input in requirements_catalogue.predefined_inputs
    }
    for input_id, participant_input in participant_inputs.items():
        input_definition = input_definitions.get(input_id)
        if input_definition is None:
            raise ResolvedPlanAdapterError(
                f"Participant-plan input {input_id!s} has no trusted sensitivity classification"
            )
        resolved_input = resolved_participant_inputs[input_id]
        if input_definition.sensitivity == "non-sensitive":
            if resolved_input.redacted or resolved_input.value != participant_input.value:
                raise ResolvedPlanAdapterError(f"Participant-plan input {input_id!s} differs from the resolved plan")
        elif not resolved_input.redacted or resolved_input.value is not None:
            raise ResolvedPlanAdapterError(f"Sensitive participant-plan input {input_id!s} was not redacted")


def _result_observation_ids_by_manifest_step(
    manifest: ExecutionManifest,
    compiled_plan: CompiledTestPlan,
) -> Mapping[str, str]:
    compiled_cases_by_id = {test_case.test_case_id: test_case for test_case in compiled_plan.test_cases}
    observation_ids: dict[str, str] = {}
    for manifest_step in manifest.steps:
        legacy_case_id = _LEGACY_CASE_ID_BY_TEST_DEFINITION_ID.get(manifest_step.test_definition_id)
        legacy_case = compiled_cases_by_id.get(legacy_case_id) if legacy_case_id is not None else None
        if legacy_case is None or len(legacy_case.request_steps) != 1:
            raise ResolvedPlanAdapterError(
                f"Cannot identify one result observation for manifest step {manifest_step.id!s}"
            )
        observation_ids[str(manifest_step.id)] = legacy_case.request_steps[0].step_id
    return MappingProxyType(observation_ids)


def validate_execution_manifest_compatibility(prepared: PreparedExecutionManifest) -> None:
    """Reject drift between a stable manifest and its legacy runtime binding."""
    manifest = prepared.manifest
    if manifest is None:
        return
    try:
        parse_execution_manifest(execution_manifest_to_document(manifest))
    except ConfigurationContractError as error:
        raise ResolvedPlanAdapterError("Prepared execution manifest is invalid") from error
    if prepared.engine is not LegacyExecutionEngine.READ_WRITE:
        raise ResolvedPlanAdapterError("Stable walking-skeleton manifests require the Read/Write engine")
    compiled_plan = prepared.compiled_plan
    if manifest.security_profile != compiled_plan.security_profile:
        raise ResolvedPlanAdapterError("Execution manifest security profile differs from the compiled plan")

    expected_case_ids: list[str] = []
    step_by_id = {step.id: step for step in manifest.steps}
    compiled_cases_by_id = {test_case.test_case_id: test_case for test_case in compiled_plan.test_cases}
    for step in manifest.steps:
        legacy_case_id = _LEGACY_CASE_ID_BY_TEST_DEFINITION_ID.get(step.test_definition_id)
        if legacy_case_id is None:
            raise ResolvedPlanAdapterError(
                f"No legacy execution mapping for manifest test definition {step.test_definition_id!s}"
            )
        expected_case_ids.append(legacy_case_id)
        legacy_case = compiled_cases_by_id.get(legacy_case_id)
        if legacy_case is None:
            raise ResolvedPlanAdapterError(f"Compiled plan does not contain manifest work {legacy_case_id}")
        if len(legacy_case.request_steps) != 1:
            raise ResolvedPlanAdapterError(f"Legacy case {legacy_case_id} must contain exactly one request step")
        request_step = legacy_case.request_steps[0]
        if request_step.method != step.request.method.value or not _paths_describe_same_operation(
            step.request.path,
            request_step.path,
        ):
            raise ResolvedPlanAdapterError(f"Legacy request for {legacy_case_id} differs from the execution manifest")
        legacy_statuses = _legacy_expected_statuses(legacy_case)
        manifest_statuses = tuple(assertion.expected_status for assertion in step.assertions)
        if legacy_statuses != manifest_statuses:
            raise ResolvedPlanAdapterError(f"Legacy assertions for {legacy_case_id} differ from the execution manifest")
        expected_dependencies_list: list[str] = []
        for dependency_id in step.dependency_ids:
            dependency_step = step_by_id.get(dependency_id)
            if dependency_step is None:
                raise ResolvedPlanAdapterError(
                    f"Manifest dependency {dependency_id!s} does not identify a test instance"
                )
            dependency_case_id = _LEGACY_CASE_ID_BY_TEST_DEFINITION_ID.get(dependency_step.test_definition_id)
            if dependency_case_id is None:
                raise ResolvedPlanAdapterError(
                    f"No legacy execution mapping for dependency {dependency_step.test_definition_id!s}"
                )
            expected_dependencies_list.append(dependency_case_id)
        expected_dependencies = tuple(expected_dependencies_list)
        if legacy_case.dependencies != expected_dependencies:
            raise ResolvedPlanAdapterError(
                f"Legacy dependencies for {legacy_case_id} differ from the execution manifest"
            )

    actual_case_ids = tuple(test_case.test_case_id for test_case in compiled_plan.test_cases)
    if actual_case_ids != tuple(expected_case_ids) or compiled_plan.traceability.generated_test_case_ids != tuple(
        expected_case_ids
    ):
        raise ResolvedPlanAdapterError("Compiled plan selection differs from the execution manifest")
    frequency_input = next(
        (manifest_input for manifest_input in manifest.inputs if manifest_input.id == "pis.dso.input.frequency"),
        None,
    )
    if frequency_input is not None:
        if (
            prepared.runtime_inputs.get("pisStandingOrderFrequencyType") != frequency_input.value.frequency_type
            or prepared.runtime_inputs.get("pisStandingOrderFrequencyPointInTime")
            != frequency_input.value.point_in_time
        ):
            raise ResolvedPlanAdapterError("Legacy runtime frequency differs from the execution manifest")


def _paths_describe_same_operation(manifest_path: str, legacy_path: str) -> bool:
    placeholder_pattern = r"\$\{[^}]+\}|\{[^}]+\}"
    normalized_manifest = re.sub(placeholder_pattern, "{}", manifest_path)
    normalized_legacy = re.sub(placeholder_pattern, "{}", legacy_path)
    return normalized_legacy.endswith(normalized_manifest)


def _legacy_expected_statuses(test_case: CatalogueTestCase) -> tuple[int, ...]:
    statuses: list[int] = list(test_case.expected_http_statuses)
    for assertion in test_case.assertions:
        if assertion.kind == "http_status":
            expected = assertion.rule.get("expected")
            if isinstance(expected, int) and not isinstance(expected, bool) and expected not in statuses:
                statuses.append(expected)
            continue
        if assertion.kind != "legacy_fcs":
            continue
        all_of = assertion.rule.get("allOf")
        if not isinstance(all_of, list):
            continue
        for rule in all_of:
            if not isinstance(rule, Mapping):
                continue
            expected = rule.get("status-code")
            if isinstance(expected, int) and not isinstance(expected, bool) and expected not in statuses:
                statuses.append(expected)
    return tuple(statuses)


def adapt_resolved_plan_to_compiled_execution(
    resolved_plan: ResolvedPlan,
    catalogue: TestCatalogue,
    *,
    runtime_inputs: Mapping[str, JsonValue],
) -> AdaptedCompiledExecution:
    """Adapt a valid resolved plan to the existing compiled execution inputs.

    Existing environment, credential, and non-frequency business inputs remain
    supplied through the current execution boundary during this migration
    layer. The resolved plan remains authoritative for test selection and the
    logical frequency value.
    """
    if not resolved_plan.selection_valid:
        raise ResolvedPlanAdapterError("Cannot adapt a resolved plan with error findings")
    if catalogue.key != _PIS_V4_CATALOGUE_KEY:
        raise ResolvedPlanAdapterError("The walking-skeleton adapter requires the bundled v4.0 PIS catalogue")

    legacy_cases_by_id = {test_case.test_case_id: test_case for test_case in catalogue.test_cases}
    selected_case_ids: list[str] = []
    test_instance_by_id = {test_instance.id: test_instance for test_instance in resolved_plan.test_instances}
    for test_instance in resolved_plan.test_instances:
        legacy_case_id = _LEGACY_CASE_ID_BY_TEST_DEFINITION_ID.get(test_instance.test_definition_id)
        if legacy_case_id is None:
            raise ResolvedPlanAdapterError(
                f"No existing execution mapping for test definition {test_instance.test_definition_id!s}"
            )
        legacy_case = legacy_cases_by_id.get(legacy_case_id)
        if legacy_case is None:
            raise ResolvedPlanAdapterError(f"Existing PIS catalogue does not contain {legacy_case_id}")
        expected_dependencies = tuple(
            _legacy_case_id_for_instance(dependency_id, test_instance_by_id)
            for dependency_id in test_instance.dependency_ids
        )
        if legacy_case.dependencies != expected_dependencies:
            raise ResolvedPlanAdapterError(
                f"Existing dependency mapping for {legacy_case_id} does not match the resolved plan"
            )
        selected_case_ids.append(legacy_case_id)

    selected_cases = tuple(legacy_cases_by_id[case_id] for case_id in selected_case_ids)
    implemented_endpoints = tuple(
        ImplementedEndpoint(
            method=endpoint_ref.method,
            path=endpoint_ref.path,
            resource_group="Domestic standing orders",
        )
        for endpoint_ref in _ordered_endpoint_refs(selected_cases)
    )
    adapted_runtime_inputs = dict(runtime_inputs)
    _apply_resolved_frequency(resolved_plan, adapted_runtime_inputs)
    base_spec = TestPlanSpec(
        schema_version="v1",
        catalogue_key=catalogue.key,
        security_profile=cast("SecurityProfile", resolved_plan.security_profile),
        implemented_endpoints=implemented_endpoints,
        runtime_inputs=adapted_runtime_inputs,
        specification_version=resolved_plan.specification.version,
    )
    full_compiled_plan = compile_test_plan(catalogue, base_spec)
    selected_case_id_set = set(selected_case_ids)
    unmapped_case_ids = tuple(
        test_case_id
        for test_case_id in full_compiled_plan.traceability.generated_test_case_ids
        if test_case_id not in selected_case_id_set
    )
    try:
        compiled_plan = compile_test_plan(
            catalogue,
            TestPlanSpec(
                schema_version=base_spec.schema_version,
                catalogue_key=base_spec.catalogue_key,
                security_profile=base_spec.security_profile,
                implemented_endpoints=base_spec.implemented_endpoints,
                runtime_inputs=base_spec.runtime_inputs,
                specification_version=base_spec.specification_version,
                deselected_test_case_ids=unmapped_case_ids,
            ),
        )
    except CatalogueError as error:
        raise ResolvedPlanAdapterError(
            "Existing PIS catalogue requires unmapped work outside the resolved walking-skeleton plan"
        ) from error
    if compiled_plan.traceability.generated_test_case_ids != tuple(selected_case_ids):
        raise ResolvedPlanAdapterError("Existing compiler selected work outside the resolved plan")
    return AdaptedCompiledExecution(
        compiled_plan=compiled_plan,
        runtime_inputs=MappingProxyType(adapted_runtime_inputs),
    )


def _legacy_case_id_for_instance(
    instance_id: StableId,
    test_instance_by_id: Mapping[StableId, ResolvedTestInstance],
) -> str:
    dependency = test_instance_by_id.get(instance_id)
    if dependency is None:
        raise ResolvedPlanAdapterError(f"Resolved dependency instance {instance_id!s} does not exist")
    legacy_case_id = _LEGACY_CASE_ID_BY_TEST_DEFINITION_ID.get(dependency.test_definition_id)
    if legacy_case_id is None:
        raise ResolvedPlanAdapterError(
            f"No existing execution mapping for dependency {dependency.test_definition_id!s}"
        )
    return legacy_case_id


def _ordered_endpoint_refs(
    selected_cases: tuple[CatalogueTestCase, ...],
) -> tuple[EndpointRef, ...]:
    endpoint_refs: list[EndpointRef] = []
    seen: set[EndpointRef] = set()
    for selected_case in selected_cases:
        for endpoint_ref in selected_case.applicability.endpoint_refs:
            if endpoint_ref not in seen:
                seen.add(endpoint_ref)
                endpoint_refs.append(endpoint_ref)
    return tuple(endpoint_refs)


def _apply_resolved_frequency(resolved_plan: ResolvedPlan, runtime_inputs: dict[str, JsonValue]) -> None:
    frequency_input = next(
        (
            predefined_input
            for predefined_input in resolved_plan.predefined_inputs
            if predefined_input.id == "pis.dso.input.frequency"
        ),
        None,
    )
    if frequency_input is None:
        if resolved_plan.test_instances:
            raise ResolvedPlanAdapterError("Resolved standing-order tests require the logical frequency input")
        return
    frequency = frequency_input.value
    if frequency is None:
        raise ResolvedPlanAdapterError("The current execution adapter cannot consume a redacted frequency")
    if frequency.point_in_time is None:
        raise ResolvedPlanAdapterError(
            "The current execution path supports pointInTime standing-order frequencies only"
        )
    runtime_inputs["pisStandingOrderFrequencyType"] = frequency.frequency_type
    runtime_inputs["pisStandingOrderFrequencyPointInTime"] = frequency.point_in_time
