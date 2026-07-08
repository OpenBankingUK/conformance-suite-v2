"""Structured result models for conformance smoke-check output."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from conformance.catalogue import Catalogue
from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import CertificationCoverage
from conformance.run_plan_v2 import RunPlanV2, RunPlanV2TargetCoordinates
from conformance.suite_catalog import SuiteMetadata
from conformance.version import REPORT_METADATA_VERSION, resolve_conformance_tool_version

if TYPE_CHECKING:
    from conformance.approved_releases import ApprovedReleasePolicy
    from conformance.test_plan import TestPlan

CheckStatus = Literal["passed", "failed", "warn", "skipped"]
"""Outcome values emitted by smoke-check steps and summaries.

Matches the four-state PRD outcome model: PASS, FAIL, WARN, SKIPPED.
``warn`` is emitted when an otherwise-passing v1 step declares a
``warning`` message in the manifest (signalling a deprecation or risk
that does not block certification). ``skipped`` is emitted when a v1
step cannot run because a prerequisite step produced no response.
"""

ReadinessOutcome = Literal["ready", "incomplete", "non-certifying", "failed"]
"""Aggregate endpoint-first readiness outcome labels for schema-v2 reports."""

ResourceGroupReadinessOutcome = Literal["ready", "incomplete", "failed"]
"""Per-resource-group readiness outcome labels for schema-v2 reports."""


@dataclass(frozen=True)
class SelectedCoverageSummary:
    """Participant-selected endpoint coverage summary for a readiness report.

    Attributes:
        selected_resource_groups: Ordered resource-group ids included in the run.
        selected_endpoint_count: Number of selected endpoints in scope.
        mandatory_endpoint_count: Number of mandatory in-scope endpoints.
        omitted_mandatory_endpoint_count: Number of in-scope mandatory endpoints
            that were not selected.
        coverage_complete: Whether no in-scope mandatory endpoints were omitted.
    """

    selected_resource_groups: tuple[str, ...]
    selected_endpoint_count: int
    mandatory_endpoint_count: int
    omitted_mandatory_endpoint_count: int
    coverage_complete: bool


@dataclass(frozen=True)
class ResourceGroupReadiness:
    """Readiness roll-up for one resource group in an endpoint-first run.

    Attributes:
        resource_group: Resource-group identifier.
        readiness_outcome: Group readiness outcome.
        omitted_mandatory_endpoints: Mandatory endpoint ids omitted from
            selected coverage.
        selected_test_count: Number of selected tests mapped to the group.
        passed_count: Number of selected tests that passed.
        failed_count: Number of selected tests that failed.
        skipped_count: Number of selected tests skipped due to failed runtime
            prerequisites.
        certification_eligible: Whether this group is cert-eligible.
    """

    resource_group: str
    readiness_outcome: ResourceGroupReadinessOutcome
    omitted_mandatory_endpoints: tuple[str, ...]
    selected_test_count: int
    passed_count: int
    failed_count: int
    skipped_count: int
    certification_eligible: bool


@dataclass(frozen=True)
class DcrReadinessStatus:
    """Readiness status block for DCR runs.

    Attributes:
        certifying: Always ``False`` until a formal policy is introduced.
        certifying_blocked_reason: Stable reason string for non-certifying DCR.
        passed_count: Number of selected DCR tests that passed.
        failed_count: Number of selected DCR tests that failed.
        skipped_count: Number of selected DCR tests skipped due to failed
            runtime prerequisites.
    """

    certifying: bool
    certifying_blocked_reason: str
    passed_count: int
    failed_count: int
    skipped_count: int


@dataclass(frozen=True)
class RunReadinessReport:
    """Endpoint-first readiness report emitted for schema-v2 style runs.

    Attributes:
        schema_version: Wire schema version, currently ``"2"``.
        target_coordinates: Frozen run target identity coordinates.
        catalogue_hash: Target catalogue content hash for drift detection.
        selected_coverage_summary: Coverage summary for selected endpoints.
        overall_outcome: Aggregate readiness outcome.
        resource_group_sections: Per-resource-group readiness sections.
        dcr_status: Optional DCR readiness block.
        run_id: Run identifier.
        generated_at: UTC timestamp when the readiness report was generated.
    """

    schema_version: Literal["2"]
    target_coordinates: RunPlanV2TargetCoordinates
    catalogue_hash: str
    selected_coverage_summary: SelectedCoverageSummary
    overall_outcome: ReadinessOutcome
    resource_group_sections: tuple[ResourceGroupReadiness, ...]
    dcr_status: DcrReadinessStatus | None
    run_id: str
    generated_at: datetime


@dataclass(frozen=True)
class ResourceGroupExecutionSummary:
    """Execution status counters used to derive resource-group readiness.

    Attributes:
        selected_test_count: Number of selected tests in the resource group.
        passed_count: Number of selected tests that passed.
        failed_count: Number of selected tests that failed.
        skipped_count: Number of selected tests skipped due to failed runtime
            prerequisites.
    """

    selected_test_count: int
    passed_count: int
    failed_count: int
    skipped_count: int


def summarise_selected_coverage(catalogue: Catalogue, run_plan: RunPlanV2) -> SelectedCoverageSummary:
    """Build a selected-coverage summary from a catalogue and RunPlanV2.

    Args:
        catalogue: Validated endpoint catalogue for the target.
        run_plan: Endpoint-first run plan with endpoint selections.

    Returns:
        Selected coverage summary describing selected endpoints, in-scope
        mandatory endpoint totals, and mandatory omissions.
    """
    selected_endpoint_ids = {selection.endpoint_id for selection in run_plan.endpoint_selections if selection.selected}
    selected_resource_groups = _resolve_selected_resource_groups(catalogue, run_plan)
    mandatory_in_scope = _mandatory_endpoint_ids_in_scope(catalogue, selected_resource_groups)
    omitted = tuple(
        sorted(endpoint_id for endpoint_id in mandatory_in_scope if endpoint_id not in selected_endpoint_ids)
    )
    return SelectedCoverageSummary(
        selected_resource_groups=selected_resource_groups,
        selected_endpoint_count=len(selected_endpoint_ids),
        mandatory_endpoint_count=len(mandatory_in_scope),
        omitted_mandatory_endpoint_count=len(omitted),
        coverage_complete=len(omitted) == 0,
    )


def omitted_mandatory_endpoint_ids_by_resource_group(
    catalogue: Catalogue,
    run_plan: RunPlanV2,
) -> dict[str, tuple[str, ...]]:
    """Return omitted mandatory endpoint ids keyed by selected resource group.

    Args:
        catalogue: Validated endpoint catalogue for the target.
        run_plan: Endpoint-first run plan with endpoint selections.

    Returns:
        Mapping of selected resource-group ids to sorted tuples of mandatory
        endpoint ids omitted from selected coverage.
    """
    selected_endpoint_ids = {selection.endpoint_id for selection in run_plan.endpoint_selections if selection.selected}
    resource_groups = _resolve_selected_resource_groups(catalogue, run_plan)
    grouped: dict[str, list[str]] = {group: [] for group in resource_groups}
    for endpoint in catalogue.endpoints:
        if endpoint.resource_group is None or endpoint.resource_group not in grouped:
            continue
        if endpoint.requirement != "mandatory":
            continue
        if endpoint.endpoint_id not in selected_endpoint_ids:
            grouped[endpoint.resource_group].append(endpoint.endpoint_id)
    return {group: tuple(sorted(ids)) for group, ids in grouped.items()}


def build_resource_group_readiness_sections(
    *,
    catalogue: Catalogue,
    run_plan: RunPlanV2,
    execution_summary_by_resource_group: Mapping[str, ResourceGroupExecutionSummary],
) -> tuple[ResourceGroupReadiness, ...]:
    """Build per-resource-group readiness sections for a RunPlanV2 execution.

    Args:
        catalogue: Validated endpoint catalogue for the target.
        run_plan: Endpoint-first run plan with endpoint selections.
        execution_summary_by_resource_group: Selected-test execution counters
            keyed by resource-group id.

    Returns:
        Ordered readiness sections for selected resource groups.
    """
    omitted_by_group = omitted_mandatory_endpoint_ids_by_resource_group(catalogue, run_plan)
    sections: list[ResourceGroupReadiness] = []
    for resource_group in _resolve_selected_resource_groups(catalogue, run_plan):
        summary = execution_summary_by_resource_group.get(resource_group, ResourceGroupExecutionSummary(0, 0, 0, 0))
        omitted = omitted_by_group.get(resource_group, ())
        readiness_outcome = _resource_group_readiness_outcome(
            failed_count=summary.failed_count,
            omitted_mandatory_endpoints=omitted,
        )
        sections.append(
            ResourceGroupReadiness(
                resource_group=resource_group,
                readiness_outcome=readiness_outcome,
                omitted_mandatory_endpoints=omitted,
                selected_test_count=summary.selected_test_count,
                passed_count=summary.passed_count,
                failed_count=summary.failed_count,
                skipped_count=summary.skipped_count,
                certification_eligible=(
                    readiness_outcome == "ready" and summary.failed_count == 0 and summary.skipped_count == 0
                ),
            )
        )
    return tuple(sections)


def determine_readiness_outcome(
    *,
    selected_coverage_summary: SelectedCoverageSummary,
    resource_group_sections: tuple[ResourceGroupReadiness, ...],
    dcr_status: DcrReadinessStatus | None,
) -> ReadinessOutcome:
    """Determine aggregate readiness outcome from report subcomponents.

    Args:
        selected_coverage_summary: Selected coverage summary block.
        resource_group_sections: Per-resource-group readiness sections.
        dcr_status: Optional DCR readiness status block.

    Returns:
        Aggregate readiness outcome label.
    """
    if dcr_status is not None:
        return "non-certifying"
    if any(section.failed_count > 0 for section in resource_group_sections):
        return "failed"
    if not selected_coverage_summary.coverage_complete or any(
        section.readiness_outcome == "incomplete" for section in resource_group_sections
    ):
        return "incomplete"
    if resource_group_sections and all(section.certification_eligible for section in resource_group_sections):
        return "ready"
    return "failed"


def build_dcr_readiness_status(*, passed_count: int, failed_count: int, skipped_count: int) -> DcrReadinessStatus:
    """Build the fixed non-certifying DCR readiness block.

    Args:
        passed_count: Number of selected DCR tests that passed.
        failed_count: Number of selected DCR tests that failed.
        skipped_count: Number of selected DCR tests skipped due to failed
            runtime prerequisites.

    Returns:
        DCR readiness status block with fixed non-certifying policy fields.
    """
    return DcrReadinessStatus(
        certifying=False,
        certifying_blocked_reason="No DCR certification policy exists for this tool",
        passed_count=passed_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
    )


def serialise_readiness_report(report: RunReadinessReport) -> dict[str, JsonValue]:
    """Serialise a readiness report into a JSON-compatible dictionary.

    Args:
        report: Readiness report to serialise.

    Returns:
        CamelCase JSON dictionary suitable for writing to result files.
    """
    data: dict[str, JsonValue] = {
        "schemaVersion": report.schema_version,
        "targetCoordinates": {
            "standard": report.target_coordinates.standard,
            "specification": report.target_coordinates.specification,
            "securityProfile": report.target_coordinates.security_profile,
            "specificationVersion": report.target_coordinates.specification_version,
            "catalogueHash": report.target_coordinates.catalogue_hash,
        },
        "catalogueHash": report.catalogue_hash,
        "selectedCoverageSummary": {
            "selectedResourceGroups": list(report.selected_coverage_summary.selected_resource_groups),
            "selectedEndpointCount": report.selected_coverage_summary.selected_endpoint_count,
            "mandatoryEndpointCount": report.selected_coverage_summary.mandatory_endpoint_count,
            "omittedMandatoryEndpointCount": report.selected_coverage_summary.omitted_mandatory_endpoint_count,
            "coverageComplete": report.selected_coverage_summary.coverage_complete,
        },
        "overallOutcome": report.overall_outcome,
        "resourceGroupSections": [
            {
                "resourceGroup": section.resource_group,
                "readinessOutcome": section.readiness_outcome,
                "omittedMandatoryEndpoints": list(section.omitted_mandatory_endpoints),
                "selectedTestCount": section.selected_test_count,
                "passedCount": section.passed_count,
                "failedCount": section.failed_count,
                "skippedCount": section.skipped_count,
                "certificationEligible": section.certification_eligible,
            }
            for section in report.resource_group_sections
        ],
        "runId": report.run_id,
        "generatedAt": report.generated_at.isoformat(),
    }
    if report.dcr_status is not None:
        data["dcrStatus"] = {
            "certifying": report.dcr_status.certifying,
            "certifyingBlockedReason": report.dcr_status.certifying_blocked_reason,
            "passedCount": report.dcr_status.passed_count,
            "failedCount": report.dcr_status.failed_count,
            "skippedCount": report.dcr_status.skipped_count,
        }
    return data


def parse_readiness_report(data: JsonValue) -> RunReadinessReport:
    """Parse a JSON readiness-report object into a typed dataclass instance.

    Args:
        data: Raw JSON value loaded from a result file.

    Returns:
        Parsed readiness report dataclass.

    Raises:
        ValueError: If the payload is missing required fields or has invalid
            field types.
    """
    if not isinstance(data, dict):
        raise ValueError("Readiness report must be a JSON object")
    schema_version = _readiness_require_literal(data, "schemaVersion", "2")
    target_coordinates = _parse_readiness_target_coordinates(data)
    selected_coverage_summary = _parse_selected_coverage_summary(data)
    overall_outcome = _parse_readiness_outcome(data.get("overallOutcome"))
    resource_group_sections = _parse_resource_group_sections(data)
    dcr_status = _parse_dcr_status(data.get("dcrStatus"))
    catalogue_hash = _readiness_require_string(data, "catalogueHash")
    run_id = _readiness_require_string(data, "runId")
    generated_at = _readiness_require_datetime(data, "generatedAt")
    return RunReadinessReport(
        schema_version=schema_version,
        target_coordinates=target_coordinates,
        catalogue_hash=catalogue_hash,
        selected_coverage_summary=selected_coverage_summary,
        overall_outcome=overall_outcome,
        resource_group_sections=resource_group_sections,
        dcr_status=dcr_status,
        run_id=run_id,
        generated_at=generated_at,
    )


@dataclass(frozen=True)
class StepResult:
    """Result for a single observable conformance step.

    Attributes:
        name: Stable step identifier for consumers of the result JSON.
        status: Outcome for this step (one of the ``CheckStatus`` values).
        message: Human-readable summary of the step outcome.
        url: Optional endpoint URL involved in the step.
        status_code: Optional HTTP status code returned by the endpoint.
        details: Optional structured data safe to include in the result file.
        mandatory: Whether this step was declared mandatory in the manifest.
            Used by the aggregate ``certificationEligibility`` block; not
            serialised on the individual step entry to keep the per-step
            shape stable.
        consumed_test_value_keys: Ordered test-value key names referenced by
            this step via ``${testValues.<key>}`` placeholders.
        customised_test_value_keys: Ordered subset of
            ``consumed_test_value_keys`` overridden by participant inputs.
    """

    name: str
    status: CheckStatus
    message: str
    url: str | None = None
    status_code: int | None = None
    details: Mapping[str, JsonValue] = field(default_factory=dict)
    mandatory: bool = False
    consumed_test_value_keys: tuple[str, ...] = ()
    customised_test_value_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Freeze nested details so result objects stay immutable after creation."""
        object.__setattr__(self, "details", MappingProxyType(deepcopy(dict(self.details))))

    def to_json_object(self) -> JsonObject:
        """Convert the step result into the public JSON report shape.

        Returns:
            JSON object suitable for serialisation into the result file.
        """
        result: JsonObject = {
            "name": self.name,
            "status": self.status,
            "message": self.message,
        }
        if self.url is not None:
            result["url"] = self.url
        if self.status_code is not None:
            result["statusCode"] = self.status_code
        details: JsonObject = deepcopy(dict(self.details))
        if self.consumed_test_value_keys:
            details["consumedTestValueKeys"] = list(self.consumed_test_value_keys)
        if self.customised_test_value_keys:
            details["customisedTestValueKeys"] = list(self.customised_test_value_keys)
        if details:
            result["details"] = details
        return result


