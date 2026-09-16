"""Deterministic compiler for consolidated 2.0 executable catalogues."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from enum import StrEnum

from conformance.configuration_contracts.diagnostics import (
    ConfigurationContractError,
    ConfigurationDiagnostic,
    DiagnosticCode,
    DiagnosticSeverity,
)
from conformance.configuration_contracts.models import (
    CompilationFinding,
    FindingSeverity,
    FindingSourceDocument,
    InputResolutionSource,
    ResolutionReason,
    ResolvedCapability,
    SelectionOrigin,
    StableId,
    StandingOrderFrequency,
    SuiteRelease,
)
from conformance.configuration_contracts.v2_loader import (
    dump_test_definition_catalogue,
    participant_plan_to_document,
    validate_catalogue_references,
)
from conformance.configuration_contracts.v2_models import (
    ParticipantPlan,
    ResolvedEndpoint,
    ResolvedPlan,
    ResolvedPlanProvenance,
    ResolvedPredefinedInput,
    ResolvedTestInstance,
    TestDefinitionCatalogue,
)

_RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
_LOCAL_DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$")
_REQUIRED_SCHEMA_IDS = frozenset(
    {
        "common-v2",
        "suite-release-v2",
        "test-definition-catalogue-v2",
        "participant-plan-v2",
        "resolved-plan-v2",
        "execution-manifest-v2",
        "suite-policy-v2",
    }
)


class CompilationFindingCode(StrEnum):
    """Stable findings emitted while resolving participant intent."""

    SUITE_RELEASE_MISMATCH = "plan.reference.suite-release-mismatch"
    SPECIFICATION_MISMATCH = "plan.reference.specification-mismatch"
    SECURITY_PROFILE_UNSUPPORTED = "plan.reference.security-profile-unsupported"
    CAPABILITY_UNKNOWN = "plan.selection.capability-unknown"
    SCOPE_EMPTY = "plan.selection.scope-empty"
    INPUT_UNKNOWN = "plan.selection.input-unknown"
    INPUT_NOT_APPLICABLE = "plan.selection.input-not-applicable"
    INPUT_INVALID = "plan.selection.input-invalid"
    INPUT_REQUIRED = "plan.selection.input-required"


class ParticipantPlanCompilationError(ValueError):
    """Raised when participant intent cannot produce runnable work."""

    def __init__(self, resolved_plan: ResolvedPlan) -> None:
        """Retain the inspectable invalid resolution."""
        self.resolved_plan = resolved_plan
        super().__init__(
            "; ".join(
                f"{item.code!s} at {item.instance_path or '<root>'}: {item.message}" for item in resolved_plan.findings
            )
        )


def compile_participant_plan(
    suite_release: SuiteRelease,
    catalogue: TestDefinitionCatalogue,
    participant_plan: ParticipantPlan,
) -> ResolvedPlan:
    """Compile participant intent directly against the released catalogue."""
    resolved = resolve_participant_plan(suite_release, catalogue, participant_plan)
    if not resolved.selection_valid:
        raise ParticipantPlanCompilationError(resolved)
    return resolved


def resolve_participant_plan(
    suite_release: SuiteRelease,
    catalogue: TestDefinitionCatalogue,
    participant_plan: ParticipantPlan,
) -> ResolvedPlan:
    """Resolve participant intent while preserving deterministic findings."""
    _validate_trusted_inputs(suite_release, catalogue)
    findings = _reference_findings(suite_release, catalogue, participant_plan)
    capabilities, selected_ids = _resolve_capabilities(catalogue, participant_plan, findings)
    tests = _resolve_tests(catalogue, selected_ids)
    endpoints = _resolve_endpoints(catalogue, selected_ids, tests)
    inputs = _resolve_inputs(catalogue, participant_plan, selected_ids, findings)
    immutable_findings = tuple(findings)
    return ResolvedPlan(
        schema_version="2.0",
        document_type="resolved-plan",
        id=_resolved_plan_id(suite_release, catalogue, participant_plan),
        selection_valid=not any(item.severity is FindingSeverity.ERROR for item in immutable_findings),
        scheme=catalogue.scheme,
        specification=catalogue.specification,
        security_profile=participant_plan.security_profile,
        capabilities=capabilities,
        endpoints=endpoints,
        predefined_inputs=inputs,
        test_instances=tests,
        findings=immutable_findings,
        provenance=ResolvedPlanProvenance(
            participant_plan_id=participant_plan.id,
            suite_release_id=suite_release.id,
            suite_release_version=suite_release.release_version,
            suite_published_at=suite_release.published_at,
            test_definition_catalogue_id=catalogue.id,
            tool_releases=suite_release.tool_releases,
            artifacts=suite_release.artifacts,
        ),
    )


def _validate_trusted_inputs(
    suite_release: SuiteRelease,
    catalogue: TestDefinitionCatalogue,
) -> None:
    diagnostics = list(validate_catalogue_references(catalogue))
    artifact = next(
        (
            item
            for item in suite_release.artifacts
            if item.kind == "test-definition-catalogue" and item.id == catalogue.id
        ),
        None,
    )
    if artifact is None:
        diagnostics.append(
            _diagnostic(
                DiagnosticCode.ARTIFACT_UNRESOLVED,
                f"Suite release does not bind test-definition-catalogue {catalogue.id!s}",
                "/artifacts",
            )
        )
    else:
        content = dump_test_definition_catalogue(catalogue).encode()
        actual = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if artifact.schema_version != catalogue.schema_version:
            diagnostics.append(
                _diagnostic(
                    DiagnosticCode.ARTIFACT_UNRESOLVED,
                    f"Catalogue schema version {catalogue.schema_version!r} is not release-bound",
                    "/artifacts",
                )
            )
        elif artifact.digest != actual:
            diagnostics.append(
                _diagnostic(
                    DiagnosticCode.ARTIFACT_DIGEST_MISMATCH,
                    f"Supplied catalogue digest {actual} does not match release binding",
                    "/artifacts",
                )
            )
    schema_ids = {
        str(item.id) for item in suite_release.artifacts if item.kind == "json-schema" and item.schema_version == "2.0"
    }
    for identifier in sorted(_REQUIRED_SCHEMA_IDS.difference(schema_ids)):
        diagnostics.append(
            _diagnostic(
                DiagnosticCode.ARTIFACT_UNRESOLVED,
                f"Suite release does not bind schema {identifier}",
                "/artifacts",
            )
        )
    if not any(item.kind == "suite-policy" and item.schema_version == "2.0" for item in suite_release.artifacts):
        diagnostics.append(
            _diagnostic(
                DiagnosticCode.ARTIFACT_UNRESOLVED,
                "Suite release does not bind an executable suite policy",
                "/artifacts",
            )
        )
    released_sources = {item.id: item for item in suite_release.artifacts if item.kind == "technical-source"}
    for source_index, source in enumerate(catalogue.technical_sources):
        released = released_sources.get(source.id)
        if released is None:
            diagnostics.append(
                _diagnostic(
                    DiagnosticCode.ARTIFACT_UNRESOLVED,
                    f"Suite release does not bind technical source {source.id!s}",
                    f"/technicalSources/{source_index}/id",
                )
            )
        elif released.digest != source.digest:
            diagnostics.append(
                _diagnostic(
                    DiagnosticCode.ARTIFACT_DIGEST_MISMATCH,
                    f"Technical source {source.id!s} digest differs from the release binding",
                    f"/technicalSources/{source_index}/digest",
                )
            )
    if diagnostics:
        raise ConfigurationContractError(tuple(diagnostics))


def _reference_findings(
    suite_release: SuiteRelease,
    catalogue: TestDefinitionCatalogue,
    plan: ParticipantPlan,
) -> list[CompilationFinding]:
    findings: list[CompilationFinding] = []
    if plan.suite_release_id != suite_release.id:
        findings.append(
            _finding(
                CompilationFindingCode.SUITE_RELEASE_MISMATCH,
                f"Participant plan selects {plan.suite_release_id!s}; supplied release is {suite_release.id!s}",
                "/suiteReleaseId",
                (plan.suite_release_id, suite_release.id),
            )
        )
    if (
        plan.scheme != catalogue.scheme
        or plan.specification.id != catalogue.specification.id
        or plan.specification.version != catalogue.specification.version
        or plan.specification.test_scope != catalogue.specification.test_scope
    ):
        findings.append(
            _finding(
                CompilationFindingCode.SPECIFICATION_MISMATCH,
                "Participant plan scheme, specification, version, or testScope does not match the catalogue",
                "/specification",
                (plan.id, catalogue.id),
            )
        )
    if plan.security_profile not in catalogue.allowed_security_profiles:
        findings.append(
            _finding(
                CompilationFindingCode.SECURITY_PROFILE_UNSUPPORTED,
                (
                    f"Security profile {plan.security_profile!r} is not supported by "
                    f"catalogue {catalogue.id!s}; allowed profiles: " + ", ".join(catalogue.allowed_security_profiles)
                ),
                "/securityProfile",
                (plan.id, catalogue.id),
            )
        )
    return findings


def _resolve_capabilities(
    catalogue: TestDefinitionCatalogue,
    plan: ParticipantPlan,
    findings: list[CompilationFinding],
) -> tuple[tuple[ResolvedCapability, ...], set[StableId]]:
    by_id = {item.id: item for item in catalogue.capabilities}
    explicit = set(plan.selected_capability_ids).intersection(by_id)
    selected = set(explicit)
    required_by: dict[StableId, set[StableId]] = defaultdict(set)

    def include(identifier: StableId) -> None:
        for dependency in by_id[identifier].required_capability_ids:
            required_by[dependency].add(identifier)
            if dependency not in selected:
                selected.add(dependency)
                include(dependency)

    for identifier in tuple(explicit):
        include(identifier)
    indexes = {value: index for index, value in enumerate(plan.selected_capability_ids)}
    for unknown in sorted(set(plan.selected_capability_ids).difference(by_id)):
        findings.append(
            _finding(
                CompilationFindingCode.CAPABILITY_UNKNOWN,
                f"Selected capability {unknown!s} does not exist in the test catalogue",
                f"/selectedCapabilityIds/{indexes[unknown]}",
                (unknown,),
            )
        )
    if not selected:
        findings.append(
            _finding(
                CompilationFindingCode.SCOPE_EMPTY,
                "Participant plan must select at least one supported capability",
                "/selectedCapabilityIds",
                (plan.id,),
            )
        )
    return (
        tuple(
            ResolvedCapability(
                id=item.id,
                origin=SelectionOrigin.EXPLICIT if item.id in explicit else SelectionOrigin.INFERRED,
                reasons=(
                    ResolutionReason(
                        code=StableId(
                            "plan.capability.explicit" if item.id in explicit else "catalogue.capability.dependency"
                        ),
                        source_ids=(
                            (plan.id, item.id)
                            if item.id in explicit
                            else tuple(sorted(required_by[item.id])) + (item.id,)
                        ),
                    ),
                ),
            )
            for item in catalogue.capabilities
            if item.id in selected
        ),
        selected,
    )


def _resolve_tests(
    catalogue: TestDefinitionCatalogue,
    selected_capability_ids: set[StableId],
) -> tuple[ResolvedTestInstance, ...]:
    by_id = {item.id: item for item in catalogue.test_definitions}
    selected = {
        item.id
        for item in catalogue.test_definitions
        if selected_capability_ids.intersection(item.applicability.capability_ids)
    }
    direct = set(selected)

    def include_dependencies(identifier: StableId) -> None:
        for dependency in by_id[identifier].dependencies:
            if dependency not in selected:
                selected.add(dependency)
                include_dependencies(dependency)

    for identifier in tuple(selected):
        include_dependencies(identifier)
    ordered: list[StableId] = []
    pending = [item.id for item in catalogue.test_definitions if item.id in selected]
    while pending:
        progress = False
        for identifier in tuple(pending):
            if all(dependency in ordered for dependency in by_id[identifier].dependencies):
                ordered.append(identifier)
                pending.remove(identifier)
                progress = True
        if not progress:
            break
    return tuple(
        ResolvedTestInstance(
            id=StableId(f"{identifier!s}.instance"),
            test_definition_id=identifier,
            dependency_ids=tuple(StableId(f"{dependency!s}.instance") for dependency in by_id[identifier].dependencies),
            reasons=(
                ResolutionReason(
                    code=StableId("catalogue.test.applicable" if identifier in direct else "catalogue.test.dependency"),
                    source_ids=(
                        (catalogue.id, identifier)
                        if identifier in direct
                        else tuple(by_id[identifier].dependencies) + (identifier,)
                    ),
                ),
            ),
        )
        for identifier in ordered
    )


def _resolve_endpoints(
    catalogue: TestDefinitionCatalogue,
    selected_capability_ids: set[StableId],
    tests: tuple[ResolvedTestInstance, ...],
) -> tuple[ResolvedEndpoint, ...]:
    selected = {
        endpoint_id
        for capability in catalogue.capabilities
        if capability.id in selected_capability_ids
        for endpoint_id in capability.required_endpoint_ids
    }
    test_ids = {item.test_definition_id for item in tests}
    selected.update(item.request.endpoint_id for item in catalogue.test_definitions if item.id in test_ids)
    return tuple(
        ResolvedEndpoint(
            id=item.id,
            origin=SelectionOrigin.INFERRED,
            reasons=(
                ResolutionReason(
                    code=StableId("catalogue.endpoint.applicable"),
                    source_ids=(catalogue.id, item.id),
                ),
            ),
        )
        for item in catalogue.endpoints
        if item.id in selected
    )


def _resolve_inputs(
    catalogue: TestDefinitionCatalogue,
    plan: ParticipantPlan,
    selected_capability_ids: set[StableId],
    findings: list[CompilationFinding],
) -> tuple[ResolvedPredefinedInput, ...]:
    definitions = {item.id: item for item in catalogue.predefined_inputs}
    supplied = {item.input_id: item for item in plan.predefined_inputs}
    indexes = {item.input_id: index for index, item in enumerate(plan.predefined_inputs)}
    applicable = {
        item.id
        for item in catalogue.predefined_inputs
        if selected_capability_ids.intersection(item.required_for_capability_ids)
    }
    for identifier in sorted(set(supplied).difference(definitions)):
        findings.append(
            _finding(
                CompilationFindingCode.INPUT_UNKNOWN,
                f"Participant input {identifier!s} does not exist in the test catalogue",
                f"/predefinedInputs/{indexes[identifier]}/inputId",
                (identifier,),
            )
        )
    for identifier in sorted(set(supplied).intersection(definitions).difference(applicable)):
        findings.append(
            _finding(
                CompilationFindingCode.INPUT_NOT_APPLICABLE,
                f"Participant input {identifier!s} is not applicable to selected capabilities",
                f"/predefinedInputs/{indexes[identifier]}/inputId",
                (identifier,),
            )
        )
    resolved: list[ResolvedPredefinedInput] = []
    for definition in catalogue.predefined_inputs:
        if definition.id not in applicable:
            continue
        participant_input = supplied.get(definition.id)
        value = participant_input.value if participant_input is not None else definition.default_value
        if value is None:
            findings.append(
                _finding(
                    CompilationFindingCode.INPUT_REQUIRED,
                    f"Predefined input {definition.id!s} is required",
                    "/predefinedInputs",
                    (definition.id,),
                )
            )
            continue
        if not _valid_input_value(str(definition.value_type), value):
            findings.append(
                _finding(
                    CompilationFindingCode.INPUT_INVALID,
                    f"Value for {definition.id!s} is invalid for {definition.value_type!s}",
                    (
                        f"/predefinedInputs/{indexes[definition.id]}/value"
                        if participant_input is not None
                        else "/predefinedInputs"
                    ),
                    (definition.id,),
                )
            )
            continue
        redacted = definition.sensitivity != "non-sensitive"
        resolved.append(
            ResolvedPredefinedInput(
                id=definition.id,
                source=(
                    InputResolutionSource.PARTICIPANT
                    if participant_input is not None
                    else InputResolutionSource.DEFAULT
                ),
                value=None if redacted else value,
                redacted=redacted,
                reasons=(
                    ResolutionReason(
                        code=StableId(
                            "plan.input.supplied" if participant_input is not None else "catalogue.input.defaulted"
                        ),
                        source_ids=(plan.id, definition.id),
                    ),
                ),
            )
        )
    return tuple(resolved)


def _valid_input_value(value_type: str, value: object) -> bool:
    if value_type == "string":
        return isinstance(value, str) and bool(value)
    if value_type == "date-time":
        return isinstance(value, str) and _datetime_matches(value, _RFC3339)
    if value_type == "local-date-time":
        return isinstance(value, str) and _datetime_matches(value, _LOCAL_DATE_TIME)
    if value_type == "standing-order-frequency-v4":
        return isinstance(value, StandingOrderFrequency)
    return False


def _datetime_matches(value: str, pattern: re.Pattern[str]) -> bool:
    if pattern.fullmatch(value) is None:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _resolved_plan_id(
    suite_release: SuiteRelease,
    catalogue: TestDefinitionCatalogue,
    plan: ParticipantPlan,
) -> StableId:
    plan_document = participant_plan_to_document(plan)
    plan_document.pop("executionConfiguration", None)
    sensitive_ids = {str(item.id) for item in catalogue.predefined_inputs if item.sensitivity != "non-sensitive"}
    raw_inputs = plan_document["predefinedInputs"]
    if not isinstance(raw_inputs, list):
        raise TypeError("Schema-valid participant plan must contain predefinedInputs")
    plan_document["predefinedInputs"] = [
        (
            {"inputId": item["inputId"], "redacted": True}
            if isinstance(item, dict) and item.get("inputId") in sensitive_ids
            else item
        )
        for item in raw_inputs
    ]
    identity = {
        "suiteRelease": {
            "id": str(suite_release.id),
            "version": suite_release.release_version,
            "publishedAt": suite_release.published_at,
        },
        "catalogue": json.loads(dump_test_definition_catalogue(catalogue)),
        "participantIntent": plan_document,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return StableId(f"resolved-plan:{hashlib.sha256(encoded).hexdigest()}")


def _finding(
    code: CompilationFindingCode,
    message: str,
    path: str,
    related_ids: tuple[StableId, ...],
) -> CompilationFinding:
    return CompilationFinding(
        code=StableId(code.value),
        severity=FindingSeverity.ERROR,
        message=message,
        source_document=FindingSourceDocument.PARTICIPANT_PLAN,
        instance_path=path,
        related_ids=related_ids,
    )


def _diagnostic(code: DiagnosticCode, message: str, path: str) -> ConfigurationDiagnostic:
    return ConfigurationDiagnostic(
        code=code,
        severity=DiagnosticSeverity.ERROR,
        message=message,
        instance_path=path,
    )
