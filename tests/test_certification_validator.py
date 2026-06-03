import json
from pathlib import Path

import pytest

from conformance.certification_validator import (
    APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
    ApprovedReleasePolicy,
    CertificationValidationError,
    SubmittedReport,
    parse_approved_release_policy,
    parse_submitted_report,
    render_confluence_summary,
    validate_certification_report,
    validate_report,
)
from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import Manifest, parse_manifest
from conformance.results import CheckStatus


@pytest.mark.unit
def test_validate_report_accepts_pass_and_warn_mandatory_steps() -> None:
    manifest = _manifest_with_steps(mandatory_step_ids=("discovery", "jwks"), optional_step_ids=("optional",))
    report = _report(
        tool_version="1.2.3",
        steps=(
            ("discovery", "passed"),
            ("jwks", "warn"),
            ("optional", "failed"),
        ),
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is True
    assert result.reasons == ()
    assert result.tool_version_approved is True
    rendered = result.to_json_object()
    assert rendered["valid"] is True
    mandatory = rendered["mandatory"]
    assert isinstance(mandatory, dict)
    assert mandatory["total"] == 2
    assert mandatory["passed"] == 1
    assert mandatory["warn"] == 1
    assert render_confluence_summary(result).startswith("Certification report validation: PASS")


@pytest.mark.unit
def test_validate_report_rejects_missing_failed_and_skipped_mandatory_steps() -> None:
    manifest = _manifest_with_steps(mandatory_step_ids=("missing", "failed", "skipped", "passed"))
    report = _report(
        tool_version="1.2.3",
        steps=(
            ("failed", "failed"),
            ("skipped", "skipped"),
            ("passed", "passed"),
        ),
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is False
    assert result.reasons == (
        "mandatory_step_missing",
        "mandatory_step_failed",
        "mandatory_step_skipped",
    )
    rendered = result.to_json_object()
    mandatory = rendered["mandatory"]
    assert isinstance(mandatory, dict)
    assert mandatory["missing"] == 1
    assert mandatory["failed"] == 1
    assert mandatory["skipped"] == 1
    summary = render_confluence_summary(result)
    assert "Certification report validation: FAIL" in summary
    assert "Mandatory step is missing from the submitted report: missing" in summary
    assert "Mandatory step failed in the submitted report: failed" in summary
    assert "Mandatory step was skipped in the submitted report: skipped" in summary


@pytest.mark.unit
def test_validate_report_rejects_unapproved_tool_version() -> None:
    manifest = _manifest_with_steps(mandatory_step_ids=("discovery",))
    report = _report(tool_version="1.2.3", steps=(("discovery", "passed"),))
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("2.0.0",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is False
    assert result.tool_version_approved is False
    assert result.reasons == ("tool_version_not_approved",)
    assert "Tool version is not in the approved-release policy: 1.2.3" in render_confluence_summary(result)


@pytest.mark.unit
def test_validate_report_rejects_manifest_without_mandatory_steps() -> None:
    manifest = _manifest_with_steps(mandatory_step_ids=(), optional_step_ids=("optional",))
    report = _report(tool_version="1.2.3", steps=(("optional", "passed"),))
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    with pytest.raises(CertificationValidationError, match="mandatory certification steps"):
        validate_report(report=report, manifest=manifest, policy=policy)


@pytest.mark.unit
def test_parse_submitted_report_rejects_missing_metadata() -> None:
    with pytest.raises(CertificationValidationError, match="report.metadata is required"):
        parse_submitted_report({"tool": {"version": "1.2.3"}, "steps": []})


@pytest.mark.unit
def test_parse_submitted_report_rejects_invalid_step_status() -> None:
    raw_report: JsonObject = {
        "metadata": {"reportVersion": "1.0"},
        "tool": {"version": "1.2.3"},
        "steps": [{"name": "discovery", "status": "unknown"}],
    }

    with pytest.raises(CertificationValidationError, match=r"report.steps\[0\].status must be one of"):
        parse_submitted_report(raw_report)


@pytest.mark.unit
def test_parse_approved_release_policy_rejects_wrong_schema_version() -> None:
    with pytest.raises(CertificationValidationError, match="schemaVersion"):
        parse_approved_release_policy({"schemaVersion": "v2", "approvedToolVersions": ["1.2.3"]})


@pytest.mark.unit
def test_validate_certification_report_loads_inputs_from_paths(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    manifest_path = tmp_path / "manifest.json"
    policy_path = tmp_path / "policy.json"
    _write_json(
        report_path,
        _report_json(tool_version="1.2.3", steps=(("discovery", "passed"),)),
    )
    _write_json(manifest_path, _manifest_json(mandatory_step_ids=("discovery",), optional_step_ids=()))
    _write_json(
        policy_path,
        {"schemaVersion": APPROVED_RELEASE_POLICY_SCHEMA_VERSION, "approvedToolVersions": ["1.2.3"]},
    )

    result = validate_certification_report(
        report_path,
        manifest_path=manifest_path,
        approved_releases_path=policy_path,
    )

    assert result.valid is True
    assert result.report_version == "1.0"
    assert result.tool_version == "1.2.3"


def _manifest_with_steps(*, mandatory_step_ids: tuple[str, ...], optional_step_ids: tuple[str, ...] = ()) -> Manifest:
    return parse_manifest(_manifest_json(mandatory_step_ids=mandatory_step_ids, optional_step_ids=optional_step_ids))


def _manifest_json(*, mandatory_step_ids: tuple[str, ...], optional_step_ids: tuple[str, ...]) -> JsonObject:
    steps: list[JsonValue] = []
    for step_id in mandatory_step_ids:
        steps.append(_manifest_step(step_id=step_id, mandatory=True, optional=False))
    for step_id in optional_step_ids:
        steps.append(_manifest_step(step_id=step_id, mandatory=False, optional=True))
    return {"schemaVersion": "v1", "name": "validator", "steps": steps}


def _manifest_step(*, step_id: str, mandatory: bool, optional: bool) -> JsonObject:
    request: JsonObject = {"method": "GET", "url": f"https://example.com/{step_id}"}
    assertions: list[JsonValue] = [{"type": "http_status", "expected": 200}]
    step: JsonObject = {
        "id": step_id,
        "name": step_id,
        "request": request,
        "assertions": assertions,
    }
    if mandatory:
        step["mandatory"] = True
    if optional:
        step["optional"] = True
    return step


def _report(*, tool_version: str, steps: tuple[tuple[str, CheckStatus], ...]) -> SubmittedReport:
    return parse_submitted_report(_report_json(tool_version=tool_version, steps=steps))


def _report_json(*, tool_version: str, steps: tuple[tuple[str, CheckStatus], ...]) -> JsonObject:
    rendered_steps: list[JsonValue] = []
    for step_id, status in steps:
        rendered_steps.append({"name": step_id, "status": status, "message": "ok"})
    return {
        "metadata": {"reportVersion": "1.0"},
        "tool": {"version": tool_version},
        "steps": rendered_steps,
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
