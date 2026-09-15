"""Tests for the source-authored replacement DCR 3.4 catalogue family."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import cast

import pytest
import yaml

from conformance.configuration_contracts import (
    DiagnosticCode,
    HttpMethod,
    SelectionOrigin,
    compile_participant_plan,
    dump_requirements_catalogue,
    dump_resolved_plan,
    dump_test_definition_catalogue,
    generate_execution_manifest,
    load_participant_plan,
    load_requirements_catalogue,
    load_resolved_plan,
    load_suite_release,
    load_test_definition_catalogue,
    parse_test_definition_catalogue,
    validate_catalogue_references,
    verify_suite_release_artifacts,
)
from conformance.json_types import JsonObject
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.unit

_CATALOGUE_ROOT = REPO_ROOT / "conformance" / "configuration_contracts" / "catalogues" / "dcr" / "v3_4"
_FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "dcr"
_VERSION_FIXTURE_ROOT = _FIXTURE_ROOT / "v3_4"
_OPENAPI_PATH = REPO_ROOT / "conformance" / "standards" / "ob_dcr" / "v3_4" / "client-registration-openapi.yaml"
_PARITY_PATH = REPO_ROOT / "conformance" / "standards" / "ob_dcr" / "v3_4" / "parity-contract.json"


def test_dcr_catalogue_is_bound_to_the_authoritative_v34_operation_inventory() -> None:
    """Bind all four DCR operations to exact locations in the pinned OpenAPI."""
    requirements = load_requirements_catalogue(_CATALOGUE_ROOT / "requirements.json")
    openapi = cast(JsonObject, yaml.safe_load(_OPENAPI_PATH.read_text(encoding="utf-8")))

    assert requirements.specification.id == "dynamic-client-registration"
    assert requirements.specification.version == "3.4"
    assert requirements.specification.requirements_scope == "dcr"
    assert requirements.predefined_inputs == ()
    assert [(endpoint.method, endpoint.path, endpoint.operation_id) for endpoint in requirements.endpoints] == [
        (HttpMethod.POST, "/register", None),
        (HttpMethod.GET, "/register/{ClientId}", None),
        (HttpMethod.PUT, "/register/{ClientId}", None),
        (HttpMethod.DELETE, "/register/{ClientId}", None),
    ]

    technical_source = requirements.technical_sources[0]
    assert technical_source.digest == f"sha256:{hashlib.sha256(_OPENAPI_PATH.read_bytes()).hexdigest()}"
    for endpoint in requirements.endpoints:
        operation = _resolve_json_pointer(openapi, endpoint.source_pointer)
        assert endpoint.source_id == technical_source.id
        assert isinstance(operation, dict)
        assert "operationId" not in operation

    assert cast(list[str], _resolve_json_pointer(openapi, "/paths/~1register/post/tags"))[-1] == "Conditional"
    for method in ("get", "put", "delete"):
        assert (
            cast(list[str], _resolve_json_pointer(openapi, f"/paths/~1register~1{{ClientId}}/{method}/tags"))[-1]
            == "Optional"
        )


def test_dcr_capabilities_preserve_optional_scope_without_widening_assessment() -> None:
    """All four capabilities remain independently selectable assessment scope."""
    requirements = load_requirements_catalogue(_CATALOGUE_ROOT / "requirements.json")
    capabilities = {str(capability.id): capability for capability in requirements.capabilities}

    assert all(capability.selection == "conditional" for capability in capabilities.values())
    assert set(capabilities) == {
        "dcr.v34.capability.registration",
        "dcr.v34.capability.retrieval",
        "dcr.v34.capability.update",
        "dcr.v34.capability.deletion",
    }


def test_dcr_test_definitions_are_referentially_complete_and_protocol_explicit() -> None:
    """Keep test coverage separate from requirements while preserving DCR protocol constraints."""
    requirements = load_requirements_catalogue(_CATALOGUE_ROOT / "requirements.json")
    tests = load_test_definition_catalogue(_CATALOGUE_ROOT / "test-definitions.json")

    assert dump_requirements_catalogue(requirements) == (_CATALOGUE_ROOT / "requirements.json").read_text(
        encoding="utf-8"
    )
    assert dump_test_definition_catalogue(tests) == (_CATALOGUE_ROOT / "test-definitions.json").read_text(
        encoding="utf-8"
    )
    assert validate_catalogue_references(requirements, tests) == ()
    assert {
        requirement_id for definition in tests.test_definitions for requirement_id in definition.covered_requirement_ids
    } == {requirement.id for requirement in requirements.requirements if requirement.assessment == "tested"}
    assert all(not str(definition.id).startswith("DCR-") for definition in tests.test_definitions)
    assert len(tests.test_definitions) == 17

    by_id = {str(definition.id): definition for definition in tests.test_definitions}
    registration = by_id["dcr.v34.test.registration.positive"]
    assert registration.request.content_type == "application/json"
    assert registration.request.transport_profile == "mutual-tls"
    assert registration.request.authorization_profile is None
    assert registration.request.modifications[0].location == "request-body"
    assert registration.outputs[0].id == "dcr.v34.output.registration.client-id"
    assert {assertion.type for assertion in registration.assertions} == {
        "http-status",
        "response-schema",
        "json-present",
    }

    update = by_id["dcr.v34.test.update.positive"]
    assert update.request.transport_profile == "mutual-tls"
    assert update.request.authorization_profile == "oauth2-client-credentials"
    assert update.request.content_type == "application/json"
    deletion = by_id["dcr.v34.test.deletion.positive"]
    assert deletion.request.authorization_profile == "oauth2-client-credentials"
    assert deletion.request.transport_profile == "mutual-tls"


def test_dcr_management_plan_compiles_deterministically_to_golden() -> None:
    """Resolve optional management intent with inferred registration and stable output."""
    inputs = (
        load_suite_release(_VERSION_FIXTURE_ROOT / "suite-release.json"),
        load_requirements_catalogue(_CATALOGUE_ROOT / "requirements.json"),
        load_test_definition_catalogue(_CATALOGUE_ROOT / "test-definitions.json"),
        load_participant_plan(_VERSION_FIXTURE_ROOT / "participant-plan.json"),
    )

    first = compile_participant_plan(*inputs)
    second = compile_participant_plan(*inputs)

    assert first == second
    assert dump_resolved_plan(first) == (_VERSION_FIXTURE_ROOT / "resolved-plan.json").read_text(encoding="utf-8")
    assert load_resolved_plan(_VERSION_FIXTURE_ROOT / "resolved-plan.json") == first
    assert [(str(capability.id), capability.origin) for capability in first.capabilities] == [
        ("dcr.v34.capability.retrieval", SelectionOrigin.EXPLICIT),
        ("dcr.v34.capability.update", SelectionOrigin.EXPLICIT),
        ("dcr.v34.capability.deletion", SelectionOrigin.EXPLICIT),
    ]
    assert [str(endpoint.id) for endpoint in first.endpoints] == [
        "dcr.v34.endpoint.client-retrieval",
        "dcr.v34.endpoint.client-update",
        "dcr.v34.endpoint.client-deletion",
        "dcr.v34.endpoint.registration",
    ]
    assert first.endpoints[-1].requirement_ids == ()
    assert first.endpoints[-1].reasons[0].code == "tests.dependency.endpoint"
    assert first.test_instances[0].test_definition_id == "dcr.v34.test.registration.positive"
    assert first.test_instances[0].covered_requirement_ids == ()
    assert first.test_instances[0].reasons[0].code == "tests.dependency.required-by"
    assert all(requirement.capability_id != "dcr.v34.capability.registration" for requirement in first.requirements)
    assert {str(test_instance.test_definition_id) for test_instance in first.test_instances}.isdisjoint(
        {
            "dcr.v34.test.registration.expired",
            "dcr.v34.test.registration.invalid-issuer",
            "dcr.v34.test.registration.empty-issuer",
            "dcr.v34.test.registration.overlong-issuer",
            "dcr.v34.test.registration.invalid-auth-method",
        }
    )
    assert len(first.test_instances) == 12
    assert [finding.code for finding in first.findings] == ["plan.test.requirement-not-assessed"]
    assert first.findings[0].related_ids == ("dcr.v34.requirement.deletion-token-invalidation",)
    positions = {
        str(test_instance.test_definition_id): index for index, test_instance in enumerate(first.test_instances)
    }
    assert positions["dcr.v34.test.deletion.revoked-token"] < positions["dcr.v34.test.deletion.positive"]


def test_dcr_compilation_preserves_protocol_metadata_in_the_execution_manifest() -> None:
    """Carry DCR transport and authorization instructions through compilation."""
    suite_release = load_suite_release(_VERSION_FIXTURE_ROOT / "suite-release.json")
    requirements = load_requirements_catalogue(_CATALOGUE_ROOT / "requirements.json")
    tests = load_test_definition_catalogue(_CATALOGUE_ROOT / "test-definitions.json")
    participant_plan = load_participant_plan(_VERSION_FIXTURE_ROOT / "participant-plan.json")
    resolved = compile_participant_plan(suite_release, requirements, tests, participant_plan)

    first = generate_execution_manifest(resolved, requirements, tests)
    second = generate_execution_manifest(resolved, requirements, tests)
    steps = {str(step.test_definition_id): step for step in first.steps}

    assert first == second
    assert first.security_profile == "all"
    assert steps["dcr.v34.test.registration.positive"].request.content_type == "application/json"
    assert steps["dcr.v34.test.registration.positive"].request.transport_profile == "mutual-tls"
    update = steps["dcr.v34.test.update.positive"].request
    assert update.method is HttpMethod.PUT
    assert update.authorization_profile == "oauth2-client-credentials"
    assert update.transport_profile == "mutual-tls"
    assert {binding.type for binding in update.state_bindings} == {
        "authorization-client-id",
        "path-parameter",
    }
    assert steps["dcr.v34.test.deletion.positive"].request.method is HttpMethod.DELETE
    assert steps["dcr.v34.test.registration.positive"].outputs[0].id == ("dcr.v34.output.registration.client-id")
    schema_assertion = next(
        assertion
        for assertion in steps["dcr.v34.test.registration.positive"].assertions
        if assertion.type == "response-schema"
    )
    assert schema_assertion.schema_source_id == "ob-dcr-v34.openapi"
    assert any(artifact.id == "ob-dcr-v34.openapi" for artifact in first.provenance.artifacts)
    artifact_bytes = {
        (str(artifact.kind), str(artifact.id)): (REPO_ROOT / artifact.uri).read_bytes()
        for artifact in suite_release.artifacts
    }
    assert verify_suite_release_artifacts(suite_release, artifact_bytes) == ()


def test_dcr_parity_evidence_classifies_every_legacy_case_without_runtime_coupling() -> None:
    """Use the pinned legacy ledger only as complete migration cross-check evidence."""
    report = cast(
        JsonObject,
        json.loads((_VERSION_FIXTURE_ROOT / "parity-comparison.json").read_text(encoding="utf-8")),
    )
    parity = cast(JsonObject, json.loads(_PARITY_PATH.read_text(encoding="utf-8")))
    tests = load_test_definition_catalogue(_CATALOGUE_ROOT / "test-definitions.json")
    classifications = cast(list[JsonObject], report["classifications"])
    intentional_changes = cast(list[JsonObject], report["intentionalChanges"])
    documented_gaps = cast(list[JsonObject], report["documentedGaps"])
    summary = cast(JsonObject, report["summary"])
    replacement_ids = {str(definition.id) for definition in tests.test_definitions}

    legacy_case_ids = {
        cast(str, case["id"])
        for scenario in cast(list[JsonObject], parity["scenarios"])
        for case in cast(list[JsonObject], scenario["cases"])
    }
    assert report["baseline"] == {
        "digest": f"sha256:{hashlib.sha256(_PARITY_PATH.read_bytes()).hexdigest()}",
        "file": "parity-contract.json",
        "purpose": "migration-cross-check-only",
    }
    assert {cast(str, item["legacyCaseId"]) for item in classifications} == legacy_case_ids
    assert len(classifications) == 34
    assert summary == {
        "genuine-omission": 0,
        "intentional-correction": 2,
        "obsolete-behavior": 7,
        "retained-coverage": 25,
    }
    assert all(
        set(cast(list[str], item["replacementTestDefinitionIds"])) <= replacement_ids for item in classifications
    )
    assert [item["id"] for item in intentional_changes] == [f"dcr-v34-migration-{number:03d}" for number in range(1, 6)]
    assert {item["classification"] for item in intentional_changes} == {"intentional-correction"}
    assert documented_gaps == [
        {
            "classification": "genuine-omission",
            "id": "dcr.v34.requirement.deletion-token-invalidation",
            "rationale": (
                "The DCR operation inventory has no independent protected resource that can distinguish token "
                "invalidation from the deleted ClientId becoming unknown. The requirement remains in the "
                "requirements catalogue as documented-only and is surfaced as not assessed."
            ),
        }
    ]
    assert "parity-contract" not in (_CATALOGUE_ROOT / "requirements.json").read_text(encoding="utf-8")
    assert "parity-contract" not in (_CATALOGUE_ROOT / "test-definitions.json").read_text(encoding="utf-8")


def test_dcr_broken_cross_catalogue_reference_has_stable_diagnostic() -> None:
    """Report a missing requirement reference at its exact test-definition pointer."""
    requirements = load_requirements_catalogue(_CATALOGUE_ROOT / "requirements.json")
    invalid_tests = load_test_definition_catalogue(_FIXTURE_ROOT / "test-definitions.invalid-reference.json")

    diagnostics = validate_catalogue_references(requirements, invalid_tests)

    assert len(diagnostics) == 1
    assert diagnostics[0].code is DiagnosticCode.REFERENCE_UNRESOLVED
    assert diagnostics[0].instance_path == "/testDefinitions/0/coveredRequirementIds/0"


def test_state_bindings_reject_missing_and_non_dependency_outputs() -> None:
    """Validate cross-step dataflow before compilation."""
    requirements = load_requirements_catalogue(_CATALOGUE_ROOT / "requirements.json")
    raw_document = cast(
        JsonObject,
        json.loads((_CATALOGUE_ROOT / "test-definitions.json").read_text(encoding="utf-8")),
    )
    missing = deepcopy(raw_document)
    missing_definitions = cast(list[JsonObject], missing["testDefinitions"])
    missing_request = cast(JsonObject, missing_definitions[6]["request"])
    missing_bindings = cast(list[JsonObject], missing_request["stateBindings"])
    missing_bindings[0]["outputId"] = "dcr.v34.output.missing"
    missing_tests = parse_test_definition_catalogue(missing)

    missing_diagnostics = validate_catalogue_references(requirements, missing_tests)

    assert missing_diagnostics[0].code is DiagnosticCode.REFERENCE_UNRESOLVED
    assert missing_diagnostics[0].instance_path == "/testDefinitions/6/request/stateBindings/0/outputId"

    unrelated = deepcopy(raw_document)
    unrelated_definitions = cast(list[JsonObject], unrelated["testDefinitions"])
    unrelated_request = cast(JsonObject, unrelated_definitions[1]["request"])
    unrelated_request["stateBindings"] = [
        {
            "outputId": "dcr.v34.output.registration.client-id",
            "target": "ClientId",
            "type": "path-parameter",
        }
    ]
    unrelated_tests = parse_test_definition_catalogue(unrelated)

    unrelated_diagnostics = validate_catalogue_references(requirements, unrelated_tests)

    assert unrelated_diagnostics[0].code is DiagnosticCode.RULE_INCONSISTENT
    assert unrelated_diagnostics[0].instance_path == "/testDefinitions/1/request/stateBindings/0/outputId"


def _resolve_json_pointer(document: JsonObject, pointer: str) -> object:
    value: object = document
    for raw_part in pointer.removeprefix("/").split("/"):
        assert isinstance(value, dict)
        part = raw_part.replace("~1", "/").replace("~0", "~")
        value = value[part]
    return value
