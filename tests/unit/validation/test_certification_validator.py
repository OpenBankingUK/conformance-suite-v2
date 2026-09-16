"""Independent certification-validator unit coverage."""

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from conformance.certification_validator import (
    CertificationValidationError,
    CertificationValidationResult,
    parse_submitted_report,
    render_confluence_summary,
    validate_certification_report,
    validate_report,
)
from conformance.configuration_contracts.models import StableId
from conformance.configuration_contracts.v2_loader import (
    dump_execution_manifest,
    dump_resolved_plan,
)
from conformance.configuration_contracts.v2_models import ExecutionManifest, ResolvedPlan
from conformance.json_types import JsonObject
from conformance.results import CheckStatus
from tests.support.certification import (
    RELEASE_PATH,
    CertificationFixture,
    build_certification_fixture,
    report_with_statuses,
)
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.unit


@pytest.fixture
def certification_fixture() -> CertificationFixture:
    return build_certification_fixture()


def test_complete_approved_run_reports_distinct_test_assessment_and_eligibility() -> None:
    fixture = build_certification_fixture(
        warning_step_id="dcr.v34.test.retrieval.positive.instance.request",
    )

    result = _validate(fixture)

    assert result.valid is True
    assert result.complete is True
    assert result.automated_assessment == "passed"
    assert result.eligible is True
    assert {item.status for item in result.test_outcomes} == {"passed", "warn"}
    rendered = result.to_json_object()
    assert rendered["schemaVersion"] == "2.0"
    assert cast(JsonObject, rendered["automatedAssessment"]) == {
        "status": "passed",
        "complete": True,
        "policyId": "obl.open-banking-mvp.execution-policy",
        "resultClaim": "approved-executable-tests-only",
    }
    assert cast(JsonObject, rendered["certificationEligibility"])["eligible"] is True
    assert render_confluence_summary(result).startswith("Certification report validation: PASS")


def test_rejects_tool_version_not_approved_by_suite_release(
    certification_fixture: CertificationFixture,
) -> None:
    report = deepcopy(certification_fixture.report)
    cast(JsonObject, report["tool"])["version"] = "99.0.0"

    result = _validate(certification_fixture, report=report)

    assert result.tool_version_approved is False
    assert result.eligible is False
    assert "tool_version_not_approved" in result.reasons


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("id", "unknown.suite.release"),
        ("version", "wrong-release-version"),
    ],
)
def test_rejects_unknown_or_wrong_suite_release_claim(
    certification_fixture: CertificationFixture,
    field: str,
    replacement: str,
) -> None:
    report = deepcopy(certification_fixture.report)
    release_claim = cast(JsonObject, cast(JsonObject, report["traceability"])["suiteRelease"])
    release_claim[field] = replacement

    result = _validate(certification_fixture, report=report)

    assert result.eligible is False
    assert "suite_release_mismatch" in result.reasons


def test_rejects_stale_resolved_plan_identity(certification_fixture: CertificationFixture) -> None:
    stale = replace(certification_fixture.resolved_plan, id=StableId("resolved-plan:" + ("0" * 64)))

    result = _validate(certification_fixture, resolved_plan=stale)

    assert "resolved_plan_identity_mismatch" in result.reasons
    assert "resolved_plan_mismatch" in result.reasons


def test_rejects_stale_execution_manifest_identity(certification_fixture: CertificationFixture) -> None:
    stale = replace(certification_fixture.manifest, id=StableId("execution-manifest:" + ("0" * 64)))

    result = _validate(certification_fixture, manifest=stale)

    assert "execution_manifest_identity_mismatch" in result.reasons
    assert "execution_manifest_mismatch" in result.reasons


def test_rejects_contradictory_resolved_plan_provenance(
    certification_fixture: CertificationFixture,
) -> None:
    contradictory = replace(
        certification_fixture.resolved_plan,
        provenance=replace(
            certification_fixture.resolved_plan.provenance,
            participant_plan_id=StableId("participant.substituted"),
        ),
    )

    result = _validate(certification_fixture, resolved_plan=contradictory)

    assert "resolved_plan_mismatch" in result.reasons


