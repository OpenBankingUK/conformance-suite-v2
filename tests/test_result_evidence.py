from datetime import UTC, datetime
from typing import cast

import pytest

from conformance.approved_releases import APPROVED_RELEASE_POLICY_SCHEMA_VERSION, ApprovedReleasePolicy
from conformance.json_types import JsonObject
from conformance.results import StepResult, build_smoke_check_result
from conformance.test_plan import TestPlan, TestPlanEntry

_CUSTOM_VALUES_REASON = (
    "Custom test values were used — this run is an Exploratory Run and is not eligible for certification"
)


def _approved_policy(*approved_tool_versions: str) -> ApprovedReleasePolicy:
    """Build an approved-release policy for result-evidence tests.

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
def test_smoke_check_result_blocks_eligibility_when_custom_test_values_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFORMANCE_TOOL_VERSION", "1.0.0")
    block = build_smoke_check_result(
        "env",
        [StepResult(name="m1", status="passed", message="ok", mandatory=True)],
        started_at=datetime.now(UTC),
        custom_test_values_active=True,
        approved_release_policy=_approved_policy("1.0.0"),
        certification_coverage="complete",
    ).to_json_object()["certificationEligibility"]
    eligibility = cast(JsonObject, block)
    reasons = cast(list[str], eligibility["reasons"])

    assert eligibility["eligible"] is False
    assert eligibility["reason"] == _CUSTOM_VALUES_REASON
    assert _CUSTOM_VALUES_REASON in reasons


@pytest.mark.unit
def test_smoke_check_result_default_custom_values_flag_does_not_block_eligibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFORMANCE_TOOL_VERSION", "1.0.0")
    block = build_smoke_check_result(
        "env",
        [StepResult(name="m1", status="passed", message="ok", mandatory=True)],
        started_at=datetime.now(UTC),
        approved_release_policy=_approved_policy("1.0.0"),
        certification_coverage="complete",
    ).to_json_object()["certificationEligibility"]
    eligibility = cast(JsonObject, block)

    assert eligibility["eligible"] is True
    assert "reasons" not in eligibility


@pytest.mark.unit
def test_custom_values_reason_precedes_other_eligibility_blockers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFORMANCE_TOOL_VERSION", "1.0.0")
    plan = TestPlan(entries=(TestPlanEntry(step_id="m", mandatory=True, optional=False, selected=False),))
    block = build_smoke_check_result(
        "env",
        [],
        started_at=datetime.now(UTC),
        plan=plan,
        custom_test_values_active=True,
        approved_release_policy=_approved_policy("1.0.0"),
        certification_coverage="complete",
    ).to_json_object()["certificationEligibility"]
    eligibility = cast(JsonObject, block)
    reasons = cast(list[str], eligibility["reasons"])

    assert eligibility["eligible"] is False
    assert eligibility["reason"] == _CUSTOM_VALUES_REASON
    assert reasons[0] == _CUSTOM_VALUES_REASON
    assert reasons[1] == "Mandatory steps were deselected from the plan"


# ─── valuePurityPassed gate ───────────────────────────────────────────────────


@pytest.mark.unit
def test_value_purity_passed_true_when_no_custom_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """``valuePurityPassed`` is True when no custom test values are active."""
    monkeypatch.setenv("CONFORMANCE_TOOL_VERSION", "1.0.0")
    block = build_smoke_check_result(
        "env",
        [StepResult(name="m1", status="passed", message="ok", mandatory=True)],
        started_at=datetime.now(UTC),
        approved_release_policy=_approved_policy("1.0.0"),
        certification_coverage="complete",
    ).to_json_object()["certificationEligibility"]
    eligibility = cast(JsonObject, block)

    assert eligibility["valuePurityPassed"] is True


@pytest.mark.unit
def test_value_purity_passed_false_when_custom_test_values_active(monkeypatch: pytest.MonkeyPatch) -> None:
    """``valuePurityPassed`` is False when custom test values are active."""
    monkeypatch.setenv("CONFORMANCE_TOOL_VERSION", "1.0.0")
    block = build_smoke_check_result(
        "env",
        [StepResult(name="m1", status="passed", message="ok", mandatory=True)],
        started_at=datetime.now(UTC),
        custom_test_values_active=True,
        approved_release_policy=_approved_policy("1.0.0"),
        certification_coverage="complete",
    ).to_json_object()["certificationEligibility"]
    eligibility = cast(JsonObject, block)

    assert eligibility["valuePurityPassed"] is False


@pytest.mark.unit
def test_value_purity_passed_true_from_baseline_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """``valuePurityPassed`` derives from evidence ``source: baseline`` with no delta keys."""
    monkeypatch.setenv("CONFORMANCE_TOOL_VERSION", "1.0.0")
    block = build_smoke_check_result(
        "env",
        [StepResult(name="m1", status="passed", message="ok", mandatory=True)],
        started_at=datetime.now(UTC),
        approved_release_policy=_approved_policy("1.0.0"),
        certification_coverage="complete",
        test_value_profile_evidence={"source": "baseline", "baselineDeltaKeys": []},
    ).to_json_object()["certificationEligibility"]
    eligibility = cast(JsonObject, block)

    assert eligibility["valuePurityPassed"] is True
    assert eligibility["eligible"] is True


@pytest.mark.unit
def test_value_purity_passed_false_from_custom_source_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """``valuePurityPassed`` is False when evidence ``source`` is ``"custom"``."""
    monkeypatch.setenv("CONFORMANCE_TOOL_VERSION", "1.0.0")
    block = build_smoke_check_result(
        "env",
        [StepResult(name="m1", status="passed", message="ok", mandatory=True)],
        started_at=datetime.now(UTC),
        approved_release_policy=_approved_policy("1.0.0"),
        certification_coverage="complete",
        test_value_profile_evidence={"source": "custom", "baselineDeltaKeys": ["creditorName"]},
    ).to_json_object()["certificationEligibility"]
    eligibility = cast(JsonObject, block)

    assert eligibility["valuePurityPassed"] is False
    assert eligibility["eligible"] is False


@pytest.mark.unit
def test_value_purity_passed_false_from_non_empty_baseline_delta_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """``valuePurityPassed`` is False when evidence has non-empty ``baselineDeltaKeys``.

    Even when ``source`` claims ``"baseline"``, non-empty delta keys indicate
    that at least one effective value differs from the suite baseline.
    """
    monkeypatch.setenv("CONFORMANCE_TOOL_VERSION", "1.0.0")
    block = build_smoke_check_result(
        "env",
        [StepResult(name="m1", status="passed", message="ok", mandatory=True)],
        started_at=datetime.now(UTC),
        approved_release_policy=_approved_policy("1.0.0"),
        certification_coverage="complete",
        test_value_profile_evidence={"source": "baseline", "baselineDeltaKeys": ["paymentAmount"]},
    ).to_json_object()["certificationEligibility"]
    eligibility = cast(JsonObject, block)

    assert eligibility["valuePurityPassed"] is False
    assert eligibility["eligible"] is False


@pytest.mark.unit
def test_value_purity_passed_true_from_default_source_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """``valuePurityPassed`` is True from legacy ``source: default`` evidence."""
    monkeypatch.setenv("CONFORMANCE_TOOL_VERSION", "1.0.0")
    block = build_smoke_check_result(
        "env",
        [StepResult(name="m1", status="passed", message="ok", mandatory=True)],
        started_at=datetime.now(UTC),
        approved_release_policy=_approved_policy("1.0.0"),
        certification_coverage="complete",
        test_value_profile_evidence={"source": "default", "profileId": "ozone-demo", "overrideKeys": []},
    ).to_json_object()["certificationEligibility"]
    eligibility = cast(JsonObject, block)

    assert eligibility["valuePurityPassed"] is True
    assert eligibility["eligible"] is True


# ─── coveragePassed gate ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_coverage_passed_true_when_complete_coverage_all_mandatory_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """``coveragePassed`` is True when coverage is complete and all mandatory steps pass."""
    monkeypatch.setenv("CONFORMANCE_TOOL_VERSION", "1.0.0")
    block = build_smoke_check_result(
        "env",
        [StepResult(name="m1", status="passed", message="ok", mandatory=True)],
        started_at=datetime.now(UTC),
        approved_release_policy=_approved_policy("1.0.0"),
        certification_coverage="complete",
    ).to_json_object()["certificationEligibility"]
    eligibility = cast(JsonObject, block)

    assert eligibility["coveragePassed"] is True


@pytest.mark.unit
def test_coverage_passed_false_when_partial_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    """``coveragePassed`` is False when certification coverage is partial."""
    monkeypatch.setenv("CONFORMANCE_TOOL_VERSION", "1.0.0")
    block = build_smoke_check_result(
        "env",
        [StepResult(name="m1", status="passed", message="ok", mandatory=True)],
        started_at=datetime.now(UTC),
        approved_release_policy=_approved_policy("1.0.0"),
        certification_coverage="partial",
    ).to_json_object()["certificationEligibility"]
    eligibility = cast(JsonObject, block)

    assert eligibility["coveragePassed"] is False


@pytest.mark.unit
def test_coverage_passed_false_when_mandatory_step_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """``coveragePassed`` is False when any mandatory step fails."""
    monkeypatch.setenv("CONFORMANCE_TOOL_VERSION", "1.0.0")
    block = build_smoke_check_result(
        "env",
        [StepResult(name="m1", status="failed", message="boom", mandatory=True)],
        started_at=datetime.now(UTC),
        approved_release_policy=_approved_policy("1.0.0"),
        certification_coverage="complete",
    ).to_json_object()["certificationEligibility"]
    eligibility = cast(JsonObject, block)

    assert eligibility["coveragePassed"] is False


@pytest.mark.unit
def test_coverage_passed_false_when_mandatory_step_deselected(monkeypatch: pytest.MonkeyPatch) -> None:
    """``coveragePassed`` is False when a mandatory step is deselected from the plan."""
    monkeypatch.setenv("CONFORMANCE_TOOL_VERSION", "1.0.0")
    plan = TestPlan(entries=(TestPlanEntry(step_id="m", mandatory=True, optional=False, selected=False),))
    block = build_smoke_check_result(
        "env",
        [],
        started_at=datetime.now(UTC),
        plan=plan,
        approved_release_policy=_approved_policy("1.0.0"),
        certification_coverage="complete",
    ).to_json_object()["certificationEligibility"]
    eligibility = cast(JsonObject, block)

    assert eligibility["coveragePassed"] is False


@pytest.mark.unit
def test_both_gates_fail_independently_when_custom_values_and_partial_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both ``valuePurityPassed`` and ``coveragePassed`` report False independently.

    When a run uses custom test values *and* the manifest has partial coverage,
    both gates block certification.  Each gate's status is independently visible
    so callers can distinguish which criterion requires remediation.
    """
    monkeypatch.setenv("CONFORMANCE_TOOL_VERSION", "1.0.0")
    block = build_smoke_check_result(
        "env",
        [StepResult(name="m1", status="passed", message="ok", mandatory=True)],
        started_at=datetime.now(UTC),
        custom_test_values_active=True,
        approved_release_policy=_approved_policy("1.0.0"),
        certification_coverage="partial",
    ).to_json_object()["certificationEligibility"]
    eligibility = cast(JsonObject, block)

    assert eligibility["eligible"] is False
    assert eligibility["valuePurityPassed"] is False
    assert eligibility["coveragePassed"] is False