@dataclass(frozen=True)
class SmokeCheckResult:
    """Complete result for a model-bank smoke-check execution.

    Attributes:
        environment: Environment name copied from the input config.
        status: Aggregate pass/fail outcome across all steps.
        started_at: UTC timestamp when execution started.
        finished_at: UTC timestamp when execution finished.
        steps: Ordered step results that explain the aggregate outcome.
        plan_summary: Optional summary of the :class:`TestPlan` that drove
            this run. Embedded into the JSON report as the top-level
            ``plan`` block. ``None`` for non-manifest smoke checks and v0
            manifest runs, which have no plan model.
        deselected_mandatory_step_ids: Step ids that were declared
            ``mandatory`` in the manifest but deselected from the plan.
            Surfaced verbatim in ``certificationEligibility`` so OBL can
            see exactly which mandatory coverage the participant skipped.
        approved_release_policy: Optional approved-release policy used to
            self-assess whether the report's tool version is eligible for
            certification submission. ``None`` means the report explicitly
            marks the check as not supplied.
        suite_metadata: Optional catalog metadata for config-resolved suite
            runs. Omitted for legacy smoke checks and explicit manifest runs
            so their public result shape remains stable.
        certification_coverage: Whether the manifest used for this run declares
            full certification coverage (``complete``) or is intentionally
            partial / non-certifying (``partial``). Defaults to ``partial`` for
            non-manifest smoke checks and v0 manifest runs, which pre-date the
            certification eligibility model. A ``partial`` value blocks
            ``certificationEligibility.eligible`` even when all mandatory steps
            pass and the tool version is approved.
        auth_metadata_evidence: Optional non-secret auth-bundle evidence block
            derived from manifest ``authMetadata`` and filtered by the selected
            or executed plan.
        environment_capability_evidence: Optional non-secret environment
            capability decisions describing suite/auth/environment compatibility
            for this run.
        test_value_profile_evidence: Optional non-secret test-value profile
            evidence describing resolved profile source, conditional outcomes,
            and masked effective values.
        custom_test_values_active: Whether this run used a non-default profile
            and/or participant custom-value overrides. ``True`` marks the run as
            exploratory and blocks certification eligibility.
        custom_test_value_impact: Optional persisted impact-evidence block that
            maps participant override keys to executed and non-executed manifest
            field references.
        readiness_report: Optional endpoint-first readiness report for schema-v2
            runs.
    """

    environment: str
    status: CheckStatus
    started_at: datetime
    finished_at: datetime
    steps: tuple[StepResult, ...]
    plan_summary: Mapping[str, int] | None = None
    deselected_mandatory_step_ids: tuple[str, ...] = ()
    approved_release_policy: ApprovedReleasePolicy | None = None
    suite_metadata: SuiteMetadata | None = None
    certification_coverage: CertificationCoverage = "partial"
    auth_metadata_evidence: Mapping[str, JsonValue] | None = None
    environment_capability_evidence: Mapping[str, JsonValue] | None = None
    test_value_profile_evidence: Mapping[str, JsonValue] | None = None
    custom_test_values_active: bool = False
    custom_test_value_impact: Mapping[str, JsonValue] | None = None
    readiness_report: RunReadinessReport | None = None

    def to_json_object(self) -> JsonObject:
        """Convert the smoke-check result into the public JSON report shape.

        Returns:
            JSON object suitable for serialisation into the result file.
        """
        tool_version = resolve_conformance_tool_version()
        body: JsonObject = {
            "metadata": {"reportVersion": REPORT_METADATA_VERSION},
            "tool": {"version": tool_version},
            "environment": self.environment,
            "status": self.status,
            "startedAt": self.started_at.isoformat(),
            "finishedAt": self.finished_at.isoformat(),
            "summary": {
                "total": len(self.steps),
                "passed": sum(1 for step in self.steps if step.status == "passed"),
                "failed": sum(1 for step in self.steps if step.status == "failed"),
                "warn": sum(1 for step in self.steps if step.status == "warn"),
                "skipped": sum(1 for step in self.steps if step.status == "skipped"),
            },
            "certificationEligibility": _build_eligibility(
                self.steps,
                custom_test_values_active=self.custom_test_values_active,
                test_value_profile_evidence=self.test_value_profile_evidence,
                deselected_mandatory_step_ids=self.deselected_mandatory_step_ids,
                approved_release_policy=self.approved_release_policy,
                tool_version=tool_version,
                certification_coverage=self.certification_coverage,
            ),
            "steps": [step.to_json_object() for step in self.steps],
        }
        if self.suite_metadata is not None:
            body["suite"] = self.suite_metadata.to_json_object()
        if self.plan_summary is not None:
            body["plan"] = dict(self.plan_summary)
        if self.auth_metadata_evidence is not None:
            body["authMetadata"] = deepcopy(dict(self.auth_metadata_evidence))
        if self.environment_capability_evidence is not None:
            body["environmentCapabilities"] = deepcopy(dict(self.environment_capability_evidence))
        if self.test_value_profile_evidence is not None:
            body["testValueProfile"] = deepcopy(dict(self.test_value_profile_evidence))
        if self.custom_test_value_impact is not None:
            body["customTestValueImpact"] = deepcopy(dict(self.custom_test_value_impact))
        if self.readiness_report is not None:
            body["readinessReport"] = serialise_readiness_report(self.readiness_report)
        return body


