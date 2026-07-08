from types import MappingProxyType
from typing import cast

import pytest

from conformance.approved_releases import APPROVED_RELEASE_POLICY_SCHEMA_VERSION, ApprovedReleasePolicy
from conformance.results import StepResult


@pytest.mark.unit
def test_step_result_details_are_detached_from_input_mapping() -> None:
    details = {"keyCount": 1}

    result = StepResult(name="jwks", status="passed", message="Fetched JWKS document", details=details)
    details["keyCount"] = 999

    assert result.to_json_object()["details"] == {"keyCount": 1}


@pytest.mark.unit
def test_step_result_serialized_details_are_detached_from_result() -> None:
    result = StepResult(name="jwks", status="passed", message="Fetched JWKS document", details={"keyCount": 1})

    serialized_result = result.to_json_object()
    serialized_details = serialized_result["details"]
    assert isinstance(serialized_details, dict)
    serialized_details["keyCount"] = 999

    assert result.to_json_object()["details"] == {"keyCount": 1}
    assert isinstance(result.details, MappingProxyType)


@pytest.mark.unit
def test_step_result_mandatory_defaults_to_false() -> None:
    """``StepResult.mandatory`` defaults to ``False`` when not supplied."""
    result = StepResult(name="x", status="passed", message="ok")
    assert result.mandatory is False


@pytest.mark.unit
def test_step_result_mandatory_not_in_json_output() -> None:
    """``mandatory`` is intentionally not serialised on individual step entries.

    The per-step JSON shape is kept stable; mandatory status is surfaced only
    via the aggregate ``certificationEligibility`` block.
    """
    result = StepResult(name="x", status="passed", message="ok", mandatory=True)
    assert "mandatory" not in result.to_json_object()


@pytest.mark.unit
def test_model_bank_result_includes_report_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result
    from conformance.version import CONFORMANCE_TOOL_VERSION_ENV, REPORT_METADATA_VERSION

    monkeypatch.setenv(CONFORMANCE_TOOL_VERSION_ENV, "2.4.6")
    started = datetime.now(UTC)

    rendered = build_smoke_check_result(
        "env",
        [StepResult(name="x", status="passed", message="ok")],
        started_at=started,
    ).to_json_object()

    assert rendered["metadata"] == {"reportVersion": REPORT_METADATA_VERSION}
    assert rendered["tool"] == {"version": "2.4.6"}


@pytest.mark.unit
def test_manifest_result_includes_report_metadata_without_changing_plan() -> None:
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result
    from conformance.test_plan import TestPlan, TestPlanEntry
    from conformance.version import REPORT_METADATA_VERSION

    plan = TestPlan(entries=(TestPlanEntry(step_id="a", mandatory=True, optional=False, selected=True),))
    started = datetime.now(UTC)
    rendered = build_smoke_check_result(
        "env",
        [StepResult(name="a", status="passed", message="ok", mandatory=True)],
        started_at=started,
        plan=plan,
    ).to_json_object()

    assert rendered["metadata"] == {"reportVersion": REPORT_METADATA_VERSION}
    assert isinstance(rendered["tool"], dict)
    assert isinstance(rendered["tool"]["version"], str)
    assert rendered["plan"] == {
        "totalSteps": 1,
        "selectedSteps": 1,
        "deselectedSteps": 0,
        "mandatorySelected": 1,
        "mandatoryDeselected": 0,
        "conditionalSelected": 0,
        "conditionalDeselectedMissingValues": 0,
    }


@pytest.mark.unit
def test_eligibility_block_eligible_when_all_mandatory_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eligible when at least one mandatory step ran and all passed."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result
    from conformance.version import CONFORMANCE_TOOL_VERSION_ENV

    monkeypatch.setenv(CONFORMANCE_TOOL_VERSION_ENV, "1.2.3")
    started = datetime.now(UTC)
    steps = [
        StepResult(name="m1", status="passed", message="ok", mandatory=True),
        StepResult(name="opt", status="failed", message="boom", mandatory=False),
    ]
    block = build_smoke_check_result(
        "env",
        steps,
        started_at=started,
        approved_release_policy=_approved_policy("1.2.3"),
        certification_coverage="complete",
    ).to_json_object()["certificationEligibility"]
    assert isinstance(block, dict)
    assert block["eligible"] is True
    assert block["mandatoryTotal"] == 1
    assert block["mandatoryPassed"] == 1
    assert block["mandatoryFailed"] == 0
    assert "reason" not in block


