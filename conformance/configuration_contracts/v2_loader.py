"""Schema-authoritative loading for consolidated 2.0 configuration contracts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from copy import deepcopy
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import cast

from jsonschema import (  # type: ignore[import-untyped]  # library lacks stubs
    Draft202012Validator,
    FormatChecker,
    SchemaError,
    ValidationError,
)
from referencing import Registry, Resource

from conformance.configuration_contracts.diagnostics import (
    ConfigurationContractError,
    ConfigurationDiagnostic,
    DiagnosticCode,
    DiagnosticSeverity,
)
from conformance.configuration_contracts.models import (
    ArtifactReference,
    Capability,
    CompilationFinding,
    DetachedJwsOmittedClaim,
    DetachedJwsProfile,
    DetachedJwsSource,
    Endpoint,
    EvidenceMode,
    ExecutionDetachedJws,
    ExecutionEvidencePolicy,
    ExecutionManifestAssertion,
    ExecutionManifestHeader,
    ExecutionManifestInput,
    ExecutionManifestRequest,
    ExecutionPsuAuthorization,
    ExecutionResponseSignature,
    ExecutionTokenEndpointAuth,
    FindingSeverity,
    FindingSourceDocument,
    GeneratedHeaderValue,
    GeneratedValueStrategy,
    HttpMethod,
    InputResolutionSource,
    ParticipantExecutionConfiguration,
    ParticipantInput,
    PredefinedInput,
    PredefinedInputValue,
    RequestBaseUrlSource,
    RequestInputBinding,
    RequestModification,
    RequestStateBinding,
    ResolutionReason,
    ResolvedCapability,
    ResponseSignatureSource,
    SelectionOrigin,
    Sha256Digest,
    StableId,
    StandingOrderFrequency,
    SuiteRelease,
    TechnicalSource,
    TestAssertion,
    TestOutput,
    TokenEndpointAuthSource,
    ToolRelease,
)
from conformance.configuration_contracts.v2_models import (
    ExecutionManifest,
    ExecutionManifestProvenance,
    ExecutionManifestStep,
    ParticipantPlan,
    ResolvedEndpoint,
    ResolvedPlan,
    ResolvedPlanProvenance,
    ResolvedPredefinedInput,
    ResolvedTestInstance,
    Specification,
    SuitePolicy,
    TestApplicability,
    TestDefinition,
    TestDefinitionCatalogue,
    TestDefinitionRequest,
)
from conformance.json_types import JsonObject, JsonValue

SCHEMA_VERSION = "2.0"
_ROOT = Path(__file__).resolve().parent
_SCHEMA_ROOT = _ROOT / "schemas" / "v2"
_SCHEMA_NAMES = (
    "common",
    "suite-release",
    "test-definition-catalogue",
    "participant-plan",
    "resolved-plan",
    "execution-manifest",
    "suite-policy",
)
_SCHEMA_IDS = {name: f"https://schemas.openbanking.org.uk/conformance/v2/{name}.schema.json" for name in _SCHEMA_NAMES}
_TEMPLATE_PLACEHOLDER = re.compile(r"\$\{(runtime|generated)\.([^}]+)\}")
_COMPATIBLE_INPUT_TRANSFORMS = {
    "string": frozenset({"identity", "identity-string"}),
    "date-time": frozenset({"identity-string", "rfc3339-date-time"}),
    "local-date-time": frozenset({"identity-string", "iso8601-local-date-time"}),
    "standing-order-frequency-v4": frozenset({"standing-order-frequency-object"}),
}
_SUPPORTED_MODIFICATION_GENERATORS = frozenset(
    {
        "ais-account-access-consent",
        "ais-accounts-basic-authorization",
        "ais-accounts-detail-authorization",
        "ais-balances-authorization",
        "ais-beneficiaries-basic-authorization",
        "ais-beneficiaries-detail-authorization",
        "ais-client-credentials-authorization",
        "ais-direct-debits-authorization",
        "ais-empty-permissions-consent",
        "ais-insufficient-permission-authorization",
        "ais-invalid-transaction-permissions-consent",
        "ais-offers-authorization",
        "ais-parties-authorization",
        "ais-products-authorization",
        "ais-scheduled-payments-authorization",
        "ais-standing-orders-detail-authorization",
        "ais-statements-detail-authorization",
        "ais-transactions-basic-authorization",
        "ais-transactions-detail-authorization",
        "dcr-v34-registration-jws",
        "dcr-v34-registration-jws-complete-claims",
        "expired-unix-time",
        "invalid-resource-id",
        "next-day-date-time-offset",
        "next-day-date-time-offset-milliseconds",
        "next-day-date-time-utc",
        "next-day-date-time-utc-milliseconds",
        "overlong-string-257",
        "overlong-string-351",
        "rfc3339-date-time-offset",
        "rfc3339-date-time-utc",
        "standing-order-frequency-count-and-point",
        "unique-payment-reference",
        "unknown-client-id",
        "uuid-v4",
        "uuid4-hex",
    }
)


def load_suite_release(path: Path) -> SuiteRelease:
    """Load a content-addressed suite release using the corrected contract."""
    return parse_suite_release(_load(path))


def load_test_definition_catalogue(path: Path) -> TestDefinitionCatalogue:
    """Load one manually authored executable test catalogue."""
    return parse_test_definition_catalogue(_load(path))


def load_participant_plan(path: Path) -> ParticipantPlan:
    """Load participant intent using the corrected public contract."""
    return parse_participant_plan(_load(path))


def load_resolved_plan(path: Path) -> ResolvedPlan:
    """Load generated resolved work using the corrected contract."""
    return parse_resolved_plan(_load(path))


def load_execution_manifest(path: Path) -> ExecutionManifest:
    """Load manifest-authoritative execution instructions."""
    return parse_execution_manifest(_load(path))


def load_suite_policy(path: Path) -> SuitePolicy:
    """Load released automated-assessment policy."""
    return parse_suite_policy(_load(path))


def parse_suite_release(raw: object) -> SuiteRelease:
    """Validate and map a 2.0 suite release."""
    _validate(raw, "suite-release")
    document = _suite_release_from_schema_valid_document(cast(dict[str, object], raw))
    diagnostics = _duplicates(
        ((str(item.id), f"/artifacts/{index}/id") for index, item in enumerate(document.artifacts)),
        kind_by_id=((str(item.kind), str(item.id)) for item in document.artifacts),
    )
    if diagnostics:
        raise ConfigurationContractError(diagnostics)
    return document


def parse_suite_policy(raw: object) -> SuitePolicy:
    """Validate and map a released automated-assessment policy."""
    _validate(raw, "suite-policy")
    document = cast(dict[str, object], raw)
    return SuitePolicy(
        schema_version=cast(str, document["schemaVersion"]),
        document_type=cast(str, document["documentType"]),
        id=StableId(cast(str, document["id"])),
        result_claim=cast(str, document["resultClaim"]),
        blocking_finding_severities=tuple(cast(list[str], document["blockingFindingSeverities"])),
    )


def parse_test_definition_catalogue(raw: object) -> TestDefinitionCatalogue:
    """Validate, map, and semantically check a consolidated catalogue."""
    _validate(raw, "test-definition-catalogue")
    document = cast(dict[str, object], raw)
    specification = cast(dict[str, object], document["specification"])
    technical_sources = cast(list[dict[str, object]], document["technicalSources"])
    capabilities = cast(list[dict[str, object]], document["capabilities"])
    endpoints = cast(list[dict[str, object]], document["endpoints"])
    predefined_inputs = cast(list[dict[str, object]], document["predefinedInputs"])
    definitions = cast(list[dict[str, object]], document["testDefinitions"])
    catalogue = TestDefinitionCatalogue(
        schema_version=cast(str, document["schemaVersion"]),
        document_type=cast(str, document["documentType"]),
        id=StableId(cast(str, document["id"])),
        scheme=StableId(cast(str, document["scheme"])),
        specification=Specification(
            id=StableId(cast(str, specification["id"])),
            version=cast(str, specification["version"]),
            test_scope=StableId(cast(str, specification["testScope"])),
        ),
        allowed_security_profiles=tuple(cast(list[str], document["allowedSecurityProfiles"])),
        technical_sources=tuple(
            TechnicalSource(
                id=StableId(cast(str, source["id"])),
                title=cast(str, source["title"]),
                uri=cast(str, source["uri"]),
                digest=Sha256Digest(cast(str, source["digest"])),
            )
            for source in technical_sources
        ),
        capabilities=tuple(
            Capability(
                id=StableId(cast(str, capability["id"])),
                name=cast(str, capability["name"]),
                description=cast(str, capability["description"]),
                selection=cast(str, capability["selection"]),
                required_endpoint_ids=tuple(
                    StableId(value) for value in cast(list[str], capability["requiredEndpointIds"])
                ),
                required_capability_ids=tuple(
                    StableId(value) for value in cast(list[str], capability.get("requiredCapabilityIds", []))
                ),
            )
            for capability in capabilities
        ),
        endpoints=tuple(
            Endpoint(
                id=StableId(cast(str, endpoint["id"])),
                method=HttpMethod(cast(str, endpoint["method"])),
                path=cast(str, endpoint["path"]),
                operation_id=cast(str | None, endpoint.get("operationId")),
                source_id=StableId(cast(str, endpoint["sourceId"])),
                source_pointer=cast(str, endpoint["sourcePointer"]),
            )
            for endpoint in endpoints
        ),
        predefined_inputs=tuple(_predefined_input_from_document(item) for item in predefined_inputs),
        test_definitions=tuple(_test_definition(item) for item in definitions),
    )
    diagnostics = validate_catalogue_references(catalogue)
    if diagnostics:
        raise ConfigurationContractError(diagnostics)
    return catalogue


def parse_participant_plan(raw: object) -> ParticipantPlan:
    """Validate and map public participant intent."""
    _validate(raw, "participant-plan")
    document = cast(dict[str, object], raw)
    specification = cast(dict[str, object], document["specification"])
    config = cast(dict[str, object] | None, document.get("executionConfiguration"))
    inputs = cast(list[dict[str, object]], document["predefinedInputs"])
    plan = ParticipantPlan(
        schema_version=cast(str, document["schemaVersion"]),
        document_type=cast(str, document["documentType"]),
        id=StableId(cast(str, document["id"])),
        suite_release_id=StableId(cast(str, document["suiteReleaseId"])),
        scheme=StableId(cast(str, document["scheme"])),
        specification=Specification(
            id=StableId(cast(str, specification["id"])),
            version=cast(str, specification["version"]),
            test_scope=StableId(cast(str, specification["testScope"])),
        ),
        security_profile=cast(str, document["securityProfile"]),
        selected_capability_ids=tuple(StableId(value) for value in cast(list[str], document["selectedCapabilityIds"])),
        predefined_inputs=tuple(
            ParticipantInput(
                input_id=StableId(cast(str, item["inputId"])),
                value=_input_value_from_document(cast(str | dict[str, object], item["value"])),
            )
            for item in inputs
        ),
        execution_configuration=(
            None
            if config is None
            else ParticipantExecutionConfiguration(
                security_environment=_immutable_mapping(config["securityEnvironment"]),
                compatibility_runtime_inputs=_immutable_mapping(config["compatibilityRuntimeInputs"]),
                dynamic_client_registration=_immutable_mapping(config["dynamicClientRegistration"]),
                metadata=_immutable_mapping(config["metadata"]),
            )
        ),
    )
    duplicate_diagnostics = _duplicates(
        (
            (str(item.input_id), f"/predefinedInputs/{index}/inputId")
            for index, item in enumerate(plan.predefined_inputs)
        )
    )
    if duplicate_diagnostics:
        raise ConfigurationContractError(duplicate_diagnostics)
    return plan


def parse_resolved_plan(raw: object) -> ResolvedPlan:
    """Validate and map deterministic resolved work."""
    _validate(raw, "resolved-plan")
    document = cast(dict[str, object], raw)
    specification = cast(dict[str, object], document["specification"])
    provenance = cast(dict[str, object], document["provenance"])
    return ResolvedPlan(
        schema_version=cast(str, document["schemaVersion"]),
        document_type=cast(str, document["documentType"]),
        id=StableId(cast(str, document["id"])),
        selection_valid=cast(bool, document["selectionValid"]),
        scheme=StableId(cast(str, document["scheme"])),
        specification=Specification(
            id=StableId(cast(str, specification["id"])),
            version=cast(str, specification["version"]),
            test_scope=StableId(cast(str, specification["testScope"])),
        ),
        security_profile=cast(str, document["securityProfile"]),
        capabilities=tuple(
            ResolvedCapability(
                id=StableId(cast(str, item["id"])),
                origin=SelectionOrigin(cast(str, item["origin"])),
                reasons=_resolution_reasons_from_document(item),
            )
            for item in cast(list[dict[str, object]], document["capabilities"])
        ),
        endpoints=tuple(
            ResolvedEndpoint(
                id=StableId(cast(str, item["id"])),
                origin=SelectionOrigin(cast(str, item["origin"])),
                reasons=_resolution_reasons_from_document(item),
            )
            for item in cast(list[dict[str, object]], document["endpoints"])
        ),
        predefined_inputs=tuple(
            ResolvedPredefinedInput(
                id=StableId(cast(str, item["id"])),
                source=InputResolutionSource(cast(str, item["source"])),
                value=_optional_input_value(item),
                redacted=cast(bool, item["redacted"]),
                reasons=_resolution_reasons_from_document(item),
            )
            for item in cast(list[dict[str, object]], document["predefinedInputs"])
        ),
        test_instances=tuple(
            ResolvedTestInstance(
                id=StableId(cast(str, item["id"])),
                test_definition_id=StableId(cast(str, item["testDefinitionId"])),
                dependency_ids=tuple(StableId(value) for value in cast(list[str], item["dependencyIds"])),
                reasons=_resolution_reasons_from_document(item),
            )
            for item in cast(list[dict[str, object]], document["testInstances"])
        ),
        findings=tuple(
            CompilationFinding(
                code=StableId(cast(str, item["code"])),
                severity=FindingSeverity(cast(str, item["severity"])),
                message=cast(str, item["message"]),
                source_document=FindingSourceDocument(cast(str, item["sourceDocument"])),
                instance_path=cast(str, item["instancePath"]),
                related_ids=tuple(StableId(value) for value in cast(list[str], item["relatedIds"])),
            )
            for item in cast(list[dict[str, object]], document["findings"])
        ),
        provenance=ResolvedPlanProvenance(
            participant_plan_id=StableId(cast(str, provenance["participantPlanId"])),
            suite_release_id=StableId(cast(str, provenance["suiteReleaseId"])),
            suite_release_version=cast(str, provenance["suiteReleaseVersion"]),
            suite_published_at=cast(str, provenance["suitePublishedAt"]),
            test_definition_catalogue_id=StableId(cast(str, provenance["testDefinitionCatalogueId"])),
            tool_releases=_tool_releases(provenance),
            artifacts=_artifacts(provenance),
        ),
    )


def parse_execution_manifest(raw: object) -> ExecutionManifest:
    """Validate and map a generated 2.0 execution manifest."""
    _validate(raw, "execution-manifest")
    document = cast(dict[str, object], raw)
    provenance = cast(dict[str, object], document["provenance"])
    return ExecutionManifest(
        schema_version=cast(str, document["schemaVersion"]),
        document_type=cast(str, document["documentType"]),
        id=StableId(cast(str, document["id"])),
        security_profile=cast(str, document["securityProfile"]),
        inputs=tuple(
            ExecutionManifestInput(
                id=StableId(cast(str, item["id"])),
                source=InputResolutionSource(cast(str, item["source"])),
                value=(
                    _input_value_from_document(cast(str | dict[str, object], item["value"]))
                    if "value" in item
                    else None
                ),
                redacted=cast(bool, item["redacted"]),
            )
            for item in cast(list[dict[str, object]], document["inputs"])
        ),
        steps=tuple(_execution_manifest_step(item) for item in cast(list[dict[str, object]], document["steps"])),
        provenance=ExecutionManifestProvenance(
            resolved_plan_id=StableId(cast(str, provenance["resolvedPlanId"])),
            participant_plan_id=StableId(cast(str, provenance["participantPlanId"])),
            suite_release_id=StableId(cast(str, provenance["suiteReleaseId"])),
            suite_release_version=cast(str, provenance["suiteReleaseVersion"]),
            suite_published_at=cast(str, provenance["suitePublishedAt"]),
            test_definition_catalogue_id=StableId(cast(str, provenance["testDefinitionCatalogueId"])),
            tool_releases=_tool_releases(provenance),
            artifacts=_artifacts(provenance),
        ),
    )


def validate_catalogue_references(
    catalogue: TestDefinitionCatalogue,
) -> tuple[ConfigurationDiagnostic, ...]:
    """Validate every catalogue-owned reference, uniqueness rule, and cycle."""
    diagnostics: list[ConfigurationDiagnostic] = []
    diagnostics.extend(
        _duplicates(
            (str(item.id), f"/technicalSources/{index}/id") for index, item in enumerate(catalogue.technical_sources)
        )
    )
    diagnostics.extend(
        _duplicates((str(item.id), f"/capabilities/{index}/id") for index, item in enumerate(catalogue.capabilities))
    )
    diagnostics.extend(
        _duplicates((str(item.id), f"/endpoints/{index}/id") for index, item in enumerate(catalogue.endpoints))
    )
    diagnostics.extend(
        _duplicates(
            (str(item.id), f"/predefinedInputs/{index}/id") for index, item in enumerate(catalogue.predefined_inputs)
        )
    )
    diagnostics.extend(
        _duplicates(
            (str(item.id), f"/testDefinitions/{index}/id") for index, item in enumerate(catalogue.test_definitions)
        )
    )
    source_ids = {item.id for item in catalogue.technical_sources}
    capability_ids = {item.id for item in catalogue.capabilities}
    endpoint_ids = {item.id for item in catalogue.endpoints}
    endpoints_by_id = {item.id: item for item in catalogue.endpoints}
    input_ids = {item.id for item in catalogue.predefined_inputs}
    definitions = {item.id: item for item in catalogue.test_definitions}
    inputs_by_id = {item.id: item for item in catalogue.predefined_inputs}
    for index, endpoint in enumerate(catalogue.endpoints):
        if endpoint.source_id not in source_ids:
            diagnostics.append(_unresolved(endpoint.source_id, f"/endpoints/{index}/sourceId", "technical source"))
    for index, capability in enumerate(catalogue.capabilities):
        for ref_index, endpoint_id in enumerate(capability.required_endpoint_ids):
            if endpoint_id not in endpoint_ids:
                diagnostics.append(
                    _unresolved(
                        endpoint_id,
                        f"/capabilities/{index}/requiredEndpointIds/{ref_index}",
                        "endpoint",
                    )
                )
        for ref_index, capability_id in enumerate(capability.required_capability_ids):
            if capability_id not in capability_ids:
                diagnostics.append(
                    _unresolved(
                        capability_id,
                        f"/capabilities/{index}/requiredCapabilityIds/{ref_index}",
                        "capability",
                    )
                )
    for index, item in enumerate(catalogue.predefined_inputs):
        for ref_index, capability_id in enumerate(item.required_for_capability_ids):
            if capability_id not in capability_ids:
                diagnostics.append(
                    _unresolved(
                        capability_id,
                        f"/predefinedInputs/{index}/requiredForCapabilityIds/{ref_index}",
                        "capability",
                    )
                )
    output_producers: dict[StableId, StableId] = {}
    for test_index, definition in enumerate(catalogue.test_definitions):
        base = f"/testDefinitions/{test_index}"
        for capability_index, capability_id in enumerate(definition.applicability.capability_ids):
            if capability_id not in capability_ids:
                diagnostics.append(
                    _unresolved(
                        capability_id,
                        f"{base}/applicability/capabilityIds/{capability_index}",
                        "capability",
                    )
                )
        if definition.request.endpoint_id not in endpoint_ids:
            diagnostics.append(_unresolved(definition.request.endpoint_id, f"{base}/request/endpointId", "endpoint"))
        else:
            endpoint = endpoints_by_id[definition.request.endpoint_id]
            if definition.request.method is not endpoint.method:
                diagnostics.append(
                    _diagnostic(
                        DiagnosticCode.RULE_INCONSISTENT,
                        "Test request method does not match its linked endpoint",
                        f"{base}/request/method",
                    )
                )
            if definition.request.base_url_source is None:
                diagnostics.append(
                    _diagnostic(
                        DiagnosticCode.RULE_INCONSISTENT,
                        "Test request must declare its base URL source",
                        f"{base}/request/baseUrlSource",
                    )
                )
            if not _request_path_matches_endpoint(definition.request.path, endpoint.path):
                diagnostics.append(
                    _diagnostic(
                        DiagnosticCode.RULE_INCONSISTENT,
                        "Test request path does not match its linked endpoint",
                        f"{base}/request/path",
                    )
                )
        for binding_index, binding in enumerate(definition.request.input_bindings):
            if binding.input_id not in input_ids:
                diagnostics.append(
                    _unresolved(
                        binding.input_id,
                        f"{base}/request/inputBindings/{binding_index}/inputId",
                        "predefined input",
                    )
                )
            elif not _binding_transform_matches_input(
                value_type=str(inputs_by_id[binding.input_id].value_type),
                transform=str(binding.transform),
            ):
                diagnostics.append(
                    _diagnostic(
                        DiagnosticCode.RULE_INCONSISTENT,
                        (
                            f"Input transform {binding.transform!s} is incompatible with "
                            f"{inputs_by_id[binding.input_id].value_type!s}"
                        ),
                        f"{base}/request/inputBindings/{binding_index}/transform",
                    )
                )
        diagnostics.extend(_request_template_diagnostics(definition.request, base=base))
        if (
            definition.request.base_url_source is RequestBaseUrlSource.RESOURCE
            and definition.request.required_token_id is None
            and not _request_explicitly_omits_authorization(definition.request)
        ):
            diagnostics.append(
                _diagnostic(
                    DiagnosticCode.RULE_INCONSISTENT,
                    "Protected resource request must declare its required token or explicitly omit authorization",
                    f"{base}/request/requiredTokenId",
                )
            )
        for modification_index, modification in enumerate(definition.request.modifications):
            if (
                modification.generator is not None
                and str(modification.generator) not in _SUPPORTED_MODIFICATION_GENERATORS
            ):
                diagnostics.append(
                    _diagnostic(
                        DiagnosticCode.RULE_INCONSISTENT,
                        f"Unsupported request generator {modification.generator!s}",
                        f"{base}/request/modifications/{modification_index}/generator",
                    )
                )
        request_header_names = {item.name.lower() for item in definition.request.header_templates}
        for assertion_index, assertion in enumerate(definition.assertions):
            if (
                assertion.type == "header-equals-request"
                and (assertion.header_name or "").lower() not in request_header_names
            ):
                diagnostics.append(
                    _diagnostic(
                        DiagnosticCode.RULE_INCONSISTENT,
                        f"Request does not declare header {assertion.header_name!r} required by playback assertion",
                        f"{base}/assertions/{assertion_index}/headerName",
                    )
                )
        for dependency_index, dependency_id in enumerate(definition.dependencies):
            if dependency_id not in definitions:
                diagnostics.append(
                    _unresolved(
                        dependency_id,
                        f"{base}/dependencies/{dependency_index}",
                        "test definition",
                    )
                )
        diagnostics.extend(
            _duplicates(
                (
                    (str(assertion.id), f"{base}/assertions/{index}/id")
                    for index, assertion in enumerate(definition.assertions)
                )
            )
        )
        diagnostics.extend(
            _duplicates(
                ((str(output.id), f"{base}/outputs/{index}/id") for index, output in enumerate(definition.outputs))
            )
        )
        for output_index, output in enumerate(definition.outputs):
            if output.id in output_producers:
                diagnostics.append(_duplicate(str(output.id), f"{base}/outputs/{output_index}/id"))
            output_producers[output.id] = definition.id
        for assertion_index, assertion in enumerate(definition.assertions):
            if assertion.schema_source_id is not None and assertion.schema_source_id not in source_ids:
                diagnostics.append(
                    _unresolved(
                        assertion.schema_source_id,
                        f"{base}/assertions/{assertion_index}/schemaSourceId",
                        "technical source",
                    )
                )
    diagnostics.extend(_cycle_diagnostics(catalogue.capabilities, "capabilities", "required_capability_ids"))
    diagnostics.extend(_cycle_diagnostics(catalogue.test_definitions, "testDefinitions", "dependencies"))
    for test_index, definition in enumerate(catalogue.test_definitions):
        closure = _dependency_closure(definition.id, definitions)
        for binding_index, state_binding in enumerate(definition.request.state_bindings):
            producer = output_producers.get(state_binding.output_id)
            path = f"/testDefinitions/{test_index}/request/stateBindings/{binding_index}/outputId"
            if producer is None:
                diagnostics.append(_unresolved(state_binding.output_id, path, "test output"))
            elif producer not in closure:
                diagnostics.append(
                    _diagnostic(
                        DiagnosticCode.RULE_INCONSISTENT,
                        f"Test output {state_binding.output_id!s} is not produced by a dependency",
                        path,
                    )
                )
            if state_binding.type == "path-parameter" and f"{{{state_binding.target}}}" not in definition.request.path:
                diagnostics.append(
                    _diagnostic(
                        DiagnosticCode.RULE_INCONSISTENT,
                        f"State binding target {state_binding.target!r} is absent from the request path",
                        f"/testDefinitions/{test_index}/request/stateBindings/{binding_index}/target",
                    )
                )
    return tuple(diagnostics)


def _binding_transform_matches_input(*, value_type: str, transform: str) -> bool:
    return transform in _COMPATIBLE_INPUT_TRANSFORMS.get(value_type, frozenset())


def _request_explicitly_omits_authorization(request: TestDefinitionRequest) -> bool:
    return any(
        item.operation == "omit"
        and item.location in {"authorization", "header"}
        and (item.target or "").lower() == "authorization"
        for item in request.modifications
    )


def _request_template_diagnostics(
    request: TestDefinitionRequest,
    *,
    base: str,
) -> tuple[ConfigurationDiagnostic, ...]:
    """Reject placeholders that have no catalogue-owned declaration."""
    diagnostics: list[ConfigurationDiagnostic] = []
    runtime_refs = set(request.runtime_input_refs)
    generated_refs = set(request.generated_values)
    body_binding_targets = {item.target for item in request.input_bindings if item.type == "json-body"}
    query_binding_targets = {item.target for item in request.input_bindings if item.type == "query-parameter"}

    def inspect(value: object, *, path: str, bound: bool = False) -> None:
        if isinstance(value, str):
            for kind, identifier in _TEMPLATE_PLACEHOLDER.findall(value):
                declared = identifier in runtime_refs if kind == "runtime" else identifier in generated_refs
                if not declared and not (kind == "runtime" and bound):
                    diagnostics.append(
                        _diagnostic(
                            DiagnosticCode.RULE_INCONSISTENT,
                            f"Template references undeclared {kind} value {identifier!r}",
                            path,
                        )
                    )
            return
        if isinstance(value, Mapping):
            for key, item in value.items():
                token = str(key).replace("~", "~0").replace("/", "~1")
                inspect(item, path=f"{path}/{token}", bound=bound)
            return
        if isinstance(value, tuple | list):
            for index, item in enumerate(value):
                inspect(item, path=f"{path}/{index}", bound=bound)

    def inspect_body(value: object, *, pointer: str, path: str) -> None:
        if isinstance(value, str):
            inspect(value, path=path, bound=pointer in body_binding_targets)
            return
        if isinstance(value, Mapping):
            for key, item in value.items():
                token = str(key).replace("~", "~0").replace("/", "~1")
                inspect_body(item, pointer=f"{pointer}/{token}", path=f"{path}/{token}")
            return
        if isinstance(value, tuple | list):
            for index, item in enumerate(value):
                inspect_body(item, pointer=f"{pointer}/{index}", path=f"{path}/{index}")

    inspect(request.path, path=f"{base}/request/path")
    inspect_body(
        request.json_body_template,
        pointer="",
        path=f"{base}/request/jsonBodyTemplate",
    )
    for name, value in request.query_templates.items():
        inspect(
            value,
            path=f"{base}/request/queryTemplates/{name}",
            bound=name in query_binding_targets,
        )
    for name, value in request.form_body_template.items() if request.form_body_template is not None else ():
        inspect(value, path=f"{base}/request/formBodyTemplate/{name}")
    for index, header in enumerate(request.header_templates):
        if header.literal_value is not None:
            inspect(
                header.literal_value,
                path=f"{base}/request/headerTemplates/{index}/literalValue",
            )
    return tuple(diagnostics)


def test_definition_catalogue_to_document(catalogue: TestDefinitionCatalogue) -> JsonObject:
    """Render one consolidated catalogue deterministically."""
    return {
        "allowedSecurityProfiles": list(catalogue.allowed_security_profiles),
        "capabilities": [
            {
                "description": item.description,
                "id": str(item.id),
                "name": item.name,
                **(
                    {"requiredCapabilityIds": [str(value) for value in item.required_capability_ids]}
                    if item.required_capability_ids
                    else {}
                ),
                "requiredEndpointIds": [str(value) for value in item.required_endpoint_ids],
                "selection": item.selection,
            }
            for item in catalogue.capabilities
        ],
        "documentType": catalogue.document_type,
        "endpoints": [
            _without_none_values(
                {
                    "id": str(item.id),
                    "method": item.method.value,
                    "operationId": item.operation_id,
                    "path": item.path,
                    "sourceId": str(item.source_id),
                    "sourcePointer": item.source_pointer,
                }
            )
            for item in catalogue.endpoints
        ],
        "id": str(catalogue.id),
        "predefinedInputs": [
            _without_none_values(
                {
                    "defaultValue": (
                        None if item.default_value is None else _input_value_to_document(item.default_value)
                    ),
                    "description": item.description,
                    "exampleValue": _input_value_to_document(item.example_value),
                    "id": str(item.id),
                    "label": item.label,
                    "requiredForCapabilityIds": [str(value) for value in item.required_for_capability_ids],
                    "sensitivity": item.sensitivity,
                    "valueType": str(item.value_type),
                }
            )
            for item in catalogue.predefined_inputs
        ],
        "schemaVersion": catalogue.schema_version,
        "scheme": str(catalogue.scheme),
        "specification": {
            "id": str(catalogue.specification.id),
            "testScope": str(catalogue.specification.test_scope),
            "version": catalogue.specification.version,
        },
        "technicalSources": [
            {"digest": str(item.digest), "id": str(item.id), "title": item.title, "uri": item.uri}
            for item in catalogue.technical_sources
        ],
        "testDefinitions": [_test_definition_to_document(item) for item in catalogue.test_definitions],
    }


def participant_plan_to_document(plan: ParticipantPlan) -> JsonObject:
    """Render corrected public participant intent."""
    document: JsonObject = {
        "documentType": plan.document_type,
        "id": str(plan.id),
        "predefinedInputs": [
            {"inputId": str(item.input_id), "value": _input_value_to_document(item.value)}
            for item in plan.predefined_inputs
        ],
        "schemaVersion": plan.schema_version,
        "scheme": str(plan.scheme),
        "securityProfile": plan.security_profile,
        "selectedCapabilityIds": [str(value) for value in plan.selected_capability_ids],
        "specification": {
            "id": str(plan.specification.id),
            "testScope": str(plan.specification.test_scope),
            "version": plan.specification.version,
        },
        "suiteReleaseId": str(plan.suite_release_id),
    }
    if plan.execution_configuration is not None:
        config = plan.execution_configuration
        document["executionConfiguration"] = {
            "compatibilityRuntimeInputs": deepcopy(dict(config.compatibility_runtime_inputs)),
            "dynamicClientRegistration": deepcopy(dict(config.dynamic_client_registration)),
            "metadata": deepcopy(dict(config.metadata)),
            "securityEnvironment": deepcopy(dict(config.security_environment)),
        }
    return document


def resolved_plan_to_document(plan: ResolvedPlan) -> JsonObject:
    """Render resolved work without requirement or normative blocks."""
    return {
        "capabilities": [
            {
                "id": str(item.id),
                "origin": item.origin.value,
                "reasons": [_reason_to_document(reason) for reason in item.reasons],
            }
            for item in plan.capabilities
        ],
        "documentType": plan.document_type,
        "endpoints": [
            {
                "id": str(item.id),
                "origin": item.origin.value,
                "reasons": [_reason_to_document(reason) for reason in item.reasons],
            }
            for item in plan.endpoints
        ],
        "findings": [_finding_to_document(item) for item in plan.findings],
        "id": str(plan.id),
        "predefinedInputs": [
            {
                "id": str(item.id),
                "reasons": [_reason_to_document(reason) for reason in item.reasons],
                "redacted": item.redacted,
                "source": item.source.value,
                "value": None if item.value is None else _input_value_to_document(item.value),
            }
            for item in plan.predefined_inputs
        ],
        "provenance": {
            "artifacts": [_artifact_to_document(item) for item in plan.provenance.artifacts],
            "participantPlanId": str(plan.provenance.participant_plan_id),
            "suitePublishedAt": plan.provenance.suite_published_at,
            "suiteReleaseId": str(plan.provenance.suite_release_id),
            "suiteReleaseVersion": plan.provenance.suite_release_version,
            "testDefinitionCatalogueId": str(plan.provenance.test_definition_catalogue_id),
            "toolReleases": [{"id": str(item.id), "version": item.version} for item in plan.provenance.tool_releases],
        },
        "schemaVersion": plan.schema_version,
        "scheme": str(plan.scheme),
        "securityProfile": plan.security_profile,
        "selectionValid": plan.selection_valid,
        "specification": {
            "id": str(plan.specification.id),
            "testScope": str(plan.specification.test_scope),
            "version": plan.specification.version,
        },
        "testInstances": [
            {
                "dependencyIds": [str(value) for value in item.dependency_ids],
                "id": str(item.id),
                "reasons": [_reason_to_document(reason) for reason in item.reasons],
                "testDefinitionId": str(item.test_definition_id),
            }
            for item in plan.test_instances
        ],
    }


def execution_manifest_to_document(manifest: ExecutionManifest) -> JsonObject:
    """Render manifest-authoritative instructions without requirement coverage."""
    return {
        "documentType": manifest.document_type,
        "id": str(manifest.id),
        "inputs": [
            {
                "id": str(item.id),
                "redacted": item.redacted,
                "source": item.source.value,
                **({"value": _input_value_to_document(item.value)} if item.value is not None else {}),
            }
            for item in manifest.inputs
        ],
        "provenance": {
            "artifacts": [_artifact_to_document(item) for item in manifest.provenance.artifacts],
            "participantPlanId": str(manifest.provenance.participant_plan_id),
            "resolvedPlanId": str(manifest.provenance.resolved_plan_id),
            "suitePublishedAt": manifest.provenance.suite_published_at,
            "suiteReleaseId": str(manifest.provenance.suite_release_id),
            "suiteReleaseVersion": manifest.provenance.suite_release_version,
            "testDefinitionCatalogueId": str(manifest.provenance.test_definition_catalogue_id),
            "toolReleases": [
                {"id": str(item.id), "version": item.version} for item in manifest.provenance.tool_releases
            ],
        },
        "schemaVersion": manifest.schema_version,
        "securityProfile": manifest.security_profile,
        "steps": [
            {
                "assertions": [_assertion_to_document(item) for item in step.assertions],
                "dependencyIds": [str(value) for value in step.dependency_ids],
                "evidence": {
                    "request": step.evidence.request.value,
                    "response": step.evidence.response.value,
                },
                "id": str(step.id),
                "name": step.name,
                **({"outputs": [_test_output_to_document(item) for item in step.outputs]} if step.outputs else {}),
                "request": _execution_request_to_document(step.request),
                "testDefinitionId": str(step.test_definition_id),
                "testInstanceId": str(step.test_instance_id),
            }
            for step in manifest.steps
        ],
    }


def suite_policy_to_document(policy: SuitePolicy) -> JsonObject:
    """Render released automated-assessment policy deterministically."""
    return {
        "blockingFindingSeverities": list(policy.blocking_finding_severities),
        "documentType": policy.document_type,
        "id": str(policy.id),
        "resultClaim": policy.result_claim,
        "schemaVersion": policy.schema_version,
    }


def execution_manifest_id(manifest: ExecutionManifest) -> StableId:
    """Return the manifest's content address with its ID slot normalised."""
    document = execution_manifest_to_document(manifest)
    document["id"] = "execution-manifest:" + ("0" * 64)
    content = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return StableId(f"execution-manifest:{hashlib.sha256(content.encode()).hexdigest()}")


