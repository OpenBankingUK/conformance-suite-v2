"""Executable Open Banking UK Dynamic Client Registration 3.4 catalogue."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import cast

from conformance.catalogue import (
    CatalogueAssertion,
    CatalogueConfigurationRequirement,
    CatalogueExecutionStep,
    CatalogueExecutionStepKind,
    CatalogueGateAbsenceBehavior,
    CatalogueKey,
    CatalogueProvenance,
    CatalogueRuntimeCapability,
    CatalogueTestCase,
    CatalogueTraceGroup,
    EndpointCapability,
    EndpointRef,
    SecurityProfileApplicability,
    TestCaseApplicability,
    TestCaseRole,
    TestCatalogue,
)
from conformance.json_types import JsonObject, JsonValue

DCR_CATALOGUE_KEY = CatalogueKey(standard="open-banking", version="v3.4", api="dcr")
"""Internal catalogue boundary for Open Banking UK DCR 3.4."""

DCR_CATALOGUE_VERSION = "2026.09.dcr-v3.4-parity.1"
"""Content version for the pinned DCR 3.4 parity catalogue."""

_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "standards" / "ob_dcr" / "v3_4" / "parity-contract.json"
"""Machine-readable DCR parity contract used to build the executable catalogue."""

_ENDPOINTS_BY_METHOD: Mapping[str, EndpointRef] = MappingProxyType(
    {
        "POST": EndpointRef(method="POST", path="/register"),
        "GET": EndpointRef(method="GET", path="/register/{ClientId}"),
        "PUT": EndpointRef(method="PUT", path="/register/{ClientId}"),
        "DELETE": EndpointRef(method="DELETE", path="/register/{ClientId}"),
    }
)
"""Participant-selectable DCR endpoint references keyed by method."""

_CAPABILITY_IDS_BY_METHOD: Mapping[str, str] = MappingProxyType(
    {
        "POST": "dcr.registration.post",
        "GET": "dcr.management.get",
        "PUT": "dcr.management.put",
        "DELETE": "dcr.management.delete",
    }
)
"""Required catalogue capability ids keyed by selected DCR endpoint method."""


def _load_contract() -> JsonObject:
    """Load the pinned DCR parity contract.

    Returns:
        Parsed top-level contract object.
    """
    return _object(cast("JsonValue", json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))), location="contract")


def _object(value: JsonValue, *, location: str) -> JsonObject:
    """Narrow a JSON value to an object.

    Args:
        value: JSON value expected to be an object.
        location: Contract location used in error messages.

    Returns:
        Narrowed JSON object.

    Raises:
        ValueError: If the value is not an object.
    """
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be a JSON object")
    return value


def _array(value: JsonValue, *, location: str) -> list[JsonValue]:
    """Narrow a JSON value to an array.

    Args:
        value: JSON value expected to be an array.
        location: Contract location used in error messages.

    Returns:
        Narrowed JSON array.

    Raises:
        ValueError: If the value is not an array.
    """
    if not isinstance(value, list):
        raise ValueError(f"{location} must be a JSON array")
    return value


def _string(value: JsonValue, *, location: str) -> str:
    """Narrow a JSON value to a non-empty string.

    Args:
        value: JSON value expected to be a string.
        location: Contract location used in error messages.

    Returns:
        Non-empty string value.

    Raises:
        ValueError: If the value is not a non-empty string.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"{location} must be a non-empty string")
    return value


def _strings(value: JsonValue, *, location: str) -> tuple[str, ...]:
    """Narrow a JSON array to strings.

    Args:
        value: JSON value expected to be an array of strings.
        location: Contract location used in error messages.

    Returns:
        Ordered string values.

    Raises:
        ValueError: If any value is not a non-empty string.
    """
    return tuple(
        _string(item, location=f"{location}[{index}]") for index, item in enumerate(_array(value, location=location))
    )


def _optional_string(raw: Mapping[str, JsonValue], key: str, *, location: str) -> str | None:
    """Read an optional string from a contract object.

    Args:
        raw: Contract object containing the optional field.
        key: Field name to read.
        location: Contract location used in error messages.

    Returns:
        String value, or ``None`` when absent.

    Raises:
        ValueError: If a present value is not a non-empty string.
    """
    value = raw.get(key)
    return None if value is None else _string(value, location=f"{location}.{key}")


