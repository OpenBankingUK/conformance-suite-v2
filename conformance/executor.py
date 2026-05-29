"""Execute parsed v0/v1 manifests against JSON HTTP endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from conformance.assertions import AssertionResult, evaluate_assertion
from conformance.context import (
    ExecutionContext,
    PlaceholderResolutionError,
    RequestRecord,
    ResponseRecord,
    record_step,
    resolve_in_structure,
    resolve_placeholders,
)
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
from conformance.results import SmokeCheckResult, StepResult, build_smoke_check_result
from conformance.url_validation import HttpsUrlValidationError, validate_https_url


def run_manifest(manifest: Manifest, *, environment: str, client: httpx.Client) -> SmokeCheckResult:
    """Run a parsed manifest and return a structured smoke-check result.

    Dispatches to the v0 or v1 execution path based on schema version.
    v0 manifests are internally desugared to v1 sequential steps.

    Args:
        manifest: Parsed and validated manifest to execute.
        environment: Environment name copied into the result file.
        client: Preconfigured synchronous HTTP client used for network requests.

    Returns:
        Smoke-check result containing ordered manifest test steps.
    """
    if manifest.schema_version == "v1":
        return _run_manifest_v1(manifest, environment=environment, client=client)
    return _run_manifest_v0(manifest, environment=environment, client=client)


def _run_manifest_v1(manifest: Manifest, *, environment: str, client: httpx.Client) -> SmokeCheckResult:
    """Execute a v1 manifest with sequential steps and context carry-forward.

    Each step resolves ``${...}`` placeholders from earlier step responses,
    validates the resolved URL, fetches the endpoint, evaluates assertions,
    and records the result into the execution context for later steps.

    Args:
        manifest: Parsed v1 manifest containing sequential steps.
        environment: Environment name copied into the result file.
        client: Preconfigured synchronous HTTP client.

    Returns:
        Smoke-check result with one entry per step.
    """
    started_at = datetime.now(UTC)
    steps: list[StepResult] = []
    context = ExecutionContext()

    for manifest_step in manifest.steps:
        step_result, context = _execute_v1_step(manifest_step, context=context, client=client)
        steps.append(step_result)

    return build_smoke_check_result(environment, steps, started_at=started_at)


def _execute_v1_step(
    manifest_step: ManifestStep,
    *,
    context: ExecutionContext,
    client: httpx.Client,
) -> tuple[StepResult, ExecutionContext]:
    """Execute a single v1 manifest step with placeholder resolution.

    Resolves placeholders in the request URL, headers, and body, validates the
    resolved URL, issues the HTTP request, evaluates assertions, and records
    the step into the execution context.

    Args:
        manifest_step: The v1 step to execute.
        context: Current execution context with earlier step records.
        client: Preconfigured synchronous HTTP client.

    Returns:
        A tuple of the step result and the updated execution context.
    """
    method = manifest_step.request.method

    # Defence-in-depth: reject methods outside the supported set
    _supported_methods = {"GET", "POST", "PUT", "PATCH", "DELETE"}
    if method not in _supported_methods:
        request_record = RequestRecord(method=method, url=manifest_step.request.url)
        new_context = record_step(context, manifest_step.id, request_record, None)
        return (
            StepResult(
                name=manifest_step.id,
                status="failed",
                message=f"Unsupported request method: {method}",
                url=manifest_step.request.url,
            ),
            new_context,
        )

    # Resolve placeholders in the URL
    try:
        resolved_url = resolve_placeholders(manifest_step.request.url, context)
    except PlaceholderResolutionError as error:
        request_record = RequestRecord(method=method, url=manifest_step.request.url)
        new_context = record_step(context, manifest_step.id, request_record, None)
        return (
            StepResult(
                name=manifest_step.id,
                status="failed",
                message=f"Placeholder resolution failed: {error}",
                url=manifest_step.request.url,
            ),
            new_context,
        )

    # Resolve placeholders in headers
    resolved_headers: dict[str, str] | None = None
    if manifest_step.request.headers is not None:
        try:
            resolved_headers = {
                name: resolve_placeholders(value, context) for name, value in manifest_step.request.headers.items()
            }
        except PlaceholderResolutionError as error:
            request_record = RequestRecord(method=method, url=resolved_url)
            new_context = record_step(context, manifest_step.id, request_record, None)
            return (
                StepResult(
                    name=manifest_step.id,
                    status="failed",
                    message=f"Placeholder resolution failed: {error}",
                    url=resolved_url,
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
                    StepResult(
                        name=manifest_step.id,
                        status="failed",
                        message=f"Resolved header validation failed: {error}",
                        url=resolved_url,
                    ),
                    new_context,
                )

    # Resolve placeholders in body. JsonBody walks the structure recursively;
    # FormBody resolves each field value. Form fields are intentionally NOT
    # recorded into the step record yet — masking secrets in step evidence is
    # still deferred (DL-0013), and OAuth2 token-exchange field values
    # frequently carry secrets (authorization codes, client secrets).
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
        except PlaceholderResolutionError as error:
            request_record = RequestRecord(method=method, url=resolved_url)
            new_context = record_step(context, manifest_step.id, request_record, None)
            return (
                StepResult(
                    name=manifest_step.id,
                    status="failed",
                    message=f"Placeholder resolution failed: {error}",
                    url=resolved_url,
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
            StepResult(
                name=manifest_step.id,
                status="failed",
                message=str(error),
                url=resolved_url,
            ),
            new_context,
        )

    # Execute HTTP request
    request_record = RequestRecord(method=method, url=resolved_url)
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
        return (
            StepResult(
                name=manifest_step.id,
                status="failed",
                message=str(error),
                url=resolved_url,
                status_code=error.status_code,
            ),
            new_context,
        )

    # Record response into context
    response_record = ResponseRecord(
        status_code=response.status_code,
        body=response.body,
    )
    new_context = record_step(context, manifest_step.id, request_record, response_record)

    # Evaluate assertions
    step_result = _build_assertion_step(
        name=manifest_step.id,
        success_message=f"{manifest_step.name} passed",
        failure_message=f"{manifest_step.name} failed",
        response=response,
        assertions=manifest_step.assertions,
    )
    return step_result, new_context


def _run_manifest_v0(manifest: Manifest, *, environment: str, client: httpx.Client) -> SmokeCheckResult:
    """Execute a v0 manifest preserving original skip-on-fail semantics.

    In v0, follow-up steps are only executed when the primary step passes.
    This differs from v1 where all steps run regardless of earlier assertion
    outcomes. The method desugars each test into v1 steps but gates follow-up
    execution on primary step success.

    Args:
        manifest: Parsed v0 manifest containing tests with optional followUp.
        environment: Environment name copied into the result file.
        client: Preconfigured synchronous HTTP client.

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
            steps.append(
                StepResult(
                    name=test.id,
                    status="failed",
                    message=f"v0 manifest primary requests must use GET, got: {test.request.method}",
                    url=test.request.url,
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
        step_result, context = _execute_v1_step(primary_step, context=context, client=client)
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
                steps.append(
                    StepResult(
                        name=follow_up_id,
                        status="failed",
                        message=f"Unable to resolve follow-up URL from {test.follow_up.url_source}",
                        url=primary_url,
                    )
                )
            else:
                follow_up_step = ManifestStep(
                    id=follow_up_id,
                    name=f"{test.name} follow-up",
                    request=ManifestRequest(method=test.follow_up.request.method, url=follow_up_url),
                    assertions=test.follow_up.assertions,
                )
                follow_up_result, context = _execute_v1_step(follow_up_step, context=context, client=client)
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
) -> StepResult:
    """Build a step result by evaluating all assertions for a response.

    Args:
        name: The step name displayed in the conformance report.
        success_message: Message emitted when all assertions pass.
        failure_message: Message emitted when any assertion fails.
        response: The HTTP response to evaluate assertions against.
        assertions: The manifest assertions to apply to the response.

    Returns:
        A completed step result containing the overall pass/fail status
        and per-assertion details.
    """
    assertion_results = tuple(
        evaluate_assertion(assertion, status_code=response.status_code, body=response.body) for assertion in assertions
    )
    passed = all(assertion_result.passed for assertion_result in assertion_results)
    return StepResult(
        name=name,
        status="passed" if passed else "failed",
        message=success_message if passed else failure_message,
        url=response.url,
        status_code=response.status_code,
        details={"assertions": [_assertion_result_to_json(assertion_result) for assertion_result in assertion_results]},
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
