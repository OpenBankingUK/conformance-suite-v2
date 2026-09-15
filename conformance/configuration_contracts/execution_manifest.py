"""Deterministic execution-manifest generation from resolved plans."""

from __future__ import annotations

import hashlib
from dataclasses import replace

from conformance.configuration_contracts.loader import (
    EXECUTION_MANIFEST_SCHEMA_VERSION,
    dump_requirements_catalogue,
    dump_test_definition_catalogue,
    execution_manifest_id,
    execution_manifest_to_document,
    parse_execution_manifest,
    validate_catalogue_references,
)
from conformance.configuration_contracts.models import (
    EvidenceMode,
    ExecutionEvidencePolicy,
    ExecutionManifest,
    ExecutionManifestAssertion,
    ExecutionManifestInput,
    ExecutionManifestProvenance,
    ExecutionManifestRequest,
    ExecutionManifestStep,
    RequirementsCatalogue,
    ResolvedPlan,
    StableId,
    TestDefinitionCatalogue,
)

_EXECUTION_MANIFEST_SCHEMA_ARTIFACT_ID = StableId("execution-manifest-v1")
_EMPTY_MANIFEST_DIGEST = "0" * 64


class ExecutionManifestGenerationError(ValueError):
    """Raised when resolved work cannot become an executable manifest."""