def dump_test_definition_catalogue(catalogue: TestDefinitionCatalogue) -> str:
    """Serialize a consolidated catalogue deterministically."""
    return json.dumps(test_definition_catalogue_to_document(catalogue), indent=2, sort_keys=True) + "\n"


def dump_participant_plan(plan: ParticipantPlan) -> str:
    """Serialize participant intent deterministically."""
    return json.dumps(participant_plan_to_document(plan), indent=2, sort_keys=True) + "\n"


def dump_resolved_plan(plan: ResolvedPlan) -> str:
    """Serialize resolved work deterministically."""
    return json.dumps(resolved_plan_to_document(plan), indent=2, sort_keys=True) + "\n"


def dump_execution_manifest(manifest: ExecutionManifest) -> str:
    """Serialize an execution manifest deterministically."""
    return json.dumps(execution_manifest_to_document(manifest), indent=2, sort_keys=True) + "\n"


def dump_suite_policy(policy: SuitePolicy) -> str:
    """Serialize released automated-assessment policy deterministically."""
    return json.dumps(suite_policy_to_document(policy), indent=2, sort_keys=True) + "\n"


def validate_bundled_schemas() -> tuple[ConfigurationDiagnostic, ...]:
    """Validate every bundled 2.0 schema and all local references."""
    try:
        registry = _registry()
        for name in _SCHEMA_NAMES:
            schema = _schema(name)
            Draft202012Validator.check_schema(schema)
            registry.get_or_retrieve(_SCHEMA_IDS[name])
    except Exception as error:  # schema bootstrap must become a structured diagnostic
        return (_diagnostic(DiagnosticCode.SCHEMA_DEFINITION_INVALID, str(error), ""),)
    return ()


