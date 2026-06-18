"""Execute parsed v0/v1 manifests against JSON HTTP endpoints."""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from urllib.parse import urlsplit

import httpx

from conformance.api.auth_session_store import (
    AuthSession,
    AuthSessionAlreadyResolvedError,
    AuthSessionLimitError,
    AuthSessionStore,
    DuplicateAuthSessionError,
    InvalidAuthSessionStateError,
    UnknownAuthSessionError,
)
from conformance.approved_releases import ApprovedReleasePolicy
from conformance.assertions import AssertionResult, evaluate_assertion
from conformance.auth_metadata import (
    AuthBundleDeclaration,
    AuthBundleError,
    AuthBundleInventory,
    AuthStepRequirement,
    validate_inventory,
)
from conformance.context import (
    ExecutionContext,
    MissingPredecessorResponseError,
    PlaceholderResolutionError,
    RequestRecord,
    ResponseRecord,
    RuntimeConfig,
    record_step,
    record_token,
    resolve_in_structure,
    resolve_placeholders,
)
from conformance.environment_capabilities import (
    CapabilityEvaluation,
    EnvironmentReference,
    SuiteEnvironmentCapability,
    evaluate_suite_environment_support,
    list_environment_presets,
    make_custom_environment_reference,
    make_preset_environment_reference,
)
from conformance.execution_log import ExecutionLogger, NullExecutionLogger, is_developer_mode_enabled, new_run_id
from conformance.execution_schedule import ExecutionGroup, build_execution_schedule
from conformance.http import JsonHttpClientError, JsonHttpResponse, send_json
from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import (
    FormBody,
    GeneratedRequestObject,
    JsonBody,
    Manifest,
    ManifestAssertion,
    ManifestError,
    ManifestRequest,
    ManifestStep,
    ManifestTest,
    PsuAuthorizationStep,
    TestValueProfileSpec,
    V1Step,
    validate_header_value,
)
from conformance.masking import (
    MASKED_VALUE,
    SENSITIVE_JSON_KEYS,
    mask_form_fields,
    mask_headers,
    mask_json_value,
    mask_url_query,
)
from conformance.model_bank_config import FapiSigningConfig, OpenBankingConfig
from conformance.psu_authorization import (
    build_authorization_url,
    extract_redirect_parameters,
    redirect_matches_registered_uri,
    synthesize_psu_response,
)
from conformance.results import SmokeCheckResult, StepResult, build_smoke_check_result
from conformance.signing_credentials import SigningCredentialError, load_signing_credentials
from conformance.signing_service import (
    ClientAssertionSigningInput,
    FapiSigningService,
    JwtSigningError,
    RequestObjectSigningInput,
)
from conformance.suite_catalog import SuiteMetadata
from conformance.test_plan import TestPlan
from conformance.url_validation import HttpsUrlValidationError, validate_https_url, validate_oauth_redirect_uri

_MAX_EXECUTION_GROUP_WORKERS = 32
"""Upper bound for concurrent v1 execution-group workers.

The manifest is participant-provided input, so group count can be large. This
cap prevents unbounded thread creation while preserving queue-based execution
for additional groups.
"""

_OB_ACCOUNT_ACCESS_CONSENTS_PATH = "/open-banking/v4.0/aisp/account-access-consents"
"""Open Banking AIS consent-creation path requiring detached JWS support."""

_OB_DETACHED_JWS_ALLOWED_WRITE_PATH_SUFFIXES = frozenset(
    {
        "/aisp/account-access-consents",
        "/pisp/domestic-payment-consents",
        "/pisp/domestic-payments",
        "/pisp/domestic-scheduled-payment-consents",
        "/pisp/domestic-scheduled-payments",
        "/pisp/domestic-standing-order-consents",
        "/pisp/domestic-standing-orders",
        "/pisp/international-payment-consents",
        "/pisp/international-payments",
        "/pisp/international-scheduled-payment-consents",
        "/pisp/international-scheduled-payments",
    }
)
"""Open Banking write endpoint suffixes that may carry detached JWS signatures."""


def _normalize_url_path_for_match(path: str) -> str:
    """Normalize a URL path for endpoint eligibility checks.

    Args:
        path: Raw parsed URL path component.

    Returns:
        A canonical absolute path with repeated separators collapsed and any
        trailing slash removed, except for the root path.
    """
    normalized_segments = [segment for segment in path.split("/") if segment]
    if not normalized_segments:
        return "/"
    return "/" + "/".join(normalized_segments)


def _extract_ob_versioned_path_suffix(path: str) -> str | None:
    """Extract the endpoint suffix from an Open Banking versioned URL path.

    Args:
        path: Normalized absolute URL path.

    Returns:
        The endpoint suffix beginning after ``/open-banking/<version>`` when
        the input is a versioned Open Banking path, otherwise ``None``.
    """
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) < 3:
        return None
    if segments[0] != "open-banking":
        return None
    version_segments = segments[1].split(".")
    if len(version_segments) not in {2, 3}:
        return None
    if not version_segments[0].startswith("v"):
        return None
    numeric_parts = [version_segments[0][1:], *version_segments[1:]]
    if not all(part.isdigit() for part in numeric_parts):
        return None
    return "/" + "/".join(segments[2:])


def _attach_evidence(
    step: StepResult,
    *,
    request_evidence: dict[str, JsonValue],
    response_evidence: dict[str, JsonValue] | None,
) -> StepResult:
    """Attach masked request/response evidence to a step result.

    Implements the result-evidence policy: executed HTTP steps may include
    request/response evidence when available, while sensitive credential fields
    and headers remain masked via :mod:`conformance.masking` before embedding.
    Pre-request failures and skipped steps may only include request evidence
    because no response exists.

    Args:
        step: The step result to enrich.
        request_evidence: Best-effort, already-masked request metadata
            collected up to the point of return (always at minimum
            ``method`` and ``url``; may include ``headers``, ``body``,
            ``form``).
        response_evidence: Already-masked response metadata, or ``None`` when
            no response was received (transport failure, skipped step, or a
            pre-request validation error).

    Returns:
        A new :class:`StepResult` with ``details["request"]`` and, when
        available, ``details["response"]`` populated.
    """
    new_details: dict[str, JsonValue] = dict(step.details)
    new_details["request"] = dict(request_evidence)
    if response_evidence is not None:
        new_details["response"] = dict(response_evidence)
    return replace(step, details=new_details)


def _mask_result_headers(headers: Mapping[str, str]) -> JsonObject:
    """Return result-evidence headers masked unless developer mode is enabled.

    Args:
        headers: Header mapping captured for result evidence.

    Returns:
        Masked headers in normal mode, or an unmodified shallow copy in
        developer mode.
    """
    if is_developer_mode_enabled():
        return cast("JsonObject", dict(headers))
    return cast("JsonObject", mask_headers(headers))


def _mask_result_json_value(value: JsonValue) -> JsonValue:
    """Return result-evidence JSON masked unless developer mode is enabled.

    Args:
        value: JSON value captured for result evidence.

    Returns:
        Masked value in normal mode, or a deep-copied unmasked value in
        developer mode.
    """
    if is_developer_mode_enabled():
        return cast("JsonValue", json.loads(json.dumps(value)))
    return mask_json_value(value)


def _mask_result_form_fields(fields: Mapping[str, str]) -> JsonObject:
    """Return result-evidence form fields masked unless developer mode is enabled.

    Args:
        fields: Form field mapping captured for result evidence.

    Returns:
        Masked mapping in normal mode, or an unmodified shallow copy in
        developer mode.
    """
    if is_developer_mode_enabled():
        return cast("JsonObject", dict(fields))
    return cast("JsonObject", dict(mask_form_fields(fields)))


def _mask_result_url_query(url: str) -> str:
    """Return URL query evidence masked unless developer mode is enabled.

    Args:
        url: URL whose query string may contain sensitive OAuth values.

    Returns:
        Original URL in developer mode; otherwise URL with sensitive query
        parameters masked.
    """
    if is_developer_mode_enabled():
        return url
    return mask_url_query(url, SENSITIVE_JSON_KEYS)


def _resolve_environment_reference(
    *,
    environment: str,
    runtime_config: RuntimeConfig | None,
) -> EnvironmentReference:
    """Resolve the safest available environment capability reference.

    Prefers known preset metadata when the run environment/discovery URL matches
    a bundled preset declaration; otherwise falls back to a custom reference
    with no declaration so capability evaluation remains conservative.

    Args:
        environment: Run environment label supplied to :func:`run_manifest`.
        runtime_config: Optional runtime config carrying discovery URL and
            environment values.

    Returns:
        Environment reference for capability evaluation.
    """
    discovery_url = runtime_config.discovery_url if runtime_config is not None else None
    for preset in list_environment_presets():
        if environment == preset.environment or (discovery_url is not None and discovery_url == preset.discovery_url):
            preset_reference = make_preset_environment_reference(preset.key)
            if preset_reference is not None:
                return preset_reference
    return make_custom_environment_reference(label=environment)


def _serialize_auth_bundle(bundle: AuthBundleDeclaration) -> JsonObject:
    """Convert one auth-bundle declaration to participant-safe JSON evidence.

    Args:
        bundle: Auth bundle declaration to serialise.

    Returns:
        Non-secret JSON object carrying durable auth bundle metadata.
    """
    serialised: JsonObject = {
        "id": bundle.id,
        "tokenStepId": bundle.token_step_id,
        "requiredScopes": list(bundle.required_scopes),
        "requiredObPermissions": list(bundle.required_ob_permissions),
        "excludedObPermissions": list(bundle.excluded_ob_permissions),
        "consumingStepIds": list(bundle.consuming_step_ids),
        "capabilityRefs": list(bundle.capability_refs),
    }
    if bundle.consent_step_id is not None:
        serialised["consentStepId"] = bundle.consent_step_id
    if bundle.psu_step_id is not None:
        serialised["psuStepId"] = bundle.psu_step_id
    if bundle.token_endpoint_auth_method is not None:
        serialised["tokenEndpointAuthMethod"] = bundle.token_endpoint_auth_method
    return serialised


def _serialize_step_requirement(requirement: AuthStepRequirement) -> JsonObject:
    """Convert one selected step-to-bundle mapping to JSON evidence.

    Args:
        requirement: Step requirement mapping to serialise.

    Returns:
        JSON object containing the selected step id and bundle id.
    """
    return {"stepId": requirement.step_id, "bundleId": requirement.bundle_id}


def _select_inventory_for_steps(
    inventory: AuthBundleInventory,
    *,
    selected_step_ids: frozenset[str],
) -> AuthBundleInventory:
    """Filter auth inventory to selected-plan step mappings and relevant bundles.

    Args:
        inventory: Parsed manifest auth bundle inventory.
        selected_step_ids: Selected/executed step ids for the run plan.

    Returns:
        Inventory containing only selected step requirements and bundles that are
        directly referenced by selected steps.
    """
    if not selected_step_ids:
        return inventory
    selected_requirements = tuple(req for req in inventory.step_requirements if req.step_id in selected_step_ids)
    selected_bundle_ids = {req.bundle_id for req in selected_requirements}
    selected_bundles = tuple(
        bundle
        for bundle in inventory.bundles
        if (
            bundle.id in selected_bundle_ids
            or bundle.token_step_id in selected_step_ids
            or (bundle.consent_step_id is not None and bundle.consent_step_id in selected_step_ids)
            or (bundle.psu_step_id is not None and bundle.psu_step_id in selected_step_ids)
            or any(step_id in selected_step_ids for step_id in bundle.consuming_step_ids)
        )
    )
    return AuthBundleInventory(bundles=selected_bundles, step_requirements=selected_requirements)


def _safe_auth_inventory_for_evidence(
    *,
    manifest: Manifest,
    selected_step_ids: frozenset[str],
) -> AuthBundleInventory | None:
    """Return a validated selected-step auth inventory safe for result/log output.

    Args:
        manifest: Parsed manifest for the current run.
        selected_step_ids: Selected/executed step ids for the run plan.

    Returns:
        Validated selected-step auth inventory, or ``None`` when the manifest
        has no auth metadata or the metadata fails validation.
    """
    if manifest.auth_inventory is None:
        return None
    selected_inventory = _select_inventory_for_steps(manifest.auth_inventory, selected_step_ids=selected_step_ids)
    known_step_ids = frozenset(step.id for step in manifest.steps)
    try:
        validate_inventory(selected_inventory, known_step_ids=known_step_ids)
    except AuthBundleError:
        return None
    return selected_inventory


def _build_auth_metadata_evidence(
    *,
    manifest: Manifest,
    selected_step_ids: frozenset[str],
) -> JsonObject | None:
    """Build non-secret auth metadata evidence for result JSON and execution logs.

    Args:
        manifest: Parsed manifest for the current run.
        selected_step_ids: Selected/executed step ids for the run plan.

    Returns:
        Auth metadata evidence block, or ``None`` when metadata is unavailable
        or unsafe.
    """
    safe_inventory = _safe_auth_inventory_for_evidence(manifest=manifest, selected_step_ids=selected_step_ids)
    if safe_inventory is None:
        return None
    return {
        "bundles": [_serialize_auth_bundle(bundle) for bundle in safe_inventory.bundles],
        "selectedStepRequirements": [
            _serialize_step_requirement(requirement) for requirement in safe_inventory.step_requirements
        ],
    }


def _bundle_psu_mode(
    *,
    bundle: AuthBundleDeclaration,
    manifest: Manifest,
) -> Literal["manual", "headless"] | None:
    """Resolve the PSU mode for one auth bundle from manifest PSU step metadata.

    Args:
        bundle: Auth bundle declaration whose PSU mode is required.
        manifest: Parsed manifest containing step definitions.

    Returns:
        ``manual`` or ``headless`` when the bundle references a PSU step;
        otherwise ``None``.
    """
    if bundle.psu_step_id is None:
        return None
    step_by_id = {step.id: step for step in manifest.steps}
    psu_step = step_by_id.get(bundle.psu_step_id)
    if isinstance(psu_step, PsuAuthorizationStep):
        return psu_step.mode
    return None