def build_smoke_check_result(
    environment: str,
    steps: list[StepResult],
    *,
    started_at: datetime,
    plan: TestPlan | None = None,
    approved_release_policy: ApprovedReleasePolicy | None = None,
    suite_metadata: SuiteMetadata | None = None,
    certification_coverage: CertificationCoverage = "partial",
    auth_metadata_evidence: Mapping[str, JsonValue] | None = None,
    environment_capability_evidence: Mapping[str, JsonValue] | None = None,
    test_value_profile_evidence: Mapping[str, JsonValue] | None = None,
    custom_test_values_active: bool = False,
    custom_test_value_impact: Mapping[str, JsonValue] | None = None,
    readiness_report: RunReadinessReport | None = None,
) -> SmokeCheckResult:
    """Build an aggregate smoke-check result from collected step outcomes.

    Args:
        environment: Environment name copied from the input config.
        steps: Ordered mutable list of step outcomes collected by the runner.
        started_at: UTC timestamp captured before execution began.
        plan: Optional :class:`TestPlan` that drove this run. When supplied,
            its summary is embedded as the report's top-level ``plan`` block
            and its deselected-mandatory step ids feed into
            ``certificationEligibility``. Omit for non-manifest smoke checks
            and v0 manifest runs.
        approved_release_policy: Optional approved-release policy used by the
            generated report's participant-side certification self-assessment.
            Omit when no approved-release policy was supplied with the
            participant config.
        suite_metadata: Optional catalog metadata for a config-resolved suite
            run. Omit for smoke checks and explicit manifest runs to preserve
            their existing result shape.
        certification_coverage: Whether the manifest declares full certification
            coverage (``complete``) or is intentionally partial / non-certifying
            (``partial``). Defaults to ``"partial"`` so non-manifest callers and
            v0 manifest callers are safe by default. A ``partial`` value blocks
            ``certificationEligibility.eligible`` even when all mandatory steps
            pass and the tool version is approved.
        auth_metadata_evidence: Optional non-secret auth-bundle evidence block
            derived from manifest ``authMetadata``.
        environment_capability_evidence: Optional non-secret environment
            capability decisions for the selected suite/auth/environment
            combination.
        test_value_profile_evidence: Optional non-secret test-value profile
            evidence describing default/override selection outcomes and masked
            effective values used by this run.
        custom_test_values_active: Whether this run used a non-default profile
            and/or participant custom-value overrides.
        custom_test_value_impact: Optional persisted impact-evidence block that
            maps participant override keys to executed and non-executed manifest
            field references.
        readiness_report: Optional endpoint-first readiness report for schema-v2
            runs.

    Returns:
        Immutable smoke-check result with finished timestamp and aggregate status.
    """
    finished_at = datetime.now(UTC)
    # WARN is a non-blocking signal (PRD: "does not block certification"), so
    # a step with status=="warn" must not flip the aggregate run to "failed".
    # Only FAILED and SKIPPED (which always implies an earlier failure) fail
    # the run.
    status: CheckStatus = "passed" if all(step.status in {"passed", "warn"} for step in steps) else "failed"
    plan_summary: Mapping[str, int] | None = None
    deselected_mandatory: tuple[str, ...] = ()
    if plan is not None:
        plan_summary = plan.summary()
        deselected_mandatory = tuple(plan.deselected_mandatory_step_ids())
    return SmokeCheckResult(
        environment=environment,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        steps=tuple(steps),
        plan_summary=plan_summary,
        deselected_mandatory_step_ids=deselected_mandatory,
        approved_release_policy=approved_release_policy,
        suite_metadata=suite_metadata,
        certification_coverage=certification_coverage,
        auth_metadata_evidence=auth_metadata_evidence,
        environment_capability_evidence=environment_capability_evidence,
        test_value_profile_evidence=test_value_profile_evidence,
        custom_test_values_active=custom_test_values_active,
        custom_test_value_impact=custom_test_value_impact,
        readiness_report=readiness_report,
    )