def _test_definition(document: dict[str, object]) -> TestDefinition:
    applicability = cast(dict[str, object], document["applicability"])
    evidence = cast(dict[str, object], document["evidencePolicy"])
    request_document = cast(dict[str, object], document["request"])
    request = _execution_request(request_document)
    return TestDefinition(
        id=StableId(cast(str, document["id"])),
        name=cast(str, document["name"]),
        description=cast(str, document["description"]),
        purpose=cast(str, document["purpose"]),
        applicability=TestApplicability(
            tuple(StableId(value) for value in cast(list[str], applicability["capabilityIds"]))
        ),
        dependencies=tuple(StableId(value) for value in cast(list[str], document["dependencies"])),
        request=_test_definition_request(
            request,
            endpoint_id=StableId(cast(str, request_document["endpointId"])),
        ),
        assertions=tuple(
            _test_assertion_from_document(item) for item in cast(list[dict[str, object]], document["assertions"])
        ),
        evidence_policy=ExecutionEvidencePolicy(
            request=EvidenceMode(cast(str, evidence["request"])),
            response=EvidenceMode(cast(str, evidence["response"])),
        ),
        outputs=tuple(
            _test_output_from_document(item) for item in cast(list[dict[str, object]], document.get("outputs", []))
        ),
    )


