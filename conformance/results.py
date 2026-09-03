"""Structured result models for conformance smoke-check output."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, cast

from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import CertificationCoverage
from conformance.version import REPORT_METADATA_VERSION, resolve_conformance_tool_version

if TYPE_CHECKING:
    from conformance.approved_releases import ApprovedReleasePolicy
    from conformance.catalogue import CompiledTestPlan
    from conformance.test_plan import TestPlan

CheckStatus = Literal["passed", "failed", "warn", "skipped"]
"""Outcome values emitted by smoke-check steps and summaries.

Matches the four-state PRD outcome model: PASS, FAIL, WARN, SKIPPED.
``warn`` is emitted when an otherwise-passing v1 step declares a
``warning`` message in the manifest (signalling a deprecation or risk
that does not block certification). ``skipped`` is emitted when a v1
step cannot run because a prerequisite step produced no response.
"""


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
    """

    name: str
    status: CheckStatus
    message: str
    url: str | None = None
    status_code: int | None = None
    details: Mapping[str, JsonValue] = field(default_factory=dict)
    mandatory: bool = False

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
        if self.details:
            result["details"] = deepcopy(dict(self.details))
        return result


@dataclass(frozen=True)
class SmokeCheckResult:
    """Complete result for a model-bank smoke-check execution.

    Attributes:
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
        certification_coverage: Whether the manifest used for this run declares
            full certification coverage (``complete``) or is intentionally
            partial / non-certifying (``partial``). Defaults to ``partial`` for
            non-manifest smoke checks and v0 manifest runs, which pre-date the
            certification eligibility model. A ``partial`` value blocks
            ``certificationEligibility.eligible`` even when all mandatory steps
            pass and the tool version is approved.
        compiled_plan: Optional compiled catalogue plan whose traceability
            metadata should be embedded in the generated report.
        non_certifying_reasons: Additional catalogue-plan reasons that block
            certification eligibility even when mandatory executed steps pass.
    """

    status: CheckStatus
    started_at: datetime
    finished_at: datetime
    steps: tuple[StepResult, ...]
    plan_summary: Mapping[str, int] | None = None
    deselected_mandatory_step_ids: tuple[str, ...] = ()
    approved_release_policy: ApprovedReleasePolicy | None = None
    certification_coverage: CertificationCoverage = "partial"
    compiled_plan: CompiledTestPlan | None = None
    non_certifying_reasons: tuple[str, ...] = ()

    def to_json_object(self) -> JsonObject:
        """Convert the smoke-check result into the public JSON report shape.

        Returns:
            JSON object suitable for serialisation into the result file.
        """
        tool_version = resolve_conformance_tool_version()
        body: JsonObject = {
            "metadata": {"reportVersion": REPORT_METADATA_VERSION},
            "tool": {"version": tool_version},
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
                deselected_mandatory_step_ids=self.deselected_mandatory_step_ids,
                approved_release_policy=self.approved_release_policy,
                tool_version=tool_version,
                certification_coverage=self.certification_coverage,
                non_certifying_reasons=self.non_certifying_reasons,
            ),
            "steps": [step.to_json_object() for step in self.steps],
        }
        if self.plan_summary is not None:
            body["plan"] = dict(self.plan_summary)
        if self.compiled_plan is not None:
            body["catalogue"] = _compiled_plan_to_json_object(self.compiled_plan, steps=self.steps)
        return body


