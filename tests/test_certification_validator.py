import json
from pathlib import Path

import pytest

from conformance.certification_validator import (
    APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
    ApprovedReleasePolicy,
    CertificationValidationError,
    CertificationValidationReason,
    SubmittedReport,
    parse_approved_release_policy,
    parse_submitted_report,
    render_confluence_summary,
    validate_certification_report,
    validate_report,
)
from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import Manifest, parse_manifest
from conformance.model_bank_config import SuiteSelection
from conformance.results import CheckStatus
from conformance.suite_catalog import resolve_suite


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
def test_parse_approved_release_policy_accepts_schema_version_and_versions() -> None:
    policy = parse_approved_release_policy(
        {
            "schemaVersion": APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
            "approvedToolVersions": [" 1.2.3 ", "2.0.0"],
        }
    )

    assert policy.schema_version == APPROVED_RELEASE_POLICY_SCHEMA_VERSION
    assert policy.approved_tool_versions == ("1.2.3", "2.0.0")


@pytest.mark.unit
def test_example_approved_release_policy_is_parseable() -> None:
    policy_path = Path(__file__).resolve().parents[1] / "config" / "approved-fcs-releases-example.json"
    raw_policy = json.loads(policy_path.read_text(encoding="utf-8"))

    policy = parse_approved_release_policy(raw_policy)

    assert policy.schema_version == APPROVED_RELEASE_POLICY_SCHEMA_VERSION
    assert policy.approved_tool_versions == ("EXAMPLE-REPLACE-WITH-OBL-APPROVED-VERSION",)


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
    return {"schemaVersion": "v1", "name": "validator", "certificationCoverage": "complete", "steps": steps}


def _manifest_json_with_auth_metadata(*, certification_coverage: str = "complete") -> JsonObject:
    """Build a complete v1 manifest fixture with explicit auth metadata evidence contract.

    Args:
        certification_coverage: Coverage declaration to embed at the root.

    Returns:
        Manifest JSON object containing mandatory token/resource steps and one
        auth bundle mapping.
    """
    return {
        "schemaVersion": "v1",
        "name": "validator-auth-metadata",
        "certificationCoverage": certification_coverage,
        "steps": [
            _manifest_step(step_id="token-exchange", mandatory=True, optional=False),
            _manifest_step(step_id="accounts-list", mandatory=True, optional=False),
        ],
        "authMetadata": {
            "bundles": [
                {
                    "id": "ais-primary",
                    "tokenStepId": "token-exchange",
                    "consumingStepIds": ["accounts-list"],
                }
            ],
            "stepRequirements": [{"stepId": "accounts-list", "bundleId": "ais-primary"}],
        },
    }


def _manifest_json_with_test_value_profiles(*, certification_coverage: str = "complete") -> JsonObject:
    """Build a v1 manifest fixture that declares test-value profile metadata.

    Args:
        certification_coverage: Coverage declaration to embed at the root.

    Returns:
        Manifest JSON object with one mandatory and one conditional step.
    """
    return {
        "schemaVersion": "v1",
        "name": "validator-test-values",
        "certificationCoverage": certification_coverage,
        "testValueProfiles": {
            "defaultProfileId": "default-profile",
            "profiles": [
                {
                    "id": "default-profile",
                    "label": "Default profile",
                    "values": {
                        "instructionIdentification": "instr-default",
                        "creditorIdentification": "1234567890",
                    },
                }
            ],
            "allowedOverrideKeys": ["creditorIdentification"],
            "nonSecretKeys": ["instructionIdentification"],
        },
        "steps": [
            _manifest_step(step_id="mandatory-step", mandatory=True, optional=False),
            {
                **_manifest_step(step_id="conditional-step", mandatory=False, optional=False),
                "selectionMetadata": {
                    "conditional": True,
                    "conditionId": "creditor-supported",
                    "requiredTestValueKeys": ["creditorIdentification"],
                },
            },
        ],
    }


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