@pytest.mark.unit
def test_eligibility_block_warn_on_mandatory_is_non_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    """A WARN on a mandatory step does not block eligibility (PRD)."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result
    from conformance.version import CONFORMANCE_TOOL_VERSION_ENV

    monkeypatch.setenv(CONFORMANCE_TOOL_VERSION_ENV, "1.2.3")
    started = datetime.now(UTC)
    steps = [
        StepResult(name="m1", status="passed", message="ok", mandatory=True),
        StepResult(name="m2", status="warn", message="deprecated", mandatory=True),
    ]
    block = build_smoke_check_result(
        "env",
        steps,
        started_at=started,
        approved_release_policy=_approved_policy("1.2.3"),
        certification_coverage="complete",
    ).to_json_object()["certificationEligibility"]
    assert isinstance(block, dict)
    assert block["eligible"] is True
    assert block["mandatoryWarn"] == 1


@pytest.mark.unit
def test_eligibility_block_failed_mandatory_blocks_with_reason() -> None:
    """A failed mandatory step blocks eligibility and emits a reason."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result

    started = datetime.now(UTC)
    steps = [
        StepResult(name="m1", status="failed", message="boom", mandatory=True),
        StepResult(name="m2", status="passed", message="ok", mandatory=True),
    ]
    block = build_smoke_check_result("env", steps, started_at=started).to_json_object()["certificationEligibility"]
    assert isinstance(block, dict)
    assert block["eligible"] is False
    assert block["mandatoryFailed"] == 1
    assert "1 mandatory step(s) failed" in str(block["reason"])


@pytest.mark.unit
def test_eligibility_block_skipped_mandatory_blocks_with_reason() -> None:
    """A skipped mandatory step blocks eligibility and emits a reason."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result

    started = datetime.now(UTC)
    steps = [
        StepResult(name="m1", status="skipped", message="prereq failed", mandatory=True),
    ]
    block = build_smoke_check_result("env", steps, started_at=started).to_json_object()["certificationEligibility"]
    assert isinstance(block, dict)
    assert block["eligible"] is False
    assert "skipped" in str(block["reason"])


@pytest.mark.unit
def test_eligibility_block_no_mandatory_means_not_eligible() -> None:
    """A manifest with zero mandatory steps cannot be certified (PRD)."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result

    started = datetime.now(UTC)
    steps = [StepResult(name="opt", status="passed", message="ok", mandatory=False)]
    block = build_smoke_check_result("env", steps, started_at=started).to_json_object()["certificationEligibility"]
    assert isinstance(block, dict)
    assert block["eligible"] is False
    assert block["mandatoryTotal"] == 0
    assert "No mandatory steps" in str(block["reason"])


@pytest.mark.unit
def test_eligibility_approves_tool_version_listed_in_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Approved policy plus passing mandatory coverage makes the report eligible."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result
    from conformance.version import CONFORMANCE_TOOL_VERSION_ENV

    monkeypatch.setenv(CONFORMANCE_TOOL_VERSION_ENV, "4.5.6")
    started = datetime.now(UTC)

    rendered = build_smoke_check_result(
        "env",
        [StepResult(name="m1", status="passed", message="ok", mandatory=True)],
        started_at=started,
        approved_release_policy=_approved_policy("4.5.6"),
        certification_coverage="complete",
    ).to_json_object()

    block = rendered["certificationEligibility"]
    assert isinstance(block, dict)
    assert block["eligible"] is True
    assert "reason" not in block
    assert "reasons" not in block
    assert block["approvedRelease"] == {
        "checked": True,
        "approved": True,
        "toolVersion": "4.5.6",
        "policySchemaVersion": APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
    }
    assert rendered["tool"] == {"version": "4.5.6"}


