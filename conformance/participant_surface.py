"""Shared participant-plan boundary for browser, CLI, and REST surfaces."""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import MappingProxyType

from conformance.catalogue import CatalogueKey, CompiledTestPlan, EndpointRef, TestCatalogue
from conformance.catalogue_registry import resolve_catalogue
from conformance.configuration_contracts import (
    ConfigurationContractError,
    ExecutionManifest,
    ExecutionManifestGenerationError,
    ParticipantPlan,
    ParticipantPlanCompilationError,
    PreparedExecutionManifest,
    RequirementsCatalogue,
    ResolvedPlan,
    SuiteRelease,
    TestDefinitionCatalogue,
    compile_participant_plan,
    generate_execution_manifest,
    load_requirements_catalogue,
    load_suite_release,
    load_test_definition_catalogue,
    parse_participant_plan,
    participant_plan_to_document,
    resolve_participant_plan,
    validate_execution_manifest_compatibility,
)
from conformance.configuration_contracts.compiled_plan_adapter import LegacyExecutionEngine
from conformance.json_types import JsonObject, JsonValue
from conformance.model_bank_config import ModelBankConfig
from conformance.results import ResultTraceabilitySource, build_safe_participant_plan_snapshot
from conformance.test_plan_validation import (
    PreparedTestPlan,
    TestPlanValidationError,
    TestPlanValidationIssue,
    TestPlanValidationResult,
    prepare_test_plan_for_run,
)

_CONFIGURATION_ROOT = Path(__file__).resolve().parent / "configuration_contracts"
_REGISTRY_PATH = _CONFIGURATION_ROOT / "bundles" / "open-banking-mvp" / "suite-release.json"
_CATALOGUE_ROOT = _CONFIGURATION_ROOT / "catalogues"

_CATALOGUE_DIRECTORY_BY_SCOPE_VERSION = {
    ("ais", "3.1.11"): ("ais", "v3_1_11"),
    ("ais", "4.0.1"): ("ais", "v4_0_1"),
    ("cbpii", "3.1.11"): ("cbpii", "v3_1_11"),
    ("cbpii", "4.0.1"): ("cbpii", "v4_0_1"),
    ("dcr", "3.4"): ("dcr", "v3_4"),
    ("pis", "3.1.11"): ("pis", "v3_1_11"),
    ("pis", "4.0.1"): ("pis", "v4_0_1"),
    ("vrp", "3.1.11"): ("vrp", "v3_1_11"),
    ("vrp", "4.0.1"): ("vrp", "v4_0_1"),
}
_LEGACY_API_BY_SCOPE = {
    "ais": "ais",
    "cbpii": "cbpii",
    "dcr": "dcr",
    "pis": "pis",
    "vrp": "vrp",
}
_RESOURCE_GROUP_ID_BY_SCOPE = {
    "ais": "AIS",
    "cbpii": "CBPII",
    "pis": "PIS",
    "vrp": "VRP",
}
_RESOURCE_PATH_PREFIX_BY_SCOPE = {
    ("ais", "3.1.11"): "/open-banking/v3.1/aisp",
    ("ais", "4.0.1"): "/open-banking/v4.0/aisp",
    ("cbpii", "3.1.11"): "/open-banking/v3.1/cbpii",
    ("cbpii", "4.0.1"): "/open-banking/v4.0/cbpii",
    ("pis", "3.1.11"): "/open-banking/v3.1/pisp",
    ("pis", "4.0.1"): "/open-banking/v4.0/pisp",
}
_LEGACY_RUNTIME_INPUT_BY_PREDEFINED_SUFFIX = {
    "from-booking-date-time": "fromBookingDateTime",
    "to-booking-date-time": "toBookingDateTime",
    "debtor-account-scheme": "debtorAccountSchemeName",
    "debtor-account-identification": "debtorAccountIdentification",
    "instructed-currency": "cbpiiInstructedAmountCurrency",
    "creditor-account-scheme-name": "vrpCreditorAccountSchemeName",
    "creditor-account-identification": "vrpCreditorAccountIdentification",
    "creditor-account-name": "vrpCreditorAccountName",
    "currency": "vrpInstructedAmountCurrency",
    "valid-from-date-time": "vrpValidFromDateTime",
    "valid-to-date-time": "vrpValidToDateTime",
}
_DCR_OBSERVATION_ID_BY_TEST_DEFINITION_ID = {
    "dcr.v34.test.registration.positive": "DCR-002-C01-S02",
    "dcr.v34.test.registration.expired": "DCR-004-C01-S02",
    "dcr.v34.test.registration.invalid-issuer": "DCR-004-C02-S02",
    "dcr.v34.test.registration.empty-issuer": "DCR-004-C03-S02",
    "dcr.v34.test.registration.overlong-issuer": "DCR-004-C04-S02",
    "dcr.v34.test.registration.invalid-auth-method": "DCR-004-C05-S02",
    "dcr.v34.test.retrieval.positive": "DCR-005-C03-S01",
    "dcr.v34.test.retrieval.unknown-client": "DCR-003-C04-S01",
    "dcr.v34.test.retrieval.missing-authorization": "DCR-007-C02-S02",
    "dcr.v34.test.update.positive": "DCR-008-C03-S02",
    "dcr.v34.test.update.unknown-client": "DCR-009-C04-S02",
    "dcr.v34.test.deletion.positive": "DCR-002-C03-S01",
}