def _serialize_suite_capability(capability: SuiteEnvironmentCapability | None) -> JsonObject | None:
    """Convert suite capability metadata into JSON-safe evidence.

    Args:
        capability: Suite capability declaration returned by evaluation.

    Returns:
        JSON object with non-secret capability metadata, or ``None`` when the
        suite key has no capability row.
    """
    if capability is None:
        return None
    return {
        "standard": capability.standard,
        "specVersion": capability.spec_version,
        "api": capability.api,
        "suite": capability.suite,
        "supportedPsuModes": cast("JsonValue", sorted(capability.supported_psu_modes)),
        "supportedTokenEndpointAuthMethods": cast(
            "JsonValue",
            sorted(capability.supported_token_endpoint_auth_methods),
        ),
        "mtlsRequirement": capability.mtls_requirement,
        "fapiSigningRequirement": capability.fapi_signing_requirement,
        "redirectUriRequirement": capability.redirect_uri_requirement,
        "resourceBaseUrlRequirement": capability.resource_base_url_requirement,
    }


def _serialize_capability_evaluation(
    *,
    bundle_id: str | None,
    psu_mode: str | None,
    token_endpoint_auth_method: str | None,
    evaluation: CapabilityEvaluation,
) -> JsonObject:
    """Convert one compatibility evaluation to participant-safe JSON evidence.

    Args:
        bundle_id: Optional auth bundle identifier driving this decision.
        psu_mode: Optional selected PSU mode for this decision.
        token_endpoint_auth_method: Optional selected token-endpoint client-auth
            method for this decision.
        evaluation: Capability evaluation outcome.

    Returns:
        JSON object containing support status, blockers, warnings, selected auth
        inputs, and matched suite capability metadata.
    """
    decision: JsonObject = {
        "support": evaluation.support,
        "blockers": list(evaluation.blockers),
        "warnings": list(evaluation.warnings),
    }
    if bundle_id is not None:
        decision["bundleId"] = bundle_id
    if psu_mode is not None or token_endpoint_auth_method is not None:
        auth_inputs: JsonObject = {}
        if psu_mode is not None:
            auth_inputs["psuMode"] = psu_mode
        if token_endpoint_auth_method is not None:
            auth_inputs["tokenEndpointAuthMethod"] = token_endpoint_auth_method
        decision["authInputs"] = auth_inputs
    suite_capability = _serialize_suite_capability(evaluation.suite_capability)
    if suite_capability is not None:
        decision["suiteCapability"] = suite_capability
    return decision


def _build_environment_capability_evidence(
    *,
    manifest: Manifest,
    suite_metadata: SuiteMetadata | None,
    environment: str,
    runtime_config: RuntimeConfig | None,
    selected_step_ids: frozenset[str],
) -> JsonObject | None:
    """Build suite/environment capability-decision evidence for this run.

    Args:
        manifest: Parsed manifest for the current run.
        suite_metadata: Optional metadata for a config-resolved bundled suite.
        environment: Run environment label.
        runtime_config: Optional runtime config carrying discovery/environment
            values for preset matching.
        selected_step_ids: Selected/executed step ids for the run plan.

    Returns:
        Capability-decision evidence block, or ``None`` when suite metadata is
        unavailable.
    """
    if suite_metadata is None:
        return None
    environment_reference = _resolve_environment_reference(environment=environment, runtime_config=runtime_config)
    suite_selection = suite_metadata.to_suite_selection()
    safe_inventory = _safe_auth_inventory_for_evidence(manifest=manifest, selected_step_ids=selected_step_ids)
    decisions: list[JsonObject] = []
    if safe_inventory is None or not safe_inventory.bundles:
        decisions.append(
            _serialize_capability_evaluation(
                bundle_id=None,
                psu_mode=None,
                token_endpoint_auth_method=None,
                evaluation=evaluate_suite_environment_support(
                    selection=suite_selection,
                    environment=environment_reference,
                ),
            )
        )
    else:
        for bundle in safe_inventory.bundles:
            psu_mode = _bundle_psu_mode(bundle=bundle, manifest=manifest)
            decision = evaluate_suite_environment_support(
                selection=suite_selection,
                environment=environment_reference,
                psu_mode=psu_mode,
                token_endpoint_auth_method=bundle.token_endpoint_auth_method,
            )
            decisions.append(
                _serialize_capability_evaluation(
                    bundle_id=bundle.id,
                    psu_mode=psu_mode,
                    token_endpoint_auth_method=bundle.token_endpoint_auth_method,
                    evaluation=decision,
                )
            )
    return {
        "suiteSelection": {
            "standard": suite_selection.standard,
            "specVersion": suite_selection.spec_version,
            "profile": suite_selection.profile,
            "api": suite_selection.api,
            "suite": suite_selection.suite,
        },
        "environment": {
            "source": environment_reference.source,
            "key": environment_reference.key,
            "label": environment_reference.label,
        },
        "decisions": cast("JsonValue", decisions),
    }


def _collect_declared_test_value_keys(profile_spec: TestValueProfileSpec) -> list[str]:
    """Collect every test-value key declared by manifest profile metadata.

    Args:
        profile_spec: Manifest ``testValueProfiles`` metadata for this run.

    Returns:
        Sorted list containing keys declared in profile literal values,
        profile generated keys, and allow-listed override keys.
    """
    keys: set[str] = set(profile_spec.allowed_override_keys)
    for profile in profile_spec.profiles:
        keys.update(profile.values)
        keys.update(profile.generated_keys)
    return sorted(keys)


def _build_test_value_profile_evidence(
    *,
    manifest: Manifest,
    plan: TestPlan,
    runtime_config: RuntimeConfig | None,
) -> JsonObject | None:
    """Build non-secret test-value profile evidence for results and logs.

    Args:
        manifest: Parsed manifest for the current run.
        plan: Effective execution plan for the current run.
        runtime_config: Optional runtime config carrying effective test values
            and profile-selection metadata.

    Returns:
        Test-value profile evidence block, or ``None`` when the manifest does
        not declare ``testValueProfiles`` metadata.
    """
    profile_spec = manifest.test_value_profiles
    if profile_spec is None:
        return None

    profile_id: str = profile_spec.default_profile_id
    profile_source: str = "default"
    override_keys: list[str] = []
    for entry in plan.entries:
        if entry.test_value_profile_id is not None:
            profile_id = entry.test_value_profile_id
        if entry.test_value_profile_source is not None:
            profile_source = entry.test_value_profile_source
        if entry.test_value_override_keys:
            override_keys = list(entry.test_value_override_keys)
        if (
            entry.test_value_profile_id is not None
            or entry.test_value_profile_source is not None
            or entry.test_value_override_keys
        ):
            break

    if runtime_config is not None:
        if runtime_config.test_value_profile_id is not None:
            profile_id = runtime_config.test_value_profile_id
        if runtime_config.test_value_profile_source is not None:
            profile_source = runtime_config.test_value_profile_source
        if runtime_config.test_value_override_keys:
            override_keys = list(runtime_config.test_value_override_keys)

    if profile_id != profile_spec.default_profile_id or override_keys:
        profile_source = "overridden"

    condition_outcomes: list[JsonObject] = []
    required_keys_set: set[str] = set()
    for entry in plan.entries:
        required_keys_set.update(entry.required_test_value_keys)
        if not entry.conditional:
            continue
        outcome: JsonObject = {
            "stepId": entry.step_id,
            "selected": entry.selected,
            "requiredKeys": list(entry.required_test_value_keys),
            "missingKeys": list(entry.missing_test_value_keys),
            "allRequiredValuesPresent": not entry.missing_test_value_keys,
        }
        if entry.condition_id is not None:
            outcome["conditionId"] = entry.condition_id
        if entry.condition_label is not None:
            outcome["conditionLabel"] = entry.condition_label
        condition_outcomes.append(outcome)

    effective_values: JsonObject = {}
    available_values = runtime_config.test_values if runtime_config is not None else {}
    for key in sorted(profile_spec.non_secret_keys):
        if key in available_values:
            effective_values[key] = MASKED_VALUE

    return {
        "profileId": profile_id,
        "source": profile_source,
        "overrideKeys": cast("JsonValue", override_keys),
        "declaredKeys": cast("JsonValue", _collect_declared_test_value_keys(profile_spec)),
        "requiredKeys": cast("JsonValue", sorted(required_keys_set)),
        "conditionOutcomes": cast("JsonValue", condition_outcomes),
        "effectiveValues": effective_values,
    }


def run_manifest(
    manifest: Manifest,
    *,
    environment: str,
    client: httpx.Client,
    execution_logger: ExecutionLogger | None = None,
    plan: TestPlan | None = None,
    run_id: str | None = None,
    auth_session_store: AuthSessionStore | None = None,
    runtime_config: RuntimeConfig | None = None,
    fapi_signing_config: FapiSigningConfig | None = None,
    open_banking_config: OpenBankingConfig | None = None,
    mtls_client_configured: bool = False,
    suite_metadata: SuiteMetadata | None = None,
    approved_release_policy: ApprovedReleasePolicy | None = None,
) -> SmokeCheckResult:
    """Run a parsed manifest and return a structured smoke-check result.

    Dispatches to the v0 or v1 execution path based on schema version.
    v0 manifests are internally desugared to v1 sequential steps.

    Args:
        manifest: Parsed and validated manifest to execute.
        environment: Environment name copied into the result file.
        client: Preconfigured synchronous HTTP client used for network requests.
        execution_logger: Optional structured execution-log sink. Defaults to
            a :class:`NullExecutionLogger` for backwards-compatible callers
            that do not want a log.
        plan: Optional :class:`TestPlan` selecting which v1 steps to run.
            When ``None`` (the default) the executor builds the default plan
            from the manifest, which selects every mandatory plus every
            non-optional step — i.e. behaves as before this feature was
            added. Ignored for v0 manifests, which have no plan model.
        run_id: Optional run identifier used to correlate PSU authorisation
            sessions with this execution. When ``None`` (the default), a
            non-empty ``execution_logger.run_id`` is reused when available;
            otherwise a fresh UUID4 hex is generated so legacy callers Just
            Work. The API supplies its own. Consumed by
            ``psu-authorization`` steps; plain HTTP steps ignore it.
        auth_session_store: Optional store the executor uses to register
            and await PSU authorisation callbacks for upcoming
            ``psu-authorization`` steps. When ``None`` (the default) a
            fresh per-call :class:`AuthSessionStore` is constructed so the
            parameter is always non-``None`` inside the executor. Callers
            that need the store to be observable from outside the run
            (e.g. the API, which serves ``/callback/`` against a shared
            singleton) must pass it explicitly.
        runtime_config: Optional safe participant config values available to
            manifest placeholders via the narrow ``${config.*}`` grammar.
        fapi_signing_config: Optional validated FAPI signing configuration
            used only at execution time for generated PSU request-object JWTs.
            Kept separate from ``runtime_config`` so signing keys and paths do
            not become available to manifest placeholders.
        open_banking_config: Optional Open Banking institution metadata used
            to inject ``x-fapi-financial-id`` on outbound Open Banking
            resource-server requests. Kept separate from ``runtime_config`` so
            it does not become available to manifest placeholders.
        mtls_client_configured: Whether the shared HTTP client was configured
            with an mTLS client certificate and private key. Used to fail
            ``tls_client_auth`` token steps clearly before dispatch.
        suite_metadata: Optional catalog metadata describing a config-resolved
            suite run. Omit for explicit manifests and legacy smoke checks.
        approved_release_policy: Optional approved-release policy used by the
            generated report's participant-side certification self-assessment.

    Returns:
        Smoke-check result containing ordered manifest test steps.
    """
    logger_sink: ExecutionLogger = execution_logger or NullExecutionLogger()
    effective_run_id = run_id if run_id is not None else _logger_run_id(logger_sink) or new_run_id()
    effective_store = auth_session_store if auth_session_store is not None else AuthSessionStore()
    effective_plan = plan if plan is not None else TestPlan.default_plan_from_manifest(manifest)
    selected_step_ids = (
        frozenset(effective_plan.selected_step_ids())
        if manifest.schema_version == "v1"
        else frozenset(step.id for step in manifest.steps)
    )
    auth_metadata_evidence = _build_auth_metadata_evidence(manifest=manifest, selected_step_ids=selected_step_ids)
    environment_capability_evidence = _build_environment_capability_evidence(
        manifest=manifest,
        suite_metadata=suite_metadata,
        environment=environment,
        runtime_config=runtime_config,
        selected_step_ids=selected_step_ids,
    )
    test_value_profile_evidence = _build_test_value_profile_evidence(
        manifest=manifest,
        plan=effective_plan,
        runtime_config=runtime_config,
    )
    run_started_payload: JsonObject = {"environment": environment, "schemaVersion": manifest.schema_version}
    if suite_metadata is not None:
        run_started_payload["suite"] = suite_metadata.to_json_object()
    logger_sink.emit("run-started", payload=run_started_payload)
    if auth_metadata_evidence is not None:
        logger_sink.emit("auth-metadata-evaluated", payload=auth_metadata_evidence)
    if environment_capability_evidence is not None:
        logger_sink.emit("environment-capability-evaluated", payload=environment_capability_evidence)
    if test_value_profile_evidence is not None:
        logger_sink.emit("test-value-profile-evaluated", payload=test_value_profile_evidence)
    try:
        if manifest.schema_version == "v1":
            result = _run_manifest_v1(
                manifest,
                environment=environment,
                client=client,
                execution_logger=logger_sink,
                plan=effective_plan,
                run_id=effective_run_id,
                auth_session_store=effective_store,
                runtime_config=runtime_config,
                fapi_signing_config=fapi_signing_config,
                open_banking_config=open_banking_config,
                mtls_client_configured=mtls_client_configured,
                suite_metadata=suite_metadata,
                approved_release_policy=approved_release_policy,
                auth_metadata_evidence=auth_metadata_evidence,
                environment_capability_evidence=environment_capability_evidence,
                test_value_profile_evidence=test_value_profile_evidence,
            )
        else:
            result = _run_manifest_v0(
                manifest,
                environment=environment,
                client=client,
                execution_logger=logger_sink,
                run_id=effective_run_id,
                auth_session_store=effective_store,
                runtime_config=runtime_config,
                suite_metadata=suite_metadata,
                approved_release_policy=approved_release_policy,
                auth_metadata_evidence=auth_metadata_evidence,
                environment_capability_evidence=environment_capability_evidence,
                test_value_profile_evidence=test_value_profile_evidence,
            )
    except Exception as error:
        logger_sink.emit("application-error", payload={"message": str(error)})
        raise
    logger_sink.emit(
        "run-completed",
        payload={
            "status": result.status,
            "summary": {
                "total": len(result.steps),
                "passed": sum(1 for step in result.steps if step.status == "passed"),
                "failed": sum(1 for step in result.steps if step.status == "failed"),
                "warn": sum(1 for step in result.steps if step.status == "warn"),
                "skipped": sum(1 for step in result.steps if step.status == "skipped"),
            },
        },
    )
    return result