def build_smoke_check_result(
    steps: list[StepResult],
    *,
    started_at: datetime,
    plan: TestPlan | None = None,
    approved_release_policy: ApprovedReleasePolicy | None = None,
    certification_coverage: CertificationCoverage = "partial",
    compiled_plan: CompiledTestPlan | None = None,
    non_certifying_reasons: tuple[str, ...] = (),
) -> SmokeCheckResult:
    """Build an aggregate smoke-check result from collected step outcomes.

    Args:
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
        certification_coverage: Whether the manifest declares full certification
            coverage (``complete``) or is intentionally partial / non-certifying
            (``partial``). Defaults to ``"partial"`` so non-manifest callers and
            v0 manifest callers are safe by default. A ``partial`` value blocks
            ``certificationEligibility.eligible`` even when all mandatory steps
            pass and the tool version is approved.
        compiled_plan: Optional compiled catalogue plan whose traceability
            metadata should be embedded in the top-level ``catalogue`` block.
        non_certifying_reasons: Additional catalogue-plan reasons that should
            block certification eligibility.

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
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        steps=tuple(steps),
        plan_summary=plan_summary,
        deselected_mandatory_step_ids=deselected_mandatory,
        approved_release_policy=approved_release_policy,
        certification_coverage=certification_coverage,
        compiled_plan=compiled_plan,
        non_certifying_reasons=non_certifying_reasons,
    )


def mark_development_result_evidence(validation_result: JsonObject, result_object: JsonObject) -> None:
    """Mark development-mode result evidence as non-certifying.

    Args:
        validation_result: Validation result captured before launch.
        result_object: Mutable run result JSON object to annotate.
    """
    if validation_result.get("executionMode") != "development":
        return
    metadata = result_object.get("metadata")
    if isinstance(metadata, dict):
        metadata["executionMode"] = "development"
    else:
        result_object["metadata"] = {"executionMode": "development"}
    eligibility = result_object.get("certificationEligibility")
    if not isinstance(eligibility, dict):
        return
    reason = "Development-mode run is not certification evidence"
    raw_reasons = eligibility.get("reasons")
    reasons = list(raw_reasons) if isinstance(raw_reasons, list) else []
    if reason not in reasons:
        reasons.insert(0, reason)
    raw_issues = validation_result.get("issues")
    if isinstance(raw_issues, list):
        for raw_issue in raw_issues:
            if not isinstance(raw_issue, dict) or raw_issue.get("severity") != "warning":
                continue
            message = raw_issue.get("message")
            if isinstance(message, str) and message not in reasons:
                reasons.append(message)
    eligibility["eligible"] = False
    eligibility["reason"] = reason
    eligibility["reasons"] = reasons


def _build_eligibility(
    steps: tuple[StepResult, ...],
    *,
    deselected_mandatory_step_ids: tuple[str, ...] = (),
    approved_release_policy: ApprovedReleasePolicy | None = None,
    tool_version: str,
    certification_coverage: CertificationCoverage = "partial",
    non_certifying_reasons: tuple[str, ...] = (),
) -> JsonObject:
    """Build the ``certificationEligibility`` block for the result file.

    Implements the PRD's Certification Eligibility Assessment for Phase 1: a
    self-service check that a run is suitable for submission to OBL for
    formal certification. The criteria are driven by which steps were
    declared ``mandatory`` in the manifest — *not* hardcoded — so OBL
    Standards can adjust mandatory coverage by editing configuration.

    Eligibility rules:
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
        non_certifying_reasons: Additional compiled-plan reasons that block
            certification eligibility.

    Returns:
        JSON object containing the boolean ``eligible`` flag, per-status
        mandatory counts, ``mandatoryDeselected`` count and
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
    # 1. Deselected-mandatory — step never ran so cannot demonstrate coverage.
    # 2. Failed / skipped mandatory steps.
    # 3. No mandatory steps declared.
    # 4. Partial manifest coverage — manifest-level certification boundary.
    # 5. Unapproved tool version (policy-level check).
    # 6. Missing approved-release policy (advisory self-assessment).
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
    reasons.extend(non_certifying_reasons)
    if approved_release_policy is not None and not approved_release_policy.is_tool_version_approved(tool_version):
        reasons.append(f"Tool version is not in the approved-release policy: {tool_version}")
    if approved_release_policy is None:
        reasons.append("Approved-release policy was not supplied")

    block: JsonObject = {
        "eligible": not reasons,
        **counts,
        "certificationCoverage": coverage_block,
        "approvedRelease": approved_release,
    }
    if reasons:
        block["reason"] = reasons[0]
        block["reasons"] = reasons
    return block


def _compiled_plan_to_json_object(
    compiled_plan: CompiledTestPlan,
    *,
    steps: tuple[StepResult, ...],
) -> JsonObject:
    """Convert compiled catalogue traceability into report JSON.

    Args:
        compiled_plan: Compiled catalogue plan that drove execution.
        steps: Final execution results used to populate hierarchical statuses.

    Returns:
        JSON object containing catalogue identity, selected endpoints,
        generated test cases, runtime-input trace, applicability decisions,
        and certification planning status.
    """
    traceability = compiled_plan.traceability
    result: JsonObject = {
        "standard": traceability.catalogue_key.standard,
        "version": traceability.catalogue_key.version,
        "api": traceability.catalogue_key.api,
        "catalogueVersion": traceability.catalogue_version,
        "securityProfile": traceability.security_profile,
        "certifying": compiled_plan.certifying,
        "generatedTestCaseIds": list(traceability.generated_test_case_ids),
        "skippedTestCaseIds": [case.test_case_id for case in compiled_plan.skipped_test_cases],
        "selectedEndpoints": [
            {
                "method": endpoint.method,
                "path": endpoint.path,
                "resourceGroup": endpoint.resource_group,
                **({"capabilities": list(endpoint.capability_ids)} if endpoint.capability_ids else {}),
                **({"operationId": endpoint.operation_id} if endpoint.operation_id is not None else {}),
            }
            for endpoint in traceability.selected_endpoints
        ],
        "selectedCapabilities": [
            {
                "method": capability.method,
                "path": capability.path,
                "capabilityId": capability.capability_id,
                "label": capability.label,
                "required": capability.required,
            }
            for capability in traceability.selected_capabilities
        ],
        "applicabilityDecisions": [
            {
                "testCaseId": decision.test_case_id,
                "selected": decision.selected,
                "reason": decision.reason,
                "dependencyOf": list(decision.dependency_of),
            }
            for decision in traceability.applicability_decisions
        ],
        "runtimeInputSnapshot": [
            {
                "inputId": runtime_input.input_id,
                "inputType": runtime_input.input_type,
                "required": runtime_input.required,
                "sensitive": runtime_input.sensitive,
                "provided": runtime_input.provided,
                **({"value": runtime_input.value} if runtime_input.value is not None else {}),
            }
            for runtime_input in traceability.runtime_input_snapshot
        ],
        "nonCertifyingReasons": list(traceability.non_certifying_reasons),
    }
    if traceability.provenance is not None:
        result["provenance"] = {
            "repository": traceability.provenance.repository,
            "release": traceability.provenance.release,
            "commit": traceability.provenance.commit,
            "sourcePaths": list(traceability.provenance.source_paths),
            "references": dict(traceability.provenance.references),
        }
    trace_groups = _compiled_trace_groups_to_json_object(compiled_plan, steps=steps)
    if trace_groups:
        result["traceGroups"] = trace_groups
    return result