class ParticipantSurfaceError(ValueError):
    """Raised when a participant plan cannot cross a public surface."""


@dataclass(frozen=True, slots=True)
class ParticipantCatalogue:
    """Trusted release catalogue selected for one participant-plan scope."""

    suite_release: SuiteRelease
    requirements: RequirementsCatalogue
    test_definitions: TestDefinitionCatalogue


@dataclass(frozen=True, slots=True)
class PreparedParticipantPlan:
    """Shared public-plan preparation consumed by every launch surface."""

    participant_plan: ParticipantPlan
    resolved_plan: ResolvedPlan
    execution_manifest: ExecutionManifest
    prepared_execution: PreparedExecutionManifest
    catalogue: ParticipantCatalogue
    config: ModelBankConfig
    compiled_plan: CompiledTestPlan
    runtime_inputs: Mapping[str, JsonValue]
    safe_snapshot: JsonObject
    validation: TestPlanValidationResult


@cache
def participant_suite_release() -> SuiteRelease:
    """Load the coordinator-owned participant-facing suite release."""
    return load_suite_release(_REGISTRY_PATH)


@cache
def supported_participant_catalogues() -> tuple[ParticipantCatalogue, ...]:
    """Load every requirements/test pair bound by the participant release."""
    suite_release = participant_suite_release()
    catalogues: list[ParticipantCatalogue] = []
    for family, version_directory in _CATALOGUE_DIRECTORY_BY_SCOPE_VERSION.values():
        root = _CATALOGUE_ROOT / family / version_directory
        requirements = load_requirements_catalogue(root / "requirements.json")
        test_definitions = load_test_definition_catalogue(root / "test-definitions.json")
        catalogues.append(
            ParticipantCatalogue(
                suite_release=suite_release,
                requirements=requirements,
                test_definitions=test_definitions,
            )
        )
    return tuple(catalogues)


def participant_catalogue_for_plan(plan: ParticipantPlan) -> ParticipantCatalogue:
    """Resolve a plan only against the trusted central release registry."""
    suite_release = participant_suite_release()
    if plan.suite_release_id != suite_release.id:
        raise ParticipantSurfaceError(
            f"Participant plan selects suite release {plan.suite_release_id!s}; this tool accepts {suite_release.id!s}"
        )
    for catalogue in supported_participant_catalogues():
        specification = catalogue.requirements.specification
        if (
            plan.scheme == catalogue.requirements.scheme
            and plan.specification.id == specification.id
            and plan.specification.version == specification.version
            and plan.specification.requirements_scope == specification.requirements_scope
        ):
            return catalogue
    raise ParticipantSurfaceError(
        "No trusted requirements catalogue matches "
        f"{plan.specification.id!s}/{plan.specification.version}/"
        f"{plan.specification.requirements_scope!s}"
    )


def compile_participant_document(raw_plan: object) -> tuple[ParticipantPlan, ParticipantCatalogue, ResolvedPlan]:
    """Parse and resolve a participant-plan document for review or import."""
    try:
        participant_plan = parse_participant_plan(raw_plan)
        catalogue = participant_catalogue_for_plan(participant_plan)
        resolved_plan = compile_participant_plan(
            catalogue.suite_release,
            catalogue.requirements,
            catalogue.test_definitions,
            participant_plan,
        )
    except (ConfigurationContractError, ParticipantPlanCompilationError) as error:
        raise ParticipantSurfaceError(str(error)) from error
    return participant_plan, catalogue, resolved_plan


