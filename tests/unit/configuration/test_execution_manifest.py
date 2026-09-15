"""Tests for generated execution manifests and compatibility bindings."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import httpx
import pytest

from conformance.catalogues import PIS_PAYMENT_CATALOGUE
from conformance.configuration_contracts import (
    ConfigurationContractError,
    DiagnosticCode,
    ExecutionManifestGenerationError,
    LegacyExecutionEngine,
    ResolvedPlanAdapterError,
    compile_participant_plan,
    dump_execution_manifest,
    generate_execution_manifest,
    load_execution_manifest,
    load_participant_plan,
    load_requirements_catalogue,
    load_suite_release,
    load_test_definition_catalogue,
    parse_execution_manifest,
    prepare_resolved_execution_manifest,
)
from conformance.configuration_contracts.models import (
    ParticipantPlan,
    RequirementsCatalogue,
    ResolvedPlan,
    SuiteRelease,
)
from conformance.configuration_contracts.models import (
    TestDefinitionCatalogue as ConfigurationTestDefinitionCatalogue,
)
from conformance.executor import run_execution_manifest
from conformance.json_types import JsonValue
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.unit

_BUNDLE_ROOT = REPO_ROOT / "conformance" / "configuration_contracts" / "bundles" / "pis-domestic-standing-order-v4_0"
_FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "v1"
_EXECUTION_MANIFEST_PATH = _FIXTURE_ROOT / "execution-manifest.valid.json"


def test_resolved_plan_generates_deterministic_immutable_execution_manifest() -> None:
    _suite, requirements, test_definitions, resolved = _resolved_inputs()

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


def test_generation_rejects_redacted_runtime_input() -> None:
    _suite, requirements, test_definitions, resolved = _resolved_inputs()
    redacted_input = replace(resolved.predefined_inputs[0], value=None, redacted=True)

    with pytest.raises(ExecutionManifestGenerationError, match="is redacted"):
        generate_execution_manifest(
            replace(resolved, predefined_inputs=(redacted_input,)),
            requirements,
            test_definitions,
        )


def test_generation_rejects_catalogue_bytes_not_bound_by_resolved_provenance() -> None:
    _suite, requirements, test_definitions, resolved = _resolved_inputs()
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
    _suite, requirements, test_definitions, resolved = _resolved_inputs()
    first, second, *remaining = resolved.test_instances

    with pytest.raises(ExecutionManifestGenerationError, match="appears before dependencies"):
        generate_execution_manifest(
            replace(resolved, test_instances=(second, first, *remaining)),
            requirements,
            test_definitions,
        )


def test_resolved_execution_preparation_binds_manifest_to_legacy_read_write(
    tmp_path: Path,
) -> None:
    _suite, requirements, test_definitions, resolved = _resolved_inputs()

    prepared = prepare_resolved_execution_manifest(
        resolved,
        requirements,
        test_definitions,
        PIS_PAYMENT_CATALOGUE,
        runtime_inputs=_legacy_runtime_inputs(),
        runtime_input_base_dir=tmp_path,
    )

    assert prepared.manifest == generate_execution_manifest(resolved, requirements, test_definitions)
    assert prepared.engine is LegacyExecutionEngine.READ_WRITE
    assert prepared.compiled_plan.traceability.generated_test_case_ids == (
        "pis-v4-domestic-standing-order-consent-create",
        "pis-v4-domestic-standing-order-consent-read",
        "pis-v4-domestic-standing-order-create",
        "pis-v4-domestic-standing-order-read",
    )
    assert prepared.runtime_inputs["pisStandingOrderFrequencyType"] == "WEEK"

    with pytest.raises(ExecutionManifestGenerationError):
        generate_execution_manifest(
            replace(resolved, selection_valid=False),
            requirements,
            test_definitions,
        )


def test_compatibility_binding_rejects_compiled_execution_drift(tmp_path: Path) -> None:
    _suite, requirements, test_definitions, resolved = _resolved_inputs()
    prepared = prepare_resolved_execution_manifest(
        resolved,
        requirements,
        test_definitions,
        PIS_PAYMENT_CATALOGUE,
        runtime_inputs=_legacy_runtime_inputs(),
        runtime_input_base_dir=tmp_path,
    )
    drifted = replace(
        prepared,
        compiled_plan=replace(
            prepared.compiled_plan,
            test_cases=tuple(reversed(prepared.compiled_plan.test_cases)),
        ),
    )

    with (
        httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500))) as client,
        pytest.raises(ResolvedPlanAdapterError, match="selection differs"),
    ):
        run_execution_manifest(drifted, client=client)


def _resolved_inputs() -> tuple[
    SuiteRelease,
    RequirementsCatalogue,
    ConfigurationTestDefinitionCatalogue,
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
        compile_participant_plan(suite, requirements, test_definitions, participant_plan),
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


def _set_attribute(instance: object, name: str, value: object) -> None:
    setattr(instance, name, value)
