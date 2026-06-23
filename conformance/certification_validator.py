"""OBL-side certification report validation domain logic."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

from conformance import approved_releases
from conformance.approved_releases import ApprovedReleasePolicy as ApprovedReleasePolicy
from conformance.approved_releases import ApprovedReleasePolicyError
from conformance.auth_metadata import AuthBundleInventory, AuthStepRequirement
from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import CertificationCoverage, Manifest, ManifestError, TestValueProfileSpec, load_manifest
from conformance.masking import MASKED_VALUE
from conformance.results import CheckStatus

APPROVED_RELEASE_POLICY_SCHEMA_VERSION = approved_releases.APPROVED_RELEASE_POLICY_SCHEMA_VERSION
"""Approved-release policy schema version accepted by the validator."""

VALID_REPORT_STEP_STATUSES = frozenset({"passed", "failed", "warn", "skipped"})
"""Report step status values accepted from submitted report JSON."""

type MandatoryValidationStatus = CheckStatus | Literal["missing"]
"""Status values used when validating mandatory manifest coverage."""

type CertificationValidationReason = Literal[
    "tool_version_not_approved",
    "mandatory_step_missing",
    "mandatory_step_failed",
    "mandatory_step_skipped",
    "auth_metadata_missing",
    "auth_metadata_mismatch",
    "environment_capabilities_missing",
    "environment_capabilities_blocked",
    "test_value_profile_missing",
    "test_value_profile_overridden",
    "test_value_profile_mismatch",
    "manifest_coverage_partial",
]
"""Machine-readable blocking reasons emitted by validation results."""

_REASON_LABELS: Mapping[CertificationValidationReason, str] = MappingProxyType(
    {
        "tool_version_not_approved": "Tool version is not in the approved-release policy",
        "mandatory_step_missing": "Mandatory step is missing from the submitted report",
        "mandatory_step_failed": "Mandatory step failed in the submitted report",
        "mandatory_step_skipped": "Mandatory step was skipped in the submitted report",
        "auth_metadata_missing": "Auth metadata evidence is required for complete coverage manifests",
        "auth_metadata_mismatch": "Auth metadata evidence is inconsistent with the trusted manifest selection",
        "environment_capabilities_missing": (
            "Environment capability evidence is required for this complete-suite submission"
        ),
        "environment_capabilities_blocked": "Environment capability evidence reports blocked support",
        "test_value_profile_missing": "Test-value profile evidence is required for complete coverage manifests",
        "test_value_profile_overridden": (
            "Custom test values were used (effective values differ from suite baseline) — "
            "run is an Exploratory Run and not eligible for certification"
        ),
        "test_value_profile_mismatch": "Test-value profile evidence is inconsistent with the trusted manifest",
        "manifest_coverage_partial": "Manifest is not marked as complete certification coverage",
    }
)
"""Human-readable labels for machine-readable validation reasons."""


class CertificationValidationError(ValueError):
    """Raised when certification validation inputs are malformed."""


@dataclass(frozen=True)
class ReportStep:
    """Single step parsed from a submitted report.

    Attributes:
        step_id: Stable step identifier emitted as ``name`` in the public
            report JSON.
        status: Submitted step outcome.
    """

    step_id: str
    status: CheckStatus


@dataclass(frozen=True)
class SubmittedReport:
    """Submitted conformance report input used by the validator.

    Attributes:
        report_version: Report metadata version from ``metadata.reportVersion``.
        tool_version: FCS tool version from ``tool.version``.
        steps: Parsed step outcomes from the report's ``steps`` array.
        auth_metadata: Optional parsed ``authMetadata`` evidence.
        environment_capability_supports: Optional support decisions extracted
            from ``environmentCapabilities.decisions[*].support``.
        test_value_profile: Optional parsed ``testValueProfile`` evidence.
        suite_catalog_id: Optional suite catalog id from ``suite.catalogId``.
            Presence indicates a config-resolved bundled suite run.
    """

    report_version: str
    tool_version: str
    steps: tuple[ReportStep, ...]
    auth_metadata: SubmittedAuthMetadataEvidence | None = None
    environment_capability_supports: tuple[str, ...] | None = None
    test_value_profile: SubmittedTestValueProfileEvidence | None = None
    suite_catalog_id: str | None = None


@dataclass(frozen=True)
class SubmittedAuthBundleEvidence:
    """Submitted auth bundle evidence entry.

    Attributes:
        bundle_id: Auth bundle identifier from submitted evidence.
        token_step_id: Token-step identifier associated with the bundle.
        consuming_step_ids: Consuming protected-resource step identifiers.
    """

    bundle_id: str
    token_step_id: str
    consuming_step_ids: tuple[str, ...]


@dataclass(frozen=True)
class SubmittedAuthMetadataEvidence:
    """Submitted non-secret auth metadata evidence.

    Attributes:
        bundles: Submitted auth bundle evidence entries.
        selected_step_requirements: Submitted selected step-to-bundle mappings.
    """

    bundles: tuple[SubmittedAuthBundleEvidence, ...]
    selected_step_requirements: tuple[AuthStepRequirement, ...]


@dataclass(frozen=True)
class SubmittedConditionOutcomeEvidence:
    """Submitted per-step conditional test-value outcome evidence.

    Attributes:
        step_id: Manifest step identifier for this condition outcome.
        selected: Whether the step was selected by plan evaluation.
        required_keys: Required test-value keys declared for the condition.
        missing_keys: Required keys missing from the effective profile.
    """

    step_id: str
    selected: bool
    required_keys: tuple[str, ...]
    missing_keys: tuple[str, ...]


@dataclass(frozen=True)
class SubmittedTestValueProfileEvidence:
    """Submitted non-secret test-value profile evidence.

    Supports both the legacy profile-based shape (``source`` is
    ``"default"`` or ``"overridden"``) and the new baseline-delta shape
    (``source`` is ``"baseline"`` or ``"custom"``).

    Attributes:
        source: Evidence source label.  ``"default"``/``"overridden"`` come
            from the legacy ``testValueProfiles`` path; ``"baseline"``/``"custom"``
            come from the new ``testValues`` baseline-delta path.
        profile_id: Effective test-value profile id used for the run.  Empty
            string for new-shape evidence that carries no profile.
        override_keys: Override key names applied by participant config (legacy
            path only).  Empty for new-shape evidence.
        declared_keys: Key names declared by trusted manifest metadata (legacy
            path only).  Empty for new-shape evidence.
        required_keys: Union of required test-value keys for selected plan rows
            (legacy path only).  Empty for new-shape evidence.
        condition_outcomes: Optional per-step conditional selection outcomes
            (legacy path only).  Empty for new-shape evidence.
        effective_values: Optional masked values surfaced for non-secret keys
            (legacy path only).  Empty for new-shape evidence.
        baseline_delta_keys: Sorted tuple of key names whose effective value
            differs from the suite baseline (new ``testValues`` path only).
            Empty for legacy-shape evidence.
    """

    source: str
    profile_id: str = ""
    override_keys: tuple[str, ...] = ()
    declared_keys: tuple[str, ...] = ()
    required_keys: tuple[str, ...] = ()
    condition_outcomes: tuple[SubmittedConditionOutcomeEvidence, ...] = ()
    effective_values: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    baseline_delta_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class MandatoryStepValidation:
    """Validation outcome for one mandatory manifest step.

    Attributes:
        step_id: Mandatory manifest step identifier.
        status: Submitted report status for this step, or ``missing`` when
            the report did not include the mandatory step.
        reason: Machine-readable blocking reason for this step, or ``None``
            when the mandatory step passed validation.
    """

    step_id: str
    status: MandatoryValidationStatus
    reason: CertificationValidationReason | None = None

    @property
    def valid(self) -> bool:
        """Return whether this mandatory step is certification-valid.

        Returns:
            True when the mandatory step has a non-blocking status
            (``passed`` or ``warn``); otherwise False.
        """
        return self.reason is None

    def to_json_object(self) -> JsonObject:
        """Convert the mandatory step validation to JSON-compatible data.

        Returns:
            JSON object suitable for serialising in validation output.
        """
        body: JsonObject = {"stepId": self.step_id, "status": self.status, "valid": self.valid}
        if self.reason is not None:
            body["reason"] = self.reason
        return body


@dataclass(frozen=True)
class CertificationValidationResult:
    """Structured result produced by OBL certification validation.

    Attributes:
        valid: Whether the report satisfies all validator criteria.
        report_version: Submitted report metadata version.
        tool_version: Submitted FCS tool version.
        tool_version_approved: Whether the submitted tool version appears in
            the approved-release policy.
        policy_schema_version: Approved-release policy schema version used.
        mandatory_steps: Per-step validation outcomes for every mandatory
            manifest step.
        reasons: Unique machine-readable blocking reasons for the result.
        manifest_coverage: Manifest-level certification coverage declaration
            sourced from the submitted manifest. A ``partial`` value produces
            the ``manifest_coverage_partial`` blocking reason and is surfaced
            in the JSON output for audit purposes.
    """

    valid: bool
    report_version: str
    tool_version: str
    tool_version_approved: bool
    policy_schema_version: str
    mandatory_steps: tuple[MandatoryStepValidation, ...]
    reasons: tuple[CertificationValidationReason, ...]
    manifest_coverage: CertificationCoverage = "partial"

    def to_json_object(self) -> JsonObject:
        """Convert the validation result into JSON-compatible data.

        Returns:
            JSON object suitable for serialising in CLI or API output.
        """
        mandatory_summary = _mandatory_summary(self.mandatory_steps)
        mandatory_step_objects: list[JsonValue] = []
        for step in self.mandatory_steps:
            mandatory_step_objects.append(step.to_json_object())
        mandatory_summary["steps"] = mandatory_step_objects

        return {
            "valid": self.valid,
            "report": {"reportVersion": self.report_version, "toolVersion": self.tool_version},
            "certificationCoverage": {"value": self.manifest_coverage},
            "approvedRelease": {
                "approved": self.tool_version_approved,
                "policySchemaVersion": self.policy_schema_version,
            },
            "mandatory": mandatory_summary,
            "reasons": list(self.reasons),
        }


def validate_certification_report(
    report_path: Path,
    *,
    manifest_path: Path,
    approved_releases_path: Path,
) -> CertificationValidationResult:
    """Load validator inputs from disk and validate a submitted report.

    Args:
        report_path: Path to the submitted report JSON file.
        manifest_path: Path to the manifest JSON file used for the run.
        approved_releases_path: Path to the approved-release policy JSON file.

    Returns:
        Structured certification validation result.

    Raises:
        CertificationValidationError: If the report, manifest, or policy file
            cannot be loaded or does not have the required shape.
    """
    report = load_submitted_report(report_path)
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as error:
        raise CertificationValidationError(f"Invalid manifest: {error}") from error
    policy = load_approved_release_policy(approved_releases_path)
    return validate_report(report=report, manifest=manifest, policy=policy)


def load_submitted_report(report_path: Path) -> SubmittedReport:
    """Load and parse a submitted report JSON file.

    Args:
        report_path: Path to the submitted report JSON file.

    Returns:
        Parsed submitted report.

    Raises:
        CertificationValidationError: If the file cannot be read, decoded, or
            parsed as a certification report.
    """
    return parse_submitted_report(_load_json_file(report_path, label="report"))


def load_approved_release_policy(policy_path: Path) -> ApprovedReleasePolicy:
    """Load and parse an approved-release policy JSON file.

    Args:
        policy_path: Path to the approved-release policy JSON file.

    Returns:
        Parsed approved-release policy.

    Raises:
        CertificationValidationError: If the file cannot be read, decoded, or
            parsed as an approved-release policy.
    """
    try:
        return approved_releases.load_approved_release_policy(policy_path)
    except ApprovedReleasePolicyError as error:
        raise CertificationValidationError(str(error)) from error


def parse_submitted_report(raw_report: object) -> SubmittedReport:
    """Parse a decoded JSON value as a submitted certification report.

    Args:
        raw_report: Decoded JSON value expected to be the report root object.

    Returns:
        Parsed submitted report with metadata, tool version, and step statuses.

    Raises:
        CertificationValidationError: If the report root or required fields
            are missing or malformed.
    """
    report = _as_object(raw_report, location="report")
    metadata = _required_object(report, "metadata", location="report")
    tool = _required_object(report, "tool", location="report")
    raw_steps = _required_array(report, "steps", location="report")

    return SubmittedReport(
        report_version=_required_non_empty_string(metadata, "reportVersion", location="report.metadata"),
        tool_version=_required_non_empty_string(tool, "version", location="report.tool"),
        steps=_parse_report_steps(raw_steps),
        auth_metadata=_parse_optional_auth_metadata(report, location="report"),
        environment_capability_supports=_parse_optional_environment_capabilities(report, location="report"),
        test_value_profile=_parse_optional_test_value_profile(report, location="report"),
        suite_catalog_id=_parse_optional_suite_catalog_id(report, location="report"),
    )


def parse_approved_release_policy(raw_policy: object) -> ApprovedReleasePolicy:
    """Parse a decoded JSON value as an approved-release policy.

    Args:
        raw_policy: Decoded JSON value expected to be the policy root object.

    Returns:
        Parsed approved-release policy.

    Raises:
        CertificationValidationError: If the policy root or required fields
            are missing or malformed.
    """
    try:
        return approved_releases.parse_approved_release_policy(raw_policy)
    except ApprovedReleasePolicyError as error:
        raise CertificationValidationError(str(error)) from error


def mandatory_step_ids_from_manifest(manifest: Manifest) -> tuple[str, ...]:
    """Return mandatory step ids declared by a parsed manifest.

    Args:
        manifest: Parsed conformance manifest.

    Returns:
        Ordered mandatory step ids derived from v1 manifest configuration.
        v0 manifests return an empty tuple because they do not declare
        mandatory criteria.
    """
    if manifest.schema_version != "v1":
        return ()
    return tuple(step.id for step in manifest.steps if step.mandatory)


def validate_report(
    *,
    report: SubmittedReport,
    manifest: Manifest,
    policy: ApprovedReleasePolicy,
) -> CertificationValidationResult:
    """Validate a parsed submitted report against manifest and release policy.

    Args:
        report: Parsed submitted report.
        manifest: Parsed manifest used for the original run.
        policy: Approved-release policy to check the submitted tool version.

    Returns:
        Structured validation result. The result is invalid when the manifest
        has partial certification coverage, any mandatory step is missing,
        failed or skipped, or the tool version is not approved.

    Raises:
        CertificationValidationError: If the manifest does not declare any
            mandatory certification criteria.
    """
    mandatory_step_ids = mandatory_step_ids_from_manifest(manifest)
    if not mandatory_step_ids:
        raise CertificationValidationError("manifest does not declare any mandatory certification steps")

    manifest_coverage = manifest.certification_coverage
    report_steps = _report_steps_by_id(report.steps)
    mandatory_step_results = tuple(
        _validate_mandatory_step(step_id=step_id, report_steps=report_steps) for step_id in mandatory_step_ids
    )
    tool_version_approved = policy.is_tool_version_approved(report.tool_version)
    auth_evidence_reasons = _validate_complete_manifest_auth_evidence(
        report=report,
        manifest=manifest,
        report_steps=report_steps,
    )
    test_value_profile_reasons = _validate_complete_manifest_test_value_profile_evidence(
        report=report,
        manifest=manifest,
        report_step_ids=frozenset(report_steps.keys()),
    )
    capability_evidence_reasons = _validate_environment_capability_evidence(report=report, manifest=manifest)

    reasons = _validation_reasons(
        mandatory_steps=mandatory_step_results,
        tool_version_approved=tool_version_approved,
        auth_evidence_reasons=(*auth_evidence_reasons, *test_value_profile_reasons),
        capability_evidence_reasons=capability_evidence_reasons,
        manifest_coverage_partial=(manifest_coverage != "complete"),
    )
    return CertificationValidationResult(
        valid=not reasons,
        report_version=report.report_version,
        tool_version=report.tool_version,
        tool_version_approved=tool_version_approved,
        policy_schema_version=policy.schema_version,
        mandatory_steps=mandatory_step_results,
        reasons=reasons,
        manifest_coverage=manifest_coverage,
    )


def render_confluence_summary(result: CertificationValidationResult) -> str:
    """Render a concise Confluence-ready validation summary.

    Args:
        result: Structured certification validation result to summarise.

    Returns:
        Plain text summary suitable for pasting into Confluence.
    """
    status = "PASS" if result.valid else "FAIL"
    approval = "approved" if result.tool_version_approved else "not approved"
    counts = _count_mandatory_steps(result.mandatory_steps)
    lines = [
        f"Certification report validation: {status}",
        "",
        f"Tool version: {result.tool_version} ({approval})",
        f"Report metadata version: {result.report_version}",
        f"Approved-release policy: {result.policy_schema_version}",
        f"Certification coverage: {result.manifest_coverage}",
        (
            "Mandatory steps: "
            f"{counts['total']} total, {counts['passed']} passed, {counts['warn']} warn, "
            f"{counts['failed']} failed, {counts['skipped']} skipped, {counts['missing']} missing"
        ),
    ]
    if result.reasons:
        lines.extend(["", "Blocking reasons:"])
        lines.extend(_blocking_reason_lines(result))
    return "\n".join(lines)


def _load_json_file(path: Path, *, label: str) -> object:
    """Load a JSON file from disk.

    Args:
        path: Path to the JSON file.
        label: Human-readable input label for error messages.

    Returns:
        Decoded JSON value.

    Raises:
        CertificationValidationError: If the file cannot be read or decoded.
    """
    resolved_path = path.resolve()
    try:
        return json.loads(resolved_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CertificationValidationError(f"Invalid JSON {label}: {error.msg}") from error
    except OSError as error:
        raise CertificationValidationError(f"Unable to read {label} file: {error}") from error


def _parse_report_steps(raw_steps: list[object]) -> tuple[ReportStep, ...]:
    """Parse submitted report step entries.

    Args:
        raw_steps: Raw decoded ``report.steps`` array.

    Returns:
        Parsed step entries in report order.

    Raises:
        CertificationValidationError: If any step entry is malformed or if
            duplicate step identifiers are present.
    """
    steps: list[ReportStep] = []
    seen_step_ids: set[str] = set()
    for index, raw_step in enumerate(raw_steps):
        location = f"report.steps[{index}]"
        step = _as_object(raw_step, location=location)
        step_id = _required_non_empty_string(step, "name", location=location)
        if step_id in seen_step_ids:
            raise CertificationValidationError(f"{location}.name {step_id!r} is duplicated")
        seen_step_ids.add(step_id)
        steps.append(
            ReportStep(
                step_id=step_id,
                status=_required_report_step_status(step, "status", location=location),
            )
        )
    return tuple(steps)


def _validate_mandatory_step(
    *,
    step_id: str,
    report_steps: Mapping[str, CheckStatus],
) -> MandatoryStepValidation:
    """Validate one mandatory manifest step against submitted report steps.

    Args:
        step_id: Mandatory manifest step identifier.
        report_steps: Mapping from submitted report step id to status.

    Returns:
        Per-step mandatory validation outcome.
    """
    status = report_steps.get(step_id)
    if status is None:
        return MandatoryStepValidation(step_id=step_id, status="missing", reason="mandatory_step_missing")
    if status == "failed":
        return MandatoryStepValidation(step_id=step_id, status=status, reason="mandatory_step_failed")
    if status == "skipped":
        return MandatoryStepValidation(step_id=step_id, status=status, reason="mandatory_step_skipped")
    return MandatoryStepValidation(step_id=step_id, status=status)


def _validation_reasons(
    *,
    mandatory_steps: tuple[MandatoryStepValidation, ...],
    tool_version_approved: bool,
    auth_evidence_reasons: tuple[CertificationValidationReason, ...],
    capability_evidence_reasons: tuple[CertificationValidationReason, ...],
    manifest_coverage_partial: bool,
) -> tuple[CertificationValidationReason, ...]:
    """Build unique machine-readable reasons for a validation result.

    Args:
        mandatory_steps: Per-step mandatory validation outcomes.
        tool_version_approved: Whether the submitted tool version is approved.
        auth_evidence_reasons: Auth evidence blockers for complete-coverage
            manifest submissions.
        capability_evidence_reasons: Environment capability evidence blockers
            for complete-coverage submissions.
        manifest_coverage_partial: Whether the manifest declares partial
            (non-complete) certification coverage. When ``True``, the
            ``manifest_coverage_partial`` reason is appended after step-level
            blockers so that more actionable reasons occupy the primary slot
            when multiple blockers are present.

    Returns:
        Ordered unique blocking reasons.
    """
    reasons: list[CertificationValidationReason] = []
    # Coverage is appended last so that more actionable step-level reasons
    # (tool version, missing/failed/skipped mandatory steps) take the primary
    # slot in the tuple when multiple blockers are present. When all step-level
    # checks pass but coverage is partial, this will be the sole and therefore
    # primary reason.
    if not tool_version_approved:
        reasons.append("tool_version_not_approved")
    for step in mandatory_steps:
        if step.reason is not None and step.reason not in reasons:
            reasons.append(step.reason)
    for reason in (*auth_evidence_reasons, *capability_evidence_reasons):
        if reason not in reasons:
            reasons.append(reason)
    if manifest_coverage_partial:
        reasons.append("manifest_coverage_partial")
    return tuple(reasons)


def _report_steps_by_id(steps: tuple[ReportStep, ...]) -> dict[str, CheckStatus]:
    """Index submitted report steps by step id.

    Args:
        steps: Parsed submitted report steps.

    Returns:
        Mapping from step id to submitted status.
    """
    return {step.step_id: step.status for step in steps}


def _mandatory_summary(mandatory_steps: tuple[MandatoryStepValidation, ...]) -> JsonObject:
    """Build the JSON-compatible mandatory validation summary.

    Args:
        mandatory_steps: Per-step mandatory validation outcomes.

    Returns:
        JSON object containing mandatory status counts.
    """
    counts = _count_mandatory_steps(mandatory_steps)
    return {
        "total": counts["total"],
        "passed": counts["passed"],
        "warn": counts["warn"],
        "failed": counts["failed"],
        "skipped": counts["skipped"],
        "missing": counts["missing"],
    }


def _count_mandatory_steps(mandatory_steps: tuple[MandatoryStepValidation, ...]) -> dict[str, int]:
    """Count mandatory validation outcomes by status.

    Args:
        mandatory_steps: Per-step mandatory validation outcomes.

    Returns:
        Dict with total, passed, warn, failed, skipped, and missing counts.
    """
    return {
        "total": len(mandatory_steps),
        "passed": sum(1 for step in mandatory_steps if step.status == "passed"),
        "warn": sum(1 for step in mandatory_steps if step.status == "warn"),
        "failed": sum(1 for step in mandatory_steps if step.status == "failed"),
        "skipped": sum(1 for step in mandatory_steps if step.status == "skipped"),
        "missing": sum(1 for step in mandatory_steps if step.status == "missing"),
    }


def _blocking_reason_lines(result: CertificationValidationResult) -> list[str]:
    """Render blocking reasons for the Confluence summary.

    Args:
        result: Structured certification validation result.

    Returns:
        Human-readable bullet lines for every blocking reason.
    """
    lines: list[str] = []
    for reason in result.reasons:
        if reason == "tool_version_not_approved":
            lines.append(f"- {_REASON_LABELS[reason]}: {result.tool_version}")
        elif reason == "manifest_coverage_partial" or reason in {
            "auth_metadata_missing",
            "auth_metadata_mismatch",
            "environment_capabilities_missing",
            "environment_capabilities_blocked",
            "test_value_profile_missing",
            "test_value_profile_overridden",
            "test_value_profile_mismatch",
        }:
            lines.append(f"- {_REASON_LABELS[reason]}")
        else:
            for step in result.mandatory_steps:
                if step.reason == reason:
                    lines.append(f"- {_REASON_LABELS[reason]}: {step.step_id}")
    return lines


def _validate_complete_manifest_auth_evidence(
    *,
    report: SubmittedReport,
    manifest: Manifest,
    report_steps: Mapping[str, CheckStatus],
) -> tuple[CertificationValidationReason, ...]:
    """Validate submitted auth evidence for complete-coverage manifests.

    Args:
        report: Parsed submitted report.
        manifest: Trusted manifest used for the certified run.
        report_steps: Mapping of submitted report step ids to statuses.

    Returns:
        Tuple containing zero or more auth evidence blocking reasons.
    """
    if manifest.certification_coverage != "complete" or manifest.auth_inventory is None:
        return ()
    if report.auth_metadata is None:
        return ("auth_metadata_missing",)
    expected_inventory = _select_manifest_auth_inventory_for_report(
        manifest.auth_inventory,
        report_step_ids=frozenset(report_steps.keys()),
    )
    if not _auth_evidence_matches_expected(report=report.auth_metadata, expected=expected_inventory):
        return ("auth_metadata_mismatch",)
    return ()


def _validate_complete_manifest_test_value_profile_evidence(
    *,
    report: SubmittedReport,
    manifest: Manifest,
    report_step_ids: frozenset[str],
) -> tuple[CertificationValidationReason, ...]:
    """Validate submitted test-value profile evidence for complete manifests.

    Handles both the legacy ``testValueProfiles`` shape and the new
    ``testValues`` baseline-delta shape.  The value-purity gate blocks
    certification when ``source`` is ``"custom"`` or ``"overridden"``, or
    when :attr:`~SubmittedTestValueProfileEvidence.baseline_delta_keys` is
    non-empty (new shape).  The legacy ``"default"`` and new ``"baseline"``
    source values pass the gate.

    Args:
        report: Parsed submitted report.
        manifest: Trusted manifest used for the certified run.
        report_step_ids: Step ids present in the submitted report.

    Returns:
        Tuple containing zero or more test-value profile blocking reasons.
    """
    has_test_values = manifest.test_values is not None
    profile_spec = manifest.test_value_profiles
    if manifest.certification_coverage != "complete" or (not has_test_values and profile_spec is None):
        return ()
    if report.test_value_profile is None:
        return ("test_value_profile_missing",)

    evidence = report.test_value_profile

    # Value-purity gate: new-shape "custom" or non-empty baseline_delta_keys,
    # or legacy "overridden", all block certification.
    if evidence.source == "custom" or evidence.baseline_delta_keys:
        return ("test_value_profile_overridden",)
    if evidence.source == "overridden":
        return ("test_value_profile_overridden",)

    # For legacy profile-based manifests: validate evidence consistency.
    if profile_spec is not None and not _test_value_profile_evidence_matches_expected(
        evidence=evidence,
        manifest=manifest,
        report_step_ids=report_step_ids,
    ):
        return ("test_value_profile_mismatch",)
    return ()


def _validate_environment_capability_evidence(
    *,
    report: SubmittedReport,
    manifest: Manifest,
) -> tuple[CertificationValidationReason, ...]:
    """Validate submitted environment capability evidence for complete suites.

    Args:
        report: Parsed submitted report.
        manifest: Trusted manifest used for the certified run.

    Returns:
        Tuple containing zero or more capability evidence blocking reasons.
    """
    if manifest.certification_coverage != "complete":
        return ()
    if not _requires_environment_capability_evidence(report=report, manifest=manifest):
        return ()
    if not report.environment_capability_supports:
        return ("environment_capabilities_missing",)
    if any(support == "blocked" for support in report.environment_capability_supports):
        return ("environment_capabilities_blocked",)
    return ()


def _requires_environment_capability_evidence(*, report: SubmittedReport, manifest: Manifest) -> bool:
    """Decide whether environment capability evidence is mandatory.

    Args:
        report: Parsed submitted report.
        manifest: Trusted manifest used for certification validation.

    Returns:
        True when suite/catalog metadata indicates bundled-suite validation or
        when trusted manifest auth metadata declares any auth bundles.
    """
    if report.suite_catalog_id is not None:
        return True
    if manifest.auth_inventory is None:
        return False
    return bool(manifest.auth_inventory.bundles)


def _test_value_profile_evidence_matches_expected(
    *,
    evidence: SubmittedTestValueProfileEvidence,
    manifest: Manifest,
    report_step_ids: frozenset[str],
) -> bool:
    """Return whether submitted test-value profile evidence matches manifest truth.

    Args:
        evidence: Submitted parsed test-value profile evidence.
        manifest: Trusted manifest used for validation.
        report_step_ids: Step ids present in the submitted report.

    Returns:
        True when evidence fields are internally consistent and match trusted
        manifest metadata for declared keys, selected-step required keys, and
        conditional outcomes.
    """
    profile_spec = manifest.test_value_profiles
    if profile_spec is None:
        return False
    known_profile_ids = {profile.id for profile in profile_spec.profiles}
    if evidence.profile_id not in known_profile_ids:
        return False
    if not set(evidence.override_keys).issubset(profile_spec.allowed_override_keys):
        return False
    expected_source = (
        "default"
        if evidence.profile_id == profile_spec.default_profile_id and not evidence.override_keys
        else "overridden"
    )
    if evidence.source != expected_source:
        return False
    if set(evidence.declared_keys) != _expected_declared_test_value_keys(profile_spec):
        return False
    if set(evidence.required_keys) != _expected_required_test_value_keys(manifest, report_step_ids=report_step_ids):
        return False
    if not _condition_outcomes_match_manifest(
        evidence.condition_outcomes,
        manifest=manifest,
        report_step_ids=report_step_ids,
    ):
        return False
    non_secret_keys = profile_spec.non_secret_keys
    if set(evidence.effective_values).difference(non_secret_keys):
        return False
    return all(value == MASKED_VALUE for value in evidence.effective_values.values())


def _expected_declared_test_value_keys(profile_spec: TestValueProfileSpec) -> set[str]:
    """Compute declared test-value keys from trusted profile metadata.

    Args:
        profile_spec: Trusted manifest profile metadata object.

    Returns:
        Set of declared keys including profile literals, generated keys, and
        allow-listed override keys.
    """
    keys = set(profile_spec.allowed_override_keys)
    for profile in profile_spec.profiles:
        keys.update(profile.values)
        keys.update(profile.generated_keys)
    return keys


def _expected_required_test_value_keys(manifest: Manifest, *, report_step_ids: frozenset[str]) -> set[str]:
    """Compute expected required test-value keys for selected report steps.

    Args:
        manifest: Trusted manifest used for validation.
        report_step_ids: Step ids present in the submitted report.

    Returns:
        Set of required keys from selected conditional steps.
    """
    required_keys: set[str] = set()
    for step in manifest.steps:
        if step.id not in report_step_ids or step.selection_metadata is None:
            continue
        required_keys.update(step.selection_metadata.required_test_value_keys)
    return required_keys


def _condition_outcomes_match_manifest(
    outcomes: tuple[SubmittedConditionOutcomeEvidence, ...],
    *,
    manifest: Manifest,
    report_step_ids: frozenset[str],
) -> bool:
    """Return whether submitted condition outcomes match manifest declarations.

    Args:
        outcomes: Submitted per-step condition outcomes.
        manifest: Trusted manifest used for validation.
        report_step_ids: Step ids present in the submitted report.

    Returns:
        True when every selected conditional report step has a matching outcome
        and each outcome matches trusted required-key metadata.
    """
    conditional_steps = {
        step.id: step.selection_metadata
        for step in manifest.steps
        if step.selection_metadata is not None and step.selection_metadata.conditional
    }
    outcome_by_step = {outcome.step_id: outcome for outcome in outcomes}
    if not set(outcome_by_step).issubset(conditional_steps):
        return False
    for step_id, outcome in outcome_by_step.items():
        metadata = conditional_steps[step_id]
        assert metadata is not None  # noqa: S101 - filtered to non-None above
        if set(outcome.required_keys) != set(metadata.required_test_value_keys):
            return False
        if outcome.selected and step_id not in report_step_ids:
            return False
        if outcome.selected and outcome.missing_keys:
            return False
    for step_id in conditional_steps:
        if step_id not in report_step_ids:
            continue
        if step_id not in outcome_by_step:
            return False
        if not outcome_by_step[step_id].selected:
            return False
        if outcome_by_step[step_id].missing_keys:
            return False
    return True


def _select_manifest_auth_inventory_for_report(
    inventory: AuthBundleInventory,
    *,
    report_step_ids: frozenset[str],
) -> AuthBundleInventory:
    """Filter trusted manifest auth inventory to submitted report step coverage.

    Args:
        inventory: Trusted manifest auth inventory.
        report_step_ids: Step ids present in the submitted report.

    Returns:
        Filtered inventory containing selected step requirements and directly
        related bundles.
    """
    if not report_step_ids:
        return inventory
    selected_requirements = tuple(req for req in inventory.step_requirements if req.step_id in report_step_ids)
    selected_bundle_ids = {req.bundle_id for req in selected_requirements}
    selected_bundles = tuple(
        bundle
        for bundle in inventory.bundles
        if (
            bundle.id in selected_bundle_ids
            or bundle.token_step_id in report_step_ids
            or (bundle.consent_step_id is not None and bundle.consent_step_id in report_step_ids)
            or (bundle.psu_step_id is not None and bundle.psu_step_id in report_step_ids)
            or any(step_id in report_step_ids for step_id in bundle.consuming_step_ids)
        )
    )
    return AuthBundleInventory(bundles=selected_bundles, step_requirements=selected_requirements)


def _auth_evidence_matches_expected(
    *,
    report: SubmittedAuthMetadataEvidence,
    expected: AuthBundleInventory,
) -> bool:
    """Return whether submitted auth evidence matches trusted manifest evidence.

    Args:
        report: Submitted auth metadata evidence block.
        expected: Trusted manifest inventory filtered to report coverage.

    Returns:
        True when selected step-to-bundle mappings and bundle metadata align.
    """
    expected_requirements = {req.step_id: req.bundle_id for req in expected.step_requirements}
    report_requirements = {req.step_id: req.bundle_id for req in report.selected_step_requirements}
    if report_requirements != expected_requirements:
        return False
    expected_bundles = {
        bundle.id: (
            bundle.token_step_id,
            frozenset(bundle.consuming_step_ids),
        )
        for bundle in expected.bundles
    }
    report_bundles = {
        bundle.bundle_id: (
            bundle.token_step_id,
            frozenset(bundle.consuming_step_ids),
        )
        for bundle in report.bundles
    }
    return report_bundles == expected_bundles


def _as_object(value: object, *, location: str) -> dict[str, object]:
    """Return ``value`` as a JSON object.

    Args:
        value: Decoded JSON value to validate.
        location: Dot-path location string used in error messages.

    Returns:
        JSON object with string keys.

    Raises:
        CertificationValidationError: If the value is not a JSON object or
            any key is not a string.
    """
    if not isinstance(value, dict):
        raise CertificationValidationError(f"{location} must be a JSON object")
    if any(not isinstance(key, str) for key in value):
        raise CertificationValidationError(f"{location} keys must be strings")
    return cast(dict[str, object], value)


def _required_object(parent: Mapping[str, object], key: str, *, location: str) -> dict[str, object]:
    """Extract a required JSON object field.

    Args:
        parent: Parent JSON object.
        key: Field name to extract.
        location: Dot-path location string used in error messages.

    Returns:
        Required child JSON object.

    Raises:
        CertificationValidationError: If the field is missing or not a JSON
            object.
    """
    if key not in parent:
        raise CertificationValidationError(f"{location}.{key} is required")
    return _as_object(parent[key], location=f"{location}.{key}")


def _optional_object(parent: Mapping[str, object], key: str, *, location: str) -> dict[str, object] | None:
    """Extract an optional JSON object field.

    Args:
        parent: Parent JSON object.
        key: Field name to extract.
        location: Dot-path location string used in error messages.

    Returns:
        Child JSON object, or ``None`` when the key is absent.

    Raises:
        CertificationValidationError: If the field is present but is not a
            JSON object.
    """
    if key not in parent:
        return None
    return _as_object(parent[key], location=f"{location}.{key}")


def _required_array(parent: Mapping[str, object], key: str, *, location: str) -> list[object]:
    """Extract a required JSON array field.

    Args:
        parent: Parent JSON object.
        key: Field name to extract.
        location: Dot-path location string used in error messages.

    Returns:
        Required child JSON array.

    Raises:
        CertificationValidationError: If the field is missing or not a JSON
            array.
    """
    if key not in parent:
        raise CertificationValidationError(f"{location}.{key} is required")
    value = parent[key]
    if not isinstance(value, list):
        raise CertificationValidationError(f"{location}.{key} must be a JSON array")
    return cast(list[object], value)


def _optional_array(parent: Mapping[str, object], key: str, *, location: str) -> list[object] | None:
    """Extract an optional JSON array field.

    Args:
        parent: Parent JSON object.
        key: Field name to extract.
        location: Dot-path location string used in error messages.

    Returns:
        Child JSON array, or ``None`` when the key is absent.

    Raises:
        CertificationValidationError: If the field is present but is not a JSON
            array.
    """
    if key not in parent:
        return None
    value = parent[key]
    if not isinstance(value, list):
        raise CertificationValidationError(f"{location}.{key} must be a JSON array")
    return cast(list[object], value)


def _required_non_empty_string(parent: Mapping[str, object], key: str, *, location: str) -> str:
    """Extract a required non-empty string field.

    Args:
        parent: Parent JSON object.
        key: Field name to extract.
        location: Dot-path location string used in error messages.

    Returns:
        Stripped non-empty string value.

    Raises:
        CertificationValidationError: If the field is missing, not a string,
            or empty after stripping.
    """
    if key not in parent:
        raise CertificationValidationError(f"{location}.{key} is required")
    value = parent[key]
    if not isinstance(value, str):
        raise CertificationValidationError(f"{location}.{key} must be a string")
    stripped = value.strip()
    if not stripped:
        raise CertificationValidationError(f"{location}.{key} must not be empty")
    return stripped


def _required_report_step_status(parent: Mapping[str, object], key: str, *, location: str) -> CheckStatus:
    """Extract a required report step status field.

    Args:
        parent: Parent JSON object.
        key: Field name to extract.
        location: Dot-path location string used in error messages.

    Returns:
        Parsed check status.

    Raises:
        CertificationValidationError: If the field is missing or is not one
            of the public report step status values.
    """
    value = _required_non_empty_string(parent, key, location=location)
    if value not in VALID_REPORT_STEP_STATUSES:
        allowed_values = ", ".join(sorted(VALID_REPORT_STEP_STATUSES))
        raise CertificationValidationError(f"{location}.{key} must be one of: {allowed_values}")
    return cast(CheckStatus, value)


def _required_bool(parent: Mapping[str, object], key: str, *, location: str) -> bool:
    """Extract a required boolean field.

    Args:
        parent: Parent JSON object.
        key: Field name to extract.
        location: Dot-path location string used in error messages.

    Returns:
        Parsed boolean value.

    Raises:
        CertificationValidationError: If the field is missing or not a boolean.
    """
    if key not in parent:
        raise CertificationValidationError(f"{location}.{key} is required")
    value = parent[key]
    if not isinstance(value, bool):
        raise CertificationValidationError(f"{location}.{key} must be a boolean")
    return value


def _parse_required_non_empty_string_array(raw_values: list[object], *, location: str) -> tuple[str, ...]:
    """Parse an array of non-empty strings.

    Args:
        raw_values: Decoded JSON array value.
        location: Dot-path location string used in error messages.

    Returns:
        Tuple of stripped non-empty string values.

    Raises:
        CertificationValidationError: If any item is not a non-empty string or
            duplicates another item.
    """
    values: list[str] = []
    seen_values: set[str] = set()
    for index, raw_value in enumerate(raw_values):
        if not isinstance(raw_value, str):
            raise CertificationValidationError(f"{location}[{index}] must be a string")
        stripped = raw_value.strip()
        if not stripped:
            raise CertificationValidationError(f"{location}[{index}] must not be empty")
        if stripped in seen_values:
            raise CertificationValidationError(f"{location}[{index}] {stripped!r} is duplicated")
        seen_values.add(stripped)
        values.append(stripped)
    return tuple(values)


def _parse_optional_auth_metadata(
    report: Mapping[str, object],
    *,
    location: str,
) -> SubmittedAuthMetadataEvidence | None:
    """Parse optional submitted ``authMetadata`` evidence.

    Args:
        report: Submitted report root object.
        location: Dot-path location string used in error messages.

    Returns:
        Parsed auth metadata evidence, or ``None`` when absent.

    Raises:
        CertificationValidationError: If the evidence block is malformed.
    """
    auth_metadata = _optional_object(report, "authMetadata", location=location)
    if auth_metadata is None:
        return None
    raw_bundles = _required_array(auth_metadata, "bundles", location=f"{location}.authMetadata")
    raw_requirements = _required_array(auth_metadata, "selectedStepRequirements", location=f"{location}.authMetadata")
    bundles: list[SubmittedAuthBundleEvidence] = []
    seen_bundle_ids: set[str] = set()
    for index, raw_bundle in enumerate(raw_bundles):
        bundle = _as_object(raw_bundle, location=f"{location}.authMetadata.bundles[{index}]")
        bundle_id = _required_non_empty_string(bundle, "id", location=f"{location}.authMetadata.bundles[{index}]")
        token_step_id = _required_non_empty_string(
            bundle,
            "tokenStepId",
            location=f"{location}.authMetadata.bundles[{index}]",
        )
        if bundle_id in seen_bundle_ids:
            raise CertificationValidationError(
                f"{location}.authMetadata.bundles[{index}].id {bundle_id!r} is duplicated"
            )
        seen_bundle_ids.add(bundle_id)
        consuming_step_ids = _parse_optional_non_empty_string_array(
            bundle,
            key="consumingStepIds",
            location=f"{location}.authMetadata.bundles[{index}]",
        )
        bundles.append(
            SubmittedAuthBundleEvidence(
                bundle_id=bundle_id,
                token_step_id=token_step_id,
                consuming_step_ids=consuming_step_ids,
            )
        )
    requirements: list[AuthStepRequirement] = []
    seen_requirement_step_ids: set[str] = set()
    for index, raw_requirement in enumerate(raw_requirements):
        requirement = _as_object(raw_requirement, location=f"{location}.authMetadata.selectedStepRequirements[{index}]")
        step_id = _required_non_empty_string(
            requirement,
            "stepId",
            location=f"{location}.authMetadata.selectedStepRequirements[{index}]",
        )
        bundle_id = _required_non_empty_string(
            requirement,
            "bundleId",
            location=f"{location}.authMetadata.selectedStepRequirements[{index}]",
        )
        if step_id in seen_requirement_step_ids:
            raise CertificationValidationError(
                f"{location}.authMetadata.selectedStepRequirements[{index}].stepId {step_id!r} is duplicated"
            )
        seen_requirement_step_ids.add(step_id)
        requirements.append(AuthStepRequirement(step_id=step_id, bundle_id=bundle_id))
    return SubmittedAuthMetadataEvidence(
        bundles=tuple(bundles),
        selected_step_requirements=tuple(requirements),
    )


def _parse_optional_environment_capabilities(
    report: Mapping[str, object],
    *,
    location: str,
) -> tuple[str, ...] | None:
    """Parse optional ``environmentCapabilities`` evidence support outcomes.

    Args:
        report: Submitted report root object.
        location: Dot-path location string used in error messages.

    Returns:
        Tuple of decision support labels, or ``None`` when absent.

    Raises:
        CertificationValidationError: If the evidence block is malformed.
    """
    capability_block = _optional_object(report, "environmentCapabilities", location=location)
    if capability_block is None:
        return None
    decisions = _required_array(capability_block, "decisions", location=f"{location}.environmentCapabilities")
    supports: list[str] = []
    allowed_supports = {"supported", "blocked", "unknown"}
    for index, raw_decision in enumerate(decisions):
        decision = _as_object(raw_decision, location=f"{location}.environmentCapabilities.decisions[{index}]")
        support = _required_non_empty_string(
            decision,
            "support",
            location=f"{location}.environmentCapabilities.decisions[{index}]",
        )
        if support not in allowed_supports:
            allowed = ", ".join(sorted(allowed_supports))
            raise CertificationValidationError(
                f"{location}.environmentCapabilities.decisions[{index}].support must be one of: {allowed}"
            )
        supports.append(support)
    return tuple(supports)


def _parse_optional_test_value_profile(
    report: Mapping[str, object],
    *,
    location: str,
) -> SubmittedTestValueProfileEvidence | None:
    """Parse optional submitted ``testValueProfile`` evidence.

    Accepts both the legacy profile-based shape (``source`` is ``"default"``
    or ``"overridden"``, with ``profileId``, ``overrideKeys``, ``declaredKeys``,
    ``requiredKeys``, ``conditionOutcomes``) and the new baseline-delta shape
    (``source`` is ``"baseline"`` or ``"custom"``, with an optional
    ``baselineDeltaKeys`` array).

    Args:
        report: Submitted report root object.
        location: Dot-path location string used in error messages.

    Returns:
        Parsed test-value profile evidence, or ``None`` when absent.

    Raises:
        CertificationValidationError: If the evidence block is malformed.
    """
    block = _optional_object(report, "testValueProfile", location=location)
    if block is None:
        return None
    source = _required_non_empty_string(block, "source", location=f"{location}.testValueProfile")
    _valid_sources = {"default", "overridden", "baseline", "custom"}
    if source not in _valid_sources:
        allowed = ", ".join(sorted(_valid_sources))
        raise CertificationValidationError(f"{location}.testValueProfile.source must be one of: {allowed}")

    # New baseline-delta shape: source is "baseline" or "custom".
    if source in ("baseline", "custom"):
        raw_delta_keys = _optional_array(block, "baselineDeltaKeys", location=f"{location}.testValueProfile")
        baseline_delta_keys: tuple[str, ...] = ()
        if raw_delta_keys is not None:
            baseline_delta_keys = _parse_required_non_empty_string_array(
                raw_delta_keys,
                location=f"{location}.testValueProfile.baselineDeltaKeys",
            )
        return SubmittedTestValueProfileEvidence(
            source=source,
            baseline_delta_keys=baseline_delta_keys,
        )

    # Legacy profile-based shape: source is "default" or "overridden".
    raw_override_keys = _required_array(block, "overrideKeys", location=f"{location}.testValueProfile")
    raw_declared_keys = _required_array(block, "declaredKeys", location=f"{location}.testValueProfile")
    raw_required_keys = _required_array(block, "requiredKeys", location=f"{location}.testValueProfile")
    raw_condition_outcomes = _required_array(block, "conditionOutcomes", location=f"{location}.testValueProfile")
    effective_values = _optional_object(block, "effectiveValues", location=f"{location}.testValueProfile") or {}

    override_keys = _parse_required_non_empty_string_array(
        raw_override_keys,
        location=f"{location}.testValueProfile.overrideKeys",
    )
    declared_keys = _parse_required_non_empty_string_array(
        raw_declared_keys,
        location=f"{location}.testValueProfile.declaredKeys",
    )
    required_keys = _parse_required_non_empty_string_array(
        raw_required_keys,
        location=f"{location}.testValueProfile.requiredKeys",
    )
    condition_outcomes: list[SubmittedConditionOutcomeEvidence] = []
    seen_condition_steps: set[str] = set()
    for index, raw_condition in enumerate(raw_condition_outcomes):
        condition = _as_object(raw_condition, location=f"{location}.testValueProfile.conditionOutcomes[{index}]")
        step_id = _required_non_empty_string(
            condition,
            "stepId",
            location=f"{location}.testValueProfile.conditionOutcomes[{index}]",
        )
        if step_id in seen_condition_steps:
            raise CertificationValidationError(
                f"{location}.testValueProfile.conditionOutcomes[{index}].stepId {step_id!r} is duplicated"
            )
        seen_condition_steps.add(step_id)
        selected = _required_bool(
            condition,
            "selected",
            location=f"{location}.testValueProfile.conditionOutcomes[{index}]",
        )
        raw_condition_required = _required_array(
            condition,
            "requiredKeys",
            location=f"{location}.testValueProfile.conditionOutcomes[{index}]",
        )
        raw_missing = _required_array(
            condition,
            "missingKeys",
            location=f"{location}.testValueProfile.conditionOutcomes[{index}]",
        )
        condition_outcomes.append(
            SubmittedConditionOutcomeEvidence(
                step_id=step_id,
                selected=selected,
                required_keys=_parse_required_non_empty_string_array(
                    raw_condition_required,
                    location=f"{location}.testValueProfile.conditionOutcomes[{index}].requiredKeys",
                ),
                missing_keys=_parse_required_non_empty_string_array(
                    raw_missing,
                    location=f"{location}.testValueProfile.conditionOutcomes[{index}].missingKeys",
                ),
            )
        )

    parsed_effective_values: dict[str, str] = {}
    for key, value in effective_values.items():
        if not isinstance(value, str):
            raise CertificationValidationError(f"{location}.testValueProfile.effectiveValues.{key} must be a string")
        parsed_effective_values[key] = value

    return SubmittedTestValueProfileEvidence(
        source=source,
        profile_id=_required_non_empty_string(block, "profileId", location=f"{location}.testValueProfile"),
        override_keys=override_keys,
        declared_keys=declared_keys,
        required_keys=required_keys,
        condition_outcomes=tuple(condition_outcomes),
        effective_values=MappingProxyType(parsed_effective_values),
    )


def _parse_optional_suite_catalog_id(report: Mapping[str, object], *, location: str) -> str | None:
    """Parse optional ``suite.catalogId`` evidence for bundled suite runs.

    Args:
        report: Submitted report root object.
        location: Dot-path location string used in error messages.

    Returns:
        Suite catalog id when present, else ``None``.

    Raises:
        CertificationValidationError: If ``suite`` is present but malformed.
    """
    suite = _optional_object(report, "suite", location=location)
    if suite is None:
        return None
    return _required_non_empty_string(suite, "catalogId", location=f"{location}.suite")


def _parse_optional_non_empty_string_array(
    parent: Mapping[str, object],
    *,
    key: str,
    location: str,
) -> tuple[str, ...]:
    """Extract an optional array of non-empty strings.

    Args:
        parent: Parent JSON object.
        key: Array field name.
        location: Dot-path location string used in error messages.

    Returns:
        Tuple of stripped non-empty string values, or an empty tuple when
        absent.

    Raises:
        CertificationValidationError: If the field is present but is not a
            JSON array of non-empty strings.
    """
    raw_values = _optional_array(parent, key, location=location)
    if raw_values is None:
        return ()
    values: list[str] = []
    for index, raw_value in enumerate(raw_values):
        if not isinstance(raw_value, str):
            raise CertificationValidationError(f"{location}.{key}[{index}] must be a string")
        stripped = raw_value.strip()
        if not stripped:
            raise CertificationValidationError(f"{location}.{key}[{index}] must not be empty")
        values.append(stripped)
    return tuple(values)
