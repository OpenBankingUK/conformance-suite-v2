"""Shared participant-plan boundary for browser, CLI, and REST surfaces."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import MappingProxyType

from conformance.configuration_contracts import (
    PreparedExecutionManifest,
    preflight_suite_release_artifacts,
    validate_execution_manifest_compatibility,
)
from conformance.configuration_contracts.compiled_plan_adapter import LegacyExecutionEngine
from conformance.configuration_contracts.diagnostics import ConfigurationContractError
from conformance.configuration_contracts.models import (
    SuiteRelease,
)
from conformance.configuration_contracts.v2_compiler import (
    ParticipantPlanCompilationError,
    compile_participant_plan,
    resolve_participant_plan,
)
from conformance.configuration_contracts.v2_execution_manifest import (
    ExecutionManifestGenerationError,
    generate_execution_manifest,
)
from conformance.configuration_contracts.v2_loader import (
    load_suite_release,
    load_test_definition_catalogue,
    parse_participant_plan,
    participant_plan_to_document,
)
from conformance.configuration_contracts.v2_models import (
    ExecutionManifest,
    ParticipantPlan,
    ResolvedPlan,
    TestDefinitionCatalogue,
)
from conformance.json_types import JsonObject, JsonValue
from conformance.model_bank_config import ConfigError, ModelBankConfig, parse_model_bank_config
from conformance.plan_configuration import (
    dcr_execution_runtime_inputs,
    parse_dcr_plan_configuration,
    validate_dcr_file_references,
)
from conformance.results import ResultTraceabilitySource, build_safe_participant_plan_snapshot
from conformance.test_plan_validation import (
    TestPlanValidationIssue,
    TestPlanValidationResult,
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
_LEGACY_RUNTIME_INPUT_BY_PREDEFINED_SUFFIX = {
    "from-booking-date-time": "fromBookingDateTime",
    "to-booking-date-time": "toBookingDateTime",
    "debtor-account-scheme": "debtorAccountSchemeName",
    "debtor-account-identification": "debtorAccountIdentification",
    "debtor-account-name": "debtorAccountName",
    "instructed-currency": "cbpiiInstructedAmountCurrency",
    "creditor-account-scheme-name": "vrpCreditorAccountSchemeName",
    "creditor-account-identification": "vrpCreditorAccountIdentification",
    "creditor-account-name": "vrpCreditorAccountName",
    "currency": "vrpInstructedAmountCurrency",
    "valid-from-date-time": "vrpValidFromDateTime",
    "valid-to-date-time": "vrpValidToDateTime",
}
_PIS_LEGACY_RUNTIME_INPUT_BY_PREDEFINED_SUFFIX = {
    "creditor-account-scheme-name": "pisCreditorAccountSchemeName",
    "creditor-account-identification": "pisCreditorAccountIdentification",
    "creditor-account-name": "pisCreditorAccountName",
    "international-creditor-account-scheme-name": "pisInternationalCreditorAccountSchemeName",
    "international-creditor-account-identification": "pisInternationalCreditorAccountIdentification",
    "international-creditor-account-name": "pisInternationalCreditorAccountName",
    "instructed-amount": "pisInstructedAmountAmount",
    "instructed-currency": "pisInstructedAmountCurrency",
    "currency-of-transfer": "pisCurrencyOfTransfer",
    "requested-execution-date-time": "pisRequestedExecutionDateTime",
    "first-payment-date-time": "pisFirstPaymentDateTime",
}
_PIS_BUSINESS_RUNTIME_ALIASES = frozenset(
    {
        *_PIS_LEGACY_RUNTIME_INPUT_BY_PREDEFINED_SUFFIX.values(),
        "pisStandingOrderFrequencyV31",
        "pisStandingOrderFrequencyType",
        "pisStandingOrderFrequencyCountPerPeriod",
        "pisStandingOrderFrequencyPointInTime",
    }
)
_PIS_ALLOWED_COMPATIBILITY_RUNTIME_INPUTS = frozenset(
    {
        "accessToken",
        "accessTokenRef",
        "discoveryUrl",
        "domesticPaymentConsentId",
        "domesticPaymentId",
        "domesticScheduledPaymentConsentId",
        "domesticScheduledPaymentId",
        "domesticStandingOrderConsentId",
        "domesticStandingOrderId",
        "idempotencyKey",
        "internationalPaymentConsentId",
        "internationalPaymentId",
        "internationalScheduledPaymentConsentId",
        "internationalScheduledPaymentId",
        "invalidAccessToken",
        "resourceBaseUrl",
        "xCustomerIpAddress",
        "xCustomerUserAgent",
        "xFapiFinancialId",
        "xFapiInteractionId",
    }
)


class ParticipantSurfaceError(ValueError):
    """Raised when a participant plan cannot cross a public surface."""


@dataclass(frozen=True, slots=True)
class ParticipantCatalogue:
    """Trusted released executable catalogue selected for participant intent."""

    suite_release: SuiteRelease
    test_catalogue: TestDefinitionCatalogue


@dataclass(frozen=True, slots=True)
class PreparedParticipantPlan:
    """Shared public-plan preparation consumed by every launch surface."""

    participant_plan: ParticipantPlan
    resolved_plan: ResolvedPlan
    execution_manifest: ExecutionManifest
    prepared_execution: PreparedExecutionManifest
    catalogue: ParticipantCatalogue
    config: ModelBankConfig
    runtime_inputs: Mapping[str, JsonValue]
    safe_snapshot: JsonObject
    validation: TestPlanValidationResult


@cache
def participant_suite_release() -> SuiteRelease:
    """Load the coordinator-owned participant-facing suite release."""
    return load_suite_release(_REGISTRY_PATH)


@cache
def supported_participant_catalogues() -> tuple[ParticipantCatalogue, ...]:
    """Load every executable catalogue bound by the participant release."""
    suite_release = participant_suite_release()
    catalogues: list[ParticipantCatalogue] = []
    for family, version_directory in _CATALOGUE_DIRECTORY_BY_SCOPE_VERSION.values():
        root = _CATALOGUE_ROOT / family / version_directory
        test_catalogue = load_test_definition_catalogue(root / "test-catalogue.v2.json")
        catalogues.append(
            ParticipantCatalogue(
                suite_release=suite_release,
                test_catalogue=test_catalogue,
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
        specification = catalogue.test_catalogue.specification
        if (
            plan.scheme == catalogue.test_catalogue.scheme
            and plan.specification.id == specification.id
            and plan.specification.version == specification.version
            and plan.specification.test_scope == specification.test_scope
        ):
            return catalogue
    raise ParticipantSurfaceError(
        "No trusted executable test catalogue matches "
        f"{plan.specification.id!s}/{plan.specification.version}/"
        f"{plan.specification.test_scope!s}"
    )


def compile_participant_document(raw_plan: object) -> tuple[ParticipantPlan, ParticipantCatalogue, ResolvedPlan]:
    """Parse and resolve a participant-plan document for review or import."""
    try:
        participant_plan = parse_participant_plan(raw_plan)
        _validate_pis_compatibility_inputs(participant_plan)
        catalogue = participant_catalogue_for_plan(participant_plan)
        resolved_plan = compile_participant_plan(
            catalogue.suite_release,
            catalogue.test_catalogue,
            participant_plan,
        )
    except (ConfigurationContractError, ParticipantPlanCompilationError) as error:
        raise ParticipantSurfaceError(str(error)) from error
    return participant_plan, catalogue, resolved_plan


def resolve_participant_document(raw_plan: object) -> tuple[ParticipantPlan, ParticipantCatalogue, ResolvedPlan]:
    """Parse and resolve a plan while preserving non-runnable findings."""
    try:
        participant_plan = parse_participant_plan(raw_plan)
        _validate_pis_compatibility_inputs(participant_plan)
        catalogue = participant_catalogue_for_plan(participant_plan)
        resolved_plan = resolve_participant_plan(
            catalogue.suite_release,
            catalogue.test_catalogue,
            participant_plan,
        )
    except ConfigurationContractError as error:
        raise ParticipantSurfaceError(str(error)) from error
    return participant_plan, catalogue, resolved_plan


def _validate_pis_compatibility_inputs(plan: ParticipantPlan) -> None:
    """Restrict PIS compatibility inputs to technical runtime categories."""
    configuration = plan.execution_configuration
    if configuration is None or str(plan.specification.test_scope) != "pis":
        return
    forbidden = sorted(_PIS_BUSINESS_RUNTIME_ALIASES.intersection(configuration.compatibility_runtime_inputs))
    if forbidden:
        raise ParticipantSurfaceError(
            "PIS participant business values must use predefinedInputs, not "
            "executionConfiguration.compatibilityRuntimeInputs: " + ", ".join(forbidden)
        )
    unsupported = sorted(
        set(configuration.compatibility_runtime_inputs).difference(_PIS_ALLOWED_COMPATIBILITY_RUNTIME_INPUTS)
    )
    if unsupported:
        raise ParticipantSurfaceError(
            "Unsupported PIS compatibility runtime inputs; only environment, "
            "credential, protocol-session, and runtime-generated values are allowed: " + ", ".join(unsupported)
        )


def prepare_participant_plan_for_run(
    raw_plan: object,
    *,
    base_dir: Path,
) -> PreparedParticipantPlan:
    """Prepare the shared participant document for manifest-authoritative execution."""
    participant_plan, catalogue, resolved_plan = compile_participant_document(raw_plan)
    if participant_plan.execution_configuration is None:
        raise ParticipantSurfaceError("executionConfiguration is required to launch a participant plan")
    try:
        execution_manifest = generate_execution_manifest(
            resolved_plan,
            catalogue.test_catalogue,
        )
        config, runtime_inputs = _prepare_runtime_environment(participant_plan, base_dir=base_dir)
    except (ConfigError, ExecutionManifestGenerationError) as error:
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
    safe_snapshot = build_safe_participant_plan_snapshot(participant_plan, catalogue.test_catalogue)
    prepared_execution = _prepare_execution_binding(
        participant_plan,
        resolved_plan,
        execution_manifest,
        runtime_inputs=runtime_inputs,
        suite_release=catalogue.suite_release,
        safe_snapshot=safe_snapshot,
        runtime_input_base_dir=base_dir,
    )
    return PreparedParticipantPlan(
        participant_plan=participant_plan,
        resolved_plan=resolved_plan,
        execution_manifest=execution_manifest,
        prepared_execution=prepared_execution,
        catalogue=catalogue,
        config=config,
        runtime_inputs=runtime_inputs,
        safe_snapshot=safe_snapshot,
        validation=validation,
    )


def _prepare_execution_binding(
    participant_plan: ParticipantPlan,
    resolved_plan: ResolvedPlan,
    manifest: ExecutionManifest,
    *,
    runtime_inputs: Mapping[str, JsonValue],
    suite_release: SuiteRelease,
    safe_snapshot: JsonObject,
    runtime_input_base_dir: Path,
) -> PreparedExecutionManifest:
    scope = str(participant_plan.specification.test_scope)
    prepared = PreparedExecutionManifest(
        manifest=manifest,
        engine=LegacyExecutionEngine.DCR if scope == "dcr" else LegacyExecutionEngine.READ_WRITE,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=runtime_input_base_dir,
        artifact_resolver=preflight_suite_release_artifacts(manifest, suite_release),
        result_traceability=ResultTraceabilitySource(
            execution_manifest=manifest,
            resolved_plan=resolved_plan,
            participant_plan_snapshot=safe_snapshot,
        ),
    )
    validate_execution_manifest_compatibility(prepared)
    return prepared


def participant_plan_export_document(
    plan: ParticipantPlan,
    catalogue: TestDefinitionCatalogue,
    *,
    include_secrets: bool,
) -> JsonObject:
    """Return a complete or secret-safe participant-plan export."""
    if include_secrets:
        return participant_plan_to_document(plan)
    document = participant_plan_to_document(plan)
    sensitive_ids = {str(item.id) for item in catalogue.predefined_inputs if item.sensitivity != "non-sensitive"}
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


def _prepare_runtime_environment(
    plan: ParticipantPlan,
    *,
    base_dir: Path,
) -> tuple[ModelBankConfig, Mapping[str, JsonValue]]:
    """Validate non-work runtime configuration without rebuilding test work."""
    configuration = plan.execution_configuration
    if configuration is None:
        raise ParticipantSurfaceError("executionConfiguration is required to launch a participant plan")
    scope = str(plan.specification.test_scope)
    runtime_inputs = dict(configuration.compatibility_runtime_inputs)
    runtime_inputs.update(_participant_input_runtime_values(plan, scope=scope))
    security = deepcopy(dict(configuration.security_environment))
    for key in ("discoveryUrl", "resourceBaseUrl"):
        if key in security:
            runtime_inputs[key] = security[key]
    if scope == "dcr":
        dcr_config = parse_dcr_plan_configuration(
            configuration.security_environment,
            configuration.dynamic_client_registration,
            configuration.metadata,
        )
        validate_dcr_file_references(dcr_config)
        runtime_inputs.update(dcr_execution_runtime_inputs(dcr_config))
    config_document = _model_bank_config_document(security)
    config = parse_model_bank_config(config_document, base_dir=base_dir)
    _validate_runtime_file_references(config)
    return config, MappingProxyType(runtime_inputs)


def _participant_input_runtime_values(plan: ParticipantPlan, *, scope: str) -> JsonObject:
    """Expose participant values only as non-authoritative runtime data."""
    values: JsonObject = {}
    for participant_input in plan.predefined_inputs:
        input_id = str(participant_input.input_id)
        value = participant_input.value
        values[input_id] = (
            {
                "frequencyType": value.frequency_type,
                **({"countPerPeriod": value.count_per_period} if value.count_per_period is not None else {}),
                **({"pointInTime": value.point_in_time} if value.point_in_time is not None else {}),
            }
            if not isinstance(value, str)
            else value
        )
        suffix = input_id.rsplit(".input.", maxsplit=1)[-1]
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
        elif scope == "pis":
            legacy_id = _PIS_LEGACY_RUNTIME_INPUT_BY_PREDEFINED_SUFFIX.get(suffix)
        else:
            legacy_id = _LEGACY_RUNTIME_INPUT_BY_PREDEFINED_SUFFIX.get(suffix)
        if legacy_id is None:
            continue
        if isinstance(value, str):
            values[legacy_id] = value
    return values


def _model_bank_config_document(security: JsonObject) -> JsonObject:
    """Map the v2 security environment to the runtime's typed configuration."""
    document: JsonObject = {}
    if "discoveryUrl" in security:
        document["discoveryUrl"] = security["discoveryUrl"]
    oauth_keys = {
        "clientId": "clientId",
        "redirectUri": "redirectUri",
        "authorizationEndpoint": "authorizationEndpoint",
        "issuer": "issuer",
        "tokenEndpoint": "tokenEndpoint",
        "resourceBaseUrl": "resourceBaseUrl",
        "responseType": "responseType",
        "signingAlgorithm": "requestObjectSigningAlg",
    }
    oauth = {target: security[source] for source, target in oauth_keys.items() if source in security}
    if {"clientId", "redirectUri"} <= oauth.keys():
        document["oauth"] = oauth
    if "resourceBaseUrl" in security:
        document["resourceServer"] = {"baseUrl": security["resourceBaseUrl"]}
    mtls = security.get("mtls")
    if isinstance(mtls, dict):
        tls_keys = {
            "caBundlePath": "caBundlePath",
            "certificatePath": "clientCertificatePath",
            "privateKeyPath": "clientPrivateKeyPath",  # pragma: allowlist secret -- configuration field names only
        }
        document["tls"] = {target: mtls[source] for source, target in tls_keys.items() if source in mtls}
    signing_keys = {
        "signingCertificatePath": "signingCertificatePath",
        "signingPrivateKeyPath": "signingPrivateKeyPath",  # pragma: allowlist secret -- configuration field names only
        "signingKeyId": "kid",
        "clientAssertionIssuer": "clientAssertionIssuer",
        "clientAssertionSubject": "clientAssertionSubject",
        "clientAuthMethod": "tokenEndpointAuthMethod",
    }
    signing = {target: security[source] for source, target in signing_keys.items() if source in security}
    if set(signing_keys.values()) <= signing.keys():
        document["fapiSigning"] = signing
    return document


def _validate_runtime_file_references(config: ModelBankConfig) -> None:
    """Fail before launch when configured credential paths do not exist."""
    paths = (
        config.tls.ca_bundle_path,
        config.tls.client_certificate_path,
        config.tls.client_private_key_path,
        None if config.fapi_signing is None else config.fapi_signing.signing_certificate_path,
        None if config.fapi_signing is None else config.fapi_signing.signing_private_key_path,
    )
    if missing := next((path for path in paths if path is not None and not path.is_file()), None):
        raise ConfigError(f"Configured credential path does not exist: {missing}")