def _optional_boolean(raw: Mapping[str, JsonValue], key: str, *, location: str) -> bool:
    """Read an optional boolean from a contract object.

    Args:
        raw: Contract object containing the optional field.
        key: Field name to read.
        location: Contract location used in error messages.

    Returns:
        Boolean value, defaulting to ``False``.

    Raises:
        ValueError: If a present value is not a boolean.
    """
    value = raw.get(key, False)
    if not isinstance(value, bool):
        raise ValueError(f"{location}.{key} must be a boolean")
    return value


def _optional_status(raw: Mapping[str, JsonValue], key: str, *, location: str) -> int | None:
    """Read an optional HTTP status from a contract object.

    Args:
        raw: Contract object containing the optional field.
        key: Field name to read.
        location: Contract location used in error messages.

    Returns:
        Integer HTTP status, or ``None`` when absent.

    Raises:
        ValueError: If a present value is not an integer HTTP status.
    """
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 599:
        raise ValueError(f"{location}.{key} must be an HTTP status")
    return value


def _statuses(value: JsonValue, *, location: str) -> tuple[int, ...]:
    """Read locked HTTP statuses from a contract array.

    Args:
        value: JSON value expected to contain HTTP statuses.
        location: Contract location used in error messages.

    Returns:
        Ordered HTTP status values.

    Raises:
        ValueError: If an entry is not an integer HTTP status.
    """
    statuses: list[int] = []
    for index, item in enumerate(_array(value, location=location)):
        if isinstance(item, bool) or not isinstance(item, int) or not 100 <= item <= 599:
            raise ValueError(f"{location}[{index}] must be an HTTP status")
        statuses.append(item)
    return tuple(statuses)


def _endpoint_refs(methods: Iterable[str]) -> tuple[EndpointRef, ...]:
    """Resolve DCR endpoint methods to participant-selectable references.

    Args:
        methods: Endpoint method names from the parity contract.

    Returns:
        De-duplicated endpoint references in contract order.

    Raises:
        ValueError: If the contract names an unsupported endpoint method.
    """
    refs: list[EndpointRef] = []
    for method in methods:
        endpoint = _ENDPOINTS_BY_METHOD.get(method)
        if endpoint is None:
            raise ValueError(f"Unsupported DCR endpoint method in parity contract: {method}")
        if endpoint not in refs:
            refs.append(endpoint)
    return tuple(refs)


def _reference_urls(contract: Mapping[str, JsonValue]) -> Mapping[str, str]:
    """Index pinned contract reference URLs.

    Args:
        contract: Top-level DCR parity contract.

    Returns:
        Immutable mapping of reference ids to URLs.
    """
    references: dict[str, str] = {}
    for index, item in enumerate(_array(contract["references"], location="contract.references")):
        reference = _object(item, location=f"contract.references[{index}]")
        reference_id = _string(reference["id"], location=f"contract.references[{index}].id")
        references[reference_id] = _string(reference["url"], location=f"contract.references[{index}].url")
    return MappingProxyType(references)


def _provenance(contract: Mapping[str, JsonValue], references: Mapping[str, str]) -> CatalogueProvenance:
    """Build pinned legacy provenance metadata.

    Args:
        contract: Top-level DCR parity contract.
        references: Pinned reference URLs indexed by id.

    Returns:
        Catalogue provenance for the released legacy DCR baseline.
    """
    baseline = _object(contract["baseline"], location="contract.baseline")
    return CatalogueProvenance(
        repository=_string(baseline["repository"], location="contract.baseline.repository"),
        release=_string(baseline["release"], location="contract.baseline.release"),
        commit=_string(baseline["commit"], location="contract.baseline.commit"),
        source_paths=(
            _string(baseline["scenarioSource"], location="contract.baseline.scenarioSource"),
            _string(baseline["versionAdapterSource"], location="contract.baseline.versionAdapterSource"),
            _string(baseline["responseValidatorSource"], location="contract.baseline.responseValidatorSource"),
        ),
        references=references,
    )