def generate_execution_manifest(
    resolved_plan: ResolvedPlan,
    requirements_catalogue: RequirementsCatalogue,
    test_definition_catalogue: TestDefinitionCatalogue,
) -> ExecutionManifest:
    """Generate deterministic runner instructions from a valid resolved plan.

    The initial manifest records protocol-neutral HTTP operations and delegates
    their execution to the explicit legacy Read/Write compatibility adapter.
    HTTP, OAuth, PSU, JWS, scheduling, and evidence implementation remain in
    the existing runtime until their catalogue families migrate.
    """
    _validate_generation_inputs(resolved_plan, requirements_catalogue, test_definition_catalogue)

    endpoints_by_id = {endpoint.id: endpoint for endpoint in requirements_catalogue.endpoints}
    definitions_by_id = {
        test_definition.id: test_definition for test_definition in test_definition_catalogue.test_definitions
    }
    selected_endpoint_ids = {endpoint.id for endpoint in resolved_plan.endpoints}
    resolved_inputs_by_id = {
        predefined_input.id: predefined_input for predefined_input in resolved_plan.predefined_inputs
    }
    step_id_by_instance_id = {
        test_instance.id: StableId(f"{test_instance.id!s}.request") for test_instance in resolved_plan.test_instances
    }

    inputs: list[ExecutionManifestInput] = []
    for predefined_input in resolved_plan.predefined_inputs:
        if not predefined_input.redacted and predefined_input.value is None:
            raise ExecutionManifestGenerationError(f"Resolved input {predefined_input.id!s} has no executable value")
        inputs.append(
            ExecutionManifestInput(
                id=predefined_input.id,
                source=predefined_input.source,
                value=predefined_input.value,
                redacted=predefined_input.redacted,
            )
        )

    steps: list[ExecutionManifestStep] = []
    preceding_instance_ids: set[StableId] = set()
    for test_instance in resolved_plan.test_instances:
        test_definition = definitions_by_id.get(test_instance.test_definition_id)
        if test_definition is None:
            raise ExecutionManifestGenerationError(
                f"Resolved test definition {test_instance.test_definition_id!s} does not exist"
            )
        if test_definition.request.endpoint_id not in selected_endpoint_ids:
            raise ExecutionManifestGenerationError(
                f"Resolved test {test_instance.id!s} references unselected endpoint "
                f"{test_definition.request.endpoint_id!s}"
            )
        endpoint = endpoints_by_id[test_definition.request.endpoint_id]
        applicable_requirement_ids = {requirement.id for requirement in resolved_plan.requirements}
        expected_coverage = tuple(
            requirement_id
            for requirement_id in test_definition.covered_requirement_ids
            if requirement_id in applicable_requirement_ids
        )
        if test_instance.covered_requirement_ids != expected_coverage:
            raise ExecutionManifestGenerationError(
                f"Resolved requirement coverage for {test_instance.id!s} differs from its test definition"
            )
        expected_dependency_ids = tuple(
            StableId(f"{dependency_id!s}.instance") for dependency_id in test_definition.dependencies
        )
        if test_instance.dependency_ids != expected_dependency_ids:
            raise ExecutionManifestGenerationError(
                f"Resolved dependencies for {test_instance.id!s} differ from its test definition"
            )
        forward_dependency_ids = tuple(
            dependency_id
            for dependency_id in test_instance.dependency_ids
            if dependency_id not in preceding_instance_ids
        )
        if forward_dependency_ids:
            raise ExecutionManifestGenerationError(
                f"Resolved test {test_instance.id!s} appears before dependencies: "
                + ", ".join(str(dependency_id) for dependency_id in forward_dependency_ids)
            )
        missing_input_ids = tuple(
            binding.input_id
            for binding in test_definition.request.input_bindings
            if binding.input_id not in resolved_inputs_by_id
        )
        if missing_input_ids:
            raise ExecutionManifestGenerationError(
                f"Resolved test {test_instance.id!s} is missing inputs: "
                + ", ".join(str(input_id) for input_id in missing_input_ids)
            )
        try:
            dependency_step_ids = tuple(
                step_id_by_instance_id[dependency_id] for dependency_id in test_instance.dependency_ids
            )
        except KeyError as error:
            raise ExecutionManifestGenerationError(
                f"Resolved dependency {error.args[0]!s} does not identify an executable test instance"
            ) from error
        steps.append(
            ExecutionManifestStep(
                id=step_id_by_instance_id[test_instance.id],
                test_instance_id=test_instance.id,
                test_definition_id=test_instance.test_definition_id,
                name=test_definition.name,
                dependency_ids=dependency_step_ids,
                covered_requirement_ids=test_instance.covered_requirement_ids,
                request=ExecutionManifestRequest(
                    method=endpoint.method,
                    path=endpoint.path,
                    input_bindings=test_definition.request.input_bindings,
                    modifications=test_definition.request.modifications,
                    state_bindings=test_definition.request.state_bindings,
                    content_type=test_definition.request.content_type,
                    transport_profile=test_definition.request.transport_profile,
                    authorization_profile=test_definition.request.authorization_profile,
                ),
                assertions=tuple(
                    ExecutionManifestAssertion(
                        id=assertion.id,
                        type=assertion.type,
                        expected_status=assertion.expected_status,
                        expected_statuses=assertion.expected_statuses,
                        schema_ref=assertion.schema_ref,
                        schema_source_id=assertion.schema_source_id,
                        header_name=assertion.header_name,
                        json_pointer=assertion.json_pointer,
                        expected_value=assertion.expected_value,
                    )
                    for assertion in test_definition.assertions
                ),
                outputs=test_definition.outputs,
                evidence=ExecutionEvidencePolicy(
                    request=EvidenceMode.MASKED,
                    response=EvidenceMode.MASKED,
                ),
            )
        )
        preceding_instance_ids.add(test_instance.id)

    source_provenance = resolved_plan.provenance
    provisional = ExecutionManifest(
        schema_version=EXECUTION_MANIFEST_SCHEMA_VERSION,
        document_type="execution-manifest",
        id=StableId(f"execution-manifest:{_EMPTY_MANIFEST_DIGEST}"),
        security_profile=resolved_plan.security_profile,
        inputs=tuple(inputs),
        steps=tuple(steps),
        provenance=ExecutionManifestProvenance(
            resolved_plan_id=resolved_plan.id,
            participant_plan_id=source_provenance.participant_plan_id,
            suite_release_id=source_provenance.suite_release_id,
            suite_release_version=source_provenance.suite_release_version,
            suite_published_at=source_provenance.suite_published_at,
            requirements_catalogue_id=source_provenance.requirements_catalogue_id,
            test_definition_catalogue_id=source_provenance.test_definition_catalogue_id,
            tool_releases=source_provenance.tool_releases,
            artifacts=source_provenance.artifacts,
        ),
    )
    manifest = replace(provisional, id=execution_manifest_id(provisional))
    try:
        parse_execution_manifest(execution_manifest_to_document(manifest))
    except ValueError as error:
        raise ExecutionManifestGenerationError("Generated execution manifest is invalid") from error
    return manifest


