"""Compatibility adapter from the resolved skeleton to current execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
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
from conformance.configuration_contracts.models import ResolvedPlan, ResolvedTestInstance, StableId
from conformance.json_types import JsonValue

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