def test_rejects_contradictory_manifest_provenance(
    certification_fixture: CertificationFixture,
) -> None:
    contradictory = replace(
        certification_fixture.manifest,
        provenance=replace(
            certification_fixture.manifest.provenance,
            participant_plan_id=StableId("participant.substituted"),
        ),
    )

    result = _validate(certification_fixture, manifest=contradictory)

    assert "execution_manifest_mismatch" in result.reasons


def test_rejects_blocking_compiler_findings(certification_fixture: CertificationFixture) -> None:
    report = deepcopy(certification_fixture.report)
    snapshot = cast(JsonObject, cast(JsonObject, report["traceability"])["participantPlanSnapshot"])
    snapshot["selectedCapabilityIds"] = ["dcr.v34.capability.unknown"]

    result = _validate(certification_fixture, report=report)

    assert result.eligible is False
    assert "blocking_compiler_finding" in result.reasons
    assert "selection_invalid" in result.reasons
    assert result.test_outcomes == ()
    assert result.automated_assessment == "incomplete"
    assert result.complete is False


def test_rejects_missing_applicable_test_in_resolved_plan(
    certification_fixture: CertificationFixture,
) -> None:
    incomplete = replace(
        certification_fixture.resolved_plan,
        test_instances=certification_fixture.resolved_plan.test_instances[:-1],
    )

    result = _validate(certification_fixture, resolved_plan=incomplete)

    assert result.eligible is False
    assert "resolved_plan_mismatch" in result.reasons


def test_rejects_unknown_test_definition_trace(certification_fixture: CertificationFixture) -> None:
    report = deepcopy(certification_fixture.report)
    traceability = cast(JsonObject, report["traceability"])
    definitions = cast(list[JsonObject], traceability["testDefinitions"])
    definitions.append({"id": "dcr.v34.test.unknown"})

    result = _validate(certification_fixture, report=report)

    assert "result_test_definitions_mismatch" in result.reasons


def test_rejects_unknown_test_instance_trace(certification_fixture: CertificationFixture) -> None:
    report = deepcopy(certification_fixture.report)
    traceability = cast(JsonObject, report["traceability"])
    instances = cast(list[JsonObject], traceability["compiledTestInstances"])
    instances[0]["id"] = "dcr.v34.test.unknown.instance"

    result = _validate(certification_fixture, report=report)

    assert "result_test_instances_mismatch" in result.reasons


def test_rejects_unknown_step_and_observation(certification_fixture: CertificationFixture) -> None:
    report = deepcopy(certification_fixture.report)
    steps = cast(list[JsonObject], report["steps"])
    steps.append(
        {
            "name": "dcr.v34.test.unknown.instance.request",
            "status": "passed",
            "message": "not executed",
            "details": {"assertions": []},
        }
    )
    traceability = cast(JsonObject, report["traceability"])
    manifest_trace = cast(JsonObject, traceability["executionManifest"])
    trace_steps = cast(list[JsonObject], manifest_trace["steps"])
    trace_steps.append(
        {
            "id": "dcr.v34.test.unknown.instance.request",
            "testInstanceId": "dcr.v34.test.unknown.instance",
            "testDefinitionId": "dcr.v34.test.unknown",
            "assertionIds": [],
            "resultObservationId": "dcr.v34.test.unknown.instance.request",
            "resultStatus": "passed",
        }
    )

    result = _validate(certification_fixture, report=report)

    assert "unknown_observation:dcr.v34.test.unknown.instance.request" in result.reasons
    assert "unknown_trace_step:dcr.v34.test.unknown.instance.request" in result.reasons