@pytest.mark.unit
def test_eligibility_rejects_tool_version_absent_from_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unapproved tool versions block participant-side eligibility."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result
    from conformance.version import CONFORMANCE_TOOL_VERSION_ENV

    monkeypatch.setenv(CONFORMANCE_TOOL_VERSION_ENV, "4.5.6")
    started = datetime.now(UTC)

    block = build_smoke_check_result(
        "env",
        [StepResult(name="m1", status="passed", message="ok", mandatory=True)],
        started_at=started,
        approved_release_policy=_approved_policy("9.9.9"),
        certification_coverage="complete",
    ).to_json_object()["certificationEligibility"]

    assert isinstance(block, dict)
    assert block["eligible"] is False
    assert block["reason"] == "Tool version is not in the approved-release policy: 4.5.6"
    assert block["reasons"] == ["Tool version is not in the approved-release policy: 4.5.6"]
    assert block["approvedRelease"] == {
        "checked": True,
        "approved": False,
        "toolVersion": "4.5.6",
        "policySchemaVersion": APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
    }


@pytest.mark.unit
def test_eligibility_rejects_absent_approved_release_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing approved-release policy is explicit and non-eligible."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result
    from conformance.version import CONFORMANCE_TOOL_VERSION_ENV

    monkeypatch.setenv(CONFORMANCE_TOOL_VERSION_ENV, "4.5.6")
    started = datetime.now(UTC)

    block = build_smoke_check_result(
        "env",
        [StepResult(name="m1", status="passed", message="ok", mandatory=True)],
        started_at=started,
        certification_coverage="complete",
    ).to_json_object()["certificationEligibility"]

    assert isinstance(block, dict)
    assert block["eligible"] is False
    assert block["reason"] == "Approved-release policy was not supplied"
    assert block["reasons"] == ["Approved-release policy was not supplied"]
    assert block["approvedRelease"] == {
        "checked": False,
        "approved": False,
        "toolVersion": "4.5.6",
    }


