"""Tests for source-authored replacement AIS catalogue families."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from conformance.configuration_contracts import (
    ConfigurationContractError,
    DiagnosticCode,
    ParticipantPlanCompilationError,
    Sha256Digest,
    StableId,
    compile_participant_plan,
    dump_execution_manifest,
    dump_requirements_catalogue,
    dump_resolved_plan,
    generate_execution_manifest,
    load_participant_plan,
    load_requirements_catalogue,
    load_resolved_plan,
    load_suite_release,
    load_test_definition_catalogue,
    parse_execution_manifest,
    parse_participant_plan,
    parse_requirements_catalogue,
    validate_catalogue_references,
)
from conformance.json_types import JsonObject
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.unit

_CATALOGUE_ROOT = REPO_ROOT / "conformance" / "configuration_contracts" / "catalogues" / "ais"
_FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "ais"
_VERSION_CASES = (
    (
        "v3_1_11",
        "3.1.11",
        "v311",
        REPO_ROOT / "conformance" / "standards" / "ob_read_write" / "v3_1_11" / "account-info-openapi.json",
        96,
        94,
    ),
    (
        "v4_0_1",
        "4.0.1",
        "v401",
        REPO_ROOT / "conformance" / "standards" / "ob_read_write" / "v4_0_1" / "account-info-openapi.json",
        95,
        93,
    ),
)


@pytest.mark.parametrize(
    ("directory", "version", "id_version", "openapi_path", "_parity_rows", "_retained_rows"),
    _VERSION_CASES,
)
def test_ais_catalogues_are_source_bound_and_referentially_complete(
    directory: str,
    version: str,
    id_version: str,
    openapi_path: Path,
    _parity_rows: int,
    _retained_rows: int,
) -> None:
    catalogue_root = _CATALOGUE_ROOT / directory
    requirements = load_requirements_catalogue(catalogue_root / "requirements.json")
    test_definitions = load_test_definition_catalogue(catalogue_root / "test-definitions.json")
    openapi = cast(JsonObject, json.loads(openapi_path.read_text(encoding="utf-8")))

    assert requirements.id == f"obl.ais-{id_version}.requirements"
    assert requirements.specification.version == version
    assert requirements.specification.requirements_scope == "ais"
    assert test_definitions.requirements_catalogue_id == requirements.id
    assert len(requirements.capabilities) == 12
    assert len(requirements.endpoints) == 29
    assert len(requirements.requirements) == 31
    assert len(test_definitions.test_definitions) == 135
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


@pytest.mark.parametrize(
    ("directory", "_version", "_id_version", "_openapi_path", "parity_rows", "retained_rows"),
    _VERSION_CASES,
)
def test_ais_parity_evidence_classifies_every_pinned_row(
    directory: str,
    _version: str,
    _id_version: str,
    _openapi_path: Path,
    parity_rows: int,
    retained_rows: int,
) -> None:
    fixture_root = _FIXTURE_ROOT / directory
    report = cast(JsonObject, json.loads((fixture_root / "parity-comparison.json").read_text(encoding="utf-8")))
    tests = load_test_definition_catalogue(_CATALOGUE_ROOT / directory / "test-definitions.json")
    classifications = cast(list[JsonObject], report["classifications"])
    baseline = cast(JsonObject, report["baseline"])
    summary = cast(JsonObject, report["summary"])
    replacement_ids = {str(definition.id) for definition in tests.test_definitions}
    baseline_path = (
        REPO_ROOT
        / "conformance"
        / "standards"
        / "ob_read_write"
        / (
            "v3_1_11/legacy/ob_3.1_accounts_transactions_fca.json"
            if directory == "v3_1_11"
            else "v4_0/legacy-ob_4.0_accounts_transactions_fca.json"
        )
    )
    baseline_document = cast(JsonObject, json.loads(baseline_path.read_text(encoding="utf-8")))
    baseline_rows = cast(list[JsonObject], baseline_document["scripts"])

    assert baseline["purpose"] == "migration-cross-check-only"
    assert baseline["digest"] == f"sha256:{hashlib.sha256(baseline_path.read_bytes()).hexdigest()}"
    assert len(classifications) == parity_rows
    assert len({item["sourceRow"] for item in classifications}) == parity_rows
    assert summary == {
        "genuine-omission": 0,
        "intentional-correction": 1,
        "obsolete-behavior": 1,
        "retained-coverage": retained_rows,
    }
    assert all(
        set(cast(list[str], item["replacementTestDefinitionIds"])) <= replacement_ids for item in classifications
    )
    for item in classifications:
        row_locator = str(item["sourceRow"]).rsplit("#", maxsplit=1)[1]
        row_index_text, script_id = row_locator.split(":")
        assert baseline_rows[int(row_index_text)]["id"] == script_id == item["legacyScriptId"]


@pytest.mark.parametrize(
    ("directory", "_version", "id_version", "_openapi_path", "_parity_rows", "_retained_rows"),
    _VERSION_CASES,
)
def test_ais_participant_compilation_matches_deterministic_golden(
    directory: str,
    _version: str,
    id_version: str,
    _openapi_path: Path,
    _parity_rows: int,
    _retained_rows: int,
) -> None:
    catalogue_root = _CATALOGUE_ROOT / directory
    fixture_root = _FIXTURE_ROOT / directory
    requirements = load_requirements_catalogue(catalogue_root / "requirements.json")
    tests = load_test_definition_catalogue(catalogue_root / "test-definitions.json")
    inputs = (
        load_suite_release(fixture_root / "suite-release.json"),
        requirements,
        tests,
        load_participant_plan(fixture_root / "participant-plan.json"),
    )

    first = compile_participant_plan(*inputs)
    second = compile_participant_plan(*inputs)

    assert first == second
    assert first.selection_valid is True
    assert len(first.capabilities) == 12
    inferred_consent = next(
        capability
        for capability in first.capabilities
        if capability.id == f"ais.{id_version}.capability.account-access-consent"
    )
    assert inferred_consent.origin == "inferred"
    assert len(first.test_instances) == 135
    assert dump_resolved_plan(first) == (fixture_root / "resolved-plan.json").read_text(encoding="utf-8")
    assert load_resolved_plan(fixture_root / "resolved-plan.json") == first

    manifest = generate_execution_manifest(first, requirements, tests)
    loaded_manifest = parse_execution_manifest(json.loads(dump_execution_manifest(manifest)))
    assert loaded_manifest == manifest
    assert any(step.request.method == "DELETE" for step in manifest.steps)
    assert any(binding.type == "query-parameter" for step in manifest.steps for binding in step.request.input_bindings)
    assert any(assertion.expected_statuses == (400, 403) for step in manifest.steps for assertion in step.assertions)
    assert any(assertion.type == "header-equals-request" for step in manifest.steps for assertion in step.assertions)


def test_ais_broken_cross_catalogue_reference_has_stable_diagnostic() -> None:
    requirements = load_requirements_catalogue(_CATALOGUE_ROOT / "v4_0_1" / "requirements.json")
    invalid_tests = load_test_definition_catalogue(_FIXTURE_ROOT / "test-definitions.invalid-reference.json")

    diagnostics = validate_catalogue_references(requirements, invalid_tests)

    assert len(diagnostics) == 1
    assert diagnostics[0].code is DiagnosticCode.REFERENCE_UNRESOLVED
    assert diagnostics[0].instance_path == "/testDefinitions/0/coveredRequirementIds/0"


def test_ais_capability_dependency_cycle_has_stable_diagnostic() -> None:
    path = _CATALOGUE_ROOT / "v4_0_1" / "requirements.json"
    document = cast(JsonObject, json.loads(path.read_text(encoding="utf-8")))
    capabilities = cast(list[JsonObject], document["capabilities"])
    consent_id = cast(str, capabilities[0]["id"])
    accounts_id = cast(str, capabilities[1]["id"])
    capabilities[0]["requiredCapabilityIds"] = [accounts_id]
    assert cast(list[str], capabilities[1]["requiredCapabilityIds"]) == [consent_id]

    with pytest.raises(ConfigurationContractError) as captured:
        parse_requirements_catalogue(document)

    assert captured.value.diagnostics[0].code is DiagnosticCode.DEPENDENCY_CYCLE
    assert captured.value.diagnostics[0].instance_path == "/capabilities/1/requiredCapabilityIds/0"


def test_ais_compiler_rejects_timezone_in_v401_transaction_input() -> None:
    catalogue_root = _CATALOGUE_ROOT / "v4_0_1"
    fixture_root = _FIXTURE_ROOT / "v4_0_1"
    document = cast(JsonObject, json.loads((fixture_root / "participant-plan.json").read_text(encoding="utf-8")))
    inputs = cast(list[JsonObject], document["predefinedInputs"])
    inputs[0]["value"] = "2026-01-01T00:00:00Z"

    with pytest.raises(ParticipantPlanCompilationError) as captured:
        compile_participant_plan(
            load_suite_release(fixture_root / "suite-release.json"),
            load_requirements_catalogue(catalogue_root / "requirements.json"),
            load_test_definition_catalogue(catalogue_root / "test-definitions.json"),
            parse_participant_plan(document),
        )

    assert captured.value.resolved_plan.findings[0].code == "plan.selection.input-invalid"
    assert captured.value.resolved_plan.findings[0].instance_path == "/predefinedInputs/0/value"


@pytest.mark.parametrize("dependency_kind", ["missing", "cycle"])
def test_ais_compiler_rejects_invalid_typed_capability_dependencies(
    dependency_kind: str,
) -> None:
    catalogue_root = _CATALOGUE_ROOT / "v4_0_1"
    fixture_root = _FIXTURE_ROOT / "v4_0_1"
    suite = load_suite_release(fixture_root / "suite-release.json")
    requirements = load_requirements_catalogue(catalogue_root / "requirements.json")
    consent, accounts, *remaining = requirements.capabilities
    if dependency_kind == "missing":
        accounts = replace(
            accounts,
            required_capability_ids=(StableId("ais.v401.capability.missing"),),
        )
        expected_code = DiagnosticCode.REFERENCE_UNRESOLVED
    else:
        consent = replace(consent, required_capability_ids=(accounts.id,))
        expected_code = DiagnosticCode.DEPENDENCY_CYCLE
    changed_requirements = replace(requirements, capabilities=(consent, accounts, *remaining))
    changed_digest = Sha256Digest(
        "sha256:" + hashlib.sha256(dump_requirements_catalogue(changed_requirements).encode()).hexdigest()
    )
    changed_artifacts = tuple(
        replace(artifact, digest=changed_digest) if artifact.kind == "requirements-catalogue" else artifact
        for artifact in suite.artifacts
    )

    with pytest.raises(ConfigurationContractError) as captured:
        compile_participant_plan(
            replace(suite, artifacts=changed_artifacts),
            changed_requirements,
            load_test_definition_catalogue(catalogue_root / "test-definitions.json"),
            load_participant_plan(fixture_root / "participant-plan.json"),
        )

    assert captured.value.diagnostics[0].code is expected_code
    assert captured.value.diagnostics[0].instance_path == "/capabilities/1/requiredCapabilityIds/0"


def _resolve_json_pointer(document: JsonObject, pointer: str) -> object:
    value: object = document
    for raw_part in pointer.removeprefix("/").split("/"):
        assert isinstance(value, dict)
        part = raw_part.replace("~1", "/").replace("~0", "~")
        value = value[part]
    return value
