"""Schema-authoritative loading for versioned configuration documents."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from functools import cache
from pathlib import Path
from typing import cast

from jsonschema import (  # type: ignore[import-untyped]  # runtime schema library lacks stubs
    Draft202012Validator,
    FormatChecker,
    SchemaError,
    ValidationError,
)
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable

from conformance.configuration_contracts.diagnostics import (
    ConfigurationContractError,
    ConfigurationDiagnostic,
    DiagnosticCode,
    DiagnosticSeverity,
)
from conformance.configuration_contracts.models import (
    ArtifactReference,
    Capability,
    CompilerFinding,
    CompilerFindingSeverity,
    Endpoint,
    HttpMethod,
    NormativeReference,
    ParticipantInput,
    ParticipantPlan,
    ParticipantSpecification,
    PredefinedInput,
    RequestInputBinding,
    Requirement,
    RequirementRule,
    RequirementsCatalogue,
    RequirementTargetType,
    ResolutionSource,
    ResolvedInput,
    ResolvedPlan,
    ResolvedPlanProvenance,
    ResolvedSelection,
    ResolvedTestInstance,
    Sha256Digest,
    SpecificationReference,
    StableId,
    StandingOrderFrequency,
    SuiteRelease,
    TestAssertion,
    TestDefinition,
    TestDefinitionCatalogue,
    TestRequest,
    ToolRelease,
)
from conformance.json_types import JsonObject, JsonValue

SUITE_RELEASE_SCHEMA_VERSION = "1.0"
"""Suite-release document version currently supported by this foundation."""

CATALOGUE_SCHEMA_VERSION = "1.0"
"""Requirements and test-definition document version supported by the skeleton."""

PARTICIPANT_PLAN_SCHEMA_VERSION = "1.0"
"""Participant-plan document version supported by the walking skeleton."""

RESOLVED_PLAN_SCHEMA_VERSION = "1.0"
"""Resolved-plan document version emitted by the walking-skeleton compiler."""

_SCHEMA_ROOT = Path(__file__).resolve().parent / "schemas" / "v1"
_COMMON_SCHEMA_ID = "https://schemas.openbanking.org.uk/conformance/v1/common.schema.json"
_SUITE_RELEASE_SCHEMA_ID = "https://schemas.openbanking.org.uk/conformance/v1/suite-release.schema.json"
_REQUIREMENTS_CATALOGUE_SCHEMA_ID = (
    "https://schemas.openbanking.org.uk/conformance/v1/requirements-catalogue.schema.json"
)
_TEST_DEFINITION_CATALOGUE_SCHEMA_ID = (
    "https://schemas.openbanking.org.uk/conformance/v1/test-definition-catalogue.schema.json"
)
_PARTICIPANT_PLAN_SCHEMA_ID = "https://schemas.openbanking.org.uk/conformance/v1/participant-plan.schema.json"
_RESOLVED_PLAN_SCHEMA_ID = "https://schemas.openbanking.org.uk/conformance/v1/resolved-plan.schema.json"
_SCHEMA_PATHS = {
    _COMMON_SCHEMA_ID: _SCHEMA_ROOT / "common.schema.json",
    _SUITE_RELEASE_SCHEMA_ID: _SCHEMA_ROOT / "suite-release.schema.json",
    _REQUIREMENTS_CATALOGUE_SCHEMA_ID: _SCHEMA_ROOT / "requirements-catalogue.schema.json",
    _TEST_DEFINITION_CATALOGUE_SCHEMA_ID: _SCHEMA_ROOT / "test-definition-catalogue.schema.json",
    _PARTICIPANT_PLAN_SCHEMA_ID: _SCHEMA_ROOT / "participant-plan.schema.json",
    _RESOLVED_PLAN_SCHEMA_ID: _SCHEMA_ROOT / "resolved-plan.schema.json",
}


def load_suite_release(path: Path) -> SuiteRelease:
    """Load a suite-release descriptor from JSON into immutable typed data.

    Raises:
        ConfigurationContractError: If the file cannot be read, decoded,
            schema-validated, or semantically validated.
    """
    return parse_suite_release(_load_json_document(path))


def load_requirements_catalogue(path: Path) -> RequirementsCatalogue:
    """Load a requirements catalogue into immutable typed data."""
    return parse_requirements_catalogue(_load_json_document(path))


def load_test_definition_catalogue(path: Path) -> TestDefinitionCatalogue:
    """Load a test-definition catalogue into immutable typed data."""
    return parse_test_definition_catalogue(_load_json_document(path))


def load_participant_plan(path: Path) -> ParticipantPlan:
    """Load participant intent into an immutable typed plan."""
    return parse_participant_plan(_load_json_document(path))


def load_resolved_plan(path: Path) -> ResolvedPlan:
    """Load a generated resolved plan into immutable typed data."""
    return parse_resolved_plan(_load_json_document(path))


def parse_suite_release(raw_document: object) -> SuiteRelease:
    """Validate decoded JSON and map it into an immutable suite release.

    Structural rules are owned exclusively by the external JSON Schema. This
    function only selects the schema version, invokes it, maps valid fields,
    and applies cross-item semantic checks that JSON Schema does not duplicate.

    Raises:
        ConfigurationContractError: If validation fails.
    """
    schema_version = _selected_suite_release_schema_version(raw_document)
    if schema_version is not None and schema_version != SUITE_RELEASE_SCHEMA_VERSION:
        raise ConfigurationContractError(
            (
                _diagnostic(
                    DiagnosticCode.SCHEMA_VERSION_UNSUPPORTED,
                    f"Unsupported suite-release schema version {schema_version!r}",
                    instance_path="/schemaVersion",
                ),
            )
        )

    validation_diagnostics = _validate_document(raw_document, schema_id=_SUITE_RELEASE_SCHEMA_ID)
    if validation_diagnostics:
        raise ConfigurationContractError(validation_diagnostics)

    document = _suite_release_from_schema_valid_document(cast(dict[str, object], raw_document))
    semantic_diagnostics = _validate_suite_release_semantics(document)
    if semantic_diagnostics:
        raise ConfigurationContractError(semantic_diagnostics)
    return document


def parse_requirements_catalogue(raw_document: object) -> RequirementsCatalogue:
    """Validate and map one schema-versioned requirements catalogue."""
    _reject_unsupported_schema_version(raw_document, document_name="requirements catalogue")
    validation_diagnostics = _validate_document(raw_document, schema_id=_REQUIREMENTS_CATALOGUE_SCHEMA_ID)
    if validation_diagnostics:
        raise ConfigurationContractError(validation_diagnostics)

    document = _requirements_catalogue_from_schema_valid_document(cast(dict[str, object], raw_document))
    semantic_diagnostics = _validate_requirements_catalogue_semantics(document)
    if semantic_diagnostics:
        raise ConfigurationContractError(semantic_diagnostics)
    return document


def parse_test_definition_catalogue(raw_document: object) -> TestDefinitionCatalogue:
    """Validate and map one schema-versioned test-definition catalogue."""
    _reject_unsupported_schema_version(raw_document, document_name="test-definition catalogue")
    validation_diagnostics = _validate_document(raw_document, schema_id=_TEST_DEFINITION_CATALOGUE_SCHEMA_ID)
    if validation_diagnostics:
        raise ConfigurationContractError(validation_diagnostics)

    document = _test_definition_catalogue_from_schema_valid_document(cast(dict[str, object], raw_document))
    semantic_diagnostics = _validate_test_definition_catalogue_semantics(document)
    if semantic_diagnostics:
        raise ConfigurationContractError(semantic_diagnostics)
    return document


def parse_participant_plan(raw_document: object) -> ParticipantPlan:
    """Validate and map one walking-skeleton participant plan."""
    _reject_unsupported_schema_version(raw_document, document_name="participant plan")
    validation_diagnostics = _validate_document(raw_document, schema_id=_PARTICIPANT_PLAN_SCHEMA_ID)
    if validation_diagnostics:
        raise ConfigurationContractError(validation_diagnostics)
    return _participant_plan_from_schema_valid_document(cast(dict[str, object], raw_document))


def parse_resolved_plan(raw_document: object) -> ResolvedPlan:
    """Validate and map one generated walking-skeleton resolved plan."""
    _reject_unsupported_schema_version(raw_document, document_name="resolved plan")
    validation_diagnostics = _validate_document(raw_document, schema_id=_RESOLVED_PLAN_SCHEMA_ID)
    if validation_diagnostics:
        raise ConfigurationContractError(validation_diagnostics)
    return _resolved_plan_from_schema_valid_document(cast(dict[str, object], raw_document))


def validate_catalogue_references(
    requirements_catalogue: RequirementsCatalogue,
    test_definition_catalogue: TestDefinitionCatalogue,
) -> tuple[ConfigurationDiagnostic, ...]:
    """Validate references across the separate requirements and test artefacts."""
    diagnostics: list[ConfigurationDiagnostic] = []
    if test_definition_catalogue.requirements_catalogue_id != requirements_catalogue.id:
        diagnostics.append(
            _diagnostic(
                DiagnosticCode.REFERENCE_UNRESOLVED,
                (
                    f"Requirements catalogue {test_definition_catalogue.requirements_catalogue_id!s} "
                    f"does not match supplied catalogue {requirements_catalogue.id!s}"
                ),
                instance_path="/requirementsCatalogueId",
            )
        )

    capability_ids = {capability.id for capability in requirements_catalogue.capabilities}
    endpoint_ids = {endpoint.id for endpoint in requirements_catalogue.endpoints}
    input_ids = {predefined_input.id for predefined_input in requirements_catalogue.predefined_inputs}
    requirement_ids = {requirement.id for requirement in requirements_catalogue.requirements}
    for test_index, test_definition in enumerate(test_definition_catalogue.test_definitions):
        base_path = f"/testDefinitions/{test_index}"
        if test_definition.capability_id not in capability_ids:
            diagnostics.append(
                _unresolved_reference_diagnostic(
                    test_definition.capability_id,
                    instance_path=f"{base_path}/capabilityId",
                    object_kind="capability",
                )
            )
        if test_definition.request.endpoint_id not in endpoint_ids:
            diagnostics.append(
                _unresolved_reference_diagnostic(
                    test_definition.request.endpoint_id,
                    instance_path=f"{base_path}/request/endpointId",
                    object_kind="endpoint",
                )
            )
        for requirement_index, requirement_id in enumerate(test_definition.covered_requirement_ids):
            if requirement_id not in requirement_ids:
                diagnostics.append(
                    _unresolved_reference_diagnostic(
                        requirement_id,
                        instance_path=f"{base_path}/coveredRequirementIds/{requirement_index}",
                        object_kind="requirement",
                    )
                )
        for binding_index, binding in enumerate(test_definition.request.input_bindings):
            if binding.input_id not in input_ids:
                diagnostics.append(
                    _unresolved_reference_diagnostic(
                        binding.input_id,
                        instance_path=f"{base_path}/request/inputBindings/{binding_index}/inputId",
                        object_kind="predefined input",
                    )
                )
    return tuple(diagnostics)


def verify_suite_release_artifacts(
    suite_release: SuiteRelease,
    artifact_bytes: Mapping[tuple[str, str], bytes],
) -> tuple[ConfigurationDiagnostic, ...]:
    """Verify every suite artefact reference against exact supplied bytes.

    The mapping key is ``(kind, id)`` because stable IDs are unique within
    their declared object kind and suite-release namespace.
    """
    diagnostics: list[ConfigurationDiagnostic] = []
    for index, artifact in enumerate(suite_release.artifacts):
        artifact_key = (str(artifact.kind), str(artifact.id))
        content = artifact_bytes.get(artifact_key)
        if content is None:
            diagnostics.append(
                _diagnostic(
                    DiagnosticCode.ARTIFACT_UNRESOLVED,
                    f"Referenced artefact {artifact.kind!s}/{artifact.id!s} was not supplied",
                    instance_path=f"/artifacts/{index}/uri",
                )
            )
            continue
        actual_digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if actual_digest != artifact.digest:
            diagnostics.append(
                _diagnostic(
                    DiagnosticCode.ARTIFACT_DIGEST_MISMATCH,
                    (
                        f"Artefact {artifact.kind!s}/{artifact.id!s} has digest "
                        f"{actual_digest}; expected {artifact.digest!s}"
                    ),
                    instance_path=f"/artifacts/{index}/digest",
                )
            )
    return tuple(diagnostics)


def suite_release_to_document(suite_release: SuiteRelease) -> JsonObject:
    """Convert an immutable suite release into its schema-owned wire shape."""
    return {
        "artifacts": [
            {
                "digest": str(artifact.digest),
                "id": str(artifact.id),
                "kind": str(artifact.kind),
                "mediaType": artifact.media_type,
                "schemaVersion": artifact.schema_version,
                "uri": artifact.uri,
            }
            for artifact in suite_release.artifacts
        ],
        "documentType": suite_release.document_type,
        "id": str(suite_release.id),
        "publishedAt": suite_release.published_at,
        "releaseVersion": suite_release.release_version,
        "schemaVersion": suite_release.schema_version,
        "toolReleases": [
            {"id": str(tool_release.id), "version": tool_release.version}
            for tool_release in suite_release.tool_releases
        ],
    }


def requirements_catalogue_to_document(catalogue: RequirementsCatalogue) -> JsonObject:
    """Convert an immutable requirements catalogue to its wire shape."""
    return {
        "capabilities": [
            {
                "description": capability.description,
                "id": str(capability.id),
                "name": capability.name,
                "requiredEndpointIds": [str(endpoint_id) for endpoint_id in capability.required_endpoint_ids],
                "selection": capability.selection,
            }
            for capability in catalogue.capabilities
        ],
        "documentType": catalogue.document_type,
        "endpoints": [
            {
                "id": str(endpoint.id),
                "method": endpoint.method.value,
                "operationId": endpoint.operation_id,
                "path": endpoint.path,
            }
            for endpoint in catalogue.endpoints
        ],
        "id": str(catalogue.id),
        "normativeReferences": [
            {
                "id": str(reference.id),
                "section": reference.section,
                "title": reference.title,
                "uri": reference.uri,
            }
            for reference in catalogue.normative_references
        ],
        "predefinedInputs": [
            {
                "description": predefined_input.description,
                "exampleValue": _frequency_to_document(predefined_input.example_value),
                "id": str(predefined_input.id),
                "label": predefined_input.label,
                "requiredForCapabilityIds": [
                    str(capability_id) for capability_id in predefined_input.required_for_capability_ids
                ],
                "sensitivity": predefined_input.sensitivity,
                "valueType": str(predefined_input.value_type),
            }
            for predefined_input in catalogue.predefined_inputs
        ],
        "requirements": [
            {
                "id": str(requirement.id),
                "normativeReferenceIds": [str(reference_id) for reference_id in requirement.normative_reference_ids],
                "rule": {
                    "capabilityId": str(requirement.rule.capability_id),
                    "targetId": str(requirement.rule.target_id),
                    "targetType": requirement.rule.target_type.value,
                    "type": requirement.rule.type,
                },
                "statement": requirement.statement,
            }
            for requirement in catalogue.requirements
        ],
        "schemaVersion": catalogue.schema_version,
        "scheme": str(catalogue.scheme),
        "specification": {
            "id": str(catalogue.specification.id),
            "requirementsScope": str(catalogue.specification.requirements_scope),
            "version": catalogue.specification.version,
        },
    }


def test_definition_catalogue_to_document(catalogue: TestDefinitionCatalogue) -> JsonObject:
    """Convert an immutable test-definition catalogue to its wire shape."""
    return {
        "documentType": catalogue.document_type,
        "id": str(catalogue.id),
        "requirementsCatalogueId": str(catalogue.requirements_catalogue_id),
        "schemaVersion": catalogue.schema_version,
        "testDefinitions": [
            {
                "assertions": [
                    {
                        "expectedStatus": assertion.expected_status,
                        "id": str(assertion.id),
                        "type": assertion.type,
                    }
                    for assertion in test_definition.assertions
                ],
                "capabilityId": str(test_definition.capability_id),
                "coveredRequirementIds": [
                    str(requirement_id) for requirement_id in test_definition.covered_requirement_ids
                ],
                "dependencies": [str(dependency_id) for dependency_id in test_definition.dependencies],
                "description": test_definition.description,
                "id": str(test_definition.id),
                "name": test_definition.name,
                "request": {
                    "endpointId": str(test_definition.request.endpoint_id),
                    "inputBindings": [
                        {
                            "inputId": str(binding.input_id),
                            "target": binding.target,
                            "transform": str(binding.transform),
                            "type": binding.type,
                        }
                        for binding in test_definition.request.input_bindings
                    ],
                },
            }
            for test_definition in catalogue.test_definitions
        ],
    }


def participant_plan_to_document(plan: ParticipantPlan) -> JsonObject:
    """Convert immutable participant intent to its schema-owned wire shape."""
    return {
        "documentType": plan.document_type,
        "id": str(plan.id),
        "predefinedInputs": [
            {
                "inputId": str(participant_input.input_id),
                "value": _frequency_to_document(participant_input.value),
            }
            for participant_input in plan.predefined_inputs
        ],
        "schemaVersion": plan.schema_version,
        "scheme": str(plan.scheme),
        "securityProfile": str(plan.security_profile),
        "selectedCapabilityIds": [str(capability_id) for capability_id in plan.selected_capability_ids],
        "specification": {
            "id": str(plan.specification.id),
            "requirementsScope": str(plan.specification.requirements_scope),
            "version": plan.specification.version,
        },
        "suiteReleaseId": str(plan.suite_release_id),
    }


def resolved_plan_to_document(plan: ResolvedPlan) -> JsonObject:
    """Convert a generated resolved plan to its deterministic wire shape."""
    return {
        "applicableRequirements": [_resolved_selection_to_document(item) for item in plan.applicable_requirements],
        "certificationEligible": plan.certification_eligible,
        "compilationAllowed": plan.compilation_allowed,
        "documentType": plan.document_type,
        "findings": [
            {
                "code": str(finding.code),
                "instancePath": finding.instance_path,
                "message": finding.message,
                "relatedIds": [str(related_id) for related_id in finding.related_ids],
                "severity": finding.severity.value,
            }
            for finding in plan.findings
        ],
        "id": str(plan.id),
        "provenance": {
            "artifacts": [_artifact_reference_to_document(artifact) for artifact in plan.provenance.artifacts],
            "participantPlanDigest": str(plan.provenance.participant_plan_digest),
            "participantPlanId": str(plan.provenance.participant_plan_id),
            "requirementsCatalogueId": str(plan.provenance.requirements_catalogue_id),
            "suiteReleaseId": str(plan.provenance.suite_release_id),
            "suiteReleaseDigest": str(plan.provenance.suite_release_digest),
            "suiteReleasePublishedAt": plan.provenance.suite_release_published_at,
            "suiteReleaseVersion": plan.provenance.suite_release_version,
            "testDefinitionCatalogueId": str(plan.provenance.test_definition_catalogue_id),
            "toolReleases": [
                {"id": str(tool_release.id), "version": tool_release.version}
                for tool_release in plan.provenance.tool_releases
            ],
        },
        "resolvedInputs": [
            {
                "id": str(resolved_input.id),
                "sensitivity": resolved_input.sensitivity,
                "source": resolved_input.source.value,
                "sourceIds": [str(source_id) for source_id in resolved_input.source_ids],
                "value": (None if resolved_input.value is None else _frequency_to_document(resolved_input.value)),
                "valueType": str(resolved_input.value_type),
            }
            for resolved_input in plan.resolved_inputs
        ],
        "schemaVersion": plan.schema_version,
        "securityProfile": str(plan.security_profile),
        "selectedCapabilities": [_resolved_selection_to_document(item) for item in plan.selected_capabilities],
        "selectedEndpoints": [_resolved_selection_to_document(item) for item in plan.selected_endpoints],
        "testInstances": [
            {
                "coveredRequirementIds": [str(requirement_id) for requirement_id in instance.covered_requirement_ids],
                "dependencyInstanceIds": [
                    str(dependency_instance_id) for dependency_instance_id in instance.dependency_instance_ids
                ],
                "id": str(instance.id),
                "source": instance.source.value,
                "sourceIds": [str(source_id) for source_id in instance.source_ids],
                "testDefinitionId": str(instance.test_definition_id),
            }
            for instance in plan.test_instances
        ],
        "valid": plan.valid,
    }


def dump_suite_release(suite_release: SuiteRelease) -> str:
    """Serialize a suite release deterministically with a trailing newline."""
    return json.dumps(suite_release_to_document(suite_release), indent=2, sort_keys=True) + "\n"


def dump_requirements_catalogue(catalogue: RequirementsCatalogue) -> str:
    """Serialize a requirements catalogue deterministically."""
    return json.dumps(requirements_catalogue_to_document(catalogue), indent=2, sort_keys=True) + "\n"


def dump_test_definition_catalogue(catalogue: TestDefinitionCatalogue) -> str:
    """Serialize a test-definition catalogue deterministically."""
    return json.dumps(test_definition_catalogue_to_document(catalogue), indent=2, sort_keys=True) + "\n"


def dump_participant_plan(plan: ParticipantPlan) -> str:
    """Serialize participant intent deterministically."""
    return json.dumps(participant_plan_to_document(plan), indent=2, sort_keys=True) + "\n"


def dump_resolved_plan(plan: ResolvedPlan) -> str:
    """Serialize a resolved plan deterministically."""
    return json.dumps(resolved_plan_to_document(plan), indent=2, sort_keys=True) + "\n"


def validate_bundled_schemas() -> tuple[ConfigurationDiagnostic, ...]:
    """Return diagnostics for malformed bundled schemas or broken references."""
    try:
        schemas, registry = _schema_catalog()
    except ConfigurationContractError as error:
        return error.diagnostics

    diagnostics: list[ConfigurationDiagnostic] = []
    for schema_id, schema in schemas.items():
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as error:
            diagnostics.append(
                _diagnostic(
                    DiagnosticCode.SCHEMA_DEFINITION_INVALID,
                    f"Schema {schema_id!r} is invalid: {error.message}",
                    instance_path="",
                    schema_path=_json_pointer(error.absolute_schema_path),
                )
            )
        diagnostics.extend(_validate_schema_references(schema_id, schema, registry))
    return tuple(diagnostics)


def _load_json_document(path: Path) -> object:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ConfigurationContractError(
            (
                _diagnostic(
                    DiagnosticCode.IO_READ_FAILED,
                    f"Unable to read configuration document: {error}",
                    instance_path="",
                ),
            )
        ) from error

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as error:
        raise ConfigurationContractError(
            (
                _diagnostic(
                    DiagnosticCode.JSON_INVALID,
                    f"Invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}",
                    instance_path="",
                ),
            )
        ) from error


def _reject_unsupported_schema_version(raw_document: object, *, document_name: str) -> None:
    schema_version = _selected_suite_release_schema_version(raw_document)
    if schema_version is not None and schema_version != CATALOGUE_SCHEMA_VERSION:
        raise ConfigurationContractError(
            (
                _diagnostic(
                    DiagnosticCode.SCHEMA_VERSION_UNSUPPORTED,
                    f"Unsupported {document_name} schema version {schema_version!r}",
                    instance_path="/schemaVersion",
                ),
            )
        )


def _selected_suite_release_schema_version(raw_document: object) -> str | None:
    if not isinstance(raw_document, Mapping):
        return None
    value = raw_document.get("schemaVersion")
    return value if isinstance(value, str) else None


def _validate_document(raw_document: object, *, schema_id: str) -> tuple[ConfigurationDiagnostic, ...]:
    schemas, registry = _schema_catalog()
    schema = schemas[schema_id]
    validator = Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
    try:
        errors = sorted(
            _without_redundant_unevaluated_property_errors(tuple(validator.iter_errors(raw_document))),
            key=_validation_error_sort_key,
        )
    except Unresolvable as error:
        return (
            _diagnostic(
                DiagnosticCode.SCHEMA_REFERENCE_UNRESOLVED,
                f"Schema reference could not be resolved: {error}",
                instance_path="",
            ),
        )
    return tuple(diagnostic for error in errors for diagnostic in _validation_error_diagnostics(error))


def _without_redundant_unevaluated_property_errors(
    errors: tuple[ValidationError, ...],
) -> tuple[ValidationError, ...]:
    specific_error_paths = tuple(
        tuple(error.absolute_path) for error in errors if error.validator != "unevaluatedProperties"
    )
    return tuple(
        error
        for error in errors
        if error.validator != "unevaluatedProperties"
        or not any(
            len(specific_path) > len(error.absolute_path)
            and specific_path[: len(error.absolute_path)] == tuple(error.absolute_path)
            for specific_path in specific_error_paths
        )
    )


@cache
def _schema_catalog() -> tuple[dict[str, JsonObject], Registry]:
    schemas: dict[str, JsonObject] = {}
    resources: list[tuple[str, Resource[JsonValue]]] = []
    for expected_schema_id, schema_path in _SCHEMA_PATHS.items():
        try:
            decoded: object = json.loads(schema_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ConfigurationContractError(
                (
                    _diagnostic(
                        DiagnosticCode.SCHEMA_DEFINITION_INVALID,
                        f"Unable to load bundled schema {schema_path.name!r}: {error}",
                        instance_path="",
                    ),
                )
            ) from error
        if not isinstance(decoded, dict) or any(not isinstance(key, str) for key in decoded):
            raise ConfigurationContractError(
                (
                    _diagnostic(
                        DiagnosticCode.SCHEMA_DEFINITION_INVALID,
                        f"Bundled schema {schema_path.name!r} must be a JSON object",
                        instance_path="",
                    ),
                )
            )
        schema = cast(JsonObject, decoded)
        if schema.get("$id") != expected_schema_id:
            raise ConfigurationContractError(
                (
                    _diagnostic(
                        DiagnosticCode.SCHEMA_DEFINITION_INVALID,
                        f"Bundled schema {schema_path.name!r} has an unexpected $id",
                        instance_path="/$id",
                    ),
                )
            )
        schemas[expected_schema_id] = schema
        resources.append((expected_schema_id, Resource.from_contents(schema)))
    return schemas, Registry().with_resources(resources)


def _validate_schema_references(
    schema_id: str,
    schema: JsonObject,
    registry: Registry,
) -> tuple[ConfigurationDiagnostic, ...]:
    resolver = registry.resolver(schema_id)
    diagnostics: list[ConfigurationDiagnostic] = []
    for schema_path, reference in _schema_references(schema):
        try:
            resolver.lookup(reference)
        except Unresolvable as error:
            diagnostics.append(
                _diagnostic(
                    DiagnosticCode.SCHEMA_REFERENCE_UNRESOLVED,
                    f"Schema reference {reference!r} could not be resolved: {error}",
                    instance_path="",
                    schema_path=schema_path,
                )
            )
    return tuple(diagnostics)


def _schema_references(value: JsonValue, path: tuple[str | int, ...] = ()) -> Iterable[tuple[str, str]]:
    if isinstance(value, Mapping):
        for key, member in value.items():
            member_path = (*path, key)
            if key == "$ref" and isinstance(member, str):
                yield _json_pointer(member_path), member
            else:
                yield from _schema_references(member, member_path)
    elif isinstance(value, list):
        for index, member in enumerate(value):
            yield from _schema_references(member, (*path, index))


def _suite_release_from_schema_valid_document(document: dict[str, object]) -> SuiteRelease:
    tool_releases = cast(list[dict[str, object]], document["toolReleases"])
    artifacts = cast(list[dict[str, object]], document["artifacts"])
    return SuiteRelease(
        schema_version=cast(str, document["schemaVersion"]),
        document_type=cast(str, document["documentType"]),
        id=StableId(cast(str, document["id"])),
        release_version=cast(str, document["releaseVersion"]),
        published_at=cast(str, document["publishedAt"]),
        tool_releases=tuple(
            ToolRelease(
                id=StableId(cast(str, tool_release["id"])),
                version=cast(str, tool_release["version"]),
            )
            for tool_release in tool_releases
        ),
        artifacts=tuple(
            ArtifactReference(
                id=StableId(cast(str, artifact["id"])),
                kind=StableId(cast(str, artifact["kind"])),
                media_type=cast(str, artifact["mediaType"]),
                schema_version=cast(str, artifact["schemaVersion"]),
                uri=cast(str, artifact["uri"]),
                digest=Sha256Digest(cast(str, artifact["digest"])),
            )
            for artifact in artifacts
        ),
    )


def _requirements_catalogue_from_schema_valid_document(document: dict[str, object]) -> RequirementsCatalogue:
    specification = cast(dict[str, object], document["specification"])
    normative_references = cast(list[dict[str, object]], document["normativeReferences"])
    capabilities = cast(list[dict[str, object]], document["capabilities"])
    endpoints = cast(list[dict[str, object]], document["endpoints"])
    predefined_inputs = cast(list[dict[str, object]], document["predefinedInputs"])
    requirements = cast(list[dict[str, object]], document["requirements"])
    return RequirementsCatalogue(
        schema_version=cast(str, document["schemaVersion"]),
        document_type=cast(str, document["documentType"]),
        id=StableId(cast(str, document["id"])),
        scheme=StableId(cast(str, document["scheme"])),
        specification=SpecificationReference(
            id=StableId(cast(str, specification["id"])),
            version=cast(str, specification["version"]),
            requirements_scope=StableId(cast(str, specification["requirementsScope"])),
        ),
        normative_references=tuple(
            NormativeReference(
                id=StableId(cast(str, reference["id"])),
                title=cast(str, reference["title"]),
                uri=cast(str, reference["uri"]),
                section=cast(str, reference["section"]),
            )
            for reference in normative_references
        ),
        capabilities=tuple(
            Capability(
                id=StableId(cast(str, capability["id"])),
                name=cast(str, capability["name"]),
                description=cast(str, capability["description"]),
                selection=cast(str, capability["selection"]),
                required_endpoint_ids=tuple(
                    StableId(endpoint_id) for endpoint_id in cast(list[str], capability["requiredEndpointIds"])
                ),
            )
            for capability in capabilities
        ),
        endpoints=tuple(
            Endpoint(
                id=StableId(cast(str, endpoint["id"])),
                method=HttpMethod(cast(str, endpoint["method"])),
                path=cast(str, endpoint["path"]),
                operation_id=cast(str, endpoint["operationId"]),
            )
            for endpoint in endpoints
        ),
        predefined_inputs=tuple(
            _predefined_input_from_document(predefined_input) for predefined_input in predefined_inputs
        ),
        requirements=tuple(_requirement_from_document(requirement) for requirement in requirements),
    )


def _predefined_input_from_document(document: dict[str, object]) -> PredefinedInput:
    example = cast(dict[str, object], document["exampleValue"])
    return PredefinedInput(
        id=StableId(cast(str, document["id"])),
        label=cast(str, document["label"]),
        description=cast(str, document["description"]),
        value_type=StableId(cast(str, document["valueType"])),
        required_for_capability_ids=tuple(
            StableId(capability_id) for capability_id in cast(list[str], document["requiredForCapabilityIds"])
        ),
        sensitivity=cast(str, document["sensitivity"]),
        example_value=StandingOrderFrequency(
            frequency_type=cast(str, example["frequencyType"]),
            count_per_period=cast(int | None, example.get("countPerPeriod")),
            point_in_time=cast(str | None, example.get("pointInTime")),
        ),
    )


def _requirement_from_document(document: dict[str, object]) -> Requirement:
    rule = cast(dict[str, object], document["rule"])
    return Requirement(
        id=StableId(cast(str, document["id"])),
        statement=cast(str, document["statement"]),
        rule=RequirementRule(
            type=cast(str, rule["type"]),
            capability_id=StableId(cast(str, rule["capabilityId"])),
            target_type=RequirementTargetType(cast(str, rule["targetType"])),
            target_id=StableId(cast(str, rule["targetId"])),
        ),
        normative_reference_ids=tuple(
            StableId(reference_id) for reference_id in cast(list[str], document["normativeReferenceIds"])
        ),
    )


def _test_definition_catalogue_from_schema_valid_document(document: dict[str, object]) -> TestDefinitionCatalogue:
    test_definitions = cast(list[dict[str, object]], document["testDefinitions"])
    return TestDefinitionCatalogue(
        schema_version=cast(str, document["schemaVersion"]),
        document_type=cast(str, document["documentType"]),
        id=StableId(cast(str, document["id"])),
        requirements_catalogue_id=StableId(cast(str, document["requirementsCatalogueId"])),
        test_definitions=tuple(_test_definition_from_document(definition) for definition in test_definitions),
    )


def _test_definition_from_document(document: dict[str, object]) -> TestDefinition:
    request = cast(dict[str, object], document["request"])
    bindings = cast(list[dict[str, object]], request["inputBindings"])
    assertions = cast(list[dict[str, object]], document["assertions"])
    return TestDefinition(
        id=StableId(cast(str, document["id"])),
        name=cast(str, document["name"]),
        description=cast(str, document["description"]),
        capability_id=StableId(cast(str, document["capabilityId"])),
        covered_requirement_ids=tuple(
            StableId(requirement_id) for requirement_id in cast(list[str], document["coveredRequirementIds"])
        ),
        dependencies=tuple(StableId(dependency_id) for dependency_id in cast(list[str], document["dependencies"])),
        request=TestRequest(
            endpoint_id=StableId(cast(str, request["endpointId"])),
            input_bindings=tuple(
                RequestInputBinding(
                    input_id=StableId(cast(str, binding["inputId"])),
                    type=cast(str, binding["type"]),
                    target=cast(str, binding["target"]),
                    transform=StableId(cast(str, binding["transform"])),
                )
                for binding in bindings
            ),
        ),
        assertions=tuple(
            TestAssertion(
                id=StableId(cast(str, assertion["id"])),
                type=cast(str, assertion["type"]),
                expected_status=cast(int, assertion["expectedStatus"]),
            )
            for assertion in assertions
        ),
    )


def _participant_plan_from_schema_valid_document(document: dict[str, object]) -> ParticipantPlan:
    specification = cast(dict[str, object], document["specification"])
    predefined_inputs = cast(list[dict[str, object]], document["predefinedInputs"])
    return ParticipantPlan(
        schema_version=cast(str, document["schemaVersion"]),
        document_type=cast(str, document["documentType"]),
        id=StableId(cast(str, document["id"])),
        suite_release_id=StableId(cast(str, document["suiteReleaseId"])),
        scheme=StableId(cast(str, document["scheme"])),
        specification=ParticipantSpecification(
            id=StableId(cast(str, specification["id"])),
            version=cast(str, specification["version"]),
            requirements_scope=StableId(cast(str, specification["requirementsScope"])),
        ),
        security_profile=StableId(cast(str, document["securityProfile"])),
        selected_capability_ids=tuple(
            StableId(capability_id) for capability_id in cast(list[str], document["selectedCapabilityIds"])
        ),
        predefined_inputs=tuple(
            ParticipantInput(
                input_id=StableId(cast(str, participant_input["inputId"])),
                value=_frequency_from_document(cast(dict[str, object], participant_input["value"])),
            )
            for participant_input in predefined_inputs
        ),
    )


def _resolved_plan_from_schema_valid_document(document: dict[str, object]) -> ResolvedPlan:
    provenance = cast(dict[str, object], document["provenance"])
    tool_releases = cast(list[dict[str, object]], provenance["toolReleases"])
    artifacts = cast(list[dict[str, object]], provenance["artifacts"])
    resolved_inputs = cast(list[dict[str, object]], document["resolvedInputs"])
    test_instances = cast(list[dict[str, object]], document["testInstances"])
    findings = cast(list[dict[str, object]], document["findings"])
    return ResolvedPlan(
        schema_version=cast(str, document["schemaVersion"]),
        document_type=cast(str, document["documentType"]),
        id=StableId(cast(str, document["id"])),
        valid=cast(bool, document["valid"]),
        compilation_allowed=cast(bool, document["compilationAllowed"]),
        certification_eligible=cast(bool, document["certificationEligible"]),
        security_profile=StableId(cast(str, document["securityProfile"])),
        selected_capabilities=tuple(
            _resolved_selection_from_document(selection)
            for selection in cast(list[dict[str, object]], document["selectedCapabilities"])
        ),
        selected_endpoints=tuple(
            _resolved_selection_from_document(selection)
            for selection in cast(list[dict[str, object]], document["selectedEndpoints"])
        ),
        applicable_requirements=tuple(
            _resolved_selection_from_document(selection)
            for selection in cast(list[dict[str, object]], document["applicableRequirements"])
        ),
        resolved_inputs=tuple(_resolved_input_from_document(resolved_input) for resolved_input in resolved_inputs),
        test_instances=tuple(_resolved_test_instance_from_document(instance) for instance in test_instances),
        findings=tuple(
            CompilerFinding(
                code=StableId(cast(str, finding["code"])),
                severity=CompilerFindingSeverity(cast(str, finding["severity"])),
                message=cast(str, finding["message"]),
                instance_path=cast(str, finding["instancePath"]),
                related_ids=tuple(StableId(value) for value in cast(list[str], finding["relatedIds"])),
            )
            for finding in findings
        ),
        provenance=ResolvedPlanProvenance(
            suite_release_id=StableId(cast(str, provenance["suiteReleaseId"])),
            suite_release_version=cast(str, provenance["suiteReleaseVersion"]),
            suite_release_published_at=cast(str, provenance["suiteReleasePublishedAt"]),
            tool_releases=tuple(
                ToolRelease(
                    id=StableId(cast(str, tool_release["id"])),
                    version=cast(str, tool_release["version"]),
                )
                for tool_release in tool_releases
            ),
            artifacts=tuple(_artifact_reference_from_document(artifact) for artifact in artifacts),
            participant_plan_id=StableId(cast(str, provenance["participantPlanId"])),
            participant_plan_digest=Sha256Digest(cast(str, provenance["participantPlanDigest"])),
            suite_release_digest=Sha256Digest(cast(str, provenance["suiteReleaseDigest"])),
            requirements_catalogue_id=StableId(cast(str, provenance["requirementsCatalogueId"])),
            test_definition_catalogue_id=StableId(cast(str, provenance["testDefinitionCatalogueId"])),
        ),
    )


def _resolved_selection_from_document(document: dict[str, object]) -> ResolvedSelection:
    return ResolvedSelection(
        id=StableId(cast(str, document["id"])),
        source=ResolutionSource(cast(str, document["source"])),
        source_ids=tuple(StableId(value) for value in cast(list[str], document["sourceIds"])),
    )


def _resolved_input_from_document(document: dict[str, object]) -> ResolvedInput:
    raw_value = document["value"]
    value: StandingOrderFrequency | None
    if raw_value is None:
        value = None
    else:
        value = _frequency_from_document(cast(dict[str, object], raw_value))
    return ResolvedInput(
        id=StableId(cast(str, document["id"])),
        value_type=StableId(cast(str, document["valueType"])),
        sensitivity=cast(str, document["sensitivity"]),
        source=ResolutionSource(cast(str, document["source"])),
        source_ids=tuple(StableId(source_id) for source_id in cast(list[str], document["sourceIds"])),
        value=value,
    )


def _resolved_test_instance_from_document(document: dict[str, object]) -> ResolvedTestInstance:
    return ResolvedTestInstance(
        id=StableId(cast(str, document["id"])),
        test_definition_id=StableId(cast(str, document["testDefinitionId"])),
        source=ResolutionSource(cast(str, document["source"])),
        source_ids=tuple(StableId(source_id) for source_id in cast(list[str], document["sourceIds"])),
        dependency_instance_ids=tuple(
            StableId(instance_id) for instance_id in cast(list[str], document["dependencyInstanceIds"])
        ),
        covered_requirement_ids=tuple(
            StableId(requirement_id) for requirement_id in cast(list[str], document["coveredRequirementIds"])
        ),
    )


def _artifact_reference_from_document(document: dict[str, object]) -> ArtifactReference:
    return ArtifactReference(
        id=StableId(cast(str, document["id"])),
        kind=StableId(cast(str, document["kind"])),
        media_type=cast(str, document["mediaType"]),
        schema_version=cast(str, document["schemaVersion"]),
        uri=cast(str, document["uri"]),
        digest=Sha256Digest(cast(str, document["digest"])),
    )


def _artifact_reference_to_document(artifact: ArtifactReference) -> JsonObject:
    return {
        "digest": str(artifact.digest),
        "id": str(artifact.id),
        "kind": str(artifact.kind),
        "mediaType": artifact.media_type,
        "schemaVersion": artifact.schema_version,
        "uri": artifact.uri,
    }


def _resolved_selection_to_document(selection: ResolvedSelection) -> JsonObject:
    return {
        "id": str(selection.id),
        "source": selection.source.value,
        "sourceIds": [str(source_id) for source_id in selection.source_ids],
    }


def _frequency_from_document(document: dict[str, object]) -> StandingOrderFrequency:
    return StandingOrderFrequency(
        frequency_type=cast(str, document["frequencyType"]),
        count_per_period=cast(int | None, document.get("countPerPeriod")),
        point_in_time=cast(str | None, document.get("pointInTime")),
    )


def _frequency_to_document(frequency: StandingOrderFrequency) -> JsonObject:
    document: JsonObject = {"frequencyType": frequency.frequency_type}
    if frequency.count_per_period is not None:
        document["countPerPeriod"] = frequency.count_per_period
    if frequency.point_in_time is not None:
        document["pointInTime"] = frequency.point_in_time
    return document


def _validate_requirements_catalogue_semantics(
    catalogue: RequirementsCatalogue,
) -> tuple[ConfigurationDiagnostic, ...]:
    diagnostics: list[ConfigurationDiagnostic] = []
    diagnostics.extend(
        _duplicate_id_diagnostics(
            (
                (str(reference.id), f"/normativeReferences/{index}/id")
                for index, reference in enumerate(catalogue.normative_references)
            ),
            object_kind="normative reference",
        )
    )
    diagnostics.extend(
        _duplicate_id_diagnostics(
            (
                (str(capability.id), f"/capabilities/{index}/id")
                for index, capability in enumerate(catalogue.capabilities)
            ),
            object_kind="capability",
        )
    )
    diagnostics.extend(
        _duplicate_id_diagnostics(
            ((str(endpoint.id), f"/endpoints/{index}/id") for index, endpoint in enumerate(catalogue.endpoints)),
            object_kind="endpoint",
        )
    )
    diagnostics.extend(
        _duplicate_id_diagnostics(
            (
                (str(predefined_input.id), f"/predefinedInputs/{index}/id")
                for index, predefined_input in enumerate(catalogue.predefined_inputs)
            ),
            object_kind="predefined input",
        )
    )
    diagnostics.extend(
        _duplicate_id_diagnostics(
            (
                (str(requirement.id), f"/requirements/{index}/id")
                for index, requirement in enumerate(catalogue.requirements)
            ),
            object_kind="requirement",
        )
    )

    reference_ids = {reference.id for reference in catalogue.normative_references}
    capability_ids = {capability.id for capability in catalogue.capabilities}
    endpoint_ids = {endpoint.id for endpoint in catalogue.endpoints}
    input_ids = {predefined_input.id for predefined_input in catalogue.predefined_inputs}
    for capability_index, capability in enumerate(catalogue.capabilities):
        for endpoint_index, endpoint_id in enumerate(capability.required_endpoint_ids):
            if endpoint_id not in endpoint_ids:
                diagnostics.append(
                    _unresolved_reference_diagnostic(
                        endpoint_id,
                        instance_path=f"/capabilities/{capability_index}/requiredEndpointIds/{endpoint_index}",
                        object_kind="endpoint",
                    )
                )
    for input_index, predefined_input in enumerate(catalogue.predefined_inputs):
        for capability_index, capability_id in enumerate(predefined_input.required_for_capability_ids):
            if capability_id not in capability_ids:
                diagnostics.append(
                    _unresolved_reference_diagnostic(
                        capability_id,
                        instance_path=f"/predefinedInputs/{input_index}/requiredForCapabilityIds/{capability_index}",
                        object_kind="capability",
                    )
                )
    target_ids = {
        RequirementTargetType.ENDPOINT: endpoint_ids,
        RequirementTargetType.PREDEFINED_INPUT: input_ids,
    }
    for requirement_index, requirement in enumerate(catalogue.requirements):
        if requirement.rule.capability_id not in capability_ids:
            diagnostics.append(
                _unresolved_reference_diagnostic(
                    requirement.rule.capability_id,
                    instance_path=f"/requirements/{requirement_index}/rule/capabilityId",
                    object_kind="capability",
                )
            )
        if requirement.rule.target_id not in target_ids[requirement.rule.target_type]:
            diagnostics.append(
                _unresolved_reference_diagnostic(
                    requirement.rule.target_id,
                    instance_path=f"/requirements/{requirement_index}/rule/targetId",
                    object_kind=requirement.rule.target_type.value,
                )
            )
        for reference_index, reference_id in enumerate(requirement.normative_reference_ids):
            if reference_id not in reference_ids:
                diagnostics.append(
                    _unresolved_reference_diagnostic(
                        reference_id,
                        instance_path=f"/requirements/{requirement_index}/normativeReferenceIds/{reference_index}",
                        object_kind="normative reference",
                    )
                )
    return tuple(diagnostics)


def _validate_test_definition_catalogue_semantics(
    catalogue: TestDefinitionCatalogue,
) -> tuple[ConfigurationDiagnostic, ...]:
    diagnostics = list(
        _duplicate_id_diagnostics(
            (
                (str(test_definition.id), f"/testDefinitions/{index}/id")
                for index, test_definition in enumerate(catalogue.test_definitions)
            ),
            object_kind="test definition",
        )
    )
    diagnostics.extend(
        _duplicate_id_diagnostics(
            (
                (
                    str(assertion.id),
                    f"/testDefinitions/{test_index}/assertions/{assertion_index}/id",
                )
                for test_index, test_definition in enumerate(catalogue.test_definitions)
                for assertion_index, assertion in enumerate(test_definition.assertions)
            ),
            object_kind="assertion",
        )
    )
    test_ids = {test_definition.id for test_definition in catalogue.test_definitions}
    for test_index, test_definition in enumerate(catalogue.test_definitions):
        for dependency_index, dependency_id in enumerate(test_definition.dependencies):
            if dependency_id not in test_ids:
                diagnostics.append(
                    _unresolved_reference_diagnostic(
                        dependency_id,
                        instance_path=f"/testDefinitions/{test_index}/dependencies/{dependency_index}",
                        object_kind="test definition",
                    )
                )
    diagnostics.extend(_dependency_cycle_diagnostics(catalogue))
    return tuple(diagnostics)


def _dependency_cycle_diagnostics(
    catalogue: TestDefinitionCatalogue,
) -> tuple[ConfigurationDiagnostic, ...]:
    definitions = {definition.id: definition for definition in catalogue.test_definitions}
    indexes = {definition.id: index for index, definition in enumerate(catalogue.test_definitions)}
    state: dict[StableId, int] = {}
    diagnostics: list[ConfigurationDiagnostic] = []

    def visit(test_id: StableId) -> None:
        state[test_id] = 1
        definition = definitions[test_id]
        for dependency_index, dependency_id in enumerate(definition.dependencies):
            if dependency_id not in definitions:
                continue
            if state.get(dependency_id) == 1:
                diagnostics.append(
                    _diagnostic(
                        DiagnosticCode.DEPENDENCY_CYCLE,
                        f"Test dependency {dependency_id!s} creates a cycle",
                        instance_path=f"/testDefinitions/{indexes[test_id]}/dependencies/{dependency_index}",
                    )
                )
            elif state.get(dependency_id, 0) == 0:
                visit(dependency_id)
        state[test_id] = 2

    for definition in catalogue.test_definitions:
        if state.get(definition.id, 0) == 0:
            visit(definition.id)
    return tuple(diagnostics)


def _validate_suite_release_semantics(suite_release: SuiteRelease) -> tuple[ConfigurationDiagnostic, ...]:
    diagnostics: list[ConfigurationDiagnostic] = []
    diagnostics.extend(
        _duplicate_id_diagnostics(
            (
                (str(tool_release.id), f"/toolReleases/{index}/id")
                for index, tool_release in enumerate(suite_release.tool_releases)
            ),
            object_kind="tool-release",
        )
    )
    diagnostics.extend(
        _duplicate_id_diagnostics(
            (
                (f"{artifact.kind!s}\0{artifact.id!s}", f"/artifacts/{index}/id")
                for index, artifact in enumerate(suite_release.artifacts)
            ),
            object_kind="artefact kind",
        )
    )
    diagnostics.extend(
        _diagnostic(
            DiagnosticCode.SUITE_RELEASE_SELF_REFERENCE,
            "A suite-release descriptor cannot bind its own bytes",
            instance_path=f"/artifacts/{index}/id",
        )
        for index, artifact in enumerate(suite_release.artifacts)
        if artifact.kind == suite_release.document_type and artifact.id == suite_release.id
    )
    return tuple(diagnostics)


def _duplicate_id_diagnostics(
    identifiers: Iterable[tuple[str, str]],
    *,
    object_kind: str,
) -> tuple[ConfigurationDiagnostic, ...]:
    seen: set[str] = set()
    diagnostics: list[ConfigurationDiagnostic] = []
    for identifier, instance_path in identifiers:
        if identifier in seen:
            display_identifier = identifier.rsplit("\0", maxsplit=1)[-1]
            diagnostics.append(
                _diagnostic(
                    DiagnosticCode.DUPLICATE_ID,
                    f"Stable ID {display_identifier!r} is duplicated within {object_kind}",
                    instance_path=instance_path,
                )
            )
        else:
            seen.add(identifier)
    return tuple(diagnostics)


def _unresolved_reference_diagnostic(
    identifier: StableId,
    *,
    instance_path: str,
    object_kind: str,
) -> ConfigurationDiagnostic:
    return _diagnostic(
        DiagnosticCode.REFERENCE_UNRESOLVED,
        f"Referenced {object_kind} {identifier!s} does not exist",
        instance_path=instance_path,
    )


def _validation_error_diagnostics(error: ValidationError) -> tuple[ConfigurationDiagnostic, ...]:
    unexpected_properties = (
        re.findall(r"'([^']+)'", error.message)
        if error.validator in {"additionalProperties", "unevaluatedProperties"}
        else []
    )
    if unexpected_properties:
        return tuple(
            _diagnostic(
                DiagnosticCode.SCHEMA_VALIDATION_FAILED,
                f"Property {property_name!r} is not allowed",
                instance_path=_json_pointer((*error.absolute_path, property_name)),
                schema_path=_json_pointer(error.absolute_schema_path),
            )
            for property_name in unexpected_properties
        )
    return (
        _diagnostic(
            DiagnosticCode.SCHEMA_VALIDATION_FAILED,
            error.message,
            instance_path=_json_pointer(error.absolute_path),
            schema_path=_json_pointer(error.absolute_schema_path),
        ),
    )


def _validation_error_sort_key(error: ValidationError) -> tuple[str, str, str]:
    return (
        _json_pointer(error.absolute_path),
        _json_pointer(error.absolute_schema_path),
        error.message,
    )


def _json_pointer(path: Iterable[object]) -> str:
    tokens = (str(token).replace("~", "~0").replace("/", "~1") for token in path)
    return "".join(f"/{token}" for token in tokens)


def _diagnostic(
    code: DiagnosticCode,
    message: str,
    *,
    instance_path: str,
    schema_path: str | None = None,
) -> ConfigurationDiagnostic:
    return ConfigurationDiagnostic(
        code=code,
        severity=DiagnosticSeverity.ERROR,
        message=message,
        instance_path=instance_path,
        schema_path=schema_path,
    )