def _report(
    *,
    tool_version: str,
    steps: tuple[tuple[str, CheckStatus], ...],
    auth_metadata: JsonObject | None = None,
    environment_capabilities: JsonObject | None = None,
    test_value_profile: JsonObject | None = None,
    suite: JsonObject | None = None,
) -> SubmittedReport:
    """Build and parse a submitted-report fixture with optional evidence blocks.

    Args:
        tool_version: Tool version embedded in the fixture report.
        steps: Report step-id/status tuples.
        auth_metadata: Optional ``authMetadata`` evidence block.
        environment_capabilities: Optional ``environmentCapabilities`` block.
        test_value_profile: Optional ``testValueProfile`` evidence block.
        suite: Optional ``suite`` metadata block.

    Returns:
        Parsed submitted report fixture.
    """
    return parse_submitted_report(
        _report_json(
            tool_version=tool_version,
            steps=steps,
            auth_metadata=auth_metadata,
            environment_capabilities=environment_capabilities,
            test_value_profile=test_value_profile,
            suite=suite,
        )
    )


def _report_json(
    *,
    tool_version: str,
    steps: tuple[tuple[str, CheckStatus], ...],
    auth_metadata: JsonObject | None = None,
    environment_capabilities: JsonObject | None = None,
    test_value_profile: JsonObject | None = None,
    suite: JsonObject | None = None,
) -> JsonObject:
    """Build a report JSON fixture with optional auth/capability/suite evidence.

    Args:
        tool_version: Tool version embedded in the fixture report.
        steps: Report step-id/status tuples.
        auth_metadata: Optional ``authMetadata`` evidence block.
        environment_capabilities: Optional ``environmentCapabilities`` block.
        test_value_profile: Optional ``testValueProfile`` evidence block.
        suite: Optional ``suite`` metadata block.

    Returns:
        Report JSON object.
    """
    rendered_steps: list[JsonValue] = []
    for step_id, status in steps:
        rendered_steps.append({"name": step_id, "status": status, "message": "ok"})
    report: JsonObject = {
        "metadata": {"reportVersion": "1.0"},
        "tool": {"version": tool_version},
        "steps": rendered_steps,
    }
    if auth_metadata is not None:
        report["authMetadata"] = auth_metadata
    if environment_capabilities is not None:
        report["environmentCapabilities"] = environment_capabilities
    if test_value_profile is not None:
        report["testValueProfile"] = test_value_profile
    if suite is not None:
        report["suite"] = suite
    return report


