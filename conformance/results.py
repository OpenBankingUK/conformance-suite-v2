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