def _test_definition_to_document(item: TestDefinition) -> JsonObject:
    request = _execution_request_to_document(item.request)
    request["endpointId"] = str(item.request.endpoint_id)
    return {
        "applicability": {"capabilityIds": [str(value) for value in item.applicability.capability_ids]},
        "assertions": [_assertion_to_document(value) for value in item.assertions],
        "dependencies": [str(value) for value in item.dependencies],
        "description": item.description,
        "evidencePolicy": {
            "request": item.evidence_policy.request.value,
            "response": item.evidence_policy.response.value,
        },
        "id": str(item.id),
        "name": item.name,
        **({"outputs": [_test_output_to_document(value) for value in item.outputs]} if item.outputs else {}),
        "purpose": item.purpose,
        "request": request,
    }


def _execution_manifest_step(document: dict[str, object]) -> ExecutionManifestStep:
    evidence = cast(dict[str, object], document["evidence"])
    return ExecutionManifestStep(
        id=StableId(cast(str, document["id"])),
        test_instance_id=StableId(cast(str, document["testInstanceId"])),
        test_definition_id=StableId(cast(str, document["testDefinitionId"])),
        name=cast(str, document["name"]),
        dependency_ids=tuple(StableId(value) for value in cast(list[str], document["dependencyIds"])),
        request=_execution_request(cast(dict[str, object], document["request"])),
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
            for item in (
                _test_assertion_from_document(value) for value in cast(list[dict[str, object]], document["assertions"])
            )
        ),
        outputs=tuple(
            _test_output_from_document(item) for item in cast(list[dict[str, object]], document.get("outputs", []))
        ),
        evidence=ExecutionEvidencePolicy(
            request=EvidenceMode(cast(str, evidence["request"])),
            response=EvidenceMode(cast(str, evidence["response"])),
        ),
    )


