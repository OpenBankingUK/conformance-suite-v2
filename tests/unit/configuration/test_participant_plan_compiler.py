"""Tests for strict participant-plan resolution and legacy execution adaptation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from conformance.catalogues import PIS_PAYMENT_CATALOGUE
from conformance.configuration_contracts import (
    CompilationFindingCode,
    ConfigurationContractError,
    DiagnosticCode,
    FindingSourceDocument,
    InputResolutionSource,
    ParticipantPlan,
    ParticipantPlanCompilationError,
    ResolvedPlanAdapterError,
    SelectionOrigin,
    Sha256Digest,
    StableId,
    StandingOrderFrequency,
    SuiteRelease,
    adapt_resolved_plan_to_compiled_execution,
    compile_participant_plan,
    dump_participant_plan,
    dump_requirements_catalogue,
    dump_resolved_plan,
    dump_test_definition_catalogue,
    load_participant_plan,
    load_requirements_catalogue,
    load_resolved_plan,
    load_suite_release,
    load_test_definition_catalogue,
    parse_participant_plan,
)
from conformance.configuration_contracts.models import (
    RequirementsCatalogue,
)
from conformance.configuration_contracts.models import (
    TestDefinitionCatalogue as ConfigurationTestDefinitionCatalogue,
)
from conformance.json_types import JsonValue
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.unit

_BUNDLE_ROOT = REPO_ROOT / "conformance" / "configuration_contracts" / "bundles" / "pis-domestic-standing-order-v4_0"
_FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "v1"
_PARTICIPANT_PLAN_PATH = _FIXTURE_ROOT / "participant-plan.valid.json"
_RESOLVED_PLAN_PATH = _FIXTURE_ROOT / "resolved-plan.valid.json"


def test_participant_plan_loads_as_immutable_schema_owned_intent() -> None:
    plan = load_participant_plan(_PARTICIPANT_PLAN_PATH)

    assert plan.document_type == "participant-plan"
    assert plan.suite_release_id == "obl.pis-dso-v4.walking-skeleton-suite"
    assert plan.specification.requirements_scope == "pis"
    assert plan.security_profile == "fapi1-advanced"
    assert plan.selected_capability_ids == ("pis.domestic-standing-order",)
    assert plan.predefined_inputs[0].value == StandingOrderFrequency(
        frequency_type="WEEK",
        count_per_period=None,
        point_in_time="03",
    )
    assert dump_participant_plan(plan) == _PARTICIPANT_PLAN_PATH.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("fixture_name", "expected_path"),
    [
        ("participant-plan.invalid-duplicate-input.json", "/predefinedInputs/1/inputId"),
        ("participant-plan.invalid-frequency.json", "/predefinedInputs/0/value"),
    ],
)
def test_invalid_participant_plan_fixtures_have_stable_diagnostics(
    fixture_name: str,
    expected_path: str,
) -> None:
    with pytest.raises(ConfigurationContractError) as captured:
        load_participant_plan(_FIXTURE_ROOT / fixture_name)

    expected_code = (
        DiagnosticCode.DUPLICATE_ID
        if fixture_name.endswith("duplicate-input.json")
        else DiagnosticCode.SCHEMA_VALIDATION_FAILED
    )
    assert captured.value.diagnostics[0].code is expected_code
    assert captured.value.diagnostics[0].instance_path == expected_path


def test_participant_plan_rejects_developer_mode_selection_syntax() -> None:
    raw_document: object = json.loads(_PARTICIPANT_PLAN_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_document, dict)
    raw_document["mode"] = "development"

    with pytest.raises(ConfigurationContractError) as captured:
        parse_participant_plan(raw_document)

    assert captured.value.diagnostics[0].code is DiagnosticCode.SCHEMA_VALIDATION_FAILED
    assert captured.value.diagnostics[0].instance_path == "/mode"


def test_participant_plan_requires_non_empty_strict_scope() -> None:
    raw_document: object = json.loads(_PARTICIPANT_PLAN_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_document, dict)
    raw_document["selectedCapabilityIds"] = []

    with pytest.raises(ConfigurationContractError) as captured:
        parse_participant_plan(raw_document)

    assert captured.value.diagnostics[0].code is DiagnosticCode.SCHEMA_VALIDATION_FAILED
    assert captured.value.diagnostics[0].instance_path == "/selectedCapabilityIds"


def test_compiler_emits_deterministic_resolved_plan_with_full_traceability() -> None:
    suite_release, requirements, test_definitions, participant_plan = _compiler_inputs()

    first = compile_participant_plan(suite_release, requirements, test_definitions, participant_plan)
    second = compile_participant_plan(suite_release, requirements, test_definitions, participant_plan)

    assert first == second
    assert dump_resolved_plan(first) == _RESOLVED_PLAN_PATH.read_text(encoding="utf-8")
    assert load_resolved_plan(_RESOLVED_PLAN_PATH) == first
    assert first.selection_valid is True
    assert first.findings == ()
    assert [(item.id, item.origin) for item in first.capabilities] == [
        ("pis.domestic-standing-order", SelectionOrigin.EXPLICIT)
    ]
    assert [(item.id, item.origin) for item in first.endpoints] == [
        ("pis.dso.endpoint.consent-create", SelectionOrigin.INFERRED),
        ("pis.dso.endpoint.consent-read", SelectionOrigin.INFERRED),
        ("pis.dso.endpoint.order-create", SelectionOrigin.INFERRED),
        ("pis.dso.endpoint.order-read", SelectionOrigin.INFERRED),
    ]
    assert [test.test_definition_id for test in first.test_instances] == [
        "pis.dso.test.consent-create",
        "pis.dso.test.consent-read",
        "pis.dso.test.order-create",
        "pis.dso.test.order-read",
    ]
    assert first.test_instances[-1].dependency_ids == ("pis.dso.test.order-create.instance",)
    assert first.predefined_inputs[0].source is InputResolutionSource.PARTICIPANT
    assert first.provenance.artifacts == suite_release.artifacts


def test_compiler_normalizes_order_insensitive_participant_selections() -> None:
    suite_release, requirements, test_definitions, participant_plan = _compiler_inputs()
    reordered = replace(
        participant_plan,
        selected_capability_ids=tuple(reversed(participant_plan.selected_capability_ids)),
        predefined_inputs=tuple(reversed(participant_plan.predefined_inputs)),
    )

    assert compile_participant_plan(
        suite_release,
        requirements,
        test_definitions,
        reordered,
    ) == compile_participant_plan(
        suite_release,
        requirements,
        test_definitions,
        participant_plan,
    )


def test_strict_compilation_retains_missing_input_finding() -> None:
    suite_release, requirements, test_definitions, participant_plan = _compiler_inputs()
    participant_plan = replace(participant_plan, predefined_inputs=())

    with pytest.raises(ParticipantPlanCompilationError) as captured:
        compile_participant_plan(suite_release, requirements, test_definitions, participant_plan)

    resolved = captured.value.resolved_plan
    assert resolved.selection_valid is False
    assert [finding.code for finding in resolved.findings] == [CompilationFindingCode.INPUT_REQUIRED.value]
    assert resolved.findings[0].instance_path == "/predefinedInputs"
    assert resolved.findings[0].source_document is FindingSourceDocument.PARTICIPANT_PLAN
    assert resolved.findings[0].related_ids == (
        "pis.dso.input.frequency",
        "pis.dso.requirement.frequency",
    )
    assert resolved.predefined_inputs == ()
    assert len(resolved.test_instances) == 4


def test_strict_compilation_rejects_programmatic_empty_scope() -> None:
    suite_release, requirements, test_definitions, participant_plan = _compiler_inputs()

    with pytest.raises(ParticipantPlanCompilationError) as captured:
        compile_participant_plan(
            suite_release,
            requirements,
            test_definitions,
            replace(participant_plan, selected_capability_ids=(), predefined_inputs=()),
        )

    assert [finding.code for finding in captured.value.resolved_plan.findings] == [
        CompilationFindingCode.SCOPE_EMPTY.value
    ]


def test_compiler_rejects_unknown_and_inapplicable_participant_selections() -> None:
    suite_release, requirements, test_definitions, participant_plan = _compiler_inputs()
    invalid_plan = replace(
        participant_plan,
        selected_capability_ids=(StableId("pis.unknown"),),
    )

    with pytest.raises(ParticipantPlanCompilationError) as captured:
        compile_participant_plan(suite_release, requirements, test_definitions, invalid_plan)

    assert [finding.code for finding in captured.value.resolved_plan.findings] == [
        CompilationFindingCode.CAPABILITY_UNKNOWN.value,
        CompilationFindingCode.SCOPE_EMPTY.value,
        CompilationFindingCode.INPUT_NOT_APPLICABLE.value,
    ]


def test_catalogue_default_is_recorded_separately_from_participant_values() -> None:
    suite_release, requirements, test_definitions, participant_plan = _compiler_inputs()
    predefined_input = requirements.predefined_inputs[0]
    requirements = replace(
        requirements,
        predefined_inputs=(replace(predefined_input, default_value=predefined_input.example_value),),
    )
    suite_release = _with_requirements_digest(suite_release, requirements)

    resolved = compile_participant_plan(
        suite_release,
        requirements,
        test_definitions,
        replace(participant_plan, predefined_inputs=()),
    )

    assert resolved.predefined_inputs[0].source is InputResolutionSource.DEFAULT
    assert resolved.predefined_inputs[0].value == predefined_input.example_value
    assert resolved.predefined_inputs[0].reasons[0].code == "requirements.input.defaulted"


def test_resolved_plan_adapts_to_existing_compiled_execution_contract() -> None:
    suite_release, requirements, test_definitions, participant_plan = _compiler_inputs()
    resolved = compile_participant_plan(suite_release, requirements, test_definitions, participant_plan)

    adapted = adapt_resolved_plan_to_compiled_execution(
        resolved,
        PIS_PAYMENT_CATALOGUE,
        runtime_inputs=_legacy_runtime_inputs(),
    )
    compiled = adapted.compiled_plan

    assert compiled.traceability.generated_test_case_ids == (
        "pis-v4-domestic-standing-order-consent-create",
        "pis-v4-domestic-standing-order-consent-read",
        "pis-v4-domestic-standing-order-create",
        "pis-v4-domestic-standing-order-read",
    )
    runtime_values = {
        trace.input_id: trace.value for trace in compiled.traceability.runtime_input_snapshot if trace.provided
    }
    assert runtime_values["pisStandingOrderFrequencyType"] == "WEEK"
    assert runtime_values["pisStandingOrderFrequencyPointInTime"] == "03"
    assert adapted.runtime_inputs["pisStandingOrderFrequencyType"] == "WEEK"
    assert adapted.runtime_inputs["pisStandingOrderFrequencyPointInTime"] == "03"
    decisions = {decision.test_case_id: decision for decision in compiled.traceability.applicability_decisions}
    assert decisions["pis-v4-domestic-standing-order-consent-reject-invalid-frequency"].reason == (
        "deselected by participant"
    )


def test_adapter_rejects_frequency_shape_not_supported_by_current_runtime() -> None:
    suite_release, requirements, test_definitions, participant_plan = _compiler_inputs()
    participant_input = participant_plan.predefined_inputs[0]
    count_frequency_plan = replace(
        participant_plan,
        predefined_inputs=(
            replace(
                participant_input,
                value=StandingOrderFrequency(
                    frequency_type="WODL",
                    count_per_period=2,
                    point_in_time=None,
                ),
            ),
        ),
    )
    resolved = compile_participant_plan(
        suite_release,
        requirements,
        test_definitions,
        count_frequency_plan,
    )

    with pytest.raises(
        ResolvedPlanAdapterError,
        match="supports pointInTime standing-order frequencies only",
    ):
        adapt_resolved_plan_to_compiled_execution(
            resolved,
            PIS_PAYMENT_CATALOGUE,
            runtime_inputs=_legacy_runtime_inputs(),
        )


def test_compiler_rejects_catalogue_bytes_not_bound_by_suite_release() -> None:
    suite_release, requirements, test_definitions, participant_plan = _compiler_inputs()
    changed_input = replace(
        requirements.predefined_inputs[0],
        label="Changed without updating the suite release",
    )

    with pytest.raises(ConfigurationContractError) as captured:
        compile_participant_plan(
            suite_release,
            replace(requirements, predefined_inputs=(changed_input,)),
            test_definitions,
            participant_plan,
        )

    assert captured.value.diagnostics[0].code is DiagnosticCode.ARTIFACT_DIGEST_MISMATCH


def test_compiler_revalidates_programmatic_test_dependencies() -> None:
    suite_release, requirements, test_definitions, participant_plan = _compiler_inputs()
    first_definition = replace(
        test_definitions.test_definitions[0],
        dependencies=(StableId("pis.dso.test.missing"),),
    )
    changed_test_definitions = replace(
        test_definitions,
        test_definitions=(first_definition, *test_definitions.test_definitions[1:]),
    )
    suite_release = _with_test_definition_digest(suite_release, changed_test_definitions)

    with pytest.raises(ConfigurationContractError) as captured:
        compile_participant_plan(
            suite_release,
            requirements,
            changed_test_definitions,
            participant_plan,
        )

    assert any(
        diagnostic.code is DiagnosticCode.REFERENCE_UNRESOLVED
        and diagnostic.instance_path == "/testDefinitions/0/dependencies/0"
        for diagnostic in captured.value.diagnostics
    )


def _compiler_inputs() -> tuple[
    SuiteRelease,
    RequirementsCatalogue,
    ConfigurationTestDefinitionCatalogue,
    ParticipantPlan,
]:
    return (
        load_suite_release(_BUNDLE_ROOT / "suite-release.json"),
        load_requirements_catalogue(_BUNDLE_ROOT / "requirements.json"),
        load_test_definition_catalogue(_BUNDLE_ROOT / "test-definitions.json"),
        load_participant_plan(_PARTICIPANT_PLAN_PATH),
    )


def _with_requirements_digest(
    suite_release: SuiteRelease,
    requirements: RequirementsCatalogue,
) -> SuiteRelease:
    digest = Sha256Digest(
        f"sha256:{hashlib.sha256(dump_requirements_catalogue(requirements).encode('utf-8')).hexdigest()}"
    )
    return replace(
        suite_release,
        artifacts=tuple(
            replace(artifact, digest=digest)
            if artifact.kind == "requirements-catalogue" and artifact.id == requirements.id
            else artifact
            for artifact in suite_release.artifacts
        ),
    )


def _with_test_definition_digest(
    suite_release: SuiteRelease,
    test_definitions: ConfigurationTestDefinitionCatalogue,
) -> SuiteRelease:
    digest = Sha256Digest(
        f"sha256:{hashlib.sha256(dump_test_definition_catalogue(test_definitions).encode('utf-8')).hexdigest()}"
    )
    return replace(
        suite_release,
        artifacts=tuple(
            replace(artifact, digest=digest)
            if artifact.kind == "test-definition-catalogue" and artifact.id == test_definitions.id
            else artifact
            for artifact in suite_release.artifacts
        ),
    )


def _legacy_runtime_inputs() -> dict[str, JsonValue]:
    return {
        "pisCreditorAccountIdentification": "08080021325698",
        "pisCreditorAccountName": "Merchant",
        "pisCreditorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
        "pisFirstPaymentDateTime": "2026-10-01T00:00:00Z",
        "pisInstructedAmountAmount": "10.00",
        "pisInstructedAmountCurrency": "GBP",
        "resourceBaseUrl": "https://rs.example.com",
    }