def _trace_group(scenario: Mapping[str, JsonValue]) -> CatalogueTraceGroup:
    """Build generic trace metadata for one legacy DCR scenario.

    Args:
        scenario: Scenario entry from the pinned parity contract.

    Returns:
        Generic catalogue parent-group trace metadata.
    """
    scenario_id = _string(scenario["id"], location="contract.scenarios[].id")
    required_methods = _strings(
        scenario["requiredSelectedMethods"],
        location=f"contract.scenarios[{scenario_id}].requiredSelectedMethods",
    )
    absence_behavior_value = scenario.get("absenceBehavior", "exclude")
    absence_behavior = _string(
        absence_behavior_value,
        location=f"contract.scenarios[{scenario_id}].absenceBehavior",
    )
    if absence_behavior not in {"exclude", "skip"}:
        raise ValueError(f"Unsupported absence behavior for {scenario_id}: {absence_behavior}")
    return CatalogueTraceGroup(
        group_id=scenario_id,
        name=_string(scenario["name"], location=f"contract.scenarios[{scenario_id}].name"),
        intent=_string(scenario["intent"], location=f"contract.scenarios[{scenario_id}].intent"),
        source_symbol=_string(
            scenario["sourceSymbol"],
            location=f"contract.scenarios[{scenario_id}].sourceSymbol",
        ),
        normative_reference_ids=_strings(
            scenario["normativeReferences"],
            location=f"contract.scenarios[{scenario_id}].normativeReferences",
        ),
        required_endpoint_refs=_endpoint_refs(required_methods),
        absence_behavior=cast("CatalogueGateAbsenceBehavior", absence_behavior),
    )


def _execution_step(
    raw_step: Mapping[str, JsonValue],
    *,
    definitions: Mapping[str, JsonValue],
    case_id: str,
    sensitive_state_ids: frozenset[str],
) -> CatalogueExecutionStep:
    """Build one ordered DCR catalogue execution step.

    Args:
        raw_step: Case step entry from the parity contract.
        definitions: Step definitions indexed by definition id.
        case_id: Parent case identifier used in validation messages.
        sensitive_state_ids: Scenario state values that must always be masked.

    Returns:
        Protocol-neutral execution step for the future DCR adapter.

    Raises:
        ValueError: If the referenced definition or step kind is invalid.
    """
    step_id = _string(raw_step["id"], location=f"contract.scenarios[].cases[{case_id}].steps[].id")
    definition_id = _string(
        raw_step["definition"],
        location=f"contract.scenarios[].cases[{case_id}].steps[{step_id}].definition",
    )
    raw_definition = definitions.get(definition_id)
    if raw_definition is None:
        raise ValueError(f"Unknown DCR step definition: {definition_id}")
    definition = _object(raw_definition, location=f"contract.stepDefinitions.{definition_id}")
    kind = _string(definition["kind"], location=f"contract.stepDefinitions.{definition_id}.kind")
    if kind not in {"assertion", "http", "state"}:
        raise ValueError(f"Unsupported DCR execution step kind: {kind}")
    state_inputs = _strings(
        definition.get("consumes", []),
        location=f"contract.stepDefinitions.{definition_id}.consumes",
    )
    state_outputs = _strings(
        definition.get("produces", []),
        location=f"contract.stepDefinitions.{definition_id}.produces",
    )
    return CatalogueExecutionStep(
        step_id=step_id,
        definition_id=definition_id,
        name=definition_id.replace("-", " ").capitalize(),
        kind=cast("CatalogueExecutionStepKind", kind),
        behavior=_string(
            definition["behavior"],
            location=f"contract.stepDefinitions.{definition_id}.behavior",
        ),
        legacy_operation=_string(
            definition["legacyBuilderCall"],
            location=f"contract.stepDefinitions.{definition_id}.legacyBuilderCall",
        ),
        state_inputs=state_inputs,
        state_outputs=state_outputs,
        expected_status=_optional_status(
            definition,
            "expectedStatus",
            location=f"contract.stepDefinitions.{definition_id}",
        ),
        sensitive=(
            _optional_boolean(
                definition,
                "sensitive",
                location=f"contract.stepDefinitions.{definition_id}",
            )
            or not sensitive_state_ids.isdisjoint((*state_inputs, *state_outputs))
        ),
        variant=_optional_string(raw_step, "variant", location=f"contract.scenarios[].cases[{case_id}]"),
        deviation_id=_optional_string(
            raw_step,
            "deviation",
            location=f"contract.scenarios[].cases[{case_id}]",
        ),
    )