def _resolve_selected_resource_groups(catalogue: Catalogue, run_plan: RunPlanV2) -> tuple[str, ...]:
    """Resolve selected resource groups for readiness calculations.

    Args:
        catalogue: Validated endpoint catalogue for the run target.
        run_plan: Endpoint-first run plan with optional resource-group picks.

    Returns:
        Ordered selected resource-group ids.
    """
    if run_plan.resource_groups:
        return run_plan.resource_groups
    selected_endpoint_ids = {selection.endpoint_id for selection in run_plan.endpoint_selections if selection.selected}
    groups: list[str] = []
    for endpoint in catalogue.endpoints:
        if endpoint.endpoint_id not in selected_endpoint_ids:
            continue
        if endpoint.resource_group is None:
            continue
        if endpoint.resource_group in groups:
            continue
        groups.append(endpoint.resource_group)
    return tuple(groups)


def _mandatory_endpoint_ids_in_scope(catalogue: Catalogue, selected_resource_groups: tuple[str, ...]) -> set[str]:
    """Collect mandatory endpoint ids within selected readiness scope.

    Args:
        catalogue: Validated endpoint catalogue for the run target.
        selected_resource_groups: Ordered selected resource-group ids.

    Returns:
        Set of mandatory endpoint ids in scope.
    """
    mandatory: set[str] = set()
    has_resource_groups = any(endpoint.resource_group is not None for endpoint in catalogue.endpoints)
    for endpoint in catalogue.endpoints:
        if endpoint.requirement != "mandatory":
            continue
        if has_resource_groups:
            if endpoint.resource_group is None:
                continue
            if endpoint.resource_group not in selected_resource_groups:
                continue
        mandatory.add(endpoint.endpoint_id)
    return mandatory


