"""Execute parsed v0/v1 manifests against JSON HTTP endpoints."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
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
from conformance.context import (
    ExecutionContext,
    MissingPredecessorResponseError,
    PlaceholderResolutionError,
    RequestRecord,
    ResponseRecord,
    RuntimeConfig,
    record_step,
    resolve_in_structure,
    resolve_placeholders,
)
from conformance.execution_log import ExecutionLogger, NullExecutionLogger, new_run_id
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
from conformance.url_validation import HttpsUrlValidationError, validate_https_url

_MAX_EXECUTION_GROUP_WORKERS = 32
"""Upper bound for concurrent v1 execution-group workers.

The manifest is participant-provided input, so group count can be large. This
cap prevents unbounded thread creation while preserving queue-based execution
for additional groups.
"""

_OB_ACCOUNT_ACCESS_CONSENTS_PATH = "/open-banking/v4.0/aisp/account-access-consents"
"""Open Banking AIS consent-creation path requiring detached JWS support."""


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
    """Attach masked request/response evidence to a non-PASS step result.

    Implements the PRD outcome rule: *"Full request and response captured on
    FAIL, WARN, and SKIPPED. Summary only on PASS."* Sensitive credential
    fields and headers are masked via :mod:`conformance.masking` before being
    embedded so reports remain safe to share with OBL.

    PASS steps are returned unchanged: the spec keeps successful runs lean,
    and exposing full payloads on every passing API call would bloat reports
    and expand the surface area for accidental disclosure.

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
        available, ``details["response"]`` populated. PASS results are
        returned unchanged.
    """
    if step.status == "passed":
        return step
    new_details: dict[str, JsonValue] = dict(step.details)
    new_details["request"] = dict(request_evidence)
    if response_evidence is not None:
        new_details["response"] = dict(response_evidence)
    return replace(step, details=new_details)


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
    run_started_payload: JsonObject = {"environment": environment, "schemaVersion": manifest.schema_version}
    if suite_metadata is not None:
        run_started_payload["suite"] = suite_metadata.to_json_object()
    logger_sink.emit("run-started", payload=run_started_payload)
    try:
        if manifest.schema_version == "v1":
            effective_plan = plan if plan is not None else TestPlan.default_plan_from_manifest(manifest)
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
                mtls_client_configured=mtls_client_configured,
                suite_metadata=suite_metadata,
                approved_release_policy=approved_release_policy,
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
    mtls_client_configured: bool,
    suite_metadata: SuiteMetadata | None,
    approved_release_policy: ApprovedReleasePolicy | None,
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
        mtls_client_configured: Whether the shared HTTP client has mTLS
            client credentials configured for ``tls_client_auth`` steps.
        suite_metadata: Optional catalog metadata to embed in the result for
            config-resolved suite runs.
        approved_release_policy: Optional approved-release policy used by the
            generated report's certification self-assessment.

    Returns:
        Smoke-check result with one entry per executed (selected) step.
    """
    started_at = datetime.now(UTC)
    schedule = build_execution_schedule(manifest, plan)
    steps: list[StepResult] = []
    context = ExecutionContext(config=runtime_config)

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
    resolved_url: str,
    mtls_client_configured: bool,
) -> dict[str, str] | None:
    """Apply runtime FAPI token-endpoint auth to a resolved form request.

    Args:
        manifest_step: Parsed HTTP step that may declare token-endpoint auth.
        resolved_form_body: Placeholder-resolved form fields, or ``None`` when
            the step is not form-encoded.
        fapi_signing_config: Optional validated FAPI signing configuration.
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
        signing_service = FapiSigningService(
            signing_config=fapi_signing_config,
            signing_credentials=load_signing_credentials(fapi_signing_config),
        )
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
        clock: Optional monotonic clock used for deadline checks. Defaults
            to :func:`time.monotonic`; injectable for tests.
        sleep: Optional sleep function used between polls. Defaults to
            :func:`time.sleep`; injectable for tests.

    Returns:
        A tuple of the PSU step result and updated execution context.
    """
    effective_clock = time.monotonic if clock is None else clock
    effective_sleep = time.sleep if sleep is None else sleep
    execution_logger.emit("step-started", step_id=manifest_step.id)
    step_result, new_context = _execute_v1_psu_step_inner(
        manifest_step,
        context=context,
        client=client,
        run_id=run_id,
        auth_session_store=auth_session_store,
        execution_logger=execution_logger,
        fapi_signing_config=fapi_signing_config,
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
        clock: Monotonic clock used for deadline checks.
        sleep: Sleep function used between polls.

    Returns:
        A tuple of the PSU step result and updated execution context.
    """
    initial_result_url = mask_url_query(manifest_step.authorization_endpoint, SENSITIVE_JSON_KEYS)
    request_evidence: dict[str, JsonValue] = {
        "method": "GET",
        "url": initial_result_url,
    }
    try:
        resolved_authorization_endpoint = resolve_placeholders(manifest_step.authorization_endpoint, context)
        resolved_client_id = resolve_placeholders(manifest_step.client_id, context)
        resolved_redirect_uri = resolve_placeholders(manifest_step.redirect_uri, context)
        resolved_state = resolve_placeholders(manifest_step.state, context) if manifest_step.state is not None else None
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

    resolved_result_url = mask_url_query(resolved_authorization_endpoint, SENSITIVE_JSON_KEYS)
    request_evidence["url"] = resolved_result_url
    try:
        validate_https_url(
            resolved_authorization_endpoint,
            label=f"Step '{manifest_step.id}' authorizationEndpoint",
        )
        validate_https_url(resolved_redirect_uri, label=f"Step '{manifest_step.id}' redirectUri")
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
            authorization_endpoint=resolved_authorization_endpoint,
            client_id=resolved_client_id,
            redirect_uri=resolved_redirect_uri,
            state=session.state,
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
        request_object=resolved_request_object,
    )
    result_url = mask_url_query(authorization_url, SENSITIVE_JSON_KEYS)
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
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    state: str,
) -> str | None:
    """Resolve or generate the PSU request object for one authorisation step.

    Args:
        manifest_step: Parsed PSU authorisation step being executed.
        context: Current execution context used for placeholder resolution.
        fapi_signing_config: Optional validated signing configuration for
            runtime-generated request objects.
        authorization_endpoint: Resolved ASPSP authorisation endpoint URL.
        client_id: Resolved OAuth client identifier.
        redirect_uri: Resolved registered redirect URI.
        state: Registered auth-session state that must be embedded into the
            request object when generated.

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
    return _generate_psu_request_object(
        generated_request_object=request_object,
        fapi_signing_config=fapi_signing_config,
        authorization_endpoint=authorization_endpoint,
        client_id=client_id,
        redirect_uri=redirect_uri,
        response_type=manifest_step.response_type,
        scope=manifest_step.scope,
        state=state,
    )


def _generate_psu_request_object(
    *,
    generated_request_object: GeneratedRequestObject,
    fapi_signing_config: FapiSigningConfig | None,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    response_type: str,
    scope: str,
    state: str,
) -> str:
    """Generate a signed PSU request-object JWT from validated runtime config.

    Args:
        generated_request_object: Typed manifest directive naming the runtime
            request-object source.
        fapi_signing_config: Optional validated signing configuration for the
            current participant run.
        authorization_endpoint: Resolved ASPSP authorisation endpoint URL.
        client_id: Resolved OAuth client identifier.
        redirect_uri: Resolved registered redirect URI.
        response_type: Static OAuth response type from the PSU step.
        scope: Static OAuth scope from the PSU step.
        state: Registered auth-session state that must be embedded into the
            request object.

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

    signing_service = FapiSigningService(
        signing_config=fapi_signing_config,
        signing_credentials=load_signing_credentials(fapi_signing_config),
    )
    signed_request_object = signing_service.sign_request_object(
        RequestObjectSigningInput(
            issuer=fapi_signing_config.client_assertion_issuer,
            audience=authorization_endpoint,
            client_id=client_id,
            redirect_uri=redirect_uri,
            response_type=response_type,
            scope=scope,
            state=state,
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
        mtls_client_configured: Whether the shared HTTP client has mTLS
            client credentials configured.

    Returns:
        A tuple of the step result and the updated execution context.
    """
    del run_id, auth_session_store  # Plain HTTP steps don't consume these directly.
    execution_logger.emit("step-started", step_id=manifest_step.id)
    step_result, new_context = _execute_v1_step_inner(
        manifest_step,
        context=context,
        client=client,
        execution_logger=execution_logger,
        fapi_signing_config=fapi_signing_config,
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
        request_evidence["headers"] = cast("JsonObject", mask_headers(resolved_headers))

    try:
        resolved_form_body = _apply_token_endpoint_auth_policy(
            manifest_step=manifest_step,
            resolved_form_body=resolved_form_body,
            fapi_signing_config=fapi_signing_config,
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
        request_evidence["body"] = mask_json_value(resolved_json_body)
    elif resolved_form_body is not None:
        request_evidence["form"] = dict(mask_form_fields(resolved_form_body))

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

    # Build masked response evidence — attached to non-PASS step results so
    # participants can debug failed assertions without OBL assistance while
    # tokens/credentials in the body remain redacted.
    response_evidence: dict[str, JsonValue] = {
        "statusCode": response.status_code,
        "body": mask_json_value(dict(response.body)),
    }
    if response.headers:
        response_evidence["headers"] = cast("JsonObject", mask_headers(response.headers))
    # Per PRD: response bodies are NOT duplicated into the execution log —
    # they already live in the result-file evidence for non-PASS outcomes.
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
) -> tuple[dict[str, str] | None, bytes | None]:
    """Add a detached JWS header for supported Open Banking JSON requests.

    Args:
        manifest_step: Parsed manifest step being executed.
        resolved_url: Fully resolved request URL.
        resolved_headers: Resolved outbound request headers.
        resolved_json_body: Resolved JSON body, if this is a JSON request.
        fapi_signing_config: Optional validated runtime signing configuration.

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
    if not _requires_ob_detached_jws(manifest_step=manifest_step, resolved_url=resolved_url):
        raise ValueError("Detached request signing is only supported for account-access-consents requests")
    if resolved_json_body is None:
        raise ValueError("Detached request signing requires a JSON request body")

    serialized_json_body = _serialize_json_request_body(resolved_json_body)
    signing_service = FapiSigningService(
        signing_config=fapi_signing_config,
        signing_credentials=load_signing_credentials(fapi_signing_config),
    )
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
        ``True`` when the step targets the AIS account-access-consents
        endpoint and is an eligible write method, otherwise ``False``.
    """
    if manifest_step.request.method not in {"POST", "PUT", "PATCH"}:
        return False
    return _normalize_url_path_for_match(urlsplit(resolved_url).path) == _OB_ACCOUNT_ACCESS_CONSENTS_PATH


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
