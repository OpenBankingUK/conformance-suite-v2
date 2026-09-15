"""Tests for source-authored replacement CBPII catalogue families."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

import pytest

from conformance.configuration_contracts import (
    DiagnosticCode,
    compile_participant_plan,
    dump_resolved_plan,
    load_participant_plan,
    load_requirements_catalogue,
    load_resolved_plan,
    load_suite_release,
    load_test_definition_catalogue,
    validate_catalogue_references,
    verify_suite_release_artifacts,
)
from conformance.configuration_contracts.models import HttpMethod
from conformance.json_types import JsonObject
from tests.support.parity import assert_parity_report_matches_baseline
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.unit

_CATALOGUE_ROOT = REPO_ROOT / "conformance" / "configuration_contracts" / "catalogues" / "cbpii"
_FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "cbpii"
_BASELINE_PATHS = {
    "v3_1_11": (
        REPO_ROOT / "conformance" / "standards" / "ob_read_write" / "v3_1_11" / "legacy" / "ob_3.1_cbpii_fca.json"
    ),
    "v4_0_1": _FIXTURE_ROOT / "v4_0_1" / "ob_4.0_cbpii_fca.json",
}
_VERSION_CASES = (
    (
        "v3_1_11",
        "3.1.11",
        "v311",
        REPO_ROOT / "conformance" / "standards" / "ob_read_write" / "v3_1_11" / "confirmation-funds-openapi.json",
    ),
    (
        "v4_0_1",
        "4.0.1",
        "v401",
        REPO_ROOT / "conformance" / "standards" / "ob_read_write" / "v4_0_1" / "confirmation-funds-openapi.json",
    ),
)


@pytest.mark.parametrize(("directory", "version", "id_version", "openapi_path"), _VERSION_CASES)
def test_cbpii_catalogues_are_source_bound_and_referentially_complete(
    directory: str,
    version: str,
    id_version: str,
    openapi_path: Path,
) -> None:
    catalogue_root = _CATALOGUE_ROOT / directory
    requirements = load_requirements_catalogue(catalogue_root / "requirements.json")
    test_definitions = load_test_definition_catalogue(catalogue_root / "test-definitions.json")
    openapi = cast(JsonObject, json.loads(openapi_path.read_text(encoding="utf-8")))

    assert requirements.id == f"obl.cbpii-{id_version}.requirements"
    assert requirements.specification.version == version
    assert requirements.specification.requirements_scope == "cbpii"
    assert test_definitions.requirements_catalogue_id == requirements.id
    assert len(requirements.capabilities) == 1
    assert len(requirements.endpoints) == 4
    assert len(requirements.predefined_inputs) == 4
    assert len(requirements.requirements) == 8
    assert len(test_definitions.test_definitions) == 13
    assert validate_catalogue_references(requirements, test_definitions) == ()

    technical_source = requirements.technical_sources[0]
    assert technical_source.digest == f"sha256:{hashlib.sha256(openapi_path.read_bytes()).hexdigest()}"
    for endpoint in requirements.endpoints:
        operation = _resolve_json_pointer(openapi, endpoint.source_pointer)
        assert endpoint.source_id == technical_source.id
        assert isinstance(operation, dict)
        assert operation["operationId"] == endpoint.operation_id

    covered_ids = {
        requirement_id
        for definition in test_definitions.test_definitions
        for requirement_id in definition.covered_requirement_ids
    }
    assert covered_ids == {requirement.id for requirement in requirements.requirements}
    assert all(definition.assertions for definition in test_definitions.test_definitions)


@pytest.mark.parametrize(("directory", "_version", "_id_version", "_openapi_path"), _VERSION_CASES)
def test_cbpii_shared_contract_vocabulary_covers_family_requirements(
    directory: str,
    _version: str,
    _id_version: str,
    _openapi_path: Path,
) -> None:
    requirements = load_requirements_catalogue(_CATALOGUE_ROOT / directory / "requirements.json")
    test_definitions = load_test_definition_catalogue(_CATALOGUE_ROOT / directory / "test-definitions.json")

    assert any(endpoint.method is HttpMethod.DELETE for endpoint in requirements.endpoints)
    sensitive = next(
        predefined_input
        for predefined_input in requirements.predefined_inputs
        if predefined_input.id.endswith("debtor-account-identification")
    )
    assert sensitive.sensitivity == "sensitive"
    assert any(
        modification.location == "path-parameter"
        for definition in test_definitions.test_definitions
        for modification in definition.request.modifications
    )
    assert any(
        modification.generator == "next-day-date-time-offset-milliseconds"
        for definition in test_definitions.test_definitions
        for modification in definition.request.modifications
    )


@pytest.mark.parametrize(("directory", "_version", "_id_version", "_openapi_path"), _VERSION_CASES)
def test_cbpii_parity_evidence_classifies_every_pinned_row(
    directory: str,
    _version: str,
    _id_version: str,
    _openapi_path: Path,
) -> None:
    fixture_root = _FIXTURE_ROOT / directory
    report = cast(JsonObject, json.loads((fixture_root / "parity-comparison.json").read_text(encoding="utf-8")))
    tests = load_test_definition_catalogue(_CATALOGUE_ROOT / directory / "test-definitions.json")
    classifications = cast(list[JsonObject], report["classifications"])
    baseline = cast(JsonObject, report["baseline"])
    summary = cast(JsonObject, report["summary"])
    replacement_ids = {str(definition.id) for definition in tests.test_definitions}

    assert_parity_report_matches_baseline(report, _BASELINE_PATHS[directory])
    assert baseline["purpose"] == "migration-cross-check-only"
    assert len(classifications) == 14
    assert len({item["sourceRow"] for item in classifications}) == 14
    assert summary == {
        "genuine-omission": 0,
        "intentional-correction": 4,
        "obsolete-behavior": 0,
        "retained-coverage": 10,
    }
    assert all(
        set(cast(list[str], item["replacementTestDefinitionIds"])) <= replacement_ids for item in classifications
    )


@pytest.mark.parametrize(("directory", "_version", "_id_version", "_openapi_path"), _VERSION_CASES)
def test_cbpii_participant_compilation_matches_deterministic_golden(
    directory: str,
    _version: str,
    _id_version: str,
    _openapi_path: Path,
) -> None:
    catalogue_root = _CATALOGUE_ROOT / directory
    fixture_root = _FIXTURE_ROOT / directory
    suite_release = load_suite_release(fixture_root / "suite-release.json")
    requirements = load_requirements_catalogue(catalogue_root / "requirements.json")
    test_definitions = load_test_definition_catalogue(catalogue_root / "test-definitions.json")
    participant_plan = load_participant_plan(fixture_root / "participant-plan.json")

    first = compile_participant_plan(suite_release, requirements, test_definitions, participant_plan)
    second = compile_participant_plan(suite_release, requirements, test_definitions, participant_plan)

    assert first == second
    assert first.selection_valid is True
    assert len(first.test_instances) == 13
    assert dump_resolved_plan(first) == (fixture_root / "resolved-plan.json").read_text(encoding="utf-8")
    assert load_resolved_plan(fixture_root / "resolved-plan.json") == first
    sensitive = next(
        predefined_input
        for predefined_input in first.predefined_inputs
        if predefined_input.id.endswith("debtor-account-identification")
    )
    assert sensitive.redacted is True
    assert sensitive.value is None

    artifact_bytes = {
        (str(artifact.kind), str(artifact.id)): (REPO_ROOT / artifact.uri).read_bytes()
        for artifact in suite_release.artifacts
    }
    assert verify_suite_release_artifacts(suite_release, artifact_bytes) == ()


def test_cbpii_broken_cross_catalogue_reference_has_stable_diagnostic() -> None:
    requirements = load_requirements_catalogue(_CATALOGUE_ROOT / "v4_0_1" / "requirements.json")
    invalid_tests = load_test_definition_catalogue(_FIXTURE_ROOT / "test-definitions.invalid-reference.json")

    diagnostics = validate_catalogue_references(requirements, invalid_tests)

    assert len(diagnostics) == 1
    assert diagnostics[0].code is DiagnosticCode.REFERENCE_UNRESOLVED
    assert diagnostics[0].instance_path == "/testDefinitions/0/coveredRequirementIds/0"


def _resolve_json_pointer(document: JsonObject, pointer: str) -> object:
    value: object = document
    for raw_part in pointer.removeprefix("/").split("/"):
        assert isinstance(value, dict)
        part = raw_part.replace("~1", "/").replace("~0", "~")
        value = value[part]
    return value
