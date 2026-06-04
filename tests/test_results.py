from types import MappingProxyType

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
    assert str(block["reasons"][0]) == "Mandatory steps were deselected from the plan"


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
def test_suite_metadata_absent_when_not_supplied() -> None:
    """Smoke checks and explicit manifest runs omit the suite metadata block."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result

    started = datetime.now(UTC)
    rendered = build_smoke_check_result(
        "env",
        [StepResult(name="x", status="passed", message="ok")],
        started_at=started,
    ).to_json_object()

    assert "suite" not in rendered


@pytest.mark.unit
def test_suite_metadata_serialized_when_supplied() -> None:
    """Config-resolved suite runs expose safe catalog metadata in results."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result
    from conformance.suite_catalog import SuiteMetadata

    started = datetime.now(UTC)
    metadata = SuiteMetadata(
        catalog_id="ob-read-write/v4.0/fapi1-advanced/discovery-jwks",
        label="Open Banking Read/Write v4.0 FAPI 1 Advanced discovery/JWKS smoke suite",
        standard="ob-read-write",
        spec_version="v4.0",
        profile="fapi1-advanced",
        suite="discovery-jwks",
        manifest_resource="ob-read-write-v4.0-fapi1-advanced-discovery-jwks.json",
        description="Smoke-level discovery and JWKS checks.",
    )

    rendered = build_smoke_check_result(
        "env",
        [StepResult(name="x", status="passed", message="ok")],
        started_at=started,
        suite_metadata=metadata,
    ).to_json_object()

    assert rendered["suite"] == {
        "catalogId": "ob-read-write/v4.0/fapi1-advanced/discovery-jwks",
        "manifestResource": "ob-read-write-v4.0-fapi1-advanced-discovery-jwks.json",
        "standard": "ob-read-write",
        "specVersion": "v4.0",
        "profile": "fapi1-advanced",
        "suite": "discovery-jwks",
    }
