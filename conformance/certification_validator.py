"""Independent OBL-side validation of approved executable-test results."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from jsonschema import Draft202012Validator, SchemaError  # type: ignore[import-untyped]  # library lacks stubs

from conformance.configuration_contracts.diagnostics import ConfigurationContractError
from conformance.configuration_contracts.models import (
    ParticipantInput,
    StableId,
    StandingOrderFrequency,
    SuiteRelease,
)
from conformance.configuration_contracts.suite_release_artifacts import (
    TRUSTED_CONFIGURATION_ROOT,
    SuiteReleaseArtifactError,
    SuiteReleaseArtifactResolver,
    preflight_suite_release_artifacts,
)
from conformance.configuration_contracts.v2_compiler import resolve_participant_plan
from conformance.configuration_contracts.v2_execution_manifest import (
    ExecutionManifestGenerationError,
    generate_execution_manifest,
)
from conformance.configuration_contracts.v2_loader import (
    execution_manifest_id,
    execution_manifest_to_document,
    load_execution_manifest,
    load_resolved_plan,
    load_suite_release,
    parse_suite_policy,
    parse_test_definition_catalogue,
    resolved_plan_to_document,
)
from conformance.configuration_contracts.v2_models import (
    ExecutionManifest,
    ExecutionManifestStep,
    ParticipantPlan,
    ResolvedPlan,
    Specification,
    SuitePolicy,
    TestDefinitionCatalogue,
)
from conformance.json_types import JsonObject
from conformance.results import CheckStatus

VALID_REPORT_STEP_STATUSES = frozenset({"passed", "failed", "warn", "skipped"})
"""Result observation status values accepted from submitted report JSON."""

type AssertionStatus = Literal["passed", "failed"]
type TestOutcomeStatus = CheckStatus | Literal["missing", "incomplete"]
type AutomatedAssessmentStatus = Literal["passed", "failed", "incomplete"]
type CertificationValidationReason = str


class CertificationValidationError(ValueError):
    """Raised when trusted or submitted certification inputs are malformed."""


@dataclass(frozen=True, slots=True)
class ReportAssertionObservation:
    """Observed outcome for one manifest assertion."""

    assertion_id: str
    status: AssertionStatus


@dataclass(frozen=True, slots=True)
class ReportObservation:
    """One result observation emitted for a manifest step."""

    observation_id: str
    status: CheckStatus
    assertions: tuple[ReportAssertionObservation, ...]


@dataclass(frozen=True, slots=True)
class ParticipantScope:
    """Secret-safe participant declaration embedded in result traceability."""

    raw_snapshot: JsonObject
    participant_plan_id: str
    suite_release_id: str
    scheme: str
    specification_id: str
    specification_version: str
    test_scope: str
    security_profile: str
    selected_capability_ids: tuple[str, ...]
    predefined_inputs: tuple[JsonObject, ...]


@dataclass(frozen=True, slots=True)
class TraceabilityManifestStep:
    """Submitted result claim linking a manifest step to one observation."""

    step_id: str
    test_instance_id: str
    test_definition_id: str
    assertion_ids: tuple[str, ...]
    result_observation_id: str
    result_status: str


@dataclass(frozen=True, slots=True)
class SubmittedTraceability:
    """Submitted release-to-observation traceability claims."""

    suite_release_id: str
    suite_release_version: str
    suite_published_at: str
    participant_scope: ParticipantScope
    test_definition_ids: tuple[str, ...]
    compiled_test_instances: tuple[JsonObject, ...]
    execution_manifest_id: str
    execution_manifest_schema_version: str
    resolved_plan_id: str
    manifest_steps: tuple[TraceabilityManifestStep, ...]
    compiler_findings: tuple[JsonObject, ...]


@dataclass(frozen=True, slots=True)
class SubmittedReport:
    """Parsed public result plus the claims needed for independent validation."""

    report_version: str
    tool_version: str
    status: CheckStatus
    observations: tuple[ReportObservation, ...]
    traceability: SubmittedTraceability
    summary_claim: JsonObject | None
    eligibility_claim: bool | None


@dataclass(frozen=True, slots=True)
class ApprovedTestOutcome:
    """Independently calculated outcome for one applicable approved test."""

    test_definition_id: str
    test_instance_id: str
    manifest_step_ids: tuple[str, ...]
    status: TestOutcomeStatus

    def to_json_object(self) -> JsonObject:
        """Render one approved-test outcome."""
        return {
            "testDefinitionId": self.test_definition_id,
            "testInstanceId": self.test_instance_id,
            "manifestStepIds": list(self.manifest_step_ids),
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class CertificationValidationResult:
    """Independent assessment with test, automation, and eligibility layers."""

    valid: bool
    report_version: str
    tool_version: str
    suite_release_id: str
    suite_release_version: str
    tool_version_approved: bool
    policy_id: str
    result_claim: str
    test_outcomes: tuple[ApprovedTestOutcome, ...]
    automated_assessment: AutomatedAssessmentStatus
    complete: bool
    eligible: bool
    reasons: tuple[CertificationValidationReason, ...]

    def to_json_object(self) -> JsonObject:
        """Convert the validation result into the versioned public shape."""
        counts = _test_outcome_counts(self.test_outcomes)
        return {
            "schemaVersion": "2.0",
            "valid": self.valid,
            "report": {
                "reportVersion": self.report_version,
                "toolVersion": self.tool_version,
            },
            "approvedRelease": {
                "id": self.suite_release_id,
                "version": self.suite_release_version,
                "toolVersionApproved": self.tool_version_approved,
            },
            "individualTests": {
                **counts,
                "tests": [outcome.to_json_object() for outcome in self.test_outcomes],
            },
            "automatedAssessment": {
                "status": self.automated_assessment,
                "complete": self.complete,
                "policyId": self.policy_id,
                "resultClaim": self.result_claim,
            },
            "certificationEligibility": {
                "eligible": self.eligible,
                "reasons": list(self.reasons),
            },
            "reasons": list(self.reasons),
        }


def validate_certification_report(
    report_path: Path,
    *,
    suite_release_path: Path,
    resolved_plan_path: Path,
    manifest_path: Path,
    trusted_root: Path = TRUSTED_CONFIGURATION_ROOT,
) -> CertificationValidationResult:
    """Load and independently validate one submitted schema-version 2.0 run."""
    report = load_submitted_report(report_path)
    try:
        suite_release = load_suite_release(suite_release_path)
        resolved_plan = load_resolved_plan(resolved_plan_path)
        manifest = load_execution_manifest(manifest_path)
        resolver = preflight_suite_release_artifacts(manifest, suite_release, trusted_root=trusted_root)
        catalogue, policy = _load_applicable_release_assets(
            resolver,
            participant_scope=report.traceability.participant_scope,
        )
    except (
        ConfigurationContractError,
        ExecutionManifestGenerationError,
        OSError,
        SuiteReleaseArtifactError,
        ValueError,
    ) as error:
        raise CertificationValidationError(f"Invalid certification input: {error}") from error
    return validate_report(
        report=report,
        suite_release=suite_release,
        catalogue=catalogue,
        policy=policy,
        resolved_plan=resolved_plan,
        manifest=manifest,
    )


def load_submitted_report(report_path: Path) -> SubmittedReport:
    """Load a submitted result report from disk."""
    return parse_submitted_report(_load_json_file(report_path, label="report"))


def parse_submitted_report(raw_report: object) -> SubmittedReport:
    """Parse the public result and its release-to-observation traceability."""
    report = _as_object(raw_report, location="report")
    metadata = _required_object(report, "metadata", location="report")
    tool = _required_object(report, "tool", location="report")
    observations = _parse_report_observations(_required_array(report, "steps", location="report"))
    traceability = _parse_traceability(_required_object(report, "traceability", location="report"))
    summary = _optional_object(report, "summary", location="report")
    eligibility = _optional_object(report, "certificationEligibility", location="report")
    eligibility_claim: bool | None = None
    if eligibility is not None and "eligible" in eligibility:
        eligibility_claim = _required_bool(eligibility, "eligible", location="report.certificationEligibility")
    return SubmittedReport(
        report_version=_required_non_empty_string(metadata, "reportVersion", location="report.metadata"),
        tool_version=_required_non_empty_string(tool, "version", location="report.tool"),
        status=_required_report_step_status(report, "status", location="report"),
        observations=observations,
        traceability=traceability,
        summary_claim=cast(JsonObject | None, summary),
        eligibility_claim=eligibility_claim,
    )


def validate_report(
    *,
    report: SubmittedReport,
    suite_release: SuiteRelease,
    catalogue: TestDefinitionCatalogue,
    policy: SuitePolicy,
    resolved_plan: ResolvedPlan,
    manifest: ExecutionManifest,
) -> CertificationValidationResult:
    """Validate identities and independently assess approved executable tests."""
    reasons: list[str] = []
    traceability = report.traceability
    scope = traceability.participant_scope

    if (
        traceability.suite_release_id != suite_release.id
        or traceability.suite_release_version != suite_release.release_version
        or traceability.suite_published_at != suite_release.published_at
        or scope.suite_release_id != suite_release.id
    ):
        _add_reason(reasons, "suite_release_mismatch")

    tool_version_approved = any(item.version == report.tool_version for item in suite_release.tool_releases)
    if not tool_version_approved:
        _add_reason(reasons, "tool_version_not_approved")

    participant_plan = _participant_plan_from_scope(scope, catalogue)
    try:
        expected_plan = resolve_participant_plan(suite_release, catalogue, participant_plan)
    except (ConfigurationContractError, ValueError) as error:
        raise CertificationValidationError(f"Unable to resolve trusted participant scope: {error}") from error

    if resolved_plan.id != expected_plan.id:
        _add_reason(reasons, "resolved_plan_identity_mismatch")
    if resolved_plan_to_document(resolved_plan) != resolved_plan_to_document(expected_plan):
        _add_reason(reasons, "resolved_plan_mismatch")

    blocking_severities = set(policy.blocking_finding_severities)
    if any(finding.severity.value in blocking_severities for finding in expected_plan.findings):
        _add_reason(reasons, "blocking_compiler_finding")

    observations = {item.observation_id: item for item in report.observations}
    test_outcomes: tuple[ApprovedTestOutcome, ...] = ()
    automated_assessment: AutomatedAssessmentStatus = "incomplete"
    if expected_plan.selection_valid:
        try:
            expected_manifest = generate_execution_manifest(expected_plan, catalogue)
        except ExecutionManifestGenerationError as error:
            raise CertificationValidationError(f"Unable to regenerate expected execution manifest: {error}") from error
        if manifest.id != execution_manifest_id(manifest):
            _add_reason(reasons, "execution_manifest_identity_mismatch")
        if execution_manifest_to_document(manifest) != execution_manifest_to_document(expected_manifest):
            _add_reason(reasons, "execution_manifest_mismatch")
        reasons.extend(_observation_reasons(expected_manifest, observations))
        reasons.extend(
            _traceability_reasons(
                report,
                expected_plan=expected_plan,
                expected_manifest=expected_manifest,
                observations=observations,
            )
        )
        test_outcomes = _derive_test_outcomes(
            expected_plan,
            expected_manifest,
            observations,
        )
        automated_assessment = _automated_assessment(test_outcomes)
    else:
        _add_reason(reasons, "selection_invalid")
    reasons = list(dict.fromkeys(reasons))

    complete = automated_assessment != "incomplete"
    if automated_assessment == "failed":
        _add_reason(reasons, "approved_test_failed")
    elif automated_assessment == "incomplete":
        _add_reason(reasons, "approved_test_incomplete")

    independently_eligible = not reasons and automated_assessment == "passed" and tool_version_approved
    reasons.extend(_runner_claim_reasons(report, independently_eligible=independently_eligible))
    reasons = list(dict.fromkeys(reasons))
    eligible = not reasons and automated_assessment == "passed" and tool_version_approved

    return CertificationValidationResult(
        valid=eligible,
        report_version=report.report_version,
        tool_version=report.tool_version,
        suite_release_id=str(suite_release.id),
        suite_release_version=suite_release.release_version,
        tool_version_approved=tool_version_approved,
        policy_id=str(policy.id),
        result_claim=policy.result_claim,
        test_outcomes=test_outcomes,
        automated_assessment=automated_assessment,
        complete=complete,
        eligible=eligible,
        reasons=tuple(reasons),
    )


def render_confluence_summary(result: CertificationValidationResult) -> str:
    """Render a concise reviewer summary without broadening the policy claim."""
    status = "PASS" if result.valid else "FAIL"
    counts = _test_outcome_counts(result.test_outcomes)
    lines = [
        f"Certification report validation: {status}",
        "",
        f"Suite release: {result.suite_release_id} ({result.suite_release_version})",
        (f"Tool version: {result.tool_version} ({'approved' if result.tool_version_approved else 'not approved'})"),
        f"Report metadata version: {result.report_version}",
        f"Automated assessment: {result.automated_assessment}",
        f"Approved-test completeness: {'complete' if result.complete else 'incomplete'}",
        f"Certification eligibility: {'eligible' if result.eligible else 'not eligible'}",
        (
            "Approved tests: "
            f"{counts['total']} total, {counts['passed']} passed, {counts['warn']} warn, "
            f"{counts['failed']} failed, {counts['skipped']} skipped, "
            f"{counts['missing']} missing, {counts['incomplete']} incomplete"
        ),
        f"Assessment claim: {result.result_claim}",
    ]
    if result.reasons:
        lines.extend(["", "Blocking reasons:"])
        lines.extend(f"- {reason}" for reason in result.reasons)
    return "\n".join(lines)


def _load_applicable_release_assets(
    resolver: SuiteReleaseArtifactResolver,
    *,
    participant_scope: ParticipantScope,
) -> tuple[TestDefinitionCatalogue, SuitePolicy]:
    """Load verified release assets and select the catalogue for declared scope."""
    catalogues: list[TestDefinitionCatalogue] = []
    policies: list[SuitePolicy] = []
    for reference in resolver.suite_release.artifacts:
        artifact = resolver.resolved_artifacts[reference.id]
        if reference.kind == "json-schema":
            document = json.loads(artifact.content)
            try:
                Draft202012Validator.check_schema(document)
            except SchemaError as error:
                raise CertificationValidationError(
                    f"Released schema {reference.id!s} is invalid: {error.message}"
                ) from error
        elif reference.kind == "test-definition-catalogue":
            document = json.loads(artifact.content)
            catalogue = parse_test_definition_catalogue(document)
            if catalogue.id != reference.id or catalogue.schema_version != reference.schema_version:
                raise CertificationValidationError(
                    f"Released catalogue {reference.id!s} identity does not match its artifact binding"
                )
            catalogues.append(catalogue)
        elif reference.kind == "suite-policy":
            document = json.loads(artifact.content)
            policy = parse_suite_policy(document)
            if policy.id != reference.id or policy.schema_version != reference.schema_version:
                raise CertificationValidationError(
                    f"Released policy {reference.id!s} identity does not match its artifact binding"
                )
            policies.append(policy)

    matching_catalogues = tuple(
        item
        for item in catalogues
        if str(item.scheme) == participant_scope.scheme
        and str(item.specification.id) == participant_scope.specification_id
        and item.specification.version == participant_scope.specification_version
        and str(item.specification.test_scope) == participant_scope.test_scope
    )
    if len(matching_catalogues) != 1:
        raise CertificationValidationError(
            "Approved suite release must bind exactly one test catalogue for the participant scope"
        )
    if len(policies) != 1:
        raise CertificationValidationError("Approved suite release must bind exactly one suite policy")
    catalogue = matching_catalogues[0]
    released_sources = {item.id: item for item in resolver.suite_release.artifacts if item.kind == "technical-source"}
    for source in catalogue.technical_sources:
        released = released_sources.get(source.id)
        if released is None or released.digest != source.digest:
            raise CertificationValidationError(
                f"Catalogue technical source {source.id!s} does not match the approved suite release"
            )
    return catalogue, policies[0]


def _participant_plan_from_scope(
    scope: ParticipantScope,
    catalogue: TestDefinitionCatalogue,
) -> ParticipantPlan:
    """Recreate non-secret participant intent for deterministic re-resolution."""
    definitions = {str(item.id): item for item in catalogue.predefined_inputs}
    inputs: list[ParticipantInput] = []
    for index, item in enumerate(scope.predefined_inputs):
        location = f"report.traceability.participantPlanSnapshot.predefinedInputs[{index}]"
        input_id = _required_non_empty_string(item, "inputId", location=location)
        definition = definitions.get(input_id)
        if definition is None:
            raise CertificationValidationError(f"{location}.inputId {input_id!r} is not in the approved catalogue")
        redacted = _required_bool(item, "redacted", location=location)
        catalogue_sensitive = definition.sensitivity != "non-sensitive"
        if redacted != catalogue_sensitive:
            raise CertificationValidationError(f"{location}.redacted contradicts the approved catalogue sensitivity")
        if redacted:
            value = definition.example_value
        else:
            if "value" not in item:
                raise CertificationValidationError(f"{location}.value is required for a non-sensitive input")
            value = _participant_input_value(item["value"], location=f"{location}.value")
        inputs.append(ParticipantInput(input_id=StableId(input_id), value=value))
    return ParticipantPlan(
        schema_version="2.0",
        document_type="participant-plan",
        id=StableId(scope.participant_plan_id),
        suite_release_id=StableId(scope.suite_release_id),
        scheme=StableId(scope.scheme),
        specification=Specification(
            id=StableId(scope.specification_id),
            version=scope.specification_version,
            test_scope=StableId(scope.test_scope),
        ),
        security_profile=scope.security_profile,
        selected_capability_ids=tuple(StableId(value) for value in scope.selected_capability_ids),
        predefined_inputs=tuple(inputs),
    )


def _participant_input_value(value: object, *, location: str) -> str | StandingOrderFrequency:
    if isinstance(value, str):
        return value
    document = _as_object(value, location=location)
    frequency_type = _required_non_empty_string(document, "frequencyType", location=location)
    count = document.get("countPerPeriod")
    point = document.get("pointInTime")
    if count is not None and (not isinstance(count, int) or isinstance(count, bool)):
        raise CertificationValidationError(f"{location}.countPerPeriod must be an integer")
    if point is not None and not isinstance(point, str):
        raise CertificationValidationError(f"{location}.pointInTime must be a string")
    return StandingOrderFrequency(
        frequency_type=frequency_type,
        count_per_period=count,
        point_in_time=point,
    )


def _observation_reasons(
    manifest: ExecutionManifest,
    observations: Mapping[str, ReportObservation],
) -> list[str]:
    reasons: list[str] = []
    steps_by_id = {str(step.id): step for step in manifest.steps}
    for observation_id in observations.keys() - steps_by_id.keys():
        _add_reason(reasons, f"unknown_observation:{observation_id}")
    for step in manifest.steps:
        step_id = str(step.id)
        observation = observations.get(step_id)
        if observation is None:
            _add_reason(reasons, f"missing_observation:{step_id}")
            continue
        expected_assertion_ids = {str(item.id) for item in step.assertions}
        observed_assertions = {item.assertion_id: item for item in observation.assertions}
        for assertion_id in observed_assertions.keys() - expected_assertion_ids:
            _add_reason(reasons, f"unknown_assertion:{step_id}/{assertion_id}")
        for assertion_id in expected_assertion_ids - observed_assertions.keys():
            _add_reason(reasons, f"missing_assertion_observation:{step_id}/{assertion_id}")
        if observation.status in {"passed", "warn"} and any(item.status == "failed" for item in observation.assertions):
            _add_reason(reasons, f"contradictory_outcome:{step_id}")

        failed_dependency = any(
            observations.get(str(dependency_id)) is None
            or observations[str(dependency_id)].status in {"failed", "skipped"}
            for dependency_id in step.dependency_ids
        )
        if failed_dependency and observation.status != "skipped":
            _add_reason(reasons, f"observation_for_unexecuted_work:{step_id}")
        if not failed_dependency and observation.status == "skipped":
            _add_reason(reasons, f"skipped_required_work:{step_id}")
    return reasons


def _traceability_reasons(
    report: SubmittedReport,
    *,
    expected_plan: ResolvedPlan,
    expected_manifest: ExecutionManifest,
    observations: Mapping[str, ReportObservation],
) -> list[str]:
    reasons: list[str] = []
    traceability = report.traceability
    if traceability.execution_manifest_id != expected_manifest.id:
        _add_reason(reasons, "result_manifest_identity_mismatch")
    if traceability.execution_manifest_schema_version != expected_manifest.schema_version:
        _add_reason(reasons, "result_manifest_schema_mismatch")
    if traceability.resolved_plan_id != expected_plan.id:
        _add_reason(reasons, "result_resolved_plan_identity_mismatch")

    expected_definitions = tuple(dict.fromkeys(str(item.test_definition_id) for item in expected_manifest.steps))
    if traceability.test_definition_ids != expected_definitions:
        _add_reason(reasons, "result_test_definitions_mismatch")

    expected_instances = tuple(
        {
            "id": str(item.id),
            "testDefinitionId": str(item.test_definition_id),
            "dependencyIds": [str(value) for value in item.dependency_ids],
        }
        for item in expected_plan.test_instances
    )
    if traceability.compiled_test_instances != expected_instances:
        _add_reason(reasons, "result_test_instances_mismatch")

    expected_findings = cast(
        list[JsonObject],
        resolved_plan_to_document(expected_plan)["findings"],
    )
    if traceability.compiler_findings != tuple(expected_findings):
        _add_reason(reasons, "result_compiler_findings_mismatch")

    trace_steps = {item.step_id: item for item in traceability.manifest_steps}
    expected_step_ids = {str(item.id) for item in expected_manifest.steps}
    for step_id in trace_steps.keys() - expected_step_ids:
        _add_reason(reasons, f"unknown_trace_step:{step_id}")
    for step in expected_manifest.steps:
        step_id = str(step.id)
        traced = trace_steps.get(step_id)
        if traced is None:
            _add_reason(reasons, f"missing_trace_step:{step_id}")
            continue
        observation = observations.get(step_id)
        if (
            traced.test_instance_id != step.test_instance_id
            or traced.test_definition_id != step.test_definition_id
            or traced.assertion_ids != tuple(str(item.id) for item in step.assertions)
            or traced.result_observation_id != step_id
            or traced.result_status != (observation.status if observation is not None else "missing")
        ):
            _add_reason(reasons, f"traceability_mismatch:{step_id}")
    return reasons


def _derive_test_outcomes(
    plan: ResolvedPlan,
    manifest: ExecutionManifest,
    observations: Mapping[str, ReportObservation],
) -> tuple[ApprovedTestOutcome, ...]:
    steps_by_instance: dict[str, list[ExecutionManifestStep]] = {}
    for step in manifest.steps:
        steps_by_instance.setdefault(str(step.test_instance_id), []).append(step)
    outcomes: list[ApprovedTestOutcome] = []
    for instance in plan.test_instances:
        instance_id = str(instance.id)
        manifest_steps = steps_by_instance.get(instance_id, [])
        step_ids = tuple(str(step.id) for step in manifest_steps)
        statuses: list[TestOutcomeStatus] = []
        for step in manifest_steps:
            observation = observations.get(str(step.id))
            if observation is None:
                statuses.append("missing")
                continue
            expected_assertions = {str(item.id) for item in step.assertions}
            observed_assertions = {item.assertion_id for item in observation.assertions}
            if expected_assertions != observed_assertions:
                statuses.append("incomplete")
            elif any(item.status == "failed" for item in observation.assertions):
                statuses.append("failed")
            else:
                statuses.append(observation.status)
        status = _aggregate_test_status(statuses)
        outcomes.append(
            ApprovedTestOutcome(
                test_definition_id=str(instance.test_definition_id),
                test_instance_id=instance_id,
                manifest_step_ids=step_ids,
                status=status,
            )
        )
    return tuple(outcomes)


def _aggregate_test_status(statuses: list[TestOutcomeStatus]) -> TestOutcomeStatus:
    if not statuses or "missing" in statuses:
        return "missing"
    if "incomplete" in statuses:
        return "incomplete"
    if "skipped" in statuses:
        return "skipped"
    if "failed" in statuses:
        return "failed"
    if "warn" in statuses:
        return "warn"
    return "passed"


def _automated_assessment(outcomes: tuple[ApprovedTestOutcome, ...]) -> AutomatedAssessmentStatus:
    if not outcomes or any(item.status in {"missing", "incomplete", "skipped"} for item in outcomes):
        return "incomplete"
    if any(item.status == "failed" for item in outcomes):
        return "failed"
    return "passed"


def _runner_claim_reasons(
    report: SubmittedReport,
    *,
    independently_eligible: bool,
) -> list[str]:
    reasons: list[str] = []
    aggregate_status: CheckStatus = (
        "passed" if all(item.status in {"passed", "warn"} for item in report.observations) else "failed"
    )
    if report.status != aggregate_status:
        _add_reason(reasons, "result_status_contradiction")
    if report.summary_claim is not None:
        expected_summary: JsonObject = {
            "total": len(report.observations),
            "passed": sum(item.status == "passed" for item in report.observations),
            "failed": sum(item.status == "failed" for item in report.observations),
            "warn": sum(item.status == "warn" for item in report.observations),
            "skipped": sum(item.status == "skipped" for item in report.observations),
        }
        if report.summary_claim != expected_summary:
            _add_reason(reasons, "result_summary_contradiction")
    if report.eligibility_claim is True and not independently_eligible:
        _add_reason(reasons, "runner_eligibility_contradiction")
    return reasons


def _test_outcome_counts(outcomes: tuple[ApprovedTestOutcome, ...]) -> JsonObject:
    statuses: tuple[TestOutcomeStatus, ...] = (
        "passed",
        "failed",
        "warn",
        "skipped",
        "missing",
        "incomplete",
    )
    counts: JsonObject = {"total": len(outcomes)}
    for status in statuses:
        counts[status] = sum(item.status == status for item in outcomes)
    return counts


def _parse_report_observations(raw_steps: list[object]) -> tuple[ReportObservation, ...]:
    observations: list[ReportObservation] = []
    seen: set[str] = set()
    for index, raw_step in enumerate(raw_steps):
        location = f"report.steps[{index}]"
        step = _as_object(raw_step, location=location)
        observation_id = _required_non_empty_string(step, "name", location=location)
        if observation_id in seen:
            raise CertificationValidationError(f"{location}.name {observation_id!r} is duplicated")
        seen.add(observation_id)
        details = _optional_object(step, "details", location=location)
        raw_assertions = (
            [] if details is None else _optional_array(details, "assertions", location=f"{location}.details")
        )
        assertions: list[ReportAssertionObservation] = []
        assertion_ids: set[str] = set()
        for assertion_index, raw_assertion in enumerate(raw_assertions or []):
            assertion_location = f"{location}.details.assertions[{assertion_index}]"
            assertion = _as_object(raw_assertion, location=assertion_location)
            assertion_id = _required_non_empty_string(assertion, "assertionId", location=assertion_location)
            if assertion_id in assertion_ids:
                raise CertificationValidationError(f"{assertion_location}.assertionId {assertion_id!r} is duplicated")
            assertion_ids.add(assertion_id)
            status = _required_non_empty_string(assertion, "status", location=assertion_location)
            if status not in {"passed", "failed"}:
                raise CertificationValidationError(f"{assertion_location}.status must be one of: failed, passed")
            assertions.append(
                ReportAssertionObservation(
                    assertion_id=assertion_id,
                    status=cast(AssertionStatus, status),
                )
            )
        observations.append(
            ReportObservation(
                observation_id=observation_id,
                status=_required_report_step_status(step, "status", location=location),
                assertions=tuple(assertions),
            )
        )
    return tuple(observations)


def _parse_traceability(traceability: dict[str, object]) -> SubmittedTraceability:
    location = "report.traceability"
    release = _required_object(traceability, "suiteRelease", location=location)
    snapshot = _required_object(traceability, "participantPlanSnapshot", location=location)
    scope = _parse_participant_scope(snapshot)
    definitions = _required_array(traceability, "testDefinitions", location=location)
    definition_ids = tuple(
        _required_non_empty_string(
            _as_object(item, location=f"{location}.testDefinitions[{index}]"),
            "id",
            location=f"{location}.testDefinitions[{index}]",
        )
        for index, item in enumerate(definitions)
    )
    if len(set(definition_ids)) != len(definition_ids):
        raise CertificationValidationError(f"{location}.testDefinitions contains duplicate ids")

    instances = tuple(
        cast(
            JsonObject,
            _as_object(item, location=f"{location}.compiledTestInstances[{index}]"),
        )
        for index, item in enumerate(_required_array(traceability, "compiledTestInstances", location=location))
    )
    manifest = _required_object(traceability, "executionManifest", location=location)
    raw_manifest_steps = _required_array(manifest, "steps", location=f"{location}.executionManifest")
    manifest_steps: list[TraceabilityManifestStep] = []
    seen_step_ids: set[str] = set()
    for index, raw_step in enumerate(raw_manifest_steps):
        step_location = f"{location}.executionManifest.steps[{index}]"
        step = _as_object(raw_step, location=step_location)
        step_id = _required_non_empty_string(step, "id", location=step_location)
        if step_id in seen_step_ids:
            raise CertificationValidationError(f"{step_location}.id {step_id!r} is duplicated")
        seen_step_ids.add(step_id)
        manifest_steps.append(
            TraceabilityManifestStep(
                step_id=step_id,
                test_instance_id=_required_non_empty_string(step, "testInstanceId", location=step_location),
                test_definition_id=_required_non_empty_string(step, "testDefinitionId", location=step_location),
                assertion_ids=tuple(
                    _non_empty_string(item, location=f"{step_location}.assertionIds[{assertion_index}]")
                    for assertion_index, item in enumerate(
                        _required_array(step, "assertionIds", location=step_location)
                    )
                ),
                result_observation_id=_required_non_empty_string(
                    step,
                    "resultObservationId",
                    location=step_location,
                ),
                result_status=_required_non_empty_string(step, "resultStatus", location=step_location),
            )
        )
    findings = tuple(
        cast(JsonObject, _as_object(item, location=f"{location}.compilerFindings[{index}]"))
        for index, item in enumerate(_required_array(traceability, "compilerFindings", location=location))
    )
    return SubmittedTraceability(
        suite_release_id=_required_non_empty_string(release, "id", location=f"{location}.suiteRelease"),
        suite_release_version=_required_non_empty_string(
            release,
            "version",
            location=f"{location}.suiteRelease",
        ),
        suite_published_at=_required_non_empty_string(
            release,
            "publishedAt",
            location=f"{location}.suiteRelease",
        ),
        participant_scope=scope,
        test_definition_ids=definition_ids,
        compiled_test_instances=instances,
        execution_manifest_id=_required_non_empty_string(
            manifest,
            "id",
            location=f"{location}.executionManifest",
        ),
        execution_manifest_schema_version=_required_non_empty_string(
            manifest,
            "schemaVersion",
            location=f"{location}.executionManifest",
        ),
        resolved_plan_id=_required_non_empty_string(
            manifest,
            "resolvedPlanId",
            location=f"{location}.executionManifest",
        ),
        manifest_steps=tuple(manifest_steps),
        compiler_findings=findings,
    )


def _parse_participant_scope(snapshot: dict[str, object]) -> ParticipantScope:
    location = "report.traceability.participantPlanSnapshot"
    schema_version = _required_non_empty_string(snapshot, "schemaVersion", location=location)
    document_type = _required_non_empty_string(snapshot, "documentType", location=location)
    if schema_version != "2.0" or document_type != "participant-plan":
        raise CertificationValidationError(f"{location} must identify a schema-version 2.0 participant plan")
    specification = _required_object(snapshot, "specification", location=location)
    selected_capabilities = tuple(
        _non_empty_string(item, location=f"{location}.selectedCapabilityIds[{index}]")
        for index, item in enumerate(_required_array(snapshot, "selectedCapabilityIds", location=location))
    )
    if len(set(selected_capabilities)) != len(selected_capabilities):
        raise CertificationValidationError(f"{location}.selectedCapabilityIds contains duplicates")
    predefined_inputs = tuple(
        cast(JsonObject, _as_object(item, location=f"{location}.predefinedInputs[{index}]"))
        for index, item in enumerate(_required_array(snapshot, "predefinedInputs", location=location))
    )
    return ParticipantScope(
        raw_snapshot=cast(JsonObject, snapshot),
        participant_plan_id=_required_non_empty_string(snapshot, "id", location=location),
        suite_release_id=_required_non_empty_string(snapshot, "suiteReleaseId", location=location),
        scheme=_required_non_empty_string(snapshot, "scheme", location=location),
        specification_id=_required_non_empty_string(specification, "id", location=f"{location}.specification"),
        specification_version=_required_non_empty_string(
            specification,
            "version",
            location=f"{location}.specification",
        ),
        test_scope=_required_non_empty_string(
            specification,
            "testScope",
            location=f"{location}.specification",
        ),
        security_profile=_required_non_empty_string(snapshot, "securityProfile", location=location),
        selected_capability_ids=selected_capabilities,
        predefined_inputs=predefined_inputs,
    )


def _load_json_file(path: Path, *, label: str) -> object:
    try:
        return json.loads(path.resolve().read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CertificationValidationError(f"Invalid JSON {label}: {error.msg}") from error
    except OSError as error:
        raise CertificationValidationError(f"Unable to read {label} file: {error}") from error


def _add_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _as_object(value: object, *, location: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise CertificationValidationError(f"{location} must be a JSON object")
    return cast(dict[str, object], value)


def _required_object(parent: Mapping[str, object], key: str, *, location: str) -> dict[str, object]:
    if key not in parent:
        raise CertificationValidationError(f"{location}.{key} is required")
    return _as_object(parent[key], location=f"{location}.{key}")


def _optional_object(
    parent: Mapping[str, object],
    key: str,
    *,
    location: str,
) -> dict[str, object] | None:
    if key not in parent:
        return None
    return _as_object(parent[key], location=f"{location}.{key}")


def _required_array(parent: Mapping[str, object], key: str, *, location: str) -> list[object]:
    if key not in parent:
        raise CertificationValidationError(f"{location}.{key} is required")
    value = parent[key]
    if not isinstance(value, list):
        raise CertificationValidationError(f"{location}.{key} must be a JSON array")
    return cast(list[object], value)


def _optional_array(
    parent: Mapping[str, object],
    key: str,
    *,
    location: str,
) -> list[object] | None:
    if key not in parent:
        return None
    value = parent[key]
    if not isinstance(value, list):
        raise CertificationValidationError(f"{location}.{key} must be a JSON array")
    return cast(list[object], value)


def _non_empty_string(value: object, *, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CertificationValidationError(f"{location} must be a non-empty string")
    return value.strip()


def _required_non_empty_string(parent: Mapping[str, object], key: str, *, location: str) -> str:
    if key not in parent:
        raise CertificationValidationError(f"{location}.{key} is required")
    return _non_empty_string(parent[key], location=f"{location}.{key}")


def _required_bool(parent: Mapping[str, object], key: str, *, location: str) -> bool:
    if key not in parent:
        raise CertificationValidationError(f"{location}.{key} is required")
    value = parent[key]
    if not isinstance(value, bool):
        raise CertificationValidationError(f"{location}.{key} must be a boolean")
    return value


def _required_report_step_status(parent: Mapping[str, object], key: str, *, location: str) -> CheckStatus:
    value = _required_non_empty_string(parent, key, location=location)
    if value not in VALID_REPORT_STEP_STATUSES:
        allowed_values = ", ".join(sorted(VALID_REPORT_STEP_STATUSES))
        raise CertificationValidationError(f"{location}.{key} must be one of: {allowed_values}")
    return cast(CheckStatus, value)
