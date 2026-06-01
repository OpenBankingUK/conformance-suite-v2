"""Execute parsed v0/v1 manifests against JSON HTTP endpoints."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import httpx

from conformance.assertions import AssertionResult, evaluate_assertion
from conformance.context import (
    ExecutionContext,
    MissingPredecessorResponseError,
    PlaceholderResolutionError,
    RequestRecord,
    ResponseRecord,
    record_step,
    resolve_in_structure,
    resolve_placeholders,
)
from conformance.execution_log import ExecutionLogger, NullExecutionLogger
from conformance.http import JsonHttpClientError, JsonHttpResponse, send_json
from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import (
    FormBody,
    JsonBody,
    Manifest,
    ManifestAssertion,
    ManifestError,
    ManifestRequest,
    ManifestStep,
    ManifestTest,
    validate_header_value,
)
from conformance.masking import mask_form_fields, mask_headers, mask_json_value
from conformance.results import SmokeCheckResult, StepResult, build_smoke_check_result
from conformance.url_validation import HttpsUrlValidationError, validate_https_url


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

    Returns:
        Smoke-check result containing ordered manifest test steps.
    """
    logger_sink: ExecutionLogger = execution_logger or NullExecutionLogger()
    logger_sink.emit(
        "run-started",
        payload={"environment": environment, "schemaVersion": manifest.schema_version},
    )
    if manifest.schema_version == "v1":
        result = _run_manifest_v1(manifest, environment=environment, client=client, execution_logger=logger_sink)
    else:
        result = _run_manifest_v0(manifest, environment=environment, client=client, execution_logger=logger_sink)
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


def _run_manifest_v1(
    manifest: Manifest,
    *,
    environment: str,
    client: httpx.Client,
    execution_logger: ExecutionLogger,
) -> SmokeCheckResult:
    """Execute a v1 manifest with sequential steps and context carry-forward.

    Each step resolves ``${...}`` placeholders from earlier step responses,
    validates the resolved URL, fetches the endpoint, evaluates assertions,
    and records the result into the execution context for later steps.

    Args:
        manifest: Parsed v1 manifest containing sequential steps.
        environment: Environment name copied into the result file.
        client: Preconfigured synchronous HTTP client.
        execution_logger: Structured execution-log sink.

    Returns:
        Smoke-check result with one entry per step.
    """
    started_at = datetime.now(UTC)
    steps: list[StepResult] = []
    context = ExecutionContext()

    for manifest_step in manifest.steps:
        step_result, context = _execute_v1_step(
            manifest_step, context=context, client=client, execution_logger=execution_logger
        )
        # Carry the manifest's mandatory flag onto the step result so the
        # aggregate certificationEligibility block can reason about it
        # without re-walking the manifest. Done here (rather than inside
        # _execute_v1_step) so the executor's many StepResult creation
        # sites stay focused on outcome semantics.
        if manifest_step.mandatory:
            step_result = replace(step_result, mandatory=True)
        steps.append(step_result)

    return build_smoke_check_result(environment, steps, started_at=started_at)


def _execute_v1_step(
    manifest_step: ManifestStep,
    *,
    context: ExecutionContext,
    client: httpx.Client,
    execution_logger: ExecutionLogger,
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

    Returns:
        A tuple of the step result and the updated execution context.
    """
    execution_logger.emit("step-started", step_id=manifest_step.id)
    step_result, new_context = _execute_v1_step_inner(
        manifest_step, context=context, client=client, execution_logger=execution_logger
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
        request_evidence["headers"] = dict(mask_headers(resolved_headers))
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
        request_evidence["body"] = mask_json_value(resolved_json_body)
    elif resolved_form_body is not None:
        request_evidence["form"] = dict(mask_form_fields(resolved_form_body))

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
            json_body=resolved_json_body,
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


def _run_manifest_v0(
    manifest: Manifest,
    *,
    environment: str,
    client: httpx.Client,
    execution_logger: ExecutionLogger,
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

    Returns:
        Smoke-check result with step entries matching v0 naming conventions.
    """
    started_at = datetime.now(UTC)
    steps: list[StepResult] = []
    context = ExecutionContext()

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
            primary_step, context=context, client=client, execution_logger=execution_logger
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
                    follow_up_step, context=context, client=client, execution_logger=execution_logger
                )
                steps.append(follow_up_result)

    return build_smoke_check_result(environment, steps, started_at=started_at)


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
        evaluate_assertion(assertion, status_code=response.status_code, body=response.body) for assertion in assertions
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
