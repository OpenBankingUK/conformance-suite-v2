"""Tests for the configuration-driven PIS domestic-standing-order slice."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from conformance.configuration_contracts import (
    ConfigurationContractError,
    DiagnosticCode,
    dump_requirements_catalogue,
    dump_test_definition_catalogue,
    load_requirements_catalogue,
    load_suite_release,
    load_test_definition_catalogue,
    parse_requirements_catalogue,
    parse_test_definition_catalogue,
    validate_catalogue_references,
    verify_suite_release_artifacts,
)
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.unit

_BUNDLE_ROOT = REPO_ROOT / "conformance" / "configuration_contracts" / "bundles" / "pis-domestic-standing-order-v4_0"
_FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "v1"
_REQUIREMENTS_PATH = _BUNDLE_ROOT / "requirements.json"
_TEST_DEFINITIONS_PATH = _BUNDLE_ROOT / "test-definitions.json"


def test_walking_skeleton_catalogues_load_as_separate_immutable_documents() -> None:
    requirements = load_requirements_catalogue(_REQUIREMENTS_PATH)
    tests = load_test_definition_catalogue(_TEST_DEFINITIONS_PATH)

    assert requirements.document_type == "requirements-catalogue"
    assert tests.document_type == "test-definition-catalogue"
    assert tests.requirements_catalogue_id == requirements.id
    assert requirements.specification.id == "read-write-api"
    assert requirements.specification.version == "4.0"
    assert requirements.specification.requirements_scope == "pis"
    with pytest.raises(FrozenInstanceError):
        _set_attribute(requirements.capabilities[0], "selection", "mandatory")
    with pytest.raises(FrozenInstanceError):
        _set_attribute(tests.test_definitions[0], "name", "changed")
    assert dump_requirements_catalogue(requirements) == _REQUIREMENTS_PATH.read_text(encoding="utf-8")
    assert dump_test_definition_catalogue(tests) == _TEST_DEFINITIONS_PATH.read_text(encoding="utf-8")


def test_conditional_capability_requires_exact_openapi_operation_inventory() -> None:
    requirements = load_requirements_catalogue(_REQUIREMENTS_PATH)
    capability = requirements.capabilities[0]

    assert capability.id == "pis.domestic-standing-order"
    assert capability.selection == "conditional"
    assert capability.required_endpoint_ids == (
        "pis.dso.endpoint.consent-create",
        "pis.dso.endpoint.consent-read",
        "pis.dso.endpoint.order-create",
        "pis.dso.endpoint.order-read",
    )
    assert [(endpoint.method.value, endpoint.path, endpoint.operation_id) for endpoint in requirements.endpoints] == [
        ("POST", "/domestic-standing-order-consents", "CreateDomesticStandingOrderConsents"),
        ("GET", "/domestic-standing-order-consents/{ConsentId}", "GetDomesticStandingOrderConsentsConsentId"),
        ("POST", "/domestic-standing-orders", "CreateDomesticStandingOrders"),
        (
            "GET",
            "/domestic-standing-orders/{DomesticStandingOrderId}",
            "GetDomesticStandingOrdersDomesticStandingOrderId",
        ),
    ]
    assert {requirement.rule.type for requirement in requirements.requirements} == {"required-when-capability-selected"}


def test_frequency_is_logical_input_with_test_owned_request_bindings() -> None:
    requirements = load_requirements_catalogue(_REQUIREMENTS_PATH)
    tests = load_test_definition_catalogue(_TEST_DEFINITIONS_PATH)
    predefined_input = requirements.predefined_inputs[0]

    assert predefined_input.id == "pis.dso.input.frequency"
    assert predefined_input.example_value.frequency_type == "WEEK"
    assert predefined_input.example_value.point_in_time == "03"
    assert predefined_input.example_value.count_per_period is None

    bindings = [binding for definition in tests.test_definitions for binding in definition.request.input_bindings]
    assert len(bindings) == 2
    assert {binding.input_id for binding in bindings} == {predefined_input.id}
    assert {binding.target for binding in bindings} == {"/Data/Initiation/MandateRelatedInformation/Frequency"}
    assert {binding.transform for binding in bindings} == {"pis-v4-standing-order-frequency"}


def test_test_definitions_declare_coverage_and_dependency_chain() -> None:
    requirements = load_requirements_catalogue(_REQUIREMENTS_PATH)
    tests = load_test_definition_catalogue(_TEST_DEFINITIONS_PATH)
    definitions = {str(definition.id): definition for definition in tests.test_definitions}

    assert all(definition.covered_requirement_ids for definition in tests.test_definitions)
    assert {
        requirement_id for definition in tests.test_definitions for requirement_id in definition.covered_requirement_ids
    } == {requirement.id for requirement in requirements.requirements}
    assert definitions["pis.dso.test.consent-create"].dependencies == ()
    assert definitions["pis.dso.test.consent-read"].dependencies == ("pis.dso.test.consent-create",)
    assert definitions["pis.dso.test.order-create"].dependencies == ("pis.dso.test.consent-read",)
    assert definitions["pis.dso.test.order-read"].dependencies == ("pis.dso.test.order-create",)
    assert validate_catalogue_references(requirements, tests) == ()


def test_invalid_frequency_combination_is_rejected_by_schema() -> None:
    with pytest.raises(ConfigurationContractError) as captured:
        load_requirements_catalogue(_FIXTURE_ROOT / "requirements-catalogue.invalid-frequency.json")

    assert captured.value.diagnostics[0].code is DiagnosticCode.SCHEMA_VALIDATION_FAILED
    assert captured.value.diagnostics[0].instance_path == "/predefinedInputs/0/exampleValue"


def test_frequency_point_in_time_must_be_exactly_two_digits() -> None:
    raw_document: object = json.loads(_REQUIREMENTS_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_document, dict)
    predefined_inputs = raw_document["predefinedInputs"]
    assert isinstance(predefined_inputs, list)
    predefined_input = predefined_inputs[0]
    assert isinstance(predefined_input, dict)
    example_value = predefined_input["exampleValue"]
    assert isinstance(example_value, dict)
    example_value["pointInTime"] = "03\n"

    with pytest.raises(ConfigurationContractError) as captured:
        parse_requirements_catalogue(raw_document)

    assert captured.value.diagnostics[0].code is DiagnosticCode.SCHEMA_VALIDATION_FAILED
    assert captured.value.diagnostics[0].instance_path == "/predefinedInputs/0/exampleValue/pointInTime"


def test_broken_cross_catalogue_requirement_reference_is_reported() -> None:
    requirements = load_requirements_catalogue(_REQUIREMENTS_PATH)
    tests = load_test_definition_catalogue(_FIXTURE_ROOT / "test-definition-catalogue.invalid-reference.json")

    diagnostics = validate_catalogue_references(requirements, tests)

    assert len(diagnostics) == 1
    assert diagnostics[0].code is DiagnosticCode.REFERENCE_UNRESOLVED
    assert diagnostics[0].instance_path == "/testDefinitions/0/coveredRequirementIds/0"


def test_requirement_internal_references_are_validated() -> None:
    raw_document: object = json.loads(_REQUIREMENTS_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_document, dict)
    capabilities = raw_document["capabilities"]
    assert isinstance(capabilities, list)
    capability = capabilities[0]
    assert isinstance(capability, dict)
    capability["requiredEndpointIds"] = ["pis.dso.endpoint.missing"]

    with pytest.raises(ConfigurationContractError) as captured:
        parse_requirements_catalogue(raw_document)

    assert captured.value.diagnostics[0].code is DiagnosticCode.REFERENCE_UNRESOLVED
    assert captured.value.diagnostics[0].instance_path == "/capabilities/0/requiredEndpointIds/0"


def test_test_dependency_cycles_are_rejected() -> None:
    with pytest.raises(ConfigurationContractError) as captured:
        load_test_definition_catalogue(_FIXTURE_ROOT / "test-definition-catalogue.invalid-dependency-cycle.json")

    assert captured.value.diagnostics[0].code is DiagnosticCode.DEPENDENCY_CYCLE
    assert captured.value.diagnostics[0].instance_path == "/testDefinitions/1/dependencies/0"


def test_assertion_ids_are_unique_across_test_definitions() -> None:
    raw_document: object = json.loads(_TEST_DEFINITIONS_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_document, dict)
    test_definitions = raw_document["testDefinitions"]
    assert isinstance(test_definitions, list)
    first_definition = test_definitions[0]
    second_definition = test_definitions[1]
    assert isinstance(first_definition, dict)
    assert isinstance(second_definition, dict)
    first_assertions = first_definition["assertions"]
    second_assertions = second_definition["assertions"]
    assert isinstance(first_assertions, list)
    assert isinstance(second_assertions, list)
    first_assertion = first_assertions[0]
    second_assertion = second_assertions[0]
    assert isinstance(first_assertion, dict)
    assert isinstance(second_assertion, dict)
    second_assertion["id"] = first_assertion["id"]

    with pytest.raises(ConfigurationContractError) as captured:
        parse_test_definition_catalogue(raw_document)

    assert captured.value.diagnostics[0].code is DiagnosticCode.DUPLICATE_ID
    assert captured.value.diagnostics[0].instance_path == "/testDefinitions/1/assertions/0/id"


@pytest.mark.parametrize(
    ("path", "parser"),
    [
        (_REQUIREMENTS_PATH, parse_requirements_catalogue),
        (_TEST_DEFINITIONS_PATH, parse_test_definition_catalogue),
    ],
)
def test_walking_skeleton_documents_reject_unknown_properties(
    path: Path,
    parser: Callable[[object], object],
) -> None:
    raw_document: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw_document, dict)
    raw_document["unexpected"] = True

    with pytest.raises(ConfigurationContractError) as captured:
        parser(raw_document)

    assert captured.value.diagnostics[0].code is DiagnosticCode.SCHEMA_VALIDATION_FAILED
    assert captured.value.diagnostics[0].instance_path == "/unexpected"


def test_suite_release_binds_exact_catalogue_and_schema_bytes() -> None:
    suite_release = load_suite_release(_BUNDLE_ROOT / "suite-release.json")
    artifact_bytes = {
        (str(artifact.kind), str(artifact.id)): (REPO_ROOT / artifact.uri).read_bytes()
        for artifact in suite_release.artifacts
    }

    assert {(str(artifact.kind), str(artifact.id)) for artifact in suite_release.artifacts} == {
        ("json-schema", "participant-plan-v1"),
        ("json-schema", "requirements-catalogue-v1"),
        ("json-schema", "resolved-plan-v1"),
        ("json-schema", "test-definition-catalogue-v1"),
        ("requirements-catalogue", "obl.pis-domestic-standing-order-v4.requirements"),
        ("test-definition-catalogue", "obl.pis-domestic-standing-order-v4.tests"),
    }
    assert verify_suite_release_artifacts(suite_release, artifact_bytes) == ()


def _set_attribute(instance: object, name: str, value: object) -> None:
    setattr(instance, name, value)