def _resource_group_readiness_outcome(
    *,
    failed_count: int,
    omitted_mandatory_endpoints: tuple[str, ...],
) -> ResourceGroupReadinessOutcome:
    """Determine one resource group's readiness outcome.

    Args:
        failed_count: Number of selected tests in the group that failed.
        omitted_mandatory_endpoints: Mandatory endpoint ids omitted from
            selected coverage.

    Returns:
        Resource-group readiness outcome label.
    """
    if failed_count > 0:
        return "failed"
    if omitted_mandatory_endpoints:
        return "incomplete"
    return "ready"


def _readiness_require_string(obj: Mapping[str, JsonValue], key: str) -> str:
    """Read one required non-empty string field from readiness JSON.

    Args:
        obj: Source JSON object.
        key: Field name to read.

    Returns:
        Parsed non-empty string value.

    Raises:
        ValueError: If the field is missing or not a non-empty string.
    """
    value = obj.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Readiness report field {key!r} must be a non-empty string")
    return value


def _readiness_require_int(obj: Mapping[str, JsonValue], key: str) -> int:
    """Read one required integer field from readiness JSON.

    Args:
        obj: Source JSON object.
        key: Field name to read.

    Returns:
        Parsed integer value.

    Raises:
        ValueError: If the field is missing or not an integer.
    """
    value = obj.get(key)
    if not isinstance(value, int):
        raise ValueError(f"Readiness report field {key!r} must be an integer")
    return value


def _readiness_require_bool(obj: Mapping[str, JsonValue], key: str) -> bool:
    """Read one required boolean field from readiness JSON.

    Args:
        obj: Source JSON object.
        key: Field name to read.

    Returns:
        Parsed boolean value.

    Raises:
        ValueError: If the field is missing or not a boolean.
    """
    value = obj.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"Readiness report field {key!r} must be a boolean")
    return value


def _readiness_require_literal(obj: Mapping[str, JsonValue], key: str, expected: str) -> Literal["2"]:
    """Read one required literal field from readiness JSON.

    Args:
        obj: Source JSON object.
        key: Field name to read.
        expected: Required literal value.

    Returns:
        The expected value when present.

    Raises:
        ValueError: If the field is missing or does not equal ``expected``.
    """
    value = _readiness_require_string(obj, key)
    if value != expected:
        raise ValueError(f"Readiness report field {key!r} must be {expected!r}")
    return "2"


