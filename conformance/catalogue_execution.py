"""Catalogue-native planning and primitive execution.

This module compiles an intent-based :class:`conformance.run_plan_v2.RunPlanV2`
into an executable catalogue dependency graph.  It is intentionally independent
of the legacy suite-manifest executor: plugin catalogues are the source of test
applicability, runner primitive selection, masking metadata, and readiness
policy inputs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, cast

from conformance.catalogue import (
    Catalogue,
    CatalogueExecutableTest,
    CatalogueIdentity,
    CatalogueMaskingMetadata,
    CatalogueReadinessPolicy,
    CatalogueRunnerPrimitive,
)
from conformance.json_types import JsonValue
from conformance.plugins.registry import PluginRegistry
from conformance.run_plan_v2 import EndpointSelection, RunPlanV2
from conformance.target_config import SecurityProfile, Specification, Standard, TestTargetConfig

CatalogueStepOutcome = Literal["passed", "failed", "skipped"]
"""Outcome values emitted by catalogue-native primitive execution."""

PrimitiveHandler = Callable[["CompiledCatalogueStep"], "CataloguePrimitiveResult"]
"""Callable used by plugin-owned primitive implementations."""

_SHARED_READ_WRITE_PRIMITIVES: tuple[str, ...] = ("read-write.discovery", "read-write.jwks")
"""Read/Write primitives that are safe to de-duplicate across resource groups."""

_RESOURCE_GROUP_READ_WRITE_PRIMITIVES: tuple[str, ...] = (
    "read-write.client-credentials-token",
    "read-write.psu-authorization",
)
"""Read/Write primitives that are isolated per selected resource group."""


class CataloguePlanningError(ValueError):
    """Raised when a RunPlanV2 cannot be compiled against its catalogue."""


class CatalogueExecutionError(RuntimeError):
    """Raised when a compiled catalogue graph cannot be executed."""


@dataclass(frozen=True)
class CompiledCatalogueStep:
    """One step in a catalogue-native executable dependency graph.

    Attributes:
        step_id: Stable graph-local step identifier.
        primitive_id: Catalogue runner primitive identifier.
        primitive_type: Catalogue runner primitive type.
        display_label: Human-readable step label.
        dependencies: Ordered step IDs that must pass before this step runs.
        test_id: Catalogue executable test ID when the step represents a test,
            or ``None`` when it is an inferred prerequisite.
        endpoint_id: Endpoint exercised by the step, when applicable.
        resource_group: Resource group isolated for this step, when applicable.
        field_values: Participant-supplied endpoint field values.
    """

    step_id: str
    primitive_id: str
    primitive_type: str
    display_label: str
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    test_id: str | None = None
    endpoint_id: str | None = None
    resource_group: str | None = None
    field_values: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class CompiledCatalogueRun:
    """Compiled catalogue-native run graph for one RunPlanV2.

    Attributes:
        plugin_id: Plugin selected for the plan target.
        target: Canonical target coordinates used to load the catalogue.
        catalogue_identity: Live catalogue identity and content hash.
        selected_resource_groups: Resource groups included in the run.
        selected_endpoint_ids: Endpoint IDs included in executable coverage.
        steps: Topologically ordered compiled steps.
        mandatory_endpoint_ids: Mandatory endpoints/operations in the selected
            target scope, whether selected or omitted.
        omitted_mandatory_endpoint_ids: Mandatory endpoints in selected
            resource groups that were not selected and are therefore omitted
            from execution/results.
        omitted_mandatory_endpoint_ids_by_resource_group: Mandatory endpoint
            omissions keyed by selected resource group.
        readiness_policy: Catalogue-owned readiness policy metadata.
        masking: Catalogue-owned masking metadata.
    """

    plugin_id: str
    target: TestTargetConfig
    catalogue_identity: CatalogueIdentity
    selected_resource_groups: tuple[str, ...]
    selected_endpoint_ids: tuple[str, ...]
    steps: tuple[CompiledCatalogueStep, ...]
    mandatory_endpoint_ids: tuple[str, ...] = field(default_factory=tuple)
    omitted_mandatory_endpoint_ids: tuple[str, ...] = field(default_factory=tuple)
    omitted_mandatory_endpoint_ids_by_resource_group: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    readiness_policy: CatalogueReadinessPolicy | None = None
    masking: CatalogueMaskingMetadata | None = None


@dataclass(frozen=True)
class CataloguePrimitiveResult:
    """Outcome returned by one primitive handler invocation.

    Attributes:
        outcome: ``"passed"`` or ``"failed"`` for attempted primitives.
            ``"skipped"`` is reserved for dependency-blocked steps generated by
            the generic executor.
        detail: Human-readable result detail.
        evidence: Masked JSON-compatible evidence emitted by the primitive.
    """

    outcome: Literal["passed", "failed"]
    detail: str
    evidence: Mapping[str, JsonValue] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class CatalogueStepResult:
    """Result for one compiled catalogue step.

    Attributes:
        step_id: The compiled step identifier.
        primitive_id: Runner primitive that produced the result.
        outcome: Step outcome.
        detail: Human-readable outcome detail.
        test_id: Catalogue executable test ID, when the result is for a test.
        endpoint_id: Endpoint exercised by this step, when applicable.
        resource_group: Resource group isolated for this step, when applicable.
        evidence: Masked JSON-compatible step evidence.
    """

    step_id: str
    primitive_id: str
    outcome: CatalogueStepOutcome
    detail: str
    test_id: str | None = None
    endpoint_id: str | None = None
    resource_group: str | None = None
    evidence: Mapping[str, JsonValue] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class CatalogueRunResult:
    """Result of executing a compiled catalogue run graph.

    Attributes:
        compiled_run: The compiled graph that was executed.
        step_results: Ordered step results in execution order.
    """

    compiled_run: CompiledCatalogueRun
    step_results: tuple[CatalogueStepResult, ...]


def compile_catalogue_run_plan(plan: RunPlanV2, *, registry: PluginRegistry) -> CompiledCatalogueRun:
    """Compile a RunPlanV2 into a catalogue-native dependency graph.

    Args:
        plan: Intent-based RunPlanV2 to compile.
        registry: Plugin registry used to resolve and load the target catalogue.

    Returns:
        A topologically ordered :class:`CompiledCatalogueRun`.

    Raises:
        CataloguePlanningError: If the plan's selections do not match the
            loaded catalogue, or if the catalogue cannot provide executable
            tests for the requested coverage.
    """
    target = _target_from_plan(plan)
    plugin = registry.resolve(target)
    catalogue = plugin.load_catalogue(target)
    selected_resource_groups = _selected_resource_groups(plan, catalogue)
    selected_endpoint_ids = _selected_endpoint_ids(plan, catalogue, selected_resource_groups=selected_resource_groups)
    selected_endpoint_set = frozenset(selected_endpoint_ids)
    selected_resource_group_set = frozenset(selected_resource_groups)

    applicable_tests = tuple(
        test
        for test in catalogue.executable_tests
        if _test_applies(
            test,
            target_specification_version=catalogue.identity.specification_version,
            selected_resource_groups=selected_resource_group_set,
            selected_endpoint_ids=selected_endpoint_set,
        )
    )
    if not applicable_tests:
        raise CataloguePlanningError("No catalogue executable tests apply to the selected target coverage")

    primitive_by_id = _runner_primitives_by_id(catalogue)
    steps = _compile_steps(
        applicable_tests,
        primitive_by_id=primitive_by_id,
        selected_resource_groups=selected_resource_groups,
        selected_endpoint_field_values=_selected_endpoint_field_values(plan),
    )
    omitted_mandatory = _omitted_mandatory_endpoint_ids(
        catalogue,
        selected_resource_groups=selected_resource_group_set,
        selected_endpoint_ids=selected_endpoint_set,
    )
    mandatory_endpoint_ids = _mandatory_endpoint_ids(
        catalogue,
        selected_resource_groups=selected_resource_group_set,
    )
    omitted_mandatory_by_resource_group = _omitted_mandatory_endpoint_ids_by_resource_group(
        catalogue,
        selected_resource_groups=selected_resource_groups,
        selected_endpoint_ids=selected_endpoint_set,
    )

    return CompiledCatalogueRun(
        plugin_id=plugin.plugin_id,
        target=target,
        catalogue_identity=catalogue.identity,
        selected_resource_groups=selected_resource_groups,
        selected_endpoint_ids=selected_endpoint_ids,
        steps=steps,
        mandatory_endpoint_ids=mandatory_endpoint_ids,
        omitted_mandatory_endpoint_ids=omitted_mandatory,
        omitted_mandatory_endpoint_ids_by_resource_group=omitted_mandatory_by_resource_group,
        readiness_policy=catalogue.readiness_policy,
        masking=catalogue.masking,
    )


def execute_compiled_catalogue_run(
    compiled_run: CompiledCatalogueRun,
    *,
    primitive_handlers: Mapping[str, PrimitiveHandler],
) -> CatalogueRunResult:
    """Execute a compiled catalogue graph with plugin-owned primitive handlers.

    Args:
        compiled_run: The dependency graph produced by
            :func:`compile_catalogue_run_plan`.
        primitive_handlers: Mapping keyed by primitive ID or primitive type.
            Primitive ID matches are preferred, with primitive type as fallback.

    Returns:
        Ordered step results for every compiled step.

    Raises:
        CatalogueExecutionError: If a step has an unknown dependency or no
            primitive handler is registered for its primitive ID/type.
    """
    results_by_step_id: dict[str, CatalogueStepResult] = {}
    ordered_results: list[CatalogueStepResult] = []
    for step in compiled_run.steps:
        blocked_by = _first_blocking_dependency(step, results_by_step_id)
        if blocked_by is not None:
            result = _skipped_result(step, blocked_by=blocked_by)
        else:
            handler = _handler_for(step, primitive_handlers)
            primitive_result = handler(step)
            result = CatalogueStepResult(
                step_id=step.step_id,
                primitive_id=step.primitive_id,
                outcome=primitive_result.outcome,
                detail=primitive_result.detail,
                test_id=step.test_id,
                endpoint_id=step.endpoint_id,
                resource_group=step.resource_group,
                evidence=MappingProxyType(dict(primitive_result.evidence)),
            )
        results_by_step_id[step.step_id] = result
        ordered_results.append(result)
    return CatalogueRunResult(compiled_run=compiled_run, step_results=tuple(ordered_results))


def _target_from_plan(plan: RunPlanV2) -> TestTargetConfig:
    """Build target coordinates from a RunPlanV2.

    Args:
        plan: RunPlanV2 carrying target coordinates.

    Returns:
        A :class:`TestTargetConfig` suitable for plugin resolution.
    """
    return TestTargetConfig(
        standard=cast(Standard, plan.target.standard),
        specification=cast(Specification, plan.target.specification),
        security_profile=cast(SecurityProfile, plan.target.security_profile),
        specification_version=plan.target.specification_version,
        resource_groups=plan.resource_groups,
    )


def _selected_resource_groups(plan: RunPlanV2, catalogue: Catalogue) -> tuple[str, ...]:
    """Resolve and validate selected resource groups.

    Args:
        plan: RunPlanV2 being compiled.
        catalogue: Loaded catalogue for the plan target.

    Returns:
        Ordered selected resource groups.

    Raises:
        CataloguePlanningError: If a resource-group catalogue receives no
            selected groups, or if the plan names an unknown group.
    """
    available_groups = tuple(group.resource_group for group in catalogue.resource_groups)
    if not available_groups:
        return ()
    if not plan.resource_groups:
        raise CataloguePlanningError("Catalogue target requires at least one selected resource group")

    unknown = tuple(group for group in plan.resource_groups if group not in available_groups)
    if unknown:
        raise CataloguePlanningError(f"Unknown resource group selection(s): {list(unknown)!r}")
    return plan.resource_groups


def _selected_endpoint_ids(
    plan: RunPlanV2,
    catalogue: Catalogue,
    *,
    selected_resource_groups: tuple[str, ...],
) -> tuple[str, ...]:
    """Resolve selected endpoint IDs from explicit selections or catalogue defaults.

    Args:
        plan: RunPlanV2 being compiled.
        catalogue: Loaded catalogue for the plan target.
        selected_resource_groups: Resource groups selected for this run.

    Returns:
        Ordered endpoint IDs included in executable coverage.

    Raises:
        CataloguePlanningError: If explicit endpoint selections name unknown,
            duplicate, or out-of-scope endpoints.
    """
    endpoint_by_id = {endpoint.endpoint_id: endpoint for endpoint in catalogue.endpoints}
    if not plan.endpoint_selections:
        return tuple(
            endpoint.endpoint_id
            for endpoint in catalogue.endpoints
            if _endpoint_in_selected_scope(endpoint.resource_group, selected_resource_groups)
        )

    seen: set[str] = set()
    selected: list[str] = []
    for selection in plan.endpoint_selections:
        if selection.endpoint_id in seen:
            raise CataloguePlanningError(f"Duplicate endpoint selection for {selection.endpoint_id!r}")
        seen.add(selection.endpoint_id)
        endpoint = endpoint_by_id.get(selection.endpoint_id)
        if endpoint is None:
            raise CataloguePlanningError(
                f"Endpoint selection {selection.endpoint_id!r} is not present in the catalogue"
            )
        if not _endpoint_in_selected_scope(endpoint.resource_group, selected_resource_groups):
            raise CataloguePlanningError(
                f"Endpoint selection {selection.endpoint_id!r} is outside selected resource groups "
                f"{list(selected_resource_groups)!r}"
            )
        _validate_selection_operation(selection, endpoint.operation, endpoint.method)
        if selection.selected:
            selected.append(selection.endpoint_id)
    return tuple(selected)


def _endpoint_in_selected_scope(resource_group: str | None, selected_resource_groups: tuple[str, ...]) -> bool:
    """Return whether an endpoint belongs to the selected run scope.

    Args:
        resource_group: Endpoint resource group, or ``None`` for specifications
            without resource groups.
        selected_resource_groups: Selected resource groups for the run.

    Returns:
        ``True`` when the endpoint should be considered for selection.
    """
    if resource_group is None:
        return True
    return resource_group in selected_resource_groups


def _validate_selection_operation(selection: EndpointSelection, catalogue_operation: str, method: str) -> None:
    """Validate a RunPlanV2 endpoint selection operation against the catalogue.

    Args:
        selection: Participant endpoint selection to validate.
        catalogue_operation: Catalogue OpenAPI operation identifier.
        method: Catalogue HTTP method.

    Raises:
        CataloguePlanningError: If the selection operation matches neither the
            catalogue operation identifier nor the HTTP method.
    """
    if selection.operation in {catalogue_operation, method}:
        return
    raise CataloguePlanningError(
        f"Endpoint selection {selection.endpoint_id!r} operation {selection.operation!r} does not match "
        f"catalogue operation {catalogue_operation!r} or method {method!r}"
    )


def _selected_endpoint_field_values(plan: RunPlanV2) -> Mapping[str, Mapping[str, str]]:
    """Return selected endpoint field values keyed by endpoint ID.

    Args:
        plan: RunPlanV2 being compiled.

    Returns:
        Immutable mapping of endpoint IDs to immutable field-value maps.
    """
    values: dict[str, Mapping[str, str]] = {}
    for selection in plan.endpoint_selections:
        if selection.selected:
            values[selection.endpoint_id] = MappingProxyType(dict(selection.field_values))
    return MappingProxyType(values)


def _test_applies(
    test: CatalogueExecutableTest,
    *,
    target_specification_version: str,
    selected_resource_groups: frozenset[str],
    selected_endpoint_ids: frozenset[str],
) -> bool:
    """Evaluate catalogue applicability predicates for one executable test.

    Args:
        test: Catalogue executable test definition.
        target_specification_version: Canonical catalogue specification version.
        selected_resource_groups: Selected resource groups.
        selected_endpoint_ids: Selected endpoint IDs.

    Returns:
        ``True`` when all predicates pass and the test should be executed.
    """
    for predicate in test.applicability:
        if not _predicate_applies(
            predicate.predicate_type,
            predicate.parameters,
            target_specification_version=target_specification_version,
            selected_resource_groups=selected_resource_groups,
            selected_endpoint_ids=selected_endpoint_ids,
        ):
            return False
    return True


def _predicate_applies(
    predicate_type: str,
    parameters: Mapping[str, JsonValue],
    *,
    target_specification_version: str,
    selected_resource_groups: frozenset[str],
    selected_endpoint_ids: frozenset[str],
) -> bool:
    """Evaluate one catalogue applicability predicate.

    Args:
        predicate_type: Predicate type string from the catalogue.
        parameters: Predicate parameter object from the catalogue.
        target_specification_version: Canonical catalogue specification version.
        selected_resource_groups: Selected resource groups.
        selected_endpoint_ids: Selected endpoint IDs.

    Returns:
        ``True`` when the predicate is satisfied.

    Raises:
        CataloguePlanningError: If the predicate type is not supported.
    """
    if predicate_type == "resource-group-selected":
        resource_group = parameters.get("resourceGroup")
        return isinstance(resource_group, str) and resource_group in selected_resource_groups
    if predicate_type == "endpoint-selected":
        endpoint_id = parameters.get("endpointId")
        return isinstance(endpoint_id, str) and endpoint_id in selected_endpoint_ids
    if predicate_type == "target-version":
        version = parameters.get("specificationVersion")
        return isinstance(version, str) and version == target_specification_version
    raise CataloguePlanningError(f"Unsupported catalogue applicability predicate {predicate_type!r}")


def _runner_primitives_by_id(catalogue: Catalogue) -> Mapping[str, CatalogueRunnerPrimitive]:
    """Return runner primitives keyed by ID.

    Args:
        catalogue: Loaded catalogue.

    Returns:
        Immutable primitive lookup map.
    """
    return MappingProxyType({primitive.primitive_id: primitive for primitive in catalogue.runner_primitives})


def _compile_steps(
    applicable_tests: tuple[CatalogueExecutableTest, ...],
    *,
    primitive_by_id: Mapping[str, CatalogueRunnerPrimitive],
    selected_resource_groups: tuple[str, ...],
    selected_endpoint_field_values: Mapping[str, Mapping[str, str]],
) -> tuple[CompiledCatalogueStep, ...]:
    """Build prerequisite and test steps in dependency order.

    Args:
        applicable_tests: Catalogue tests selected by applicability evaluation.
        primitive_by_id: Catalogue runner primitive lookup map.
        selected_resource_groups: Resource groups selected for the run.
        selected_endpoint_field_values: Endpoint field values keyed by endpoint
            ID.

    Returns:
        Topologically ordered compiled steps.

    Raises:
        CataloguePlanningError: If a selected test references an unknown
            runner primitive.
    """
    steps: list[CompiledCatalogueStep] = []
    required_read_write_groups = _required_read_write_groups(applicable_tests, selected_resource_groups)
    steps.extend(_shared_read_write_steps(primitive_by_id, enabled=bool(required_read_write_groups)))
    for resource_group in required_read_write_groups:
        steps.extend(_resource_group_read_write_steps(resource_group, primitive_by_id=primitive_by_id))

    existing_step_ids = {step.step_id for step in steps}
    for test in applicable_tests:
        primitive = primitive_by_id.get(test.runner_primitive_id)
        if primitive is None:
            raise CataloguePlanningError(
                f"Catalogue test {test.test_id!r} references unknown runner primitive {test.runner_primitive_id!r}"
            )
        dependencies = _dependencies_for_test(test, existing_step_ids=existing_step_ids)
        steps.append(
            CompiledCatalogueStep(
                step_id=test.test_id,
                test_id=test.test_id,
                primitive_id=primitive.primitive_id,
                primitive_type=primitive.primitive_type,
                display_label=test.display_label,
                dependencies=dependencies,
                endpoint_id=test.endpoint_id,
                resource_group=test.resource_group,
                field_values=MappingProxyType(dict(selected_endpoint_field_values.get(test.endpoint_id or "", {}))),
            )
        )
    return tuple(steps)


def _required_read_write_groups(
    applicable_tests: tuple[CatalogueExecutableTest, ...],
    selected_resource_groups: tuple[str, ...],
) -> tuple[str, ...]:
    """Return resource groups requiring inferred Read/Write prerequisites.

    Args:
        applicable_tests: Catalogue tests selected for execution.
        selected_resource_groups: Resource groups selected for the run.

    Returns:
        Ordered resource groups that have at least one selected test.
    """
    groups_with_tests = {test.resource_group for test in applicable_tests if test.resource_group is not None}
    return tuple(group for group in selected_resource_groups if group in groups_with_tests)


def _shared_read_write_steps(
    primitive_by_id: Mapping[str, CatalogueRunnerPrimitive],
    *,
    enabled: bool,
) -> tuple[CompiledCatalogueStep, ...]:
    """Build shared Read/Write prerequisite steps when available.

    Args:
        primitive_by_id: Catalogue runner primitive lookup map.
        enabled: Whether selected tests require Read/Write prerequisites.

    Returns:
        Ordered shared prerequisite steps.  Missing primitives are ignored so
        non-Read/Write catalogues can still use the compiler.
    """
    if not enabled:
        return ()
    steps: list[CompiledCatalogueStep] = []
    for primitive_id in _SHARED_READ_WRITE_PRIMITIVES:
        primitive = primitive_by_id.get(primitive_id)
        if primitive is None:
            continue
        dependencies = ("read-write.discovery",) if primitive_id == "read-write.jwks" else ()
        steps.append(
            CompiledCatalogueStep(
                step_id=primitive_id,
                primitive_id=primitive.primitive_id,
                primitive_type=primitive.primitive_type,
                display_label=primitive.description,
                dependencies=dependencies,
            )
        )
    return tuple(steps)


def _resource_group_read_write_steps(
    resource_group: str,
    *,
    primitive_by_id: Mapping[str, CatalogueRunnerPrimitive],
) -> tuple[CompiledCatalogueStep, ...]:
    """Build per-resource-group Read/Write prerequisite steps.

    Args:
        resource_group: Resource group identifier.
        primitive_by_id: Catalogue runner primitive lookup map.

    Returns:
        Ordered resource-group prerequisite steps.  Missing primitives are
        ignored so catalogues can opt into only the primitives they define.
    """
    steps: list[CompiledCatalogueStep] = []
    for primitive_id in _RESOURCE_GROUP_READ_WRITE_PRIMITIVES:
        primitive = primitive_by_id.get(primitive_id)
        if primitive is None:
            continue
        suffix = primitive_id.removeprefix("read-write.")
        step_id = f"read-write.{resource_group}.{suffix}"
        dependencies = _resource_group_prerequisite_dependencies(resource_group, primitive_id)
        steps.append(
            CompiledCatalogueStep(
                step_id=step_id,
                primitive_id=primitive.primitive_id,
                primitive_type=primitive.primitive_type,
                display_label=f"{resource_group}: {primitive.description}",
                dependencies=dependencies,
                resource_group=resource_group,
            )
        )
    return tuple(steps)


def _resource_group_prerequisite_dependencies(resource_group: str, primitive_id: str) -> tuple[str, ...]:
    """Return dependencies for a resource-group prerequisite primitive.

    Args:
        resource_group: Resource group identifier.
        primitive_id: Runner primitive identifier.

    Returns:
        Ordered dependency step IDs.
    """
    if primitive_id == "read-write.client-credentials-token":
        return ("read-write.discovery", "read-write.jwks")
    if primitive_id == "read-write.psu-authorization":
        return (f"read-write.{resource_group}.client-credentials-token",)
    return ()


def _dependencies_for_test(test: CatalogueExecutableTest, *, existing_step_ids: set[str]) -> tuple[str, ...]:
    """Infer dependencies for one executable catalogue test.

    Args:
        test: Catalogue executable test definition.
        existing_step_ids: Step IDs already emitted for prerequisites.

    Returns:
        Ordered dependency step IDs that exist in the compiled graph.
    """
    dependencies: list[str] = []
    if "read-write.discovery" in existing_step_ids:
        dependencies.append("read-write.discovery")
    if "read-write.jwks" in existing_step_ids:
        dependencies.append("read-write.jwks")
    if test.resource_group is not None:
        auth_step = f"read-write.{test.resource_group}.psu-authorization"
        token_step = f"read-write.{test.resource_group}.client-credentials-token"
        if auth_step in existing_step_ids:
            dependencies.append(auth_step)
        elif token_step in existing_step_ids:
            dependencies.append(token_step)
    return tuple(dict.fromkeys(dependencies))


def _omitted_mandatory_endpoint_ids(
    catalogue: Catalogue,
    *,
    selected_resource_groups: frozenset[str],
    selected_endpoint_ids: frozenset[str],
) -> tuple[str, ...]:
    """Return mandatory endpoints omitted from selected coverage.

    Args:
        catalogue: Loaded catalogue.
        selected_resource_groups: Selected resource groups.
        selected_endpoint_ids: Selected endpoint IDs.

    Returns:
        Ordered mandatory endpoint IDs omitted by the plan.
    """
    omitted: list[str] = []
    for endpoint in catalogue.endpoints:
        if endpoint.requirement != "mandatory":
            continue
        if endpoint.resource_group is not None and endpoint.resource_group not in selected_resource_groups:
            continue
        if endpoint.endpoint_id not in selected_endpoint_ids:
            omitted.append(endpoint.endpoint_id)
    return tuple(omitted)


def _mandatory_endpoint_ids(
    catalogue: Catalogue,
    *,
    selected_resource_groups: frozenset[str],
) -> tuple[str, ...]:
    """Return mandatory endpoint IDs in the selected target scope.

    Args:
        catalogue: Loaded catalogue being compiled.
        selected_resource_groups: Selected resource-group IDs. Empty for
            specifications such as DCR that do not use resource groups.

    Returns:
        Mandatory endpoint IDs in selected catalogue order.
    """
    mandatory: list[str] = []
    for endpoint in catalogue.endpoints:
        if endpoint.requirement != "mandatory":
            continue
        if endpoint.resource_group is not None and endpoint.resource_group not in selected_resource_groups:
            continue
        mandatory.append(endpoint.endpoint_id)
    return tuple(mandatory)


def _omitted_mandatory_endpoint_ids_by_resource_group(
    catalogue: Catalogue,
    *,
    selected_resource_groups: tuple[str, ...],
    selected_endpoint_ids: frozenset[str],
) -> Mapping[str, tuple[str, ...]]:
    """Return mandatory endpoint omissions keyed by selected resource group.

    Args:
        catalogue: Loaded catalogue being compiled.
        selected_resource_groups: Ordered selected resource groups.
        selected_endpoint_ids: Endpoint IDs selected for execution.

    Returns:
        Immutable mapping from resource-group ID to mandatory endpoint IDs
        omitted from the run.
    """
    grouped: dict[str, list[str]] = {group: [] for group in selected_resource_groups}
    for endpoint in catalogue.endpoints:
        if endpoint.resource_group is None or endpoint.resource_group not in grouped:
            continue
        if endpoint.requirement != "mandatory":
            continue
        if endpoint.endpoint_id in selected_endpoint_ids:
            continue
        grouped[endpoint.resource_group].append(endpoint.endpoint_id)
    return MappingProxyType({group: tuple(ids) for group, ids in grouped.items()})


def _first_blocking_dependency(
    step: CompiledCatalogueStep,
    results_by_step_id: Mapping[str, CatalogueStepResult],
) -> str | None:
    """Return the first failed or skipped dependency for a step.

    Args:
        step: Step about to execute.
        results_by_step_id: Results already produced by prior steps.

    Returns:
        Blocking dependency step ID, or ``None`` when all dependencies passed.

    Raises:
        CatalogueExecutionError: If a dependency is not present in prior
            results, meaning the graph is not topologically ordered.
    """
    for dependency in step.dependencies:
        dependency_result = results_by_step_id.get(dependency)
        if dependency_result is None:
            raise CatalogueExecutionError(
                f"Step {step.step_id!r} depends on {dependency!r}, but that dependency has not run"
            )
        if dependency_result.outcome != "passed":
            return dependency
    return None


def _handler_for(
    step: CompiledCatalogueStep,
    primitive_handlers: Mapping[str, PrimitiveHandler],
) -> PrimitiveHandler:
    """Return the handler for a compiled step.

    Args:
        step: Step requiring a primitive handler.
        primitive_handlers: Mapping keyed by primitive ID or primitive type.

    Returns:
        The handler callable.

    Raises:
        CatalogueExecutionError: If no handler exists for the primitive ID/type.
    """
    handler = primitive_handlers.get(step.primitive_id) or primitive_handlers.get(step.primitive_type)
    if handler is None:
        raise CatalogueExecutionError(
            f"No primitive handler registered for primitive {step.primitive_id!r} (type {step.primitive_type!r})"
        )
    return handler


def _skipped_result(step: CompiledCatalogueStep, *, blocked_by: str) -> CatalogueStepResult:
    """Build a skipped result for a dependency-blocked step.

    Args:
        step: Step that could not run.
        blocked_by: Dependency step ID that failed or was skipped.

    Returns:
        A skipped :class:`CatalogueStepResult`.
    """
    return CatalogueStepResult(
        step_id=step.step_id,
        primitive_id=step.primitive_id,
        outcome="skipped",
        detail=f"Blocked by prerequisite {blocked_by!r}",
        test_id=step.test_id,
        endpoint_id=step.endpoint_id,
        resource_group=step.resource_group,
    )
