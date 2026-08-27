"""Execute parsed v0/v1 manifests against JSON HTTP endpoints."""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import cast
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
from conformance.catalogue import (
    CatalogueAssertion,
    CatalogueRequestStep,
    CatalogueTestCase,
    CompiledTestPlan,
    RuntimeInputRequirement,
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
from conformance.execution_log import ExecutionLogger, NullExecutionLogger, is_developer_mode_enabled, new_run_id
from conformance.execution_schedule import ExecutionGroup, build_execution_schedule
from conformance.http import JsonHttpClientError, JsonHttpResponse, send_json
from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import (
    PSU_AUTHORIZATION_TIMEOUT_SECONDS,
    DetachedJwsPolicy,
    FormBody,
    GeneratedRequestObject,
    HeaderAssertion,
    HttpStatusAssertion,
    JsonBody,
    JsonFieldAssertion,
    JsonFieldRule,
    Manifest,
    ManifestAssertion,
    ManifestError,
    ManifestRequest,
    ManifestStep,
    ManifestTest,
    PsuAuthorizationStep,
    ResponseSchemaAssertion,
    ResponseSignaturePolicy,
    StepPhase,
    TokenEndpointAuthPolicy,
    V1Step,
    validate_header_value,
)
from conformance.masking import SENSITIVE_JSON_KEYS, mask_form_fields, mask_headers, mask_json_value, mask_url_query
from conformance.model_bank_config import FapiSigningConfig
from conformance.psu_authorization import (
    build_authorization_url,
    extract_redirect_parameters,
    redirect_matches_registered_uri,
    synthesize_psu_response,
)
from conformance.response_signature import ResponseSignatureValidationError, validate_ob_response_signature
from conformance.results import SmokeCheckResult, StepResult, build_smoke_check_result
from conformance.signing_credentials import SigningCredentialError, load_signing_credentials
from conformance.signing_service import (
    ClientAssertionSigningInput,
    FapiSigningService,
    JwtSigningError,
    OpenBankingDetachedJwsProfile,
    RequestObjectSigningInput,
)
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

_OB_PIS_PATH_PREFIX = "/open-banking/v4.0/pisp/"
"""Open Banking PIS path prefix for payment-initiation detached JWS support."""

_OB_VRP_RESOURCE_PATH_PREFIXES = ("/domestic-vrp-consents", "/domestic-vrps")
"""Open Banking VRP resource paths requiring detached JWS support."""

_CATALOGUE_PATH_VARIABLE_PATTERN = re.compile(r"(?<!\$)\{([^}]+)\}")
"""Pattern matching OpenAPI-style path variables in catalogue request paths."""

_CATALOGUE_GENERATED_VALUE_PATTERN = re.compile(r"\$\{generated\.([^}]+)\}")
"""Pattern matching catalogue-owned generated runtime values in request templates."""

_CATALOGUE_RUNTIME_VALUE_PATTERN = re.compile(r"\$\{runtime\.([^}]+)\}")
"""Pattern matching plan-sourced runtime values in request templates."""

_CBPII_CLIENT_CREDENTIALS_TOKEN_ID = "cbpii-client-credentials"  # noqa: S105 - semantic token id
"""Semantic token id for CBPII client-credentials protected-resource calls."""

_CBPII_FUNDS_CONFIRMATION_TOKEN_ID = "cbpii-funds-confirmation"  # noqa: S105 - semantic token id
"""Semantic token id for CBPII authorisation-code funds-confirmation calls."""

_CBPII_CONSENT_CREATE_STEP_ID = "cbpii-consent-create-core-request"
"""Catalogue step id whose response supplies the CBPII consent id."""

_CBPII_AUTHORIZATION_STEP_ID = "setup-cbpii-consent-authorisation"
"""Synthetic PSU authorisation step id for CBPII authorised consent setup."""

_CBPII_AUTHORIZATION_TOKEN_STEP_ID = "setup-token-cbpii-funds-confirmation"  # noqa: S105 - step id
"""Synthetic authorisation-code token exchange step id for CBPII funds confirmations."""

_CBPII_AUTHORISED_RESOURCE_STEP_IDS = frozenset(
    {
        "cbpii-consent-get-authorised-request",
        "cbpii-funds-confirmation-create-request",
    }
)
"""CBPII resource steps that require a PSU-authorised funds-confirmation consent."""

_CBPII_CAPTURED_CONSENT_ID = f"${{steps.{_CBPII_CONSENT_CREATE_STEP_ID}.response.body.Data.ConsentId}}"
"""Placeholder resolving to the CBPII consent id created during the run."""

_AIS_CLIENT_CREDENTIALS_TOKEN_ID = "ais-client-credentials"  # noqa: S105 - semantic token id
"""Semantic token id for AIS client-credentials consent-creation calls."""

_AIS_BASIC_ACCOUNT_ACCESS_TOKEN_ID = "ais-account-access-basic"  # noqa: S105 - semantic token id
"""Semantic token id for PSU-authorised AIS calls using basic read permissions."""

_AIS_DETAIL_ACCOUNT_ACCESS_TOKEN_ID = "ais-account-access-detail"  # noqa: S105 - semantic token id
"""Semantic token id for PSU-authorised AIS calls using detail read permissions."""

_AIS_CONSENT_CREATE_STEP_ID = "ais-at-setup-consent-request"
"""Catalogue template step id used to create AIS account-access consent steps."""

_AIS_ACCOUNT_ACCESS_TOKEN_STEP_ID = "ais-at-setup-token-request"  # noqa: S105 - step id
"""Catalogue template step id used to create AIS authorisation-code token steps."""

_AIS_AUTHORIZATION_STEP_ID = "setup-ais-consent-authorisation"
"""Legacy synthetic PSU authorisation step id retained for compatibility."""

_AIS_CAPTURED_CONSENT_ID = f"${{steps.{_AIS_CONSENT_CREATE_STEP_ID}.response.body.Data.ConsentId}}"
"""Legacy AIS consent-id placeholder retained for compatibility."""

_AIS_PERMISSION_PROFILE_TOKEN_IDS = {
    "basic": _AIS_BASIC_ACCOUNT_ACCESS_TOKEN_ID,
    "detail": _AIS_DETAIL_ACCOUNT_ACCESS_TOKEN_ID,
}
"""Semantic token ids keyed by AIS legacy permission profile."""

_AIS_BASIC_ACCOUNT_ACCESS_CONSENT_BODY: JsonObject = {
    "Data": {
        "Permissions": [
            "ReadAccountsBasic",
            "ReadBalances",
            "ReadBeneficiariesBasic",
            "ReadDirectDebits",
            "ReadOffers",
            "ReadParty",
            "ReadPartyPSU",
            "ReadProducts",
            "ReadScheduledPaymentsBasic",
            "ReadStandingOrdersBasic",
            "ReadStatementsBasic",
            "ReadTransactionsBasic",
            "ReadTransactionsCredits",
            "ReadTransactionsDebits",
        ],
    },
    "Risk": {},
}
"""AIS account-access-consent payload matching the legacy basic permission profile."""

_AIS_DETAIL_ACCOUNT_ACCESS_CONSENT_BODY: JsonObject = {
    "Data": {
        "Permissions": [
            "ReadAccountsDetail",
            "ReadBalances",
            "ReadBeneficiariesDetail",
            "ReadDirectDebits",
            "ReadOffers",
            "ReadPAN",
            "ReadParty",
            "ReadPartyPSU",
            "ReadProducts",
            "ReadScheduledPaymentsDetail",
            "ReadStandingOrdersDetail",
            "ReadStatementsDetail",
            "ReadTransactionsCredits",
            "ReadTransactionsDebits",
            "ReadTransactionsDetail",
        ],
    },
    "Risk": {},
}
"""AIS account-access-consent payload matching the legacy detail permission profile."""

_AIS_ACCOUNT_ACCESS_CONSENT_BODIES: Mapping[str, JsonObject] = {
    "basic": _AIS_BASIC_ACCOUNT_ACCESS_CONSENT_BODY,
    "detail": _AIS_DETAIL_ACCOUNT_ACCESS_CONSENT_BODY,
}
"""AIS account-access-consent payloads keyed by legacy permission profile."""

_PIS_MISSING_SIGNATURE_STEP_ID = "pis-v4-domestic-payment-consent-reject-invalid-signature-request"
"""PIS negative test step that must deliberately omit a detached JWS header."""

_PIS_CONSENT_AUTHORIZATION_STEPS = {
    "pis-v4-domestic-payment-consent-create-request": (
        "setup-pis-domestic-payment-consent-authorisation",
        "Authorise domestic payment consent",
        "setup-token-pis-domestic-payment-access",
        "pis-domestic-payment-access",
        "domestic payment",
    ),
    "pis-v4-domestic-scheduled-payment-consent-create-request": (
        "setup-pis-domestic-scheduled-payment-consent-authorisation",
        "Authorise domestic scheduled payment consent",
        "setup-token-pis-domestic-scheduled-payment-access",
        "pis-domestic-scheduled-payment-access",
        "domestic scheduled payment",
    ),
    "pis-v4-domestic-standing-order-consent-create-request": (
        "setup-pis-domestic-standing-order-consent-authorisation",
        "Authorise domestic standing-order consent",
        "setup-token-pis-domestic-standing-order-access",
        "pis-domestic-standing-order-access",
        "domestic standing-order",
    ),
    "pis-v4-international-payment-consent-create-request": (
        "setup-pis-international-payment-consent-authorisation",
        "Authorise international payment consent",
        "setup-token-pis-international-payment-access",
        "pis-international-payment-access",
        "international payment",
    ),
    "pis-v4-international-scheduled-payment-consent-create-request": (
        "setup-pis-international-scheduled-payment-consent-authorisation",
        "Authorise international scheduled payment consent",
        "setup-token-pis-international-scheduled-payment-access",
        "pis-international-scheduled-payment-access",
        "international scheduled payment",
    ),
}
"""Synthetic PSU and token-exchange metadata keyed by PIS consent-creation step."""

_PIS_CONSENT_AUTHORIZATION_DEPENDENT_STEPS = {
    "pis-v4-domestic-payment-consent-create-request": frozenset(
        {
            "pis-v4-domestic-payment-consent-read-authorised-request",
            "pis-v4-domestic-payment-funds-confirmation-request",
            "pis-v4-domestic-payment-create-request",
            "pis-v4-domestic-payment-read-request",
        }
    ),
    "pis-v4-domestic-scheduled-payment-consent-create-request": frozenset(
        {
            "pis-v4-domestic-scheduled-payment-consent-read-request",
            "pis-v4-domestic-scheduled-payment-create-request",
            "pis-v4-domestic-scheduled-payment-read-request",
        }
    ),
    "pis-v4-domestic-standing-order-consent-create-request": frozenset(
        {
            "pis-v4-domestic-standing-order-consent-read-request",
            "pis-v4-domestic-standing-order-create-request",
            "pis-v4-domestic-standing-order-read-request",
            "pis-v4-domestic-standing-order-read-with-number-and-final-date-request",
            "pis-v4-domestic-standing-order-read-with-final-amount-only-request",
            "pis-v4-domestic-standing-order-reject-invalid-frequency-request",
        }
    ),
    "pis-v4-international-payment-consent-create-request": frozenset(
        {
            "pis-v4-international-payment-consent-read-request",
            "pis-v4-international-payment-create-request",
            "pis-v4-international-payment-read-request",
        }
    ),
    "pis-v4-international-scheduled-payment-consent-create-request": frozenset(
        {
            "pis-v4-international-scheduled-payment-consent-read-request",
            "pis-v4-international-scheduled-payment-create-request",
            "pis-v4-international-scheduled-payment-read-request",
        }
    ),
}
"""PIS request steps that need each consent to be PSU-authorised first."""

_VRP_CONSENT_AUTHORIZATION_STEP_IDS = frozenset(
    {
        "vrp-consent-create-awaiting-authorisation-v31-pre-3111-request",
        "vrp-consent-create-awaiting-authorisation-v31-3111-request",
        "vrp-consent-create-awaiting-authorisation-v4-request",
        "cvrp-consent-create-awaiting-authorisation-v4-request",
    }
)
"""VRP/cVRP consent-creation request steps that can be PSU-authorised."""

_VRP_PSU_PAYMENT_ACCESS_TOKEN_SUFFIX = "-psu-payment-access"  # noqa: S105 - semantic token id suffix
"""Suffix for semantic token ids produced by VRP/cVRP PSU authorisation."""

_VRP_PSU_AUTHORIZATION_STEP_SUFFIX = "-authorisation"
"""Suffix for synthetic VRP/cVRP PSU authorisation step ids."""

_VRP_PSU_AUTHORIZATION_TOKEN_STEP_SUFFIX = "-psu-payment-token"  # noqa: S105 - step id suffix
"""Suffix for synthetic VRP/cVRP authorisation-code token exchange step ids."""

_CATALOGUE_TOKEN_SCOPES = {
    _AIS_CLIENT_CREDENTIALS_TOKEN_ID: "accounts",
    _CBPII_CLIENT_CREDENTIALS_TOKEN_ID: "fundsconfirmations",
    "pis-payment-access": "payments",
    "vrp-payment-access": "payments",
}
"""OAuth client-credentials scopes for synthetic catalogue token setup."""


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


def run_manifest(
    manifest: Manifest,
    *,
    client: httpx.Client,
    execution_logger: ExecutionLogger | None = None,
    plan: TestPlan | None = None,
    run_id: str | None = None,
    auth_session_store: AuthSessionStore | None = None,
    runtime_config: RuntimeConfig | None = None,
    fapi_signing_config: FapiSigningConfig | None = None,
    mtls_client_configured: bool = False,
    approved_release_policy: ApprovedReleasePolicy | None = None,
) -> SmokeCheckResult:
    """Run a parsed manifest and return a structured smoke-check result.

    Dispatches to the v0 or v1 execution path based on schema version.
    v0 manifests are internally desugared to v1 sequential steps.

    Args:
        manifest: Parsed and validated manifest to execute.
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
        mtls_client_configured: Whether the shared HTTP client was configured
            with an mTLS client certificate and private key. Used to fail
            ``tls_client_auth`` token steps clearly before dispatch.
        approved_release_policy: Optional approved-release policy used by the
            generated report's participant-side certification self-assessment.

    Returns:
        Smoke-check result containing ordered manifest test steps.
    """
    logger_sink: ExecutionLogger = execution_logger or NullExecutionLogger()
    effective_run_id = run_id if run_id is not None else _logger_run_id(logger_sink) or new_run_id()
    effective_store = auth_session_store if auth_session_store is not None else AuthSessionStore()
    run_started_payload: JsonObject = {"schemaVersion": manifest.schema_version}
    logger_sink.emit("run-started", payload=run_started_payload)
    try:
        if manifest.schema_version == "v1":
            effective_plan = plan if plan is not None else TestPlan.default_plan_from_manifest(manifest)
            result = _run_manifest_v1(
                manifest,
                client=client,
                execution_logger=logger_sink,
                plan=effective_plan,
                run_id=effective_run_id,
                auth_session_store=effective_store,
                runtime_config=runtime_config,
                fapi_signing_config=fapi_signing_config,
                mtls_client_configured=mtls_client_configured,
                approved_release_policy=approved_release_policy,
            )
        else:
            result = _run_manifest_v0(
                manifest,
                client=client,
                execution_logger=logger_sink,
                run_id=effective_run_id,
                auth_session_store=effective_store,
                runtime_config=runtime_config,
                approved_release_policy=approved_release_policy,
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


def run_compiled_test_plan(
    compiled_plan: CompiledTestPlan,
    *,
    runtime_inputs: Mapping[str, JsonValue],
    runtime_input_base_dir: Path,
    client: httpx.Client,
    execution_logger: ExecutionLogger | None = None,
    run_id: str | None = None,
    auth_session_store: AuthSessionStore | None = None,
    runtime_config: RuntimeConfig | None = None,
    fapi_signing_config: FapiSigningConfig | None = None,
    mtls_client_configured: bool = False,
    approved_release_policy: ApprovedReleasePolicy | None = None,
) -> SmokeCheckResult:
    """Run a compiled catalogue plan and return structured result evidence.

    Args:
        compiled_plan: Deterministic catalogue graph produced by the compiler.
        runtime_inputs: Original runtime input mapping from the plan spec. This
            may include sensitive values and is never persisted directly.
        runtime_input_base_dir: Directory used to resolve runtime
            ``file_reference`` values safely.
        client: Preconfigured synchronous HTTP client used for network requests.
        execution_logger: Optional structured execution-log sink.
        run_id: Optional run identifier used for log/auth-session correlation.
        auth_session_store: Optional PSU authorisation store.
        runtime_config: Optional safe participant config values.
        fapi_signing_config: Optional validated FAPI signing configuration.
        mtls_client_configured: Whether the shared HTTP client has mTLS
            credentials configured.
        approved_release_policy: Optional approved-release policy used by the
            report's certification self-assessment.

    Returns:
        Smoke-check result populated with catalogue traceability metadata.
    """
    logger_sink: ExecutionLogger = execution_logger or NullExecutionLogger()
    effective_run_id = run_id if run_id is not None else _logger_run_id(logger_sink) or new_run_id()
    effective_store = auth_session_store if auth_session_store is not None else AuthSessionStore()
    synthetic_manifest = _compiled_plan_to_manifest(
        compiled_plan,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=runtime_input_base_dir,
        runtime_config=runtime_config,
    )
    logger_sink.emit(
        "run-started",
        payload={
            "catalogue": {
                "standard": compiled_plan.catalogue_key.standard,
                "version": compiled_plan.catalogue_key.version,
                "api": compiled_plan.catalogue_key.api,
                "catalogueVersion": compiled_plan.catalogue_version,
            },
        },
    )
    try:
        result = _run_manifest_v1(
            synthetic_manifest,
            client=client,
            execution_logger=logger_sink,
            plan=TestPlan.default_plan_from_manifest(synthetic_manifest),
            run_id=effective_run_id,
            auth_session_store=effective_store,
            runtime_config=runtime_config,
            fapi_signing_config=fapi_signing_config,
            mtls_client_configured=mtls_client_configured,
            approved_release_policy=approved_release_policy,
            compiled_plan=compiled_plan,
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


def _compiled_plan_to_manifest(
    compiled_plan: CompiledTestPlan,
    *,
    runtime_inputs: Mapping[str, JsonValue],
    runtime_input_base_dir: Path,
    runtime_config: RuntimeConfig | None,
) -> Manifest:
    """Build an internal manifest facade for existing HTTP execution plumbing.

    Args:
        compiled_plan: Compiled catalogue plan to execute.
        runtime_inputs: Original plan-spec runtime input mapping.
        runtime_input_base_dir: Directory used to resolve file references.
        runtime_config: Safe participant config values, used for discovery URL.

    Returns:
        Synthetic v1 manifest containing one selected step per catalogue
        request step.
    """
    steps: list[V1Step] = []
    steps.extend(_catalogue_synthetic_token_steps(compiled_plan))
    for test_case in compiled_plan.test_cases:
        requirements = {requirement.input_id: requirement for requirement in test_case.runtime_input_requirements}
        for request_step in test_case.request_steps:
            if request_step.step_id == _AIS_CONSENT_CREATE_STEP_ID:
                for profile in _compiled_plan_ais_permission_profiles(compiled_plan):
                    manifest_step = _ais_profile_consent_step(
                        request_step,
                        profile=profile,
                        runtime_inputs=runtime_inputs,
                        runtime_input_base_dir=runtime_input_base_dir,
                        runtime_config=runtime_config,
                        requirements=requirements,
                    )
                    steps.append(manifest_step)
                    steps.append(_ais_psu_authorization_step(profile=profile))
                continue
            if request_step.step_id == _AIS_ACCOUNT_ACCESS_TOKEN_STEP_ID:
                steps.extend(
                    _ais_authorization_code_token_step(profile=profile)
                    for profile in _compiled_plan_ais_permission_profiles(compiled_plan)
                )
                continue
            manifest_step = _catalogue_request_step_to_manifest_step(
                test_case,
                request_step,
                runtime_inputs=runtime_inputs,
                runtime_input_base_dir=runtime_input_base_dir,
                runtime_config=runtime_config,
                requirements=requirements,
            )
            steps.append(manifest_step)
            steps.extend(compiled_plan_synthetic_inline_steps(compiled_plan, request_step))
    catalogue_name = (
        f"{compiled_plan.catalogue_key.standard} "
        f"{compiled_plan.catalogue_key.version} "
        f"{compiled_plan.catalogue_key.api}"
    )
    return Manifest(
        schema_version="v1",
        name=catalogue_name,
        certification_coverage="complete",
        steps=tuple(steps),
    )


def compiled_plan_synthetic_setup_steps(compiled_plan: CompiledTestPlan) -> tuple[ManifestStep, ...]:
    """Build synthetic setup steps required by a compiled catalogue plan.

    Args:
        compiled_plan: Compiled catalogue plan whose selected resource steps
            may consume runtime-produced artifacts.

    Returns:
        Manifest setup steps inserted before catalogue request steps.
    """
    return _catalogue_synthetic_token_steps(compiled_plan)


def compiled_plan_synthetic_inline_steps(
    compiled_plan: CompiledTestPlan,
    request_step: CatalogueRequestStep,
) -> tuple[V1Step, ...]:
    """Build synthetic runtime steps inserted after a catalogue request step.

    Args:
        compiled_plan: Compiled catalogue plan whose request sequence is being
            snapshotted or converted.
        request_step: Catalogue request step most recently emitted.

    Returns:
        Synthetic runtime steps inserted immediately after ``request_step``.
    """
    return _catalogue_inline_authorization_steps(compiled_plan, request_step)


def _catalogue_synthetic_token_steps(compiled_plan: CompiledTestPlan) -> tuple[ManifestStep, ...]:
    """Build runtime token-acquisition setup steps missing from catalogue cases.

    Args:
        compiled_plan: Compiled catalogue plan whose selected resource steps
            may consume semantic access-token ids.

    Returns:
        Synthetic setup steps that acquire and record semantic access tokens
        before protected-resource requests execute.
    """
    required_token_ids: list[str] = []
    produced_token_ids: set[str] = set()
    for test_case in compiled_plan.test_cases:
        for request_step in test_case.request_steps:
            if request_step.produced_token_id is not None:
                produced_token_ids.add(request_step.produced_token_id)
            if request_step.required_token_id is not None and request_step.required_token_id not in required_token_ids:
                required_token_ids.append(request_step.required_token_id)

    steps: list[ManifestStep] = []
    for token_id in required_token_ids:
        if token_id in produced_token_ids:
            continue
        scope = _CATALOGUE_TOKEN_SCOPES.get(token_id)
        if scope is None:
            continue
        steps.append(_catalogue_client_credentials_token_step(token_id=token_id, scope=scope))
    return tuple(steps)


def _catalogue_inline_authorization_steps(
    compiled_plan: CompiledTestPlan,
    request_step: CatalogueRequestStep,
) -> tuple[V1Step, ...]:
    """Build inline runtime authorisation steps that depend on a catalogue response.

    Args:
        compiled_plan: Compiled catalogue plan whose selected resource steps may
            require a PSU-authorised consent.
        request_step: Catalogue request step most recently converted.

    Returns:
        Synthetic runtime steps to insert immediately after ``request_step``.
    """
    if request_step.step_id != _CBPII_CONSENT_CREATE_STEP_ID:
        pis_steps = _pis_inline_authorization_steps(compiled_plan, request_step)
        if pis_steps:
            return pis_steps
        return _vrp_inline_authorization_steps(compiled_plan, request_step)
    if not _compiled_plan_requires_cbpii_consent_authorization(compiled_plan):
        return ()
    return (_cbpii_psu_authorization_step(), _cbpii_authorization_code_token_step())


def _pis_inline_authorization_steps(
    compiled_plan: CompiledTestPlan,
    request_step: CatalogueRequestStep,
) -> tuple[V1Step, ...]:
    """Build a PIS PSU authorisation step after a selected consent creation.

    Args:
        compiled_plan: Compiled catalogue plan whose selected PIS resource steps
            determine whether an authorisation step is needed.
        request_step: Request step most recently emitted into the manifest.

    Returns:
            A PSU authorisation step and matching authorisation-code token
            exchange when downstream PIS steps need the created consent authorised,
            otherwise an empty tuple.
    """
    if request_step.step_id not in _PIS_CONSENT_AUTHORIZATION_STEPS:
        return ()
    if not _compiled_plan_requires_pis_consent_authorization(compiled_plan, request_step.step_id):
        return ()
    return (_pis_psu_authorization_step(request_step.step_id), _pis_authorization_code_token_step(request_step.step_id))


def _vrp_inline_authorization_steps(
    compiled_plan: CompiledTestPlan,
    request_step: CatalogueRequestStep,
) -> tuple[V1Step, ...]:
    """Build VRP PSU authorisation after each selected consent creation.

    Args:
        compiled_plan: Compiled catalogue plan whose selected VRP/cVRP steps may
            require a PSU-authorised payments token.
        request_step: Request step most recently emitted into the manifest.

    Returns:
        A PSU authorisation step and matching authorisation-code token exchange
        when downstream VRP/cVRP steps need this consent's payment access,
        otherwise an empty tuple.
    """
    if request_step.step_id not in _VRP_CONSENT_AUTHORIZATION_STEP_IDS:
        return ()
    if not _compiled_plan_requires_vrp_consent_authorization(compiled_plan, request_step.step_id):
        return ()
    return (
        _vrp_psu_authorization_step(request_step.step_id),
        _vrp_authorization_code_token_step(request_step.step_id),
    )


def _compiled_plan_requires_pis_consent_authorization(compiled_plan: CompiledTestPlan, consent_step_id: str) -> bool:
    """Return whether selected PIS steps need a PSU-authorised consent.

    Args:
        compiled_plan: Compiled catalogue plan to inspect.
        consent_step_id: PIS consent-creation request step id.

    Returns:
        ``True`` when any selected downstream step consumes the consent created
        by ``consent_step_id``.
    """
    dependent_step_ids = _PIS_CONSENT_AUTHORIZATION_DEPENDENT_STEPS.get(consent_step_id, frozenset())
    return any(
        request_step.step_id in dependent_step_ids
        for test_case in compiled_plan.test_cases
        for request_step in test_case.request_steps
    )


def _compiled_plan_requires_vrp_consent_authorization(compiled_plan: CompiledTestPlan, consent_step_id: str) -> bool:
    """Return whether selected VRP/cVRP steps need this consent authorised.

    Args:
        compiled_plan: Compiled catalogue plan to inspect.
        consent_step_id: VRP/cVRP consent-creation request step id.

    Returns:
        ``True`` when a selected request consumes the PSU token produced by
        authorising ``consent_step_id``.
    """
    token_id = _vrp_psu_payment_access_token_id(consent_step_id)
    return any(
        request_step.required_token_id == token_id
        for test_case in compiled_plan.test_cases
        for request_step in test_case.request_steps
    )


def _vrp_authorization_step_id(consent_step_id: str) -> str:
    """Return the PSU authorisation step id for one VRP consent step.

    Args:
        consent_step_id: VRP/cVRP consent-creation request step id.

    Returns:
        Stable manifest step id for the synthetic PSU authorisation step.
    """
    return f"{consent_step_id.removesuffix('-request')}{_VRP_PSU_AUTHORIZATION_STEP_SUFFIX}"


def _vrp_authorization_token_step_id(consent_step_id: str) -> str:
    """Return the authorisation-code token step id for one VRP consent step.

    Args:
        consent_step_id: VRP/cVRP consent-creation request step id.

    Returns:
        Stable manifest step id for the synthetic token-exchange step.
    """
    return f"{consent_step_id.removesuffix('-request')}{_VRP_PSU_AUTHORIZATION_TOKEN_STEP_SUFFIX}"


def _vrp_psu_payment_access_token_id(consent_step_id: str) -> str:
    """Return the PSU payment token id produced for one VRP consent.

    Args:
        consent_step_id: VRP/cVRP consent-creation request step id.

    Returns:
        Semantic token id consumed by payments and funds-confirmation requests
        bound to ``consent_step_id``.
    """
    return f"{consent_step_id.removesuffix('-request')}{_VRP_PSU_PAYMENT_ACCESS_TOKEN_SUFFIX}"


def _compiled_plan_ais_permission_profiles(compiled_plan: CompiledTestPlan) -> tuple[str, ...]:
    """Return selected legacy AIS permission profiles in deterministic order.

    Args:
        compiled_plan: Compiled catalogue plan to inspect.

    Returns:
        Permission profile labels required by selected protected AIS resource
        requests.
    """
    required_token_ids = {
        request_step.required_token_id
        for test_case in compiled_plan.test_cases
        for request_step in test_case.request_steps
        if request_step.required_token_id is not None
    }
    return tuple(
        profile for profile, token_id in _AIS_PERMISSION_PROFILE_TOKEN_IDS.items() if token_id in required_token_ids
    )


def _ais_profile_consent_step(
    request_step: CatalogueRequestStep,
    *,
    profile: str,
    runtime_inputs: Mapping[str, JsonValue],
    runtime_input_base_dir: Path,
    runtime_config: RuntimeConfig | None,
    requirements: Mapping[str, RuntimeInputRequirement],
) -> ManifestStep:
    """Build one AIS consent-creation step for a legacy permission profile.

    Args:
        request_step: Catalogue consent template request.
        profile: Legacy AIS permission profile to materialise.
        runtime_inputs: Original plan-spec runtime input mapping.
        runtime_input_base_dir: Directory used to resolve file references.
        runtime_config: Safe participant config values, used for discovery URL.
        requirements: Runtime input requirements keyed by input id.

    Returns:
        Manifest HTTP step that creates a profile-specific AIS account-access
        consent.
    """
    generated_runtime_values = _catalogue_generated_runtime_values(request_step)
    resolved_url = _catalogue_request_url(
        request_step,
        runtime_inputs=runtime_inputs,
        runtime_config=runtime_config,
        generated_runtime_values=generated_runtime_values,
    )
    return ManifestStep(
        id=_ais_profile_consent_step_id(profile),
        name=f"Create AIS {profile} account-access consent",
        request=ManifestRequest(
            method=request_step.method,
            url=resolved_url,
            headers=_catalogue_request_headers(
                request_step,
                runtime_inputs=runtime_inputs,
                runtime_input_base_dir=runtime_input_base_dir,
                requirements=requirements,
                generated_header_values=_catalogue_generated_header_values(request_step),
                generated_runtime_values=generated_runtime_values,
            ),
            body=JsonBody(value=_AIS_ACCOUNT_ACCESS_CONSENT_BODIES[profile]),
            detached_jws=DetachedJwsPolicy(source="fapi-signing"),
        ),
        assertions=(HttpStatusAssertion(type="http_status", expected=201),),
        mandatory=True,
        group="catalogue",
        phase="setup",
        required_token_id=request_step.required_token_id,
    )


def _compiled_plan_requires_cbpii_consent_authorization(compiled_plan: CompiledTestPlan) -> bool:
    """Return whether selected CBPII resource steps need PSU authorisation.

    Args:
        compiled_plan: Compiled catalogue plan to inspect.

    Returns:
        ``True`` when a selected CBPII authorised-consent or funds-confirmation
        request is present.
    """
    return any(
        request_step.step_id in _CBPII_AUTHORISED_RESOURCE_STEP_IDS
        for test_case in compiled_plan.test_cases
        for request_step in test_case.request_steps
    )


def _compiled_plan_requires_ais_account_access(compiled_plan: CompiledTestPlan) -> bool:
    """Return whether selected AIS steps need PSU-authorised account access.

    Args:
        compiled_plan: Compiled catalogue plan to inspect.

    Returns:
        ``True`` when a selected AIS request consumes the semantic
        an ``ais-account-access-*`` bearer token.
    """
    return any(
        request_step.required_token_id in _AIS_PERMISSION_PROFILE_TOKEN_IDS.values()
        for test_case in compiled_plan.test_cases
        for request_step in test_case.request_steps
    )


def _ais_profile_consent_step_id(profile: str) -> str:
    """Return the AIS consent-creation step id for a permission profile.

    Args:
        profile: Legacy AIS permission profile.

    Returns:
        Stable setup step id for the profile's account-access-consent request.
    """
    return f"ais-at-setup-{profile}-consent-request"


def _ais_profile_authorization_step_id(profile: str) -> str:
    """Return the AIS PSU authorisation step id for a permission profile.

    Args:
        profile: Legacy AIS permission profile.

    Returns:
        Stable setup step id for the profile's PSU authorisation.
    """
    return f"setup-ais-{profile}-consent-authorisation"


def _ais_profile_token_step_id(profile: str) -> str:
    """Return the AIS token-exchange step id for a permission profile.

    Args:
        profile: Legacy AIS permission profile.

    Returns:
        Stable setup step id for the profile's authorisation-code exchange.
    """
    return f"ais-at-setup-{profile}-token-request"


def _ais_profile_captured_consent_id(profile: str) -> str:
    """Return the consent-id placeholder for a permission profile.

    Args:
        profile: Legacy AIS permission profile.

    Returns:
        Placeholder resolving to the created AIS account-access consent id.
    """
    return f"${{steps.{_ais_profile_consent_step_id(profile)}.response.body.Data.ConsentId}}"


def _cbpii_psu_authorization_step() -> PsuAuthorizationStep:
    """Build the CBPII PSU consent-authorisation setup step.

    Returns:
        PSU authorisation step that binds the captured CBPII consent id into a
        generated FAPI request object.
    """
    return PsuAuthorizationStep(
        id=_CBPII_AUTHORIZATION_STEP_ID,
        name="Authorise CBPII funds-confirmation consent",
        mode="manual",
        authorization_endpoint="${config.oauth.authorizationEndpoint}",
        client_id="${config.oauth.clientId}",
        redirect_uri="${config.oauth.redirectUri}",
        scope="openid fundsconfirmations",
        request_object=GeneratedRequestObject(
            source="fapi-signing",
            audience="${config.oauth.issuer}",
            openbanking_intent_id=_CBPII_CAPTURED_CONSENT_ID,
        ),
        mandatory=True,
        group="catalogue",
        phase="execution",
    )


def _pis_psu_authorization_step(consent_step_id: str) -> PsuAuthorizationStep:
    """Build a PIS PSU consent-authorisation step.

    Args:
        consent_step_id: PIS consent-creation request step whose response body
            contains the intent id to authorise.

    Returns:
        PSU authorisation step that binds the captured PIS consent id into a
        generated FAPI request object.

    Raises:
        ValueError: If ``consent_step_id`` is not a known PIS consent step.
    """
    step_metadata = _PIS_CONSENT_AUTHORIZATION_STEPS.get(consent_step_id)
    if step_metadata is None:
        raise ValueError(f"Unknown PIS consent step id: {consent_step_id}")
    step_id, step_name, _token_step_id, _token_id, _flow_label = step_metadata
    captured_consent_id = f"${{steps.{consent_step_id}.response.body.Data.ConsentId}}"
    return PsuAuthorizationStep(
        id=step_id,
        name=step_name,
        mode="manual",
        authorization_endpoint="${config.oauth.authorizationEndpoint}",
        client_id="${config.oauth.clientId}",
        redirect_uri="${config.oauth.redirectUri}",
        scope="openid payments",
        request_object=GeneratedRequestObject(
            source="fapi-signing",
            audience="${config.oauth.issuer}",
            openbanking_intent_id=captured_consent_id,
        ),
        mandatory=True,
        group="catalogue",
        phase="execution",
    )


def _pis_authorization_code_token_step(consent_step_id: str) -> ManifestStep:
    """Build a PIS authorisation-code token exchange step for one consent flow.

    Args:
        consent_step_id: PIS consent-creation request step whose PSU
            authorisation code should be exchanged.

    Returns:
        HTTP token-exchange step that records a flow-specific PIS bearer token
        for downstream consent, funds-confirmation, and payment requests.

    Raises:
        ValueError: If ``consent_step_id`` is not a known PIS consent step.
    """
    step_metadata = _PIS_CONSENT_AUTHORIZATION_STEPS.get(consent_step_id)
    if step_metadata is None:
        raise ValueError(f"Unknown PIS consent step id: {consent_step_id}")
    psu_step_id, _psu_step_name, token_step_id, token_id, flow_label = step_metadata
    return ManifestStep(
        id=token_step_id,
        name=f"Exchange PIS {flow_label} authorisation code for payments token",
        request=ManifestRequest(
            method="POST",
            url="${config.oauth.tokenEndpoint}",
            body=FormBody(
                fields={
                    "grant_type": "authorization_code",
                    "code": f"${{steps.{psu_step_id}.response.body.code}}",
                    "redirect_uri": "${config.oauth.redirectUri}",
                    "client_id": "${config.oauth.clientId}",
                }
            ),
        ),
        assertions=(HttpStatusAssertion(type="http_status", expected=200),),
        mandatory=True,
        group="catalogue",
        phase="execution",
        token_endpoint_auth_policy=TokenEndpointAuthPolicy(source="fapi-signing"),
        produces_token_id=token_id,
    )


def _vrp_psu_authorization_step(consent_step_id: str) -> PsuAuthorizationStep:
    """Build a VRP PSU consent-authorisation step.

    Args:
        consent_step_id: VRP/cVRP consent-creation request step whose response
            body contains the intent id to authorise.

    Returns:
        PSU authorisation step that binds the captured VRP consent id into a
        generated FAPI request object.
    """
    captured_consent_id = f"${{steps.{consent_step_id}.response.body.Data.ConsentId}}"
    return PsuAuthorizationStep(
        id=_vrp_authorization_step_id(consent_step_id),
        name="Authorise VRP consent",
        mode="manual",
        authorization_endpoint="${config.oauth.authorizationEndpoint}",
        client_id="${config.oauth.clientId}",
        redirect_uri="${config.oauth.redirectUri}",
        scope="openid payments",
        request_object=GeneratedRequestObject(
            source="fapi-signing",
            audience="${config.oauth.issuer}",
            openbanking_intent_id=captured_consent_id,
        ),
        mandatory=True,
        group="catalogue",
        phase="execution",
    )


def _vrp_authorization_code_token_step(consent_step_id: str) -> ManifestStep:
    """Build a VRP authorisation-code token exchange step.

    Args:
        consent_step_id: VRP/cVRP consent-creation request step id whose PSU
            authorisation produced the code.

    Returns:
        HTTP token-exchange step that records the VRP bearer token for
        downstream payment and funds-confirmation requests.
    """
    authorisation_step_id = _vrp_authorization_step_id(consent_step_id)
    return ManifestStep(
        id=_vrp_authorization_token_step_id(consent_step_id),
        name="Exchange VRP authorisation code for payments token",
        request=ManifestRequest(
            method="POST",
            url="${config.oauth.tokenEndpoint}",
            body=FormBody(
                fields={
                    "grant_type": "authorization_code",
                    "code": f"${{steps.{authorisation_step_id}.response.body.code}}",
                    "redirect_uri": "${config.oauth.redirectUri}",
                    "client_id": "${config.oauth.clientId}",
                }
            ),
        ),
        assertions=(HttpStatusAssertion(type="http_status", expected=200),),
        mandatory=True,
        group="catalogue",
        phase="execution",
        token_endpoint_auth_policy=TokenEndpointAuthPolicy(source="fapi-signing"),
        produces_token_id=_vrp_psu_payment_access_token_id(consent_step_id),
    )


def _ais_psu_authorization_step(*, profile: str) -> PsuAuthorizationStep:
    """Build the AIS PSU consent-authorisation setup step.

    Args:
        profile: Legacy AIS permission profile to authorise.

    Returns:
        PSU authorisation step that binds the captured AIS consent id into a
        generated FAPI request object.
    """
    return PsuAuthorizationStep(
        id=_ais_profile_authorization_step_id(profile),
        name=f"Authorise AIS {profile} account-access consent",
        mode="manual",
        authorization_endpoint="${config.oauth.authorizationEndpoint}",
        client_id="${config.oauth.clientId}",
        redirect_uri="${config.oauth.redirectUri}",
        scope="openid accounts",
        request_object=GeneratedRequestObject(
            source="fapi-signing",
            audience="${config.oauth.issuer}",
            openbanking_intent_id=_ais_profile_captured_consent_id(profile),
        ),
        mandatory=True,
        group="catalogue",
        phase="setup",
    )


def _cbpii_authorization_code_token_step() -> ManifestStep:
    """Build the CBPII authorisation-code token exchange setup step.

    Returns:
        HTTP token-exchange step that records the CBPII funds-confirmation
        bearer token for downstream protected-resource requests.
    """
    return ManifestStep(
        id=_CBPII_AUTHORIZATION_TOKEN_STEP_ID,
        name="Exchange CBPII authorisation code for funds-confirmation token",
        request=ManifestRequest(
            method="POST",
            url="${config.oauth.tokenEndpoint}",
            body=FormBody(
                fields={
                    "grant_type": "authorization_code",
                    "code": f"${{steps.{_CBPII_AUTHORIZATION_STEP_ID}.response.body.code}}",
                    "redirect_uri": "${config.oauth.redirectUri}",
                    "client_id": "${config.oauth.clientId}",
                }
            ),
        ),
        assertions=(HttpStatusAssertion(type="http_status", expected=200),),
        mandatory=True,
        group="catalogue",
        phase="execution",
        token_endpoint_auth_policy=TokenEndpointAuthPolicy(source="fapi-signing"),
        produces_token_id=_CBPII_FUNDS_CONFIRMATION_TOKEN_ID,
    )


def _ais_authorization_code_token_step(*, profile: str) -> ManifestStep:
    """Build an AIS authorisation-code token exchange for one profile.

    Args:
        profile: Legacy AIS permission profile whose PSU authorisation code
            should be exchanged.

    Returns:
        HTTP token-exchange step that records the profile-specific AIS bearer
        token for downstream protected-resource requests.
    """
    return ManifestStep(
        id=_ais_profile_token_step_id(profile),
        name=f"Exchange AIS {profile} authorisation code for account-access token",
        request=ManifestRequest(
            method="POST",
            url="${config.oauth.tokenEndpoint}",
            body=FormBody(
                fields={
                    "grant_type": "authorization_code",
                    "code": f"${{steps.{_ais_profile_authorization_step_id(profile)}.response.body.code}}",
                    "redirect_uri": "${config.oauth.redirectUri}",
                    "client_id": "${config.oauth.clientId}",
                }
            ),
        ),
        assertions=(HttpStatusAssertion(type="http_status", expected=200),),
        mandatory=True,
        group="catalogue",
        phase="setup",
        token_endpoint_auth_policy=TokenEndpointAuthPolicy(source="fapi-signing"),
        produces_token_id=_AIS_PERMISSION_PROFILE_TOKEN_IDS[profile],
    )


def _catalogue_client_credentials_token_step(*, token_id: str, scope: str) -> ManifestStep:
    """Build a client-credentials OAuth token setup step for a semantic token.

    Args:
        token_id: Semantic token id consumed by protected-resource steps.
        scope: OAuth scope value requested from the token endpoint.

    Returns:
        Manifest setup step that records ``access_token`` as ``token_id``.
    """
    return ManifestStep(
        id=f"setup-token-{token_id}",
        name=f"Acquire {scope} access token",
        request=ManifestRequest(
            method="POST",
            url="${config.oauth.tokenEndpoint}",
            body=FormBody(
                fields={
                    "grant_type": "client_credentials",
                    "scope": scope,
                    "client_id": "${config.oauth.clientId}",
                }
            ),
        ),
        assertions=(HttpStatusAssertion(type="http_status", expected=200),),
        mandatory=True,
        group="setup",
        phase="setup",
        token_endpoint_auth_policy=TokenEndpointAuthPolicy(source="fapi-signing"),
        produces_token_id=token_id,
    )


def _catalogue_request_step_to_manifest_step(
    test_case: CatalogueTestCase,
    request_step: CatalogueRequestStep,
    *,
    runtime_inputs: Mapping[str, JsonValue],
    runtime_input_base_dir: Path,
    runtime_config: RuntimeConfig | None,
    requirements: Mapping[str, RuntimeInputRequirement],
) -> ManifestStep:
    """Convert one catalogue request skeleton into an executable HTTP step.

    Args:
        test_case: Catalogue test case that owns the request step.
        request_step: Request skeleton to execute.
        runtime_inputs: Original plan-spec runtime input mapping.
        runtime_input_base_dir: Directory used to resolve file references.
        runtime_config: Safe participant config values, used for discovery URL.
        requirements: Runtime input requirements keyed by input id.

    Returns:
        Manifest-compatible HTTP step for the existing executor.
    """
    generated_header_values = _catalogue_generated_header_values(request_step)
    generated_runtime_values = _catalogue_generated_runtime_values(request_step)
    resolved_url = _catalogue_request_url(
        request_step,
        runtime_inputs=runtime_inputs,
        runtime_config=runtime_config,
        generated_runtime_values=generated_runtime_values,
    )
    headers = _catalogue_request_headers(
        request_step,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=runtime_input_base_dir,
        requirements=requirements,
        generated_header_values=generated_header_values,
        generated_runtime_values=generated_runtime_values,
    )
    body = _catalogue_request_body(
        request_step,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=runtime_input_base_dir,
        requirements=requirements,
        generated_runtime_values=generated_runtime_values,
    )
    return ManifestStep(
        id=request_step.step_id,
        name=request_step.name,
        request=ManifestRequest(
            method=request_step.method,
            url=resolved_url,
            headers=headers,
            body=body,
            detached_jws=_catalogue_detached_jws_policy(request_step),
        ),
        assertions=tuple(
            _catalogue_assertion_to_manifest_assertion(
                assertion,
                runtime_inputs=runtime_inputs,
                generated_header_values=generated_header_values,
            )
            for assertion in test_case.assertions
        ),
        mandatory=test_case.mandatory,
        group="catalogue",
        phase=_catalogue_step_phase(test_case, request_step),
        required_token_id=request_step.required_token_id,
        produces_token_id=request_step.produced_token_id,
        response_signature_policy=(
            ResponseSignaturePolicy(source="discovery-jwks") if test_case.response_signature_required else None
        ),
        token_endpoint_auth_policy=_catalogue_token_endpoint_auth_policy(request_step),
    )


def _catalogue_step_phase(test_case: CatalogueTestCase, request_step: CatalogueRequestStep) -> StepPhase:
    """Return the manifest execution phase for a catalogue request.

    Args:
        test_case: Catalogue case that owns the request.
        request_step: Request step being converted.

    Returns:
        Manifest phase for scheduling. AIS consent setup is deliberately
        scheduled in setup so it runs before protected AIS resource checks.
    """
    if request_step.step_id == _AIS_CONSENT_CREATE_STEP_ID:
        return "setup"
    if test_case.role in {"setup", "security", "token"}:
        return "setup"
    return "execution"


def _catalogue_detached_jws_policy(request_step: CatalogueRequestStep) -> DetachedJwsPolicy | None:
    """Return detached-JWS policy for generated Open Banking write requests.

    Args:
        request_step: Request step being converted.

    Returns:
        Detached-JWS policy for AIS account-access consent creation and PIS
        payment-initiation write requests, otherwise ``None``.
    """
    if request_step.step_id == _AIS_CONSENT_CREATE_STEP_ID:
        return DetachedJwsPolicy(source="fapi-signing")
    if (
        request_step.method in {"POST", "PUT", "PATCH"}
        and request_step.path.startswith(_OB_PIS_PATH_PREFIX)
        and request_step.step_id.startswith("pis-v4-")
        and request_step.step_id != _PIS_MISSING_SIGNATURE_STEP_ID
    ):
        return DetachedJwsPolicy(
            source="fapi-signing",
            omit_protected_headers=request_step.detached_jws_omit_claims,
        )
    if (
        request_step.method in {"POST", "PUT", "PATCH"}
        and request_step.step_id.startswith(("vrp-", "cvrp-"))
        and request_step.body_template is not None
    ):
        return DetachedJwsPolicy(source="fapi-signing")
    return None


def _catalogue_token_endpoint_auth_policy(request_step: CatalogueRequestStep) -> TokenEndpointAuthPolicy | None:
    """Return token-endpoint authentication policy for catalogue setup steps.

    Args:
        request_step: Request step being converted.

    Returns:
        FAPI token endpoint authentication policy for generated AIS token
        exchange steps, otherwise ``None``.
    """
    if request_step.step_id == _AIS_ACCOUNT_ACCESS_TOKEN_STEP_ID:
        return TokenEndpointAuthPolicy(source="fapi-signing")
    return None


def _catalogue_request_url(
    request_step: CatalogueRequestStep,
    *,
    runtime_inputs: Mapping[str, JsonValue],
    runtime_config: RuntimeConfig | None,
    generated_runtime_values: Mapping[str, str],
) -> str:
    """Resolve a catalogue request path to an absolute HTTPS URL.

    Args:
        request_step: Catalogue request skeleton.
        runtime_inputs: Original plan-spec runtime input mapping.
        runtime_config: Safe participant config values, used for discovery URL.
        generated_runtime_values: Generated runtime values keyed by catalogue
            data id for the current request step.

    Returns:
        Absolute URL to dispatch.

    Raises:
        ValueError: If a required runtime value for URL construction is absent
            or cannot be represented as a string.
    """
    if request_step.path == "/.well-known/openid-configuration":
        if runtime_config is None or runtime_config.discovery_url is None:
            raise ValueError("Discovery catalogue step requires runtime config discoveryUrl")
        return runtime_config.discovery_url
    if request_step.step_id == _AIS_ACCOUNT_ACCESS_TOKEN_STEP_ID or request_step.step_id in {
        _ais_profile_token_step_id(profile) for profile in _AIS_PERMISSION_PROFILE_TOKEN_IDS
    }:
        return "${config.oauth.tokenEndpoint}"

    base_url = _required_runtime_string(runtime_inputs, "resourceBaseUrl")
    resolved_path = _resolve_catalogue_path_variables(request_step, runtime_inputs=runtime_inputs)
    resolved_path = _resolve_catalogue_template_string(
        resolved_path,
        generated_runtime_values=generated_runtime_values,
        runtime_inputs=runtime_inputs,
    )
    return f"{base_url.rstrip('/')}/{resolved_path.lstrip('/')}"


def _resolve_catalogue_path_variables(
    request_step: CatalogueRequestStep,
    *,
    runtime_inputs: Mapping[str, JsonValue],
) -> str:
    """Replace OpenAPI-style path variables with plan-spec runtime values.

    Args:
        request_step: Catalogue request skeleton with a standards path.
        runtime_inputs: Original plan-spec runtime input mapping.

    Returns:
        Request path with ``{Variable}`` tokens substituted where present.

    Raises:
        ValueError: If a path variable cannot be matched to a runtime input.
    """

    def replace_variable(match: re.Match[str]) -> str:
        """Return the runtime value for one matched path variable.

        Args:
            match: Regular-expression match for ``{Variable}``.

        Returns:
            Runtime input value converted to a string.

        Raises:
            ValueError: If no matching runtime input exists.
        """
        variable = match.group(1)
        for candidate in _path_variable_input_candidates(variable, request_step.runtime_input_refs):
            value = runtime_inputs.get(candidate)
            if isinstance(value, str) and value:
                return value
        raise ValueError(f"Runtime input for path variable {{{variable}}} is missing")

    return _CATALOGUE_PATH_VARIABLE_PATTERN.sub(replace_variable, request_step.path)


def _path_variable_input_candidates(variable: str, runtime_input_refs: tuple[str, ...]) -> tuple[str, ...]:
    """Return candidate runtime input ids for an OpenAPI path variable.

    Args:
        variable: Path variable name without braces.
        runtime_input_refs: Runtime input ids referenced by the request step.

    Returns:
        Candidate input ids in precedence order.
    """
    lower_camel = variable[:1].lower() + variable[1:]
    suffix_matches = tuple(ref for ref in runtime_input_refs if ref.lower().endswith(lower_camel.lower()))
    semantic_matches = ("consentedAccountId",) if lower_camel == "accountId" else ()
    return (variable, lower_camel, *semantic_matches, *suffix_matches)


def _catalogue_request_headers(
    request_step: CatalogueRequestStep,
    *,
    runtime_inputs: Mapping[str, JsonValue],
    runtime_input_base_dir: Path,
    requirements: Mapping[str, RuntimeInputRequirement],
    generated_header_values: Mapping[str, str],
    generated_runtime_values: Mapping[str, str],
) -> dict[str, str] | None:
    """Build runtime headers for a catalogue request.

    Args:
        request_step: Catalogue request skeleton.
        runtime_inputs: Original plan-spec runtime input mapping.
        runtime_input_base_dir: Directory used to resolve file references.
        requirements: Runtime input requirements keyed by input id.
        generated_header_values: Generated header values keyed by lower-case
            header name for the current request step.
        generated_runtime_values: Generated runtime values keyed by catalogue
            data id for the current request step.

    Returns:
        Header mapping, or ``None`` when no headers are needed.

    Raises:
        ValueError: If a referenced token or required header value cannot be resolved.
    """
    headers: dict[str, str] = {}
    if request_step.required_token_id is not None:
        headers["Authorization"] = f"Bearer ${{tokens.{request_step.required_token_id}.access_token}}"
    access_token = _optional_access_token(
        request_step,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=runtime_input_base_dir,
        requirements=requirements,
    )
    if access_token is not None and request_step.required_token_id is None:
        headers["Authorization"] = f"Bearer {access_token}"
    invalid_access_token = generated_runtime_values.get("invalidAccessToken")
    if invalid_access_token is not None and request_step.required_token_id is None:
        headers["Authorization"] = f"Bearer {invalid_access_token}"
    for request_header in request_step.headers:
        value: str | None
        if request_header.generated_value is not None:
            value = generated_header_values[request_header.name.lower()]
        else:
            if request_header.input_id is None:
                raise ValueError(f"Catalogue request header '{request_header.name}' has no value source")
            value = _catalogue_request_header_value(
                request_header.input_id,
                runtime_inputs=runtime_inputs,
                requirements=requirements,
            )
        if value is not None:
            headers[request_header.name] = value
    return headers or None


def _catalogue_generated_header_values(request_step: CatalogueRequestStep) -> dict[str, str]:
    """Generate per-request catalogue header values.

    Args:
        request_step: Catalogue request skeleton whose header declarations may
            request generated values.

    Returns:
        Generated header values keyed by lower-case HTTP header name.
    """
    return {
        request_header.name.lower(): _generated_header_value(request_header.generated_value)
        for request_header in request_step.headers
        if request_header.generated_value is not None
    }


def _generated_header_value(generated_value: str) -> str:
    """Return a generated outbound header value.

    Args:
        generated_value: Catalogue generation strategy.

    Returns:
        Header value generated for a single request.

    Raises:
        ValueError: If the catalogue declares an unsupported strategy.
    """
    if generated_value == "uuid4":
        return str(uuid.uuid4())
    raise ValueError(f"Unsupported generated request-header value strategy '{generated_value}'")


def _catalogue_generated_runtime_values(request_step: CatalogueRequestStep) -> dict[str, str]:
    """Generate runtime values scoped to one catalogue request step.

    Args:
        request_step: Catalogue request skeleton whose templates may reference
            generated runtime values.

    Returns:
        Generated runtime values keyed by catalogue data id.
    """
    return {
        value_id: _generated_runtime_value(strategy) for value_id, strategy in request_step.generated_values.items()
    }


def _generated_runtime_value(generated_value: str) -> str:
    """Return a generated catalogue runtime value.

    Args:
        generated_value: Catalogue generation strategy.

    Returns:
        Runtime value generated for one execution-prepared request.

    Raises:
        ValueError: If the catalogue declares an unsupported strategy.
    """
    if generated_value == "uuid4":
        return str(uuid.uuid4())
    if generated_value == "uuid4-hex":
        return uuid.uuid4().hex
    if generated_value == "invalid-resource-id":
        return f"invalid-{uuid.uuid4()}"
    if generated_value == "invalid-access-token":
        return f"invalid-{secrets.token_urlsafe(24)}"
    if generated_value == "next-day-date-time-offset":
        return (
            (datetime.now(UTC) + timedelta(days=1))
            .astimezone(timezone(timedelta(hours=-7)))
            .replace(microsecond=0)
            .isoformat()
        )
    if generated_value == "next-day-date-time-utc":
        return (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    raise ValueError(f"Unsupported generated runtime value strategy '{generated_value}'")


def _catalogue_request_header_value(
    input_id: str,
    *,
    runtime_inputs: Mapping[str, JsonValue],
    requirements: Mapping[str, RuntimeInputRequirement],
) -> str | None:
    """Resolve a catalogue-declared outbound header value.

    Args:
        input_id: Runtime input id backing the outbound header.
        runtime_inputs: Original plan-spec runtime input mapping.
        requirements: Runtime input requirements keyed by input id.

    Returns:
        Header value to send, or ``None`` when an optional input is omitted.

    Raises:
        ValueError: If a required header input is missing or if the input value is
            not a string.
    """
    value = runtime_inputs.get(input_id)
    if value is None or (isinstance(value, str) and not value.strip()):
        requirement = requirements.get(input_id)
        if requirement is not None and requirement.required:
            return _required_runtime_string(runtime_inputs, input_id)
        return None
    if not isinstance(value, str):
        raise ValueError(f"Runtime input '{input_id}' must be a string")
    return value.strip()


def _optional_access_token(
    request_step: CatalogueRequestStep,
    *,
    runtime_inputs: Mapping[str, JsonValue],
    runtime_input_base_dir: Path,
    requirements: Mapping[str, RuntimeInputRequirement],
) -> str | None:
    """Resolve a bearer token for a request when the catalogue asks for one.

    Args:
        request_step: Catalogue request skeleton.
        runtime_inputs: Original plan-spec runtime input mapping.
        runtime_input_base_dir: Directory used to resolve file references.
        requirements: Runtime input requirements keyed by input id.

    Returns:
        Bearer token string, or ``None`` when the request does not require one.

    Raises:
        ValueError: If the token input is present but cannot be resolved.
    """
    for input_id in request_step.runtime_input_refs:
        if input_id not in {"accessToken", "accessTokenRef", "invalidAccessToken"}:
            continue
        requirement = requirements.get(input_id)
        if requirement is not None and requirement.input_type == "file_reference":
            return _read_runtime_file_text(runtime_inputs, input_id, root=runtime_input_base_dir)
        return _required_runtime_string(runtime_inputs, input_id)
    return None


def _catalogue_request_body(
    request_step: CatalogueRequestStep,
    *,
    runtime_inputs: Mapping[str, JsonValue],
    runtime_input_base_dir: Path,
    requirements: Mapping[str, RuntimeInputRequirement],
    generated_runtime_values: Mapping[str, str],
) -> JsonBody | FormBody | None:
    """Build a JSON request body from a catalogue runtime file reference.

    Args:
        request_step: Catalogue request skeleton.
        runtime_inputs: Original plan-spec runtime input mapping.
        runtime_input_base_dir: Directory used to resolve file references.
        requirements: Runtime input requirements keyed by input id.
        generated_runtime_values: Generated runtime values keyed by catalogue
            data id for the current request step.

    Returns:
        JSON body for methods that send a body, or ``None`` when no body
        reference is declared.

    Raises:
        ValueError: If a request body file reference is invalid.
    """
    if request_step.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    if request_step.step_id == _AIS_CONSENT_CREATE_STEP_ID:
        return JsonBody(value=_AIS_BASIC_ACCOUNT_ACCESS_CONSENT_BODY)
    if request_step.step_id == _AIS_ACCOUNT_ACCESS_TOKEN_STEP_ID:
        return FormBody(
            fields={
                "grant_type": "authorization_code",
                "code": f"${{steps.{_ais_profile_authorization_step_id('basic')}.response.body.code}}",
                "redirect_uri": "${config.oauth.redirectUri}",
                "client_id": "${config.oauth.clientId}",
            }
        )
    if request_step.body_template is not None:
        return JsonBody(
            value=_resolve_catalogue_template_values(
                request_step.body_template,
                generated_runtime_values=generated_runtime_values,
                runtime_inputs=runtime_inputs,
            )
        )
    for input_id in request_step.runtime_input_refs:
        requirement = requirements.get(input_id)
        if requirement is None or requirement.input_type != "file_reference" or "request" not in input_id.lower():
            continue
        return JsonBody(value=_read_runtime_json_file(runtime_inputs, input_id, root=runtime_input_base_dir))
    return None


def _resolve_catalogue_template_values(
    value: JsonValue,
    *,
    generated_runtime_values: Mapping[str, str],
    runtime_inputs: Mapping[str, JsonValue],
) -> JsonValue:
    """Replace catalogue-owned placeholders inside a request template.

    Args:
        value: JSON request template value.
        generated_runtime_values: Generated values keyed by catalogue data id.
        runtime_inputs: Runtime inputs supplied for the selected plan.

    Returns:
        Template value with ``${generated.*}`` and ``${runtime.*}``
        placeholders replaced.

    Raises:
        ValueError: If a template references an unavailable value.
    """
    if isinstance(value, str):
        return _resolve_catalogue_template_string(
            value,
            generated_runtime_values=generated_runtime_values,
            runtime_inputs=runtime_inputs,
        )
    if isinstance(value, list):
        return [
            _resolve_catalogue_template_values(
                item,
                generated_runtime_values=generated_runtime_values,
                runtime_inputs=runtime_inputs,
            )
            for item in value
        ]
    if isinstance(value, dict):
        return {
            str(key): _resolve_catalogue_template_values(
                item,
                generated_runtime_values=generated_runtime_values,
                runtime_inputs=runtime_inputs,
            )
            for key, item in value.items()
        }
    return value


def _resolve_catalogue_template_string(
    value: str,
    *,
    generated_runtime_values: Mapping[str, str],
    runtime_inputs: Mapping[str, JsonValue],
) -> str:
    """Replace catalogue-owned placeholders in one string.

    Args:
        value: String that may contain catalogue-owned placeholders.
        generated_runtime_values: Generated values keyed by catalogue data id.
        runtime_inputs: Runtime inputs supplied for the selected plan.

    Returns:
        String with generated and runtime placeholders replaced.

    Raises:
        ValueError: If a placeholder references an unavailable value.
    """

    def replace_generated(match: re.Match[str]) -> str:
        """Return one generated value for a regex placeholder match.

        Args:
            match: Regex match containing the generated value id.

        Returns:
            Generated value for the matched id.

        Raises:
            ValueError: If the generated value id was not declared.
        """
        value_id = match.group(1)
        generated_value = generated_runtime_values.get(value_id)
        if generated_value is None:
            raise ValueError(f"Generated runtime value '{value_id}' is not declared for this request step")
        return generated_value

    def replace_runtime(match: re.Match[str]) -> str:
        """Return one runtime input for a regex placeholder match.

        Args:
            match: Regex match containing the runtime input id.

        Returns:
            Runtime input value for the matched id.

        Raises:
            ValueError: If the runtime input is missing or not a string.
        """
        input_id = match.group(1)
        runtime_value = runtime_inputs.get(input_id)
        if runtime_value is None or (isinstance(runtime_value, str) and not runtime_value.strip()):
            raise ValueError(f"Runtime input '{input_id}' is required for this request template")
        if not isinstance(runtime_value, str):
            raise ValueError(f"Runtime input '{input_id}' must be a string")
        return runtime_value.strip()

    generated_resolved = _CATALOGUE_GENERATED_VALUE_PATTERN.sub(replace_generated, value)
    return _CATALOGUE_RUNTIME_VALUE_PATTERN.sub(replace_runtime, generated_resolved)


def _read_runtime_json_file(
    runtime_inputs: Mapping[str, JsonValue],
    input_id: str,
    *,
    root: Path,
) -> JsonValue:
    """Read a JSON runtime file reference under the plan-spec directory.

    Args:
        runtime_inputs: Original plan-spec runtime input mapping.
        input_id: Runtime input id that contains the file reference.
        root: Directory that relative references are resolved under.

    Returns:
        Decoded JSON value from the referenced file.

    Raises:
        ValueError: If the reference is missing, escapes ``root``, or contains
            invalid JSON.
    """
    path = _runtime_file_path(runtime_inputs, input_id, root=root)
    try:
        return cast("JsonValue", json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as error:
        raise ValueError(f"Runtime input file '{input_id}' must contain valid JSON: {error.msg}") from error
    except OSError as error:
        raise ValueError(f"Unable to read runtime input file '{input_id}': {error}") from error


def _read_runtime_file_text(
    runtime_inputs: Mapping[str, JsonValue],
    input_id: str,
    *,
    root: Path,
) -> str:
    """Read a text runtime file reference under the plan-spec directory.

    Args:
        runtime_inputs: Original plan-spec runtime input mapping.
        input_id: Runtime input id that contains the file reference.
        root: Directory that relative references are resolved under.

    Returns:
        Stripped text content from the referenced file.

    Raises:
        ValueError: If the reference is missing, escapes ``root``, cannot be
            read, or is empty.
    """
    path = _runtime_file_path(runtime_inputs, input_id, root=root)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ValueError(f"Unable to read runtime input file '{input_id}': {error}") from error
    if not value:
        raise ValueError(f"Runtime input file '{input_id}' must not be empty")
    return value


def _runtime_file_path(runtime_inputs: Mapping[str, JsonValue], input_id: str, *, root: Path) -> Path:
    """Resolve a runtime file-reference path under a trusted root.

    Args:
        runtime_inputs: Original plan-spec runtime input mapping.
        input_id: Runtime input id that contains the file reference.
        root: Directory that relative references are resolved under.

    Returns:
        Resolved file path.

    Raises:
        ValueError: If the runtime value is not a non-empty string or escapes
            the supplied root.
    """
    raw_value = _required_runtime_string(runtime_inputs, input_id)
    raw_path = Path(raw_value)
    resolved_root = root.resolve()
    resolved_path = raw_path.resolve() if raw_path.is_absolute() else (resolved_root / raw_path).resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise ValueError(f"Runtime input file '{input_id}' must resolve inside the plan-spec directory")
    return resolved_path


def _required_runtime_string(runtime_inputs: Mapping[str, JsonValue], input_id: str) -> str:
    """Extract a required runtime string value.

    Args:
        runtime_inputs: Original plan-spec runtime input mapping.
        input_id: Runtime input id to read.

    Returns:
        Non-empty string runtime value.

    Raises:
        ValueError: If the value is absent or not a non-empty string.
    """
    value = runtime_inputs.get(input_id)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Runtime input '{input_id}' must be a non-empty string")
    return value.strip()


def _catalogue_assertion_to_manifest_assertion(
    assertion: CatalogueAssertion,
    *,
    runtime_inputs: Mapping[str, JsonValue],
    generated_header_values: Mapping[str, str],
) -> ManifestAssertion:
    """Convert a catalogue assertion into the executor's assertion model.

    Args:
        assertion: Catalogue assertion to convert.
        runtime_inputs: Runtime inputs used to resolve selected-run assertion
            values for playback checks.
        generated_header_values: Generated request-header values keyed by
            lower-case header name for playback checks.

    Returns:
        Manifest-compatible assertion dataclass.

    Raises:
        ValueError: If the assertion rule cannot be mapped to an executable
            assertion shape.
    """
    if assertion.kind == "http_status":
        expected_one_of = assertion.rule.get("expectedOneOf")
        if isinstance(expected_one_of, list) and expected_one_of:
            parsed_statuses: list[int] = []
            for status_code in expected_one_of:
                if not isinstance(status_code, int) or isinstance(status_code, bool):
                    raise ValueError(
                        f"Catalogue assertion '{assertion.assertion_id}' requires integer rule.expectedOneOf values"
                    )
                parsed_statuses.append(status_code)
            return HttpStatusAssertion(type="http_status", expected_one_of=tuple(parsed_statuses))
        expected = assertion.rule.get("expected")
        if not isinstance(expected, int) or isinstance(expected, bool):
            raise ValueError(f"Catalogue assertion '{assertion.assertion_id}' requires integer rule.expected")
        return HttpStatusAssertion(type="http_status", expected=expected)
    if assertion.kind == "json_field":
        return _catalogue_json_field_assertion(assertion)
    if assertion.kind == "header":
        return _catalogue_header_assertion(
            assertion,
            runtime_inputs=runtime_inputs,
            generated_header_values=generated_header_values,
        )
    return _catalogue_response_schema_assertion(assertion)


def _catalogue_json_field_assertion(assertion: CatalogueAssertion) -> JsonFieldAssertion:
    """Convert a catalogue JSON-field assertion.

    Args:
        assertion: Catalogue assertion with ``kind == "json_field"``.

    Returns:
        Manifest-compatible JSON-field assertion.

    Raises:
        ValueError: If required rule keys are missing or invalid.
    """
    path = assertion.rule.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError(f"Catalogue assertion '{assertion.assertion_id}' requires string rule.path")
    if assertion.rule.get("present") is True or assertion.rule.get("expected") == "present":
        return JsonFieldAssertion(type="json_field", path=path, rule="required")
    if "expected" in assertion.rule:
        return JsonFieldAssertion(type="json_field", path=path, rule="equals", value=assertion.rule["expected"])
    rule = assertion.rule.get("rule")
    supported_simple_rules = {
        "required",
        "permission_filtered",
        "all_items_have_field",
        "all_items_absent_fields",
        "https_url",
        "array",
        "absent",
        "string",
        "number",
        "boolean",
        "object",
        "non_empty_array",
    }
    if isinstance(rule, str) and rule in supported_simple_rules:
        if rule == "permission_filtered":
            return JsonFieldAssertion(type="json_field", path=path, rule="required")
        if rule == "all_items_absent_fields":
            fields = assertion.rule.get("fields")
            if not isinstance(fields, list) or not fields or not all(isinstance(field, str) for field in fields):
                raise ValueError(
                    f"Catalogue assertion '{assertion.assertion_id}' requires non-empty string array rule.fields"
                )
            field_names = tuple(str(field) for field in fields)
            return JsonFieldAssertion(
                type="json_field",
                path=path,
                rule=cast(JsonFieldRule, rule),
                fields=field_names,
            )
        if rule == "all_items_have_field":
            field = assertion.rule.get("field")
            if not isinstance(field, str) or not field:
                raise ValueError(f"Catalogue assertion '{assertion.assertion_id}' requires string rule.field")
            return JsonFieldAssertion(
                type="json_field",
                path=path,
                rule=cast(JsonFieldRule, rule),
                field=field,
            )
        return JsonFieldAssertion(type="json_field", path=path, rule=cast(JsonFieldRule, rule))
    raise ValueError(f"Catalogue assertion '{assertion.assertion_id}' cannot be mapped to a JSON-field rule")


def _catalogue_header_assertion(
    assertion: CatalogueAssertion,
    *,
    runtime_inputs: Mapping[str, JsonValue],
    generated_header_values: Mapping[str, str],
) -> HeaderAssertion:
    """Convert a catalogue header assertion.

    Args:
        assertion: Catalogue assertion with ``kind == "header"``.
        runtime_inputs: Runtime inputs used to resolve expected playback values.
        generated_header_values: Generated request-header values keyed by
            lower-case header name for playback checks.

    Returns:
        Manifest-compatible header assertion.

    Raises:
        ValueError: If required rule keys are missing or invalid.
    """
    name = assertion.rule.get("name", assertion.rule.get("header"))
    if not isinstance(name, str) or not name:
        raise ValueError(f"Catalogue assertion '{assertion.assertion_id}' requires rule.name or rule.header")
    if (
        assertion.rule.get("required") is True
        or assertion.rule.get("presence") == "required"
        or assertion.rule.get("rule") == "present"
    ):
        return HeaderAssertion(type="header", name=name, rule="present")
    if assertion.rule.get("rule") == "playback":
        return HeaderAssertion(
            type="header",
            name=name,
            rule="equals",
            value=_catalogue_header_playback_value(
                name,
                runtime_inputs=runtime_inputs,
                generated_header_values=generated_header_values,
            ),
        )
    contains = assertion.rule.get("contains")
    if isinstance(contains, str) and contains:
        return HeaderAssertion(type="header", name=name, rule="contains", value=contains)
    expected = assertion.rule.get("expected")
    if isinstance(expected, str) and expected:
        return HeaderAssertion(type="header", name=name, rule="equals", value=expected)
    raise ValueError(f"Catalogue assertion '{assertion.assertion_id}' cannot be mapped to a header rule")


def _catalogue_header_playback_value(
    name: str,
    *,
    runtime_inputs: Mapping[str, JsonValue],
    generated_header_values: Mapping[str, str],
) -> str:
    """Return the runtime input value expected in a response-header playback check.

    Args:
        name: Header name whose response value should echo the request.
        runtime_inputs: Runtime inputs supplied for the selected plan.
        generated_header_values: Generated request-header values keyed by
            lower-case header name.

    Returns:
        Expected header value supplied by the matching runtime input.

    Raises:
        ValueError: If the header cannot be mapped to a runtime input or the
            required input is missing.
    """
    generated_value = generated_header_values.get(name.lower())
    if generated_value is not None:
        return generated_value
    input_id = _catalogue_header_runtime_input_id(name)
    if input_id is None:
        raise ValueError(f"Catalogue header playback is unsupported for '{name}'")
    return _required_runtime_string(runtime_inputs, input_id)


def _catalogue_header_runtime_input_id(name: str) -> str | None:
    """Return the runtime input id associated with a request header name.

    Args:
        name: HTTP header name from a catalogue assertion.

    Returns:
        Runtime input id when the header has a selected-run input, otherwise
        ``None``.
    """
    normalized_name = name.lower()
    if normalized_name == "x-fapi-interaction-id":
        return "xFapiInteractionId"
    return None


def _catalogue_response_schema_assertion(assertion: CatalogueAssertion) -> ResponseSchemaAssertion:
    """Convert a catalogue response-schema assertion.

    Args:
        assertion: Catalogue assertion with ``kind == "response_schema"``.

    Returns:
        Manifest-compatible response-schema assertion.

    Raises:
        ValueError: If required schema rule keys are missing.
    """
    source = assertion.rule.get("source")
    document = assertion.rule.get("document")
    schema_ref = assertion.rule.get("schemaRef")
    body_path = assertion.rule.get("bodyPath")
    if source != "bundled_openapi" or not isinstance(document, str):
        raise ValueError(f"Catalogue assertion '{assertion.assertion_id}' requires bundled OpenAPI schema metadata")
    return ResponseSchemaAssertion(
        type="response_schema",
        source="bundled_openapi",
        document=document,
        schema_ref=schema_ref if isinstance(schema_ref, str) else None,
        body_path=body_path if isinstance(body_path, str) else None,
    )


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


class _ResponseSignatureJwksCache:
    """Fetch and cache discovery JWKS for response signature validation."""

    def __init__(self, client: httpx.Client) -> None:
        """Initialise the JWKS cache.

        Args:
            client: HTTP client used for discovery and JWKS requests.
        """
        self._client = client
        self._jwks: JsonObject | None = None
        self._lock = threading.Lock()

    def get(self, runtime_config: RuntimeConfig | None) -> JsonObject:
        """Return the cached JWKS, fetching it from discovery when needed.

        Args:
            runtime_config: Runtime config containing the discovery URL.

        Returns:
            JWKS JSON object with a ``keys`` array.

        Raises:
            ValueError: If runtime config or discovery metadata is missing or
                unsafe.
            JsonHttpClientError: If discovery or JWKS HTTP retrieval fails.
        """
        if self._jwks is not None:
            return self._jwks
        with self._lock:
            if self._jwks is None:
                self._jwks = _fetch_response_signature_jwks(self._client, runtime_config)
        return self._jwks


def _fetch_response_signature_jwks(client: httpx.Client, runtime_config: RuntimeConfig | None) -> JsonObject:
    """Fetch the JWKS advertised by the configured discovery document.

    Args:
        client: HTTP client used for discovery and JWKS requests.
        runtime_config: Runtime config containing ``discoveryUrl``.

    Returns:
        JWKS JSON object.

    Raises:
        ValueError: If runtime config, ``jwks_uri``, or the JWKS shape is
            invalid.
        JsonHttpClientError: If discovery or JWKS retrieval fails.
    """
    if runtime_config is None or runtime_config.discovery_url is None:
        raise ValueError("Response signature validation requires runtime config discoveryUrl")
    discovery_response = send_json(client, "GET", runtime_config.discovery_url)
    jwks_uri = discovery_response.body.get("jwks_uri")
    if not isinstance(jwks_uri, str) or not jwks_uri.strip():
        raise ValueError("OpenID discovery response must contain jwks_uri for response signature validation")
    validate_https_url(jwks_uri.strip(), label="discovery jwks_uri")
    jwks_response = send_json(client, "GET", jwks_uri.strip())
    keys = jwks_response.body.get("keys")
    if not isinstance(keys, list):
        raise ValueError("JWKS response must contain a keys array")
    return dict(jwks_response.body)


def _run_manifest_v1(
    manifest: Manifest,
    *,
    client: httpx.Client,
    execution_logger: ExecutionLogger,
    plan: TestPlan,
    run_id: str,
    auth_session_store: AuthSessionStore,
    runtime_config: RuntimeConfig | None,
    fapi_signing_config: FapiSigningConfig | None,
    mtls_client_configured: bool,
    approved_release_policy: ApprovedReleasePolicy | None,
    compiled_plan: CompiledTestPlan | None = None,
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
        mtls_client_configured: Whether the shared HTTP client has mTLS
            client credentials configured for ``tls_client_auth`` steps.
        approved_release_policy: Optional approved-release policy used by the
            generated report's certification self-assessment.
        compiled_plan: Optional compiled catalogue plan whose traceability
            should be embedded in the result.

    Returns:
        Smoke-check result with one entry per executed (selected) step.
    """
    started_at = datetime.now(UTC)
    schedule = build_execution_schedule(manifest, plan)
    steps: list[StepResult] = []
    context = ExecutionContext(config=runtime_config)
    fapi_signing_service = _LazyFapiSigningService(fapi_signing_config)
    response_signature_jwks_cache = _ResponseSignatureJwksCache(client)

    # Emit one ``step-deselected`` event per deselected step before any
    # ``step-started`` event. Done up-front (rather than interleaved with
    # execution) so a log consumer can read the plan-vs-manifest delta
    # without scanning the entire run.
    for entry in plan.entries:
        if not entry.selected:
            execution_logger.emit(
                "step-deselected",
                step_id=entry.step_id,
                payload={"mandatory": entry.mandatory},
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
        mtls_client_configured=mtls_client_configured,
        response_signature_jwks_cache=response_signature_jwks_cache,
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
        mtls_client_configured=mtls_client_configured,
        response_signature_jwks_cache=response_signature_jwks_cache,
    )
    steps.extend(execution_steps)
    if compiled_plan is not None:
        steps = _attach_catalogue_evidence_to_steps(steps, compiled_plan)

    return build_smoke_check_result(
        steps,
        started_at=started_at,
        plan=plan,
        approved_release_policy=approved_release_policy,
        certification_coverage=manifest.certification_coverage,
        compiled_plan=compiled_plan,
        non_certifying_reasons=(compiled_plan.traceability.non_certifying_reasons if compiled_plan is not None else ()),
    )


def _attach_catalogue_evidence_to_steps(
    steps: list[StepResult],
    compiled_plan: CompiledTestPlan,
) -> list[StepResult]:
    """Attach catalogue role and compliance scope to step result evidence.

    Args:
        steps: Step results emitted by the internal HTTP executor.
        compiled_plan: Compiled catalogue plan that owns the executed request
            steps.

    Returns:
        Step results with ``details.catalogue`` populated for catalogue-backed
        request steps.
    """
    request_metadata: dict[str, JsonObject] = {}
    for test_case in compiled_plan.test_cases:
        for request_step in test_case.request_steps:
            request_metadata[request_step.step_id] = {
                "testCaseId": test_case.test_case_id,
                "requestStepId": request_step.step_id,
                "role": test_case.role,
                "complianceScope": list(test_case.compliance_scope),
            }

    enriched_steps: list[StepResult] = []
    for step in steps:
        metadata = request_metadata.get(step.name)
        if metadata is None:
            enriched_steps.append(step)
            continue
        details = dict(step.details)
        details["catalogue"] = metadata
        enriched_steps.append(replace(step, details=details))
    return enriched_steps


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
    mtls_client_configured: bool,
    response_signature_jwks_cache: _ResponseSignatureJwksCache,
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
        mtls_client_configured: Whether the shared HTTP client has mTLS
            client credentials configured for ``tls_client_auth`` steps.
        response_signature_jwks_cache: Per-run cache for response JWS
            verification keys.

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
            mtls_client_configured=mtls_client_configured,
            response_signature_jwks_cache=response_signature_jwks_cache,
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
    mtls_client_configured: bool,
    response_signature_jwks_cache: _ResponseSignatureJwksCache,
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
        mtls_client_configured: Whether the shared HTTP client has mTLS
            client credentials configured for ``tls_client_auth`` steps.
        response_signature_jwks_cache: Per-run cache for response JWS
            verification keys.

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
                    mtls_client_configured,
                    response_signature_jwks_cache,
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
    mtls_client_configured: bool,
    response_signature_jwks_cache: _ResponseSignatureJwksCache,
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
        mtls_client_configured: Whether the shared HTTP client has mTLS
            client credentials configured for ``tls_client_auth`` steps.
        response_signature_jwks_cache: Per-run cache for response JWS
            verification keys.

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
        mtls_client_configured=mtls_client_configured,
        response_signature_jwks_cache=response_signature_jwks_cache,
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
    mtls_client_configured: bool,
    response_signature_jwks_cache: _ResponseSignatureJwksCache,
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
        mtls_client_configured: Whether the shared HTTP client has mTLS
            client credentials configured for ``tls_client_auth`` steps.
        response_signature_jwks_cache: Per-run cache for response JWS
            verification keys.

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
            mtls_client_configured=mtls_client_configured,
            response_signature_jwks_cache=response_signature_jwks_cache,
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

    deadline = clock() + PSU_AUTHORIZATION_TIMEOUT_SECONDS
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
                details={"timeoutSeconds": PSU_AUTHORIZATION_TIMEOUT_SECONDS},
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
            openbanking_intent_id=openbanking_intent_id,
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

    response_evidence: dict[str, JsonValue] = {"statusCode": response.status_code}
    if not 300 <= response.status_code < 400:
        return (
            _attach_evidence(
                StepResult(
                    name=manifest_step.id,
                    status="failed",
                    message=(
                        "PSU authorisation headless request did not return a redirect "
                        f"(got HTTP {response.status_code})"
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
    mtls_client_configured: bool = False,
    response_signature_jwks_cache: _ResponseSignatureJwksCache | None = None,
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
        mtls_client_configured: Whether the shared HTTP client has mTLS
            client credentials configured.
        response_signature_jwks_cache: Optional per-run cache for response JWS
            verification keys. A cache is created when omitted for direct unit
            callers.

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
        mtls_client_configured=mtls_client_configured,
        response_signature_jwks_cache=response_signature_jwks_cache or _ResponseSignatureJwksCache(client),
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
    mtls_client_configured: bool,
    response_signature_jwks_cache: _ResponseSignatureJwksCache,
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
        mtls_client_configured: Whether the shared HTTP client has mTLS
            client credentials configured.
        response_signature_jwks_cache: Per-run cache for response JWS
            verification keys.

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
            allow_non_json_response=not _assertions_require_json_body(manifest_step.assertions),
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
        execution_logger.emit(
            "application-error",
            step_id=manifest_step.id,
            payload={
                "message": str(error),
                **({"statusCode": error.status_code} if error.status_code is not None else {}),
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

    try:
        response_signature_evidence = _validate_response_signature_if_required(
            manifest_step=manifest_step,
            response=response,
            context=context,
            response_signature_jwks_cache=response_signature_jwks_cache,
        )
    except (JsonHttpClientError, HttpsUrlValidationError, ResponseSignatureValidationError, ValueError) as error:
        execution_logger.emit(
            "response-signature-invalid",
            step_id=manifest_step.id,
            payload={"message": str(error)},
        )
        response_evidence["responseSignature"] = {"status": "failed", "message": str(error)}
        return (
            _attach_evidence(
                StepResult(
                    name=manifest_step.id,
                    status="failed",
                    message=f"Response signature validation failed: {error}",
                    url=resolved_url,
                    status_code=response.status_code,
                ),
                request_evidence=request_evidence,
                response_evidence=response_evidence,
            ),
            new_context,
        )
    if response_signature_evidence is not None:
        execution_logger.emit(
            "response-signature-validated",
            step_id=manifest_step.id,
            payload=response_signature_evidence,
        )
        response_evidence["responseSignature"] = {"status": "passed", **response_signature_evidence}

    # Evaluate assertions
    step_result = _build_assertion_step(
        name=manifest_step.id,
        success_message=f"{manifest_step.name} passed",
        failure_message=f"{manifest_step.name} failed",
        response=response,
        assertions=manifest_step.assertions,
        warning=manifest_step.warning,
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


def _assertions_require_json_body(assertions: tuple[ManifestAssertion, ...]) -> bool:
    """Return whether any assertion needs a parsed JSON response body.

    Args:
        assertions: Manifest assertions attached to the current request step.

    Returns:
        ``True`` when JSON-field or schema assertions are present; ``False``
        when the step can be evaluated using only status and headers.
    """
    return any(isinstance(assertion, JsonFieldAssertion | ResponseSchemaAssertion) for assertion in assertions)


def _validate_response_signature_if_required(
    *,
    manifest_step: ManifestStep,
    response: JsonHttpResponse,
    context: ExecutionContext,
    response_signature_jwks_cache: _ResponseSignatureJwksCache,
) -> JsonObject | None:
    """Validate a required response detached JWS and return evidence.

    Args:
        manifest_step: Executed HTTP step whose policy may require validation.
        response: JSON HTTP response received for the step.
        context: Execution context containing runtime config.
        response_signature_jwks_cache: Per-run cache for discovery JWKS.

    Returns:
        Non-secret response-signature evidence when validation was required and
        passed, otherwise ``None``.

    Raises:
        ValueError: If the response-signature source is unsupported.
        JsonHttpClientError: If discovery or JWKS retrieval fails.
        HttpsUrlValidationError: If discovery advertises an unsafe JWKS URL.
        ResponseSignatureValidationError: If the signature itself is invalid.
    """
    policy = manifest_step.response_signature_policy
    if policy is None:
        return None
    if policy.source != "discovery-jwks":
        raise ValueError("Unsupported response signature validation source")
    signature = response.headers.get("x-jws-signature")
    if signature is None or not signature.strip():
        raise ResponseSignatureValidationError("x-jws-signature header is missing")
    validation = validate_ob_response_signature(
        signature=signature,
        payload=response.body_bytes,
        jwks=response_signature_jwks_cache.get(context.config),
    )
    return validation.to_json_object()


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
    if fapi_signing_config is None:
        raise ValueError("Detached request signing requires fapiSigning configuration")
    if fapi_signing_service is None:
        raise ValueError("Detached request signing requires fapiSigning configuration")
    if not _requires_ob_detached_jws(manifest_step=manifest_step, resolved_url=resolved_url):
        raise ValueError("Detached request signing is only supported for AIS consent, PIS, and VRP write requests")
    if resolved_json_body is None:
        raise ValueError("Detached request signing requires a JSON request body")

    serialized_json_body = _serialize_json_request_body(resolved_json_body)
    signing_service = fapi_signing_service.get()
    if signing_service is None:
        raise ValueError("Detached request signing requires fapiSigning configuration")
    detached_signature = signing_service.sign_detached_json_payload(
        serialized_json_body,
        profile=_detached_jws_profile_for_request(resolved_url),
        omit_protected_headers=manifest_step.request.detached_jws.omit_protected_headers,
    )
    validate_header_value(
        detached_signature,
        location=f"step '{manifest_step.id}' generated header x-jws-signature",
    )

    signed_headers = dict(resolved_headers) if resolved_headers is not None else {}
    signed_headers["x-jws-signature"] = detached_signature
    return signed_headers, serialized_json_body


def _detached_jws_profile_for_request(resolved_url: str) -> OpenBankingDetachedJwsProfile:
    """Return the Open Banking detached-JWS profile for one request URL.

    Args:
        resolved_url: Fully resolved request URL.

    Returns:
        PIS v4 write requests use the v3.1.4+/v4 profile; existing AIS consent
        signing keeps the legacy unencoded-payload profile.
    """
    normalized_path = _normalize_url_path_for_match(urlsplit(resolved_url).path)
    if normalized_path.startswith(_OB_PIS_PATH_PREFIX) or _is_ob_vrp_path(normalized_path):
        return "ob-v3.1.4+"
    return "legacy-b64-false"


def _is_ob_vrp_path(normalized_path: str) -> bool:
    """Return whether a normalized path targets an Open Banking VRP resource.

    Args:
        normalized_path: Canonical absolute URL path.

    Returns:
        ``True`` when the path targets domestic VRP consent/payment resources,
        either directly from the generated catalogue path or under a versioned
        ``/pisp`` base path.
    """
    for resource_prefix in _OB_VRP_RESOURCE_PATH_PREFIXES:
        if normalized_path == resource_prefix or normalized_path.startswith(f"{resource_prefix}/"):
            return True
        versioned_pisp_prefix = f"/pisp{resource_prefix}"
        if normalized_path.endswith(versioned_pisp_prefix) or f"{versioned_pisp_prefix}/" in normalized_path:
            return True
    return False


def _requires_ob_detached_jws(*, manifest_step: ManifestStep, resolved_url: str) -> bool:
    """Return whether a step should carry an Open Banking detached JWS.

    Args:
        manifest_step: Parsed manifest step being executed.
        resolved_url: Fully resolved request URL.

    Returns:
        ``True`` when the step targets the AIS account-access-consents endpoint,
        a PIS endpoint, or a VRP endpoint and is an eligible write method,
        otherwise ``False``.
    """
    if manifest_step.request.method not in {"POST", "PUT", "PATCH"}:
        return False
    normalized_path = _normalize_url_path_for_match(urlsplit(resolved_url).path)
    return (
        normalized_path == _OB_ACCOUNT_ACCESS_CONSENTS_PATH
        or normalized_path.startswith(_OB_PIS_PATH_PREFIX)
        or _is_ob_vrp_path(normalized_path)
    )


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
    client: httpx.Client,
    execution_logger: ExecutionLogger,
    run_id: str,
    auth_session_store: AuthSessionStore,
    runtime_config: RuntimeConfig | None,
    approved_release_policy: ApprovedReleasePolicy | None,
) -> SmokeCheckResult:
    """Execute a v0 manifest preserving original skip-on-fail semantics.

    In v0, follow-up steps are only executed when the primary step passes.
    This differs from v1 where all steps run regardless of earlier assertion
    outcomes. The method desugars each test into v1 steps but gates follow-up
    execution on primary step success.

    Args:
        manifest: Parsed v0 manifest containing tests with optional followUp.
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
        approved_release_policy: Optional approved-release policy used by the
            generated report's certification self-assessment.

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
        steps,
        started_at=started_at,
        approved_release_policy=approved_release_policy,
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