@pytest.mark.unit
def test_eligibility_collects_multiple_blocking_reasons(monkeypatch: pytest.MonkeyPatch) -> None:
    """The legacy reason is the highest-priority entry from all blockers."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result
    from conformance.version import CONFORMANCE_TOOL_VERSION_ENV

    monkeypatch.setenv(CONFORMANCE_TOOL_VERSION_ENV, "4.5.6")
    started = datetime.now(UTC)

    block = build_smoke_check_result(
        "env",
        [
            StepResult(name="m1", status="failed", message="boom", mandatory=True),
            StepResult(name="m2", status="skipped", message="prereq failed", mandatory=True),
        ],
        started_at=started,
        approved_release_policy=_approved_policy("9.9.9"),
        certification_coverage="complete",
    ).to_json_object()["certificationEligibility"]

    assert isinstance(block, dict)
    assert block["eligible"] is False
    assert block["reason"] == "1 mandatory step(s) failed"
    assert block["reasons"] == [
        "1 mandatory step(s) failed",
        "1 mandatory step(s) skipped due to earlier failures",
        "Tool version is not in the approved-release policy: 4.5.6",
    ]


# ─── TestPlan deselection eligibility precedence ─────────────────────────────


@pytest.mark.unit
def test_eligibility_deselected_mandatory_blocks_with_dedicated_reason() -> None:
    """Deselecting a mandatory step blocks eligibility with the dedicated reason."""
    from datetime import UTC, datetime

    from conformance.manifest import parse_manifest
    from conformance.results import build_smoke_check_result
    from conformance.test_plan import TestPlan

    manifest = parse_manifest(
        {
            "schemaVersion": "v1",
            "name": "elig",
            "steps": [
                {
                    "id": "m",
                    "name": "M",
                    "mandatory": True,
                    "request": {"method": "GET", "url": "https://example.com/m"},
                    "assertions": [{"type": "http_status", "expected": 200}],
                }
            ],
        }
    )
    plan = TestPlan.default_plan_from_manifest(manifest).with_deselection(["m"])
    started = datetime.now(UTC)

    block = build_smoke_check_result("env", [], started_at=started, plan=plan).to_json_object()[
        "certificationEligibility"
    ]
    assert isinstance(block, dict)
    assert block["eligible"] is False
    assert block["reason"] == "Mandatory steps were deselected from the plan"
    assert block["mandatoryDeselected"] == 1
    assert block["mandatoryDeselectedStepIds"] == ["m"]
    reasons = block["reasons"]
    assert isinstance(reasons, list)
    assert str(reasons[0]) == "Mandatory steps were deselected from the plan"
    assert "No mandatory steps declared" not in reasons


@pytest.mark.unit
def test_eligibility_deselected_mandatory_precedence_over_no_mandatory() -> None:
    """Deselected-mandatory reason takes precedence over ``no mandatory declared``."""
    from datetime import UTC, datetime

    from conformance.results import StepResult, build_smoke_check_result
    from conformance.test_plan import TestPlan, TestPlanEntry

    # Hand-build a plan with one deselected-mandatory and no other entries,
    # so the executed-step list is empty (zero mandatory ran).
    plan = TestPlan(entries=(TestPlanEntry(step_id="m", mandatory=True, optional=False, selected=False),))
    started = datetime.now(UTC)

    rendered = build_smoke_check_result(
        "env",
        [StepResult(name="opt", status="passed", message="ok")],
        started_at=started,
        plan=plan,
    ).to_json_object()
    block = rendered["certificationEligibility"]
    assert isinstance(block, dict)
    assert block["reason"] == "Mandatory steps were deselected from the plan"
    assert block["mandatoryTotal"] == 1
    reasons = block["reasons"]
    assert isinstance(reasons, list)
    assert "No mandatory steps declared" not in reasons


def _approved_policy(*approved_tool_versions: str) -> ApprovedReleasePolicy:
    """Build an approved-release policy for result tests.

    Args:
        approved_tool_versions: Tool versions accepted by the policy.

    Returns:
        Approved-release policy with the current schema version.
    """
    return ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=approved_tool_versions,
    )


@pytest.mark.unit
def test_plan_block_shape_stable() -> None:
    """The top-level ``plan`` block exposes exactly the documented counts."""
    from datetime import UTC, datetime

    from conformance.results import StepResult, build_smoke_check_result
    from conformance.test_plan import TestPlan, TestPlanEntry

    plan = TestPlan(
        entries=(
            TestPlanEntry(step_id="a", mandatory=True, optional=False, selected=True),
            TestPlanEntry(step_id="b", mandatory=False, optional=True, selected=False),
            TestPlanEntry(step_id="c", mandatory=False, optional=False, selected=True),
        )
    )
    started = datetime.now(UTC)
    rendered = build_smoke_check_result(
        "env",
        [StepResult(name="a", status="passed", message="ok", mandatory=True)],
        started_at=started,
        plan=plan,
    ).to_json_object()

    assert rendered["plan"] == {
        "totalSteps": 3,
        "selectedSteps": 2,
        "deselectedSteps": 1,
        "mandatorySelected": 1,
        "mandatoryDeselected": 0,
        "conditionalSelected": 0,
        "conditionalDeselectedMissingValues": 0,
    }


@pytest.mark.unit
def test_plan_block_absent_when_no_plan_supplied() -> None:
    """Smoke checks and v0 runs (no plan) omit the ``plan`` block."""
    from datetime import UTC, datetime

    from conformance.results import StepResult, build_smoke_check_result

    started = datetime.now(UTC)
    rendered = build_smoke_check_result(
        "env",
        [StepResult(name="x", status="passed", message="ok")],
        started_at=started,
    ).to_json_object()
    assert "plan" not in rendered


@pytest.mark.unit
def test_auth_and_environment_evidence_blocks_are_serialized_when_supplied() -> None:
    """Result JSON includes optional auth/capability evidence blocks when present."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result

    started = datetime.now(UTC)
    rendered = build_smoke_check_result(
        "env",
        [StepResult(name="x", status="passed", message="ok")],
        started_at=started,
        auth_metadata_evidence={
            "bundles": [{"id": "ais", "tokenStepId": "token-exchange"}],
            "selectedStepRequirements": [{"stepId": "accounts-list", "bundleId": "ais"}],
        },
        environment_capability_evidence={
            "suiteSelection": {"standard": "ob-read-write"},
            "environment": {"source": "custom", "label": "env"},
            "decisions": [{"support": "unknown", "warnings": ["undeclared"], "blockers": []}],
        },
    ).to_json_object()

    assert rendered["authMetadata"] == {
        "bundles": [{"id": "ais", "tokenStepId": "token-exchange"}],
        "selectedStepRequirements": [{"stepId": "accounts-list", "bundleId": "ais"}],
    }
    assert rendered["environmentCapabilities"] == {
        "suiteSelection": {"standard": "ob-read-write"},
        "environment": {"source": "custom", "label": "env"},
        "decisions": [{"support": "unknown", "warnings": ["undeclared"], "blockers": []}],
    }