def test_rejects_unknown_assertion_observation(certification_fixture: CertificationFixture) -> None:
    report = deepcopy(certification_fixture.report)
    first_step = cast(list[JsonObject], report["steps"])[0]
    assertions = cast(list[JsonObject], cast(JsonObject, first_step["details"])["assertions"])
    assertions.append({"assertionId": "dcr.v34.assertion.unknown", "status": "passed"})

    result = _validate(certification_fixture, report=report)

    step_id = cast(str, first_step["name"])
    assert f"unknown_assertion:{step_id}/dcr.v34.assertion.unknown" in result.reasons


def test_rejects_missing_observation_and_success_claim(certification_fixture: CertificationFixture) -> None:
    report = deepcopy(certification_fixture.report)
    removed = cast(list[JsonObject], report["steps"]).pop()

    result = _validate(certification_fixture, report=report)

    assert f"missing_observation:{removed['name']}" in result.reasons
    assert result.automated_assessment == "incomplete"
    assert result.eligible is False
    assert "approved_test_incomplete" in result.reasons
    assert "runner_eligibility_contradiction" in result.reasons


def test_rejects_missing_assertion_observation(certification_fixture: CertificationFixture) -> None:
    report = deepcopy(certification_fixture.report)
    first_step = cast(list[JsonObject], report["steps"])[0]
    assertions = cast(list[JsonObject], cast(JsonObject, first_step["details"])["assertions"])
    removed = assertions.pop()

    result = _validate(certification_fixture, report=report)

    assert f"missing_assertion_observation:{first_step['name']}/{removed['assertionId']}" in result.reasons
    assert result.automated_assessment == "incomplete"


def test_rejects_skipped_required_work(certification_fixture: CertificationFixture) -> None:
    step_id = str(certification_fixture.manifest.steps[0].id)
    report = report_with_statuses(certification_fixture, {step_id: "skipped"})

    result = _validate(certification_fixture, report=report)

    assert f"skipped_required_work:{step_id}" in result.reasons
    assert result.automated_assessment == "incomplete"
    assert result.eligible is False


def test_rejects_observation_for_work_after_failed_prerequisite(
    certification_fixture: CertificationFixture,
) -> None:
    dependant = next(step for step in certification_fixture.manifest.steps if step.dependency_ids)
    prerequisite_id = str(dependant.dependency_ids[0])
    report = report_with_statuses(certification_fixture, {prerequisite_id: "failed"})

    result = _validate(certification_fixture, report=report)

    assert f"observation_for_unexecuted_work:{dependant.id!s}" in result.reasons
    assert result.eligible is False


def test_failed_prerequisite_and_skipped_dependants_are_ineligible(
    certification_fixture: CertificationFixture,
) -> None:
    prerequisite_id = str(certification_fixture.manifest.steps[0].id)
    statuses: dict[str, CheckStatus] = {
        str(step.id): ("failed" if str(step.id) == prerequisite_id else "skipped")
        for step in certification_fixture.manifest.steps
    }
    report = report_with_statuses(certification_fixture, statuses)

    result = _validate(certification_fixture, report=report)

    assert result.automated_assessment == "incomplete"
    assert result.eligible is False
    assert "approved_test_incomplete" in result.reasons


def test_rejects_contradictory_step_and_assertion_outcomes(
    certification_fixture: CertificationFixture,
) -> None:
    report = deepcopy(certification_fixture.report)
    first_step = cast(list[JsonObject], report["steps"])[0]
    first_assertion = cast(list[JsonObject], cast(JsonObject, first_step["details"])["assertions"])[0]
    first_assertion["status"] = "failed"

    result = _validate(certification_fixture, report=report)

    assert f"contradictory_outcome:{first_step['name']}" in result.reasons
    assert result.automated_assessment == "failed"


def test_rejects_runner_eligibility_claim_that_contradicts_evidence(
    certification_fixture: CertificationFixture,
) -> None:
    step_id = str(certification_fixture.manifest.steps[0].id)
    report = report_with_statuses(certification_fixture, {step_id: "failed"})
    cast(JsonObject, report["certificationEligibility"])["eligible"] = True

    result = _validate(certification_fixture, report=report)

    assert "runner_eligibility_contradiction" in result.reasons
    assert result.eligible is False