def resolve_participant_document(raw_plan: object) -> tuple[ParticipantPlan, ParticipantCatalogue, ResolvedPlan]:
    """Parse and resolve a plan while preserving non-runnable findings."""
    try:
        participant_plan = parse_participant_plan(raw_plan)
        catalogue = participant_catalogue_for_plan(participant_plan)
        resolved_plan = resolve_participant_plan(
            catalogue.suite_release,
            catalogue.requirements,
            catalogue.test_definitions,
            participant_plan,
        )
    except ConfigurationContractError as error:
        raise ParticipantSurfaceError(str(error)) from error
    return participant_plan, catalogue, resolved_plan


def prepare_participant_plan_for_run(
    raw_plan: object,
    *,
    base_dir: Path,
) -> PreparedParticipantPlan:
    """Prepare the shared participant document for compatibility execution."""
    participant_plan, catalogue, resolved_plan = compile_participant_document(raw_plan)
    if participant_plan.execution_configuration is None:
        raise ParticipantSurfaceError("executionConfiguration is required to launch a participant plan")
    try:
        execution_manifest = generate_execution_manifest(
            resolved_plan,
            catalogue.requirements,
            catalogue.test_definitions,
        )
        compatibility_document = _legacy_compatibility_document(
            participant_plan,
            catalogue.requirements,
            resolved_plan,
        )
        prepared_legacy = prepare_test_plan_for_run(compatibility_document, base_dir=base_dir)
    except (ExecutionManifestGenerationError, TestPlanValidationError) as error:
        raise ParticipantSurfaceError(f"Execution configuration is invalid: {error}") from error
    validation = TestPlanValidationResult(
        schema_version=participant_plan.schema_version,
        execution_mode="certification",
        issues=tuple(
            TestPlanValidationIssue(
                layer="business",
                severity="warning",
                message=f"{finding.code!s}: {finding.message}",
            )
            for finding in resolved_plan.findings
        ),
    )
    safe_snapshot = build_safe_participant_plan_snapshot(participant_plan, catalogue.requirements)
    prepared_execution = _prepare_execution_binding(
        participant_plan,
        resolved_plan,
        execution_manifest,
        prepared_legacy,
        safe_snapshot=safe_snapshot,
        runtime_input_base_dir=base_dir,
    )
    return PreparedParticipantPlan(
        participant_plan=participant_plan,
        resolved_plan=resolved_plan,
        execution_manifest=execution_manifest,
        prepared_execution=prepared_execution,
        catalogue=catalogue,
        config=prepared_legacy.config,
        compiled_plan=prepared_legacy.compiled_plan,
        runtime_inputs=prepared_legacy.runtime_inputs,
        safe_snapshot=safe_snapshot,
        validation=validation,
    )


def _prepare_execution_binding(
    participant_plan: ParticipantPlan,
    resolved_plan: ResolvedPlan,
    manifest: ExecutionManifest,
    prepared_legacy: PreparedTestPlan,
    *,
    safe_snapshot: JsonObject,
    runtime_input_base_dir: Path,
) -> PreparedExecutionManifest:
    observation_ids = _manifest_observation_ids(manifest, prepared_legacy.compiled_plan)
    scope = str(participant_plan.specification.requirements_scope)
    prepared = PreparedExecutionManifest(
        manifest=manifest,
        engine=LegacyExecutionEngine.DCR if scope == "dcr" else LegacyExecutionEngine.READ_WRITE,
        compiled_plan=prepared_legacy.compiled_plan,
        runtime_inputs=prepared_legacy.runtime_inputs,
        runtime_input_base_dir=runtime_input_base_dir,
        result_traceability=ResultTraceabilitySource(
            execution_manifest=manifest,
            resolved_plan=resolved_plan,
            participant_plan_snapshot=safe_snapshot,
            result_observation_id_by_manifest_step_id=observation_ids,
        ),
    )
    validate_execution_manifest_compatibility(prepared)
    return prepared