def _semantic_assertion(
    step: CatalogueExecutionStep,
    response_validation_requirements: tuple[str, ...],
) -> CatalogueAssertion | None:
    """Map a semantic DCR assertion step to the shared locked assertion model.

    Args:
        step: Protocol-neutral DCR execution step.
        response_validation_requirements: Pinned DCR 3.4 response requirements.

    Returns:
        Locked shared assertion, or ``None`` for non-semantic/status steps.
    """
    if step.definition_id == "validate-registration-endpoint":
        return CatalogueAssertion(
            assertion_id=step.step_id,
            kind="json_field",
            description=step.behavior,
            rule={"path": "discovery.registration_endpoint", "format": "absolute-https"},
        )
    if step.definition_id == "validate-registration-response-34":
        return CatalogueAssertion(
            assertion_id=step.step_id,
            kind="response_schema",
            description=step.behavior,
            rule={
                "schema": "open-banking-dcr-3.4",
                "requirements": list(response_validation_requirements),
                "hostIndependent": True,
            },
        )
    if step.definition_id == "parse-retrieved-client":
        return CatalogueAssertion(
            assertion_id=step.step_id,
            kind="response_schema",
            description=step.behavior,
            rule={"consistency": "registered-client-and-token-endpoint"},
        )
    if step.definition_id == "validate-registration-error":
        return CatalogueAssertion(
            assertion_id=step.step_id,
            kind="response_schema",
            description=step.behavior,
            rule={"schema": "rfc7591-registration-error", "mutatesRegistrationState": False},
        )
    return None


def _assertions(
    case_id: str,
    statuses: tuple[int, ...],
    execution_steps: tuple[CatalogueExecutionStep, ...],
    response_validation_requirements: tuple[str, ...],
) -> tuple[CatalogueAssertion, ...]:
    """Build locked case assertions from status and semantic contract rules.

    Args:
        case_id: Stable case traceability identifier.
        statuses: Locked expected HTTP statuses for the case.
        execution_steps: Ordered case execution steps.
        response_validation_requirements: Pinned DCR 3.4 response requirements.

    Returns:
        Locked assertions in deterministic status-then-step order.
    """
    assertions: list[CatalogueAssertion] = []
    if statuses:
        rule: dict[str, JsonValue] = (
            {"expected": statuses[0]} if len(statuses) == 1 else {"expectedOneOf": list(statuses)}
        )
        assertions.append(
            CatalogueAssertion(
                assertion_id=f"{case_id}-expected-status",
                kind="http_status",
                description=f"HTTP status is {' or '.join(map(str, statuses))}",
                rule=rule,
            )
        )
    assertions.extend(
        assertion
        for step in execution_steps
        if (assertion := _semantic_assertion(step, response_validation_requirements)) is not None
    )
    return tuple(assertions)


def _case_role(group_id: str, execution_steps: tuple[CatalogueExecutionStep, ...]) -> TestCaseRole:
    """Choose the shared execution role for a DCR case.

    Args:
        group_id: Parent DCR scenario identifier.
        execution_steps: Ordered execution steps in the case.

    Returns:
        Shared catalogue role suitable for scheduling and evidence.
    """
    if group_id == "DCR-001":
        return "setup"
    if any(step.definition_id == "request-client-credentials-token" for step in execution_steps):
        return "token"
    if any(
        step.definition_id in {"validate-registration-error", "set-empty-bearer-token"}
        or step.variant is not None
        and step.variant != "valid"
        for step in execution_steps
    ):
        return "security"
    return "resource"