@pytest.mark.unit
def test_test_value_profile_evidence_is_serialized_when_supplied() -> None:
    """Result JSON includes optional test-value profile evidence when present."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result

    started = datetime.now(UTC)
    rendered = build_smoke_check_result(
        "env",
        [StepResult(name="x", status="passed", message="ok")],
        started_at=started,
        test_value_profile_evidence={
            "profileId": "ozone-demo",
            "source": "overridden",
            "overrideKeys": ["creditorName"],
            "declaredKeys": ["creditorName", "instructionIdentification"],
            "requiredKeys": ["creditorName"],
            "conditionOutcomes": [
                {
                    "stepId": "domestic-payment-consent",
                    "selected": True,
                    "requiredKeys": ["creditorName"],
                    "missingKeys": [],
                    "allRequiredValuesPresent": True,
                }
            ],
            "effectiveValues": {"instructionIdentification": "***"},
        },
    ).to_json_object()

    assert rendered["testValueProfile"] == {
        "profileId": "ozone-demo",
        "source": "overridden",
        "overrideKeys": ["creditorName"],
        "declaredKeys": ["creditorName", "instructionIdentification"],
        "requiredKeys": ["creditorName"],
        "conditionOutcomes": [
            {
                "stepId": "domestic-payment-consent",
                "selected": True,
                "requiredKeys": ["creditorName"],
                "missingKeys": [],
                "allRequiredValuesPresent": True,
            }
        ],
        "effectiveValues": {"instructionIdentification": "***"},
    }


@pytest.mark.unit
def test_custom_test_value_impact_is_serialized_when_supplied() -> None:
    """Result JSON includes optional custom-test-value impact evidence."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result

    started = datetime.now(UTC)
    rendered = build_smoke_check_result(
        "env",
        [StepResult(name="x", status="passed", message="ok")],
        started_at=started,
        custom_test_value_impact={
            "profileId": "ozone-demo",
            "source": "overridden",
            "overrideKeys": ["creditorName"],
            "summary": {
                "overrideKeyCount": 1,
                "executedReferenceCount": 2,
                "referencedButNotRunCount": 1,
                "executedStepCount": 1,
                "referencedButNotRunStepCount": 1,
            },
            "executedReferences": [
                {
                    "stepId": "domestic-payment-consent",
                    "stepName": "Domestic payment consent",
                    "status": "passed",
                    "key": "creditorName",
                    "requestArea": "request-json-body",
                    "fieldPath": "request.body.Data.Initiation.CreditorAccount.Name",
                }
            ],
            "referencedButNotRun": [
                {
                    "stepId": "domestic-payment-consent-negative",
                    "stepName": "Domestic payment consent negative",
                    "notRunReason": "deselected",
                    "key": "creditorName",
                    "requestArea": "request-json-body",
                    "fieldPath": "request.body.Data.Initiation.CreditorAccount.Name",
                }
            ],
        },
    ).to_json_object()

    assert rendered["customTestValueImpact"] == {
        "profileId": "ozone-demo",
        "source": "overridden",
        "overrideKeys": ["creditorName"],
        "summary": {
            "overrideKeyCount": 1,
            "executedReferenceCount": 2,
            "referencedButNotRunCount": 1,
            "executedStepCount": 1,
            "referencedButNotRunStepCount": 1,
        },
        "executedReferences": [
            {
                "stepId": "domestic-payment-consent",
                "stepName": "Domestic payment consent",
                "status": "passed",
                "key": "creditorName",
                "requestArea": "request-json-body",
                "fieldPath": "request.body.Data.Initiation.CreditorAccount.Name",
            }
        ],
        "referencedButNotRun": [
            {
                "stepId": "domestic-payment-consent-negative",
                "stepName": "Domestic payment consent negative",
                "notRunReason": "deselected",
                "key": "creditorName",
                "requestArea": "request-json-body",
                "fieldPath": "request.body.Data.Initiation.CreditorAccount.Name",
            }
        ],
    }