def _execution_request(document: dict[str, object]) -> ExecutionManifestRequest:
    bindings = cast(list[dict[str, object]], document["inputBindings"])
    return ExecutionManifestRequest(
        method=HttpMethod(cast(str, document["method"])),
        path=cast(str, document["path"]),
        input_bindings=tuple(
            RequestInputBinding(
                input_id=StableId(cast(str, item["inputId"])),
                type=cast(str, item["type"]),
                target=cast(str, item["target"]),
                transform=StableId(cast(str, item["transform"])),
            )
            for item in bindings
        ),
        modifications=tuple(
            _request_modification_from_document(item)
            for item in cast(list[dict[str, object]], document["modifications"])
        ),
        state_bindings=tuple(
            _state_binding_from_document(item)
            for item in cast(list[dict[str, object]], document.get("stateBindings", []))
        ),
        content_type=cast(str | None, document.get("contentType")),
        transport_profile=_optional_stable_id(document.get("transportProfile")),
        authorization_profile=_optional_stable_id(document.get("authorizationProfile")),
        base_url_source=RequestBaseUrlSource(cast(str, document["baseUrlSource"])),
        query_templates=_immutable_string_mapping(document.get("queryTemplates", {})),
        header_templates=tuple(
            _execution_header_from_document(item)
            for item in cast(list[dict[str, object]], document.get("headerTemplates", []))
        ),
        json_body_template=cast(JsonValue | None, deepcopy(document.get("jsonBodyTemplate"))),
        form_body_template=(
            _immutable_string_mapping(document["formBodyTemplate"]) if "formBodyTemplate" in document else None
        ),
        runtime_input_refs=tuple(cast(list[str], document.get("runtimeInputRefs", []))),
        generated_values=MappingProxyType(
            {
                name: GeneratedValueStrategy(value)
                for name, value in cast(dict[str, str], document.get("generatedValues", {})).items()
            }
        ),
        required_token_id=_optional_stable_id(document.get("requiredTokenId")),
        required_token_scope=cast(str | None, document.get("requiredTokenScope")),
        produced_token_id=_optional_stable_id(document.get("producedTokenId")),
        invalidate_produced_authorization_token=cast(bool, document.get("invalidateProducedAuthorizationToken", False)),
        detached_jws=(
            _execution_detached_jws_from_document(cast(dict[str, object], document["detachedJws"]))
            if "detachedJws" in document
            else None
        ),
        token_endpoint_auth=(
            ExecutionTokenEndpointAuth(
                source=TokenEndpointAuthSource(
                    cast(str, cast(dict[str, object], document["tokenEndpointAuth"])["source"])
                )
            )
            if "tokenEndpointAuth" in document
            else None
        ),
        response_signature=(
            ExecutionResponseSignature(
                source=ResponseSignatureSource(
                    cast(str, cast(dict[str, object], document["responseSignature"])["source"])
                )
            )
            if "responseSignature" in document
            else None
        ),
        psu_authorization=(
            _execution_psu_authorization_from_document(cast(dict[str, object], document["psuAuthorization"]))
            if "psuAuthorization" in document
            else None
        ),
        required_psu_authorization_step_id=_optional_stable_id(document.get("requiredPsuAuthorizationStepId")),
    )


