"""Deterministic compiler for configuration-driven participant plans."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from enum import StrEnum

from conformance.configuration_contracts.diagnostics import (
    ConfigurationContractError,
    ConfigurationDiagnostic,
    DiagnosticCode,
    DiagnosticSeverity,
)
from conformance.configuration_contracts.loader import (
    dump_requirements_catalogue,
    dump_test_definition_catalogue,
    participant_plan_to_document,
    requirements_catalogue_to_document,
    suite_release_to_document,
    test_definition_catalogue_to_document,
    validate_catalogue_references,
)
from conformance.configuration_contracts.models import (
    CompilationFinding,
    FindingSeverity,
    FindingSourceDocument,
    InputResolutionSource,
    ParticipantPlan,
    PredefinedInput,
    PredefinedInputValue,
    RequirementsCatalogue,
    RequirementTargetType,
    ResolutionReason,
    ResolvedCapability,
    ResolvedEndpoint,
    ResolvedPlan,
    ResolvedPlanProvenance,
    ResolvedPredefinedInput,
    ResolvedRequirement,
    ResolvedTestInstance,
    SelectionOrigin,
    StableId,
    SuiteRelease,
    TestDefinition,
    TestDefinitionCatalogue,
)
from conformance.json_types import JsonValue


class CompilationFindingCode(StrEnum):
    """Stable machine-readable findings emitted by participant-plan resolution."""

    SUITE_RELEASE_MISMATCH = "plan.reference.suite-release-mismatch"
    SPECIFICATION_MISMATCH = "plan.reference.specification-mismatch"
    CAPABILITY_UNKNOWN = "plan.selection.capability-unknown"
    SCOPE_EMPTY = "plan.selection.scope-empty"
    INPUT_UNKNOWN = "plan.selection.input-unknown"
    INPUT_NOT_APPLICABLE = "plan.selection.input-not-applicable"
    INPUT_INVALID = "plan.selection.input-invalid"
    INPUT_REQUIRED = "plan.selection.input-required"
    RULE_UNSUPPORTED = "plan.requirement.rule-unsupported"
    REQUIREMENT_UNCOVERED = "plan.test.requirement-uncovered"
    REQUIREMENT_NOT_ASSESSED = "plan.test.requirement-not-assessed"


class ParticipantPlanCompilationError(ValueError):
    """Raised when strict MVP compilation produces one or more error findings."""

    def __init__(self, resolved_plan: ResolvedPlan) -> None:
        """Create an error that retains the complete inspectable resolution."""
        if resolved_plan.selection_valid:
            raise ValueError("ParticipantPlanCompilationError requires an invalid resolved plan")
        self.resolved_plan = resolved_plan
        summary = "; ".join(
            f"{finding.code!s} at {finding.instance_path or '<root>'}: {finding.message}"
            for finding in resolved_plan.findings
        )
        super().__init__(summary)


def compile_participant_plan(
    suite_release: SuiteRelease,
    requirements_catalogue: RequirementsCatalogue,
    test_definition_catalogue: TestDefinitionCatalogue,
    participant_plan: ParticipantPlan,
) -> ResolvedPlan:
    """Compile participant intent under the strict MVP enforcement policy.

    The compiler always constructs deterministic resolved output first. Invalid
    participant intent then raises :class:`ParticipantPlanCompilationError`,
    whose ``resolved_plan`` retains every finding and partial resolution.
    Trusted catalogue or suite-release inconsistencies raise
    :class:`ConfigurationContractError` instead.
    """
    resolved_plan = resolve_participant_plan(
        suite_release,
        requirements_catalogue,
        test_definition_catalogue,
        participant_plan,
    )
    if not resolved_plan.selection_valid:
        raise ParticipantPlanCompilationError(resolved_plan)
    return resolved_plan


def resolve_participant_plan(
    suite_release: SuiteRelease,
    requirements_catalogue: RequirementsCatalogue,
    test_definition_catalogue: TestDefinitionCatalogue,
    participant_plan: ParticipantPlan,
) -> ResolvedPlan:
    """Resolve participant intent while retaining findings for strict rejection."""
    _validate_trusted_compiler_inputs(suite_release, requirements_catalogue, test_definition_catalogue)

    findings = _plan_reference_findings(suite_release, requirements_catalogue, participant_plan)
    capabilities, selected_capability_ids = _resolve_capabilities(requirements_catalogue, participant_plan, findings)
    requirements = _resolve_requirements(requirements_catalogue, selected_capability_ids, findings)
    endpoints = _resolve_endpoints(requirements_catalogue, requirements)
    predefined_inputs = _resolve_predefined_inputs(
        requirements_catalogue,
        participant_plan,
        requirements,
        findings,
    )
    test_instances = _resolve_test_instances(
        test_definition_catalogue,
        selected_capability_ids,
        {endpoint.id for endpoint in endpoints},
        {requirement.id for requirement in requirements},
    )
    endpoints = _include_dependency_endpoints(
        requirements_catalogue,
        test_definition_catalogue,
        endpoints,
        test_instances,
    )
    _append_uncovered_requirement_findings(requirements, test_instances, findings)
    immutable_findings = tuple(findings)
    return ResolvedPlan(
        schema_version="1.0",
        document_type="resolved-plan",
        id=_resolved_plan_id(
            suite_release,
            requirements_catalogue,
            test_definition_catalogue,
            participant_plan,
        ),
        selection_valid=not any(finding.severity is FindingSeverity.ERROR for finding in immutable_findings),
        scheme=requirements_catalogue.scheme,
        specification=requirements_catalogue.specification,
        security_profile=participant_plan.security_profile,
        capabilities=capabilities,
        endpoints=endpoints,
        requirements=requirements,
        predefined_inputs=predefined_inputs,
        test_instances=test_instances,
        findings=immutable_findings,
        provenance=ResolvedPlanProvenance(
            participant_plan_id=participant_plan.id,
            suite_release_id=suite_release.id,
            suite_release_version=suite_release.release_version,
            suite_published_at=suite_release.published_at,
            requirements_catalogue_id=requirements_catalogue.id,
            test_definition_catalogue_id=test_definition_catalogue.id,
            tool_releases=suite_release.tool_releases,
            artifacts=suite_release.artifacts,
        ),
    )


def _validate_trusted_compiler_inputs(
    suite_release: SuiteRelease,
    requirements_catalogue: RequirementsCatalogue,
    test_definition_catalogue: TestDefinitionCatalogue,
) -> None:
    diagnostics = list(validate_catalogue_references(requirements_catalogue, test_definition_catalogue))
    required_artifacts = (
        ("requirements-catalogue", requirements_catalogue.id, requirements_catalogue.schema_version),
        ("test-definition-catalogue", test_definition_catalogue.id, test_definition_catalogue.schema_version),
    )
    artifact_indexes = {
        (str(artifact.kind), artifact.id): (index, artifact) for index, artifact in enumerate(suite_release.artifacts)
    }
    for kind, identifier, schema_version in required_artifacts:
        indexed_artifact = artifact_indexes.get((kind, identifier))
        if indexed_artifact is None:
            diagnostics.append(
                ConfigurationDiagnostic(
                    code=DiagnosticCode.ARTIFACT_UNRESOLVED,
                    severity=DiagnosticSeverity.ERROR,
                    message=f"Suite release does not bind {kind} {identifier!s}",
                    instance_path="/artifacts",
                )
            )
            continue
        index, artifact = indexed_artifact
        if artifact.schema_version != schema_version:
            diagnostics.append(
                ConfigurationDiagnostic(
                    code=DiagnosticCode.ARTIFACT_UNRESOLVED,
                    severity=DiagnosticSeverity.ERROR,
                    message=(
                        f"Suite release binds {kind} {identifier!s} at schema version "
                        f"{artifact.schema_version!r}, not {schema_version!r}"
                    ),
                    instance_path=f"/artifacts/{index}/schemaVersion",
                )
            )
            continue
        serialized = (
            dump_requirements_catalogue(requirements_catalogue)
            if kind == "requirements-catalogue"
            else dump_test_definition_catalogue(test_definition_catalogue)
        ).encode("utf-8")
        actual_digest = f"sha256:{hashlib.sha256(serialized).hexdigest()}"
        if artifact.digest != actual_digest:
            diagnostics.append(
                ConfigurationDiagnostic(
                    code=DiagnosticCode.ARTIFACT_DIGEST_MISMATCH,
                    severity=DiagnosticSeverity.ERROR,
                    message=(
                        f"Supplied {kind} {identifier!s} has digest {actual_digest}; "
                        f"suite release requires {artifact.digest!s}"
                    ),
                    instance_path=f"/artifacts/{index}/digest",
                )
            )
    diagnostics.extend(_trusted_requirement_rule_diagnostics(requirements_catalogue))
    diagnostics.extend(_trusted_capability_dependency_diagnostics(requirements_catalogue))
    diagnostics.extend(_trusted_test_dependency_diagnostics(test_definition_catalogue))
    if diagnostics:
        raise ConfigurationContractError(tuple(diagnostics))


def _trusted_requirement_rule_diagnostics(
    requirements_catalogue: RequirementsCatalogue,
) -> tuple[ConfigurationDiagnostic, ...]:
    diagnostics: list[ConfigurationDiagnostic] = []
    endpoint_rules_by_capability: dict[StableId, set[StableId]] = defaultdict(set)
    input_rules_by_capability: dict[StableId, set[StableId]] = defaultdict(set)
    for requirement in requirements_catalogue.requirements:
        if requirement.rule.type != "required-when-capability-selected":
            continue
        targets = (
            endpoint_rules_by_capability
            if requirement.rule.target_type is RequirementTargetType.ENDPOINT
            else input_rules_by_capability
        )
        targets[requirement.rule.capability_id].add(requirement.rule.target_id)

    for capability_index, capability in enumerate(requirements_catalogue.capabilities):
        declared_endpoint_ids = set(capability.required_endpoint_ids)
        rule_endpoint_ids = endpoint_rules_by_capability[capability.id]
        if declared_endpoint_ids != rule_endpoint_ids:
            diagnostics.append(
                ConfigurationDiagnostic(
                    code=DiagnosticCode.RULE_INCONSISTENT,
                    severity=DiagnosticSeverity.ERROR,
                    message=(
                        f"Capability {capability.id!s} requiredEndpointIds do not match its endpoint requirement rules"
                    ),
                    instance_path=f"/capabilities/{capability_index}/requiredEndpointIds",
                )
            )
    for input_index, predefined_input in enumerate(requirements_catalogue.predefined_inputs):
        rule_capability_ids = {
            capability_id
            for capability_id, input_ids in input_rules_by_capability.items()
            if predefined_input.id in input_ids
        }
        if set(predefined_input.required_for_capability_ids) != rule_capability_ids:
            diagnostics.append(
                ConfigurationDiagnostic(
                    code=DiagnosticCode.RULE_INCONSISTENT,
                    severity=DiagnosticSeverity.ERROR,
                    message=(
                        f"Predefined input {predefined_input.id!s} requiredForCapabilityIds do not match "
                        "its requirement rules"
                    ),
                    instance_path=f"/predefinedInputs/{input_index}/requiredForCapabilityIds",
                )
            )
    return tuple(diagnostics)


def _trusted_capability_dependency_diagnostics(
    requirements_catalogue: RequirementsCatalogue,
) -> tuple[ConfigurationDiagnostic, ...]:
    capabilities = {capability.id: capability for capability in requirements_catalogue.capabilities}
    indexes = {capability.id: index for index, capability in enumerate(requirements_catalogue.capabilities)}
    diagnostics: list[ConfigurationDiagnostic] = []
    state: dict[StableId, int] = {}

    def visit(capability_id: StableId) -> None:
        state[capability_id] = 1
        capability = capabilities[capability_id]
        for dependency_index, dependency_id in enumerate(capability.required_capability_ids):
            instance_path = f"/capabilities/{indexes[capability_id]}/requiredCapabilityIds/{dependency_index}"
            if dependency_id not in capabilities:
                diagnostics.append(
                    ConfigurationDiagnostic(
                        code=DiagnosticCode.REFERENCE_UNRESOLVED,
                        severity=DiagnosticSeverity.ERROR,
                        message=f"Referenced capability {dependency_id!s} does not exist",
                        instance_path=instance_path,
                    )
                )
                continue
            if state.get(dependency_id) == 1:
                diagnostics.append(
                    ConfigurationDiagnostic(
                        code=DiagnosticCode.DEPENDENCY_CYCLE,
                        severity=DiagnosticSeverity.ERROR,
                        message=f"Capability dependency {dependency_id!s} creates a cycle",
                        instance_path=instance_path,
                    )
                )
            elif state.get(dependency_id, 0) == 0:
                visit(dependency_id)
        state[capability_id] = 2

    for capability in requirements_catalogue.capabilities:
        if state.get(capability.id, 0) == 0:
            visit(capability.id)
    return tuple(diagnostics)


def _trusted_test_dependency_diagnostics(
    test_definition_catalogue: TestDefinitionCatalogue,
) -> tuple[ConfigurationDiagnostic, ...]:
    definitions = {
        test_definition.id: test_definition for test_definition in test_definition_catalogue.test_definitions
    }
    indexes = {
        test_definition.id: index for index, test_definition in enumerate(test_definition_catalogue.test_definitions)
    }
    diagnostics: list[ConfigurationDiagnostic] = []
    state: dict[StableId, int] = {}

    def visit(test_id: StableId) -> None:
        state[test_id] = 1
        definition = definitions[test_id]
        for dependency_index, dependency_id in enumerate(definition.dependencies):
            instance_path = f"/testDefinitions/{indexes[test_id]}/dependencies/{dependency_index}"
            if dependency_id not in definitions:
                diagnostics.append(
                    ConfigurationDiagnostic(
                        code=DiagnosticCode.REFERENCE_UNRESOLVED,
                        severity=DiagnosticSeverity.ERROR,
                        message=f"Referenced test definition {dependency_id!s} does not exist",
                        instance_path=instance_path,
                    )
                )
                continue
            if state.get(dependency_id) == 1:
                diagnostics.append(
                    ConfigurationDiagnostic(
                        code=DiagnosticCode.DEPENDENCY_CYCLE,
                        severity=DiagnosticSeverity.ERROR,
                        message=f"Test dependency {dependency_id!s} creates a cycle",
                        instance_path=instance_path,
                    )
                )
            elif state.get(dependency_id, 0) == 0:
                visit(dependency_id)
        state[test_id] = 2

    for definition in test_definition_catalogue.test_definitions:
        if state.get(definition.id, 0) == 0:
            visit(definition.id)
    return tuple(diagnostics)


def _plan_reference_findings(
    suite_release: SuiteRelease,
    requirements_catalogue: RequirementsCatalogue,
    participant_plan: ParticipantPlan,
) -> list[CompilationFinding]:
    findings: list[CompilationFinding] = []
    if participant_plan.suite_release_id != suite_release.id:
        findings.append(
            _finding(
                CompilationFindingCode.SUITE_RELEASE_MISMATCH,
                (
                    f"Participant plan selects suite release {participant_plan.suite_release_id!s}; "
                    f"supplied release is {suite_release.id!s}"
                ),
                source_document=FindingSourceDocument.PARTICIPANT_PLAN,
                instance_path="/suiteReleaseId",
                related_ids=(participant_plan.suite_release_id, suite_release.id),
            )
        )
    specification = participant_plan.specification
    expected = requirements_catalogue.specification
    if (
        participant_plan.scheme != requirements_catalogue.scheme
        or specification.id != expected.id
        or specification.version != expected.version
        or specification.requirements_scope != expected.requirements_scope
    ):
        findings.append(
            _finding(
                CompilationFindingCode.SPECIFICATION_MISMATCH,
                "Participant plan specification does not match the supplied requirements catalogue",
                source_document=FindingSourceDocument.PARTICIPANT_PLAN,
                instance_path="/specification",
                related_ids=(participant_plan.id, requirements_catalogue.id),
            )
        )
    return findings


def _resolve_capabilities(
    requirements_catalogue: RequirementsCatalogue,
    participant_plan: ParticipantPlan,
    findings: list[CompilationFinding],
) -> tuple[tuple[ResolvedCapability, ...], set[StableId]]:
    capabilities = {capability.id: capability for capability in requirements_catalogue.capabilities}
    catalogue_ids = set(capabilities)
    explicit_ids = set(participant_plan.selected_capability_ids).intersection(catalogue_ids)
    selected_ids = set(explicit_ids)
    required_by: dict[StableId, set[StableId]] = defaultdict(set)

    def include_dependencies(capability_id: StableId) -> None:
        for dependency_id in capabilities[capability_id].required_capability_ids:
            required_by[dependency_id].add(capability_id)
            if dependency_id not in selected_ids:
                selected_ids.add(dependency_id)
                include_dependencies(dependency_id)

    for capability_id in tuple(explicit_ids):
        include_dependencies(capability_id)

    indexes = {capability_id: index for index, capability_id in enumerate(participant_plan.selected_capability_ids)}
    for capability_id in sorted(set(participant_plan.selected_capability_ids).difference(catalogue_ids)):
        findings.append(
            _finding(
                CompilationFindingCode.CAPABILITY_UNKNOWN,
                f"Selected capability {capability_id!s} does not exist in the requirements catalogue",
                source_document=FindingSourceDocument.PARTICIPANT_PLAN,
                instance_path=f"/selectedCapabilityIds/{indexes[capability_id]}",
                related_ids=(capability_id,),
            )
        )
    if not selected_ids:
        findings.append(
            _finding(
                CompilationFindingCode.SCOPE_EMPTY,
                "Participant plan must select at least one supported capability",
                source_document=FindingSourceDocument.PARTICIPANT_PLAN,
                instance_path="/selectedCapabilityIds",
                related_ids=(participant_plan.id,),
            )
        )
    resolved = tuple(
        ResolvedCapability(
            id=capability.id,
            origin=(SelectionOrigin.EXPLICIT if capability.id in explicit_ids else SelectionOrigin.INFERRED),
            reasons=(
                ResolutionReason(
                    code=StableId(
                        "plan.capability.explicit"
                        if capability.id in explicit_ids
                        else "requirements.capability.required"
                    ),
                    source_ids=(
                        (participant_plan.id, capability.id)
                        if capability.id in explicit_ids
                        else tuple(sorted(required_by[capability.id]))
                    ),
                ),
            ),
        )
        for capability in requirements_catalogue.capabilities
        if capability.id in selected_ids
    )
    return resolved, selected_ids


def _resolve_requirements(
    requirements_catalogue: RequirementsCatalogue,
    selected_capability_ids: set[StableId],
    findings: list[CompilationFinding],
) -> tuple[ResolvedRequirement, ...]:
    resolved: list[ResolvedRequirement] = []
    for index, requirement in enumerate(requirements_catalogue.requirements):
        if requirement.rule.type != "required-when-capability-selected":
            findings.append(
                _finding(
                    CompilationFindingCode.RULE_UNSUPPORTED,
                    f"Requirement {requirement.id!s} uses unsupported rule {requirement.rule.type!r}",
                    source_document=FindingSourceDocument.REQUIREMENTS_CATALOGUE,
                    instance_path=f"/requirements/{index}/rule/type",
                    related_ids=(requirement.id,),
                )
            )
            continue
        if requirement.rule.capability_id not in selected_capability_ids:
            continue
        resolved.append(
            ResolvedRequirement(
                id=requirement.id,
                capability_id=requirement.rule.capability_id,
                target_type=requirement.rule.target_type,
                target_id=requirement.rule.target_id,
                normative_reference_ids=requirement.normative_reference_ids,
                reasons=(
                    ResolutionReason(
                        code=StableId("requirements.rule.applied"),
                        source_ids=(requirement.id, requirement.rule.capability_id),
                    ),
                ),
                assessment=requirement.assessment,
            )
        )
    return tuple(resolved)


def _resolve_endpoints(
    requirements_catalogue: RequirementsCatalogue,
    requirements: tuple[ResolvedRequirement, ...],
) -> tuple[ResolvedEndpoint, ...]:
    requirement_ids_by_endpoint: dict[StableId, list[StableId]] = defaultdict(list)
    for requirement in requirements:
        if requirement.target_type is RequirementTargetType.ENDPOINT:
            requirement_ids_by_endpoint[requirement.target_id].append(requirement.id)
    return tuple(
        ResolvedEndpoint(
            id=endpoint.id,
            origin=SelectionOrigin.INFERRED,
            requirement_ids=tuple(requirement_ids_by_endpoint[endpoint.id]),
            reasons=(
                ResolutionReason(
                    code=StableId("requirements.endpoint.inferred"),
                    source_ids=tuple(requirement_ids_by_endpoint[endpoint.id]),
                ),
            ),
        )
        for endpoint in requirements_catalogue.endpoints
        if endpoint.id in requirement_ids_by_endpoint
    )


def _include_dependency_endpoints(
    requirements_catalogue: RequirementsCatalogue,
    test_definition_catalogue: TestDefinitionCatalogue,
    endpoints: tuple[ResolvedEndpoint, ...],
    test_instances: tuple[ResolvedTestInstance, ...],
) -> tuple[ResolvedEndpoint, ...]:
    """Include operation endpoints used only by generated setup dependencies."""
    selected_ids = {endpoint.id for endpoint in endpoints}
    definitions = {definition.id: definition for definition in test_definition_catalogue.test_definitions}
    required_by_endpoint: dict[StableId, list[StableId]] = defaultdict(list)
    for instance in test_instances:
        definition = definitions[instance.test_definition_id]
        if definition.request.endpoint_id not in selected_ids:
            required_by_endpoint[definition.request.endpoint_id].append(instance.id)
    dependency_endpoints = tuple(
        ResolvedEndpoint(
            id=endpoint.id,
            origin=SelectionOrigin.INFERRED,
            requirement_ids=(),
            reasons=(
                ResolutionReason(
                    code=StableId("tests.dependency.endpoint"),
                    source_ids=tuple(required_by_endpoint[endpoint.id]),
                ),
            ),
        )
        for endpoint in requirements_catalogue.endpoints
        if endpoint.id in required_by_endpoint
    )
    return (*endpoints, *dependency_endpoints)


def _resolve_predefined_inputs(
    requirements_catalogue: RequirementsCatalogue,
    participant_plan: ParticipantPlan,
    requirements: tuple[ResolvedRequirement, ...],
    findings: list[CompilationFinding],
) -> tuple[ResolvedPredefinedInput, ...]:
    catalogue_inputs = {
        predefined_input.id: predefined_input for predefined_input in requirements_catalogue.predefined_inputs
    }
    participant_inputs = {
        participant_input.input_id: participant_input for participant_input in participant_plan.predefined_inputs
    }
    participant_indexes = {
        participant_input.input_id: index for index, participant_input in enumerate(participant_plan.predefined_inputs)
    }
    required_ids: dict[StableId, list[StableId]] = defaultdict(list)
    for requirement in requirements:
        if requirement.target_type is RequirementTargetType.PREDEFINED_INPUT:
            required_ids[requirement.target_id].append(requirement.id)

    for input_id in sorted(set(participant_inputs).difference(catalogue_inputs)):
        findings.append(
            _finding(
                CompilationFindingCode.INPUT_UNKNOWN,
                f"Participant input {input_id!s} does not exist in the requirements catalogue",
                source_document=FindingSourceDocument.PARTICIPANT_PLAN,
                instance_path=f"/predefinedInputs/{participant_indexes[input_id]}/inputId",
                related_ids=(input_id,),
            )
        )
    for input_id in sorted(set(participant_inputs).intersection(catalogue_inputs).difference(required_ids)):
        findings.append(
            _finding(
                CompilationFindingCode.INPUT_NOT_APPLICABLE,
                f"Participant input {input_id!s} is not applicable to the selected capabilities",
                source_document=FindingSourceDocument.PARTICIPANT_PLAN,
                instance_path=f"/predefinedInputs/{participant_indexes[input_id]}",
                related_ids=(input_id,),
            )
        )

    resolved: list[ResolvedPredefinedInput] = []
    for predefined_input in requirements_catalogue.predefined_inputs:
        if predefined_input.id not in required_ids:
            continue
        participant_input = participant_inputs.get(predefined_input.id)
        if participant_input is not None:
            if (
                predefined_input.value_type == "string"
                and not isinstance(participant_input.value, str)
                or predefined_input.value_type == "standing-order-frequency-v4"
                and isinstance(participant_input.value, str)
            ):
                findings.append(
                    _finding(
                        CompilationFindingCode.INPUT_INVALID,
                        (f"Participant input {predefined_input.id!s} does not match {predefined_input.value_type!s}"),
                        source_document=FindingSourceDocument.PARTICIPANT_PLAN,
                        instance_path=f"/predefinedInputs/{participant_indexes[predefined_input.id]}/value",
                        related_ids=(predefined_input.id,),
                    )
                )
                continue
            resolved.append(
                _resolved_input(
                    predefined_input,
                    participant_input.value,
                    source=InputResolutionSource.PARTICIPANT,
                    requirement_ids=tuple(required_ids[predefined_input.id]),
                    source_ids=(participant_plan.id, predefined_input.id),
                )
            )
            continue
        if predefined_input.default_value is not None:
            resolved.append(
                _resolved_input(
                    predefined_input,
                    predefined_input.default_value,
                    source=InputResolutionSource.DEFAULT,
                    requirement_ids=tuple(required_ids[predefined_input.id]),
                    source_ids=(requirements_catalogue.id, predefined_input.id),
                )
            )
            continue
        findings.append(
            _finding(
                CompilationFindingCode.INPUT_REQUIRED,
                f"Required predefined input {predefined_input.id!s} was not supplied and has no default",
                source_document=FindingSourceDocument.PARTICIPANT_PLAN,
                instance_path="/predefinedInputs",
                related_ids=(predefined_input.id, *required_ids[predefined_input.id]),
            )
        )
    return tuple(resolved)


def _resolved_input(
    predefined_input: PredefinedInput,
    value: PredefinedInputValue,
    *,
    source: InputResolutionSource,
    requirement_ids: tuple[StableId, ...],
    source_ids: tuple[StableId, ...],
) -> ResolvedPredefinedInput:
    redacted = predefined_input.sensitivity != "non-sensitive"
    reason_code = (
        StableId("plan.input.supplied")
        if source is InputResolutionSource.PARTICIPANT
        else StableId("requirements.input.defaulted")
    )
    return ResolvedPredefinedInput(
        id=predefined_input.id,
        source=source,
        value=None if redacted else value,
        redacted=redacted,
        requirement_ids=requirement_ids,
        reasons=(ResolutionReason(code=reason_code, source_ids=source_ids),),
    )


def _resolve_test_instances(
    test_definition_catalogue: TestDefinitionCatalogue,
    selected_capability_ids: set[StableId],
    selected_endpoint_ids: set[StableId],
    applicable_requirement_ids: set[StableId],
) -> tuple[ResolvedTestInstance, ...]:
    definitions = {definition.id: definition for definition in test_definition_catalogue.test_definitions}
    direct_ids = {
        definition.id
        for definition in test_definition_catalogue.test_definitions
        if definition.capability_id in selected_capability_ids
        and definition.request.endpoint_id in selected_endpoint_ids
    }
    ordered_ids: list[StableId] = []
    visited: set[StableId] = set()
    dependency_of: dict[StableId, set[StableId]] = defaultdict(set)

    def visit(test_id: StableId, dependent_id: StableId | None = None) -> None:
        if dependent_id is not None:
            dependency_of[test_id].add(dependent_id)
        if test_id in visited:
            return
        definition = definitions[test_id]
        for dependency_id in definition.dependencies:
            visit(dependency_id, test_id)
        visited.add(test_id)
        ordered_ids.append(test_id)

    for definition in test_definition_catalogue.test_definitions:
        if definition.id in direct_ids:
            visit(definition.id)

    instance_ids = {test_id: _test_instance_id(test_id) for test_id in ordered_ids}
    return tuple(
        _resolved_test_instance(
            definitions[test_id],
            instance_ids,
            direct=test_id in direct_ids,
            dependent_ids=dependency_of.get(test_id, set()),
            applicable_requirement_ids=applicable_requirement_ids,
        )
        for test_id in ordered_ids
    )


def _resolved_test_instance(
    definition: TestDefinition,
    instance_ids: dict[StableId, StableId],
    *,
    direct: bool,
    dependent_ids: set[StableId],
    applicable_requirement_ids: set[StableId],
) -> ResolvedTestInstance:
    reasons: list[ResolutionReason] = []
    if direct:
        reasons.append(
            ResolutionReason(
                code=StableId("tests.applicability.selected"),
                source_ids=(definition.id, definition.capability_id, definition.request.endpoint_id),
            )
        )
    if dependent_ids:
        reasons.append(
            ResolutionReason(
                code=StableId("tests.dependency.required-by"),
                source_ids=tuple(sorted(dependent_ids)),
            )
        )
    return ResolvedTestInstance(
        id=instance_ids[definition.id],
        test_definition_id=definition.id,
        dependency_ids=tuple(instance_ids[dependency_id] for dependency_id in definition.dependencies),
        covered_requirement_ids=tuple(
            requirement_id
            for requirement_id in definition.covered_requirement_ids
            if requirement_id in applicable_requirement_ids
        ),
        reasons=tuple(reasons),
    )


def _append_uncovered_requirement_findings(
    requirements: tuple[ResolvedRequirement, ...],
    test_instances: tuple[ResolvedTestInstance, ...],
    findings: list[CompilationFinding],
) -> None:
    covered_requirement_ids = {
        requirement_id for test_instance in test_instances for requirement_id in test_instance.covered_requirement_ids
    }
    for requirement in requirements:
        if requirement.assessment == "documented-only":
            findings.append(
                CompilationFinding(
                    code=StableId(CompilationFindingCode.REQUIREMENT_NOT_ASSESSED.value),
                    severity=FindingSeverity.WARNING,
                    message=(
                        f"Applicable requirement {requirement.id!s} is documented but has no "
                        "deterministic test in this catalogue"
                    ),
                    source_document=FindingSourceDocument.RESOLVED_PLAN,
                    instance_path="/testInstances",
                    related_ids=(requirement.id,),
                )
            )
            continue
        if requirement.id not in covered_requirement_ids:
            findings.append(
                _finding(
                    CompilationFindingCode.REQUIREMENT_UNCOVERED,
                    f"Applicable requirement {requirement.id!s} has no selected test coverage",
                    source_document=FindingSourceDocument.RESOLVED_PLAN,
                    instance_path="/testInstances",
                    related_ids=(requirement.id,),
                )
            )


def _test_instance_id(test_definition_id: StableId) -> StableId:
    candidate = f"{test_definition_id!s}.instance"
    if len(candidate) <= 128:
        return StableId(candidate)
    digest = hashlib.sha256(str(test_definition_id).encode("ascii")).hexdigest()
    return StableId(f"test-instance:{digest}")


def _resolved_plan_id(
    suite_release: SuiteRelease,
    requirements_catalogue: RequirementsCatalogue,
    test_definition_catalogue: TestDefinitionCatalogue,
    participant_plan: ParticipantPlan,
) -> StableId:
    participant_document = participant_plan_to_document(participant_plan)
    selected_capability_ids: list[JsonValue] = []
    selected_capability_ids.extend(
        sorted(str(capability_id) for capability_id in participant_plan.selected_capability_ids)
    )
    predefined_inputs = participant_document["predefinedInputs"]
    if not isinstance(predefined_inputs, list):
        raise TypeError("Participant-plan serialization produced invalid predefinedInputs")
    participant_document["selectedCapabilityIds"] = selected_capability_ids
    participant_document["predefinedInputs"] = sorted(predefined_inputs, key=_participant_input_sort_key)
    compiler_input = {
        "participantPlan": participant_document,
        "requirementsCatalogue": requirements_catalogue_to_document(requirements_catalogue),
        "suiteRelease": suite_release_to_document(suite_release),
        "testDefinitionCatalogue": test_definition_catalogue_to_document(test_definition_catalogue),
    }
    canonical = json.dumps(compiler_input, separators=(",", ":"), sort_keys=True)
    return StableId(f"resolved-plan:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}")


def _participant_input_sort_key(value: JsonValue) -> str:
    if not isinstance(value, Mapping):
        return ""
    input_id = value.get("inputId")
    return input_id if isinstance(input_id, str) else ""


def _finding(
    code: CompilationFindingCode,
    message: str,
    *,
    source_document: FindingSourceDocument,
    instance_path: str,
    related_ids: Iterable[StableId],
) -> CompilationFinding:
    return CompilationFinding(
        code=StableId(code.value),
        severity=FindingSeverity.ERROR,
        message=message,
        source_document=source_document,
        instance_path=instance_path,
        related_ids=tuple(related_ids),
    )
