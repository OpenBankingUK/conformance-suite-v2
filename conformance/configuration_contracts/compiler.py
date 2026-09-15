"""Deterministic participant-plan resolution for the PIS walking skeleton."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping

from conformance.configuration_contracts.diagnostics import (
    ConfigurationDiagnostic,
    DiagnosticCode,
    DiagnosticSeverity,
)
from conformance.configuration_contracts.loader import (
    dump_requirements_catalogue,
    dump_suite_release,
    dump_test_definition_catalogue,
    participant_plan_to_document,
    validate_catalogue_references,
    verify_suite_release_artifacts,
)
from conformance.configuration_contracts.models import (
    CompilerFinding,
    CompilerFindingCode,
    CompilerFindingSeverity,
    ParticipantPlan,
    Requirement,
    RequirementsCatalogue,
    RequirementTargetType,
    ResolutionSource,
    ResolvedInput,
    ResolvedPlan,
    ResolvedPlanProvenance,
    ResolvedSelection,
    ResolvedTestInstance,
    Sha256Digest,
    StableId,
    SuiteRelease,
    TestDefinition,
    TestDefinitionCatalogue,
)

RESOLVED_PLAN_SCHEMA_VERSION = "1.0"
"""Resolved-plan schema emitted by this compiler."""

_REQUIREMENTS_ARTIFACT_KIND = "requirements-catalogue"
_TEST_DEFINITIONS_ARTIFACT_KIND = "test-definition-catalogue"


class ParticipantPlanCompilationError(ValueError):
    """Raised when strict MVP policy blocks an invalid resolved plan."""

    def __init__(self, resolved_plan: ResolvedPlan) -> None:
        """Create an error that retains the complete deterministic resolution."""
        self.resolved_plan = resolved_plan
        summary = "; ".join(
            f"{finding.code!s} at {finding.instance_path or '<root>'}: {finding.message}"
            for finding in resolved_plan.findings
            if finding.severity is CompilerFindingSeverity.ERROR
        )
        super().__init__(summary)


def compile_participant_plan(
    *,
    suite_release: SuiteRelease,
    requirements_catalogue: RequirementsCatalogue,
    test_definition_catalogue: TestDefinitionCatalogue,
    participant_plan: ParticipantPlan,
    artifact_bytes: Mapping[tuple[str, str], bytes],
) -> ResolvedPlan:
    """Resolve participant intent and enforce strict MVP compilation policy.

    Invalid intent still produces a complete resolved plan on
    :class:`ParticipantPlanCompilationError`, preserving findings and
    provenance without permitting execution.
    """
    resolved_plan = resolve_participant_plan(
        suite_release=suite_release,
        requirements_catalogue=requirements_catalogue,
        test_definition_catalogue=test_definition_catalogue,
        participant_plan=participant_plan,
        artifact_bytes=artifact_bytes,
    )
    if not resolved_plan.compilation_allowed:
        raise ParticipantPlanCompilationError(resolved_plan)
    return resolved_plan


def resolve_participant_plan(
    *,
    suite_release: SuiteRelease,
    requirements_catalogue: RequirementsCatalogue,
    test_definition_catalogue: TestDefinitionCatalogue,
    participant_plan: ParticipantPlan,
    artifact_bytes: Mapping[tuple[str, str], bytes],
) -> ResolvedPlan:
    """Resolve one participant plan without discarding invalid-plan findings."""
    findings: list[CompilerFinding] = []
    findings.extend(
        _configuration_diagnostic_to_finding(diagnostic)
        for diagnostic in verify_suite_release_artifacts(suite_release, artifact_bytes)
    )
    findings.extend(
        _configuration_diagnostic_to_finding(diagnostic)
        for diagnostic in validate_catalogue_references(requirements_catalogue, test_definition_catalogue)
    )
    findings.extend(
        _release_and_scope_findings(
            suite_release=suite_release,
            requirements_catalogue=requirements_catalogue,
            test_definition_catalogue=test_definition_catalogue,
            participant_plan=participant_plan,
            artifact_bytes=artifact_bytes,
        )
    )

    capabilities_by_id = {capability.id: capability for capability in requirements_catalogue.capabilities}
    participant_capability_counts = Counter(participant_plan.selected_capability_ids)
    for capability_id, count in sorted(participant_capability_counts.items(), key=lambda item: str(item[0])):
        if count > 1:
            findings.append(
                _finding(
                    CompilerFindingCode.SELECTION_DUPLICATE,
                    f"Capability {capability_id!s} must be selected at most once",
                    instance_path="/selectedCapabilityIds",
                    related_ids=(capability_id,),
                )
            )
        if capability_id not in capabilities_by_id:
            findings.append(
                _finding(
                    CompilerFindingCode.SELECTION_UNKNOWN,
                    f"Selected capability {capability_id!s} does not exist in the requirements catalogue",
                    instance_path="/selectedCapabilityIds",
                    related_ids=(capability_id,),
                )
            )

    selected_capability_ids = {
        capability_id for capability_id in participant_capability_counts if capability_id in capabilities_by_id
    }
    if not selected_capability_ids:
        findings.append(
            _finding(
                CompilerFindingCode.SELECTION_EMPTY,
                "At least one known capability must be selected for an executable plan",
                instance_path="/selectedCapabilityIds",
            )
        )
    selected_capabilities = tuple(
        ResolvedSelection(
            id=capability.id,
            source=ResolutionSource.EXPLICIT,
            source_ids=(participant_plan.id,),
        )
        for capability in requirements_catalogue.capabilities
        if capability.id in selected_capability_ids
    )

    applicable_requirements = tuple(
        requirement
        for requirement in requirements_catalogue.requirements
        if requirement.rule.capability_id in selected_capability_ids
    )
    resolved_requirements = tuple(
        ResolvedSelection(
            id=requirement.id,
            source=ResolutionSource.INFERRED,
            source_ids=(requirement.rule.capability_id,),
        )
        for requirement in applicable_requirements
    )

    endpoint_source_ids: dict[StableId, list[StableId]] = {}
    for capability in requirements_catalogue.capabilities:
        if capability.id not in selected_capability_ids:
            continue
        for endpoint_id in capability.required_endpoint_ids:
            endpoint_source_ids.setdefault(endpoint_id, []).append(capability.id)
    for requirement in applicable_requirements:
        if requirement.rule.target_type is RequirementTargetType.ENDPOINT:
            endpoint_source_ids.setdefault(requirement.rule.target_id, []).append(requirement.id)
    selected_endpoints = tuple(
        ResolvedSelection(
            id=endpoint.id,
            source=ResolutionSource.INFERRED,
            source_ids=_unique_ids(endpoint_source_ids[endpoint.id]),
        )
        for endpoint in requirements_catalogue.endpoints
        if endpoint.id in endpoint_source_ids
    )
    selected_endpoint_ids = {endpoint.id for endpoint in selected_endpoints}

    resolved_inputs, input_findings = _resolve_inputs(
        requirements_catalogue=requirements_catalogue,
        participant_plan=participant_plan,
        applicable_requirements=applicable_requirements,
    )
    findings.extend(input_findings)

    test_instances, test_findings = _resolve_test_instances(
        test_definition_catalogue=test_definition_catalogue,
        selected_capability_ids=selected_capability_ids,
        selected_endpoint_ids=selected_endpoint_ids,
        applicable_requirement_ids={requirement.id for requirement in applicable_requirements},
    )
    findings.extend(test_findings)
    ordered_findings = tuple(
        sorted(
            findings,
            key=lambda finding: (
                finding.instance_path,
                str(finding.code),
                tuple(str(identifier) for identifier in finding.related_ids),
                finding.message,
            ),
        )
    )
    valid = not any(finding.severity is CompilerFindingSeverity.ERROR for finding in ordered_findings)
    resolved_id = _resolved_plan_id(
        participant_plan=participant_plan,
        suite_release=suite_release,
        requirements_catalogue=requirements_catalogue,
        test_definition_catalogue=test_definition_catalogue,
    )
    return ResolvedPlan(
        schema_version=RESOLVED_PLAN_SCHEMA_VERSION,
        document_type="resolved-plan",
        id=resolved_id,
        valid=valid,
        compilation_allowed=valid,
        certification_eligible=valid,
        security_profile=participant_plan.security_profile,
        selected_capabilities=selected_capabilities,
        selected_endpoints=selected_endpoints,
        applicable_requirements=resolved_requirements,
        resolved_inputs=resolved_inputs,
        test_instances=test_instances,
        findings=ordered_findings,
        provenance=ResolvedPlanProvenance(
            suite_release_id=suite_release.id,
            suite_release_version=suite_release.release_version,
            suite_release_published_at=suite_release.published_at,
            tool_releases=suite_release.tool_releases,
            artifacts=suite_release.artifacts,
            participant_plan_id=participant_plan.id,
            participant_plan_digest=_sha256_digest(
                json.dumps(
                    participant_plan_to_document(participant_plan),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ),
            suite_release_digest=_sha256_digest(dump_suite_release(suite_release).encode()),
            requirements_catalogue_id=requirements_catalogue.id,
            test_definition_catalogue_id=test_definition_catalogue.id,
        ),
    )


def _release_and_scope_findings(
    *,
    suite_release: SuiteRelease,
    requirements_catalogue: RequirementsCatalogue,
    test_definition_catalogue: TestDefinitionCatalogue,
    participant_plan: ParticipantPlan,
    artifact_bytes: Mapping[tuple[str, str], bytes],
) -> tuple[CompilerFinding, ...]:
    findings: list[CompilerFinding] = []
    if participant_plan.suite_release_id != suite_release.id:
        findings.append(
            _finding(
                CompilerFindingCode.SUITE_RELEASE_MISMATCH,
                (
                    f"Participant plan targets suite release {participant_plan.suite_release_id!s}; "
                    f"supplied release is {suite_release.id!s}"
                ),
                instance_path="/suiteReleaseId",
                related_ids=(participant_plan.suite_release_id, suite_release.id),
            )
        )
    if (
        participant_plan.scheme != requirements_catalogue.scheme
        or participant_plan.specification.id != requirements_catalogue.specification.id
        or participant_plan.specification.version != requirements_catalogue.specification.version
        or participant_plan.specification.requirements_scope != requirements_catalogue.specification.requirements_scope
    ):
        findings.append(
            _finding(
                CompilerFindingCode.SCOPE_MISMATCH,
                "Participant plan scope does not match the supplied requirements catalogue",
                instance_path="/specification",
                related_ids=(requirements_catalogue.id,),
            )
        )
    if test_definition_catalogue.requirements_catalogue_id != requirements_catalogue.id:
        findings.append(
            _finding(
                DiagnosticCode.REFERENCE_UNRESOLVED,
                "Test-definition catalogue targets a different requirements catalogue",
                instance_path="/requirementsCatalogueId",
                related_ids=(
                    test_definition_catalogue.requirements_catalogue_id,
                    requirements_catalogue.id,
                ),
            )
        )
    artifacts = {(str(artifact.kind), str(artifact.id)) for artifact in suite_release.artifacts}
    for kind, identifier in (
        (_REQUIREMENTS_ARTIFACT_KIND, requirements_catalogue.id),
        (_TEST_DEFINITIONS_ARTIFACT_KIND, test_definition_catalogue.id),
    ):
        if (kind, str(identifier)) not in artifacts:
            findings.append(
                _finding(
                    CompilerFindingCode.RELEASE_ARTIFACT_MISSING,
                    f"Suite release does not bind {kind} {identifier!s}",
                    instance_path="/artifacts",
                    related_ids=(identifier,),
                )
            )
    for kind, identifier, typed_bytes in (
        (
            _REQUIREMENTS_ARTIFACT_KIND,
            requirements_catalogue.id,
            dump_requirements_catalogue(requirements_catalogue).encode(),
        ),
        (
            _TEST_DEFINITIONS_ARTIFACT_KIND,
            test_definition_catalogue.id,
            dump_test_definition_catalogue(test_definition_catalogue).encode(),
        ),
    ):
        supplied_bytes = artifact_bytes.get((kind, str(identifier)))
        if supplied_bytes is not None and supplied_bytes != typed_bytes:
            findings.append(
                _finding(
                    CompilerFindingCode.RELEASE_ARTIFACT_CONTENT_MISMATCH,
                    f"Loaded {kind} {identifier!s} does not match the release-bound bytes",
                    instance_path="/artifacts",
                    related_ids=(identifier,),
                )
            )
    return tuple(findings)


def _resolve_inputs(
    *,
    requirements_catalogue: RequirementsCatalogue,
    participant_plan: ParticipantPlan,
    applicable_requirements: tuple[Requirement, ...],
) -> tuple[tuple[ResolvedInput, ...], tuple[CompilerFinding, ...]]:
    required_input_sources: dict[StableId, list[StableId]] = {}
    for requirement in applicable_requirements:
        if requirement.rule.target_type is RequirementTargetType.PREDEFINED_INPUT:
            required_input_sources.setdefault(requirement.rule.target_id, []).append(requirement.id)

    known_inputs = {
        predefined_input.id: predefined_input for predefined_input in requirements_catalogue.predefined_inputs
    }
    participant_input_counts = Counter(
        participant_input.input_id for participant_input in participant_plan.predefined_inputs
    )
    participant_inputs = {
        participant_input.input_id: participant_input for participant_input in participant_plan.predefined_inputs
    }
    findings: list[CompilerFinding] = []
    for input_id, count in sorted(participant_input_counts.items(), key=lambda item: str(item[0])):
        if count > 1:
            findings.append(
                _finding(
                    CompilerFindingCode.INPUT_DUPLICATE,
                    f"Predefined input {input_id!s} must be supplied at most once",
                    instance_path="/predefinedInputs",
                    related_ids=(input_id,),
                )
            )
        if input_id not in known_inputs:
            findings.append(
                _finding(
                    CompilerFindingCode.INPUT_UNKNOWN,
                    f"Predefined input {input_id!s} does not exist in the requirements catalogue",
                    instance_path="/predefinedInputs",
                    related_ids=(input_id,),
                )
            )
        elif input_id not in required_input_sources:
            findings.append(
                _finding(
                    CompilerFindingCode.INPUT_NOT_APPLICABLE,
                    f"Predefined input {input_id!s} is not applicable to the selected capabilities",
                    instance_path="/predefinedInputs",
                    related_ids=(input_id,),
                )
            )

    resolved_inputs: list[ResolvedInput] = []
    for predefined_input in requirements_catalogue.predefined_inputs:
        source_ids = required_input_sources.get(predefined_input.id)
        if source_ids is None:
            continue
        participant_input = participant_inputs.get(predefined_input.id)
        if participant_input is None:
            findings.append(
                _finding(
                    CompilerFindingCode.INPUT_MISSING,
                    f"Required predefined input {predefined_input.id!s} was not supplied",
                    instance_path="/predefinedInputs",
                    related_ids=(predefined_input.id, *source_ids),
                )
            )
            continue
        value = None if predefined_input.sensitivity == "sensitive" else participant_input.value
        resolved_inputs.append(
            ResolvedInput(
                id=predefined_input.id,
                value_type=predefined_input.value_type,
                sensitivity=predefined_input.sensitivity,
                source=ResolutionSource.SUPPLIED,
                source_ids=_unique_ids((participant_plan.id, *source_ids)),
                value=value,
            )
        )
    return tuple(resolved_inputs), tuple(findings)


def _resolve_test_instances(
    *,
    test_definition_catalogue: TestDefinitionCatalogue,
    selected_capability_ids: set[StableId],
    selected_endpoint_ids: set[StableId],
    applicable_requirement_ids: set[StableId],
) -> tuple[tuple[ResolvedTestInstance, ...], tuple[CompilerFinding, ...]]:
    definitions_by_id = {
        test_definition.id: test_definition for test_definition in test_definition_catalogue.test_definitions
    }
    direct_ids: set[StableId] = set()
    source_ids_by_test: dict[StableId, list[StableId]] = {}
    covered_requirement_ids: set[StableId] = set()
    for test_definition in test_definition_catalogue.test_definitions:
        applicable_coverage = tuple(
            requirement_id
            for requirement_id in test_definition.covered_requirement_ids
            if requirement_id in applicable_requirement_ids
        )
        if (
            test_definition.capability_id in selected_capability_ids
            and test_definition.request.endpoint_id in selected_endpoint_ids
            and applicable_coverage
        ):
            direct_ids.add(test_definition.id)
            source_ids_by_test[test_definition.id] = list(applicable_coverage)
            covered_requirement_ids.update(applicable_coverage)

    findings = [
        _finding(
            CompilerFindingCode.REQUIREMENT_COVERAGE_MISSING,
            f"Applicable requirement {requirement_id!s} has no selected test definition",
            instance_path="/requirements",
            related_ids=(requirement_id,),
        )
        for requirement_id in sorted(applicable_requirement_ids - covered_requirement_ids, key=str)
    ]

    selected_ids = set(direct_ids)
    dependency_of: dict[StableId, list[StableId]] = {}

    def include_dependencies(test_id: StableId) -> None:
        for dependency_id in definitions_by_id[test_id].dependencies:
            dependency_of.setdefault(dependency_id, []).append(test_id)
            if dependency_id not in selected_ids:
                selected_ids.add(dependency_id)
                include_dependencies(dependency_id)

    for test_definition in test_definition_catalogue.test_definitions:
        if test_definition.id in direct_ids:
            include_dependencies(test_definition.id)

    ordered_definitions = _topologically_order_definitions(
        test_definition_catalogue.test_definitions,
        selected_ids=selected_ids,
    )
    instances = tuple(
        ResolvedTestInstance(
            id=_compiled_instance_id(test_definition.id),
            test_definition_id=test_definition.id,
            source=(ResolutionSource.INFERRED if test_definition.id in direct_ids else ResolutionSource.DEPENDENCY),
            source_ids=(
                _unique_ids(source_ids_by_test[test_definition.id])
                if test_definition.id in direct_ids
                else _unique_ids(dependency_of.get(test_definition.id, ()))
            ),
            dependency_instance_ids=tuple(
                _compiled_instance_id(dependency_id) for dependency_id in test_definition.dependencies
            ),
            covered_requirement_ids=tuple(
                requirement_id
                for requirement_id in test_definition.covered_requirement_ids
                if requirement_id in applicable_requirement_ids
            ),
        )
        for test_definition in ordered_definitions
    )
    return instances, tuple(findings)


def _topologically_order_definitions(
    definitions: tuple[TestDefinition, ...],
    *,
    selected_ids: set[StableId],
) -> tuple[TestDefinition, ...]:
    definitions_by_id = {definition.id: definition for definition in definitions}
    visited: set[StableId] = set()
    ordered: list[TestDefinition] = []

    def visit(test_id: StableId) -> None:
        if test_id in visited:
            return
        visited.add(test_id)
        for dependency_id in definitions_by_id[test_id].dependencies:
            if dependency_id in selected_ids:
                visit(dependency_id)
        ordered.append(definitions_by_id[test_id])

    for definition in definitions:
        if definition.id in selected_ids:
            visit(definition.id)
    return tuple(ordered)


def _compiled_instance_id(test_definition_id: StableId) -> StableId:
    return StableId(f"compiled:{test_definition_id!s}")


def _resolved_plan_id(
    *,
    participant_plan: ParticipantPlan,
    suite_release: SuiteRelease,
    requirements_catalogue: RequirementsCatalogue,
    test_definition_catalogue: TestDefinitionCatalogue,
) -> StableId:
    identity = {
        "participantPlan": participant_plan_to_document(participant_plan),
        "requirementsCatalogueId": str(requirements_catalogue.id),
        "suiteRelease": json.loads(dump_suite_release(suite_release)),
        "testDefinitionCatalogueId": str(test_definition_catalogue.id),
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return StableId(f"resolved:{hashlib.sha256(encoded).hexdigest()}")


def _sha256_digest(value: bytes) -> Sha256Digest:
    return Sha256Digest(f"sha256:{hashlib.sha256(value).hexdigest()}")


def _configuration_diagnostic_to_finding(diagnostic: ConfigurationDiagnostic) -> CompilerFinding:
    return CompilerFinding(
        code=StableId(diagnostic.code.value),
        severity=(
            CompilerFindingSeverity.ERROR
            if diagnostic.severity is DiagnosticSeverity.ERROR
            else CompilerFindingSeverity.WARNING
        ),
        message=diagnostic.message,
        instance_path=diagnostic.instance_path,
    )


def _finding(
    code: DiagnosticCode | CompilerFindingCode,
    message: str,
    *,
    instance_path: str,
    related_ids: tuple[StableId, ...] = (),
) -> CompilerFinding:
    return CompilerFinding(
        code=StableId(code.value),
        severity=CompilerFindingSeverity.ERROR,
        message=message,
        instance_path=instance_path,
        related_ids=_unique_ids(related_ids),
    )


def _unique_ids(values: Iterable[StableId]) -> tuple[StableId, ...]:
    return tuple(dict.fromkeys(values))
