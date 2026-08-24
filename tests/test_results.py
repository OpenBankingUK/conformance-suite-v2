from datetime import UTC, datetime
from types import MappingProxyType
from typing import cast

import pytest

from conformance.approved_releases import APPROVED_RELEASE_POLICY_SCHEMA_VERSION, ApprovedReleasePolicy
from conformance.catalogue import (
    AssertionOverride,
    CatalogueAssertion,
    CatalogueKey,
    CatalogueRequestStep,
    CatalogueTestCase,
    CompiledTestPlan,
    EndpointCapability,
    EndpointRef,
    ImplementedEndpoint,
    RuntimeInputRequirement,
    SecurityProfileApplicability,
    TestCaseApplicability,
    TestCatalogue,
    TestPlanSpec,
    compile_test_plan,
)
from conformance.json_types import JsonObject
from conformance.results import CheckStatus, StepResult, build_smoke_check_result


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
def test_catalogue_result_serializes_capability_traceability_and_safe_runtime_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compiled catalogue result evidence includes capability traceability without secrets."""
    from conformance.version import CONFORMANCE_TOOL_VERSION_ENV

    monkeypatch.setenv(CONFORMANCE_TOOL_VERSION_ENV, "1.0.0")
    compiled_plan = _compiled_capability_plan()

    rendered = build_smoke_check_result(
        [StepResult(name="accounts-balances-request", status="passed", message="ok", mandatory=True)],
        started_at=datetime.now(UTC),
        approved_release_policy=_approved_policy("1.0.0"),
        certification_coverage="complete",
        compiled_plan=compiled_plan,
        non_certifying_reasons=compiled_plan.traceability.non_certifying_reasons,
    ).to_json_object()

    catalogue = rendered["catalogue"]
    eligibility = rendered["certificationEligibility"]
    assert isinstance(catalogue, dict)
    assert isinstance(eligibility, dict)
    assert catalogue["selectedEndpoints"] == [
        {
            "method": "GET",
            "path": "/open-banking/v4.0/aisp/accounts",
            "resourceGroup": "Accounts",
            "capabilities": ["accounts.balances"],
        }
    ]
    selected_capabilities = cast("list[JsonObject]", catalogue["selectedCapabilities"])
    assert [capability["capabilityId"] for capability in selected_capabilities] == [
        "accounts.read",
        "accounts.balances",
    ]
    applicability_decisions = cast("list[JsonObject]", catalogue["applicabilityDecisions"])
    assert applicability_decisions[0]["reason"] == (
        "applicable to selected profile, implemented endpoints, and selected capabilities"
    )
    runtime_snapshot = cast("list[JsonObject]", catalogue["runtimeInputSnapshot"])
    assert runtime_snapshot[0]["value"] == "https://resource.example.com"
    assert "value" not in runtime_snapshot[1]
    assert catalogue["nonCertifyingReasons"] == [
        "Assertion override supplied for accounts-balances.status-200: diagnostic import"
    ]
    assert eligibility["eligible"] is False
    assert eligibility["reason"] == "Assertion override supplied for accounts-balances.status-200: diagnostic import"


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
    block = build_smoke_check_result(steps, started_at=started).to_json_object()["certificationEligibility"]
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
    block = build_smoke_check_result(steps, started_at=started).to_json_object()["certificationEligibility"]
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
    block = build_smoke_check_result(steps, started_at=started).to_json_object()["certificationEligibility"]
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

    block = build_smoke_check_result([], started_at=started, plan=plan).to_json_object()["certificationEligibility"]
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


@pytest.mark.unit
def test_v4_ais_slice_eligibility_counts_warn_failed_and_skipped_mandatory_steps() -> None:
    """Mandatory accounting remains stable for mixed manifest step outcomes."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result

    status_by_step: dict[str, CheckStatus] = {
        "openid-discovery": "passed",
        "jwks-fetch": "warn",
        "client-credentials-token": "passed",
        "account-access-consent": "passed",
        "psu-authorization": "passed",
        "token-exchange": "passed",
        "accounts-list": "passed",
        "account-balances": "failed",
        "account-transactions": "skipped",
    }
    steps = [
        StepResult(
            name=step_id,
            status=status,
            message=step_id,
            mandatory=True,
        )
        for step_id, status in status_by_step.items()
    ]

    block = build_smoke_check_result(
        steps,
        started_at=datetime.now(UTC),
        certification_coverage="partial",
    ).to_json_object()["certificationEligibility"]

    assert isinstance(block, dict)
    assert block["eligible"] is False
    assert block["mandatoryTotal"] == 9
    assert block["mandatoryPassed"] == 6
    assert block["mandatoryWarn"] == 1
    assert block["mandatoryFailed"] == 1
    assert block["mandatorySkipped"] == 1
    reasons = block["reasons"]
    assert isinstance(reasons, list)
    assert "1 mandatory step(s) failed" in reasons
    assert "1 mandatory step(s) skipped due to earlier failures" in reasons
    assert "Manifest is not marked as complete certification coverage" in reasons
    assert "Approved-release policy was not supplied" in reasons


