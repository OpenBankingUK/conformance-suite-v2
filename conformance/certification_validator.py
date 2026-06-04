"""OBL-side certification report validation domain logic."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

from conformance import approved_releases
from conformance.approved_releases import ApprovedReleasePolicy as ApprovedReleasePolicy
from conformance.approved_releases import ApprovedReleasePolicyError
from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import Manifest, ManifestError, load_manifest
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
]
"""Machine-readable blocking reasons emitted by validation results."""

_REASON_LABELS: Mapping[CertificationValidationReason, str] = MappingProxyType(
    {
        "tool_version_not_approved": "Tool version is not in the approved-release policy",
        "mandatory_step_missing": "Mandatory step is missing from the submitted report",
        "mandatory_step_failed": "Mandatory step failed in the submitted report",
        "mandatory_step_skipped": "Mandatory step was skipped in the submitted report",
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
    """

    report_version: str
    tool_version: str
    steps: tuple[ReportStep, ...]


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
    """

    valid: bool
    report_version: str
    tool_version: str
    tool_version_approved: bool
    policy_schema_version: str
    mandatory_steps: tuple[MandatoryStepValidation, ...]
    reasons: tuple[CertificationValidationReason, ...]

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
        Structured validation result. The result is invalid when any mandatory
        step is missing, failed, skipped, or when the tool version is not
        approved.

    Raises:
        CertificationValidationError: If the manifest does not declare any
            mandatory certification criteria.
    """
    mandatory_step_ids = mandatory_step_ids_from_manifest(manifest)
    if not mandatory_step_ids:
        raise CertificationValidationError("manifest does not declare any mandatory certification steps")

    report_steps = _report_steps_by_id(report.steps)
    mandatory_step_results = tuple(
        _validate_mandatory_step(step_id=step_id, report_steps=report_steps) for step_id in mandatory_step_ids
    )
    tool_version_approved = policy.is_tool_version_approved(report.tool_version)

    reasons = _validation_reasons(
        mandatory_steps=mandatory_step_results,
        tool_version_approved=tool_version_approved,
    )
    return CertificationValidationResult(
        valid=not reasons,
        report_version=report.report_version,
        tool_version=report.tool_version,
        tool_version_approved=tool_version_approved,
        policy_schema_version=policy.schema_version,
        mandatory_steps=mandatory_step_results,
        reasons=reasons,
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
) -> tuple[CertificationValidationReason, ...]:
    """Build unique machine-readable reasons for a validation result.

    Args:
        mandatory_steps: Per-step mandatory validation outcomes.
        tool_version_approved: Whether the submitted tool version is approved.

    Returns:
        Ordered unique blocking reasons.
    """
    reasons: list[CertificationValidationReason] = []
    if not tool_version_approved:
        reasons.append("tool_version_not_approved")
    for step in mandatory_steps:
        if step.reason is not None and step.reason not in reasons:
            reasons.append(step.reason)
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
    if "tool_version_not_approved" in result.reasons:
        lines.append(f"- {_REASON_LABELS['tool_version_not_approved']}: {result.tool_version}")
    for step in result.mandatory_steps:
        if step.reason is not None:
            lines.append(f"- {_REASON_LABELS[step.reason]}: {step.step_id}")
    return lines


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