def _manifest_observation_ids(
    manifest: ExecutionManifest,
    compiled_plan: CompiledTestPlan,
) -> Mapping[str, str]:
    request_candidates = [
        (request.method, request.path, request.step_id)
        for test_case in compiled_plan.test_cases
        for request in test_case.request_steps
    ]
    execution_candidates = [
        step.step_id for test_case in compiled_plan.test_cases for step in test_case.execution_steps
    ]
    if execution_candidates and not request_candidates:
        available_ids = set(execution_candidates)
        dcr_observations: dict[str, str] = {}
        for manifest_step in manifest.steps:
            observation_id = _DCR_OBSERVATION_ID_BY_TEST_DEFINITION_ID.get(str(manifest_step.test_definition_id))
            if observation_id is None or observation_id not in available_ids:
                raise ParticipantSurfaceError(
                    "The DCR compatibility executor cannot provide a stable observation for "
                    f"{manifest_step.test_definition_id!s}"
                )
            dcr_observations[str(manifest_step.id)] = observation_id
        return MappingProxyType(dcr_observations)

    unused = list(request_candidates)
    observations: dict[str, str] = {}
    for manifest_step in manifest.steps:
        normalized_manifest_path = _normalized_placeholder_path(manifest_step.request.path)
        match_index = next(
            (
                index
                for index, (method, path, _step_id) in enumerate(unused)
                if method == manifest_step.request.method.value
                and _normalized_placeholder_path(path).endswith(normalized_manifest_path)
            ),
            None,
        )
        if match_index is None:
            raise ParticipantSurfaceError(
                f"Compatibility execution has no observation for manifest step {manifest_step.id!s}"
            )
        _method, _path, observation_id = unused.pop(match_index)
        observations[str(manifest_step.id)] = observation_id
    return MappingProxyType(observations)


def participant_plan_export_document(
    plan: ParticipantPlan,
    requirements: RequirementsCatalogue,
    *,
    include_secrets: bool,
) -> JsonObject:
    """Return a complete or secret-safe participant-plan export."""
    if include_secrets:
        return participant_plan_to_document(plan)
    document = participant_plan_to_document(plan)
    sensitive_ids = {str(item.id) for item in requirements.predefined_inputs if item.sensitivity != "non-sensitive"}
    raw_inputs = document.get("predefinedInputs")
    if isinstance(raw_inputs, list):
        document["predefinedInputs"] = [
            item for item in raw_inputs if isinstance(item, dict) and item.get("inputId") not in sensitive_ids
        ]
    raw_configuration = document.get("executionConfiguration")
    if isinstance(raw_configuration, dict):
        raw_configuration["compatibilityRuntimeInputs"] = {}
        security = raw_configuration.get("securityEnvironment")
        if isinstance(security, dict):
            for key in (
                "clientAssertionIssuer",
                "clientAssertionSubject",
                "clientId",
                "signingKeyId",
            ):
                security.pop(key, None)
        dcr = raw_configuration.get("dynamicClientRegistration")
        if isinstance(dcr, dict):
            dcr.pop("registrationIssuerOverride", None)
    return document


def _legacy_compatibility_document(
    plan: ParticipantPlan,
    requirements: RequirementsCatalogue,
    resolved_plan: ResolvedPlan,
) -> JsonObject:
    configuration = plan.execution_configuration
    if configuration is None:
        raise ParticipantSurfaceError("executionConfiguration is required to launch a participant plan")
    scope = str(plan.specification.requirements_scope)
    runtime_inputs = dict(configuration.compatibility_runtime_inputs)
    runtime_inputs.update(_legacy_predefined_inputs(plan, scope=scope))
    security_environment = deepcopy(dict(configuration.security_environment))
    if scope == "dcr":
        return {
            "schemaVersion": "1.0",
            "specification": {
                "family": "OBL_DCR",
                "scheme": str(plan.scheme),
                "name": str(plan.specification.id),
                "version": plan.specification.version,
            },
            "securityEnvironment": security_environment,
            "endpoints": _legacy_dcr_endpoints(requirements, resolved_plan),
            "dynamicClientRegistration": deepcopy(dict(configuration.dynamic_client_registration)),
            "metadata": deepcopy(dict(configuration.metadata)),
        }
    return {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": plan.specification.version,
            "profile": "FAPI1_ADVANCED",
        },
        "securityEnvironment": security_environment,
        "resourceGroups": [
            {
                "id": _RESOURCE_GROUP_ID_BY_SCOPE[scope],
                "endpoints": _legacy_read_write_endpoints(requirements, resolved_plan),
            }
        ],
        "businessTestData": {"runtimeInputs": runtime_inputs},
        "metadata": deepcopy(dict(configuration.metadata)),
    }


