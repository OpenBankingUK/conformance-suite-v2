"""Tests for generated execution manifests and compatibility bindings."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from typing import cast

import pytest

from conformance.configuration_contracts import (
    ConfigurationContractError,
    DiagnosticCode,
    ExecutionManifestGenerationError,
    compile_participant_plan,
    dump_execution_manifest,
    execution_manifest_to_document,
    generate_execution_manifest,
    load_execution_manifest,
    load_participant_plan,
    load_requirements_catalogue,
    load_suite_release,
    load_test_definition_catalogue,
    parse_execution_manifest,
)
from conformance.configuration_contracts.models import (
    CompilationFinding,
    FindingSeverity,
    FindingSourceDocument,
    ParticipantPlan,
    RequirementsCatalogue,
    ResolvedPlan,
    StableId,
    SuiteRelease,
)
from conformance.configuration_contracts.models import (
    TestDefinitionCatalogue as ConfigurationTestDefinitionCatalogue,
)
from conformance.json_types import JsonObject
from conformance.results import (
    ResultTraceabilitySource,
    StepResult,
    build_safe_participant_plan_snapshot,
    build_smoke_check_result,
)
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.unit

_BUNDLE_ROOT = REPO_ROOT / "conformance" / "configuration_contracts" / "bundles" / "pis-domestic-standing-order-v4_0"
_FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "v1"
_EXECUTION_MANIFEST_PATH = _FIXTURE_ROOT / "execution-manifest.valid.json"


def test_resolved_plan_generates_deterministic_immutable_execution_manifest() -> None:
    _suite, requirements, test_definitions, _participant_plan, resolved = _resolved_inputs()

    first = generate_execution_manifest(resolved, requirements, test_definitions)
    second = generate_execution_manifest(resolved, requirements, test_definitions)

    assert first == second
    assert dump_execution_manifest(first) == _EXECUTION_MANIFEST_PATH.read_text(encoding="utf-8")
    assert load_execution_manifest(_EXECUTION_MANIFEST_PATH) == first
    assert first.provenance.resolved_plan_id == resolved.id
    assert [step.test_instance_id for step in first.steps] == [
        test_instance.id for test_instance in resolved.test_instances
    ]
    assert first.steps[-1].dependency_ids == ("pis.dso.test.order-create.instance.request",)
    assert first.inputs[0].value is not None
    assert not isinstance(first.inputs[0].value, str)
    assert first.inputs[0].value.point_in_time == "03"
    with pytest.raises(FrozenInstanceError):
        _set_attribute(first.steps[0], "name", "changed")


@pytest.mark.parametrize(
    ("fixture_name", "expected_code", "expected_path"),
    [
        (
            "execution-manifest.invalid-unknown-property.json",
            DiagnosticCode.SCHEMA_VALIDATION_FAILED,
            "/participantOverrides",
        ),
        (
            "execution-manifest.invalid-reference.json",
            DiagnosticCode.REFERENCE_UNRESOLVED,
            "/steps/1/dependencyIds/0",
        ),
        (
            "execution-manifest.invalid-id.json",
            DiagnosticCode.EXECUTION_MANIFEST_INCONSISTENT,
            "/id",
        ),
    ],
)
def test_invalid_execution_manifest_fixtures_have_stable_diagnostics(
    fixture_name: str,
    expected_code: DiagnosticCode,
    expected_path: str,
) -> None:
    with pytest.raises(ConfigurationContractError) as captured:
        load_execution_manifest(_FIXTURE_ROOT / fixture_name)

    assert captured.value.diagnostics[0].code is expected_code
    assert captured.value.diagnostics[0].instance_path == expected_path


def test_execution_manifest_rejects_participant_override_syntax() -> None:
    raw_document: object = json.loads(_EXECUTION_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_document, dict)
    raw_document["requestOverrides"] = []

    with pytest.raises(ConfigurationContractError) as captured:
        parse_execution_manifest(raw_document)

    assert captured.value.diagnostics[0].code is DiagnosticCode.SCHEMA_VALIDATION_FAILED
    assert captured.value.diagnostics[0].instance_path == "/requestOverrides"


def test_generation_references_redacted_runtime_input_without_copying_its_value() -> None:
    _suite, requirements, test_definitions, _participant_plan, resolved = _resolved_inputs()
    redacted_input = replace(resolved.predefined_inputs[0], value=None, redacted=True)

    manifest = generate_execution_manifest(
        replace(resolved, predefined_inputs=(redacted_input,)),
        requirements,
        test_definitions,
    )

    assert manifest.inputs[0].redacted is True
    assert manifest.inputs[0].value is None
    serialized = execution_manifest_to_document(manifest)
    assert serialized["inputs"] == [
        {
            "id": "pis.dso.input.frequency",
            "redacted": True,
            "source": "participant",
        }
    ]


def test_generation_rejects_catalogue_bytes_not_bound_by_resolved_provenance() -> None:
    _suite, requirements, test_definitions, _participant_plan, resolved = _resolved_inputs()
    changed_definition = replace(test_definitions.test_definitions[0], name="Changed after resolution")

    with pytest.raises(ExecutionManifestGenerationError, match="does not match resolved-plan provenance"):
        generate_execution_manifest(
            resolved,
            requirements,
            replace(
                test_definitions,
                test_definitions=(changed_definition, *test_definitions.test_definitions[1:]),
            ),
        )


def test_generation_rejects_non_topological_resolved_work() -> None:
    _suite, requirements, test_definitions, _participant_plan, resolved = _resolved_inputs()
    first, second, *remaining = resolved.test_instances

    with pytest.raises(ExecutionManifestGenerationError, match="appears before dependencies"):
        generate_execution_manifest(
            replace(resolved, test_instances=(second, first, *remaining)),
            requirements,
            test_definitions,
        )


@pytest.mark.parametrize(("failed_step_index", "expected_status"), [(None, "passed"), (2, "failed")])
def test_result_traceability_connects_stable_ids_for_passed_and_failed_runs(
    failed_step_index: int | None,
    expected_status: str,
) -> None:
    _suite, requirements, test_definitions, participant_plan, resolved = _resolved_inputs()
    manifest = generate_execution_manifest(resolved, requirements, test_definitions)
    source = ResultTraceabilitySource(
        execution_manifest=manifest,
        resolved_plan=resolved,
        participant_plan_snapshot=build_safe_participant_plan_snapshot(participant_plan, requirements),
    )
    failed_observation_id = None if failed_step_index is None else str(manifest.steps[failed_step_index].id)
    observations = [
        StepResult(
            name=str(step.id),
            status="failed" if str(step.id) == failed_observation_id else "passed",
            message="request completed",
            mandatory=True,
        )
        for step in manifest.steps
    ]

    first = build_smoke_check_result(
        observations,
        started_at=datetime.now(UTC),
        result_traceability=source,
    ).to_json_object()
    second_traceability = build_smoke_check_result(
        observations,
        started_at=datetime.now(UTC),
        result_traceability=source,
    ).to_json_object()["traceability"]

    assert first["status"] == expected_status
    assert first["traceability"] == second_traceability
    traceability = cast(JsonObject, first["traceability"])
    assert traceability["suiteRelease"] == {
        "id": "obl.pis-dso-v4.walking-skeleton-suite",
        "version": "walking-skeleton.1",
        "publishedAt": "2026-09-15T08:57:48Z",
    }
    participant_snapshot = cast(JsonObject, traceability["participantPlanSnapshot"])
    assert participant_snapshot["id"] == "participant.pis-dso.example"
    assert participant_snapshot["predefinedInputs"] == [
        {
            "inputId": "pis.dso.input.frequency",
            "redacted": False,
            "value": {"frequencyType": "WEEK", "pointInTime": "03"},
        }
    ]
    assert "requirements" not in traceability
    assert "normativeReferenceIds" not in json.dumps(traceability)
    assert "coveredRequirementIds" not in json.dumps(traceability)
    assert [item["id"] for item in cast("list[JsonObject]", traceability["testDefinitions"])] == [
        test_instance.test_definition_id for test_instance in resolved.test_instances
    ]
    assert [item["id"] for item in cast("list[JsonObject]", traceability["compiledTestInstances"])] == [
        test_instance.id for test_instance in resolved.test_instances
    ]
    execution_manifest = cast(JsonObject, traceability["executionManifest"])
    manifest_steps = cast("list[JsonObject]", execution_manifest["steps"])
    assert [step["id"] for step in manifest_steps] == [step.id for step in manifest.steps]
    assert [step["resultObservationId"] for step in manifest_steps] == [step.id for step in manifest.steps]
    assert [step["resultStatus"] for step in manifest_steps] == [
        "failed" if step["resultObservationId"] == failed_observation_id else "passed" for step in manifest_steps
    ]
    assert "compatibilityObservations" not in traceability
    assert traceability["compilerFindings"] == []


def test_result_traceability_preserves_compiler_findings() -> None:
    _suite, requirements, test_definitions, participant_plan, resolved = _resolved_inputs()
    manifest = generate_execution_manifest(resolved, requirements, test_definitions)
    source = ResultTraceabilitySource(
        execution_manifest=manifest,
        resolved_plan=resolved,
        participant_plan_snapshot=build_safe_participant_plan_snapshot(participant_plan, requirements),
    )
    warning = CompilationFinding(
        code=StableId("compiler.policy.warning"),
        severity=FindingSeverity.WARNING,
        message="Illustrative retained warning",
        source_document=FindingSourceDocument.PARTICIPANT_PLAN,
        instance_path="/selectedCapabilityIds/0",
        related_ids=(StableId("pis.domestic-standing-order"),),
    )
    source = replace(source, resolved_plan=replace(resolved, findings=(warning,)))

    rendered = build_smoke_check_result(
        [],
        started_at=datetime.now(UTC),
        result_traceability=source,
    ).to_json_object()
    traceability = cast(JsonObject, rendered["traceability"])

    assert traceability["compilerFindings"] == [
        {
            "code": "compiler.policy.warning",
            "severity": "warning",
            "message": "Illustrative retained warning",
            "sourceDocument": "participant-plan",
            "instancePath": "/selectedCapabilityIds/0",
            "relatedIds": ["pis.domestic-standing-order"],
        }
    ]


def test_participant_plan_snapshot_omits_sensitive_values() -> None:
    _suite, requirements, _test_definitions, participant_plan, _resolved = _resolved_inputs()
    input_definition = replace(requirements.predefined_inputs[0], sensitivity="sensitive")

    snapshot = build_safe_participant_plan_snapshot(
        participant_plan,
        replace(requirements, predefined_inputs=(input_definition,)),
    )

    assert snapshot["predefinedInputs"] == [{"inputId": "pis.dso.input.frequency", "redacted": True}]
    assert "03" not in json.dumps(snapshot)


def _resolved_inputs() -> tuple[
    SuiteRelease,
    RequirementsCatalogue,
    ConfigurationTestDefinitionCatalogue,
    ParticipantPlan,
    ResolvedPlan,
]:
    suite = load_suite_release(_BUNDLE_ROOT / "suite-release.json")
    requirements = load_requirements_catalogue(_BUNDLE_ROOT / "requirements.json")
    test_definitions = load_test_definition_catalogue(_BUNDLE_ROOT / "test-definitions.json")
    participant_plan: ParticipantPlan = load_participant_plan(_FIXTURE_ROOT / "participant-plan.valid.json")
    return (
        suite,
        requirements,
        test_definitions,
        participant_plan,
        compile_participant_plan(suite, requirements, test_definitions, participant_plan),
    )


def _set_attribute(instance: object, name: str, value: object) -> None:
    setattr(instance, name, value)