def _logger_run_id(execution_logger: ExecutionLogger) -> str | None:
    """Return a run identifier exposed by a stateful execution logger.

    Args:
        execution_logger: Execution-log sink supplied to ``run_manifest``.

    Returns:
        The logger's non-empty run identifier, or ``None`` when the sink does
        not expose one.
    """
    logger_run_id = getattr(execution_logger, "run_id", None)
    return logger_run_id if isinstance(logger_run_id, str) and logger_run_id else None


def _build_fapi_signing_service(
    fapi_signing_config: FapiSigningConfig | None,
) -> FapiSigningService | None:
    """Build a reusable runtime signing service for one manifest run.

    Args:
        fapi_signing_config: Optional validated signing configuration for the
            current participant run.

    Returns:
        One signing service loaded from disk for the run, or ``None`` when
        runtime FAPI signing is not configured.

    Raises:
        SigningCredentialError: If the configured PEM files cannot be read or
            validated.
    """
    if fapi_signing_config is None:
        return None
    return FapiSigningService(
        signing_config=fapi_signing_config,
        signing_credentials=load_signing_credentials(fapi_signing_config),
    )


class _LazyFapiSigningService:
    """Load and cache runtime signing credentials on demand for one run."""

    def __init__(self, fapi_signing_config: FapiSigningConfig | None) -> None:
        """Initialise the lazy signing-service cache.

        Args:
            fapi_signing_config: Optional validated signing configuration for
                the current participant run.
        """
        self._fapi_signing_config = fapi_signing_config
        self._service: FapiSigningService | None = None
        self._loaded = False
        self._lock = threading.Lock()

    def get(self) -> FapiSigningService | None:
        """Return the cached signing service, loading it at most once.

        Returns:
            Cached runtime signing service, or ``None`` when FAPI signing is
            not configured for the current run.

        Raises:
            SigningCredentialError: If the configured PEM files cannot be read
                or validated.
        """
        if self._loaded:
            return self._service

        with self._lock:
            if not self._loaded:
                self._service = _build_fapi_signing_service(self._fapi_signing_config)
                self._loaded = True
        return self._service


def _run_manifest_v1(
    manifest: Manifest,
    *,
    environment: str,
    client: httpx.Client,
    execution_logger: ExecutionLogger,
    plan: TestPlan,
    run_id: str,
    auth_session_store: AuthSessionStore,
    runtime_config: RuntimeConfig | None,
    fapi_signing_config: FapiSigningConfig | None,
    open_banking_config: OpenBankingConfig | None,
    mtls_client_configured: bool,
    suite_metadata: SuiteMetadata | None,
    approved_release_policy: ApprovedReleasePolicy | None,
    auth_metadata_evidence: Mapping[str, JsonValue] | None,
    environment_capability_evidence: Mapping[str, JsonValue] | None,
    test_value_profile_evidence: Mapping[str, JsonValue] | None,
) -> SmokeCheckResult:
    """Execute a v1 manifest with setup first and grouped execution after.

    Each step resolves ``${...}`` placeholders from earlier step responses,
    validates the resolved URL, fetches the endpoint, evaluates assertions,
    and records the result into the execution context for later steps. The
    execution schedule is derived from manifest phase/group metadata plus the
    selected step plan: selected setup steps run first, then execution steps
    run in deterministic group order.

    Only steps whose ids appear in ``plan.selected_step_ids()`` are executed.
    Deselected steps do not run and produce no :class:`StepResult` (they
    are not the same as ``SKIPPED``). A ``step-deselected`` event is emitted
    once per deselected step before any ``step-started`` event, so the log
    preserves a complete record of the plan-vs-manifest delta.

    Args:
        manifest: Parsed v1 manifest containing sequential steps.
        environment: Environment name copied into the result file.
        client: Preconfigured synchronous HTTP client.
        execution_logger: Structured execution-log sink.
        plan: Test plan governing which steps run and which are skipped
            entirely. Must have been derived from this manifest (the
            executor does not re-validate that the plan's step ids match
            the manifest — :meth:`TestPlan.default_plan_from_manifest`
            and :meth:`TestPlan.with_deselection` already enforce that).
        run_id: Run identifier propagated to per-step executors so PSU
            authorisation steps can register sessions against this run.
        auth_session_store: Store the executor uses to register and await
            PSU authorisation callbacks. Threaded to per-step executors.
        runtime_config: Optional safe participant config values available to
            ``${config.*}`` placeholders.
        fapi_signing_config: Optional validated signing configuration used to
            generate runtime FAPI request-object JWTs for PSU steps.
        open_banking_config: Optional Open Banking institution metadata used
            to inject ``x-fapi-financial-id`` on Open Banking resource-server
            requests.
        mtls_client_configured: Whether the shared HTTP client has mTLS
            client credentials configured for ``tls_client_auth`` steps.
        suite_metadata: Optional catalog metadata to embed in the result for
            config-resolved suite runs.
        approved_release_policy: Optional approved-release policy used by the
            generated report's certification self-assessment.
        auth_metadata_evidence: Optional non-secret auth metadata evidence
            emitted for selected steps in this run.
        environment_capability_evidence: Optional non-secret suite/environment
            capability decisions emitted for this run.
        test_value_profile_evidence: Optional non-secret test-value profile
            evidence emitted for this run.

    Returns:
        Smoke-check result with one entry per executed (selected) step.
    """
    started_at = datetime.now(UTC)
    schedule = build_execution_schedule(manifest, plan)
    steps: list[StepResult] = []
    context = ExecutionContext(config=runtime_config)
    fapi_signing_service = _LazyFapiSigningService(fapi_signing_config)

    # Emit one ``step-deselected`` event per deselected step before any
    # ``step-started`` event. Done up-front (rather than interleaved with
    # execution) so a log consumer can read the plan-vs-manifest delta
    # without scanning the entire run.
    for entry in plan.entries:
        if not entry.selected:
            execution_logger.emit(
                "step-deselected",
                step_id=entry.step_id,
                payload={
                    "mandatory": entry.mandatory,
                    "conditional": entry.conditional,
                    "testValueProfileSource": entry.test_value_profile_source,
                    "requiredTestValueKeys": cast("JsonValue", list(entry.required_test_value_keys)),
                    "missingTestValueKeys": cast("JsonValue", list(entry.missing_test_value_keys)),
                    "testValueOverrideKeys": cast("JsonValue", list(entry.test_value_override_keys)),
                },
            )

    setup_steps, context = _execute_v1_step_sequence(
        schedule.setup_steps,
        context=context,
        client=client,
        execution_logger=execution_logger,
        run_id=run_id,
        auth_session_store=auth_session_store,
        fapi_signing_config=fapi_signing_config,
        fapi_signing_service=fapi_signing_service,
        open_banking_config=open_banking_config,
        mtls_client_configured=mtls_client_configured,
    )
    steps.extend(setup_steps)

    execution_steps = _execute_v1_execution_groups_concurrently(
        schedule_execution_groups=schedule.execution_groups,
        setup_context=context,
        manifest=manifest,
        client=client,
        execution_logger=execution_logger,
        run_id=run_id,
        auth_session_store=auth_session_store,
        fapi_signing_config=fapi_signing_config,
        fapi_signing_service=fapi_signing_service,
        open_banking_config=open_banking_config,
        mtls_client_configured=mtls_client_configured,
    )
    steps.extend(execution_steps)

    return build_smoke_check_result(
        environment,
        steps,
        started_at=started_at,
        plan=plan,
        suite_metadata=suite_metadata,
        approved_release_policy=approved_release_policy,
        certification_coverage=manifest.certification_coverage,
        auth_metadata_evidence=auth_metadata_evidence,
        environment_capability_evidence=environment_capability_evidence,
        test_value_profile_evidence=test_value_profile_evidence,
    )


def _execute_v1_step_sequence(
    manifest_steps: tuple[V1Step, ...],
    *,
    context: ExecutionContext,
    client: httpx.Client,
    execution_logger: ExecutionLogger,
    run_id: str,
    auth_session_store: AuthSessionStore,
    fapi_signing_config: FapiSigningConfig | None,
    fapi_signing_service: _LazyFapiSigningService | None,
    open_banking_config: OpenBankingConfig | None,
    mtls_client_configured: bool,
) -> tuple[list[StepResult], ExecutionContext]:
    """Execute an ordered sequence of selected v1 steps.

    Args:
        manifest_steps: Selected v1 steps to execute in manifest order.
        context: Current execution context with earlier step responses.
        client: Preconfigured synchronous HTTP client.
        execution_logger: Structured execution-log sink.
        run_id: Run identifier propagated to PSU authorisation steps.
        auth_session_store: Store used by PSU authorisation steps to
            register and await callbacks.
        fapi_signing_config: Optional validated signing configuration used by
            PSU steps that generate request-object JWTs at runtime.
        fapi_signing_service: Optional lazy runtime signing-service cache
            shared across selected steps in the current manifest run.
        open_banking_config: Optional Open Banking institution metadata used
            to inject ``x-fapi-financial-id`` on Open Banking resource-server
            requests.
        mtls_client_configured: Whether the shared HTTP client has mTLS
            client credentials configured for ``tls_client_auth`` steps.

    Returns:
        Ordered step results and the updated execution context after the
        final step in the sequence.
    """
    steps: list[StepResult] = []
    for manifest_step in manifest_steps:
        step_result, context = _execute_v1_manifest_step(
            manifest_step,
            context=context,
            client=client,
            execution_logger=execution_logger,
            run_id=run_id,
            auth_session_store=auth_session_store,
            fapi_signing_config=fapi_signing_config,
            fapi_signing_service=fapi_signing_service,
            open_banking_config=open_banking_config,
            mtls_client_configured=mtls_client_configured,
        )
        steps.append(step_result)
    return steps, context