def test_legacy_runner_ineligibility_claim_does_not_override_approved_v2_evidence(
    certification_fixture: CertificationFixture,
) -> None:
    report = deepcopy(certification_fixture.report)
    cast(JsonObject, report["certificationEligibility"])["eligible"] = False

    result = _validate(certification_fixture, report=report)

    assert result.valid is True
    assert result.eligible is True
    assert "runner_eligibility_contradiction" not in result.reasons


def test_rejects_runner_summary_claim_that_contradicts_observations(
    certification_fixture: CertificationFixture,
) -> None:
    report = deepcopy(certification_fixture.report)
    cast(JsonObject, report["summary"])["passed"] = 0

    result = _validate(certification_fixture, report=report)

    assert "result_summary_contradiction" in result.reasons
    assert result.eligible is False


def test_parse_report_requires_v2_traceability(certification_fixture: CertificationFixture) -> None:
    report = deepcopy(certification_fixture.report)
    del report["traceability"]

    with pytest.raises(CertificationValidationError, match=r"report\.traceability is required"):
        parse_submitted_report(report)


def test_parse_report_rejects_non_v2_participant_snapshot(
    certification_fixture: CertificationFixture,
) -> None:
    report = deepcopy(certification_fixture.report)
    snapshot = cast(JsonObject, cast(JsonObject, report["traceability"])["participantPlanSnapshot"])
    snapshot["schemaVersion"] = "1.0"

    with pytest.raises(CertificationValidationError, match="schema-version 2.0 participant plan"):
        parse_submitted_report(report)


@pytest.mark.parametrize("artifact_kind", ["test-definition-catalogue", "technical-source"])
def test_path_validation_rejects_substituted_release_artifact(
    certification_fixture: CertificationFixture,
    artifact_kind: str,
    tmp_path: Path,
) -> None:
    trusted_root = tmp_path / "trusted"
    shutil.copytree(REPO_ROOT / "conformance", trusted_root / "conformance")
    reference = next(
        item
        for item in certification_fixture.release.artifacts
        if item.kind == artifact_kind
        and (
            item.id == certification_fixture.catalogue.id
            or item.id in {source.id for source in certification_fixture.catalogue.technical_sources}
        )
    )
    substituted = trusted_root / reference.uri
    substituted.write_bytes(substituted.read_bytes() + b"\n")
    report_path, resolved_path, manifest_path = _write_submitted_files(certification_fixture, tmp_path)
    release_path = trusted_root / RELEASE_PATH.relative_to(REPO_ROOT)

    with pytest.raises(CertificationValidationError, match="digest does not match"):
        validate_certification_report(
            report_path,
            suite_release_path=release_path,
            resolved_plan_path=resolved_path,
            manifest_path=manifest_path,
            trusted_root=trusted_root,
        )


def _validate(
    fixture: CertificationFixture,
    *,
    report: JsonObject | None = None,
    resolved_plan: ResolvedPlan | None = None,
    manifest: ExecutionManifest | None = None,
) -> CertificationValidationResult:
    return validate_report(
        report=parse_submitted_report(report or fixture.report),
        suite_release=fixture.release,
        catalogue=fixture.catalogue,
        policy=fixture.policy,
        resolved_plan=resolved_plan or fixture.resolved_plan,
        manifest=manifest or fixture.manifest,
    )


def _write_submitted_files(
    fixture: CertificationFixture,
    root: Path,
) -> tuple[Path, Path, Path]:
    report_path = root / "report.json"
    resolved_path = root / "resolved-plan.json"
    manifest_path = root / "execution-manifest.json"
    report_path.write_text(json.dumps(fixture.report), encoding="utf-8")
    resolved_path.write_text(dump_resolved_plan(fixture.resolved_plan), encoding="utf-8")
    manifest_path.write_text(dump_execution_manifest(fixture.manifest), encoding="utf-8")
    return report_path, resolved_path, manifest_path