def _test_definition_request(
    request: ExecutionManifestRequest,
    *,
    endpoint_id: StableId,
) -> TestDefinitionRequest:
    return TestDefinitionRequest(
        endpoint_id=endpoint_id,
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


def _optional_stable_id(value: object) -> StableId | None:
    return None if value is None else StableId(cast(str, value))


def _optional_input_value(document: dict[str, object]) -> PredefinedInputValue | None:
    value = document.get("value")
    return None if value is None else _input_value_from_document(cast(str | dict[str, object], value))


def _request_path_matches_endpoint(request_path: str, endpoint_path: str) -> bool:
    def normalized(value: str) -> str:
        return re.sub(r"\$\{[^}]+\}|\{[^}]+\}", "{}", value)

    return normalized(request_path).endswith(normalized(endpoint_path))


def _immutable_string_mapping(value: object) -> Mapping[str, str]:
    from types import MappingProxyType

    return MappingProxyType(dict(cast(dict[str, str], value)))


def _tool_releases(provenance: dict[str, object]) -> tuple[ToolRelease, ...]:
    return tuple(
        ToolRelease(id=StableId(cast(str, item["id"])), version=cast(str, item["version"]))
        for item in cast(list[dict[str, object]], provenance["toolReleases"])
    )


def _artifacts(provenance: dict[str, object]) -> tuple[ArtifactReference, ...]:
    return tuple(_artifact_from_document(item) for item in cast(list[dict[str, object]], provenance["artifacts"]))


def _validate(raw: object, name: str) -> None:
    if isinstance(raw, dict) and raw.get("schemaVersion") != SCHEMA_VERSION:
        raise ConfigurationContractError(
            (
                _diagnostic(
                    DiagnosticCode.SCHEMA_VERSION_UNSUPPORTED,
                    f"Unsupported {name} schema version {raw.get('schemaVersion')!r}",
                    "/schemaVersion",
                ),
            )
        )
    try:
        validator = Draft202012Validator(
            _schema(name),
            registry=_registry(),
            format_checker=FormatChecker(),
        )
        errors = sorted(validator.iter_errors(raw), key=lambda error: tuple(str(value) for value in error.path))
    except (SchemaError, ValidationError) as schema_error:
        raise ConfigurationContractError(
            (_diagnostic(DiagnosticCode.SCHEMA_DEFINITION_INVALID, str(schema_error), ""),)
        ) from schema_error
    if errors:
        validation_error = errors[0]
        path = "/" + "/".join(_escape_pointer(str(value)) for value in validation_error.absolute_path)
        raise ConfigurationContractError(
            (
                ConfigurationDiagnostic(
                    code=DiagnosticCode.SCHEMA_VALIDATION_FAILED,
                    severity=DiagnosticSeverity.ERROR,
                    message=validation_error.message,
                    instance_path="" if path == "/" else path,
                    schema_path="/"
                    + "/".join(_escape_pointer(str(value)) for value in validation_error.absolute_schema_path),
                ),
            )
        )


@cache
def _registry() -> Registry[dict[str, object]]:
    registry: Registry[dict[str, object]] = Registry()
    for name in _SCHEMA_NAMES:
        schema = _schema(name)
        registry = registry.with_resource(_SCHEMA_IDS[name], Resource.from_contents(schema))
    return registry


@cache
def _schema(name: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads((_SCHEMA_ROOT / f"{name}.schema.json").read_text()))


def _load(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ConfigurationContractError((_diagnostic(DiagnosticCode.JSON_INVALID, error.msg, ""),)) from error
    except OSError as error:
        raise ConfigurationContractError((_diagnostic(DiagnosticCode.IO_READ_FAILED, str(error), ""),)) from error


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
        artifacts=tuple(_artifact_from_document(artifact) for artifact in artifacts),
    )


def _predefined_input_from_document(document: dict[str, object]) -> PredefinedInput:
    example = cast(str | dict[str, object], document["exampleValue"])
    default = cast(str | dict[str, object] | None, document.get("defaultValue"))
    return PredefinedInput(
        id=StableId(cast(str, document["id"])),
        label=cast(str, document["label"]),
        description=cast(str, document["description"]),
        value_type=StableId(cast(str, document["valueType"])),
        required_for_capability_ids=tuple(
            StableId(capability_id) for capability_id in cast(list[str], document["requiredForCapabilityIds"])
        ),
        sensitivity=cast(str, document["sensitivity"]),
        example_value=_input_value_from_document(example),
        default_value=None if default is None else _input_value_from_document(default),
    )


def _execution_request_to_document(request: ExecutionManifestRequest) -> JsonObject:
    return {
        **(
            {"authorizationProfile": str(request.authorization_profile)}
            if request.authorization_profile is not None
            else {}
        ),
        **({"baseUrlSource": request.base_url_source.value} if request.base_url_source is not None else {}),
        **({"contentType": request.content_type} if request.content_type is not None else {}),
        **(
            {
                "detachedJws": {
                    "omittedClaims": [claim.value for claim in request.detached_jws.omitted_claims],
                    "profile": request.detached_jws.profile.value,
                    "source": request.detached_jws.source.value,
                }
            }
            if request.detached_jws is not None
            else {}
        ),
        **({"formBodyTemplate": dict(request.form_body_template)} if request.form_body_template is not None else {}),
        **(
            {"generatedValues": {name: strategy.value for name, strategy in request.generated_values.items()}}
            if request.generated_values
            else {}
        ),
        **(
            {"headerTemplates": [_execution_header_to_document(header) for header in request.header_templates]}
            if request.header_templates
            else {}
        ),
        "inputBindings": [
            {
                "inputId": str(binding.input_id),
                "target": binding.target,
                "transform": str(binding.transform),
                "type": binding.type,
            }
            for binding in request.input_bindings
        ],
        **(
            {"jsonBodyTemplate": _mutable_json_value(request.json_body_template)}
            if request.json_body_template is not None
            else {}
        ),
        "method": request.method.value,
        "modifications": [_request_modification_to_document(modification) for modification in request.modifications],
        "path": request.path,
        **({"producedTokenId": str(request.produced_token_id)} if request.produced_token_id is not None else {}),
        **({"invalidateProducedAuthorizationToken": True} if request.invalidate_produced_authorization_token else {}),
        **(
            {
                "psuAuthorization": {
                    "authorizationStepId": str(request.psu_authorization.authorization_step_id),
                    "authorizationStepName": request.psu_authorization.authorization_step_name,
                    "flowLabel": request.psu_authorization.flow_label,
                    "tokenId": str(request.psu_authorization.token_id),
                    "tokenStepId": str(request.psu_authorization.token_step_id),
                }
            }
            if request.psu_authorization is not None
            else {}
        ),
        **({"queryTemplates": dict(request.query_templates)} if request.query_templates else {}),
        **({"requiredTokenId": str(request.required_token_id)} if request.required_token_id is not None else {}),
        **({"requiredTokenScope": request.required_token_scope} if request.required_token_scope is not None else {}),
        **(
            {"requiredPsuAuthorizationStepId": str(request.required_psu_authorization_step_id)}
            if request.required_psu_authorization_step_id is not None
            else {}
        ),
        **(
            {"responseSignature": {"source": request.response_signature.source.value}}
            if request.response_signature is not None
            else {}
        ),
        **({"runtimeInputRefs": list(request.runtime_input_refs)} if request.runtime_input_refs else {}),
        **(
            {"stateBindings": [_state_binding_to_document(binding) for binding in request.state_bindings]}
            if request.state_bindings
            else {}
        ),
        **(
            {"tokenEndpointAuth": {"source": request.token_endpoint_auth.source.value}}
            if request.token_endpoint_auth is not None
            else {}
        ),
        **({"transportProfile": str(request.transport_profile)} if request.transport_profile is not None else {}),
    }


def _execution_header_to_document(header: ExecutionManifestHeader) -> JsonObject:
    return {
        "name": header.name,
        **({"literalValue": header.literal_value} if header.literal_value is not None else {}),
        **({"runtimeInputRef": header.runtime_input_ref} if header.runtime_input_ref is not None else {}),
        **({"generatedValue": header.generated_value.value} if header.generated_value is not None else {}),
    }


def _mutable_json_value(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {str(key): _mutable_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_mutable_json_value(item) for item in value]
    return value


def _execution_header_from_document(document: dict[str, object]) -> ExecutionManifestHeader:
    generated_value = cast(str | None, document.get("generatedValue"))
    return ExecutionManifestHeader(
        name=cast(str, document["name"]),
        literal_value=cast(str | None, document.get("literalValue")),
        runtime_input_ref=cast(str | None, document.get("runtimeInputRef")),
        generated_value=(GeneratedHeaderValue(generated_value) if generated_value is not None else None),
    )


def _execution_detached_jws_from_document(document: dict[str, object]) -> ExecutionDetachedJws:
    return ExecutionDetachedJws(
        profile=DetachedJwsProfile(cast(str, document["profile"])),
        omitted_claims=tuple(
            DetachedJwsOmittedClaim(claim) for claim in cast(list[str], document.get("omittedClaims", []))
        ),
        source=DetachedJwsSource(cast(str, document["source"])),
    )


def _execution_psu_authorization_from_document(document: dict[str, object]) -> ExecutionPsuAuthorization:
    return ExecutionPsuAuthorization(
        authorization_step_id=StableId(cast(str, document["authorizationStepId"])),
        authorization_step_name=cast(str, document["authorizationStepName"]),
        token_step_id=StableId(cast(str, document["tokenStepId"])),
        token_id=StableId(cast(str, document["tokenId"])),
        flow_label=cast(str, document["flowLabel"]),
    )


def _resolution_reasons_from_document(document: dict[str, object]) -> tuple[ResolutionReason, ...]:
    reasons = cast(list[dict[str, object]], document["reasons"])
    return tuple(
        ResolutionReason(
            code=StableId(cast(str, reason["code"])),
            source_ids=tuple(StableId(source_id) for source_id in cast(list[str], reason["sourceIds"])),
        )
        for reason in reasons
    )


def _artifact_from_document(document: dict[str, object]) -> ArtifactReference:
    return ArtifactReference(
        id=StableId(cast(str, document["id"])),
        kind=StableId(cast(str, document["kind"])),
        media_type=cast(str, document["mediaType"]),
        schema_version=cast(str, document["schemaVersion"]),
        uri=cast(str, document["uri"]),
        digest=Sha256Digest(cast(str, document["digest"])),
    )


def _request_modification_to_document(modification: RequestModification) -> JsonObject:
    return _without_none_values(
        {
            "generator": None if modification.generator is None else str(modification.generator),
            "id": str(modification.id),
            "location": modification.location,
            "operation": modification.operation,
            "target": modification.target,
            "value": modification.value,
        }
    )


def _request_modification_from_document(document: dict[str, object]) -> RequestModification:
    generator = cast(str | None, document.get("generator"))
    return RequestModification(
        id=StableId(cast(str, document["id"])),
        operation=cast(str, document["operation"]),
        location=cast(str, document["location"]),
        target=cast(str | None, document.get("target")),
        value=cast(str | None, document.get("value")),
        generator=None if generator is None else StableId(generator),
    )


def _state_binding_to_document(binding: RequestStateBinding) -> JsonObject:
    return {
        "outputId": str(binding.output_id),
        "target": binding.target,
        "type": binding.type,
    }


def _state_binding_from_document(document: dict[str, object]) -> RequestStateBinding:
    return RequestStateBinding(
        output_id=StableId(cast(str, document["outputId"])),
        type=cast(str, document["type"]),
        target=cast(str, document["target"]),
    )


def _test_output_to_document(output: TestOutput) -> JsonObject:
    return _without_none_values(
        {
            "id": str(output.id),
            "jsonPointer": output.json_pointer,
            "sensitive": output.sensitive,
            "source": output.source,
        }
    )


def _test_output_from_document(document: dict[str, object]) -> TestOutput:
    return TestOutput(
        id=StableId(cast(str, document["id"])),
        source=cast(str, document["source"]),
        json_pointer=cast(str | None, document.get("jsonPointer")),
        sensitive=cast(bool, document["sensitive"]),
    )


def _assertion_to_document(assertion: TestAssertion | ExecutionManifestAssertion) -> JsonObject:
    return _without_none_values(
        {
            "expectedStatus": assertion.expected_status,
            "expectedStatuses": (None if assertion.expected_statuses is None else list(assertion.expected_statuses)),
            "expectedValue": assertion.expected_value,
            "headerName": assertion.header_name,
            "id": str(assertion.id),
            "jsonPointer": assertion.json_pointer,
            "schemaRef": assertion.schema_ref,
            "schemaSourceId": (None if assertion.schema_source_id is None else str(assertion.schema_source_id)),
            "type": assertion.type,
        }
    )


def _test_assertion_from_document(document: dict[str, object]) -> TestAssertion:
    return TestAssertion(
        id=StableId(cast(str, document["id"])),
        type=cast(str, document["type"]),
        expected_status=cast(int | None, document.get("expectedStatus")),
        expected_statuses=(
            None if "expectedStatuses" not in document else tuple(cast(list[int], document["expectedStatuses"]))
        ),
        schema_ref=cast(str | None, document.get("schemaRef")),
        schema_source_id=(
            None
            if (schema_source_id := cast(str | None, document.get("schemaSourceId"))) is None
            else StableId(schema_source_id)
        ),
        header_name=cast(str | None, document.get("headerName")),
        json_pointer=cast(str | None, document.get("jsonPointer")),
        expected_value=cast(str | None, document.get("expectedValue")),
    )


def _frequency_to_document(frequency: StandingOrderFrequency) -> JsonObject:
    document: JsonObject = {"frequencyType": frequency.frequency_type}
    if frequency.count_per_period is not None:
        document["countPerPeriod"] = frequency.count_per_period
    if frequency.point_in_time is not None:
        document["pointInTime"] = frequency.point_in_time
    return document


def _input_value_to_document(value: PredefinedInputValue) -> JsonValue:
    return value if isinstance(value, str) else _frequency_to_document(value)


def _input_value_from_document(value: str | dict[str, object]) -> PredefinedInputValue:
    return value if isinstance(value, str) else _frequency_from_document(value)


def _frequency_from_document(document: dict[str, object]) -> StandingOrderFrequency:
    return StandingOrderFrequency(
        frequency_type=cast(str, document["frequencyType"]),
        count_per_period=cast(int | None, document.get("countPerPeriod")),
        point_in_time=cast(str | None, document.get("pointInTime")),
    )


def _artifact_to_document(artifact: ArtifactReference) -> JsonObject:
    return {
        "digest": str(artifact.digest),
        "id": str(artifact.id),
        "kind": str(artifact.kind),
        "mediaType": artifact.media_type,
        "schemaVersion": artifact.schema_version,
        "uri": artifact.uri,
    }


def _without_none_values(document: dict[str, JsonValue | None]) -> JsonObject:
    return {key: value for key, value in document.items() if value is not None}


def _immutable_mapping(value: object) -> Mapping[str, JsonValue]:
    return MappingProxyType(deepcopy(cast(dict[str, JsonValue], value)))


def _duplicates(
    values: Iterable[tuple[str, str]],
    *,
    kind_by_id: Iterable[tuple[str, str]] | None = None,
) -> tuple[ConfigurationDiagnostic, ...]:
    seen: set[object] = set()
    diagnostics: list[ConfigurationDiagnostic] = []
    kind_values = iter(kind_by_id) if kind_by_id is not None else None
    for identifier, path in values:
        key: object = identifier
        if kind_values is not None:
            key = next(kind_values)
        if key in seen:
            diagnostics.append(_duplicate(identifier, path))
        seen.add(key)
    return tuple(diagnostics)


def _duplicate(identifier: str, path: str) -> ConfigurationDiagnostic:
    return _diagnostic(DiagnosticCode.DUPLICATE_ID, f"Duplicate stable ID {identifier!r}", path)


def _unresolved(identifier: StableId, path: str, kind: str) -> ConfigurationDiagnostic:
    return _diagnostic(
        DiagnosticCode.REFERENCE_UNRESOLVED,
        f"Referenced {kind} {identifier!s} does not exist",
        path,
    )


def _cycle_diagnostics(
    values: tuple[Capability | TestDefinition, ...], collection: str, attribute: str
) -> tuple[ConfigurationDiagnostic, ...]:
    by_id = {item.id: item for item in values}
    indexes = {item.id: index for index, item in enumerate(values)}
    state: dict[StableId, int] = {}
    diagnostics: list[ConfigurationDiagnostic] = []

    def visit(identifier: StableId) -> None:
        state[identifier] = 1
        for ref_index, dependency_id in enumerate(cast(tuple[StableId, ...], getattr(by_id[identifier], attribute))):
            if dependency_id not in by_id:
                continue
            if state.get(dependency_id) == 1:
                field = "requiredCapabilityIds" if attribute == "required_capability_ids" else "dependencies"
                diagnostics.append(
                    _diagnostic(
                        DiagnosticCode.DEPENDENCY_CYCLE,
                        f"Dependency {dependency_id!s} creates a cycle",
                        f"/{collection}/{indexes[identifier]}/{field}/{ref_index}",
                    )
                )
            elif state.get(dependency_id, 0) == 0:
                visit(dependency_id)
        state[identifier] = 2

    for identifier in by_id:
        if state.get(identifier, 0) == 0:
            visit(identifier)
    return tuple(diagnostics)


def _dependency_closure(
    identifier: StableId,
    definitions: Mapping[StableId, TestDefinition],
) -> set[StableId]:
    closure: set[StableId] = set()
    pending = list(definitions[identifier].dependencies)
    while pending:
        dependency = pending.pop()
        if dependency in closure or dependency not in definitions:
            continue
        closure.add(dependency)
        pending.extend(definitions[dependency].dependencies)
    return closure


def _reason_to_document(reason: ResolutionReason) -> JsonObject:
    return {"code": str(reason.code), "sourceIds": [str(value) for value in reason.source_ids]}


def _finding_to_document(finding: CompilationFinding) -> JsonObject:
    return {
        "code": str(finding.code),
        "instancePath": finding.instance_path,
        "message": finding.message,
        "relatedIds": [str(value) for value in finding.related_ids],
        "severity": finding.severity.value,
        "sourceDocument": finding.source_document.value,
    }


def _diagnostic(code: DiagnosticCode, message: str, path: str) -> ConfigurationDiagnostic:
    return ConfigurationDiagnostic(
        code=code,
        severity=DiagnosticSeverity.ERROR,
        message=message,
        instance_path=path,
    )


def _escape_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")