def _build_case(
    raw_case: Mapping[str, JsonValue],
    *,
    trace_group: CatalogueTraceGroup,
    definitions: Mapping[str, JsonValue],
    response_validation_requirements: tuple[str, ...],
    sensitive_state_ids: frozenset[str],
) -> CatalogueTestCase:
    """Build one DCR catalogue case from the pinned contract.

    Args:
        raw_case: Case entry from the parity contract.
        trace_group: Generic parent-scenario trace metadata.
        definitions: Step definitions indexed by definition id.
        response_validation_requirements: Pinned DCR 3.4 response requirements.
        sensitive_state_ids: Scenario state values that must always be masked.

    Returns:
        Executable catalogue case with gates, dependencies, state, and assertions.
    """
    case_id = _string(raw_case["id"], location="contract.scenarios[].cases[].id")
    case_methods = _strings(
        raw_case.get("requiredSelectedMethods", []),
        location=f"contract.scenarios[].cases[{case_id}].requiredSelectedMethods",
    )
    endpoint_refs = _endpoint_refs(
        [endpoint.method for endpoint in trace_group.required_endpoint_refs] + list(case_methods)
    )
    execution_steps = tuple(
        _execution_step(
            _object(item, location=f"contract.scenarios[].cases[{case_id}].steps[{index}]"),
            definitions=definitions,
            case_id=case_id,
            sensitive_state_ids=sensitive_state_ids,
        )
        for index, item in enumerate(_array(raw_case["steps"], location=f"contract.scenarios[].cases[{case_id}].steps"))
    )
    statuses = _statuses(
        raw_case["expectedHttpStatuses"],
        location=f"contract.scenarios[].cases[{case_id}].expectedHttpStatuses",
    )
    absence_behavior = _string(
        raw_case.get("absenceBehavior", trace_group.absence_behavior),
        location=f"contract.scenarios[].cases[{case_id}].absenceBehavior",
    )
    if absence_behavior not in {"exclude", "skip"}:
        raise ValueError(f"Unsupported absence behavior for {case_id}: {absence_behavior}")
    return CatalogueTestCase(
        test_case_id=case_id,
        name=_string(raw_case["legacyName"], location=f"contract.scenarios[].cases[{case_id}].legacyName"),
        role=_case_role(trace_group.group_id, execution_steps),
        compliance_scope=(
            f"legacy-dcr-v1.4.0:{trace_group.source_symbol}#{case_id}",
            *trace_group.normative_reference_ids,
        ),
        applicability=TestCaseApplicability(
            security_profiles=SecurityProfileApplicability(profiles=("all",)),
            endpoint_refs=endpoint_refs,
            required_capability_ids=tuple(_CAPABILITY_IDS_BY_METHOD[endpoint.method] for endpoint in endpoint_refs),
            specification_versions=("3.4",),
        ),
        mandatory=True,
        dependencies=_strings(
            raw_case["prerequisiteCaseIds"],
            location=f"contract.scenarios[].cases[{case_id}].prerequisiteCaseIds",
        ),
        assertions=_assertions(
            case_id,
            statuses,
            execution_steps,
            response_validation_requirements,
        ),
        trace_group=trace_group,
        execution_steps=execution_steps,
        state_inputs=_strings(
            raw_case["stateConsumes"],
            location=f"contract.scenarios[].cases[{case_id}].stateConsumes",
        ),
        state_outputs=_strings(
            raw_case["stateProduces"],
            location=f"contract.scenarios[].cases[{case_id}].stateProduces",
        ),
        expected_http_statuses=statuses,
        gate_absence_behavior=cast("CatalogueGateAbsenceBehavior", absence_behavior),
    )


def _endpoint_capabilities() -> tuple[EndpointCapability, ...]:
    """Build required capabilities for each selected DCR endpoint.

    Returns:
        Endpoint capabilities in participant-facing POST/GET/PUT/DELETE order.
    """
    labels = {
        "POST": ("Client registration", "Register clients using raw PS256 application/jose over mTLS."),
        "GET": ("Client retrieval", "Retrieve dynamically registered clients over mTLS."),
        "PUT": ("Client update", "Update dynamically registered clients using signed JOSE over mTLS."),
        "DELETE": ("Client deletion", "Delete dynamically registered clients over mTLS."),
    }
    return tuple(
        EndpointCapability(
            capability_id=_CAPABILITY_IDS_BY_METHOD[method],
            label=labels[method][0],
            description=labels[method][1],
            required=True,
            endpoint_refs=(endpoint,),
        )
        for method, endpoint in _ENDPOINTS_BY_METHOD.items()
    )


