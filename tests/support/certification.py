"""Deterministic schema-version 2.0 certification test fixtures."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from conformance.approved_releases import ApprovedReleasePolicy
from conformance.configuration_contracts.models import StableId, StandingOrderFrequency, SuiteRelease
from conformance.configuration_contracts.v2_compiler import compile_participant_plan
from conformance.configuration_contracts.v2_execution_manifest import generate_execution_manifest
from conformance.configuration_contracts.v2_loader import (
    load_suite_release,
    load_test_definition_catalogue,
    parse_participant_plan,
    parse_suite_policy,
)
from conformance.configuration_contracts.v2_models import (
    ExecutionManifest,
    ExecutionManifestStep,
    ParticipantPlan,
    ResolvedPlan,
    SuitePolicy,
    TestDefinitionCatalogue,
)
from conformance.json_types import JsonObject, JsonValue
from conformance.results import (
    CheckStatus,
    ResultTraceabilitySource,
    StepResult,
    build_safe_participant_plan_snapshot,
    build_smoke_check_result,
)
from tests.support.paths import REPO_ROOT

CONFIGURATION_ROOT = REPO_ROOT / "conformance" / "configuration_contracts"
RELEASE_PATH = CONFIGURATION_ROOT / "bundles" / "open-banking-mvp" / "suite-release.json"
POLICY_PATH = CONFIGURATION_ROOT / "policies" / "open-banking-mvp-execution-policy.json"
DCR_CATALOGUE_PATH = CONFIGURATION_ROOT / "catalogues" / "dcr" / "v3_4" / "test-catalogue.v2.json"


@dataclass(frozen=True, slots=True)
class CertificationFixture:
    """Trusted inputs and a complete matching submitted report."""

    release: SuiteRelease
    catalogue: TestDefinitionCatalogue
    policy: SuitePolicy
    participant_plan: ParticipantPlan
    resolved_plan: ResolvedPlan
    manifest: ExecutionManifest
    report: JsonObject


def build_certification_fixture(
    *,
    capability_id: str = "dcr.v34.capability.retrieval",
    warning_step_id: str | None = None,
) -> CertificationFixture:
    """Build a complete approved run from shipped v2 contracts."""
    release = load_suite_release(RELEASE_PATH)
    catalogue = load_test_definition_catalogue(DCR_CATALOGUE_PATH)
    participant_plan = parse_participant_plan(
        _participant_document(
            release,
            catalogue,
            capability_id=capability_id,
        )
    )
    resolved_plan = compile_participant_plan(release, catalogue, participant_plan)
    manifest = generate_execution_manifest(resolved_plan, catalogue)
    steps = tuple(
        _step_result(
            step,
            status=("warn" if str(step.id) == warning_step_id else "passed"),
        )
        for step in manifest.steps
    )
    result = build_smoke_check_result(
        list(steps),
        started_at=datetime(2026, 9, 16, tzinfo=UTC),
        approved_release_policy=ApprovedReleasePolicy(
            schema_version="v1",
            approved_tool_versions=(release.tool_releases[0].version,),
        ),
        certification_coverage="complete",
        result_traceability=ResultTraceabilitySource(
            execution_manifest=manifest,
            resolved_plan=resolved_plan,
            participant_plan_snapshot=build_safe_participant_plan_snapshot(participant_plan, catalogue),
        ),
    ).to_json_object()
    cast(JsonObject, result["tool"])["version"] = release.tool_releases[0].version
    cast(JsonObject, result["certificationEligibility"])["eligible"] = True
    return CertificationFixture(
        release=release,
        catalogue=catalogue,
        policy=parse_suite_policy(_load_json(POLICY_PATH)),
        participant_plan=participant_plan,
        resolved_plan=resolved_plan,
        manifest=manifest,
        report=result,
    )


def report_with_statuses(
    fixture: CertificationFixture,
    statuses: Mapping[str, CheckStatus],
) -> JsonObject:
    """Rebuild a fixture report with selected observation statuses."""
    steps = tuple(_step_result(step, status=statuses.get(str(step.id), "passed")) for step in fixture.manifest.steps)
    result = build_smoke_check_result(
        list(steps),
        started_at=datetime(2026, 9, 16, tzinfo=UTC),
        approved_release_policy=ApprovedReleasePolicy(
            schema_version="v1",
            approved_tool_versions=(fixture.release.tool_releases[0].version,),
        ),
        certification_coverage="complete",
        result_traceability=ResultTraceabilitySource(
            execution_manifest=fixture.manifest,
            resolved_plan=fixture.resolved_plan,
            participant_plan_snapshot=build_safe_participant_plan_snapshot(
                fixture.participant_plan,
                fixture.catalogue,
            ),
        ),
    ).to_json_object()
    cast(JsonObject, result["tool"])["version"] = fixture.release.tool_releases[0].version
    independently_passing = all(status in {"passed", "warn"} for status in statuses.values())
    cast(JsonObject, result["certificationEligibility"])["eligible"] = independently_passing
    return result


def _participant_document(
    release: SuiteRelease,
    catalogue: TestDefinitionCatalogue,
    *,
    capability_id: str,
) -> JsonObject:
    selected = StableId(capability_id)
    required = {selected}
    pending = [selected]
    capabilities = {item.id: item for item in catalogue.capabilities}
    while pending:
        for dependency in capabilities[pending.pop()].required_capability_ids:
            if dependency not in required:
                required.add(dependency)
                pending.append(dependency)
    inputs: list[JsonValue] = []
    for item in catalogue.predefined_inputs:
        if required.intersection(item.required_for_capability_ids):
            inputs.append({"inputId": str(item.id), "value": _json_value(item.example_value)})
    return {
        "documentType": "participant-plan",
        "id": "participant.certification-validator",
        "predefinedInputs": inputs,
        "schemaVersion": "2.0",
        "scheme": str(catalogue.scheme),
        "securityProfile": "all",
        "selectedCapabilityIds": [capability_id],
        "specification": {
            "id": str(catalogue.specification.id),
            "testScope": str(catalogue.specification.test_scope),
            "version": catalogue.specification.version,
        },
        "suiteReleaseId": str(release.id),
    }


def _step_result(step: ExecutionManifestStep, *, status: CheckStatus) -> StepResult:
    assertions: list[JsonValue] = []
    for index, assertion in enumerate(step.assertions):
        assertion_status = "failed" if status == "failed" and index == 0 else "passed"
        assertions.append(
            {
                "assertionId": str(assertion.id),
                "status": assertion_status,
                "message": assertion_status,
            }
        )
    if status == "skipped":
        assertions = []
    return StepResult(
        name=str(step.id),
        status=status,
        message=status,
        mandatory=True,
        details={"assertions": assertions},
    )


def _json_value(value: object) -> JsonValue:
    if isinstance(value, StandingOrderFrequency):
        return {
            "frequencyType": value.frequency_type,
            **({"countPerPeriod": value.count_per_period} if value.count_per_period is not None else {}),
            **({"pointInTime": value.point_in_time} if value.point_in_time is not None else {}),
        }
    return cast(JsonValue, value)


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))