def _readiness_require_datetime(obj: Mapping[str, JsonValue], key: str) -> datetime:
    """Read one required ISO-8601 datetime field from readiness JSON.

    Args:
        obj: Source JSON object.
        key: Field name to read.

    Returns:
        Parsed UTC-aware datetime value.

    Raises:
        ValueError: If the field is missing or is not a valid ISO datetime.
    """
    raw_value = _readiness_require_string(obj, key)
    try:
        parsed = datetime.fromisoformat(raw_value)
    except ValueError as exc:
        raise ValueError(f"Readiness report field {key!r} must be a valid ISO datetime") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_readiness_target_coordinates(data: Mapping[str, JsonValue]) -> RunPlanV2TargetCoordinates:
    """Parse target coordinates from readiness JSON.

    Args:
        data: Readiness JSON object.

    Returns:
        Parsed run target coordinates.

    Raises:
        ValueError: If ``targetCoordinates`` is missing or invalid.
    """
    raw_target = data.get("targetCoordinates")
    if not isinstance(raw_target, dict):
        raise ValueError("Readiness report field 'targetCoordinates' must be a JSON object")
    return RunPlanV2TargetCoordinates(
        standard=_readiness_require_string(raw_target, "standard"),
        specification=_readiness_require_string(raw_target, "specification"),
        security_profile=_readiness_require_string(raw_target, "securityProfile"),
        specification_version=_readiness_require_string(raw_target, "specificationVersion"),
        catalogue_hash=_readiness_require_string(raw_target, "catalogueHash"),
    )


def _parse_selected_coverage_summary(data: Mapping[str, JsonValue]) -> SelectedCoverageSummary:
    """Parse selected coverage summary from readiness JSON.

    Args:
        data: Readiness JSON object.

    Returns:
        Parsed selected coverage summary.

    Raises:
        ValueError: If ``selectedCoverageSummary`` is missing or invalid.
    """
    raw_summary = data.get("selectedCoverageSummary")
    if not isinstance(raw_summary, dict):
        raise ValueError("Readiness report field 'selectedCoverageSummary' must be a JSON object")
    raw_groups = raw_summary.get("selectedResourceGroups")
    if not isinstance(raw_groups, list) or not all(isinstance(item, str) for item in raw_groups):
        raise ValueError("Readiness report field 'selectedResourceGroups' must be a list of strings")
    selected_groups: tuple[str, ...] = tuple(item for item in raw_groups if isinstance(item, str))
    return SelectedCoverageSummary(
        selected_resource_groups=selected_groups,
        selected_endpoint_count=_readiness_require_int(raw_summary, "selectedEndpointCount"),
        mandatory_endpoint_count=_readiness_require_int(raw_summary, "mandatoryEndpointCount"),
        omitted_mandatory_endpoint_count=_readiness_require_int(raw_summary, "omittedMandatoryEndpointCount"),
        coverage_complete=_readiness_require_bool(raw_summary, "coverageComplete"),
    )


def _parse_readiness_outcome(raw_outcome: JsonValue) -> ReadinessOutcome:
    """Parse aggregate readiness outcome from readiness JSON.

    Args:
        raw_outcome: Raw outcome JSON value.

    Returns:
        Parsed readiness outcome.

    Raises:
        ValueError: If the outcome is missing or not a valid value.
    """
    valid_outcomes: tuple[ReadinessOutcome, ...] = ("ready", "incomplete", "non-certifying", "failed")
    if isinstance(raw_outcome, str) and raw_outcome in valid_outcomes:
        return raw_outcome
    raise ValueError(f"Readiness report field 'overallOutcome' must be one of {list(valid_outcomes)}")


def _parse_resource_group_sections(data: Mapping[str, JsonValue]) -> tuple[ResourceGroupReadiness, ...]:
    """Parse resource-group readiness sections from readiness JSON.

    Args:
        data: Readiness JSON object.

    Returns:
        Parsed tuple of resource-group readiness sections.

    Raises:
        ValueError: If ``resourceGroupSections`` is missing or invalid.
    """
    raw_sections = data.get("resourceGroupSections")
    if not isinstance(raw_sections, list):
        raise ValueError("Readiness report field 'resourceGroupSections' must be a JSON array")
    sections: list[ResourceGroupReadiness] = []
    valid_outcomes: tuple[ResourceGroupReadinessOutcome, ...] = ("ready", "incomplete", "failed")
    for raw_section in raw_sections:
        if not isinstance(raw_section, dict):
            raise ValueError("Each readiness resource-group section must be a JSON object")
        raw_outcome = raw_section.get("readinessOutcome")
        if not isinstance(raw_outcome, str) or raw_outcome not in valid_outcomes:
            raise ValueError(f"Resource-group readiness outcome must be one of {list(valid_outcomes)}")
        raw_omitted = raw_section.get("omittedMandatoryEndpoints")
        if not isinstance(raw_omitted, list) or not all(isinstance(item, str) for item in raw_omitted):
            raise ValueError("Field 'omittedMandatoryEndpoints' must be a list of strings")
        omitted_endpoints: tuple[str, ...] = tuple(item for item in raw_omitted if isinstance(item, str))
        sections.append(
            ResourceGroupReadiness(
                resource_group=_readiness_require_string(raw_section, "resourceGroup"),
                readiness_outcome=raw_outcome,
                omitted_mandatory_endpoints=omitted_endpoints,
                selected_test_count=_readiness_require_int(raw_section, "selectedTestCount"),
                passed_count=_readiness_require_int(raw_section, "passedCount"),
                failed_count=_readiness_require_int(raw_section, "failedCount"),
                skipped_count=_readiness_require_int(raw_section, "skippedCount"),
                certification_eligible=_readiness_require_bool(raw_section, "certificationEligible"),
            )
        )
    return tuple(sections)