def _configuration_requirements() -> tuple[CatalogueConfigurationRequirement, ...]:
    """Build canonical DCR runtime configuration requirements.

    Returns:
        Required and optional canonical configuration fields with sensitivity.
    """
    post = (_ENDPOINTS_BY_METHOD["POST"],)
    return (
        CatalogueConfigurationRequirement(
            "securityEnvironment.discoveryUrl",
            "url",
            "Authorization-server discovery URL",
            endpoint_refs=post,
        ),
        CatalogueConfigurationRequirement(
            "securityEnvironment.signingPrivateKeyPath",
            "file_reference",
            "Registration JOSE signing private key",
            sensitive=True,
            endpoint_refs=post,
        ),
        CatalogueConfigurationRequirement(
            "securityEnvironment.signingKeyId",
            "string",
            "Registration JOSE signing key identifier",
            endpoint_refs=post,
        ),
        CatalogueConfigurationRequirement(
            "securityEnvironment.mtls.certificatePath",
            "file_reference",
            "mTLS transport certificate",
            endpoint_refs=post,
        ),
        CatalogueConfigurationRequirement(
            "securityEnvironment.mtls.privateKeyPath",
            "file_reference",
            "mTLS transport private key",
            sensitive=True,
            endpoint_refs=post,
        ),
        CatalogueConfigurationRequirement(
            "securityEnvironment.mtls.caBundlePath",
            "file_reference",
            "Optional mTLS CA bundle",
            required=False,
            endpoint_refs=post,
        ),
        CatalogueConfigurationRequirement(
            "dynamicClientRegistration.softwareStatementAssertionPath",
            "file_reference",
            "Software Statement Assertion",
            sensitive=True,
            endpoint_refs=post,
        ),
        CatalogueConfigurationRequirement(
            "dynamicClientRegistration.signingCertificatePath",
            "file_reference",
            "Optional registration claim signing certificate",
            required=False,
            endpoint_refs=post,
        ),
        CatalogueConfigurationRequirement(
            "dynamicClientRegistration.registrationAudience",
            "string",
            "Base62 ASPSP registration audience",
            required=True,
            endpoint_refs=post,
        ),
        CatalogueConfigurationRequirement(
            "dynamicClientRegistration.registrationIssuerOverride",
            "string",
            "Optional registration issuer override",
            required=False,
            endpoint_refs=post,
        ),
        CatalogueConfigurationRequirement(
            "dynamicClientRegistration.redirectUrisOverride",
            "json",
            "Optional registration redirect URI overrides",
            required=False,
            endpoint_refs=post,
        ),
        CatalogueConfigurationRequirement(
            "dynamicClientRegistration.transportCertificateSubjectDnOverride",
            "string",
            "Optional transport certificate subject-DN override",
            required=False,
            endpoint_refs=post,
        ),
        CatalogueConfigurationRequirement(
            "dynamicClientRegistration.useNumericOidSubjectDn",
            "boolean",
            "Render certificate subject DNs with numeric OIDs",
            required=False,
            endpoint_refs=post,
        ),
        CatalogueConfigurationRequirement(
            "dynamicClientRegistration.disableKeepAlive",
            "boolean",
            "Disable HTTP keep-alive for transport compatibility",
            required=False,
            endpoint_refs=post,
        ),
    )


def _runtime_capabilities(contract: Mapping[str, JsonValue]) -> tuple[CatalogueRuntimeCapability, ...]:
    """Build generated DCR runtime capability policies.

    Args:
        contract: Top-level DCR parity contract.

    Returns:
        Runtime capabilities for JOSE, token setup, state, and scheduling.
    """
    runtime = _object(contract["runtimeContract"], location="contract.runtimeContract")
    registration = _object(runtime["registrationRequest"], location="contract.runtimeContract.registrationRequest")
    token = _object(runtime["tokenRequest"], location="contract.runtimeContract.tokenRequest")
    state = _object(runtime["statePolicy"], location="contract.runtimeContract.statePolicy")
    return (
        CatalogueRuntimeCapability(
            capability_id="dcr.registration-jose",
            label="Signed registration JOSE",
            description="Registration uses raw compact PS256 JOSE with application/jose over mTLS.",
            supported_values=(
                _string(
                    registration["signingAlgorithm"],
                    location="contract.runtimeContract.registrationRequest.signingAlgorithm",
                ),
                _string(
                    registration["contentType"],
                    location="contract.runtimeContract.registrationRequest.contentType",
                ),
                "mTLS",
            ),
        ),
        CatalogueRuntimeCapability(
            capability_id="dcr.client-credentials-token",
            label="Generated client-credentials token dependency",
            description="Token traffic is generated by compiled cases and is never participant-selectable scope.",
            supported_values=_strings(
                token["executableAuthMethods"],
                location="contract.runtimeContract.tokenRequest.executableAuthMethods",
            ),
            unsupported_values=_strings(
                token["unsupportedAuthMethods"],
                location="contract.runtimeContract.tokenRequest.unsupportedAuthMethods",
            ),
            generated_dependency=True,
        ),
        CatalogueRuntimeCapability(
            capability_id="dcr.scenario-state",
            label="Scenario-local dynamic client state",
            description=_string(state["isolation"], location="contract.runtimeContract.statePolicy.isolation"),
            supported_values=_strings(
                state["values"],
                location="contract.runtimeContract.statePolicy.values",
            ),
            generated_dependency=True,
        ),
        CatalogueRuntimeCapability(
            capability_id="dcr.sequential-scheduling",
            label="Sequential scenario scheduling",
            description="DCR scenarios execute sequentially with explicit prerequisite failure propagation.",
            supported_values=("sequential", "dependent-case-skip", "best-effort-cleanup"),
            generated_dependency=True,
        ),
    )