@pytest.mark.unit
def test_coverage_passed_true_value_purity_false_blocks_overall_eligibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Value-purity gate alone can block eligibility when coverage gate passes."""
    monkeypatch.setenv("CONFORMANCE_TOOL_VERSION", "1.0.0")
    block = build_smoke_check_result(
        "env",
        [StepResult(name="m1", status="passed", message="ok", mandatory=True)],
        started_at=datetime.now(UTC),
        custom_test_values_active=True,
        approved_release_policy=_approved_policy("1.0.0"),
        certification_coverage="complete",
    ).to_json_object()["certificationEligibility"]
    eligibility = cast(JsonObject, block)

    assert eligibility["eligible"] is False
    assert eligibility["valuePurityPassed"] is False
    assert eligibility["coveragePassed"] is True


@pytest.mark.unit
def test_value_purity_true_coverage_false_blocks_overall_eligibility(monkeypatch: pytest.MonkeyPatch) -> None:
    """Coverage gate alone can block eligibility when value-purity gate passes."""
    monkeypatch.setenv("CONFORMANCE_TOOL_VERSION", "1.0.0")
    block = build_smoke_check_result(
        "env",
        [StepResult(name="m1", status="passed", message="ok", mandatory=True)],
        started_at=datetime.now(UTC),
        approved_release_policy=_approved_policy("1.0.0"),
        certification_coverage="partial",
    ).to_json_object()["certificationEligibility"]
    eligibility = cast(JsonObject, block)

    assert eligibility["eligible"] is False
    assert eligibility["valuePurityPassed"] is True
    assert eligibility["coveragePassed"] is False


@pytest.mark.unit
def test_step_result_includes_consumed_and_customised_test_value_key_evidence() -> None:
    rendered = StepResult(
        name="payment-consent",
        status="passed",
        message="ok",
        consumed_test_value_keys=("paymentAmount", "creditorName"),
        customised_test_value_keys=("paymentAmount",),
    ).to_json_object()
    details = cast(JsonObject, rendered["details"])

    assert details["consumedTestValueKeys"] == ["paymentAmount", "creditorName"]
    assert details["customisedTestValueKeys"] == ["paymentAmount"]


@pytest.mark.unit
def test_step_result_omits_consumed_and_customised_key_evidence_when_empty() -> None:
    rendered = StepResult(
        name="payment-consent",
        status="passed",
        message="ok",
        details={"assertions": []},
    ).to_json_object()
    details = cast(JsonObject, rendered["details"])

    assert "consumedTestValueKeys" not in details
    assert "customisedTestValueKeys" not in details