def _parse_dcr_status(raw_data: JsonValue) -> DcrReadinessStatus | None:
    """Parse optional DCR readiness status from readiness JSON.

    Args:
        raw_data: Raw ``dcrStatus`` JSON value.

    Returns:
        Parsed DCR readiness status, or ``None`` when absent.

    Raises:
        ValueError: If the supplied DCR status object is invalid.
    """
    if raw_data is None:
        return None
    if not isinstance(raw_data, dict):
        raise ValueError("Readiness report field 'dcrStatus' must be a JSON object when present")
    return DcrReadinessStatus(
        certifying=_readiness_require_bool(raw_data, "certifying"),
        certifying_blocked_reason=_readiness_require_string(raw_data, "certifyingBlockedReason"),
        passed_count=_readiness_require_int(raw_data, "passedCount"),
        failed_count=_readiness_require_int(raw_data, "failedCount"),
        skipped_count=_readiness_require_int(raw_data, "skippedCount"),
    )


def _build_eligibility(
    steps: tuple[StepResult, ...],
    *,
    custom_test_values_active: bool = False,
    test_value_profile_evidence: Mapping[str, JsonValue] | None = None,
    deselected_mandatory_step_ids: tuple[str, ...] = (),
    approved_release_policy: ApprovedReleasePolicy | None = None,
    tool_version: str,
    certification_coverage: CertificationCoverage = "partial",
) -> JsonObject:
    """Build the ``certificationEligibility`` block for the result file.

    Implements the PRD's Certification Eligibility Assessment for Phase 1: a
    self-service check that a run is suitable for submission to OBL for
    formal certification. The criteria are driven by which steps were
    declared ``mandatory`` in the manifest — *not* hardcoded — so OBL
    Standards can adjust mandatory coverage by editing configuration.

    Two independent gates contribute to ``eligible``:

    * **Value-purity gate** (``valuePurityPassed``): blocks when the run used
      custom test values that differ from the suite baseline, making it an
      Exploratory Run ineligible for certification.
    * **Coverage gate** (``coveragePassed``): blocks when the manifest does not
      declare complete certification coverage, mandatory steps are missing or
      failed/skipped, or no mandatory steps are declared at all.

    Additional eligibility rules:
        * A run is eligible only when at least one mandatory step ran *and*
          every mandatory step finished as ``passed`` or ``warn``.
        * An approved-release policy must be supplied, and the report must have
          been generated by a tool version listed in that policy.
        * The manifest must declare ``certificationCoverage: complete``;
          ``partial`` (including the default when the key is absent) blocks
          eligibility even when all mandatory steps pass and the tool version
          is approved. This prevents smoke-only suites from satisfying the
          eligibility check mechanically.
        * ``warn`` is non-blocking (PRD: warnings *"do not block
          certification"*).
        * ``failed`` and ``skipped`` on a mandatory step block eligibility.
          ``skipped`` always implies an earlier failure, so it is treated as
          blocking by definition.
        * Deselecting a mandatory step from the test plan also blocks
          eligibility and takes precedence over every other reason — a run
          that never executed a mandatory step cannot demonstrate coverage
          of it, regardless of why.
        * A run with no mandatory steps cannot certify — the PRD
          requires *"all mandatory tests were included in the run"*, so
          zero mandatory steps is treated as "not a certification
          candidate". This applies equally to manifest runs that declare
          no mandatory steps and to non-manifest smoke checks (which have
          no mandatory concept at all).

    The approved-release check is an advisory participant-side self-assessment.
    OBL-side validation remains authoritative and recomputes eligibility from
    independently supplied report, manifest, and policy inputs.

    Args:
        steps: Ordered step results from the smoke-check run.
        custom_test_values_active: Whether this run used a non-default profile
            and/or participant custom-value overrides. ``True`` marks the run as
            exploratory and blocks certification eligibility. Used as the
            fallback when ``test_value_profile_evidence`` is not supplied.
        test_value_profile_evidence: Optional non-secret test-value profile
            evidence block as serialised in the result JSON. When supplied,
            ``valuePurityPassed`` is derived from ``source`` and
            ``baselineDeltaKeys`` rather than from ``custom_test_values_active``.
        deselected_mandatory_step_ids: Ids of manifest steps that were
            declared mandatory but deselected from the plan. Sourced from
            :meth:`TestPlan.deselected_mandatory_step_ids`. Empty when
            no plan was supplied to :func:`build_smoke_check_result`.
        approved_release_policy: Optional approved-release policy parsed from
            participant configuration.
        tool_version: Tool version resolved for the top-level report metadata.
        certification_coverage: Manifest-level certification coverage declaration.
            Defaults to ``"partial"`` for backwards-compatible callers that do not
            supply a manifest. A ``partial`` value blocks eligibility and adds a
            stable reason string so the blocker is machine-readable.

    Returns:
        JSON object containing the boolean ``eligible`` flag,
        ``valuePurityPassed`` and ``coveragePassed`` gate booleans,
        per-status mandatory counts, ``mandatoryDeselected`` count and
        ``mandatoryDeselectedStepIds`` list, a ``certificationCoverage`` block,
        an ``approvedRelease`` block, and blocking ``reason``/``reasons`` values
        when not eligible.
    """
    mandatory_steps = [step for step in steps if step.mandatory]
    mandatory_passed = sum(1 for step in mandatory_steps if step.status == "passed")
    mandatory_failed = sum(1 for step in mandatory_steps if step.status == "failed")
    mandatory_warn = sum(1 for step in mandatory_steps if step.status == "warn")
    mandatory_skipped = sum(1 for step in mandatory_steps if step.status == "skipped")
    mandatory_deselected = len(deselected_mandatory_step_ids)
    mandatory_total = len(mandatory_steps) + mandatory_deselected

    value_purity_passed = _compute_value_purity_passed(
        test_value_profile_evidence,
        custom_test_values_active=custom_test_values_active,
    )
    coverage_passed = _compute_coverage_passed(
        certification_coverage=certification_coverage,
        mandatory_total=mandatory_total,
        mandatory_failed=mandatory_failed,
        mandatory_skipped=mandatory_skipped,
        mandatory_deselected=mandatory_deselected,
    )

    counts: JsonObject = {
        "mandatoryTotal": mandatory_total,
        "mandatoryPassed": mandatory_passed,
        "mandatoryFailed": mandatory_failed,
        "mandatoryWarn": mandatory_warn,
        "mandatorySkipped": mandatory_skipped,
        "mandatoryDeselected": mandatory_deselected,
        "mandatoryDeselectedStepIds": list(deselected_mandatory_step_ids),
    }

    approved_release = _build_approved_release_eligibility(
        approved_release_policy=approved_release_policy,
        tool_version=tool_version,
    )

    coverage_block: JsonObject = {"value": certification_coverage}

    reasons: list[JsonValue] = []
    # Precedence order for the primary ``reason`` field (the first entry):
    # 1. Custom test values active — exploratory runs are never certifiable.
    # 2. Deselected-mandatory — step never ran so cannot demonstrate coverage.
    # 3. Failed / skipped mandatory steps.
    # 4. No mandatory steps declared.
    # 5. Partial manifest coverage — manifest-level certification boundary.
    # 6. Unapproved tool version (policy-level check).
    # 7. Missing approved-release policy (advisory self-assessment).
    if not value_purity_passed:
        reasons.append(
            "Custom test values were used — this run is an Exploratory Run and is not eligible for certification"
        )
    if mandatory_deselected:
        reasons.append("Mandatory steps were deselected from the plan")
    if mandatory_failed:
        reasons.append(f"{mandatory_failed} mandatory step(s) failed")
    if mandatory_skipped:
        reasons.append(f"{mandatory_skipped} mandatory step(s) skipped due to earlier failures")
    if not mandatory_total:
        reasons.append("No mandatory steps declared")
    if certification_coverage != "complete":
        reasons.append("Manifest is not marked as complete certification coverage")
    if approved_release_policy is not None and not approved_release_policy.is_tool_version_approved(tool_version):
        reasons.append(f"Tool version is not in the approved-release policy: {tool_version}")
    if approved_release_policy is None:
        reasons.append("Approved-release policy was not supplied")

    block: JsonObject = {
        "eligible": not reasons,
        "valuePurityPassed": value_purity_passed,
        "coveragePassed": coverage_passed,
        **counts,
        "certificationCoverage": coverage_block,
        "approvedRelease": approved_release,
    }
    if reasons:
        block["reason"] = reasons[0]
        block["reasons"] = reasons
    return block