def _validate_generation_inputs(
    resolved_plan: ResolvedPlan,
    requirements_catalogue: RequirementsCatalogue,
    test_definition_catalogue: TestDefinitionCatalogue,
) -> None:
    if not resolved_plan.selection_valid:
        raise ExecutionManifestGenerationError("Cannot generate an execution manifest from an invalid resolved plan")
    if resolved_plan.provenance.requirements_catalogue_id != requirements_catalogue.id:
        raise ExecutionManifestGenerationError("Resolved-plan requirements catalogue provenance does not match")
    if resolved_plan.provenance.test_definition_catalogue_id != test_definition_catalogue.id:
        raise ExecutionManifestGenerationError("Resolved-plan test-definition catalogue provenance does not match")
    reference_diagnostics = validate_catalogue_references(requirements_catalogue, test_definition_catalogue)
    if reference_diagnostics:
        raise ExecutionManifestGenerationError(str(reference_diagnostics[0].message))
    _verify_catalogue_digest(
        resolved_plan,
        kind="requirements-catalogue",
        identifier=requirements_catalogue.id,
        content=dump_requirements_catalogue(requirements_catalogue).encode("utf-8"),
    )
    _verify_catalogue_digest(
        resolved_plan,
        kind="test-definition-catalogue",
        identifier=test_definition_catalogue.id,
        content=dump_test_definition_catalogue(test_definition_catalogue).encode("utf-8"),
    )
    referenced_source_ids = {
        assertion.schema_source_id
        for definition in test_definition_catalogue.test_definitions
        for assertion in definition.assertions
        if assertion.schema_source_id is not None
    }
    for source in requirements_catalogue.technical_sources:
        if source.id not in referenced_source_ids:
            continue
        artifact = next(
            (
                item
                for item in resolved_plan.provenance.artifacts
                if item.kind == "technical-source" and item.id == source.id
            ),
            None,
        )
        if artifact is None or artifact.digest != source.digest:
            raise ExecutionManifestGenerationError(
                f"Resolved-plan provenance does not bind technical source {source.id!s}"
            )
    if not any(
        artifact.kind == "json-schema"
        and artifact.id == _EXECUTION_MANIFEST_SCHEMA_ARTIFACT_ID
        and artifact.schema_version == EXECUTION_MANIFEST_SCHEMA_VERSION
        for artifact in resolved_plan.provenance.artifacts
    ):
        raise ExecutionManifestGenerationError(
            "Resolved-plan suite release does not bind execution-manifest schema version 1.0"
        )


def _verify_catalogue_digest(
    resolved_plan: ResolvedPlan,
    *,
    kind: str,
    identifier: StableId,
    content: bytes,
) -> None:
    artifact = next(
        (item for item in resolved_plan.provenance.artifacts if item.kind == kind and item.id == identifier),
        None,
    )
    if artifact is None:
        raise ExecutionManifestGenerationError(f"Resolved-plan provenance does not bind {kind} {identifier!s}")
    actual_digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    if artifact.digest != actual_digest:
        raise ExecutionManifestGenerationError(
            f"Supplied {kind} {identifier!s} does not match resolved-plan provenance"
        )
