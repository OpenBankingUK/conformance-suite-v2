"""Tests for participant-plan resolution and the legacy execution adapter."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from conformance.configuration_contracts import (
    ConfigurationContractError,
    DiagnosticCode,
    ParticipantInput,
    ParticipantPlan,
    ParticipantPlanCompilationError,
    RequirementsCatalogue,
    ResolvedPlanAdapterError,
    StableId,
    StandingOrderFrequency,
    SuiteRelease,
    adapt_resolved_plan_to_compiled_execution,
    compile_participant_plan,
    dump_participant_plan,
    dump_resolved_plan,
    load_participant_plan,
    load_requirements_catalogue,
    load_resolved_plan,
    load_suite_release,
    load_test_definition_catalogue,
    parse_participant_plan,
    resolve_participant_plan,
)
from conformance.configuration_contracts import (
    TestDefinitionCatalogue as ConfigurationTestDefinitionCatalogue,
)
from conformance.context import RuntimeConfig
from conformance.json_types import JsonValue
from conformance.manifest import JsonBody, ManifestStep
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.unit

_BUNDLE_ROOT = REPO_ROOT / "conformance" / "configuration_contracts" / "bundles" / "pis-domestic-standing-order-v4_0"
_FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "v1"
_PARTICIPANT_PLAN_PATH = _FIXTURE_ROOT / "participant-plan.valid.json"
_RESOLVED_PLAN_PATH = _FIXTURE_ROOT / "resolved-plan.valid.json"


def test_participant_plan_is_strict_immutable_and_round_trips() -> None:
    plan = load_participant_plan(_PARTICIPANT_PLAN_PATH)

    assert plan.selected_capability_ids == ("pis.domestic-standing-order",)
    assert plan.predefined_inputs[0].value == StandingOrderFrequency(
        frequency_type="WEEK",
        count_per_period=None,
        point_in_time="03",
    )
    with pytest.raises(FrozenInstanceError):
        _set_attribute(plan, "security_profile", "fapi2")
    assert dump_participant_plan(plan) == _PARTICIPANT_PLAN_PATH.read_text(encoding="utf-8")


def test_participant_plan_rejects_developer_mode_selection_syntax() -> None:
    with pytest.raises(ConfigurationContractError) as captured:
        load_participant_plan(_FIXTURE_ROOT / "participant-plan.invalid-developer-selection.json")

    assert captured.value.diagnostics[0].code is DiagnosticCode.SCHEMA_VALIDATION_FAILED
    assert captured.value.diagnostics[0].instance_path == "/developmentOverrides"


def test_compiler_infers_scope_resolves_inputs_and_emits_golden_plan() -> None:
    suite_release, requirements, tests, participant, artifact_bytes = _compiler_inputs()

    resolved = compile_participant_plan(
        suite_release=suite_release,
        requirements_catalogue=requirements,
        test_definition_catalogue=tests,
        participant_plan=participant,
        artifact_bytes=artifact_bytes,
    )

    assert resolved.valid is True
    assert resolved.compilation_allowed is True
    assert resolved.certification_eligible is True
    assert resolved.findings == ()
    assert [selection.id for selection in resolved.selected_endpoints] == [
        "pis.dso.endpoint.consent-create",
        "pis.dso.endpoint.consent-read",
        "pis.dso.endpoint.order-create",
        "pis.dso.endpoint.order-read",
    ]
    assert [instance.test_definition_id for instance in resolved.test_instances] == [
        "pis.dso.test.consent-create",
        "pis.dso.test.consent-read",
        "pis.dso.test.order-create",
        "pis.dso.test.order-read",
    ]
    assert resolved.test_instances[-1].dependency_instance_ids == ("compiled:pis.dso.test.order-create",)
    assert dump_resolved_plan(resolved) == _RESOLVED_PLAN_PATH.read_text(encoding="utf-8")


def test_equivalent_inputs_produce_identical_resolved_output() -> None:
    suite_release, requirements, tests, participant, artifact_bytes = _compiler_inputs()

    first = compile_participant_plan(
        suite_release=suite_release,
        requirements_catalogue=requirements,
        test_definition_catalogue=tests,
        participant_plan=participant,
        artifact_bytes=artifact_bytes,
    )
    second = compile_participant_plan(
        suite_release=suite_release,
        requirements_catalogue=requirements,
        test_definition_catalogue=tests,
        participant_plan=participant,
        artifact_bytes=dict(reversed(tuple(artifact_bytes.items()))),
    )

    assert dump_resolved_plan(first) == dump_resolved_plan(second)


def test_resolved_plan_is_schema_valid_immutable_and_round_trips() -> None:
    resolved = load_resolved_plan(_RESOLVED_PLAN_PATH)

    assert resolved.provenance.suite_release_id == "obl.pis-dso-v4.walking-skeleton-suite"
    assert resolved.test_instances[0].covered_requirement_ids == (
        "pis.dso.requirement.consent-create",
        "pis.dso.requirement.frequency",
    )
    with pytest.raises(FrozenInstanceError):
        _set_attribute(resolved, "valid", False)
    assert resolved.resolved_inputs[0].value is not None
    with pytest.raises(FrozenInstanceError):
        _set_attribute(resolved.resolved_inputs[0].value, "frequency_type", "DAIL")
    assert dump_resolved_plan(resolved) == _RESOLVED_PLAN_PATH.read_text(encoding="utf-8")


def test_strict_compilation_preserves_missing_input_finding() -> None:
    suite_release, requirements, tests, participant, artifact_bytes = _compiler_inputs()
    participant = replace(participant, predefined_inputs=())

    with pytest.raises(ParticipantPlanCompilationError) as captured:
        compile_participant_plan(
            suite_release=suite_release,
            requirements_catalogue=requirements,
            test_definition_catalogue=tests,
            participant_plan=participant,
            artifact_bytes=artifact_bytes,
        )

    resolved = captured.value.resolved_plan
    assert resolved.valid is False
    assert resolved.compilation_allowed is False
    assert resolved.certification_eligible is False
    assert [(str(finding.code), finding.instance_path) for finding in resolved.findings] == [
        ("compiler.input.missing", "/predefinedInputs")
    ]
    assert resolved.provenance.participant_plan_id == participant.id


def test_compiler_evaluates_selection_and_input_cardinality() -> None:
    suite_release, requirements, tests, participant, artifact_bytes = _compiler_inputs()
    participant = replace(
        participant,
        selected_capability_ids=(
            StableId("pis.domestic-standing-order"),
            StableId("pis.domestic-standing-order"),
        ),
        predefined_inputs=(participant.predefined_inputs[0], participant.predefined_inputs[0]),
    )

    resolved = resolve_participant_plan(
        suite_release=suite_release,
        requirements_catalogue=requirements,
        test_definition_catalogue=tests,
        participant_plan=participant,
        artifact_bytes=artifact_bytes,
    )

    assert [(str(finding.code), finding.instance_path) for finding in resolved.findings] == [
        ("compiler.input.duplicate", "/predefinedInputs"),
        ("compiler.selection.duplicate", "/selectedCapabilityIds"),
    ]


def test_compiler_reports_unknown_and_empty_effective_selection() -> None:
    suite_release, requirements, tests, participant, artifact_bytes = _compiler_inputs()
    participant = replace(
        participant,
        selected_capability_ids=(StableId("pis.unknown"),),
    )

    resolved = resolve_participant_plan(
        suite_release=suite_release,
        requirements_catalogue=requirements,
        test_definition_catalogue=tests,
        participant_plan=participant,
        artifact_bytes=artifact_bytes,
    )

    assert {str(finding.code) for finding in resolved.findings} == {
        "compiler.input.not-applicable",
        "compiler.selection.empty",
        "compiler.selection.unknown",
    }
    assert resolved.selected_capabilities == ()
    assert resolved.test_instances == ()


def test_compiler_reports_incompatible_participant_scope() -> None:
    suite_release, requirements, tests, participant, artifact_bytes = _compiler_inputs()
    participant = replace(
        participant,
        specification=replace(participant.specification, requirements_scope=StableId("ais")),
    )

    with pytest.raises(ParticipantPlanCompilationError) as captured:
        compile_participant_plan(
            suite_release=suite_release,
            requirements_catalogue=requirements,
            test_definition_catalogue=tests,
            participant_plan=participant,
            artifact_bytes=artifact_bytes,
        )

    assert "compiler.scope.mismatch" in {str(finding.code) for finding in captured.value.resolved_plan.findings}


def test_compiler_rejects_unbound_or_tampered_release_inputs() -> None:
    suite_release, requirements, tests, participant, artifact_bytes = _compiler_inputs()
    artifact_key = next(iter(artifact_bytes))
    artifact_bytes[artifact_key] = b"tampered"

    with pytest.raises(ParticipantPlanCompilationError) as captured:
        compile_participant_plan(
            suite_release=suite_release,
            requirements_catalogue=requirements,
            test_definition_catalogue=tests,
            participant_plan=participant,
            artifact_bytes=artifact_bytes,
        )

    assert "config.integrity.artifact-digest-mismatch" in {
        str(finding.code) for finding in captured.value.resolved_plan.findings
    }


def test_compiler_rejects_typed_catalogue_that_differs_from_release_bytes() -> None:
    suite_release, requirements, tests, participant, artifact_bytes = _compiler_inputs()
    first_definition = replace(tests.test_definitions[0], name="Tampered name")
    tests = replace(tests, test_definitions=(first_definition, *tests.test_definitions[1:]))

    with pytest.raises(ParticipantPlanCompilationError) as captured:
        compile_participant_plan(
            suite_release=suite_release,
            requirements_catalogue=requirements,
            test_definition_catalogue=tests,
            participant_plan=participant,
            artifact_bytes=artifact_bytes,
        )

    assert "compiler.release.artifact-content-mismatch" in {
        str(finding.code) for finding in captured.value.resolved_plan.findings
    }


@pytest.mark.parametrize(
    ("frequency", "expected_frequency"),
    [
        (
            StandingOrderFrequency(frequency_type="WEEK", count_per_period=None, point_in_time="03"),
            {"Type": "WEEK", "PointInTime": "03"},
        ),
        (
            StandingOrderFrequency(frequency_type="INDA", count_per_period=2, point_in_time=None),
            {"Type": "INDA", "CountPerPeriod": 2},
        ),
    ],
)
def test_adapter_feeds_resolved_frequency_and_dependencies_to_existing_manifest_path(
    tmp_path: Path,
    frequency: StandingOrderFrequency,
    expected_frequency: dict[str, JsonValue],
) -> None:
    from conformance.executor import _compiled_plan_to_manifest

    suite_release, requirements, tests, participant, artifact_bytes = _compiler_inputs()
    participant = replace(
        participant,
        predefined_inputs=(
            ParticipantInput(
                input_id=StableId("pis.dso.input.frequency"),
                value=frequency,
            ),
        ),
    )
    resolved = compile_participant_plan(
        suite_release=suite_release,
        requirements_catalogue=requirements,
        test_definition_catalogue=tests,
        participant_plan=participant,
        artifact_bytes=artifact_bytes,
    )
    adapter = adapt_resolved_plan_to_compiled_execution(
        resolved,
        tests,
        suite_release=suite_release,
        requirements_catalogue=requirements,
        participant_plan=participant,
        artifact_bytes=artifact_bytes,
        legacy_runtime_inputs=_legacy_runtime_inputs(),
    )

    assert [test_case.test_case_id for test_case in adapter.compiled_plan.test_cases] == [
        "pis-v4-domestic-standing-order-consent-create",
        "pis-v4-domestic-standing-order-consent-read",
        "pis-v4-domestic-standing-order-create",
        "pis-v4-domestic-standing-order-read",
    ]
    assert "pisStandingOrderFrequencyType" not in adapter.runtime_inputs
    manifest = _compiled_plan_to_manifest(
        adapter.compiled_plan,
        runtime_inputs=adapter.runtime_inputs,
        runtime_input_base_dir=tmp_path,
        runtime_config=RuntimeConfig(discovery_url="https://auth.example.com/.well-known/openid-configuration"),
    )
    for step_id in (
        "pis-v4-domestic-standing-order-consent-create-request",
        "pis-v4-domestic-standing-order-create-request",
    ):
        step = next(step for step in manifest.steps if step.id == step_id)
        assert isinstance(step, ManifestStep)
        assert isinstance(step.request.body, JsonBody)
        body = step.request.body.value
        assert isinstance(body, dict)
        data = body["Data"]
        assert isinstance(data, dict)
        initiation = data["Initiation"]
        assert isinstance(initiation, dict)
        mandate = initiation["MandateRelatedInformation"]
        assert isinstance(mandate, dict)
        assert mandate["Frequency"] == expected_frequency


def test_adapter_rejects_a_modified_resolved_plan() -> None:
    suite_release, requirements, tests, participant, artifact_bytes = _compiler_inputs()
    resolved = compile_participant_plan(
        suite_release=suite_release,
        requirements_catalogue=requirements,
        test_definition_catalogue=tests,
        participant_plan=participant,
        artifact_bytes=artifact_bytes,
    )
    modified = replace(resolved, test_instances=())

    with pytest.raises(ResolvedPlanAdapterError, match="does not match release-bound compiler inputs"):
        adapt_resolved_plan_to_compiled_execution(
            modified,
            tests,
            suite_release=suite_release,
            requirements_catalogue=requirements,
            participant_plan=participant,
            artifact_bytes=artifact_bytes,
            legacy_runtime_inputs=_legacy_runtime_inputs(),
        )


def test_participant_frequency_schema_rejects_incompatible_cardinality() -> None:
    raw_plan: object = json.loads(_PARTICIPANT_PLAN_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_plan, dict)
    predefined_inputs = raw_plan["predefinedInputs"]
    assert isinstance(predefined_inputs, list)
    first_input = predefined_inputs[0]
    assert isinstance(first_input, dict)
    value = first_input["value"]
    assert isinstance(value, dict)
    value["countPerPeriod"] = 2

    with pytest.raises(ConfigurationContractError) as captured:
        parse_participant_plan(raw_plan)

    assert captured.value.diagnostics[0].code is DiagnosticCode.SCHEMA_VALIDATION_FAILED
    assert captured.value.diagnostics[0].instance_path == "/predefinedInputs/0/value"


def _compiler_inputs() -> tuple[
    SuiteRelease,
    RequirementsCatalogue,
    ConfigurationTestDefinitionCatalogue,
    ParticipantPlan,
    dict[tuple[str, str], bytes],
]:
    suite_release = load_suite_release(_BUNDLE_ROOT / "suite-release.json")
    requirements = load_requirements_catalogue(_BUNDLE_ROOT / "requirements.json")
    test_definitions = load_test_definition_catalogue(_BUNDLE_ROOT / "test-definitions.json")
    participant_plan = load_participant_plan(_PARTICIPANT_PLAN_PATH)
    artifact_bytes = {
        (str(artifact.kind), str(artifact.id)): (REPO_ROOT / artifact.uri).read_bytes()
        for artifact in suite_release.artifacts
    }
    return suite_release, requirements, test_definitions, participant_plan, artifact_bytes


def _legacy_runtime_inputs() -> dict[str, JsonValue]:
    return {
        "resourceBaseUrl": "https://resource.example.com",
        "pisCreditorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
        "pisCreditorAccountIdentification": "70000170000002",
        "pisCreditorAccountName": "Domestic creditor",
        "pisInstructedAmountAmount": "1.00",
        "pisInstructedAmountCurrency": "GBP",
        "pisFirstPaymentDateTime": "2026-12-01T00:00:00+00:00",
    }


def _set_attribute(instance: object, name: str, value: object) -> None:
    setattr(instance, name, value)