# ─── Packet B: certification coverage gating ─────────────────────────────────


@pytest.mark.unit
def test_eligibility_partial_coverage_blocks_even_when_all_mandatory_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Partial coverage blocks eligibility even when all mandatory steps pass and the policy approves the version.

    This is the core certification-safety invariant: a manifest not explicitly
    marked ``certificationCoverage: complete`` can never satisfy the eligibility
    check, regardless of execution outcomes or approved-release policy.
    """
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result
    from conformance.version import CONFORMANCE_TOOL_VERSION_ENV

    monkeypatch.setenv(CONFORMANCE_TOOL_VERSION_ENV, "1.0.0")
    started = datetime.now(UTC)

    block = build_smoke_check_result(
        "env",
        [StepResult(name="m1", status="passed", message="ok", mandatory=True)],
        started_at=started,
        approved_release_policy=_approved_policy("1.0.0"),
        certification_coverage="partial",
    ).to_json_object()["certificationEligibility"]

    assert isinstance(block, dict)
    assert block["eligible"] is False
    assert block["reason"] == "Manifest is not marked as complete certification coverage"
    assert "Manifest is not marked as complete certification coverage" in cast(list[str], block["reasons"])


@pytest.mark.unit
def test_eligibility_partial_coverage_reason_precedes_missing_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Partial coverage is the primary reason when a policy could not make the run eligible."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result
    from conformance.version import CONFORMANCE_TOOL_VERSION_ENV

    monkeypatch.setenv(CONFORMANCE_TOOL_VERSION_ENV, "1.0.0")
    started = datetime.now(UTC)

    block = build_smoke_check_result(
        "env",
        [StepResult(name="m1", status="passed", message="ok", mandatory=True)],
        started_at=started,
        certification_coverage="partial",
    ).to_json_object()["certificationEligibility"]

    assert isinstance(block, dict)
    assert block["eligible"] is False
    assert block["reason"] == "Manifest is not marked as complete certification coverage"
    assert block["reasons"] == [
        "Manifest is not marked as complete certification coverage",
        "Approved-release policy was not supplied",
    ]


@pytest.mark.unit
def test_eligibility_default_coverage_is_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    """Omitting ``certification_coverage`` defaults to ``partial`` and blocks eligibility.

    This preserves the safety-by-default contract: non-manifest callers, v0
    manifest runs, and callers that do not supply the new parameter all receive
    a partial-coverage result rather than inadvertently becoming certifiable.
    """
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result
    from conformance.version import CONFORMANCE_TOOL_VERSION_ENV

    monkeypatch.setenv(CONFORMANCE_TOOL_VERSION_ENV, "1.0.0")
    started = datetime.now(UTC)

    block = build_smoke_check_result(
        "env",
        [StepResult(name="m1", status="passed", message="ok", mandatory=True)],
        started_at=started,
        approved_release_policy=_approved_policy("1.0.0"),
        # certification_coverage intentionally omitted — must default to partial
    ).to_json_object()["certificationEligibility"]

    assert isinstance(block, dict)
    assert block["eligible"] is False
    assert "Manifest is not marked as complete certification coverage" in cast(list[str], block["reasons"])


@pytest.mark.unit
def test_eligibility_coverage_block_present_in_json_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``certificationCoverage`` audit block is included in every eligibility result."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result
    from conformance.version import CONFORMANCE_TOOL_VERSION_ENV

    monkeypatch.setenv(CONFORMANCE_TOOL_VERSION_ENV, "1.0.0")
    started = datetime.now(UTC)

    for coverage in ("partial", "complete"):
        block = build_smoke_check_result(
            "env",
            [StepResult(name="m1", status="passed", message="ok", mandatory=True)],
            started_at=started,
            approved_release_policy=_approved_policy("1.0.0"),
            certification_coverage=coverage,
        ).to_json_object()["certificationEligibility"]

        assert isinstance(block, dict)
        coverage_block = block["certificationCoverage"]
        assert isinstance(coverage_block, dict)
        assert coverage_block["value"] == coverage


@pytest.mark.unit
def test_eligibility_smoke_suite_manifests_are_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bundled discovery-JWKS smoke suite manifests are marked partial and cannot certify.

    Validates the certification-safety correction: smoke suites may run and report
    but are permanently non-certifiable via the ``certificationCoverage: partial``
    declaration in their manifest files.
    """
    from datetime import UTC, datetime
    from pathlib import Path

    from conformance.manifest import load_manifest
    from conformance.results import build_smoke_check_result
    from conformance.version import CONFORMANCE_TOOL_VERSION_ENV

    monkeypatch.setenv(CONFORMANCE_TOOL_VERSION_ENV, "1.0.0")
    suites_dir = Path(__file__).resolve().parents[1] / "conformance" / "suites"

    for manifest_file in sorted(suites_dir.glob("*.json")):
        manifest = load_manifest(manifest_file)
        assert manifest.certification_coverage == "partial", (
            f"{manifest_file.name} must declare certificationCoverage: partial "
            "to prevent smoke suites from satisfying the certification eligibility check"
        )

        steps = [
            StepResult(name=step.id, status="passed", message="ok", mandatory=step.mandatory) for step in manifest.steps
        ]
        started = datetime.now(UTC)
        block = build_smoke_check_result(
            "env",
            steps,
            started_at=started,
            approved_release_policy=_approved_policy("1.0.0"),
            certification_coverage=manifest.certification_coverage,
        ).to_json_object()["certificationEligibility"]

        assert isinstance(block, dict)
        assert block["eligible"] is False, f"Smoke suite manifest {manifest_file.name} must not yield eligible=True"
        assert "Manifest is not marked as complete certification coverage" in cast(list[str], block["reasons"])