def _test_value_profile_evidence_fixture(*, source: str = "default") -> JsonObject:
    """Build a minimal valid ``testValueProfile`` evidence block.

    Args:
        source: Profile source label for the fixture.

    Returns:
        Test-value profile evidence JSON object.
    """
    return {
        "profileId": "default-profile",
        "source": source,
        "overrideKeys": [],
        "declaredKeys": ["creditorIdentification", "instructionIdentification"],
        "requiredKeys": ["creditorIdentification"],
        "conditionOutcomes": [
            {
                "stepId": "conditional-step",
                "selected": True,
                "requiredKeys": ["creditorIdentification"],
                "missingKeys": [],
            }
        ],
        "effectiveValues": {"instructionIdentification": "***"},
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.mark.unit
def test_validate_report_partial_manifest_unaffected_when_auth_evidence_missing() -> None:
    """Partial manifests keep the existing partial-coverage blocker behaviour."""
    manifest = parse_manifest(_manifest_json_with_auth_metadata(certification_coverage="partial"))
    report = _report(
        tool_version="1.2.3",
        steps=(("token-exchange", "passed"), ("accounts-list", "passed")),
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is False
    assert result.reasons == ("manifest_coverage_partial",)


@pytest.mark.unit
def test_validate_report_complete_manifest_with_auth_inventory_rejects_missing_auth_evidence() -> None:
    """Complete manifests with auth metadata must include report auth evidence."""
    manifest = parse_manifest(_manifest_json_with_auth_metadata())
    report = _report(
        tool_version="1.2.3",
        steps=(("token-exchange", "passed"), ("accounts-list", "passed")),
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is False
    assert "auth_metadata_missing" in result.reasons


@pytest.mark.unit
def test_validate_report_complete_manifest_accepts_matching_auth_evidence() -> None:
    """Matching auth bundle evidence validates for complete manifests."""
    manifest = parse_manifest(_manifest_json_with_auth_metadata())
    report = _report(
        tool_version="1.2.3",
        steps=(("token-exchange", "passed"), ("accounts-list", "passed")),
        auth_metadata={
            "bundles": [
                {
                    "id": "ais-primary",
                    "tokenStepId": "token-exchange",
                    "consumingStepIds": ["accounts-list"],
                }
            ],
            "selectedStepRequirements": [{"stepId": "accounts-list", "bundleId": "ais-primary"}],
        },
        environment_capabilities={
            "suiteSelection": {"standard": "ob-read-write"},
            "environment": {"source": "custom", "label": "env"},
            "decisions": [{"support": "supported", "warnings": [], "blockers": []}],
        },
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is True
    assert "auth_metadata_missing" not in result.reasons
    assert "auth_metadata_mismatch" not in result.reasons


@pytest.mark.unit
def test_validate_report_complete_auth_manifest_requires_capability_evidence_without_suite_block() -> None:
    """Trusted auth metadata requires capability evidence even if submitted suite metadata is omitted."""
    manifest = parse_manifest(_manifest_json_with_auth_metadata())
    report = _report(
        tool_version="1.2.3",
        steps=(("token-exchange", "passed"), ("accounts-list", "passed")),
        auth_metadata={
            "bundles": [
                {
                    "id": "ais-primary",
                    "tokenStepId": "token-exchange",
                    "consumingStepIds": ["accounts-list"],
                }
            ],
            "selectedStepRequirements": [{"stepId": "accounts-list", "bundleId": "ais-primary"}],
        },
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is False
    assert "environment_capabilities_missing" in result.reasons


@pytest.mark.unit
def test_validate_report_complete_manifest_with_test_value_profiles_requires_evidence() -> None:
    """Complete manifests with test-value profiles require report profile evidence."""
    manifest = parse_manifest(_manifest_json_with_test_value_profiles())
    report = _report(
        tool_version="1.2.3",
        steps=(("mandatory-step", "passed"), ("conditional-step", "passed")),
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is False
    assert "test_value_profile_missing" in result.reasons


@pytest.mark.unit
def test_validate_report_complete_manifest_rejects_mismatched_test_value_profile_evidence() -> None:
    """Mismatched test-value profile evidence is rejected for complete manifests."""
    manifest = parse_manifest(_manifest_json_with_test_value_profiles())
    report = _report(
        tool_version="1.2.3",
        steps=(("mandatory-step", "passed"), ("conditional-step", "passed")),
        test_value_profile={
            **_test_value_profile_evidence_fixture(),
            "declaredKeys": ["wrong-key"],
        },
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is False
    assert "test_value_profile_mismatch" in result.reasons


@pytest.mark.unit
def test_validate_report_complete_manifest_rejects_overridden_test_value_profile_source() -> None:
    """An overridden test-value source is always blocked for certification runs."""
    manifest = parse_manifest(_manifest_json_with_test_value_profiles())
    report = _report(
        tool_version="1.2.3",
        steps=(("mandatory-step", "passed"), ("conditional-step", "passed")),
        test_value_profile=_test_value_profile_evidence_fixture(source="overridden"),
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is False
    assert "test_value_profile_overridden" in result.reasons
    assert "test_value_profile_mismatch" not in result.reasons


@pytest.mark.unit
def test_validate_report_complete_manifest_rejects_overridden_source_with_override_keys() -> None:
    """Override key usage remains blocked when evidence source is overridden."""
    manifest = parse_manifest(_manifest_json_with_test_value_profiles())
    report = _report(
        tool_version="1.2.3",
        steps=(("mandatory-step", "passed"), ("conditional-step", "passed")),
        test_value_profile={
            **_test_value_profile_evidence_fixture(source="overridden"),
            "overrideKeys": ["creditorIdentification"],
        },
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is False
    assert "test_value_profile_overridden" in result.reasons


@pytest.mark.unit
def test_validate_report_complete_manifest_accepts_matching_test_value_profile_evidence() -> None:
    """Matching test-value profile evidence validates for complete manifests."""
    manifest = parse_manifest(_manifest_json_with_test_value_profiles())
    report = _report(
        tool_version="1.2.3",
        steps=(("mandatory-step", "passed"), ("conditional-step", "passed")),
        test_value_profile=_test_value_profile_evidence_fixture(),
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is True
    assert "test_value_profile_missing" not in result.reasons
    assert "test_value_profile_overridden" not in result.reasons
    assert "test_value_profile_mismatch" not in result.reasons


@pytest.mark.unit
def test_validate_report_complete_manifest_rejects_unmasked_test_value_effective_values() -> None:
    """Submitted effective values must be masked in test-value profile evidence."""
    manifest = parse_manifest(_manifest_json_with_test_value_profiles())
    report = _report(
        tool_version="1.2.3",
        steps=(("mandatory-step", "passed"), ("conditional-step", "passed")),
        test_value_profile={
            **_test_value_profile_evidence_fixture(),
            "effectiveValues": {"instructionIdentification": "instr-default"},
        },
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is False
    assert "test_value_profile_mismatch" in result.reasons


@pytest.mark.unit
def test_certification_validation_reason_accepts_overridden_test_value_reason() -> None:
    """The validation reason union includes the overridden test-value blocker."""
    reason: CertificationValidationReason = "test_value_profile_overridden"

    assert reason == "test_value_profile_overridden"


@pytest.mark.unit
def test_render_confluence_summary_includes_overridden_test_value_reason_label() -> None:
    """Confluence summary shows the overridden-test-values blocking label."""
    manifest = parse_manifest(_manifest_json_with_test_value_profiles())
    report = _report(
        tool_version="1.2.3",
        steps=(("mandatory-step", "passed"), ("conditional-step", "passed")),
        test_value_profile=_test_value_profile_evidence_fixture(source="overridden"),
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)
    summary = render_confluence_summary(result)

    assert (
        "Custom test values were used (effective values differ from suite baseline) — "
        "run is an Exploratory Run and not eligible for certification"
    ) in summary


@pytest.mark.unit
def test_validate_report_partial_manifest_with_test_value_profiles_remains_coverage_blocked() -> None:
    """Partial manifests remain blocked by coverage regardless of profile evidence."""
    manifest = parse_manifest(_manifest_json_with_test_value_profiles(certification_coverage="partial"))
    report = _report(
        tool_version="1.2.3",
        steps=(("mandatory-step", "passed"), ("conditional-step", "passed")),
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is False
    assert result.reasons == ("manifest_coverage_partial",)


@pytest.mark.unit
def test_validate_report_complete_manifest_rejects_mismatched_step_to_bundle_mapping() -> None:
    """A selected step mapped to the wrong bundle id is rejected."""
    manifest = parse_manifest(_manifest_json_with_auth_metadata())
    report = _report(
        tool_version="1.2.3",
        steps=(("token-exchange", "passed"), ("accounts-list", "passed")),
        auth_metadata={
            "bundles": [
                {
                    "id": "ais-primary",
                    "tokenStepId": "token-exchange",
                    "consumingStepIds": ["accounts-list"],
                }
            ],
            "selectedStepRequirements": [{"stepId": "accounts-list", "bundleId": "ais-secondary"}],
        },
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is False
    assert "auth_metadata_mismatch" in result.reasons


@pytest.mark.unit
def test_validate_report_complete_manifest_rejects_blocked_environment_capability_decision() -> None:
    """Blocked environment-capability support in complete-suite evidence is rejected."""
    manifest = _manifest_with_steps(mandatory_step_ids=("discovery",))
    report = _report(
        tool_version="1.2.3",
        steps=(("discovery", "passed"),),
        suite={
            "catalogId": "ob-read-write/v4.0/fapi1-advanced/ais/ais-certification-baseline",
        },
        environment_capabilities={
            "suiteSelection": {"standard": "ob-read-write"},
            "environment": {"source": "custom", "label": "env"},
            "decisions": [{"support": "blocked", "warnings": [], "blockers": ["unsupported"]}],
        },
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is False
    assert "environment_capabilities_blocked" in result.reasons


@pytest.mark.unit
def test_validate_report_complete_manifest_rejects_empty_environment_capability_decisions() -> None:
    """An empty capability decision list is missing evidence, not an unblocked result."""
    manifest = parse_manifest(_manifest_json_with_auth_metadata())
    report = _report(
        tool_version="1.2.3",
        steps=(("token-exchange", "passed"), ("accounts-list", "passed")),
        auth_metadata={
            "bundles": [
                {
                    "id": "ais-primary",
                    "tokenStepId": "token-exchange",
                    "consumingStepIds": ["accounts-list"],
                }
            ],
            "selectedStepRequirements": [{"stepId": "accounts-list", "bundleId": "ais-primary"}],
        },
        environment_capabilities={
            "suiteSelection": {"standard": "ob-read-write"},
            "environment": {"source": "custom", "label": "env"},
            "decisions": [],
        },
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is False
    assert "environment_capabilities_missing" in result.reasons


# ─── Packet B: certification coverage gating ─────────────────────────────────


@pytest.mark.unit
def test_validate_report_rejects_partial_coverage_manifest() -> None:
    """A partial-coverage manifest fails OBL validation even when all mandatory steps pass.

    This is the OBL-side analogue of the participant-side eligibility check:
    a manifest not explicitly marked ``certificationCoverage: complete``
    cannot validate as certification-ready regardless of step outcomes or
    approved-release policy.
    """
    raw_manifest: JsonObject = {
        "schemaVersion": "v1",
        "name": "partial",
        "certificationCoverage": "partial",
        "steps": [_manifest_step(step_id="discovery", mandatory=True, optional=False)],
    }
    manifest = parse_manifest(raw_manifest)
    report = _report(tool_version="1.2.3", steps=(("discovery", "passed"),))
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is False
    assert "manifest_coverage_partial" in result.reasons
    assert result.manifest_coverage == "partial"


@pytest.mark.unit
def test_validate_report_omitted_coverage_defaults_to_partial_and_fails() -> None:
    """A v1 manifest with no ``certificationCoverage`` key defaults to partial and fails.

    Omitting the field is treated identically to an explicit ``partial`` declaration
    so that old or third-party manifests cannot inadvertently become certifiable.
    """
    raw_manifest: JsonObject = {
        "schemaVersion": "v1",
        "name": "no-coverage-key",
        "steps": [_manifest_step(step_id="discovery", mandatory=True, optional=False)],
    }
    manifest = parse_manifest(raw_manifest)
    report = _report(tool_version="1.2.3", steps=(("discovery", "passed"),))
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is False
    assert "manifest_coverage_partial" in result.reasons
    assert result.manifest_coverage == "partial"


@pytest.mark.unit
def test_validate_report_complete_coverage_is_valid_when_steps_pass() -> None:
    """A complete-coverage manifest with all mandatory steps passing validates successfully."""
    manifest = _manifest_with_steps(mandatory_step_ids=("discovery",))
    report = _report(tool_version="1.2.3", steps=(("discovery", "passed"),))
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is True
    assert "manifest_coverage_partial" not in result.reasons
    assert result.manifest_coverage == "complete"


@pytest.mark.unit
def test_validate_report_partial_coverage_reason_in_confluence_summary() -> None:
    """Partial coverage blocker is surfaced in the Confluence summary text."""
    raw_manifest: JsonObject = {
        "schemaVersion": "v1",
        "name": "partial",
        "certificationCoverage": "partial",
        "steps": [_manifest_step(step_id="discovery", mandatory=True, optional=False)],
    }
    manifest = parse_manifest(raw_manifest)
    report = _report(tool_version="1.2.3", steps=(("discovery", "passed"),))
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)
    summary = render_confluence_summary(result)

    assert "Certification report validation: FAIL" in summary
    assert "Certification coverage: partial" in summary
    assert "Manifest is not marked as complete certification coverage" in summary


@pytest.mark.unit
def test_validate_report_confluence_summary_orders_partial_coverage_after_primary_blockers() -> None:
    """Partial coverage is rendered after tool-version and mandatory-step blockers.

    Verifies that _blocking_reason_lines follows the ordering established by
    _validation_reasons so that more actionable blockers appear first in the
    Confluence summary when multiple reasons are present.
    """
    raw_manifest: JsonObject = {
        "schemaVersion": "v1",
        "name": "partial",
        "certificationCoverage": "partial",
        "steps": [
            _manifest_step(step_id="missing", mandatory=True, optional=False),
            _manifest_step(step_id="failed", mandatory=True, optional=False),
        ],
    }
    manifest = parse_manifest(raw_manifest)
    report = _report(tool_version="1.2.3", steps=(("failed", "failed"),))
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("2.0.0",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)
    summary = render_confluence_summary(result)

    assert result.reasons == (
        "tool_version_not_approved",
        "mandatory_step_missing",
        "mandatory_step_failed",
        "manifest_coverage_partial",
    )
    blocking_section = summary.split("Blocking reasons:\n", maxsplit=1)[1]
    blocking_lines = blocking_section.splitlines()
    assert blocking_lines == [
        "- Tool version is not in the approved-release policy: 1.2.3",
        "- Mandatory step is missing from the submitted report: missing",
        "- Mandatory step failed in the submitted report: failed",
        "- Manifest is not marked as complete certification coverage",
    ]


@pytest.mark.unit
def test_validate_report_coverage_included_in_json_output() -> None:
    """The ``certificationCoverage`` audit block is present in the JSON validation result."""
    manifest = _manifest_with_steps(mandatory_step_ids=("discovery",))
    report = _report(tool_version="1.2.3", steps=(("discovery", "passed"),))
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)
    rendered = result.to_json_object()

    coverage_block = rendered["certificationCoverage"]
    assert isinstance(coverage_block, dict)
    assert coverage_block["value"] == "complete"


@pytest.mark.unit
def test_validate_report_bundled_v4_ais_slice_counts_protected_resource_skip() -> None:
    """Bundled AIS slice exposes the protected resource step in validator counts."""
    resolved = resolve_suite(
        SuiteSelection(
            standard="ob-read-write",
            spec_version="v4.0",
            profile="fapi1-advanced",
            suite="ais-certification-slice",
        )
    )
    report = _report(
        tool_version="1.2.3",
        steps=(
            ("openid-discovery", "passed"),
            ("jwks-fetch", "passed"),
            ("client-credentials-token", "passed"),
            ("account-access-consent", "passed"),
            ("psu-authorization", "passed"),
            ("token-exchange", "passed"),
            ("accounts-list", "passed"),
            ("account-balances", "passed"),
            ("account-transactions", "skipped"),
        ),
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=resolved.manifest, policy=policy)

    assert result.valid is False
    assert result.reasons == ("mandatory_step_skipped", "manifest_coverage_partial")
    rendered = result.to_json_object()
    mandatory = rendered["mandatory"]
    assert isinstance(mandatory, dict)
    assert mandatory["total"] == 9
    assert mandatory["passed"] == 8
    assert mandatory["skipped"] == 1
    steps = mandatory["steps"]
    assert isinstance(steps, list)
    assert steps[-1] == {
        "stepId": "account-transactions",
        "status": "skipped",
        "valid": False,
        "reason": "mandatory_step_skipped",
    }


@pytest.mark.unit
def test_validate_report_smoke_suite_manifests_cannot_certify() -> None:
    """Bundled discovery-JWKS smoke suite manifests cannot pass OBL certification validation.

    Each bundled manifest must declare ``certificationCoverage: partial`` and the
    validator must reject them even if every mandatory step passes.  This test
    ensures the certification-safety correction introduced in Packet B is wired
    end-to-end for the actual shipped manifest files.
    """
    from pathlib import Path

    from conformance.manifest import load_manifest

    suites_dir = Path(__file__).resolve().parents[1] / "conformance" / "suites"
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.0.0",),
    )

    for manifest_file in sorted(suites_dir.glob("*.json")):
        manifest = load_manifest(manifest_file)

        assert manifest.certification_coverage == "partial", (
            f"{manifest_file.name} must declare certificationCoverage: partial"
        )

        if not any(step.mandatory for step in manifest.steps):
            # Manifests with no mandatory steps raise CertificationValidationError
            # (the existing pre-coverage hard error) — skip OBL validation check.
            continue

        step_outcomes: tuple[tuple[str, CheckStatus], ...] = tuple(
            (step.id, "passed") for step in manifest.steps if step.mandatory
        )
        report = _report(tool_version="1.0.0", steps=step_outcomes)

        result = validate_report(report=report, manifest=manifest, policy=policy)

        assert result.valid is False, (
            f"Smoke suite manifest {manifest_file.name} must not validate as certification-ready"
        )
        assert "manifest_coverage_partial" in result.reasons


# ─── Baseline-delta semantics (testValues block) ─────────────────────────────


def _manifest_json_with_test_values(*, certification_coverage: str = "complete") -> JsonObject:
    """Build a v1 manifest fixture that declares a ``testValues`` block.

    Args:
        certification_coverage: Coverage declaration to embed at the root.

    Returns:
        Manifest JSON object with one mandatory step and one optional step.
    """
    return {
        "schemaVersion": "v1",
        "name": "validator-test-values-new",
        "certificationCoverage": certification_coverage,
        "testValues": {
            "baseline": {
                "creditorAccountId": "BASELINE-ACCT-001",
                "remittanceInformation": "baseline-remittance",
            },
            "allowedCustomKeys": ["creditorAccountId", "remittanceInformation"],
        },
        "steps": [
            _manifest_step(step_id="mandatory-step", mandatory=True, optional=False),
        ],
    }


def _baseline_evidence(*, source: str = "baseline", delta_keys: list[str] | None = None) -> JsonObject:
    """Build a minimal valid new-shape ``testValueProfile`` evidence block.

    Args:
        source: Source label — ``"baseline"`` or ``"custom"``.
        delta_keys: Optional list of baseline delta key names.

    Returns:
        Baseline-delta test-value profile evidence JSON object.
    """
    block: JsonObject = {"source": source}
    if delta_keys is not None:
        block["baselineDeltaKeys"] = delta_keys  # type: ignore[assignment]
    return block


@pytest.mark.unit
def test_parse_submitted_report_accepts_baseline_source_shape() -> None:
    """Parser accepts ``source: baseline`` new-shape test-value profile evidence."""
    raw = {
        "metadata": {"reportVersion": "1.0"},
        "tool": {"version": "1.0.0"},
        "steps": [{"name": "mandatory-step", "status": "passed"}],
        "testValueProfile": {"source": "baseline"},
    }

    report = parse_submitted_report(raw)

    assert report.test_value_profile is not None
    assert report.test_value_profile.source == "baseline"
    assert report.test_value_profile.baseline_delta_keys == ()


@pytest.mark.unit
def test_parse_submitted_report_accepts_custom_source_shape() -> None:
    """Parser accepts ``source: custom`` with ``baselineDeltaKeys`` array."""
    raw = {
        "metadata": {"reportVersion": "1.0"},
        "tool": {"version": "1.0.0"},
        "steps": [{"name": "mandatory-step", "status": "passed"}],
        "testValueProfile": {"source": "custom", "baselineDeltaKeys": ["creditorAccountId"]},
    }

    report = parse_submitted_report(raw)

    assert report.test_value_profile is not None
    assert report.test_value_profile.source == "custom"
    assert report.test_value_profile.baseline_delta_keys == ("creditorAccountId",)


@pytest.mark.unit
def test_parse_submitted_report_rejects_unknown_source_value() -> None:
    """Parser rejects unknown ``source`` values with a validation error."""
    raw = {
        "metadata": {"reportVersion": "1.0"},
        "tool": {"version": "1.0.0"},
        "steps": [{"name": "s", "status": "passed"}],
        "testValueProfile": {"source": "invalid"},
    }

    from conformance.certification_validator import CertificationValidationError

    with pytest.raises(CertificationValidationError, match="source must be one of"):
        parse_submitted_report(raw)


@pytest.mark.unit
def test_validate_report_complete_manifest_with_test_values_requires_evidence() -> None:
    """Complete manifests with ``testValues`` block require test-value evidence."""
    manifest = parse_manifest(_manifest_json_with_test_values())
    report = _report(
        tool_version="1.2.3",
        steps=(("mandatory-step", "passed"),),
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is False
    assert "test_value_profile_missing" in result.reasons


@pytest.mark.unit
def test_validate_report_complete_manifest_with_test_values_blocks_on_custom_source() -> None:
    """``source: custom`` blocks certification for test-values manifests."""
    manifest = parse_manifest(_manifest_json_with_test_values())
    report = _report(
        tool_version="1.2.3",
        steps=(("mandatory-step", "passed"),),
        test_value_profile=_baseline_evidence(source="custom", delta_keys=["creditorAccountId"]),
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is False
    assert "test_value_profile_overridden" in result.reasons
    assert "test_value_profile_mismatch" not in result.reasons


@pytest.mark.unit
def test_validate_report_complete_manifest_with_test_values_blocks_on_non_empty_delta_keys() -> None:
    """Non-empty ``baselineDeltaKeys`` blocks certification even with ``source: custom``."""
    manifest = parse_manifest(_manifest_json_with_test_values())
    report = _report(
        tool_version="1.2.3",
        steps=(("mandatory-step", "passed"),),
        test_value_profile=_baseline_evidence(source="custom", delta_keys=["remittanceInformation"]),
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is False
    assert "test_value_profile_overridden" in result.reasons


@pytest.mark.unit
def test_validate_report_complete_manifest_with_test_values_accepts_baseline_source() -> None:
    """``source: baseline`` with empty ``baselineDeltaKeys`` passes value-purity gate."""
    manifest = parse_manifest(_manifest_json_with_test_values())
    report = _report(
        tool_version="1.2.3",
        steps=(("mandatory-step", "passed"),),
        test_value_profile=_baseline_evidence(source="baseline", delta_keys=[]),
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is True
    assert "test_value_profile_missing" not in result.reasons
    assert "test_value_profile_overridden" not in result.reasons
    assert "test_value_profile_mismatch" not in result.reasons


@pytest.mark.unit
def test_validate_report_complete_manifest_with_test_values_accepts_baseline_source_no_delta_field() -> None:
    """``source: baseline`` without a ``baselineDeltaKeys`` field also passes."""
    manifest = parse_manifest(_manifest_json_with_test_values())
    report = _report(
        tool_version="1.2.3",
        steps=(("mandatory-step", "passed"),),
        test_value_profile=_baseline_evidence(source="baseline"),
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is True
    assert "test_value_profile_overridden" not in result.reasons


@pytest.mark.unit
def test_validate_report_legacy_overridden_still_blocks_under_new_gate() -> None:
    """Legacy ``source: overridden`` still blocks certification under the updated gate."""
    manifest = parse_manifest(_manifest_json_with_test_value_profiles())
    report = _report(
        tool_version="1.2.3",
        steps=(("mandatory-step", "passed"), ("conditional-step", "passed")),
        test_value_profile=_test_value_profile_evidence_fixture(source="overridden"),
    )
    policy = ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=("1.2.3",),
    )

    result = validate_report(report=report, manifest=manifest, policy=policy)

    assert result.valid is False
    assert "test_value_profile_overridden" in result.reasons