@pytest.mark.unit
def test_v4_ais_baseline_remains_ineligible_while_manifest_coverage_is_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing mandatory manifest steps still cannot certify while coverage is partial."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result
    from conformance.version import CONFORMANCE_TOOL_VERSION_ENV

    monkeypatch.setenv(CONFORMANCE_TOOL_VERSION_ENV, "1.0.0")
    steps = [
        StepResult(name=f"mandatory-{index}", status="passed", message="passed", mandatory=True) for index in range(11)
    ]

    rendered = build_smoke_check_result(
        steps,
        started_at=datetime.now(UTC),
        approved_release_policy=_approved_policy("1.0.0"),
        certification_coverage="partial",
    ).to_json_object()

    block = rendered["certificationEligibility"]
    assert isinstance(block, dict)
    assert block["eligible"] is False
    assert block["mandatoryTotal"] == 11
    assert block["mandatoryPassed"] == 11
    assert block["reason"] == "Manifest is not marked as complete certification coverage"
    assert block["reasons"] == ["Manifest is not marked as complete certification coverage"]
    assert "suite" not in rendered


def _compiled_capability_plan() -> CompiledTestPlan:
    """Build a compiled catalogue plan with required and optional capabilities.

    Returns:
        Compiled test plan with endpoint capability traceability and a
        non-certifying assertion override.
    """
    endpoint = EndpointRef(method="GET", path="/open-banking/v4.0/aisp/accounts")
    catalogue = TestCatalogue(
        key=CatalogueKey(standard="open-banking", version="v4.0", api="ais"),
        catalogue_version="test.capabilities.1",
        capabilities=(
            EndpointCapability(
                capability_id="accounts.read",
                label="Read accounts",
                description="Required account-list baseline capability.",
                required=True,
                endpoint_refs=(endpoint,),
            ),
            EndpointCapability(
                capability_id="accounts.balances",
                label="Read account balances",
                description="Optional balances capability for accounts.",
                required=False,
                endpoint_refs=(endpoint,),
            ),
        ),
        test_cases=(
            CatalogueTestCase(
                test_case_id="accounts-balances",
                name="Read account balances",
                role="resource",
                compliance_scope=("legacy-fcs-script:accounts-balances",),
                applicability=TestCaseApplicability(
                    security_profiles=SecurityProfileApplicability(profiles=("all",)),
                    endpoint_refs=(endpoint,),
                    required_capability_ids=("accounts.read", "accounts.balances"),
                ),
                mandatory=True,
                runtime_input_requirements=(
                    RuntimeInputRequirement("resourceBaseUrl", "url", "Resource base URL"),
                    RuntimeInputRequirement("accessToken", "string", "Access token", sensitive=True),
                ),
                request_steps=(
                    CatalogueRequestStep(
                        step_id="accounts-balances-request",
                        name="GET account balances",
                        method="GET",
                        path="/open-banking/v4.0/aisp/accounts",
                        runtime_input_refs=("resourceBaseUrl", "accessToken"),
                    ),
                ),
                assertions=(CatalogueAssertion("status-200", "http_status", "HTTP 200", {"expected": 200}),),
            ),
        ),
    )
    return compile_test_plan(
        catalogue,
        TestPlanSpec(
            schema_version="v1",
            catalogue_key=catalogue.key,
            security_profile="fapi1-advanced",
            implemented_endpoints=(
                ImplementedEndpoint(
                    method="GET",
                    path="/open-banking/v4.0/aisp/accounts",
                    resource_group="Accounts",
                    capability_ids=("accounts.balances",),
                ),
            ),
            runtime_inputs={
                "resourceBaseUrl": "https://resource.example.com",
                "accessToken": "secret-access-token",
            },
            assertion_overrides=(
                AssertionOverride(
                    test_case_id="accounts-balances",
                    assertion_id="status-200",
                    reason="diagnostic import",
                ),
            ),
        ),
    )


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
        [StepResult(name="x", status="passed", message="ok")],
        started_at=started,
    ).to_json_object()
    assert "plan" not in rendered


@pytest.mark.unit
def test_suite_metadata_absent_when_not_supplied() -> None:
    """Generated reports omit the removed legacy suite metadata block."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result

    started = datetime.now(UTC)
    rendered = build_smoke_check_result(
        [StepResult(name="x", status="passed", message="ok")],
        started_at=started,
    ).to_json_object()

    assert "suite" not in rendered


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
            steps,
            started_at=started,
            approved_release_policy=_approved_policy("1.0.0"),
            certification_coverage=manifest.certification_coverage,
        ).to_json_object()["certificationEligibility"]

        assert isinstance(block, dict)
        assert block["eligible"] is False, f"Smoke suite manifest {manifest_file.name} must not yield eligible=True"
        assert "Manifest is not marked as complete certification coverage" in cast(list[str], block["reasons"])