def _compiled_trace_groups_to_json_object(
    compiled_plan: CompiledTestPlan,
    *,
    steps: tuple[StepResult, ...],
) -> list[JsonValue]:
    """Serialize selected scenario, case, and execution-step trace hierarchy.

    Args:
        compiled_plan: Compiled plan containing generic catalogue trace groups.
        steps: Final step outcomes keyed by each execution step's stable ID.

    Returns:
        Ordered trace groups with their selected cases and protocol-neutral steps.
    """
    grouped_cases: dict[str, list[JsonValue]] = {}
    group_metadata: dict[str, JsonObject] = {}
    group_order: list[str] = []
    skipped_ids = {case.test_case_id for case in compiled_plan.skipped_test_cases}
    result_by_step_id = {step.name: step for step in steps}
    trace_cases = sorted(
        (*compiled_plan.test_cases, *compiled_plan.skipped_test_cases),
        key=lambda case: case.test_case_id,
    )
    for test_case in trace_cases:
        trace_group = test_case.trace_group
        if trace_group is None:
            continue
        if trace_group.group_id not in grouped_cases:
            grouped_cases[trace_group.group_id] = []
            group_order.append(trace_group.group_id)
            group_metadata[trace_group.group_id] = {
                "traceGroupId": trace_group.group_id,
                "name": trace_group.name,
                "intent": trace_group.intent,
                "sourceSymbol": trace_group.source_symbol,
                "normativeReferenceIds": list(trace_group.normative_reference_ids),
            }
        case_is_unselected = test_case.test_case_id in skipped_ids
        rendered_steps: list[JsonValue] = []
        case_statuses: list[CheckStatus] = []
        for execution_step in test_case.execution_steps:
            step_result = result_by_step_id.get(execution_step.step_id)
            status: CheckStatus = "skipped" if case_is_unselected else step_result.status if step_result else "skipped"
            case_statuses.append(status)
            rendered_steps.append(
                {
                    "stepId": execution_step.step_id,
                    "definitionId": execution_step.definition_id,
                    "name": execution_step.name,
                    "kind": execution_step.kind,
                    "status": status,
                    **(
                        {"expectedStatus": execution_step.expected_status}
                        if execution_step.expected_status is not None
                        else {}
                    ),
                    **(
                        {"actualStatusCode": step_result.status_code}
                        if step_result is not None and step_result.status_code is not None
                        else {}
                    ),
                    **({"message": step_result.message} if step_result is not None else {}),
                    **({"variant": execution_step.variant} if execution_step.variant is not None else {}),
                    **({"deviationId": execution_step.deviation_id} if execution_step.deviation_id is not None else {}),
                    **({"skipReason": "endpoint-not-selected"} if case_is_unselected else {}),
                }
            )
        case_status = _aggregate_trace_status(case_statuses)
        grouped_cases[trace_group.group_id].append(
            {
                "testCaseId": test_case.test_case_id,
                "name": test_case.name,
                "role": test_case.role,
                "mandatory": test_case.mandatory,
                "status": case_status,
                "complianceScope": list(test_case.compliance_scope),
                "expectedHttpStatuses": list(test_case.expected_http_statuses),
                **({"skipReason": "endpoint-not-selected"} if case_is_unselected else {}),
                "steps": rendered_steps,
            }
        )
    rendered_groups: list[JsonValue] = []
    for group_id in group_order:
        test_cases = grouped_cases[group_id]
        statuses = [
            case["status"]
            for case in test_cases
            if isinstance(case, dict) and case.get("status") in {"passed", "failed", "warn", "skipped"}
        ]
        group_status = _aggregate_trace_status(cast("list[CheckStatus]", statuses))
        rendered_groups.append(
            {
                **group_metadata[group_id],
                "status": group_status,
                **({"skipReason": "endpoint-not-selected"} if group_status == "skipped" else {}),
                "testCases": test_cases,
            }
        )
    return rendered_groups


def _aggregate_trace_status(statuses: list[CheckStatus]) -> CheckStatus:
    """Aggregate child outcomes for a catalogue trace case or group.

    Args:
        statuses: Ordered child statuses.

    Returns:
        Failed when any child failed, warn when any child warned without a
        failure, passed when at least one child passed, otherwise skipped.
    """
    if "failed" in statuses:
        return "failed"
    if "warn" in statuses:
        return "warn"
    if "passed" in statuses:
        return "passed"
    return "skipped"


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
