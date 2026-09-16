"""Execution-manifest generation from consolidated 2.0 catalogue authority."""

from __future__ import annotations

from dataclasses import replace

from conformance.configuration_contracts.models import (
    ExecutionManifestAssertion,
    ExecutionManifestInput,
    ExecutionManifestRequest,
    StableId,
)
from conformance.configuration_contracts.v2_loader import (
    execution_manifest_id,
    execution_manifest_to_document,
    parse_execution_manifest,
    validate_catalogue_references,
)
from conformance.configuration_contracts.v2_models import (
    ExecutionManifest,
    ExecutionManifestProvenance,
    ExecutionManifestStep,
    ResolvedPlan,
    TestDefinitionCatalogue,
)

_EMPTY_DIGEST = "0" * 64


class ExecutionManifestGenerationError(ValueError):
    """Raised when resolved work cannot become an immutable manifest."""


def generate_execution_manifest(
    resolved_plan: ResolvedPlan,
    catalogue: TestDefinitionCatalogue,
) -> ExecutionManifest:
    """Generate deterministic instructions preserving every executable link."""
    _validate_inputs(resolved_plan, catalogue)
    definitions = {item.id: item for item in catalogue.test_definitions}
    endpoints = {item.id: item for item in catalogue.endpoints}
    selected_endpoints = {item.id for item in resolved_plan.endpoints}
    resolved_inputs = {item.id: item for item in resolved_plan.predefined_inputs}
    step_ids = {item.id: StableId(f"{item.id!s}.request") for item in resolved_plan.test_instances}
    inputs = tuple(
        ExecutionManifestInput(
            id=item.id,
            source=item.source,
            value=item.value,
            redacted=item.redacted,
        )
        for item in resolved_plan.predefined_inputs
    )
    preceding: set[StableId] = set()
    steps: list[ExecutionManifestStep] = []
    for instance in resolved_plan.test_instances:
        definition = definitions.get(instance.test_definition_id)
        if definition is None:
            raise ExecutionManifestGenerationError(
                f"Resolved test definition {instance.test_definition_id!s} does not exist"
            )
        endpoint = endpoints.get(definition.request.endpoint_id)
        if endpoint is None or endpoint.id not in selected_endpoints:
            raise ExecutionManifestGenerationError(f"Resolved test {instance.id!s} references an unselected endpoint")
        expected_dependencies = tuple(StableId(f"{item!s}.instance") for item in definition.dependencies)
        if instance.dependency_ids != expected_dependencies:
            raise ExecutionManifestGenerationError(
                f"Resolved dependencies for {instance.id!s} differ from its definition"
            )
        if any(item not in preceding for item in instance.dependency_ids):
            raise ExecutionManifestGenerationError(f"Resolved test {instance.id!s} appears before its dependencies")
        missing_inputs = tuple(
            item.input_id for item in definition.request.input_bindings if item.input_id not in resolved_inputs
        )
        if missing_inputs:
            raise ExecutionManifestGenerationError(
                f"Resolved test {instance.id!s} is missing inputs: " + ", ".join(str(item) for item in missing_inputs)
            )
        steps.append(
            ExecutionManifestStep(
                id=step_ids[instance.id],
                test_instance_id=instance.id,
                test_definition_id=instance.test_definition_id,
                name=definition.name,
                dependency_ids=tuple(step_ids[item] for item in instance.dependency_ids),
                request=_manifest_request(definition.request),
                assertions=tuple(
                    ExecutionManifestAssertion(
                        id=item.id,
                        type=item.type,
                        expected_status=item.expected_status,
                        expected_statuses=item.expected_statuses,
                        schema_ref=item.schema_ref,
                        schema_source_id=item.schema_source_id,
                        header_name=item.header_name,
                        json_pointer=item.json_pointer,
                        expected_value=item.expected_value,
                    )
                    for item in definition.assertions
                ),
                outputs=definition.outputs,
                evidence=definition.evidence_policy,
            )
        )
        preceding.add(instance.id)
    provisional = ExecutionManifest(
        schema_version="2.0",
        document_type="execution-manifest",
        id=StableId(f"execution-manifest:{_EMPTY_DIGEST}"),
        security_profile=resolved_plan.security_profile,
        inputs=inputs,
        steps=tuple(steps),
        provenance=ExecutionManifestProvenance(
            resolved_plan_id=resolved_plan.id,
            participant_plan_id=resolved_plan.provenance.participant_plan_id,
            suite_release_id=resolved_plan.provenance.suite_release_id,
            suite_release_version=resolved_plan.provenance.suite_release_version,
            suite_published_at=resolved_plan.provenance.suite_published_at,
            test_definition_catalogue_id=resolved_plan.provenance.test_definition_catalogue_id,
            tool_releases=resolved_plan.provenance.tool_releases,
            artifacts=resolved_plan.provenance.artifacts,
        ),
    )
    manifest = replace(provisional, id=execution_manifest_id(provisional))
    try:
        return parse_execution_manifest(execution_manifest_to_document(manifest))
    except ValueError as error:
        raise ExecutionManifestGenerationError("Generated execution manifest is invalid") from error


def _validate_inputs(
    resolved_plan: ResolvedPlan,
    catalogue: TestDefinitionCatalogue,
) -> None:
    if not resolved_plan.selection_valid:
        raise ExecutionManifestGenerationError("Cannot generate an execution manifest from an invalid resolved plan")
    if resolved_plan.provenance.test_definition_catalogue_id != catalogue.id:
        raise ExecutionManifestGenerationError("Resolved-plan test catalogue provenance does not match")
    diagnostics = validate_catalogue_references(catalogue)
    if diagnostics:
        raise ExecutionManifestGenerationError(diagnostics[0].message)


def _manifest_request(request: object) -> ExecutionManifestRequest:
    """Detach a catalogue request from its endpoint link without losing instructions."""
    from conformance.configuration_contracts.v2_models import TestDefinitionRequest

    if not isinstance(request, TestDefinitionRequest):
        raise ExecutionManifestGenerationError("Test definition request has an unsupported model")
    return ExecutionManifestRequest(
        method=request.method,
        path=request.path,
        input_bindings=request.input_bindings,
        modifications=request.modifications,
        state_bindings=request.state_bindings,
        content_type=request.content_type,
        transport_profile=request.transport_profile,
        authorization_profile=request.authorization_profile,
        base_url_source=request.base_url_source,
        query_templates=request.query_templates,
        header_templates=request.header_templates,
        json_body_template=request.json_body_template,
        form_body_template=request.form_body_template,
        runtime_input_refs=request.runtime_input_refs,
        generated_values=request.generated_values,
        required_token_id=request.required_token_id,
        required_token_scope=request.required_token_scope,
        produced_token_id=request.produced_token_id,
        invalidate_produced_authorization_token=request.invalidate_produced_authorization_token,
        detached_jws=request.detached_jws,
        token_endpoint_auth=request.token_endpoint_auth,
        response_signature=request.response_signature,
        psu_authorization=request.psu_authorization,
        required_psu_authorization_step_id=request.required_psu_authorization_step_id,
    )