def _build_catalogue() -> TestCatalogue:
    """Build the executable DCR 3.4 catalogue from the pinned contract.

    Returns:
        Fully populated catalogue with all 34 cases and 79 execution steps.
    """
    contract = _load_contract()
    references = _reference_urls(contract)
    definitions = _object(contract["stepDefinitions"], location="contract.stepDefinitions")
    runtime = _object(contract["runtimeContract"], location="contract.runtimeContract")
    response_validation = _object(
        runtime["responseValidation34"],
        location="contract.runtimeContract.responseValidation34",
    )
    response_requirements = _strings(
        response_validation["requirements"],
        location="contract.runtimeContract.responseValidation34.requirements",
    )
    evidence = _object(runtime["evidencePolicy"], location="contract.runtimeContract.evidencePolicy")
    sensitive_state_ids = frozenset(
        _strings(
            evidence["alwaysMasked"],
            location="contract.runtimeContract.evidencePolicy.alwaysMasked",
        )
    )
    cases: list[CatalogueTestCase] = []
    for scenario_index, raw_scenario in enumerate(_array(contract["scenarios"], location="contract.scenarios")):
        scenario = _object(raw_scenario, location=f"contract.scenarios[{scenario_index}]")
        trace_group = _trace_group(scenario)
        cases.extend(
            _build_case(
                _object(raw_case, location=f"contract.scenarios[{scenario_index}].cases[{case_index}]"),
                trace_group=trace_group,
                definitions=definitions,
                response_validation_requirements=response_requirements,
                sensitive_state_ids=sensitive_state_ids,
            )
            for case_index, raw_case in enumerate(
                _array(scenario["cases"], location=f"contract.scenarios[{scenario_index}].cases")
            )
        )
    inventory = _object(contract["inventory"], location="contract.inventory")
    scenario_ids = tuple(
        dict.fromkeys(test_case.trace_group.group_id for test_case in cases if test_case.trace_group is not None)
    )
    expected_scenario_ids = _strings(inventory["scenarioIds"], location="contract.inventory.scenarioIds")
    step_ids = tuple(step.step_id for test_case in cases for step in test_case.execution_steps)
    if (
        scenario_ids != expected_scenario_ids
        or len(cases) != inventory["caseCount"]
        or len(step_ids) != inventory["stepCount"]
        or len(step_ids) != len(set(step_ids))
    ):
        raise ValueError("DCR parity contract does not satisfy its pinned scenario/case/step inventory")
    for test_case in cases:
        locked_statuses = tuple(
            step.expected_status for step in test_case.execution_steps if step.expected_status is not None
        )
        if locked_statuses != test_case.expected_http_statuses:
            raise ValueError(f"DCR case {test_case.test_case_id} status assertions drifted from its locked outcome")
    return TestCatalogue(
        key=DCR_CATALOGUE_KEY,
        catalogue_version=DCR_CATALOGUE_VERSION,
        test_cases=tuple(cases),
        capabilities=_endpoint_capabilities(),
        configuration_requirements=_configuration_requirements(),
        runtime_capabilities=_runtime_capabilities(contract),
        provenance=_provenance(contract, references),
    )


DCR_3_4_CATALOGUE = _build_catalogue()
"""Pinned executable Open Banking UK DCR 3.4 catalogue."""