def _compute_value_purity_passed(
    test_value_profile_evidence: Mapping[str, JsonValue] | None,
    *,
    custom_test_values_active: bool,
) -> bool:
    """Determine whether the value-purity certification gate passes.

    The gate passes when all effective test values match the suite baseline
    (i.e. the run is not an Exploratory Run).  When serialised evidence is
    available it is consulted directly; otherwise ``custom_test_values_active``
    is used as the fallback signal.

    Evidence-based logic (new ``testValues`` baseline-delta shape):
        * ``source`` must be ``"baseline"`` or ``"default"``
        * ``baselineDeltaKeys`` must be absent or empty

    Args:
        test_value_profile_evidence: Optional non-secret test-value profile
            evidence block as serialised in the result JSON.  When ``None``
            the fallback flag is used instead.
        custom_test_values_active: Fallback flag used when
            ``test_value_profile_evidence`` is ``None``.  ``True`` means the
            run used custom values and the gate fails.

    Returns:
        ``True`` when the run's effective test values are pure baseline and
        therefore eligible for certification from a value-purity perspective.
    """
    if test_value_profile_evidence is not None:
        source = test_value_profile_evidence.get("source")
        pure_source = source in ("baseline", "default")
        baseline_delta_keys = test_value_profile_evidence.get("baselineDeltaKeys")
        no_delta_keys = not baseline_delta_keys
        return bool(pure_source and no_delta_keys)
    return not custom_test_values_active


def _compute_coverage_passed(
    *,
    certification_coverage: CertificationCoverage,
    mandatory_total: int,
    mandatory_failed: int,
    mandatory_skipped: int,
    mandatory_deselected: int,
) -> bool:
    """Determine whether the coverage certification gate passes.

    The gate passes when the manifest declares complete certification
    coverage and all mandatory steps were present, selected, and passed
    (or warned — warn is non-blocking per the PRD).

    Args:
        certification_coverage: Manifest-level certification coverage
            declaration.  Must be ``"complete"`` for the gate to pass.
        mandatory_total: Total mandatory step count including deselected steps.
        mandatory_failed: Number of mandatory steps that failed.
        mandatory_skipped: Number of mandatory steps that were skipped.
        mandatory_deselected: Number of mandatory steps deselected from the
            plan.

    Returns:
        ``True`` when the coverage gate passes: the manifest is complete,
        at least one mandatory step ran, and no mandatory steps failed,
        were skipped, or were deselected.
    """
    return (
        certification_coverage == "complete"
        and mandatory_total > 0
        and mandatory_failed == 0
        and mandatory_skipped == 0
        and mandatory_deselected == 0
    )


def _build_approved_release_eligibility(
    *,
    approved_release_policy: ApprovedReleasePolicy | None,
    tool_version: str,
) -> JsonObject:
    """Build the approved-release sub-block for certification eligibility.

    Args:
        approved_release_policy: Optional approved-release policy parsed from
            participant configuration.
        tool_version: Tool version resolved for the top-level report metadata.

    Returns:
        JSON object describing whether the approved-release criterion was
        checked and whether the tool version is approved.
    """
    approved_release: JsonObject = {
        "checked": approved_release_policy is not None,
        "approved": False,
        "toolVersion": tool_version,
    }
    if approved_release_policy is not None:
        approved_release["approved"] = approved_release_policy.is_tool_version_approved(tool_version)
        approved_release["policySchemaVersion"] = approved_release_policy.schema_version
    return approved_release
