"""Structured result models for conformance smoke-check output."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import CertificationCoverage
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
    )


def _build_eligibility(
    steps: tuple[StepResult, ...],
    *,
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