@pytest.mark.unit
def test_readiness_report_serialise_parse_round_trip() -> None:
    """Readiness report serialise/parse round-trip preserves all fields."""
    from datetime import UTC, datetime

    from conformance.results import (
        DcrReadinessStatus,
        ResourceGroupReadiness,
        RunReadinessReport,
        SelectedCoverageSummary,
        parse_readiness_report,
        serialise_readiness_report,
    )
    from conformance.run_plan_v2 import RunPlanV2TargetCoordinates

    report = RunReadinessReport(
        schema_version="2",
        target_coordinates=RunPlanV2TargetCoordinates(
            standard="obl",
            specification="read-write",
            security_profile="fapi1-advanced",
            specification_version="v4.0.1",
            catalogue_hash="sha256:catalogue",
        ),
        catalogue_hash="sha256:catalogue",
        selected_coverage_summary=SelectedCoverageSummary(
            selected_resource_groups=("ais",),
            selected_endpoint_count=4,
            mandatory_endpoint_count=3,
            omitted_mandatory_endpoint_count=1,
            coverage_complete=False,
        ),
        overall_outcome="failed",
        resource_group_sections=(
            ResourceGroupReadiness(
                resource_group="ais",
                readiness_outcome="failed",
                omitted_mandatory_endpoints=("accounts-detail",),
                selected_test_count=5,
                passed_count=3,
                failed_count=1,
                skipped_count=1,
                certification_eligible=False,
            ),
        ),
        dcr_status=DcrReadinessStatus(
            certifying=False,
            certifying_blocked_reason="No DCR certification policy exists for this tool",
            passed_count=1,
            failed_count=0,
            skipped_count=0,
        ),
        run_id="run-123",
        generated_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )

    serialised = serialise_readiness_report(report)
    reparsed = parse_readiness_report(serialised)

    assert reparsed == report