def _execute_v1_execution_groups_concurrently(
    *,
    schedule_execution_groups: tuple[ExecutionGroup, ...],
    setup_context: ExecutionContext,
    manifest: Manifest,
    client: httpx.Client,
    execution_logger: ExecutionLogger,
    run_id: str,
    auth_session_store: AuthSessionStore,
    fapi_signing_config: FapiSigningConfig | None,
    fapi_signing_service: _LazyFapiSigningService | None,
    open_banking_config: OpenBankingConfig | None,
    mtls_client_configured: bool,
) -> list[StepResult]:
    """Execute execution-phase groups concurrently and merge deterministically.

    Each group starts from the same post-setup execution context and runs its
    own steps sequentially. Groups do not share mutable context; this keeps
    independent groups isolated while still allowing true overlap.

    Args:
        schedule_execution_groups: Ordered execution groups from the schedule.
        setup_context: Context after all selected setup steps completed.
        manifest: Parsed v1 manifest used to recover manifest-order indices.
        client: Preconfigured synchronous HTTP client.
        execution_logger: Structured execution-log sink.
        run_id: Run identifier propagated to PSU authorisation steps.
        auth_session_store: Store used by PSU authorisation steps.
        fapi_signing_config: Optional validated signing configuration used by
            PSU steps that generate request-object JWTs at runtime.
        fapi_signing_service: Optional lazy runtime signing-service cache
            shared across selected steps in the current manifest run.
        open_banking_config: Optional Open Banking institution metadata used
            to inject ``x-fapi-financial-id`` on Open Banking resource-server
            requests.
        mtls_client_configured: Whether the shared HTTP client has mTLS
            client credentials configured for ``tls_client_auth`` steps.

    Returns:
        Executed step results sorted by original manifest order.
    """
    if not schedule_execution_groups:
        return []

    max_workers = min(len(schedule_execution_groups), _MAX_EXECUTION_GROUP_WORKERS)
    ordered_futures: list[Future[list[StepResult]]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for execution_group in schedule_execution_groups:
            ordered_futures.append(
                executor.submit(
                    _execute_v1_group,
                    execution_group.steps,
                    setup_context,
                    client,
                    execution_logger,
                    run_id,
                    auth_session_store,
                    fapi_signing_config,
                    fapi_signing_service,
                    open_banking_config,
                    mtls_client_configured,
                )
            )

    completed_steps: list[StepResult] = []
    for future in ordered_futures:
        completed_steps.extend(future.result())

    manifest_order = {step.id: index for index, step in enumerate(manifest.steps)}
    completed_steps.sort(key=lambda step: manifest_order.get(step.name, len(manifest_order)))
    return completed_steps


def _execute_v1_group(
    manifest_steps: tuple[V1Step, ...],
    setup_context: ExecutionContext,
    client: httpx.Client,
    execution_logger: ExecutionLogger,
    run_id: str,
    auth_session_store: AuthSessionStore,
    fapi_signing_config: FapiSigningConfig | None,
    fapi_signing_service: _LazyFapiSigningService | None,
    open_banking_config: OpenBankingConfig | None,
    mtls_client_configured: bool,
) -> list[StepResult]:
    """Run one execution group sequentially from the shared setup context.

    Args:
        manifest_steps: Selected steps in one execution group.
        setup_context: Post-setup context snapshot shared by all groups.
        client: Preconfigured synchronous HTTP client.
        execution_logger: Structured execution-log sink.
        run_id: Run identifier propagated to PSU authorisation steps.
        auth_session_store: Store used by PSU authorisation steps.
        fapi_signing_config: Optional validated signing configuration used by
            PSU steps that generate request-object JWTs at runtime.
        fapi_signing_service: Optional lazy runtime signing-service cache
            shared across selected steps in the current manifest run.
        open_banking_config: Optional Open Banking institution metadata used
            to inject ``x-fapi-financial-id`` on Open Banking resource-server
            requests.
        mtls_client_configured: Whether the shared HTTP client has mTLS
            client credentials configured for ``tls_client_auth`` steps.

    Returns:
        Step results for this group in group-local order.
    """
    group_steps, _ = _execute_v1_step_sequence(
        manifest_steps,
        context=setup_context,
        client=client,
        execution_logger=execution_logger,
        run_id=run_id,
        auth_session_store=auth_session_store,
        fapi_signing_config=fapi_signing_config,
        fapi_signing_service=fapi_signing_service,
        open_banking_config=open_banking_config,
        mtls_client_configured=mtls_client_configured,
    )
    return group_steps


def _execute_v1_manifest_step(
    manifest_step: V1Step,
    *,
    context: ExecutionContext,
    client: httpx.Client,
    execution_logger: ExecutionLogger,
    run_id: str,
    auth_session_store: AuthSessionStore,
    fapi_signing_config: FapiSigningConfig | None,
    fapi_signing_service: _LazyFapiSigningService | None,
    open_banking_config: OpenBankingConfig | None,
    mtls_client_configured: bool,
) -> tuple[StepResult, ExecutionContext]:
    """Execute one selected v1 step and preserve mandatory metadata.

    Args:
        manifest_step: Parsed v1 step to execute.
        context: Current execution context with earlier step responses.
        client: Preconfigured synchronous HTTP client.
        execution_logger: Structured execution-log sink.
        run_id: Run identifier propagated to PSU authorisation steps.
        auth_session_store: Store used by PSU authorisation steps to
            register and await callbacks.
        fapi_signing_config: Optional validated signing configuration used by
            PSU steps that generate request-object JWTs at runtime.
        fapi_signing_service: Optional lazy runtime signing-service cache
            shared across selected steps in the current manifest run.
        open_banking_config: Optional Open Banking institution metadata used
            to inject ``x-fapi-financial-id`` on Open Banking resource-server
            requests.
        mtls_client_configured: Whether the shared HTTP client has mTLS
            client credentials configured for ``tls_client_auth`` steps.

    Returns:
        A tuple of the step result and the updated execution context.
    """
    if isinstance(manifest_step, PsuAuthorizationStep):
        step_result, new_context = _execute_v1_psu_step(
            manifest_step,
            context=context,
            client=client,
            run_id=run_id,
            auth_session_store=auth_session_store,
            execution_logger=execution_logger,
            fapi_signing_config=fapi_signing_config,
            fapi_signing_service=fapi_signing_service,
        )
    else:
        step_result, new_context = _execute_v1_step(
            manifest_step,
            context=context,
            client=client,
            execution_logger=execution_logger,
            run_id=run_id,
            auth_session_store=auth_session_store,
            fapi_signing_config=fapi_signing_config,
            fapi_signing_service=fapi_signing_service,
            open_banking_config=open_banking_config,
            mtls_client_configured=mtls_client_configured,
        )
    if manifest_step.mandatory:
        step_result = replace(step_result, mandatory=True)
    return step_result, new_context


def _apply_token_endpoint_auth_policy(
    *,
    manifest_step: ManifestStep,
    resolved_form_body: dict[str, str] | None,
    fapi_signing_config: FapiSigningConfig | None,
    fapi_signing_service: _LazyFapiSigningService | None,
    resolved_url: str,
    mtls_client_configured: bool,
) -> dict[str, str] | None:
    """Apply runtime FAPI token-endpoint auth to a resolved form request.

    Args:
        manifest_step: Parsed HTTP step that may declare token-endpoint auth.
        resolved_form_body: Placeholder-resolved form fields, or ``None`` when
            the step is not form-encoded.
        fapi_signing_config: Optional validated FAPI signing configuration.
        fapi_signing_service: Optional lazy runtime signing-service cache.
        resolved_url: Placeholder-resolved token endpoint URL.
        mtls_client_configured: Whether the shared HTTP client has mTLS
            client credentials configured.

    Returns:
        Final form field mapping to dispatch, or ``None`` when the step has no
        form body and no auth policy.

    Raises:
        SigningCredentialError: If runtime signing credentials cannot be read
            or validated.
        JwtSigningError: If the private-key JWT client assertion cannot be
            signed.
        ValueError: If the policy is unsupported or cannot be satisfied.
    """
    if manifest_step.token_endpoint_auth_policy is None:
        return None if resolved_form_body is None else dict(resolved_form_body)

    if manifest_step.token_endpoint_auth_policy.source != "fapi-signing":
        raise ValueError("Unsupported token endpoint auth policy source")
    if resolved_form_body is None:
        raise ValueError("Token endpoint auth policy requires a form-encoded request body")
    if fapi_signing_config is None:
        raise ValueError("Token endpoint auth policy requires participant fapiSigning config")

    if (
        fapi_signing_config.token_endpoint_auth_method == "private_key_jwt"  # noqa: S105 - FAPI auth-method enum, not a secret
    ):
        conflicting_fields = [
            field_name
            for field_name in ("client_assertion", "client_assertion_type")
            if field_name in resolved_form_body
        ]
        if conflicting_fields:
            reserved_fields = ", ".join(conflicting_fields)
            raise ValueError(
                f"Token endpoint auth policy reserves these form fields for runtime FAPI signing: {reserved_fields}"
            )
        if fapi_signing_service is None:
            raise ValueError("Token endpoint auth policy requires participant fapiSigning config")
        signing_service = fapi_signing_service.get()
        if signing_service is None:
            raise ValueError("Token endpoint auth policy requires participant fapiSigning config")
        client_assertion = signing_service.sign_client_assertion(ClientAssertionSigningInput(audience=resolved_url))
        authenticated_form_body = dict(resolved_form_body)
        authenticated_form_body["client_assertion_type"] = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        authenticated_form_body["client_assertion"] = client_assertion.token
        return authenticated_form_body

    if fapi_signing_config.token_endpoint_auth_method == "tls_client_auth":  # noqa: S105 - FAPI auth-method enum, not a secret
        if not mtls_client_configured:
            raise ValueError("Token endpoint auth policy requires a configured TLS client certificate and private key")
        return dict(resolved_form_body)

    raise ValueError("Unsupported FAPI token endpoint auth method")


def _execute_v1_psu_step(
    manifest_step: PsuAuthorizationStep,
    *,
    context: ExecutionContext,
    client: httpx.Client,
    run_id: str,
    auth_session_store: AuthSessionStore,
    execution_logger: ExecutionLogger,
    fapi_signing_config: FapiSigningConfig | None = None,
    fapi_signing_service: _LazyFapiSigningService | None = None,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> tuple[StepResult, ExecutionContext]:
    """Execute a PSU authorisation step and record captured code context.

    Args:
        manifest_step: Parsed PSU authorisation step to execute.
        context: Current execution context with earlier step records.
        client: Preconfigured synchronous HTTP client. Used by headless mode
            to issue the authorisation request with redirect following disabled.
        run_id: Run identifier used to scope the auth-session registration.
        auth_session_store: Store used to register and poll the session.
        execution_logger: Structured execution-log sink.
        fapi_signing_config: Optional validated signing configuration used to
            generate a runtime request-object JWT when the PSU step requests
            ``{"source": "fapi-signing"}``.
        fapi_signing_service: Optional lazy runtime signing-service cache.
        clock: Optional monotonic clock used for deadline checks. Defaults
            to :func:`time.monotonic`; injectable for tests.
        sleep: Optional sleep function used between polls. Defaults to
            :func:`time.sleep`; injectable for tests.

    Returns:
        A tuple of the PSU step result and updated execution context.
    """
    effective_clock = time.monotonic if clock is None else clock
    effective_sleep = time.sleep if sleep is None else sleep
    effective_fapi_signing_service = (
        fapi_signing_service if fapi_signing_service is not None else _LazyFapiSigningService(fapi_signing_config)
    )
    execution_logger.emit("step-started", step_id=manifest_step.id)
    step_result, new_context = _execute_v1_psu_step_inner(
        manifest_step,
        context=context,
        client=client,
        run_id=run_id,
        auth_session_store=auth_session_store,
        execution_logger=execution_logger,
        fapi_signing_config=fapi_signing_config,
        fapi_signing_service=effective_fapi_signing_service,
        clock=effective_clock,
        sleep=effective_sleep,
    )
    execution_logger.emit(
        "step-completed",
        step_id=manifest_step.id,
        payload={"status": step_result.status, "message": step_result.message},
    )
    return step_result, new_context


def _execute_v1_psu_step_inner(
    manifest_step: PsuAuthorizationStep,
    *,
    context: ExecutionContext,
    client: httpx.Client,
    run_id: str,
    auth_session_store: AuthSessionStore,
    execution_logger: ExecutionLogger,
    fapi_signing_config: FapiSigningConfig | None,
    fapi_signing_service: _LazyFapiSigningService | None,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> tuple[StepResult, ExecutionContext]:
    """Run the PSU authorisation flow (manual or headless) without lifecycle wrapper events.

    Args:
        manifest_step: Parsed PSU authorisation step to execute.
        context: Current execution context with earlier step records.
        client: Preconfigured synchronous HTTP client. Used by headless mode
            to issue the authorisation request with redirect following disabled.
        run_id: Run identifier used to scope the auth-session registration.
        auth_session_store: Store used to register and poll the session.
        execution_logger: Structured execution-log sink.
        fapi_signing_config: Optional validated signing configuration used to
            generate a runtime request-object JWT when required.
        fapi_signing_service: Optional lazy runtime signing-service cache.
        clock: Monotonic clock used for deadline checks.
        sleep: Sleep function used between polls.

    Returns:
        A tuple of the PSU step result and updated execution context.
    """
    initial_result_url = _mask_result_url_query(manifest_step.authorization_endpoint)
    request_evidence: dict[str, JsonValue] = {
        "method": "GET",
        "url": initial_result_url,
    }
    try:
        resolved_authorization_endpoint = resolve_placeholders(manifest_step.authorization_endpoint, context)
        if context.config is not None and context.config.oauth_authorization_endpoint is not None:
            resolved_authorization_endpoint = context.config.oauth_authorization_endpoint
        resolved_client_id = resolve_placeholders(manifest_step.client_id, context)
        resolved_redirect_uri = resolve_placeholders(manifest_step.redirect_uri, context)
        resolved_state = resolve_placeholders(manifest_step.state, context) if manifest_step.state is not None else None
        resolved_nonce = (
            resolve_placeholders(manifest_step.nonce, context)
            if manifest_step.nonce is not None
            else secrets.token_urlsafe(32)
        )
    except MissingPredecessorResponseError as error:
        return _skipped_step(
            manifest_step.id,
            context=context,
            method="GET",
            url=initial_result_url,
            error=error,
            request_evidence=request_evidence,
            execution_logger=execution_logger,
        )
    except PlaceholderResolutionError as error:
        execution_logger.emit(
            "placeholder-error",
            step_id=manifest_step.id,
            payload={"location": "psu-authorization", "message": str(error)},
        )
        request_record = RequestRecord(method="GET", url=initial_result_url)
        new_context = record_step(context, manifest_step.id, request_record, None)
        return (
            _attach_evidence(
                StepResult(
                    name=manifest_step.id,
                    status="failed",
                    message=f"Placeholder resolution failed: {error}",
                    url=initial_result_url,
                ),
                request_evidence=request_evidence,
                response_evidence=None,
            ),
            new_context,
        )

    resolved_result_url = _mask_result_url_query(resolved_authorization_endpoint)
    request_evidence["url"] = resolved_result_url
    try:
        validate_https_url(
            resolved_authorization_endpoint,
            label=f"Step '{manifest_step.id}' authorizationEndpoint",
        )
        validate_oauth_redirect_uri(resolved_redirect_uri, label=f"Step '{manifest_step.id}' redirectUri")
    except HttpsUrlValidationError as error:
        request_record = RequestRecord(method="GET", url=resolved_result_url)
        new_context = record_step(context, manifest_step.id, request_record, None)
        return (
            _attach_evidence(
                StepResult(
                    name=manifest_step.id,
                    status="failed",
                    message=str(error),
                    url=resolved_result_url,
                ),
                request_evidence=request_evidence,
                response_evidence=None,
            ),
            new_context,
        )

    try:
        session = auth_session_store.register(run_id, state=resolved_state)
    except (AuthSessionLimitError, InvalidAuthSessionStateError, DuplicateAuthSessionError) as error:
        request_record = RequestRecord(method="GET", url=resolved_result_url)
        new_context = record_step(context, manifest_step.id, request_record, None)
        return (
            _attach_evidence(
                StepResult(
                    name=manifest_step.id,
                    status="failed",
                    message=f"Unable to register PSU authorisation session: {error}",
                    url=resolved_result_url,
                ),
                request_evidence=request_evidence,
                response_evidence=None,
            ),
            new_context,
        )

    try:
        resolved_request_object = _resolve_psu_request_object(
            manifest_step=manifest_step,
            context=context,
            fapi_signing_config=fapi_signing_config,
            fapi_signing_service=fapi_signing_service,
            authorization_endpoint=resolved_authorization_endpoint,
            client_id=resolved_client_id,
            redirect_uri=resolved_redirect_uri,
            state=session.state,
            nonce=resolved_nonce,
        )
    except MissingPredecessorResponseError as error:
        return _skipped_step(
            manifest_step.id,
            context=context,
            method="GET",
            url=resolved_result_url,
            error=error,
            request_evidence=request_evidence,
            execution_logger=execution_logger,
        )
    except PlaceholderResolutionError as error:
        execution_logger.emit(
            "placeholder-error",
            step_id=manifest_step.id,
            payload={"location": "psu-authorization.requestObject", "message": str(error)},
        )
        request_record = RequestRecord(method="GET", url=resolved_result_url)
        new_context = record_step(context, manifest_step.id, request_record, None)
        return (
            _attach_evidence(
                StepResult(
                    name=manifest_step.id,
                    status="failed",
                    message=f"Placeholder resolution failed: {error}",
                    url=resolved_result_url,
                ),
                request_evidence=request_evidence,
                response_evidence=None,
            ),
            new_context,
        )
    except (SigningCredentialError, JwtSigningError, ValueError) as error:
        request_record = RequestRecord(method="GET", url=resolved_result_url)
        new_context = record_step(context, manifest_step.id, request_record, None)
        return (
            _attach_evidence(
                StepResult(
                    name=manifest_step.id,
                    status="failed",
                    message=f"Unable to build PSU request object: {error}",
                    url=resolved_result_url,
                ),
                request_evidence=request_evidence,
                response_evidence=None,
            ),
            new_context,
        )

    authorization_url = build_authorization_url(
        endpoint=resolved_authorization_endpoint,
        client_id=resolved_client_id,
        redirect_uri=resolved_redirect_uri,
        response_type=manifest_step.response_type,
        scope=manifest_step.scope,
        state=session.state,
        nonce=resolved_nonce,
        request_object=resolved_request_object,
    )
    result_url = _mask_result_url_query(authorization_url)
    request_record = RequestRecord(method="GET", url=result_url)
    request_evidence["url"] = result_url
    # The browser-facing URL is emitted as the raw event payload so the CLI
    # decorator can print it for manual consent; BufferedExecutionLogger masks
    # sensitive query values before persisting or serving the structured log.
    expires_at = datetime.now(UTC) + timedelta(seconds=manifest_step.timeout_seconds)
    execution_logger.emit(
        "psu-authorization-url",
        step_id=manifest_step.id,
        payload={
            "url": authorization_url,
            "client_id": resolved_client_id,
            "request_object": resolved_request_object,
            "state": session.state,
            "nonce": resolved_nonce,
            "mode": manifest_step.mode,
            "timeout_seconds": manifest_step.timeout_seconds,
            "expires_at": expires_at.isoformat(),
        },
    )

    if manifest_step.mode == "headless":
        return _execute_headless_psu_authorization(
            manifest_step,
            context=context,
            client=client,
            run_id=run_id,
            auth_session_store=auth_session_store,
            execution_logger=execution_logger,
            authorization_url=authorization_url,
            result_url=result_url,
            request_record=request_record,
            request_evidence=request_evidence,
            registered_state=session.state,
            redirect_uri=resolved_redirect_uri,
        )

    deadline = clock() + manifest_step.timeout_seconds
    while clock() < deadline:
        sleep(0.5)
        current_session = auth_session_store.get(run_id, session.state)
        if current_session is None or current_session.status == "awaiting":
            continue
        return _complete_psu_step_from_session(
            manifest_step,
            context=context,
            request_record=request_record,
            request_evidence=request_evidence,
            authorization_url=authorization_url,
            result_url=result_url,
            current_session=current_session,
        )

    return (
        _attach_evidence(
            StepResult(
                name=manifest_step.id,
                status="failed",
                message=f"{manifest_step.name} timed out waiting for PSU authorisation callback",
                url=result_url,
                details={"timeoutSeconds": manifest_step.timeout_seconds},
            ),
            request_evidence=request_evidence,
            response_evidence=None,
        ),
        record_step(context, manifest_step.id, request_record, None),
    )


def _resolve_psu_request_object(
    *,
    manifest_step: PsuAuthorizationStep,
    context: ExecutionContext,
    fapi_signing_config: FapiSigningConfig | None,
    fapi_signing_service: _LazyFapiSigningService | None,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    nonce: str,
) -> str | None:
    """Resolve or generate the PSU request object for one authorisation step.

    Args:
        manifest_step: Parsed PSU authorisation step being executed.
        context: Current execution context used for placeholder resolution.
        fapi_signing_config: Optional validated signing configuration for
            runtime-generated request objects.
        fapi_signing_service: Optional lazy runtime signing-service cache.
        authorization_endpoint: Resolved ASPSP authorisation endpoint URL.
        client_id: Resolved OAuth client identifier.
        redirect_uri: Resolved registered redirect URI.
        state: Registered auth-session state that must be embedded into the
            request object when generated.
        nonce: OIDC nonce that must be embedded into a generated request
            object.

    Returns:
        Literal or generated request-object JWT, or ``None`` when the PSU step
        does not declare one.

    Raises:
        MissingPredecessorResponseError: If a string request object depends on
            a prior step response that was unavailable.
        PlaceholderResolutionError: If a string request object contains an
            invalid placeholder reference.
        SigningCredentialError: If runtime signing credentials cannot be read
            or validated.
        JwtSigningError: If the request object cannot be signed.
        ValueError: If the manifest requests runtime signing but no validated
            FAPI signing config was supplied.
    """
    request_object = manifest_step.request_object
    if request_object is None:
        return None
    if isinstance(request_object, str):
        return resolve_placeholders(request_object, context)
    audience = (
        resolve_placeholders(request_object.audience, context)
        if request_object.audience is not None
        else authorization_endpoint
    )
    openbanking_intent_id = (
        resolve_placeholders(request_object.openbanking_intent_id, context)
        if request_object.openbanking_intent_id is not None
        else None
    )
    return _generate_psu_request_object(
        generated_request_object=request_object,
        fapi_signing_config=fapi_signing_config,
        fapi_signing_service=fapi_signing_service,
        authorization_endpoint=authorization_endpoint,
        audience=audience,
        openbanking_intent_id=openbanking_intent_id,
        omit_openbanking_intent_id_claim=(manifest_step.signing_negative_case == "omit-request-object-signature-claim"),
        client_id=client_id,
        redirect_uri=redirect_uri,
        response_type=manifest_step.response_type,
        scope=manifest_step.scope,
        state=state,
        nonce=nonce,
    )


def _generate_psu_request_object(
    *,
    generated_request_object: GeneratedRequestObject,
    fapi_signing_config: FapiSigningConfig | None,
    fapi_signing_service: _LazyFapiSigningService | None,
    authorization_endpoint: str,
    audience: str,
    openbanking_intent_id: str | None,
    omit_openbanking_intent_id_claim: bool,
    client_id: str,
    redirect_uri: str,
    response_type: str,
    scope: str,
    state: str,
    nonce: str,
) -> str:
    """Generate a signed PSU request-object JWT from validated runtime config.

    Args:
        generated_request_object: Typed manifest directive naming the runtime
            request-object source.
        fapi_signing_config: Optional validated signing configuration for the
            current participant run.
        fapi_signing_service: Optional lazy runtime signing-service cache.
        authorization_endpoint: Resolved ASPSP authorisation endpoint URL.
        audience: Resolved request-object JWT ``aud`` claim.
        openbanking_intent_id: Optional Open Banking consent identifier
            resolved from the manifest directive.
        omit_openbanking_intent_id_claim: Whether this step should intentionally
            omit the Open Banking request-object claim for negative signing
            coverage.
        client_id: Resolved OAuth client identifier.
        redirect_uri: Resolved registered redirect URI.
        response_type: Static OAuth response type from the PSU step.
        scope: Static OAuth scope from the PSU step.
        state: Registered auth-session state that must be embedded into the
            request object.
        nonce: OIDC nonce that must be embedded into the request object.

    Returns:
        Compact PS256 JWT ready for the OAuth ``request`` query parameter.

    Raises:
        SigningCredentialError: If runtime signing credentials cannot be read
            or validated.
        JwtSigningError: If the request object cannot be signed.
        ValueError: If the runtime signing directive cannot be satisfied.
    """
    if generated_request_object.source != "fapi-signing":
        raise ValueError("Unsupported PSU requestObject source")
    if fapi_signing_config is None:
        raise ValueError("Generated PSU requestObject requires participant fapiSigning config")
    if fapi_signing_service is None:
        raise ValueError("Generated PSU requestObject requires participant fapiSigning config")

    signing_service = fapi_signing_service.get()
    if signing_service is None:
        raise ValueError("Generated PSU requestObject requires participant fapiSigning config")
    signed_request_object = signing_service.sign_request_object(
        RequestObjectSigningInput(
            issuer=fapi_signing_config.client_assertion_issuer,
            audience=audience,
            client_id=client_id,
            redirect_uri=redirect_uri,
            response_type=response_type,
            scope=scope,
            state=state,
            nonce=nonce,
            openbanking_intent_id=None if omit_openbanking_intent_id_claim else openbanking_intent_id,
        )
    )
    return signed_request_object.token


def _execute_headless_psu_authorization(
    manifest_step: PsuAuthorizationStep,
    *,
    context: ExecutionContext,
    client: httpx.Client,
    run_id: str,
    auth_session_store: AuthSessionStore,
    execution_logger: ExecutionLogger,
    authorization_url: str,
    result_url: str,
    request_record: RequestRecord,
    request_evidence: dict[str, JsonValue],
    registered_state: str,
    redirect_uri: str,
) -> tuple[StepResult, ExecutionContext]:
    """Execute a headless PSU authorisation redirect exchange.

    Args:
        manifest_step: Parsed PSU authorisation step being executed.
        context: Current execution context with earlier step records.
        client: Preconfigured HTTP client used to issue the authorisation
            request. Redirect following is disabled for this call.
        run_id: Run identifier used to scope auth-session lookup.
        auth_session_store: Store containing the registered PSU session.
        execution_logger: Structured execution-log sink.
        authorization_url: Fully built authorisation URL.
        result_url: Masked authorisation URL safe to embed in result files.
        request_record: Request record for the authorisation URL.
        request_evidence: Mutable request evidence attached to failures.
        registered_state: State value registered for this PSU step.
        redirect_uri: Resolved and validated redirect URI expected in the
            ASPSP redirect target.

    Returns:
        A tuple of the PSU step result and updated execution context.
    """
    try:
        response = client.get(authorization_url, follow_redirects=False)
    except httpx.HTTPError as error:
        return (
            _attach_evidence(
                StepResult(
                    name=manifest_step.id,
                    status="failed",
                    message=f"PSU authorisation headless request failed: {error}",
                    url=result_url,
                ),
                request_evidence=request_evidence,
                response_evidence=None,
            ),
            record_step(context, manifest_step.id, request_record, None),
        )

    expected_response = manifest_step.expected_authorization_response
    response_evidence: dict[str, JsonValue] = {"statusCode": response.status_code}
    if not 300 <= response.status_code < 400:
        if expected_response is not None and response.status_code == expected_response.expected:
            return (
                StepResult(
                    name=manifest_step.id,
                    status="passed",
                    message=(
                        f"{manifest_step.name} received expected authorisation endpoint rejection "
                        f"(HTTP {response.status_code})"
                    ),
                    url=result_url,
                    status_code=response.status_code,
                ),
                record_step(context, manifest_step.id, request_record, None),
            )
        return (
            _attach_evidence(
                StepResult(
                    name=manifest_step.id,
                    status="failed",
                    message=(
                        "PSU authorisation headless request did not return a redirect"
                        if expected_response is None
                        else (
                            "PSU authorisation headless request did not match expected "
                            f"HTTP {expected_response.expected} rejection"
                        )
                    ),
                    url=result_url,
                    status_code=response.status_code,
                ),
                request_evidence=request_evidence,
                response_evidence=response_evidence,
            ),
            record_step(context, manifest_step.id, request_record, None),
        )

    location = response.headers.get("Location")
    if location is None:
        return (
            _attach_evidence(
                StepResult(
                    name=manifest_step.id,
                    status="failed",
                    message="PSU authorisation headless redirect was missing a Location header",
                    url=result_url,
                    status_code=response.status_code,
                ),
                request_evidence=request_evidence,
                response_evidence=response_evidence,
            ),
            record_step(context, manifest_step.id, request_record, None),
        )

    if not redirect_matches_registered_uri(location=location, redirect_uri=redirect_uri):
        if _matches_expected_negative_redirect_rejection(
            manifest_step=manifest_step,
            status_code=response.status_code,
        ):
            return (
                StepResult(
                    name=manifest_step.id,
                    status="passed",
                    message=(
                        f"{manifest_step.name} received expected redirect-style authorisation endpoint rejection "
                        f"(HTTP {response.status_code})"
                    ),
                    url=result_url,
                    status_code=response.status_code,
                ),
                record_step(context, manifest_step.id, request_record, None),
            )
        return (
            _attach_evidence(
                StepResult(
                    name=manifest_step.id,
                    status="failed",
                    message="PSU authorisation redirect target did not match the configured redirectUri",
                    url=result_url,
                    status_code=response.status_code,
                ),
                request_evidence=request_evidence,
                response_evidence=response_evidence,
            ),
            record_step(context, manifest_step.id, request_record, None),
        )

    redirect_params = extract_redirect_parameters(location)
    redirect_state = redirect_params.get("state")
    if redirect_state is None or redirect_state != registered_state:
        return (
            _attach_evidence(
                StepResult(
                    name=manifest_step.id,
                    status="failed",
                    message="PSU authorisation redirect state did not match the registered session",
                    url=result_url,
                    status_code=response.status_code,
                ),
                request_evidence=request_evidence,
                response_evidence=response_evidence,
            ),
            record_step(context, manifest_step.id, request_record, None),
        )

    execution_logger.emit(
        "psu-authorization-redirect-received",
        step_id=manifest_step.id,
        payload={"state": redirect_state, "status": response.status_code},
    )

    try:
        if "error" in redirect_params:
            auth_session_store.capture_error(
                redirect_state,
                redirect_params["error"],
                redirect_params.get("error_description"),
            )
        elif "code" in redirect_params and redirect_params["code"]:
            auth_session_store.capture_code(redirect_state, redirect_params["code"])
        else:
            return (
                _attach_evidence(
                    StepResult(
                        name=manifest_step.id,
                        status="failed",
                        message="PSU authorisation redirect did not include a code or error",
                        url=result_url,
                        status_code=response.status_code,
                    ),
                    request_evidence=request_evidence,
                    response_evidence=response_evidence,
                ),
                record_step(context, manifest_step.id, request_record, None),
            )
    except (UnknownAuthSessionError, AuthSessionAlreadyResolvedError) as error:
        return (
            _attach_evidence(
                StepResult(
                    name=manifest_step.id,
                    status="failed",
                    message=f"Unable to capture PSU authorisation redirect: {error}",
                    url=result_url,
                    status_code=response.status_code,
                ),
                request_evidence=request_evidence,
                response_evidence=response_evidence,
            ),
            record_step(context, manifest_step.id, request_record, None),
        )

    current_session = auth_session_store.get(run_id, redirect_state)
    if current_session is None:
        return (
            _attach_evidence(
                StepResult(
                    name=manifest_step.id,
                    status="failed",
                    message="PSU authorisation redirect did not resolve a session for this run",
                    url=result_url,
                    status_code=response.status_code,
                ),
                request_evidence=request_evidence,
                response_evidence=response_evidence,
            ),
            record_step(context, manifest_step.id, request_record, None),
        )

    return _complete_psu_step_from_session(
        manifest_step,
        context=context,
        request_record=request_record,
        request_evidence=request_evidence,
        authorization_url=authorization_url,
        result_url=result_url,
        current_session=current_session,
    )


def _matches_expected_negative_redirect_rejection(*, manifest_step: PsuAuthorizationStep, status_code: int) -> bool:
    """Return whether a redirect-style rejection should satisfy this headless PSU step.

    Args:
        manifest_step: Parsed PSU step being executed.
        status_code: HTTP status returned by the authorisation endpoint.

    Returns:
        ``True`` when the step is the explicit request-object signing-negative
        case with an expected authorisation response and the ASPSP returned an
        HTTP redirect instead of the declared direct 4xx/5xx rejection.
    """
    expected_response = manifest_step.expected_authorization_response
    return (
        expected_response is not None
        and manifest_step.signing_negative_case == "omit-request-object-signature-claim"
        and 300 <= status_code < 400
    )


def _complete_psu_step_from_session(
    manifest_step: PsuAuthorizationStep,
    *,
    context: ExecutionContext,
    request_record: RequestRecord,
    request_evidence: dict[str, JsonValue],
    authorization_url: str,
    result_url: str,
    current_session: AuthSession,
) -> tuple[StepResult, ExecutionContext]:
    """Convert a terminal auth session into a PSU step result.

    Args:
        manifest_step: Parsed PSU authorisation step being completed.
        context: Current execution context with earlier step records.
        request_record: Request record for the authorisation URL.
        request_evidence: Request evidence attached to non-PASS results.
        authorization_url: Fully built authorisation URL.
        result_url: Masked authorisation URL safe to embed in result files.
        current_session: Terminal auth session captured by manual callback
            polling or by the headless redirect parser.

    Returns:
        A tuple of the PSU step result and updated execution context.
    """
    del authorization_url  # Intentionally unused: raw URL must not be persisted into results/log evidence.
    if current_session.status == "captured" and current_session.code is not None:
        response_record = synthesize_psu_response(code=current_session.code, state=current_session.state)
        new_context = record_step(context, manifest_step.id, request_record, response_record)
        return (
            StepResult(
                name=manifest_step.id,
                status="passed",
                message=f"{manifest_step.name} captured authorization code",
                url=result_url,
                status_code=response_record.status_code,
            ),
            new_context,
        )

    error_details: dict[str, JsonValue] = {"error": current_session.error or "authorization_error"}
    if current_session.error_description is not None:
        error_details["error_description"] = current_session.error_description
    return (
        _attach_evidence(
            StepResult(
                name=manifest_step.id,
                status="failed",
                message=f"{manifest_step.name} returned an authorization error",
                url=result_url,
                details=error_details,
            ),
            request_evidence=request_evidence,
            response_evidence=None,
        ),
        record_step(context, manifest_step.id, request_record, None),
    )


def _execute_v1_step(
    manifest_step: ManifestStep,
    *,
    context: ExecutionContext,
    client: httpx.Client,
    execution_logger: ExecutionLogger,
    run_id: str,
    auth_session_store: AuthSessionStore,
    fapi_signing_config: FapiSigningConfig | None = None,
    fapi_signing_service: _LazyFapiSigningService | None = None,
    open_banking_config: OpenBankingConfig | None = None,
    mtls_client_configured: bool = False,
) -> tuple[StepResult, ExecutionContext]:
    """Execute a single v1 manifest step with placeholder resolution.

    Resolves placeholders in the request URL, headers, and body, validates the
    resolved URL, issues the HTTP request, evaluates assertions, and records
    the step into the execution context.

    Args:
        manifest_step: The v1 step to execute.
        context: Current execution context with earlier step records.
        client: Preconfigured synchronous HTTP client.
        execution_logger: Structured execution-log sink. Receives
            ``step-started``, ``request-sent``, ``response-received``,
            ``assertion-evaluated``, ``placeholder-error`` and
            ``step-completed`` events as the step progresses.
        run_id: Run identifier propagated so PSU authorisation steps can
            register sessions against this run. Ignored by plain HTTP
            steps (Phase 1 wiring only).
        auth_session_store: Store used by PSU authorisation steps to
            register and await callbacks. Ignored by plain HTTP steps
            (Phase 1 wiring only).
        fapi_signing_config: Optional validated signing configuration used by
            runtime token-endpoint authentication policies.
        fapi_signing_service: Optional lazy runtime signing-service cache.
        open_banking_config: Optional Open Banking institution metadata used
            to inject ``x-fapi-financial-id`` on Open Banking resource-server
            requests.
        mtls_client_configured: Whether the shared HTTP client has mTLS
            client credentials configured.

    Returns:
        A tuple of the step result and the updated execution context.
    """
    del run_id, auth_session_store  # Plain HTTP steps don't consume these directly.
    effective_fapi_signing_service = (
        fapi_signing_service if fapi_signing_service is not None else _LazyFapiSigningService(fapi_signing_config)
    )
    execution_logger.emit("step-started", step_id=manifest_step.id)
    step_result, new_context = _execute_v1_step_inner(
        manifest_step,
        context=context,
        client=client,
        execution_logger=execution_logger,
        fapi_signing_config=fapi_signing_config,
        fapi_signing_service=effective_fapi_signing_service,
        open_banking_config=open_banking_config,
        mtls_client_configured=mtls_client_configured,
    )
    execution_logger.emit(
        "step-completed",
        step_id=manifest_step.id,
        payload={
            "status": step_result.status,
            "message": step_result.message,
            **({"statusCode": step_result.status_code} if step_result.status_code is not None else {}),
        },
    )
    return step_result, new_context


def _execute_v1_step_inner(
    manifest_step: ManifestStep,
    *,
    context: ExecutionContext,
    client: httpx.Client,
    execution_logger: ExecutionLogger,
    fapi_signing_config: FapiSigningConfig | None,
    fapi_signing_service: _LazyFapiSigningService | None,
    open_banking_config: OpenBankingConfig | None,
    mtls_client_configured: bool,
) -> tuple[StepResult, ExecutionContext]:
    """Inner step executor that emits per-stage events.

    Split from :func:`_execute_v1_step` purely so the outer wrapper can emit
    matched ``step-started`` / ``step-completed`` events without duplicating
    every early-return path.

    Args:
        manifest_step: The v1 step to execute.
        context: Current execution context with earlier step records.
        client: Preconfigured synchronous HTTP client.
        execution_logger: Structured execution-log sink for per-stage events.
        fapi_signing_config: Optional validated signing configuration used by
            runtime token-endpoint authentication policies.
        fapi_signing_service: Optional lazy runtime signing-service cache.
        open_banking_config: Optional Open Banking institution metadata used
            to inject ``x-fapi-financial-id`` on Open Banking resource-server
            requests.
        mtls_client_configured: Whether the shared HTTP client has mTLS
            client credentials configured.

    Returns:
        A tuple of the step result and the updated execution context.
    """
    method = manifest_step.request.method

    # Build up masked request evidence as we resolve each piece, so we can
    # attach the best-available trace to every non-PASS return below. Per
    # the PRD ("masked by default"), tokens/credentials/headers are masked
    # in evidence — only PASS results omit evidence entirely.
    request_evidence: dict[str, JsonValue] = {
        "method": method,
        "url": manifest_step.request.url,
    }

    # Defence-in-depth: reject methods outside the supported set
    _supported_methods = {"GET", "POST", "PUT", "PATCH", "DELETE"}
    if method not in _supported_methods:
        request_record = RequestRecord(method=method, url=manifest_step.request.url)
        new_context = record_step(context, manifest_step.id, request_record, None)
        return (
            _attach_evidence(
                StepResult(
                    name=manifest_step.id,
                    status="failed",
                    message=f"Unsupported request method: {method}",
                    url=manifest_step.request.url,
                ),
                request_evidence=request_evidence,
                response_evidence=None,
            ),
            new_context,
        )

    # Resolve placeholders in the URL
    try:
        resolved_url = resolve_placeholders(manifest_step.request.url, context)
    except MissingPredecessorResponseError as error:
        return _skipped_step(
            manifest_step.id,
            context=context,
            method=method,
            url=manifest_step.request.url,
            error=error,
            request_evidence=request_evidence,
            execution_logger=execution_logger,
        )
    except PlaceholderResolutionError as error:
        execution_logger.emit(
            "placeholder-error",
            step_id=manifest_step.id,
            payload={"location": "url", "message": str(error)},
        )
        request_record = RequestRecord(method=method, url=manifest_step.request.url)
        new_context = record_step(context, manifest_step.id, request_record, None)
        return (
            _attach_evidence(
                StepResult(
                    name=manifest_step.id,
                    status="failed",
                    message=f"Placeholder resolution failed: {error}",
                    url=manifest_step.request.url,
                ),
                request_evidence=request_evidence,
                response_evidence=None,
            ),
            new_context,
        )
    request_evidence["url"] = resolved_url

    # Resolve placeholders in headers
    resolved_headers: dict[str, str] | None = None
    if manifest_step.request.headers is not None:
        try:
            resolved_headers = {
                name: resolve_placeholders(value, context) for name, value in manifest_step.request.headers.items()
            }
        except MissingPredecessorResponseError as error:
            return _skipped_step(
                manifest_step.id,
                context=context,
                method=method,
                url=resolved_url,
                error=error,
                request_evidence=request_evidence,
                execution_logger=execution_logger,
            )
        except PlaceholderResolutionError as error:
            execution_logger.emit(
                "placeholder-error",
                step_id=manifest_step.id,
                payload={"location": "headers", "message": str(error)},
            )
            request_record = RequestRecord(method=method, url=resolved_url)
            new_context = record_step(context, manifest_step.id, request_record, None)
            return (
                _attach_evidence(
                    StepResult(
                        name=manifest_step.id,
                        status="failed",
                        message=f"Placeholder resolution failed: {error}",
                        url=resolved_url,
                    ),
                    request_evidence=request_evidence,
                    response_evidence=None,
                ),
                new_context,
            )

    if resolved_headers is not None:
        request_evidence["headers"] = _mask_result_headers(resolved_headers)

    # Validate resolved header values (post-substitution defence-in-depth)
    if resolved_headers is not None:
        for header_name, header_value in resolved_headers.items():
            try:
                validate_header_value(
                    header_value,
                    location=f"step '{manifest_step.id}' resolved header {header_name}",
                )
            except ManifestError as error:
                request_record = RequestRecord(method=method, url=resolved_url)
                new_context = record_step(context, manifest_step.id, request_record, None)
                return (
                    _attach_evidence(
                        StepResult(
                            name=manifest_step.id,
                            status="failed",
                            message=f"Resolved header validation failed: {error}",
                            url=resolved_url,
                        ),
                        request_evidence=request_evidence,
                        response_evidence=None,
                    ),
                    new_context,
                )

    # Resolve placeholders in body. JsonBody walks the structure recursively;
    # FormBody resolves each field value. Bodies are masked in evidence via
    # ``mask_json_value`` / ``mask_form_fields`` before being attached to the
    # step result so OAuth 2.0 token-exchange credentials (authorization
    # codes, client secrets) never appear in shared reports.
    resolved_json_body: JsonValue | None = None
    resolved_form_body: dict[str, str] | None = None
    if manifest_step.request.body is not None:
        try:
            if isinstance(manifest_step.request.body, JsonBody):
                resolved_json_body = resolve_in_structure(manifest_step.request.body.value, context)
            else:
                # FormBody: resolve each value individually. Names are not
                # templated by design (DL-0014) — only values may carry
                # placeholders.
                form_body: FormBody = manifest_step.request.body
                resolved_form_body = {
                    field_name: resolve_placeholders(field_value, context)
                    for field_name, field_value in form_body.fields.items()
                }
        except MissingPredecessorResponseError as error:
            return _skipped_step(
                manifest_step.id,
                context=context,
                method=method,
                url=resolved_url,
                error=error,
                request_evidence=request_evidence,
                execution_logger=execution_logger,
            )
        except PlaceholderResolutionError as error:
            execution_logger.emit(
                "placeholder-error",
                step_id=manifest_step.id,
                payload={"location": "body", "message": str(error)},
            )
            request_record = RequestRecord(method=method, url=resolved_url)
            new_context = record_step(context, manifest_step.id, request_record, None)
            return (
                _attach_evidence(
                    StepResult(
                        name=manifest_step.id,
                        status="failed",
                        message=f"Placeholder resolution failed: {error}",
                        url=resolved_url,
                    ),
                    request_evidence=request_evidence,
                    response_evidence=None,
                ),
                new_context,
            )

    if resolved_json_body is not None:
        request_evidence["body"] = _mask_result_json_value(resolved_json_body)
    elif resolved_form_body is not None:
        request_evidence["form"] = _mask_result_form_fields(resolved_form_body)

    # Validate resolved URL is HTTPS
    try:
        validate_https_url(resolved_url, label=f"Step '{manifest_step.id}' request URL")
    except HttpsUrlValidationError as error:
        request_record = RequestRecord(method=method, url=resolved_url)
        new_context = record_step(context, manifest_step.id, request_record, None)
        return (
            _attach_evidence(
                StepResult(
                    name=manifest_step.id,
                    status="failed",
                    message=str(error),
                    url=resolved_url,
                ),
                request_evidence=request_evidence,
                response_evidence=None,
            ),
            new_context,
        )

    serialized_json_body: bytes | None = None
    try:
        resolved_headers, serialized_json_body = _maybe_apply_ob_detached_jws(
            manifest_step=manifest_step,
            resolved_url=resolved_url,
            resolved_headers=resolved_headers,
            resolved_json_body=resolved_json_body,
            fapi_signing_config=fapi_signing_config,
            fapi_signing_service=fapi_signing_service,
        )
    except (SigningCredentialError, JwtSigningError, ValueError) as error:
        request_record = RequestRecord(method=method, url=resolved_url)
        new_context = record_step(context, manifest_step.id, request_record, None)
        return (
            _attach_evidence(
                StepResult(
                    name=manifest_step.id,
                    status="failed",
                    message=f"Unable to apply request signing: {error}",
                    url=resolved_url,
                ),
                request_evidence=request_evidence,
                response_evidence=None,
            ),
            new_context,
        )

    # Inject x-fapi-financial-id for Open Banking resource-server requests
    # when the participant config supplies a financialId value.
    # This header is masked by the existing masking layer.
    if open_banking_config is not None and _is_open_banking_resource_request(resolved_url):
        resolved_headers = dict(resolved_headers) if resolved_headers is not None else {}
        resolved_headers["x-fapi-financial-id"] = open_banking_config.financial_id

    if resolved_headers is not None:
        request_evidence["headers"] = _mask_result_headers(resolved_headers)

    try:
        resolved_form_body = _apply_token_endpoint_auth_policy(
            manifest_step=manifest_step,
            resolved_form_body=resolved_form_body,
            fapi_signing_config=fapi_signing_config,
            fapi_signing_service=fapi_signing_service,
            resolved_url=resolved_url,
            mtls_client_configured=mtls_client_configured,
        )
    except (SigningCredentialError, JwtSigningError, ValueError) as error:
        request_record = RequestRecord(method=method, url=resolved_url)
        new_context = record_step(context, manifest_step.id, request_record, None)
        return (
            _attach_evidence(
                StepResult(
                    name=manifest_step.id,
                    status="failed",
                    message=f"Unable to apply token endpoint client authentication: {error}",
                    url=resolved_url,
                ),
                request_evidence=request_evidence,
                response_evidence=None,
            ),
            new_context,
        )

    if resolved_json_body is not None:
        request_evidence["body"] = _mask_result_json_value(resolved_json_body)
    elif resolved_form_body is not None:
        request_evidence["form"] = _mask_result_form_fields(resolved_form_body)

    # Execute HTTP request
    request_record = RequestRecord(method=method, url=resolved_url)
    execution_logger.emit(
        "request-sent",
        step_id=manifest_step.id,
        payload=dict(request_evidence),
    )
    try:
        response = send_json(
            client,
            method,
            resolved_url,
            headers=resolved_headers,
            json_body=None if serialized_json_body is not None else resolved_json_body,
            json_body_bytes=serialized_json_body,
            form_body=resolved_form_body,
        )
    except JsonHttpClientError as error:
        # Preserve the response status code on the StepResult when the
        # failure occurred after a response was received (e.g. non-JSON
        # 4xx body). DL-0011 requires client-error statuses to surface in
        # the structured result so callers can distinguish a 404 from a
        # connection failure.
        new_context = record_step(context, manifest_step.id, request_record, None)
        # When the failure included a response (non-JSON body), include the
        # status code in evidence; the body is unavailable because it failed
        # JSON parsing.
        transport_response_evidence: dict[str, JsonValue] | None = None
        if error.status_code is not None:
            transport_response_evidence = {"statusCode": error.status_code}
            if error.content_type is not None:
                transport_response_evidence["contentType"] = error.content_type
            if error.body_snippet is not None:
                transport_response_evidence["bodySnippet"] = error.body_snippet
        execution_logger.emit(
            "application-error",
            step_id=manifest_step.id,
            payload={
                "message": str(error),
                **({"statusCode": error.status_code} if error.status_code is not None else {}),
                **({"contentType": error.content_type} if error.content_type is not None else {}),
                **({"bodySnippet": error.body_snippet} if error.body_snippet is not None else {}),
            },
        )
        return (
            _attach_evidence(
                StepResult(
                    name=manifest_step.id,
                    status="failed",
                    message=str(error),
                    url=resolved_url,
                    status_code=error.status_code,
                ),
                request_evidence=request_evidence,
                response_evidence=transport_response_evidence,
            ),
            new_context,
        )

    # Record response into context
    response_record = ResponseRecord(
        status_code=response.status_code,
        headers=response.headers,
        body=response.body,
    )
    new_context = record_step(context, manifest_step.id, request_record, response_record)
    new_context = _record_runtime_token_if_present(
        context=new_context,
        manifest_step=manifest_step,
        response_record=response_record,
    )

    # Build masked response evidence for result-file diagnostics while keeping
    # tokens/credentials in the body redacted.
    response_evidence: dict[str, JsonValue] = {
        "statusCode": response.status_code,
        "body": _mask_result_json_value(dict(response.body)),
    }
    if response.headers:
        response_evidence["headers"] = _mask_result_headers(response.headers)
    # Per PRD: response bodies are NOT duplicated into the execution log —
    # they already live in the result-file evidence.
    # The log records only the status code + URL so the timeline is complete
    # without inflating disk usage.
    execution_logger.emit(
        "response-received",
        step_id=manifest_step.id,
        payload={"statusCode": response.status_code, "url": response.url},
    )

    # Evaluate assertions
    step_result = _build_assertion_step(
        name=manifest_step.id,
        success_message=f"{manifest_step.name} passed",
        failure_message=f"{manifest_step.name} failed",
        response=response,
        assertions=manifest_step.assertions,
        warning=manifest_step.warning,
        request_headers=resolved_headers,
        context=context,
    )
    # Emit one assertion-evaluated event per assertion, using the structured
    # results already attached to step_result.details to avoid re-evaluating.
    assertion_entries = step_result.details.get("assertions", [])
    if isinstance(assertion_entries, list):
        for assertion_index, assertion_entry in enumerate(assertion_entries):
            if isinstance(assertion_entry, dict):
                execution_logger.emit(
                    "assertion-evaluated",
                    step_id=manifest_step.id,
                    payload={"index": assertion_index, **assertion_entry},
                )
    return (
        _attach_evidence(step_result, request_evidence=request_evidence, response_evidence=response_evidence),
        new_context,
    )


def _record_runtime_token_if_present(
    *,
    context: ExecutionContext,
    manifest_step: ManifestStep,
    response_record: ResponseRecord,
) -> ExecutionContext:
    """Record a semantic runtime token when a step produces one.

    Args:
        context: Execution context after recording the HTTP step response.
        manifest_step: Executed HTTP step that may declare ``produces_token_id``.
        response_record: Captured response record for the executed step.

    Returns:
        Updated context carrying the semantic token mapping when the step
        declares ``produces_token_id`` and the response body contains a
        non-empty string ``access_token``; otherwise returns ``context``
        unchanged.
    """
    token_id = manifest_step.produces_token_id
    if token_id is None:
        return context
    access_token = response_record.body.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        return context
    return record_token(context, token_id=token_id, access_token=access_token)


def _skipped_step(
    step_id: str,
    *,
    context: ExecutionContext,
    method: str,
    url: str,
    error: MissingPredecessorResponseError,
    request_evidence: dict[str, JsonValue] | None = None,
    execution_logger: ExecutionLogger | None = None,
) -> tuple[StepResult, ExecutionContext]:
    """Build a SKIPPED step result for a step whose prerequisite produced no response.

    Emitted when a ``${steps.<id>.response...}`` placeholder cannot be resolved
    because the referenced step never received a response (transport failure,
    URL validation failure, or earlier placeholder error). Per the PRD,
    SKIPPED — not FAILED — is the correct outcome: the test could not run
    because a prerequisite setup step failed.

    Args:
        step_id: Identifier of the step being skipped.
        context: Current execution context, recorded forward so downstream
            steps that reference *this* step's response will also skip.
        method: HTTP method of the (un-issued) request, recorded for trace.
        url: URL template or partially-resolved URL of the (un-issued) request.
        error: The underlying missing-response error, used for the message.
        request_evidence: Best-effort masked request metadata collected so
            far by the caller. When omitted, a minimal ``{method, url}``
            evidence record is constructed from the arguments.
        execution_logger: Optional sink for a ``placeholder-error`` event
            describing the missing-predecessor failure.

    Returns:
        A ``("skipped", ...)`` step result paired with the updated context.
    """
    if execution_logger is not None:
        execution_logger.emit(
            "placeholder-error",
            step_id=step_id,
            payload={"reason": "missing-predecessor-response", "message": str(error)},
        )
    request_record = RequestRecord(method=method, url=url)
    new_context = record_step(context, step_id, request_record, None)
    evidence: dict[str, JsonValue] = (
        request_evidence if request_evidence is not None else {"method": method, "url": url}
    )
    return (
        _attach_evidence(
            StepResult(
                name=step_id,
                status="skipped",
                message=f"Skipped: {error}",
                url=url,
            ),
            request_evidence=evidence,
            response_evidence=None,
        ),
        new_context,
    )


def _maybe_apply_ob_detached_jws(
    *,
    manifest_step: ManifestStep,
    resolved_url: str,
    resolved_headers: dict[str, str] | None,
    resolved_json_body: JsonValue | None,
    fapi_signing_config: FapiSigningConfig | None,
    fapi_signing_service: _LazyFapiSigningService | None,
) -> tuple[dict[str, str] | None, bytes | None]:
    """Add a detached JWS header for supported Open Banking JSON requests.

    Args:
        manifest_step: Parsed manifest step being executed.
        resolved_url: Fully resolved request URL.
        resolved_headers: Resolved outbound request headers.
        resolved_json_body: Resolved JSON body, if this is a JSON request.
        fapi_signing_config: Optional validated runtime signing configuration.
        fapi_signing_service: Optional lazy runtime signing-service cache.

    Returns:
        Tuple of final outbound headers and serialized JSON body bytes.
        When the step declares ``signingNegativeCase: omit-detached-jws-header``,
        headers are returned unchanged and no detached JWS is produced. When the
        step declares ``signingNegativeCase: omit-jwt-claim``, a compact JWS
        without the Open Banking ``b64``/``crit`` claims is produced and returned
        as a malformed ``x-jws-signature`` header value.

    Raises:
        ValueError: If the manifest opted into detached JWS but runtime
            signing config is missing, the step targets an unsupported URL,
            or the step has no JSON body.
        SigningCredentialError: If runtime signing credentials cannot be read
            or validated.
        JwtSigningError: If the detached JWS cannot be signed.
    """
    if manifest_step.request.detached_jws is None:
        return resolved_headers, None
    if manifest_step.signing_negative_case == "omit-detached-jws-header":
        return resolved_headers, None
    if manifest_step.signing_negative_case == "omit-jwt-claim":
        # Produce a detached JWS with the b64 critical claim intentionally omitted.
        # This represents the OB-400-DOP-100110 negative case where the JWT signature
        # claim is present but malformed — the header is sent but without the required
        # Open Banking b64/crit claims, causing a 400 error-code response.
        if fapi_signing_config is None:
            raise ValueError("omit-jwt-claim signing negative case requires fapiSigning configuration")
        if fapi_signing_service is None:
            raise ValueError("omit-jwt-claim signing negative case requires fapiSigning configuration")
        if not _requires_ob_detached_jws(manifest_step=manifest_step, resolved_url=resolved_url):
            raise ValueError(
                "omit-jwt-claim signing negative case is only supported for allowlisted Open Banking write endpoints"
            )
        if resolved_json_body is None:
            raise ValueError("omit-jwt-claim signing negative case requires a JSON request body")
        serialized_json_body = _serialize_json_request_body(resolved_json_body)
        signing_service = fapi_signing_service.get()
        if signing_service is None:
            raise ValueError("omit-jwt-claim signing negative case requires fapiSigning configuration")
        malformed_signature = signing_service.sign_detached_json_payload_omit_b64_claim(serialized_json_body)
        validate_header_value(
            malformed_signature,
            location=f"step '{manifest_step.id}' generated header x-jws-signature (omit-jwt-claim)",
        )
        malformed_headers = dict(resolved_headers) if resolved_headers is not None else {}
        malformed_headers["x-jws-signature"] = malformed_signature
        return malformed_headers, serialized_json_body
    if fapi_signing_config is None:
        raise ValueError("Detached request signing requires fapiSigning configuration")
    if fapi_signing_service is None:
        raise ValueError("Detached request signing requires fapiSigning configuration")
    if not _requires_ob_detached_jws(manifest_step=manifest_step, resolved_url=resolved_url):
        raise ValueError(
            "Detached request signing is only supported for account-access-consents, "
            "domestic-payment-consents, and domestic-payments write requests"
        )
    if resolved_json_body is None:
        raise ValueError("Detached request signing requires a JSON request body")

    serialized_json_body = _serialize_json_request_body(resolved_json_body)
    signing_service = fapi_signing_service.get()
    if signing_service is None:
        raise ValueError("Detached request signing requires fapiSigning configuration")
    detached_signature = signing_service.sign_detached_json_payload(serialized_json_body)
    validate_header_value(
        detached_signature,
        location=f"step '{manifest_step.id}' generated header x-jws-signature",
    )

    signed_headers = dict(resolved_headers) if resolved_headers is not None else {}
    signed_headers["x-jws-signature"] = detached_signature
    return signed_headers, serialized_json_body


def _requires_ob_detached_jws(*, manifest_step: ManifestStep, resolved_url: str) -> bool:
    """Return whether a step should carry an Open Banking detached JWS.

    Args:
        manifest_step: Parsed manifest step being executed.
        resolved_url: Fully resolved request URL.

    Returns:
        ``True`` when the step targets an allowlisted Open Banking write
        endpoint (AIS account-access-consents or supported PIS consent/payment
        writes) and uses an eligible write method, otherwise ``False``.
    """
    if manifest_step.request.method not in {"POST", "PUT", "PATCH"}:
        return False
    normalized_path = _normalize_url_path_for_match(urlsplit(resolved_url).path)
    if normalized_path == _OB_ACCOUNT_ACCESS_CONSENTS_PATH:
        return True
    versioned_suffix = _extract_ob_versioned_path_suffix(normalized_path)
    if versioned_suffix is None:
        return False
    return versioned_suffix in _OB_DETACHED_JWS_ALLOWED_WRITE_PATH_SUFFIXES


def _is_open_banking_resource_request(resolved_url: str) -> bool:
    """Return whether a URL targets an Open Banking resource endpoint.

    Args:
        resolved_url: Fully resolved request URL.

    Returns:
        ``True`` when the URL path uses the ``/open-banking/v*`` resource
        API shape, otherwise ``False``.
    """
    normalized_path = _normalize_url_path_for_match(urlsplit(resolved_url).path)
    return _extract_ob_versioned_path_suffix(normalized_path) is not None


def _serialize_json_request_body(body: JsonValue) -> bytes:
    """Serialize a resolved JSON request body into exact wire bytes.

    Args:
        body: Resolved JSON value to serialize.

    Returns:
        UTF-8 encoded JSON bytes using a deterministic compact form.
    """
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _run_manifest_v0(
    manifest: Manifest,
    *,
    environment: str,
    client: httpx.Client,
    execution_logger: ExecutionLogger,
    run_id: str,
    auth_session_store: AuthSessionStore,
    runtime_config: RuntimeConfig | None,
    suite_metadata: SuiteMetadata | None,
    approved_release_policy: ApprovedReleasePolicy | None,
    auth_metadata_evidence: Mapping[str, JsonValue] | None,
    environment_capability_evidence: Mapping[str, JsonValue] | None,
    test_value_profile_evidence: Mapping[str, JsonValue] | None,
) -> SmokeCheckResult:
    """Execute a v0 manifest preserving original skip-on-fail semantics.

    In v0, follow-up steps are only executed when the primary step passes.
    This differs from v1 where all steps run regardless of earlier assertion
    outcomes. The method desugars each test into v1 steps but gates follow-up
    execution on primary step success.

    Args:
        manifest: Parsed v0 manifest containing tests with optional followUp.
        environment: Environment name copied into the result file.
        client: Preconfigured synchronous HTTP client.
        execution_logger: Structured execution-log sink threaded through to
            each desugared v1 step.
        run_id: Run identifier propagated through desugared v1 steps. v0
            manifests have no PSU authorisation steps today, but the
            parameter keeps the v0/v1 dispatch surface symmetric.
        auth_session_store: Store propagated through desugared v1 steps,
            mirroring ``run_id`` for the same reason.
        runtime_config: Optional safe participant config values available to
            desugared step placeholder resolution.
        suite_metadata: Optional catalog metadata to embed in the result for
            config-resolved suite runs.
        approved_release_policy: Optional approved-release policy used by the
            generated report's certification self-assessment.
        auth_metadata_evidence: Optional non-secret auth metadata evidence
            emitted for selected steps in this run.
        environment_capability_evidence: Optional non-secret suite/environment
            capability decisions emitted for this run.
        test_value_profile_evidence: Optional non-secret test-value profile
            evidence emitted for this run.

    Returns:
        Smoke-check result with step entries matching v0 naming conventions.
    """
    started_at = datetime.now(UTC)
    steps: list[StepResult] = []
    context = ExecutionContext(config=runtime_config)

    for test in manifest.tests:
        # v0 contract: primary requests are GET-only. _parse_request enforces this
        # at JSON parse time, but ManifestRequest.method is typed as RequestMethod
        # (any of GET/POST/PUT/PATCH/DELETE), so a programmatically constructed
        # ManifestTest could supply a non-GET method. Reject before desugaring
        # through the shared v1 executor, which accepts all five methods.
        if test.request.method != "GET":
            request_record = RequestRecord(method=test.request.method, url=test.request.url)
            context = record_step(context, test.id, request_record, None)
            v0_method_evidence: dict[str, JsonValue] = {"method": test.request.method, "url": test.request.url}
            steps.append(
                _attach_evidence(
                    StepResult(
                        name=test.id,
                        status="failed",
                        message=f"v0 manifest primary requests must use GET, got: {test.request.method}",
                        url=test.request.url,
                    ),
                    request_evidence=v0_method_evidence,
                    response_evidence=None,
                )
            )
            continue

        # Primary step
        primary_step = ManifestStep(
            id=test.id,
            name=test.name,
            request=test.request,
            assertions=test.assertions,
        )
        step_result, context = _execute_v1_step(
            primary_step,
            context=context,
            client=client,
            execution_logger=execution_logger,
            run_id=run_id,
            auth_session_store=auth_session_store,
            fapi_signing_config=None,
            mtls_client_configured=False,
        )
        steps.append(step_result)

        # Follow-up: only execute if primary passed (v0 semantics)
        if step_result.status == "passed" and test.follow_up is not None:
            follow_up_id = f"{test.id}.followUp"
            follow_up_url = _extract_v0_follow_up_url(context, test)
            if follow_up_url is None:
                # Preserve v0 explicit "unable to resolve" failure before attempting any HTTP call.
                primary_url = context.steps[test.id].request.url
                context = record_step(
                    context,
                    follow_up_id,
                    RequestRecord(method=test.follow_up.request.method, url=primary_url),
                    None,
                )
                followup_evidence: dict[str, JsonValue] = {
                    "method": test.follow_up.request.method,
                    "url": primary_url,
                }
                steps.append(
                    _attach_evidence(
                        StepResult(
                            name=follow_up_id,
                            status="failed",
                            message=f"Unable to resolve follow-up URL from {test.follow_up.url_source}",
                            url=primary_url,
                        ),
                        request_evidence=followup_evidence,
                        response_evidence=None,
                    )
                )
            else:
                follow_up_step = ManifestStep(
                    id=follow_up_id,
                    name=f"{test.name} follow-up",
                    request=ManifestRequest(method=test.follow_up.request.method, url=follow_up_url),
                    assertions=test.follow_up.assertions,
                )
                follow_up_result, context = _execute_v1_step(
                    follow_up_step,
                    context=context,
                    client=client,
                    execution_logger=execution_logger,
                    run_id=run_id,
                    auth_session_store=auth_session_store,
                    fapi_signing_config=None,
                    mtls_client_configured=False,
                )
                steps.append(follow_up_result)

    return build_smoke_check_result(
        environment,
        steps,
        started_at=started_at,
        suite_metadata=suite_metadata,
        approved_release_policy=approved_release_policy,
        auth_metadata_evidence=auth_metadata_evidence,
        environment_capability_evidence=environment_capability_evidence,
        test_value_profile_evidence=test_value_profile_evidence,
    )


def _extract_v0_follow_up_url(context: ExecutionContext, test: ManifestTest) -> str | None:
    """Extract the v0 follow-up URL from the primary step's response body.

    Applies original v0 extraction semantics: the resolved value must be a
    non-empty string and is stripped of surrounding whitespace before use.
    Only the ``response.body.jwks_uri`` source path is supported.

    Args:
        context: Execution context containing the primary step's recorded response.
        test: The v0 manifest test whose ``follow_up.url_source`` is resolved.

    Returns:
        The stripped URL string, or ``None`` if the source path is not
        recognised, the key is absent, the value is not a string, or the
        stripped value is empty.
    """
    assert test.follow_up is not None  # noqa: S101 — caller guarantees follow_up exists
    if test.follow_up.url_source != "response.body.jwks_uri":
        return None
    step_record = context.steps.get(test.id)
    if step_record is None or step_record.response is None:
        return None
    value = step_record.response.body.get("jwks_uri")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _build_assertion_step(
    *,
    name: str,
    success_message: str,
    failure_message: str,
    response: JsonHttpResponse,
    assertions: tuple[ManifestAssertion, ...],
    warning: str | None = None,
    request_headers: Mapping[str, str] | None = None,
    context: ExecutionContext | None = None,
) -> StepResult:
    """Build a step result by evaluating all assertions for a response.

    When all assertions pass and the step declared a manifest-level
    ``warning``, the result is promoted to a ``warn`` outcome with the
    warning message surfaced in both the top-level ``message`` and the
    structured ``details``. Per the PRD, ``warn`` signals a deprecation or
    risk to the participant but does not block certification — failing
    assertions still produce ``failed`` regardless of any warning.

    Args:
        name: The step name displayed in the conformance report.
        success_message: Message emitted when all assertions pass.
        failure_message: Message emitted when any assertion fails.
        response: The HTTP response to evaluate assertions against.
        assertions: The manifest assertions to apply to the response.
        warning: Optional deprecation/risk message declared by the manifest
            step. Only applied when all assertions pass.
        request_headers: Resolved outbound request headers for the step.
            Forwarded to the assertion evaluator to support the
            ``matches_request_header`` header rule. Defaults to ``None``;
            existing callers need not change.
        context: Execution context before this step was recorded. Used to
            expose previously fetched JWKS bodies to response-signature
            assertions.

    Returns:
        A completed step result containing the overall pass/fail/warn status
        and per-assertion details.
    """
    assertion_results = tuple(
        evaluate_assertion(
            assertion,
            status_code=response.status_code,
            headers=response.headers,
            body=response.body,
            request_headers=request_headers,
            body_bytes=response.body_bytes,
            response_signature_jwks=_response_signature_jwks_by_step(context),
        )
        for assertion in assertions
    )
    passed = all(assertion_result.passed for assertion_result in assertion_results)
    details: dict[str, JsonValue] = {
        "assertions": [_assertion_result_to_json(assertion_result) for assertion_result in assertion_results],
    }
    if passed and warning is not None:
        details["warning"] = warning
        return StepResult(
            name=name,
            status="warn",
            message=f"{success_message} (warning: {warning})",
            url=response.url,
            status_code=response.status_code,
            details=details,
        )
    return StepResult(
        name=name,
        status="passed" if passed else "failed",
        message=success_message if passed else failure_message,
        url=response.url,
        status_code=response.status_code,
        details=details,
    )


def _response_signature_jwks_by_step(context: ExecutionContext | None) -> Mapping[str, JsonValue] | None:
    """Build a mapping of prior step ids to JSON response bodies for JWS verification.

    Args:
        context: Execution context available before the current step executes.

    Returns:
        Mapping of step ids to response-body JSON objects, or ``None`` when no
        context is available.
    """
    if context is None:
        return None
    jwks_by_step: dict[str, JsonValue] = {}
    for step_id, record in context.steps.items():
        if record.response is not None:
            jwks_by_step[step_id] = dict(record.response.body)
    return jwks_by_step


def _assertion_result_to_json(assertion_result: AssertionResult) -> JsonObject:
    """Convert an assertion result to the step details JSON shape.

    Args:
        assertion_result: Evaluated assertion outcome to serialise.

    Returns:
        JSON-serialisable dictionary with ``status`` and ``message`` keys.
    """
    return {
        "status": "passed" if assertion_result.passed else "failed",
        "message": assertion_result.message,
    }