def _legacy_read_write_endpoints(
    requirements: RequirementsCatalogue,
    resolved_plan: ResolvedPlan,
) -> list[JsonValue]:
    selected_ids = {endpoint.id for endpoint in resolved_plan.endpoints}
    scope = str(requirements.specification.requirements_scope)
    version = requirements.specification.version
    prefix = _RESOURCE_PATH_PREFIX_BY_SCOPE.get((scope, version), "")
    legacy_catalogue = _legacy_catalogue_for_scope_version(scope, version)
    legacy_refs = {
        endpoint_ref
        for test_case in legacy_catalogue.test_cases
        for endpoint_ref in test_case.applicability.endpoint_refs
    }
    legacy_refs.update(
        EndpointRef(
            method=request.method,
            path=request.path,
        )
        for test_case in legacy_catalogue.test_cases
        for request in test_case.request_steps
    )
    legacy_refs.update(
        endpoint_ref for capability in legacy_catalogue.capabilities for endpoint_ref in capability.endpoint_refs
    )
    endpoints: list[JsonValue] = []
    for endpoint in requirements.endpoints:
        if endpoint.id not in selected_ids:
            continue
        expected_path = f"{prefix}{endpoint.path}"
        normalized_path = _normalized_placeholder_path(expected_path)
        legacy_ref = next(
            (
                candidate
                for candidate in sorted(
                    legacy_refs,
                    key=lambda item: ("${" in item.path, item.path),
                )
                if candidate.method == endpoint.method.value
                and _normalized_placeholder_path(candidate.path) == normalized_path
            ),
            None,
        )
        if legacy_ref is None:
            raise ParticipantSurfaceError(f"No compatibility endpoint matches {endpoint.method.value} {endpoint.path}")
        endpoints.append(
            {
                "method": endpoint.method.value,
                "path": legacy_ref.path,
                **({"operationId": endpoint.operation_id} if endpoint.operation_id is not None else {}),
            }
        )
    return endpoints


def _legacy_dcr_endpoints(
    requirements: RequirementsCatalogue,
    resolved_plan: ResolvedPlan,
) -> list[JsonValue]:
    selected_ids = {endpoint.id for endpoint in resolved_plan.endpoints}
    endpoints: list[JsonValue] = []
    for endpoint in requirements.endpoints:
        if endpoint.id not in selected_ids:
            continue
        registration = endpoint.method.value == "POST" and endpoint.path == "/register"
        endpoints.append(
            {
                "method": endpoint.method.value,
                "path": endpoint.path,
                "required": registration,
                "locked": registration,
            }
        )
    return endpoints


def _legacy_predefined_inputs(plan: ParticipantPlan, *, scope: str) -> JsonObject:
    values: JsonObject = {}
    for participant_input in plan.predefined_inputs:
        input_id = str(participant_input.input_id)
        suffix = input_id.rsplit(".input.", maxsplit=1)[-1]
        value = participant_input.value
        if suffix == "standing-order-frequency":
            if isinstance(value, str):
                values["pisStandingOrderFrequencyV31"] = value
            else:
                values["pisStandingOrderFrequencyType"] = value.frequency_type
                if value.count_per_period is not None:
                    values["pisStandingOrderFrequencyCountPerPeriod"] = value.count_per_period
                if value.point_in_time is not None:
                    values["pisStandingOrderFrequencyPointInTime"] = value.point_in_time
            continue
        legacy_id: str | None
        if scope == "cbpii" and suffix == "instructed-amount":
            legacy_id = "cbpiiInstructedAmountAmount"
        elif scope == "vrp" and suffix == "instructed-amount":
            legacy_id = "vrpInstructedAmountAmount"
        else:
            legacy_id = _LEGACY_RUNTIME_INPUT_BY_PREDEFINED_SUFFIX.get(suffix)
        if legacy_id is None:
            continue
        if isinstance(value, str):
            values[legacy_id] = value
    return values


def legacy_catalogue_for_participant_plan(plan: ParticipantPlan) -> TestCatalogue:
    """Return the existing executable catalogue used by the compatibility adapter."""
    scope = str(plan.specification.requirements_scope)
    return _legacy_catalogue_for_scope_version(scope, plan.specification.version)


def _legacy_catalogue_for_scope_version(scope: str, version: str) -> TestCatalogue:
    api = _LEGACY_API_BY_SCOPE.get(scope)
    if api is None:
        raise ParticipantSurfaceError(f"No compatibility catalogue exists for scope {scope}")
    legacy_version = {
        "3.1.11": "v3.1",
        "4.0.1": "v4.0",
    }.get(version, f"v{version}")
    return resolve_catalogue(
        CatalogueKey(
            standard="open-banking",
            version=legacy_version,
            api=api,
        )
    )


def _normalized_placeholder_path(path: str) -> str:
    """Normalize path-parameter names for cross-catalogue operation matching."""
    return re.sub(r"\$\{[^}]+\}|\{[^}]+\}", "{}", path)