@pytest.mark.unit
def test_determine_readiness_outcome_variants() -> None:
    """Readiness outcome logic covers ready/incomplete/non-certifying/failed."""
    from conformance.results import (
        ResourceGroupReadiness,
        SelectedCoverageSummary,
        build_dcr_readiness_status,
        determine_readiness_outcome,
    )

    ready_summary = SelectedCoverageSummary(
        selected_resource_groups=("ais",),
        selected_endpoint_count=2,
        mandatory_endpoint_count=2,
        omitted_mandatory_endpoint_count=0,
        coverage_complete=True,
    )
    ready_section = ResourceGroupReadiness(
        resource_group="ais",
        readiness_outcome="ready",
        omitted_mandatory_endpoints=(),
        selected_test_count=2,
        passed_count=2,
        failed_count=0,
        skipped_count=0,
        certification_eligible=True,
    )
    assert (
        determine_readiness_outcome(
            selected_coverage_summary=ready_summary,
            resource_group_sections=(ready_section,),
            dcr_status=None,
        )
        == "ready"
    )

    incomplete_summary = SelectedCoverageSummary(
        selected_resource_groups=("ais",),
        selected_endpoint_count=1,
        mandatory_endpoint_count=2,
        omitted_mandatory_endpoint_count=1,
        coverage_complete=False,
    )
    incomplete_section = ResourceGroupReadiness(
        resource_group="ais",
        readiness_outcome="incomplete",
        omitted_mandatory_endpoints=("accounts-detail",),
        selected_test_count=1,
        passed_count=1,
        failed_count=0,
        skipped_count=0,
        certification_eligible=False,
    )
    assert (
        determine_readiness_outcome(
            selected_coverage_summary=incomplete_summary,
            resource_group_sections=(incomplete_section,),
            dcr_status=None,
        )
        == "incomplete"
    )

    failed_section = ResourceGroupReadiness(
        resource_group="ais",
        readiness_outcome="failed",
        omitted_mandatory_endpoints=(),
        selected_test_count=2,
        passed_count=1,
        failed_count=1,
        skipped_count=0,
        certification_eligible=False,
    )
    assert (
        determine_readiness_outcome(
            selected_coverage_summary=ready_summary,
            resource_group_sections=(failed_section,),
            dcr_status=None,
        )
        == "failed"
    )

    dcr_status = build_dcr_readiness_status(passed_count=1, failed_count=0, skipped_count=0)
    assert (
        determine_readiness_outcome(
            selected_coverage_summary=ready_summary,
            resource_group_sections=(),
            dcr_status=dcr_status,
        )
        == "non-certifying"
    )


@pytest.mark.unit
def test_omitted_mandatory_endpoint_detection_uses_selected_resource_groups() -> None:
    """Readiness omission detection returns omitted mandatory endpoints by group."""
    from conformance.catalogue import Catalogue, CatalogueIdentity, EndpointCatalogueEntry
    from conformance.results import omitted_mandatory_endpoint_ids_by_resource_group
    from conformance.run_plan_v2 import EndpointSelection, RunPlanV2, RunPlanV2TargetCoordinates

    catalogue = Catalogue(
        identity=CatalogueIdentity(
            plugin_id="read-write",
            specification="read-write",
            specification_version="v4.0.1",
            content_hash="sha256:catalogue",
        ),
        endpoints=(
            EndpointCatalogueEntry(
                endpoint_id="accounts-list",
                operation="GET",
                path="/accounts",
                method="GET",
                resource_group="ais",
                requirement="mandatory",
                display_label="Accounts list",
            ),
            EndpointCatalogueEntry(
                endpoint_id="account-balances",
                operation="GET",
                path="/accounts/{AccountId}/balances",
                method="GET",
                resource_group="ais",
                requirement="mandatory",
                display_label="Account balances",
            ),
            EndpointCatalogueEntry(
                endpoint_id="domestic-payments",
                operation="POST",
                path="/domestic-payments",
                method="POST",
                resource_group="pis",
                requirement="mandatory",
                display_label="Domestic payments",
            ),
        ),
    )
    run_plan = RunPlanV2(
        schema_version="2",
        target=RunPlanV2TargetCoordinates(
            standard="obl",
            specification="read-write",
            security_profile="fapi1-advanced",
            specification_version="v4.0.1",
            catalogue_hash="sha256:catalogue",
        ),
        resource_groups=("ais",),
        endpoint_selections=(
            EndpointSelection(
                endpoint_id="accounts-list",
                operation="GET",
                selected=True,
                field_values={},
            ),
            EndpointSelection(
                endpoint_id="account-balances",
                operation="GET",
                selected=False,
                field_values={},
            ),
        ),
    )

    omitted = omitted_mandatory_endpoint_ids_by_resource_group(catalogue, run_plan)

    assert omitted == {"ais": ("account-balances",)}
