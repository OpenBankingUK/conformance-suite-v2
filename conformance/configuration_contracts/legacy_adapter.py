"""Compatibility adapter from resolved plans to the current execution model."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import cast

from conformance.catalogue import (
    ApplicabilityDecision,
    CatalogueRequestStep,
    CatalogueTestCase,
    CompiledTestPlan,
    CompilerTraceability,
    HttpMethod,
    ImplementedEndpoint,
    RuntimeInputTrace,
    SecurityProfile,
)
from conformance.catalogues.pis import PIS_PAYMENT_CATALOGUE
from conformance.configuration_contracts.compiler import (
    ParticipantPlanCompilationError,
    compile_participant_plan,
)
from conformance.configuration_contracts.models import (
    ParticipantPlan,
    RequirementsCatalogue,
    ResolvedInput,
    ResolvedPlan,
    StableId,
    StandingOrderFrequency,
    SuiteRelease,
    TestDefinition,
    TestDefinitionCatalogue,
)
from conformance.json_types import JsonObject, JsonValue

_TEST_DEFINITION_TO_LEGACY_CASE = {
    "pis.dso.test.consent-create": "pis-v4-domestic-standing-order-consent-create",
    "pis.dso.test.consent-read": "pis-v4-domestic-standing-order-consent-read",
    "pis.dso.test.order-create": "pis-v4-domestic-standing-order-create",
    "pis.dso.test.order-read": "pis-v4-domestic-standing-order-read",
}
"""Explicit temporary mapping from the walking skeleton to current PIS cases."""

_ENDPOINT_TO_LEGACY_OPERATION: dict[str, tuple[HttpMethod, str]] = {
    "pis.dso.endpoint.consent-create": ("POST", "/open-banking/v4.0/pisp/domestic-standing-order-consents"),
    "pis.dso.endpoint.consent-read": (
        "GET",
        "/open-banking/v4.0/pisp/domestic-standing-order-consents/{domesticStandingOrderConsentId}",
    ),
    "pis.dso.endpoint.order-create": ("POST", "/open-banking/v4.0/pisp/domestic-standing-orders"),
    "pis.dso.endpoint.order-read": (
        "GET",
        "/open-banking/v4.0/pisp/domestic-standing-orders/{domesticStandingOrderId}",
    ),
}
"""Explicit temporary endpoint mapping retained until execution-manifest PR 5."""

_LEGACY_FREQUENCY_RUNTIME_INPUT_IDS = {
    "pisStandingOrderFrequencyType",
    "pisStandingOrderFrequencyPointInTime",
}


class ResolvedPlanAdapterError(ValueError):
    """Raised when a resolved plan cannot safely use the legacy execution path."""


@dataclass(frozen=True, slots=True)
class LegacyCompiledPlanAdapter:
    """Current executor inputs produced from a valid resolved plan."""

    resolved_plan: ResolvedPlan
    compiled_plan: CompiledTestPlan
    runtime_inputs: Mapping[str, JsonValue]


def adapt_resolved_plan_to_compiled_execution(
    resolved_plan: ResolvedPlan,
    test_definition_catalogue: TestDefinitionCatalogue,
    *,
    suite_release: SuiteRelease,
    requirements_catalogue: RequirementsCatalogue,
    participant_plan: ParticipantPlan,
    artifact_bytes: Mapping[tuple[str, str], bytes],
    legacy_runtime_inputs: Mapping[str, JsonValue],
) -> LegacyCompiledPlanAdapter:
    """Adapt resolved walking-skeleton work to the existing compiled executor.

    The adapter is intentionally explicit and temporary. It maps the four new
    test-definition IDs to their characterised PIS catalogue cases and applies
    logical input bindings to copied request templates. It does not teach the
    executor participant-plan or requirement semantics.
    """
    try:
        expected_resolved_plan = compile_participant_plan(
            suite_release=suite_release,
            requirements_catalogue=requirements_catalogue,
            test_definition_catalogue=test_definition_catalogue,
            participant_plan=participant_plan,
            artifact_bytes=artifact_bytes,
        )
    except ParticipantPlanCompilationError as error:
        raise ResolvedPlanAdapterError("Cannot adapt source inputs that strict compilation rejected") from error
    if resolved_plan != expected_resolved_plan:
        raise ResolvedPlanAdapterError("Resolved plan does not match release-bound compiler inputs")
    if not resolved_plan.compilation_allowed:
        raise ResolvedPlanAdapterError("Cannot adapt a resolved plan that strict compilation rejected")
    if resolved_plan.provenance.test_definition_catalogue_id != test_definition_catalogue.id:
        raise ResolvedPlanAdapterError("Resolved plan provenance does not match the supplied test definitions")

    definitions_by_id = {definition.id: definition for definition in test_definition_catalogue.test_definitions}
    legacy_cases_by_id = {test_case.test_case_id: test_case for test_case in PIS_PAYMENT_CATALOGUE.test_cases}
    resolved_inputs = {resolved_input.id: resolved_input for resolved_input in resolved_plan.resolved_inputs}
    selected_cases: list[CatalogueTestCase] = []
    decisions: list[ApplicabilityDecision] = []
    for instance in resolved_plan.test_instances:
        definition = definitions_by_id.get(instance.test_definition_id)
        if definition is None:
            raise ResolvedPlanAdapterError(
                f"Resolved test definition {instance.test_definition_id!s} is absent from the supplied catalogue"
            )
        legacy_case_id = _TEST_DEFINITION_TO_LEGACY_CASE.get(str(instance.test_definition_id))
        if legacy_case_id is None:
            raise ResolvedPlanAdapterError(
                f"No legacy execution mapping exists for test definition {instance.test_definition_id!s}"
            )
        legacy_case = legacy_cases_by_id.get(legacy_case_id)
        if legacy_case is None:
            raise ResolvedPlanAdapterError(f"Legacy PIS catalogue case {legacy_case_id!r} is unavailable")
        adapted_case = _adapt_legacy_case(
            legacy_case,
            definition=definition,
            resolved_inputs=resolved_inputs,
        )
        selected_cases.append(adapted_case)
        decisions.append(
            ApplicabilityDecision(
                test_case_id=adapted_case.test_case_id,
                selected=True,
                reason=f"adapted from resolved test instance {instance.id!s}",
            )
        )

    runtime_inputs = MappingProxyType(copy.deepcopy(dict(legacy_runtime_inputs)))
    selected_endpoints = tuple(_adapt_endpoint(selection.id) for selection in resolved_plan.selected_endpoints)
    compiled_plan = CompiledTestPlan(
        catalogue_key=PIS_PAYMENT_CATALOGUE.key,
        catalogue_version=PIS_PAYMENT_CATALOGUE.catalogue_version,
        security_profile=cast("SecurityProfile", str(resolved_plan.security_profile)),
        test_cases=tuple(selected_cases),
        traceability=CompilerTraceability(
            catalogue_key=PIS_PAYMENT_CATALOGUE.key,
            catalogue_version=PIS_PAYMENT_CATALOGUE.catalogue_version,
            security_profile=cast("SecurityProfile", str(resolved_plan.security_profile)),
            selected_endpoints=selected_endpoints,
            selected_capabilities=(),
            applicability_decisions=tuple(decisions),
            generated_test_case_ids=tuple(test_case.test_case_id for test_case in selected_cases),
            runtime_input_snapshot=_runtime_input_snapshot(tuple(selected_cases), runtime_inputs),
            non_certifying_reasons=(),
            provenance=PIS_PAYMENT_CATALOGUE.provenance,
        ),
        certifying=resolved_plan.certification_eligible,
    )
    return LegacyCompiledPlanAdapter(
        resolved_plan=resolved_plan,
        compiled_plan=compiled_plan,
        runtime_inputs=runtime_inputs,
    )


def _adapt_endpoint(endpoint_id: StableId) -> ImplementedEndpoint:
    operation = _ENDPOINT_TO_LEGACY_OPERATION.get(str(endpoint_id))
    if operation is None:
        raise ResolvedPlanAdapterError(f"No legacy execution mapping exists for endpoint {endpoint_id!s}")
    method, path = operation
    return ImplementedEndpoint(
        method=method,
        path=path,
        resource_group="pis.domestic-standing-order",
    )


def _adapt_legacy_case(
    test_case: CatalogueTestCase,
    *,
    definition: TestDefinition,
    resolved_inputs: Mapping[StableId, ResolvedInput],
) -> CatalogueTestCase:
    request_steps = tuple(
        _adapt_request_step(
            request_step,
            definition=definition,
            resolved_inputs=resolved_inputs,
        )
        for request_step in test_case.request_steps
    )
    runtime_requirements = tuple(
        requirement
        for requirement in test_case.runtime_input_requirements
        if requirement.input_id not in _LEGACY_FREQUENCY_RUNTIME_INPUT_IDS
    )
    return replace(
        test_case,
        runtime_input_requirements=runtime_requirements,
        request_steps=request_steps,
    )


def _adapt_request_step(
    request_step: CatalogueRequestStep,
    *,
    definition: TestDefinition,
    resolved_inputs: Mapping[StableId, ResolvedInput],
) -> CatalogueRequestStep:
    body_template = copy.deepcopy(request_step.body_template)
    for binding in definition.request.input_bindings:
        if binding.type != "json-body" or binding.transform != "pis-v4-standing-order-frequency":
            raise ResolvedPlanAdapterError(f"Unsupported legacy input binding {binding.type!r}/{binding.transform!s}")
        resolved_input = resolved_inputs.get(binding.input_id)
        if resolved_input is None or resolved_input.value is None:
            raise ResolvedPlanAdapterError(f"Resolved input {binding.input_id!s} has no adaptable value")
        if body_template is None or not isinstance(body_template, dict):
            raise ResolvedPlanAdapterError(f"Legacy case {definition.id!s} has no JSON body template for input binding")
        _replace_json_pointer(
            body_template,
            binding.target,
            _transform_pis_v4_standing_order_frequency(resolved_input.value),
        )
    return replace(
        request_step,
        runtime_input_refs=tuple(
            input_id
            for input_id in request_step.runtime_input_refs
            if input_id not in _LEGACY_FREQUENCY_RUNTIME_INPUT_IDS
        ),
        body_template=body_template,
    )


def _replace_json_pointer(document: JsonObject, pointer: str, value: JsonValue) -> None:
    tokens = tuple(token.replace("~1", "/").replace("~0", "~") for token in pointer.removeprefix("/").split("/"))
    if not tokens or pointer == "":
        raise ResolvedPlanAdapterError("Legacy input bindings must target an object member")
    current: JsonValue = document
    for token in tokens[:-1]:
        if not isinstance(current, dict) or token not in current:
            raise ResolvedPlanAdapterError(f"Legacy request template does not contain binding target {pointer!r}")
        current = current[token]
    if not isinstance(current, dict) or tokens[-1] not in current:
        raise ResolvedPlanAdapterError(f"Legacy request template does not contain binding target {pointer!r}")
    current[tokens[-1]] = copy.deepcopy(value)


def _transform_pis_v4_standing_order_frequency(value: StandingOrderFrequency) -> JsonObject:
    transformed: JsonObject = {"Type": value.frequency_type}
    if value.count_per_period is not None:
        transformed["CountPerPeriod"] = value.count_per_period
    if value.point_in_time is not None:
        transformed["PointInTime"] = value.point_in_time
    return transformed


def _runtime_input_snapshot(
    test_cases: tuple[CatalogueTestCase, ...],
    runtime_inputs: Mapping[str, JsonValue],
) -> tuple[RuntimeInputTrace, ...]:
    requirements = {
        requirement.input_id: requirement
        for test_case in test_cases
        for requirement in test_case.runtime_input_requirements
    }
    return tuple(
        RuntimeInputTrace(
            input_id=requirement.input_id,
            input_type=requirement.input_type,
            required=requirement.required,
            sensitive=requirement.sensitive,
            provided=requirement.input_id in runtime_inputs,
            value=(
                None
                if requirement.sensitive or requirement.input_id not in runtime_inputs
                else copy.deepcopy(runtime_inputs[requirement.input_id])
            ),
        )
        for requirement in requirements.values()
    )
